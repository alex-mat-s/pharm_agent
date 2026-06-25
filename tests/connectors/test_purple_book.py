"""Tests for Purple Book connector.

Tests verify:
1. Uses /drug/drugsfda.json, NOT /drug/nda.json
2. Uses products.active_ingredients.name (plural), NOT products.active_ingredient
3. should_use_purple_book() returns False for small_molecule
4. should_use_purple_book() returns True for biologic types
5. 404 returns no_results (not fatal)
6. 403 returns source_unavailable (not fatal)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.connectors.purple_book import (
    OPENFDA_DRUGSFDA,
    PurpleBookConnector,
    should_use_purple_book,
)
from app.schemas.evidence import ConnectorQuery, SourceType


@pytest.fixture(autouse=True)
def _clear_audit_log(tmp_path, monkeypatch):
    mock_config = MagicMock()
    mock_config.logs_dir = tmp_path / "logs"
    mock_config.logs_dir.mkdir()
    mock_config.ensure_dirs = lambda: None
    mock_config.fda_proxy_url = None
    monkeypatch.setattr("app.logging.audit_logger.config", mock_config)
    monkeypatch.setattr("app.connectors.purple_book.config", mock_config)


# =============================================================================
# Test: should_use_purple_book() molecule type routing
# =============================================================================
class TestShouldUsePurpleBook:
    """Tests for molecule_type-based routing."""

    def test_small_molecule_returns_false(self):
        """Purple Book should be skipped for small_molecule drugs."""
        assert should_use_purple_book("small_molecule") is False
        assert should_use_purple_book("small molecule") is False
        assert should_use_purple_book("SMALL_MOLECULE") is False

    def test_chemical_returns_false(self):
        """Purple Book should be skipped for chemical/synthetic drugs."""
        assert should_use_purple_book("chemical") is False
        assert should_use_purple_book("synthetic") is False

    def test_biologic_returns_true(self):
        """Purple Book should be used for biologics."""
        assert should_use_purple_book("biologic") is True
        assert should_use_purple_book("BIOLOGIC") is True

    def test_biosimilar_returns_true(self):
        """Purple Book should be used for biosimilars."""
        assert should_use_purple_book("biosimilar") is True

    def test_antibody_returns_true(self):
        """Purple Book should be used for antibodies."""
        assert should_use_purple_book("antibody") is True
        assert should_use_purple_book("monoclonal_antibody") is True

    def test_protein_returns_true(self):
        """Purple Book should be used for proteins."""
        assert should_use_purple_book("protein") is True
        assert should_use_purple_book("recombinant_protein") is True

    def test_vaccine_returns_true(self):
        """Purple Book should be used for vaccines."""
        assert should_use_purple_book("vaccine") is True

    def test_gene_therapy_returns_true(self):
        """Purple Book should be used for gene therapies."""
        assert should_use_purple_book("gene_therapy") is True
        assert should_use_purple_book("cell_therapy") is True

    def test_none_returns_false(self):
        """Unknown molecule type should skip Purple Book."""
        assert should_use_purple_book(None) is False

    def test_empty_string_returns_false(self):
        """Empty molecule type should skip Purple Book."""
        assert should_use_purple_book("") is False

    def test_unknown_type_returns_false(self):
        """Unknown molecule type should skip Purple Book."""
        assert should_use_purple_book("unknown") is False
        assert should_use_purple_book("other") is False

    def test_hydroxychloroquine_scenario(self):
        """Hydroxychloroquine is a small molecule - should NOT use Purple Book."""
        # Simulating real-world scenario where molecule_type is "small_molecule"
        assert should_use_purple_book("small_molecule") is False


# =============================================================================
# Test: Correct endpoint used (/drug/drugsfda.json, NOT /drug/nda.json)
# =============================================================================
class TestPurpleBookEndpoint:
    """Tests for correct API endpoint usage."""

    def test_uses_drugsfda_endpoint_not_nda(self):
        """Verify OPENFDA_DRUGSFDA constant is correct."""
        assert OPENFDA_DRUGSFDA == "https://api.fda.gov/drug/drugsfda.json"
        assert "nda.json" not in OPENFDA_DRUGSFDA

    def test_connector_calls_drugsfda_endpoint(self):
        """Verify connector makes requests to /drug/drugsfda.json."""
        http = MagicMock()
        http.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": []},
        )

        connector = PurpleBookConnector(http_client=http)
        query = ConnectorQuery(inn="adalimumab")

        connector.search(query, run_id="test_run")

        # Verify the correct URL was called
        for call in http.get.call_args_list:
            url = call[0][0] if call[0] else call[1].get("url", "")
            assert "drugsfda.json" in url, f"Expected drugsfda.json in URL, got: {url}"
            assert "nda.json" not in url, f"Should NOT use nda.json, got: {url}"

    def test_nda_json_endpoint_never_used(self):
        """Explicitly verify /drug/nda.json is never used."""
        http = MagicMock()
        http.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": []},
        )

        connector = PurpleBookConnector(http_client=http)
        query = ConnectorQuery(inn="adalimumab")

        connector.search(query, run_id="test_run")

        # Check all calls
        for call in http.get.call_args_list:
            url = call[0][0] if call[0] else ""
            assert "/drug/nda.json" not in url, \
                f"FORBIDDEN: /drug/nda.json endpoint used: {url}"


# =============================================================================
# Test: Correct field names (active_ingredients, NOT active_ingredient)
# =============================================================================
class TestPurpleBookFieldNames:
    """Tests for correct field name usage."""

    def test_uses_active_ingredients_plural(self):
        """Verify connector uses 'active_ingredients' (plural) not 'active_ingredient'."""
        http = MagicMock()
        http.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": []},
        )

        connector = PurpleBookConnector(http_client=http)
        query = ConnectorQuery(inn="adalimumab")

        connector.search(query, run_id="test_run")

        # Check search params
        for call in http.get.call_args_list:
            params = call[1].get("params", {})
            search_str = params.get("search", "")
            
            # If active_ingredient is in the query, it must be plural
            if "active_ingredient" in search_str.lower():
                assert "active_ingredients" in search_str, \
                    f"Expected 'active_ingredients' (plural), got: {search_str}"
                # Verify NOT singular
                assert "products.active_ingredient:" not in search_str, \
                    f"Should NOT use singular 'active_ingredient': {search_str}"

    def test_search_strategies_use_correct_fields(self):
        """Verify all search strategies use correct field names."""
        http = MagicMock()
        http.get.return_value = MagicMock(
            status_code=404,  # Force all strategies to be tried
            json=lambda: {},
        )

        connector = PurpleBookConnector(http_client=http)
        query = ConnectorQuery(inn="adalimumab")

        connector.search(query, run_id="test_run")

        # Collect all search queries
        search_queries = []
        for call in http.get.call_args_list:
            params = call[1].get("params", {})
            search_str = params.get("search", "")
            search_queries.append(search_str)

        # Verify expected queries are present
        assert any("products.active_ingredients.name" in q for q in search_queries), \
            f"Expected 'products.active_ingredients.name' in queries: {search_queries}"
        
        # Verify wrong field is NOT present
        assert not any("products.active_ingredient:" in q for q in search_queries), \
            f"Should NOT use singular 'products.active_ingredient:': {search_queries}"


# =============================================================================
# Test: 404 returns no_results (not fatal)
# =============================================================================
class TestPurpleBook404Handling:
    """Tests for 404 error handling."""

    def test_404_returns_no_results_not_fatal(self):
        """Verify 404 returns no_results warning, not error."""
        http = MagicMock()
        http.get.return_value = MagicMock(
            status_code=404,
            json=lambda: {},
        )

        connector = PurpleBookConnector(http_client=http)
        query = ConnectorQuery(inn="unknowndrug")

        result = connector.search(query, run_id="test_run")

        assert result.results_returned == 0
        assert len(result.errors) == 0, "404 should NOT produce errors"
        assert len(result.warnings) > 0, "404 should produce a warning"
        assert "no_results" in result.warnings[0], \
            f"Warning should indicate 'no_results': {result.warnings[0]}"

    def test_404_allows_fallback_strategies(self):
        """Verify 404 on first strategy tries next strategies."""
        call_count = 0

        class CountingHTTPClient:
            def get(self, url, **kwargs):
                nonlocal call_count
                call_count += 1
                resp = MagicMock()
                resp.status_code = 404
                resp.json.return_value = {}
                return resp

        connector = PurpleBookConnector(http_client=CountingHTTPClient())
        query = ConnectorQuery(inn="adalimumab")

        connector.search(query, run_id="test_run")

        # Should try multiple strategies before giving up
        assert call_count >= 2, f"Should try multiple strategies, only tried {call_count}"


# =============================================================================
# Test: 403 returns source_unavailable (not fatal)
# =============================================================================
class TestPurpleBook403Handling:
    """Tests for 403 error handling."""

    def test_403_returns_source_unavailable(self):
        """Verify 403 returns source_unavailable warning."""
        http = MagicMock()
        http.get.return_value = MagicMock(
            status_code=403,
            json=lambda: {},
        )

        connector = PurpleBookConnector(http_client=http)
        query = ConnectorQuery(inn="adalimumab")

        result = connector.search(query, run_id="test_run")

        assert result.results_returned == 0
        assert len(result.errors) == 0, "403 should NOT produce errors list"
        assert len(result.warnings) > 0, "403 should produce a warning"
        assert "source_unavailable" in result.warnings[0], \
            f"Warning should indicate 'source_unavailable': {result.warnings[0]}"

    def test_403_stops_fallback_iteration(self):
        """Verify 403 stops trying additional strategies."""
        call_count = 0

        class CountingHTTPClient:
            def get(self, url, **kwargs):
                nonlocal call_count
                call_count += 1
                resp = MagicMock()
                resp.status_code = 403
                resp.json.return_value = {}
                return resp

        connector = PurpleBookConnector(http_client=CountingHTTPClient())
        query = ConnectorQuery(inn="adalimumab")

        result = connector.search(query, run_id="test_run")

        assert call_count == 1, f"403 should stop after first query, but made {call_count} calls"
        assert "source_unavailable" in result.warnings[0]


# =============================================================================
# Test: Successful search
# =============================================================================
class TestPurpleBookSuccess:
    """Tests for successful searches."""

    def test_biologic_search_success(self):
        """Verify successful search for a biologic."""
        mock_result = {
            "application_number": "BLA125057",
            "sponsor_name": "AbbVie",
            "openfda": {
                "brand_name": ["HUMIRA"],
                "generic_name": ["ADALIMUMAB"],
            },
            "products": [
                {
                    "product_number": "001",
                    "dosage_form": "INJECTION",
                    "active_ingredients": [{"strength": "40 MG/0.8 ML"}],
                }
            ],
            "application_type": "BLA",
        }

        http = MagicMock()
        http.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [mock_result]},
        )

        connector = PurpleBookConnector(http_client=http)
        query = ConnectorQuery(inn="adalimumab")

        result = connector.search(query, run_id="test_run")

        assert result.connector_name == "purple_book"
        assert result.results_returned == 1
        assert result.sources[0].source_type == SourceType.purple_book
        assert result.sources[0].external_id == "BLA125057"
        assert "AbbVie" in result.sources[0].publisher

    def test_filters_for_bla_applications(self):
        """Verify only BLA applications are returned."""
        mock_results = [
            {
                "application_number": "BLA125057",
                "sponsor_name": "AbbVie",
                "openfda": {"brand_name": ["HUMIRA"]},
                "products": [],
            },
            {
                "application_number": "NDA012345",  # Not a BLA
                "sponsor_name": "Other",
                "openfda": {"brand_name": ["Other Drug"]},
                "products": [],
            },
        ]

        http = MagicMock()
        http.get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": mock_results},
        )

        connector = PurpleBookConnector(http_client=http)
        query = ConnectorQuery(inn="adalimumab")

        result = connector.search(query, run_id="test_run")

        # Should only include BLA applications
        assert result.results_returned == 1
        assert result.sources[0].external_id == "BLA125057"


# =============================================================================
# Test: Run continues without Purple Book data
# =============================================================================
class TestPurpleBookRunContinuation:
    """Tests verifying the run continues when Purple Book is unavailable."""

    def test_source_unavailable_is_not_fatal(self):
        """Verify source_unavailable does not raise exceptions."""
        http = MagicMock()
        http.get.return_value = MagicMock(
            status_code=403,
            json=lambda: {},
        )

        connector = PurpleBookConnector(http_client=http)
        query = ConnectorQuery(inn="adalimumab")

        # Should not raise
        result = connector.search(query, run_id="test_run")

        # Result should be valid
        assert result is not None
        assert result.connector_name == "purple_book"
        # Run can continue - no fatal errors
        assert len(result.errors) == 0
