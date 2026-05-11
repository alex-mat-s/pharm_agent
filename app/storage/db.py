from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.schemas.human_decision import HumanDecision
from app.schemas.input import RawInput
from app.schemas.intake_output import IntakeEnrichmentOutput
from app.schemas.pdf import PDFMetadata, PDFExtractionResult
from app.schemas.run import RunRecord, RunStatus, StageOutput, is_valid_transition
from app.schemas.audit import AuditEvent
from app.config import config


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """SQLite persistence layer for MVP 1."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or config.db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    raw_input_json TEXT NOT NULL,
                    enrichment_output_json TEXT,
                    human_decision_json TEXT,
                    final_summary_json TEXT,
                    error_message TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pdf_versions (
                    pdf_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    page_count INTEGER,
                    modified_timestamp TEXT,
                    ingested_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stage_outputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stage TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_ref TEXT,
                    output_ref TEXT,
                    metadata TEXT
                )
                """
            )
            conn.commit()

    # Runs
    def create_run(self, raw_input: RawInput) -> RunRecord:
        run_id = f"run_{_now_iso().replace(':', '').replace('+', '')}"
        now = _now_iso()
        raw_json = json.dumps(raw_input.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, status, created_at, updated_at, raw_input_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, RunStatus.created.value, now, now, raw_json),
            )
            conn.commit()
        return RunRecord(
            run_id=run_id,
            status=RunStatus.created,
            created_at=now,
            updated_at=now,
            raw_input_json=raw_json,
        )

    def update_run_status(self, run_id: str, new_status: RunStatus, error: str | None = None) -> RunRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Run {run_id} not found")
            current = RunStatus(row["status"])
            if not is_valid_transition(current, new_status):
                raise ValueError(
                    f"Invalid status transition: {current.value} -> {new_status.value}"
                )
            now = _now_iso()
            if error:
                conn.execute(
                    "UPDATE runs SET status = ?, updated_at = ?, error_message = ? WHERE run_id = ?",
                    (new_status.value, now, error, run_id),
                )
            else:
                conn.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    (new_status.value, now, run_id),
                )
            conn.commit()
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            return self._row_to_run(row)

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_run(row)

    def _row_to_run(self, row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            status=RunStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            raw_input_json=row["raw_input_json"],
            enrichment_output_json=row["enrichment_output_json"],
            human_decision_json=row["human_decision_json"],
            final_summary_json=row["final_summary_json"],
            error_message=row["error_message"],
        )

    def save_enrichment_output(self, run_id: str, output: IntakeEnrichmentOutput) -> None:
        out_json = json.dumps(output.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET enrichment_output_json = ? WHERE run_id = ?",
                (out_json, run_id),
            )
            conn.commit()

    def save_human_decision(self, run_id: str, decision: HumanDecision) -> None:
        dec_json = json.dumps(decision.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET human_decision_json = ? WHERE run_id = ?",
                (dec_json, run_id),
            )
            conn.commit()

    def save_final_summary(self, run_id: str, summary_json: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET final_summary_json = ? WHERE run_id = ?",
                (summary_json, run_id),
            )
            conn.commit()

    # PDFs
    def get_pdf_by_sha256(self, sha256: str) -> PDFMetadata | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pdf_versions WHERE sha256 = ?", (sha256,)
            ).fetchone()
            if row is None:
                return None
            return PDFMetadata(
                pdf_id=row["pdf_id"],
                filename=row["filename"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                page_count=row["page_count"] if row["page_count"] else 0,
                modified_timestamp=row["modified_timestamp"] or "",
                ingested_at=row["ingested_at"],
                last_seen_at=row["last_seen_at"],
            )

    def register_pdf(self, meta: PDFMetadata) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pdf_versions
                (pdf_id, filename, sha256, size_bytes, page_count, modified_timestamp, ingested_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    meta.pdf_id,
                    meta.filename,
                    meta.sha256,
                    meta.size_bytes,
                    meta.page_count,
                    meta.modified_timestamp,
                    meta.ingested_at,
                    meta.last_seen_at,
                ),
            )
            conn.commit()

    # Stage outputs
    def save_stage_output(self, stage: StageOutput) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO stage_outputs (stage, run_id, output_json, created_at, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    stage.stage,
                    stage.run_id,
                    stage.output_json,
                    stage.created_at,
                    json.dumps(stage.metadata, ensure_ascii=False),
                ),
            )
            conn.commit()

    def get_stage_outputs(self, run_id: str) -> list[StageOutput]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM stage_outputs WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
            return [
                StageOutput(
                    stage=r["stage"],
                    run_id=r["run_id"],
                    output_json=r["output_json"],
                    created_at=r["created_at"],
                    metadata=json.loads(r["metadata"]) if r["metadata"] else {},
                )
                for r in rows
            ]

    # Audit events (also persisted in SQLite for easier querying)
    def save_audit_event(self, event: AuditEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events
                (event_id, run_id, stage, event_type, timestamp, status, input_ref, output_ref, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.run_id,
                    event.stage,
                    event.event_type,
                    event.timestamp,
                    event.status,
                    event.input_ref,
                    event.output_ref,
                    json.dumps(event.metadata, ensure_ascii=False),
                ),
            )
            conn.commit()
