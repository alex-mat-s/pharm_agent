"""Tests for EPO OPS connector."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.connectors.epo_ops import EPOOPSConnector
from app.schemas.evidence import ConnectorQuery, EvidenceCategory, SourceType


@pytest.fixture
def connector() -> EPOOPSConnector:
    return EPOOPSConnector()


@pytest.fixture
def connector_with_creds() -> EPOOPSConnector:
    return EPOOPSConnector(consumer_key="test_key", consumer_secret="test_secret")


@pytest.fixture
def query() -> ConnectorQuery:
    return ConnectorQuery(inn="aspirin", max_results=5)


def test_epo_ops_no_credentials(connector: EPOOPSConnector, query: ConnectorQuery) -> None:
    """Without credentials, connector returns gracefully with warning."""
    result = connector._search(query)

    assert result.connector_name == "epo_ops"
    assert result.results_returned == 0
    assert len(result.warnings) >= 1
    assert "credentials not configured" in result.warnings[0].lower()


def test_epo_ops_token_acquisition(connector_with_creds: EPOOPSConnector) -> None:
    """Test OAuth token acquisition."""
    mock_http = MagicMock()
    mock_http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"access_token": "test_token_123", "expires_in": 1200},
        raise_for_status=lambda: None,
    )
    connector_with_creds._http = mock_http

    token = connector_with_creds._get_access_token(mock_http)

    assert token == "test_token_123"
    assert connector_with_creds._access_token == "test_token_123"


def test_epo_ops_search_success(connector_with_creds: EPOOPSConnector, query: ConnectorQuery) -> None:
    """Test successful patent search with XML response."""
    xml_response = """<?xml version="1.0" encoding="UTF-8"?>
    <ops:world-patents-data xmlns:ops="http://ops.epo.org" xmlns:epo="http://www.epo.org/exchange">
        <ops:biblio-search total-result-count="1">
            <ops:search-result>
                <epo:exchange-document doc-number="1234567" country="EP" kind="B1" family-id="F12345">
                    <epo:bibliographic-data>
                        <epo:invention-title lang="en">Aspirin formulation for cardiovascular treatment</epo:invention-title>
                        <epo:publication-reference>
                            <epo:document-id>
                                <epo:date>20200115</epo:date>
                            </epo:document-id>
                        </epo:publication-reference>
                        <epo:parties>
                            <epo:applicants>
                                <epo:applicant>
                                    <epo:name>Bayer AG</epo:name>
                                </epo:applicant>
                            </epo:applicants>
                        </epo:parties>
                    </epo:bibliographic-data>
                </epo:exchange-document>
            </ops:search-result>
        </ops:biblio-search>
    </ops:world-patents-data>
    """

    mock_http = MagicMock()
    mock_http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"access_token": "test_token", "expires_in": 1200},
        raise_for_status=lambda: None,
    )
    mock_http.get.return_value = MagicMock(
        status_code=200,
        text=xml_response,
        raise_for_status=lambda: None,
    )
    connector_with_creds._http = mock_http

    result = connector_with_creds._search(query)

    assert result.connector_name == "epo_ops"
    assert result.results_returned == 1
    assert len(result.sources) == 1
    assert len(result.evidence_items) == 1

    src = result.sources[0]
    assert src.source_type == SourceType.epo_ops
    assert src.external_id == "EP1234567"
    assert "Bayer AG" in src.publisher

    evi = result.evidence_items[0]
    assert evi.category == EvidenceCategory.patent


def test_epo_ops_no_results(connector_with_creds: EPOOPSConnector, query: ConnectorQuery) -> None:
    xml_response = """<?xml version="1.0" encoding="UTF-8"?>
    <ops:world-patents-data xmlns:ops="http://ops.epo.org">
        <ops:biblio-search total-result-count="0">
        </ops:biblio-search>
    </ops:world-patents-data>
    """

    mock_http = MagicMock()
    mock_http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"access_token": "test_token", "expires_in": 1200},
        raise_for_status=lambda: None,
    )
    mock_http.get.return_value = MagicMock(
        status_code=200,
        text=xml_response,
        raise_for_status=lambda: None,
    )
    connector_with_creds._http = mock_http

    result = connector_with_creds._search(query)

    assert result.results_returned == 0
    assert len(result.warnings) >= 1


def test_epo_ops_http_error(connector_with_creds: EPOOPSConnector, query: ConnectorQuery) -> None:
    mock_http = MagicMock()
    mock_http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"access_token": "test_token", "expires_in": 1200},
        raise_for_status=lambda: None,
    )
    mock_http.get.return_value = MagicMock(
        status_code=500,
        text="Internal Server Error",
        raise_for_status=lambda: (_ for _ in ()).throw(
            httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock(status_code=500))
        ),
    )
    connector_with_creds._http = mock_http

    result = connector_with_creds._search(query)

    assert result.results_returned == 0
    assert len(result.errors) >= 1


def test_epo_ops_token_failure(connector_with_creds: EPOOPSConnector, query: ConnectorQuery) -> None:
    mock_http = MagicMock()
    mock_http.post.return_value = MagicMock(
        status_code=401,
        text="Unauthorized",
        raise_for_status=lambda: (_ for _ in ()).throw(
            httpx.HTTPStatusError("401", request=MagicMock(), response=MagicMock(status_code=401))
        ),
    )
    connector_with_creds._http = mock_http

    result = connector_with_creds._search(query)

    assert result.results_returned == 0
    assert len(result.warnings) >= 1
    assert "token" in result.warnings[0].lower()


def test_epo_ops_cyrillic_inn(connector_with_creds: EPOOPSConnector) -> None:
    """Cyrillic INN should be handled gracefully."""
    query = ConnectorQuery(inn="ацетилсалициловая кислота", max_results=5)

    xml_response = """<?xml version="1.0" encoding="UTF-8"?>
    <ops:world-patents-data xmlns:ops="http://ops.epo.org">
        <ops:biblio-search total-result-count="0">
        </ops:biblio-search>
    </ops:world-patents-data>
    """

    mock_http = MagicMock()
    mock_http.post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"access_token": "test_token", "expires_in": 1200},
        raise_for_status=lambda: None,
    )
    mock_http.get.return_value = MagicMock(
        status_code=200,
        text=xml_response,
        raise_for_status=lambda: None,
    )
    connector_with_creds._http = mock_http

    result = connector_with_creds._search(query)

    assert result.results_returned == 0
    assert len(result.warnings) >= 1
