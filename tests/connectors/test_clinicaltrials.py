"""Tests for ClinicalTrials.gov connector with mocked HTTP responses."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.connectors.clinicaltrials import ClinicalTrialsConnector
from app.schemas.evidence import ConnectorQuery, SourceType


@pytest.fixture(autouse=True)
def _clear_audit_log(tmp_path, monkeypatch):
    mock_config = MagicMock()
    mock_config.logs_dir = tmp_path / "logs"
    mock_config.logs_dir.mkdir()
    mock_config.ensure_dirs = lambda: None
    monkeypatch.setattr("app.logging.audit_logger.config", mock_config)


def _make_study(nct_id: str, title: str = "Test Study", phase: str = "PHASE2", status: str = "RECRUITING"):
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct_id,
                "briefTitle": title,
                "officialTitle": f"Official: {title}",
            },
            "statusModule": {
                "overallStatus": status,
                "startDateStruct": {"date": "2024-01-15"},
            },
            "designModule": {
                "phases": [phase],
            },
            "sponsorCollaboratorsModule": {
                "leadSponsor": {"name": "TestPharma Inc"},
            },
            "conditionsModule": {
                "conditions": ["Stroke"],
            },
            "armsInterventionsModule": {
                "interventions": [{"name": "Aspirin"}],
            },
        }
    }


class FakeHTTPClient:
    def __init__(self, response: dict):
        self._response = response

    def get(self, url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = lambda: None
        resp.json.return_value = self._response
        return resp


def test_clinicaltrials_happy_path():
    studies = [_make_study("NCT001"), _make_study("NCT002", title="Another Study")]
    http = FakeHTTPClient({"studies": studies, "totalCount": 2})
    connector = ClinicalTrialsConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin", disease="stroke")

    result = connector.search(query, run_id="test_run")

    assert result.connector_name == "clinicaltrials"
    assert result.results_returned == 2
    assert len(result.sources) == 2
    assert result.sources[0].source_type == SourceType.clinicaltrials
    assert result.sources[0].external_id == "NCT001"
    assert result.evidence_items[0].category.value == "clinical_trial"


def test_clinicaltrials_empty_results():
    http = FakeHTTPClient({"studies": [], "totalCount": 0})
    connector = ClinicalTrialsConnector(http_client=http)
    query = ConnectorQuery(inn="unknowndrug")

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 0
    assert len(result.sources) == 0


def test_clinicaltrials_network_error():
    http = MagicMock()
    http.get.side_effect = Exception("Network error")
    connector = ClinicalTrialsConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin")

    result = connector.search(query, run_id="test_run")

    assert len(result.errors) > 0


def test_clinicaltrials_trial_metadata():
    study = _make_study("NCT123", title="Aspirin for Stroke", phase="PHASE3", status="COMPLETED")
    http = FakeHTTPClient({"studies": [study], "totalCount": 1})
    connector = ClinicalTrialsConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin", disease="stroke")

    result = connector.search(query, run_id="test_run")

    evi = result.evidence_items[0]
    assert "PHASE3" in evi.summary
    assert "COMPLETED" in evi.summary
    assert "TestPharma" in evi.key_findings[2]
