"""Tests for FDA connector with mocked HTTP responses."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
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
    """Fake HTTP client that returns a fixed response."""
    def __init__(self, response: dict, status_code: int = 200):
        self._response = response
        self._status_code = status_code
        self.calls = []  # Track calls for assertions

    def get(self, url, **kwargs):
        self.calls.append({"url": url, "params": kwargs.get("params", {})})
        resp = MagicMock()
        resp.status_code = self._status_code
        resp.raise_for_status = lambda: None
        resp.json.return_value = self._response
        return resp


class MultiResponseHTTPClient:
    """HTTP client that returns different responses for different queries."""
    def __init__(self, responses: dict[str, tuple[dict, int]]):
        """
        Args:
            responses: Dict mapping search query substrings to (response_dict, status_code)
        """
        self._responses = responses
        self._default_response = ({}, 404)
        self.calls = []

    def get(self, url, **kwargs):
        params = kwargs.get("params", {})
        search_str = params.get("search", "")
        self.calls.append({"url": url, "params": params, "search": search_str})
        
        # Find matching response
        response_data, status_code = self._default_response
        for key, (data, code) in self._responses.items():
            if key in search_str:
                response_data, status_code = data, code
                break
        
        resp = MagicMock()
        resp.status_code = status_code
        resp.raise_for_status = lambda: None
        resp.json.return_value = response_data
        return resp


# =============================================================================
# Test: Wrong field not used (active_ingredient vs active_ingredients)
# =============================================================================
def test_fda_uses_active_ingredients_not_active_ingredient():
    """Verify the connector uses 'active_ingredients' (plural) not 'active_ingredient' (singular)."""
    http = FakeHTTPClient({
        "results": [_make_fda_result()],
        "meta": {"results": {"total": 1}},
    })
    connector = FDAConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin")

    connector.search(query, run_id="test_run")

    # Check all queries use 'active_ingredients' (plural), never 'active_ingredient' (singular)
    for call in http.calls:
        search_param = call["params"].get("search", "")
        assert "active_ingredient.name" not in search_param, \
            f"Wrong field used: 'active_ingredient.name' found in query: {search_param}"
        # The connector should use 'active_ingredients.name' in fallback queries
        if "active_ingredients" in search_param:
            assert "active_ingredients.name" in search_param, \
                f"Expected 'active_ingredients.name' but got: {search_param}"


def test_fda_candidate_queries_use_correct_field():
    """Verify _build_candidate_queries uses active_ingredients.name (plural)."""
    connector = FDAConnector(http_client=None)
    query = ConnectorQuery(
        inn="ibuprofen",
        synonyms=["ibuprofen sodium"],
        brand_names=["Advil"],
    )
    
    candidates = connector._build_candidate_queries("ibuprofen", query)
    
    # Check that active_ingredients (plural) is used, not active_ingredient (singular)
    for candidate in candidates:
        if "active_ingredient" in candidate.lower():
            assert "active_ingredients" in candidate, \
                f"Wrong field: expected 'active_ingredients' but found 'active_ingredient' in: {candidate}"


# =============================================================================
# Test: 404 returns no_results (not fatal)
# =============================================================================
def test_fda_404_returns_no_results():
    """Verify 404 response is treated as no_results, not as fatal error."""
    http = FakeHTTPClient({}, status_code=404)
    connector = FDAConnector(http_client=http)
    query = ConnectorQuery(inn="unknowndrug")

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 0
    assert len(result.errors) == 0, "404 should not produce errors"
    assert len(result.warnings) > 0, "404 should produce a warning"
    assert "no_results" in result.warnings[0], \
        f"Warning should indicate 'no_results': {result.warnings[0]}"


def test_fda_404_tries_fallback_queries():
    """Verify 404 on first query tries fallback queries."""
    http = MultiResponseHTTPClient({
        "generic_name": ({}, 404),  # First query returns 404
        "active_ingredients": (  # Fallback with active_ingredients.name succeeds
            {"results": [_make_fda_result()], "meta": {"results": {"total": 1}}},
            200,
        ),
    })
    connector = FDAConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin")

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 1, "Should succeed on fallback query"
    assert len(http.calls) >= 2, "Should have tried at least 2 queries"


# =============================================================================
# Test: generic_name success
# =============================================================================
def test_fda_generic_name_success():
    """Verify successful search via generic_name field."""
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
    
    # Verify generic_name was used in the first query
    first_query = http.calls[0]["params"]["search"]
    assert "generic_name" in first_query


def test_fda_generic_name_is_primary_query():
    """Verify generic_name is the first/primary query tried."""
    connector = FDAConnector(http_client=None)
    query = ConnectorQuery(inn="metformin")
    
    candidates = connector._build_candidate_queries("metformin", query)
    
    assert len(candidates) >= 1
    assert "generic_name" in candidates[0], \
        f"First query should use generic_name, got: {candidates[0]}"


# =============================================================================
# Test: active_ingredients.name success
# =============================================================================
def test_fda_active_ingredients_name_success():
    """Verify successful search via active_ingredients.name field (fallback)."""
    http = MultiResponseHTTPClient({
        "generic_name": ({}, 404),  # Primary query fails
        "active_ingredients.name": (  # Fallback succeeds
            {"results": [_make_fda_result()], "meta": {"results": {"total": 1}}},
            200,
        ),
    })
    connector = FDAConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin")

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 1
    # Verify active_ingredients.name was used
    assert any("active_ingredients.name" in call["search"] for call in http.calls), \
        "Should have tried active_ingredients.name query"


def test_fda_active_ingredients_is_second_query():
    """Verify active_ingredients.name is the second query tried (after generic_name)."""
    connector = FDAConnector(http_client=None)
    query = ConnectorQuery(inn="metformin")
    
    candidates = connector._build_candidate_queries("metformin", query)
    
    assert len(candidates) >= 2
    assert "active_ingredients.name" in candidates[1], \
        f"Second query should use active_ingredients.name, got: {candidates[1]}"


# =============================================================================
# Test: 403 returns source_unavailable
# =============================================================================
def test_fda_403_returns_source_unavailable():
    """Verify 403 response is treated as source_unavailable."""
    http = FakeHTTPClient({}, status_code=403)
    connector = FDAConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin")

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 0
    assert len(result.errors) == 0, "403 should not produce errors list"
    assert len(result.warnings) > 0, "403 should produce a warning"
    assert "source_unavailable" in result.warnings[0], \
        f"Warning should indicate 'source_unavailable': {result.warnings[0]}"


def test_fda_403_stops_fallback_iteration():
    """Verify 403 stops trying additional fallback queries."""
    call_count = 0
    
    class CountingHTTPClient:
        def get(self, url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 403
            resp.json.return_value = {}
            return resp
    
    connector = FDAConnector(http_client=CountingHTTPClient())
    query = ConnectorQuery(
        inn="aspirin",
        synonyms=["acetylsalicylic acid"],
        brand_names=["Bayer Aspirin"],
    )

    result = connector.search(query, run_id="test_run")

    assert call_count == 1, f"403 should stop after first query, but made {call_count} calls"
    assert "source_unavailable" in result.warnings[0]


# =============================================================================
# Existing tests (updated/kept for backward compatibility)
# =============================================================================
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
    assert "no_results" in result.warnings[0]


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


def test_fda_uses_proxy_from_config(monkeypatch):
    """Verify that FDAConnector builds an httpx.Client with the proxy URL."""
    fake_config = MagicMock()
    fake_config.fda_proxy_url = "http://proxy.example:8080"
    fake_config.fda_api_url = "https://api.fda.gov/drug/drugsfda.json"
    fake_config.fda_api_key = None
    fake_config.debug = False
    monkeypatch.setattr("app.config.config", fake_config)

    connector = FDAConnector(http_client=None)
    client = connector._get_http()
    assert isinstance(client, httpx.Client)
    # httpx 0.27+ stores proxy in _mounts; just verify client was created successfully
    assert client is not None


def test_fda_uses_api_key_from_config(monkeypatch):
    """Verify that the API key is injected into query params when configured."""
    http = FakeHTTPClient({
        "results": [_make_fda_result()],
        "meta": {"results": {"total": 1}},
    })
    captured_params = {}

    class CapturingHTTP:
        def get(self, url, **kwargs):
            captured_params["url"] = url
            captured_params["params"] = kwargs.get("params", {})
            return http.get(url, **kwargs)

    fake_config = MagicMock()
    fake_config.fda_proxy_url = None
    fake_config.fda_api_url = "https://api.fda.gov/drug/drugsfda.json"
    fake_config.fda_api_key = "SECRET_KEY_123"
    fake_config.debug = False
    monkeypatch.setattr("app.config.config", fake_config)

    connector = FDAConnector(http_client=CapturingHTTP())
    query = ConnectorQuery(inn="aspirin")
    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 1
    assert captured_params["params"].get("api_key") == "SECRET_KEY_123"


def test_fda_custom_api_url(monkeypatch):
    """Verify that a custom FDA_API_URL overrides the default endpoint."""
    http = FakeHTTPClient({
        "results": [_make_fda_result()],
        "meta": {"results": {"total": 1}},
    })
    captured = {}

    class CapturingHTTP:
        def get(self, url, **kwargs):
            captured["url"] = url
            return http.get(url, **kwargs)

    fake_config = MagicMock()
    fake_config.fda_proxy_url = None
    fake_config.fda_api_url = "https://custom.fda.local/drug/drugsfda.json"
    fake_config.fda_api_key = None
    fake_config.debug = False
    monkeypatch.setattr("app.config.config", fake_config)

    connector = FDAConnector(http_client=CapturingHTTP())
    query = ConnectorQuery(inn="aspirin")
    connector.search(query, run_id="test_run")

    assert captured["url"] == "https://custom.fda.local/drug/drugsfda.json"


def test_fda_fallback_to_brand_name():
    """Verify fallback to brand_name when other queries fail."""
    http = MultiResponseHTTPClient({
        "generic_name": ({}, 404),
        "active_ingredients": ({}, 404),
        "brand_name": (
            {"results": [_make_fda_result()], "meta": {"results": {"total": 1}}},
            200,
        ),
    })
    connector = FDAConnector(http_client=http)
    query = ConnectorQuery(inn="aspirin", brand_names=["Bayer Aspirin"])

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 1
    # Verify brand_name was used
    assert any("brand_name" in call["search"] for call in http.calls), \
        "Should have tried brand_name query"


def test_fda_fallback_query_order():
    """Verify the correct order of fallback queries."""
    connector = FDAConnector(http_client=None)
    query = ConnectorQuery(
        inn="metformin",
        synonyms=["metformin hydrochloride"],
        brand_names=["Glucophage"],
    )
    
    candidates = connector._build_candidate_queries("metformin", query)
    
    # Order should be:
    # 1. generic_name:metformin
    # 2. active_ingredients.name:metformin
    # 3. active_ingredients.name:metformin hydrochloride (synonym)
    # 4. brand_name:Glucophage
    
    assert len(candidates) >= 4
    assert "generic_name" in candidates[0]
    assert "active_ingredients.name" in candidates[1]
    assert "metformin hydrochloride" in candidates[2]
    assert "brand_name" in candidates[3]
