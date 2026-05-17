"""Tests for PubMed connector with mocked HTTP responses."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.connectors.pubmed import PubMedConnector
from app.schemas.evidence import ConnectorQuery, SourceType


@pytest.fixture(autouse=True)
def _clear_audit_log(tmp_path, monkeypatch):
    mock_config = MagicMock()
    mock_config.logs_dir = tmp_path / "logs"
    mock_config.logs_dir.mkdir()
    mock_config.ensure_dirs = lambda: None
    monkeypatch.setattr("app.logging.audit_logger.config", mock_config)


def _make_esearch_response(pmids: list[str], count: int = 0):
    return {
        "esearchresult": {
            "count": str(count or len(pmids)),
            "idlist": pmids,
        }
    }


def _make_esummary_response(pmids: list[str]):
    result = {"uids": pmids}
    for pmid in pmids:
        result[pmid] = {
            "uid": pmid,
            "title": f"Study about aspirin {pmid}",
            "fulljournalname": "Test Journal",
            "pubdate": "2024 Jan",
            "sortfirstauthor": "Smith A",
            "source": "Test J",
        }
    return {"result": result}


class FakeHTTPClient:
    """Fake httpx.Client that returns pre-configured responses."""

    def __init__(self, responses: list[dict]):
        self._responses = responses
        self._call_index = 0

    def get(self, url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = lambda: None
        data = self._responses[self._call_index] if self._call_index < len(self._responses) else {}
        self._call_index += 1
        resp.json.return_value = data
        return resp


def test_pubmed_happy_path():
    pmids = ["12345", "67890"]
    http = FakeHTTPClient([
        _make_esearch_response(pmids, count=2),
        _make_esummary_response(pmids),
    ])
    connector = PubMedConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin", disease="stroke")

    result = connector.search(query, run_id="test_run")

    assert result.connector_name == "pubmed"
    assert result.results_returned == 2
    assert len(result.sources) == 2
    assert len(result.evidence_items) == 2
    assert result.sources[0].source_type == SourceType.pubmed
    assert result.sources[0].external_id == "12345"
    assert "pubmed.ncbi.nlm.nih.gov" in result.sources[0].url_or_path
    assert result.errors == []


def test_pubmed_no_results():
    http = FakeHTTPClient([
        _make_esearch_response([], count=0),
    ])
    connector = PubMedConnector(http_client=http)
    query = ConnectorQuery(inn="unknowndrug123")

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 0
    assert len(result.sources) == 0
    assert "No PubMed results found" in result.warnings


def test_pubmed_network_error():
    http = MagicMock()
    http.get.side_effect = Exception("Connection timeout")
    connector = PubMedConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin")

    result = connector.search(query, run_id="test_run")

    assert len(result.errors) > 0
    assert "Connection timeout" in result.errors[0]
    assert result.results_returned == 0


def test_pubmed_citation_label_populated():
    pmids = ["99999"]
    http = FakeHTTPClient([
        _make_esearch_response(pmids),
        _make_esummary_response(pmids),
    ])
    connector = PubMedConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin")

    result = connector.search(query, run_id="test_run")

    assert result.sources[0].citation_label
    assert "Smith A" in result.sources[0].citation_label
    assert "Test Journal" in result.sources[0].citation_label
