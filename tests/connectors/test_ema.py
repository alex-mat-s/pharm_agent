"""Tests for EMA connector using public JSON data file."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.connectors.ema import EXPECTED_FIELDS, EMAConnector
from app.schemas.evidence import ConnectorQuery, SourceType


@pytest.fixture(autouse=True)
def _clear_audit_log(tmp_path, monkeypatch):
    mock_config = MagicMock()
    mock_config.logs_dir = tmp_path / "logs"
    mock_config.logs_dir.mkdir()
    mock_config.debug = False
    mock_config.ensure_dirs = lambda: None
    monkeypatch.setattr("app.logging.audit_logger.config", mock_config)
    monkeypatch.setattr("app.config.config", mock_config)


def _make_record(
    name: str = "Aspirin EC",
    substance: str = "acetylsalicylic acid",
    inn: str = "acetylsalicylic acid",
    status: str = "Authorised",
    product_number: str = "EMEA/H/C/000001",
) -> dict:
    return {
        "category": "human",
        "name_of_medicine": name,
        "ema_product_number": product_number,
        "medicine_status": status,
        "active_substance": substance,
        "international_non_proprietary_name_common_name": inn,
        "therapeutic_area_mesh": "Cardiovascular diseases",
        "atc_code_human": "B01AC06",
        "therapeutic_indication": "Prevention of cardiovascular events",
        "medicine_url": f"https://www.ema.europa.eu/en/medicines/human/EPAR/{name.lower().replace(' ', '-')}",
        "marketing_authorisation_developer_applicant_holder": "Bayer AG",
        "european_commission_decision_date": "2005-06-15",
    }


def _make_records(n: int = 5) -> list[dict]:
    """Generate n valid EMA records with varying names."""
    recs = [_make_record()]
    for i in range(1, n):
        recs.append(_make_record(
            name=f"TestMedicine{i}",
            substance=f"substance_{i}",
            inn=f"inn_{i}",
            product_number=f"EMEA/H/C/{i:06d}",
        ))
    return recs


class FakeResponse:
    def __init__(self, data, status_code: int = 200):
        self.status_code = status_code
        self._data = data
        self.headers = {"content-type": "application/json"}

    def json(self):
        if self._data is None:
            raise ValueError("No JSON body")
        return self._data

    def raise_for_status(self):
        pass


class FakeHTTPClient:
    def __init__(self, response_data, status_code: int = 200):
        self._response_data = response_data
        self._status_code = status_code

    def get(self, url, **kwargs):
        return FakeResponse(self._response_data, self._status_code)


# ------------------------------------------------------------------
# Test 1: valid EMA JSON — happy path
# ------------------------------------------------------------------

def test_ema_valid_json(tmp_path):
    records = _make_records(3)
    http = FakeHTTPClient(records)
    cache_dir = tmp_path / "ema_cache"
    connector = EMAConnector(http_client=http, cache_dir=cache_dir)
    query = ConnectorQuery(inn="acetylsalicylic acid")

    result = connector.search(query, run_id="test_run")

    assert result.connector_name == "ema"
    assert result.results_returned == 1
    assert result.errors == []
    assert result.sources[0].source_type == SourceType.ema
    assert "acetylsalicylic acid" in result.sources[0].citation_label

    assert (cache_dir / "ema_medicines_full.json").exists()


# ------------------------------------------------------------------
# Test 2: malformed JSON
# ------------------------------------------------------------------

def test_ema_malformed_json(tmp_path):
    http = MagicMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = json.JSONDecodeError("bad", "", 0)
    http.get.return_value = resp

    connector = EMAConnector(http_client=http, cache_dir=tmp_path / "cache")
    query = ConnectorQuery(inn="aspirin")

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 0
    assert any("source_unavailable" in e for e in result.errors)


# ------------------------------------------------------------------
# Test 3: missing expected fields
# ------------------------------------------------------------------

def test_ema_missing_expected_fields(tmp_path):
    records = [{"some_random_field": "value", "another": 123}]
    http = FakeHTTPClient(records)

    connector = EMAConnector(http_client=http, cache_dir=tmp_path / "cache")
    query = ConnectorQuery(inn="aspirin")

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 0

    hc = connector.healthcheck.__wrapped__ if hasattr(connector.healthcheck, "__wrapped__") else connector.healthcheck
    connector._medicines = None
    connector._http = FakeHTTPClient(records)._http if hasattr(FakeHTTPClient(records), '_http') else None
    connector._http = None

    http2 = FakeHTTPClient(records)
    connector2 = EMAConnector(http_client=http2, cache_dir=tmp_path / "cache2")
    hc_result = connector2.healthcheck(test_substance="aspirin")
    assert not hc_result.fields_ok
    assert any("Missing fields" in e for e in hc_result.errors)


# ------------------------------------------------------------------
# Test 4: HTTP 429 with cache fallback
# ------------------------------------------------------------------

def test_ema_429_with_cache(tmp_path):
    cache_dir = tmp_path / "ema_cache"
    cache_dir.mkdir()
    records = _make_records(2)
    (cache_dir / "ema_medicines_full.json").write_text(
        json.dumps(records, ensure_ascii=False), encoding="utf-8",
    )

    http_429 = FakeHTTPClient(None, status_code=429)
    connector = EMAConnector(http_client=http_429, cache_dir=cache_dir)
    query = ConnectorQuery(inn="acetylsalicylic acid")

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 1
    assert result.errors == []
    assert "acetylsalicylic acid" in result.sources[0].citation_label


# ------------------------------------------------------------------
# Test 5: HTTP 429 without cache
# ------------------------------------------------------------------

def test_ema_429_without_cache(tmp_path):
    http_429 = FakeHTTPClient(None, status_code=429)
    connector = EMAConnector(http_client=http_429, cache_dir=tmp_path / "empty_cache")
    query = ConnectorQuery(inn="aspirin")

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 0
    assert any("source_unavailable" in e for e in result.errors)


# ------------------------------------------------------------------
# Test 6: no matches for active substance
# ------------------------------------------------------------------

def test_ema_no_matches(tmp_path):
    records = _make_records(3)
    http = FakeHTTPClient(records)
    connector = EMAConnector(http_client=http, cache_dir=tmp_path / "cache")
    query = ConnectorQuery(inn="nonexistentdrug12345")

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 0
    assert result.errors == []


# ------------------------------------------------------------------
# Additional: cache reuse (no network call on second search)
# ------------------------------------------------------------------

def test_ema_cache_reuse(tmp_path):
    records = _make_records(2)
    call_count = {"n": 0}

    class CountingHTTP:
        def get(self, url, **kwargs):
            call_count["n"] += 1
            return FakeResponse(records)

    cache_dir = tmp_path / "ema_cache"
    connector = EMAConnector(http_client=CountingHTTP(), cache_dir=cache_dir)
    q = ConnectorQuery(inn="acetylsalicylic acid")

    connector.search(q, run_id="r1")
    assert call_count["n"] == 1

    connector.search(q, run_id="r2")
    assert call_count["n"] == 1  # no second HTTP call


# ------------------------------------------------------------------
# Additional: healthcheck happy path
# ------------------------------------------------------------------

def test_ema_healthcheck_happy(tmp_path):
    records = _make_records(200)
    http = FakeHTTPClient(records)
    connector = EMAConnector(http_client=http, cache_dir=tmp_path / "cache")

    hc = connector.healthcheck(test_substance="acetylsalicylic acid")

    assert hc.http_ok
    assert hc.json_ok
    assert hc.fields_ok
    assert hc.record_count == 200
    assert hc.search_ok
    assert hc.healthy


# ------------------------------------------------------------------
# Additional: cyrillic INN fallback to synonyms
# ------------------------------------------------------------------

def test_ema_cyrillic_inn_fallback(tmp_path):
    records = _make_records(3)
    http = FakeHTTPClient(records)
    connector = EMAConnector(http_client=http, cache_dir=tmp_path / "cache")
    query = ConnectorQuery(
        inn="ацетилсалициловая кислота",
        synonyms=["acetylsalicylic acid", "ASA"],
        brand_names=["Aspirin"],
    )

    result = connector.search(query, run_id="test_run")

    assert result.results_returned == 1
    assert "acetylsalicylic acid" in result.sources[0].citation_label
