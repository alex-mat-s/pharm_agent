"""Tests for Orange Book connector."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.connectors.orange_book import OrangeBookConnector
from app.schemas.evidence import ConnectorQuery, EvidenceCategory, SourceType


@pytest.fixture
def connector() -> OrangeBookConnector:
    return OrangeBookConnector()


@pytest.fixture
def query() -> ConnectorQuery:
    return ConnectorQuery(inn="aspirin", max_results=5)


def test_orange_book_search_success(connector: OrangeBookConnector, query: ConnectorQuery) -> None:
    mock_response = {
        "results": [
            {
                "product_ndc": "12345-678-90",
                "brand_name": ["Bayer Aspirin"],
                "generic_name": ["aspirin"],
                "labeler_name": "Bayer",
                "active_ingredients": [{"name": "aspirin", "strength": "325 mg"}],
                "marketing_start_date": "2020-01-01",
                "dea_schedule": "",
            }
        ]
    }

    mock_http = MagicMock()
    mock_http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: mock_response,
        raise_for_status=lambda: None,
    )
    connector._http = mock_http

    result = connector._search(query)

    assert result.connector_name == "orange_book"
    assert result.results_returned == 1
    assert len(result.sources) == 1
    assert len(result.evidence_items) == 1

    src = result.sources[0]
    assert src.source_type == SourceType.orange_book
    assert src.external_id == "12345-678-90"
    assert "Bayer Aspirin" in src.title

    evi = result.evidence_items[0]
    assert evi.category == EvidenceCategory.regulatory
    assert evi.confidence == "high"


def test_orange_book_no_results(connector: OrangeBookConnector, query: ConnectorQuery) -> None:
    mock_http = MagicMock()
    mock_http.get.return_value = MagicMock(
        status_code=404,
        json=lambda: {},
        raise_for_status=lambda: (_ for _ in ()).throw(
            httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock(status_code=404))
        ),
    )
    connector._http = mock_http

    result = connector._search(query)

    assert result.results_returned == 0
    assert len(result.warnings) >= 1


def test_orange_book_multiple_strategies(connector: OrangeBookConnector, query: ConnectorQuery) -> None:
    """Connector tries multiple search strategies."""
    mock_response = {
        "results": [
            {
                "product_ndc": "11111-222-33",
                "brand_name": ["Aspirin Plus"],
                "generic_name": ["aspirin"],
                "labeler_name": "GenericCo",
                "active_ingredients": [{"name": "aspirin", "strength": "81 mg"}],
                "marketing_start_date": "2019-06-01",
            }
        ]
    }

    mock_http = MagicMock()
    mock_http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: mock_response,
        raise_for_status=lambda: None,
    )
    connector._http = mock_http

    result = connector._search(query)

    # Should have called get at least once for each strategy
    assert mock_http.get.call_count >= 1
    assert result.results_returned == 1


def test_orange_book_deduplication(connector: OrangeBookConnector, query: ConnectorQuery) -> None:
    """Same NDC should not appear twice."""
    mock_response = {
        "results": [
            {
                "product_ndc": "SAME-NDC",
                "brand_name": ["Brand1"],
                "generic_name": ["aspirin"],
                "labeler_name": "Co1",
                "active_ingredients": [{"name": "aspirin", "strength": "100 mg"}],
            },
            {
                "product_ndc": "SAME-NDC",
                "brand_name": ["Brand2"],
                "generic_name": ["aspirin"],
                "labeler_name": "Co2",
                "active_ingredients": [{"name": "aspirin", "strength": "200 mg"}],
            },
        ]
    }

    mock_http = MagicMock()
    mock_http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: mock_response,
        raise_for_status=lambda: None,
    )
    connector._http = mock_http

    result = connector._search(query)

    assert result.results_returned == 1  # Deduplicated


def test_orange_book_cyrillic_inn(connector: OrangeBookConnector) -> None:
    """Cyrillic INN should be handled gracefully."""
    query = ConnectorQuery(inn="ацетилсалициловая кислота", max_results=5)

    mock_response = {"results": []}
    mock_http = MagicMock()
    mock_http.get.return_value = MagicMock(
        status_code=200,
        json=lambda: mock_response,
        raise_for_status=lambda: None,
    )
    connector._http = mock_http

    result = connector._search(query)

    assert result.results_returned == 0
    assert len(result.warnings) >= 1
