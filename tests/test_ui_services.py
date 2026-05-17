"""Unit tests for app/ui/services.py and app/services/healthcheck.py.

These tests mock the backend orchestrator and DB to verify UI service logic
without requiring live API keys or network access.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.ui.services import (
    DecisionResult,
    RunCreateResult,
    create_run_and_enrich,
    get_audit_events,
    get_evidence_table,
    get_run_status,
    load_verification_packet,
    mask_secrets,
    run_scientific_agent,
    submit_decision,
)


# ─── Validation tests ─────────────────────────────────────────────────────────

class TestCreateRunValidation:
    def test_missing_inn_returns_error(self):
        result = create_run_and_enrich(
            inn="", disease=None, region=None, stage=None,
            pdf1_path="/tmp/a.pdf", pdf2_path="/tmp/b.pdf",
        )
        assert not result.success
        assert "МНН" in result.error

    def test_missing_pdf1_returns_error(self):
        result = create_run_and_enrich(
            inn="aspirin", disease=None, region=None, stage=None,
            pdf1_path=None, pdf2_path="/tmp/b.pdf",
        )
        assert not result.success
        assert "2 PDF" in result.error

    def test_missing_pdf2_returns_error(self):
        result = create_run_and_enrich(
            inn="aspirin", disease=None, region=None, stage=None,
            pdf1_path="/tmp/a.pdf", pdf2_path=None,
        )
        assert not result.success
        assert "2 PDF" in result.error

    def test_nonexistent_pdf_returns_error(self, tmp_path):
        pdf1 = tmp_path / "exists.pdf"
        pdf1.write_bytes(b"%PDF-1.4 test")
        missing = tmp_path / "missing.pdf"

        result = create_run_and_enrich(
            inn="aspirin", disease=None, region=None, stage=None,
            pdf1_path=str(pdf1), pdf2_path=str(missing),
        )
        assert not result.success
        assert "не найден" in result.error


# ─── Human verification gate tests ───────────────────────────────────────────

class TestVerificationGate:
    @patch("app.ui.services._db")
    def test_scientific_agent_blocked_without_verification(self, mock_db):
        mock_run = MagicMock()
        mock_run.status = MagicMock()
        mock_run.status.value = "awaiting_human_verification"

        # Make status comparison work
        from app.schemas.run import RunStatus
        mock_run.status = RunStatus.awaiting_human_verification

        db_instance = MagicMock()
        db_instance.get_run.return_value = mock_run
        mock_db.return_value = db_instance

        msg = run_scientific_agent("run_test_123")
        assert "верификацию" in msg.lower() or "Сначала" in msg

    @patch("app.ui.services._db")
    def test_scientific_agent_blocked_on_rejection(self, mock_db):
        from app.schemas.run import RunStatus
        mock_run = MagicMock()
        mock_run.status = RunStatus.human_rejected

        db_instance = MagicMock()
        db_instance.get_run.return_value = mock_run
        mock_db.return_value = db_instance

        msg = run_scientific_agent("run_test_456")
        assert "отклонён" in msg.lower() or "Reject" in msg or "❌" in msg


# ─── Decision submission tests ────────────────────────────────────────────────

class TestSubmitDecision:
    def test_invalid_decision_returns_error(self):
        result = submit_decision(run_id="run_x", decision="maybe", comments=None)
        assert not result.success
        assert "Неверное" in result.error


# ─── Secret masking tests ─────────────────────────────────────────────────────

class TestMaskSecrets:
    def test_masks_openrouter_key(self):
        text = "Authorization: Bearer sk-or-v1-abcdef1234567890abcdef1234567890"
        masked = mask_secrets(text)
        assert "sk-or-" not in masked
        assert "[REDACTED]" in masked

    def test_masks_bearer_token(self):
        text = "headers: {Authorization: Bearer my-secret-token}"
        masked = mask_secrets(text)
        assert "my-secret-token" not in masked

    def test_preserves_normal_text(self):
        text = "This is a normal log message without secrets."
        assert mask_secrets(text) == text


# ─── FDA 403 warning tests ────────────────────────────────────────────────────

class TestRunStatusWarnings:
    @patch("app.ui.services._db")
    def test_fda_403_shown_as_warning(self, mock_db):
        from app.schemas.run import RunStatus

        mock_run = MagicMock()
        mock_run.run_id = "run_fda_test"
        mock_run.status = RunStatus.completed
        mock_run.created_at = "2026-01-01"
        mock_run.updated_at = "2026-01-01"
        mock_run.error_message = None

        db_instance = MagicMock()
        db_instance.get_run.return_value = mock_run
        db_instance.get_run_steps.return_value = [
            {
                "step_name": "fda_lookup",
                "status": "warning",
                "details_json": json.dumps({"warning": "HTTP 403 — доступ заблокирован"}),
            },
        ]
        mock_db.return_value = db_instance

        data = get_run_status("run_fda_test")
        steps = data["steps"]
        fda_step = next((s for s in steps if s["step"] == "fda_lookup"), None)
        assert fda_step is not None
        assert fda_step["status"] == "warning"
        assert "403" in fda_step["details"]


# ─── EMA cache fallback warning test ─────────────────────────────────────────

class TestEMAWarning:
    @patch("app.ui.services._db")
    def test_ema_cache_fallback_shown(self, mock_db):
        from app.schemas.run import RunStatus

        mock_run = MagicMock()
        mock_run.run_id = "run_ema_test"
        mock_run.status = RunStatus.completed
        mock_run.created_at = "2026-01-01"
        mock_run.updated_at = "2026-01-01"
        mock_run.error_message = None

        db_instance = MagicMock()
        db_instance.get_run.return_value = mock_run
        db_instance.get_run_steps.return_value = [
            {
                "step_name": "ema_lookup",
                "status": "warning",
                "details_json": json.dumps({"warning": "Live недоступен, используется кеш"}),
            },
        ]
        mock_db.return_value = db_instance

        data = get_run_status("run_ema_test")
        steps = data["steps"]
        ema_step = next((s for s in steps if s["step"] == "ema_lookup"), None)
        assert ema_step is not None
        assert "кеш" in ema_step["details"]


# ─── Audit log masking test ───────────────────────────────────────────────────

class TestAuditLogMasking:
    @patch("app.ui.services._db")
    def test_audit_events_mask_secrets(self, mock_db):
        db_instance = MagicMock()
        db_instance.get_audit_events.return_value = [
            {
                "event_id": "e1",
                "run_id": "run_x",
                "event_type": "llm_call",
                "metadata": {"key": "sk-or-v1-supersecretkey12345678"},
            }
        ]
        mock_db.return_value = db_instance

        output = get_audit_events("run_x", count=10, event_filter="all")
        assert "sk-or-" not in output
        assert "[REDACTED]" in output
