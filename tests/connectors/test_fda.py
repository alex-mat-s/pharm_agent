"""Tests for FDA connector with mocked HTTP responses."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.connectors.fda import FDAConnector
from app.schemas.evidence import ConnectorQuery, SourceType


@pytest.fixture(autouse=True)
def _clear_audit_log(tmp_path, monkeypatch):
    mock_config = MagicMock()
    mock_config.logs_dir = tmp_path / "logs"
    mock_config.logs_dir.mkdir()
    mock_config.ensure_dirs = lambda: None
    monkeypatch.setattr("app.logging.audit_logger.config", mock_config)


def _make_fda_result(app_no: str = "NDA012345", brand: str = "Aspirin"):
    return {
        "application_number": app_no,
        "sponsor_name": "Bayer",
        "products": [
            {
                "dosage_form": "TABLET",
                "route": "ORAL",
                "active_ingredients": [{"strength": "325 MG"}],
            }
        ],
        "openfda": {
            "generic_name": ["ASPIRIN"],
            "brand_name": [brand],
        },
        "submissions": [
            {
                "submission_type": "ORIG",
                "submission_status_date": "19990101",
            }
        ],
    }


class FakeHTTPClient:
    def __init__(self, response: dict, status_code: int = 200):
        self._response = response
        self._status_code = status_code

    def get(self, url, **kwargs):
        resp = MagicMock()
        resp.status_code = self._status_code
        if self._status_code == 404:
            resp.raise_for_status = lambda: None
        else:
            resp.raise_for_status = lambda: None
        resp.json.return_value = self._response
        return resp


def test_fda_happy_path():
    http = FakeHTTPClient({
        "results": [_make_fda_result()],
        "meta": {"results": {"total": 1}},
    })
    connector = FDAConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin")

    result = connector.search(query, run_id="test_run")

    assert result.connector_name == "fda"
    assert result.results_returned == 1
    assert result.sources[0].source_type == SourceType.fda
    assert result.sources[0].external_id == "NDA012345"
    assert result.evidence_items[0].category.value == "regulatory"


def test_fda_404_no_results():
    http = FakeHTTPClient({}, status_code=404)
    connector = FDAConnector(http_client=http)
    query = ConnectorQuery(inn="unknowndrug")

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 0
    assert "No FDA results" in result.warnings[0]


def test_fda_network_error():
    http = MagicMock()
    http.get.side_effect = Exception("Timeout")
    connector = FDAConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin")

    result = connector.search(query, run_id="test_run")

    assert len(result.errors) > 0


def test_fda_citation_has_sponsor():
    http = FakeHTTPClient({
        "results": [_make_fda_result()],
        "meta": {"results": {"total": 1}},
    })
    connector = FDAConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin")

    result = connector.search(query, run_id="test_run")

    assert "Bayer" in result.sources[0].citation_label
