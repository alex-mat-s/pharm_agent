"""Tests for USPTO connector.

Tests verify:
1. Legacy PatentsView endpoint (api.patentsview.org) is NEVER used
2. Requires USPTO_ODP_API_KEY configuration
3. Returns source_unavailable when API key is missing
4. HTTP 301 redirect returns source_unavailable warning
5. HTTP 403 returns source_unavailable warning
6. HTTP 404 returns no_results warning
7. Successful search returns patents
8. Run continues when USPTO is unavailable
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.connectors.uspto import USPTOConnector, USPTO_ODP_URL
from app.schemas.evidence import ConnectorQuery, EvidenceCategory, SourceType


@pytest.fixture(autouse=True)
def _clear_audit_log(tmp_path, monkeypatch):
    mock_config = MagicMock()
    mock_config.logs_dir = tmp_path / "logs"
    mock_config.logs_dir.mkdir()
    mock_config.ensure_dirs = lambda: None
    mock_config.uspto_odp_api_key = None  # Default: no API key
    monkeypatch.setattr("app.logging.audit_logger.config", mock_config)
    monkeypatch.setattr("app.connectors.uspto.config", mock_config)


@pytest.fixture
def mock_config_with_api_key(monkeypatch, tmp_path):
    """Fixture that provides a config with USPTO API key."""
    mock_config = MagicMock()
    mock_config.logs_dir = tmp_path / "logs"
    mock_config.logs_dir.mkdir(exist_ok=True)
    mock_config.ensure_dirs = lambda: None
    mock_config.uspto_odp_api_key = "test_api_key_12345"
    monkeypatch.setattr("app.connectors.uspto.config", mock_config)
    return mock_config


@pytest.fixture
def connector() -> USPTOConnector:
    return USPTOConnector()


@pytest.fixture
def query() -> ConnectorQuery:
    return ConnectorQuery(inn="aspirin", max_results=5)


# =============================================================================
# Test: Legacy PatentsView endpoint is NEVER used
# =============================================================================
class TestLegacyPatentsViewNotUsed:
    """Tests verifying the deprecated PatentsView endpoint is not used."""

    def test_patentsview_url_not_in_code(self):
        """Verify the deprecated PatentsView URL is not used."""
        # The old URL should not be the active endpoint
        assert USPTO_ODP_URL != "https://api.patentsview.org/patents/query"
        assert "patentsview.org" not in USPTO_ODP_URL

    def test_connector_does_not_call_patentsview(self, mock_config_with_api_key):
        """Verify connector never calls api.patentsview.org."""
        http = MagicMock()
        http.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": []},
        )

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="aspirin")

        connector.search(query, run_id="test_run")

        # Check all calls
        for call in http.post.call_args_list:
            url = call[0][0] if call[0] else call[1].get("url", "")
            assert "patentsview.org" not in url, \
                f"FORBIDDEN: Legacy patentsview.org endpoint used: {url}"

    def test_uses_uspto_odp_url(self, mock_config_with_api_key):
        """Verify connector uses USPTO Open Data Portal URL."""
        assert "api.uspto.gov" in USPTO_ODP_URL or "api" in USPTO_ODP_URL


# =============================================================================
# Test: API key configuration required
# =============================================================================
class TestUSPTOApiKeyRequired:
    """Tests for API key requirement."""

    def test_missing_api_key_returns_source_unavailable(self, monkeypatch, tmp_path):
        """Verify missing API key returns source_unavailable warning."""
        mock_config = MagicMock()
        mock_config.logs_dir = tmp_path / "logs"
        mock_config.logs_dir.mkdir(exist_ok=True)
        mock_config.uspto_odp_api_key = None
        monkeypatch.setattr("app.connectors.uspto.config", mock_config)

        connector = USPTOConnector()
        connector._api_key = None  # Force no API key
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        assert result.results_returned == 0
        assert len(result.warnings) > 0
        assert "source_unavailable" in result.warnings[0]
        assert "USPTO_ODP_API_KEY" in result.warnings[0]

    def test_empty_api_key_returns_source_unavailable(self, monkeypatch, tmp_path):
        """Verify empty API key returns source_unavailable warning."""
        mock_config = MagicMock()
        mock_config.logs_dir = tmp_path / "logs"
        mock_config.logs_dir.mkdir(exist_ok=True)
        mock_config.uspto_odp_api_key = ""
        monkeypatch.setattr("app.connectors.uspto.config", mock_config)

        connector = USPTOConnector()
        connector._api_key = ""  # Empty string
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        assert result.results_returned == 0
        assert len(result.warnings) > 0
        assert "source_unavailable" in result.warnings[0]


# =============================================================================
# Test: HTTP 301 redirect returns source_unavailable
# =============================================================================
class TestUSPTO301Handling:
    """Tests for HTTP 301 handling (deprecated endpoint detection)."""

    def test_301_returns_source_unavailable(self, mock_config_with_api_key):
        """Verify 301 redirect returns source_unavailable warning."""
        http = MagicMock()
        http.post.return_value = MagicMock(
            status_code=301,
            text="Moved Permanently",
        )

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        assert result.results_returned == 0
        assert len(result.errors) == 0, "301 should NOT produce errors list"
        assert len(result.warnings) > 0
        assert "source_unavailable" in result.warnings[0]
        assert "301" in result.warnings[0]

    def test_301_indicates_deprecated_endpoint(self, mock_config_with_api_key):
        """Verify 301 warning mentions deprecated endpoint."""
        http = MagicMock()
        http.post.return_value = MagicMock(
            status_code=301,
            text="",
        )

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        # Warning should indicate the endpoint may have changed
        warning_text = " ".join(result.warnings)
        assert "redirect" in warning_text.lower() or "endpoint" in warning_text.lower() or "changed" in warning_text.lower()


# =============================================================================
# Test: HTTP 403 returns source_unavailable
# =============================================================================
class TestUSPTO403Handling:
    """Tests for HTTP 403 handling."""

    def test_403_returns_source_unavailable(self, mock_config_with_api_key):
        """Verify 403 returns source_unavailable warning."""
        http = MagicMock()
        http.post.return_value = MagicMock(
            status_code=403,
            text="Forbidden",
        )

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        assert result.results_returned == 0
        assert len(result.errors) == 0, "403 should NOT produce errors list"
        assert len(result.warnings) > 0
        assert "source_unavailable" in result.warnings[0]
        assert "403" in result.warnings[0]

    def test_403_mentions_api_key_validity(self, mock_config_with_api_key):
        """Verify 403 warning suggests checking API key."""
        http = MagicMock()
        http.post.return_value = MagicMock(
            status_code=403,
            text="",
        )

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        warning_text = " ".join(result.warnings).lower()
        assert "api" in warning_text or "key" in warning_text or "valid" in warning_text


# =============================================================================
# Test: HTTP 404 returns no_results
# =============================================================================
class TestUSPTO404Handling:
    """Tests for HTTP 404 handling."""

    def test_404_returns_no_results(self, mock_config_with_api_key):
        """Verify 404 returns no_results warning."""
        http = MagicMock()
        http.post.return_value = MagicMock(
            status_code=404,
            text="Not Found",
        )

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="unknowndrug")

        result = connector.search(query, run_id="test_run")

        assert result.results_returned == 0
        assert len(result.errors) == 0, "404 should NOT produce errors"
        assert len(result.warnings) > 0
        assert "no_results" in result.warnings[0]


# =============================================================================
# Test: Successful search
# =============================================================================
class TestUSPTOSuccess:
    """Tests for successful searches."""

    def test_successful_search(self, mock_config_with_api_key):
        """Verify successful search returns patents."""
        mock_response = {
            "results": [
                {
                    "patentNumber": "US1234567",
                    "title": "Method of treating disease with aspirin",
                    "grantDate": "2020-01-15",
                    "applicationDate": "2018-06-01",
                    "assignees": [{"organization": "Bayer AG"}],
                    "inventors": [{"name": "John Doe"}],
                }
            ]
        }

        http = MagicMock()
        http.post.return_value = MagicMock(
            status_code=200,
            json=lambda: mock_response,
            raise_for_status=lambda: None,
        )

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        assert result.connector_name == "uspto"
        assert result.results_returned == 1
        assert len(result.sources) == 1
        assert result.sources[0].source_type == SourceType.uspto
        assert result.sources[0].external_id == "US1234567"

    def test_evidence_category_is_patent(self, mock_config_with_api_key):
        """Verify evidence category is patent."""
        mock_response = {
            "results": [
                {
                    "patentNumber": "US1234567",
                    "title": "Aspirin formulation",
                    "grantDate": "2020-01-15",
                    "assignees": [],
                }
            ]
        }

        http = MagicMock()
        http.post.return_value = MagicMock(
            status_code=200,
            json=lambda: mock_response,
            raise_for_status=lambda: None,
        )

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        assert result.evidence_items[0].category == EvidenceCategory.patent


# =============================================================================
# Test: Run continues when USPTO unavailable
# =============================================================================
class TestUSPTORunContinuation:
    """Tests verifying the run continues when USPTO is unavailable."""

    def test_missing_api_key_does_not_raise(self, monkeypatch, tmp_path):
        """Verify missing API key does not raise exceptions."""
        mock_config = MagicMock()
        mock_config.logs_dir = tmp_path / "logs"
        mock_config.logs_dir.mkdir(exist_ok=True)
        mock_config.uspto_odp_api_key = None
        monkeypatch.setattr("app.connectors.uspto.config", mock_config)

        connector = USPTOConnector()
        connector._api_key = None
        query = ConnectorQuery(inn="aspirin")

        # Should not raise
        result = connector.search(query, run_id="test_run")

        # Result should be valid (run continues)
        assert result is not None
        assert result.connector_name == "uspto"

    def test_403_does_not_raise(self, mock_config_with_api_key):
        """Verify 403 does not raise exceptions."""
        http = MagicMock()
        http.post.return_value = MagicMock(
            status_code=403,
            text="Forbidden",
        )

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="aspirin")

        # Should not raise
        result = connector.search(query, run_id="test_run")

        assert result is not None
        assert len(result.errors) == 0

    def test_301_does_not_raise(self, mock_config_with_api_key):
        """Verify 301 does not raise exceptions."""
        http = MagicMock()
        http.post.return_value = MagicMock(
            status_code=301,
            text="Moved",
        )

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="aspirin")

        # Should not raise
        result = connector.search(query, run_id="test_run")

        assert result is not None
        assert len(result.errors) == 0

    def test_network_error_does_not_raise(self, mock_config_with_api_key):
        """Verify network errors do not crash the run."""
        http = MagicMock()
        http.post.side_effect = Exception("Connection timeout")

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="aspirin")

        # Should not raise
        result = connector.search(query, run_id="test_run")

        assert result is not None
        assert "source_unavailable" in " ".join(result.warnings)


# =============================================================================
# Test: Empty results handling
# =============================================================================
class TestUSPTOEmptyResults:
    """Tests for empty results handling."""

    def test_empty_results_returns_warning(self, mock_config_with_api_key):
        """Verify empty results returns appropriate warning."""
        http = MagicMock()
        http.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": []},
            raise_for_status=lambda: None,
        )

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        assert result.results_returned == 0
        assert len(result.warnings) > 0
        assert "no_results" in result.warnings[0]


# =============================================================================
# Test: Cyrillic handling
# =============================================================================
class TestUSPTOCyrillicHandling:
    """Tests for Cyrillic input handling."""

    def test_cyrillic_inn_handled_gracefully(self, mock_config_with_api_key):
        """Verify Cyrillic INN is handled without errors."""
        http = MagicMock()
        http.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": []},
            raise_for_status=lambda: None,
        )

        connector = USPTOConnector(http_client=http)
        connector._api_key = "test_key"
        query = ConnectorQuery(inn="ацетилсалициловая кислота")

        # Should not raise
        result = connector.search(query, run_id="test_run")

        assert result is not None
        assert result.results_returned == 0
