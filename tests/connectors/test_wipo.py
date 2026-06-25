"""Tests for WIPO connector.

Tests verify:
1. Does NOT scrape PATENTSCOPE result.jsf pages
2. HTTP 403 returns source_unavailable (not fatal)
3. Returns source_unavailable immediately (no API available)
4. Run continues without WIPO data
5. Source_unavailable warnings include helpful alternatives
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.connectors.wipo import WIPOConnector
from app.schemas.evidence import ConnectorQuery


@pytest.fixture(autouse=True)
def _clear_audit_log(tmp_path, monkeypatch):
    mock_config = MagicMock()
    mock_config.logs_dir = tmp_path / "logs"
    mock_config.logs_dir.mkdir()
    mock_config.ensure_dirs = lambda: None
    monkeypatch.setattr("app.logging.audit_logger.config", mock_config)


@pytest.fixture
def connector() -> WIPOConnector:
    return WIPOConnector()


@pytest.fixture
def query() -> ConnectorQuery:
    return ConnectorQuery(inn="aspirin", max_results=5)


# =============================================================================
# Test: Does NOT scrape PATENTSCOPE result.jsf pages
# =============================================================================
class TestWIPONoScraping:
    """Tests verifying WIPO does not scrape web pages."""

    def test_does_not_call_result_jsf(self):
        """Verify connector does NOT call PATENTSCOPE result.jsf pages."""
        http = MagicMock()
        
        connector = WIPOConnector(http_client=http)
        query = ConnectorQuery(inn="aspirin")

        connector.search(query, run_id="test_run")

        # Verify no HTTP calls to result.jsf
        assert http.get.call_count == 0, "Should NOT make GET requests to PATENTSCOPE"
        assert http.post.call_count == 0, "Should NOT make POST requests to PATENTSCOPE"

    def test_does_not_parse_html(self):
        """Verify connector does not attempt HTML parsing."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        # Should not raise any HTML parsing errors
        result = connector.search(query, run_id="test_run")

        assert result is not None
        # No sources should be returned (no HTML parsing)
        assert result.results_returned == 0

    def test_returns_immediately_without_api_call(self):
        """Verify _search returns immediately without making API calls."""
        call_count = 0

        class TrackingHTTPClient:
            def get(self, *args, **kwargs):
                nonlocal call_count
                call_count += 1
                return MagicMock(status_code=200)

            def post(self, *args, **kwargs):
                nonlocal call_count
                call_count += 1
                return MagicMock(status_code=200)

            def head(self, *args, **kwargs):
                nonlocal call_count
                call_count += 1
                return MagicMock(status_code=200)

        connector = WIPOConnector(http_client=TrackingHTTPClient())
        query = ConnectorQuery(inn="aspirin")

        connector._search(query)  # Direct call to _search, not search()

        # _search should NOT make any HTTP calls
        assert call_count == 0, f"_search should not make HTTP calls, but made {call_count}"


# =============================================================================
# Test: HTTP 403 returns source_unavailable (not fatal)
# =============================================================================
class TestWIPO403Handling:
    """Tests for HTTP 403 handling."""

    def test_403_returns_source_unavailable(self):
        """Verify 403 returns source_unavailable warning."""
        http = MagicMock()
        http.head.return_value = MagicMock(status_code=403)

        connector = WIPOConnector(http_client=http)
        query = ConnectorQuery(inn="aspirin")

        result = connector.search_with_connectivity_check(query, run_id="test_run")

        assert result.results_returned == 0
        assert len(result.errors) == 0, "403 should NOT produce errors list"
        assert len(result.warnings) > 0
        assert "source_unavailable" in result.warnings[0]

    def test_403_does_not_raise_exception(self):
        """Verify 403 does not raise exceptions."""
        http = MagicMock()
        http.head.return_value = MagicMock(status_code=403)

        connector = WIPOConnector(http_client=http)
        query = ConnectorQuery(inn="aspirin")

        # Should not raise
        result = connector.search_with_connectivity_check(query, run_id="test_run")

        assert result is not None
        assert result.connector_name == "wipo"

    def test_403_mentions_blocked_access(self):
        """Verify 403 warning mentions blocked access."""
        http = MagicMock()
        http.head.return_value = MagicMock(status_code=403)

        connector = WIPOConnector(http_client=http)
        query = ConnectorQuery(inn="aspirin")

        result = connector.search_with_connectivity_check(query, run_id="test_run")

        warning_text = " ".join(result.warnings).lower()
        assert "403" in warning_text or "forbidden" in warning_text or "blocked" in warning_text


# =============================================================================
# Test: Returns source_unavailable immediately
# =============================================================================
class TestWIPOSourceUnavailable:
    """Tests for source_unavailable behavior."""

    def test_returns_source_unavailable(self):
        """Verify connector returns source_unavailable immediately."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        assert result.results_returned == 0
        assert len(result.warnings) > 0
        assert "source_unavailable" in result.warnings[0]

    def test_no_results_returned(self):
        """Verify no sources are returned."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        assert len(result.sources) == 0
        assert len(result.evidence_items) == 0

    def test_explains_no_rest_api(self):
        """Verify warning explains WIPO has no REST API."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        warning_text = " ".join(result.warnings).lower()
        assert "rest api" in warning_text or "api" in warning_text


# =============================================================================
# Test: Run continues without WIPO data
# =============================================================================
class TestWIPORunContinuation:
    """Tests verifying the run continues when WIPO is unavailable."""

    def test_no_exception_raised(self):
        """Verify no exceptions are raised."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        # Should not raise
        result = connector.search(query, run_id="test_run")

        assert result is not None

    def test_result_is_valid(self):
        """Verify returned result is valid and usable."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        # Result should be valid
        assert result.connector_name == "wipo"
        assert result.query == query
        # No fatal errors
        assert len(result.errors) == 0

    def test_network_error_handled_gracefully(self):
        """Verify network errors are handled gracefully."""
        http = MagicMock()
        http.head.side_effect = Exception("Network error")

        connector = WIPOConnector(http_client=http)
        query = ConnectorQuery(inn="aspirin")

        # Should not raise
        result = connector.search_with_connectivity_check(query, run_id="test_run")

        assert result is not None
        assert "source_unavailable" in " ".join(result.warnings)

    def test_timeout_handled_gracefully(self):
        """Verify timeout errors are handled gracefully."""
        http = MagicMock()
        http.head.side_effect = httpx.TimeoutException("Timeout")

        connector = WIPOConnector(http_client=http)
        query = ConnectorQuery(inn="aspirin")

        # Should not raise
        result = connector.search_with_connectivity_check(query, run_id="test_run")

        assert result is not None
        assert "source_unavailable" in " ".join(result.warnings)


# =============================================================================
# Test: Warnings include helpful alternatives
# =============================================================================
class TestWIPOAlternativeSuggestions:
    """Tests for helpful alternative suggestions in warnings."""

    def test_suggests_epo_ops(self):
        """Verify warning suggests EPO OPS as alternative."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        warning_text = " ".join(result.warnings).lower()
        assert "epo" in warning_text

    def test_suggests_uspto(self):
        """Verify warning suggests USPTO as alternative."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        warning_text = " ".join(result.warnings).lower()
        assert "uspto" in warning_text

    def test_suggests_the_lens(self):
        """Verify warning suggests The Lens as alternative."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        warning_text = " ".join(result.warnings).lower()
        assert "lens" in warning_text

    def test_provides_manual_search_url(self):
        """Verify warning provides manual search URL."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        warning_text = " ".join(result.warnings)
        assert "patentscope.wipo.int" in warning_text


# =============================================================================
# Test: Optional source failures don't fail the run
# =============================================================================
class TestWIPOOptionalSourceBehavior:
    """Tests verifying WIPO is treated as an optional source."""

    def test_errors_list_is_empty(self):
        """Verify errors list is empty (warnings only)."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        # Errors should be empty - only warnings for optional sources
        assert len(result.errors) == 0
        # But warnings should be present
        assert len(result.warnings) > 0

    def test_connector_name_correct(self):
        """Verify connector name is set correctly."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        assert result.connector_name == "wipo"

    def test_can_be_used_in_aggregator(self):
        """Verify connector returns valid result for aggregator consumption."""
        connector = WIPOConnector()
        query = ConnectorQuery(inn="aspirin")

        result = connector.search(query, run_id="test_run")

        # Result should be valid for aggregator
        assert result.connector_name is not None
        assert result.query is not None
        assert isinstance(result.sources, list)
        assert isinstance(result.evidence_items, list)
        assert isinstance(result.warnings, list)
        assert isinstance(result.errors, list)
