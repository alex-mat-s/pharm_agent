"""Tests for Rospatent connector."""

from __future__ import annotations

from unittest.mock import Mock, patch

import httpx
import pytest

from app.connectors.ru_patent.rospatent import RospatentConnector
from app.schemas.ru_patent import LegalStatus, PatentQuery


class TestRospatentConnector:
    """Tests for RospatentConnector."""

    @pytest.fixture
    def mock_http_client(self):
        """Create a mock HTTP client."""
        return Mock(spec=httpx.Client)

    @pytest.fixture
    def connector(self, mock_http_client):
        """Create a Rospatent connector with mocked HTTP client."""
        return RospatentConnector(
            http_client=mock_http_client,
            use_cache=False,
            api_key=None,
        )

    @pytest.fixture
    def connector_with_api_key(self, mock_http_client):
        """Create a Rospatent connector with API key."""
        return RospatentConnector(
            http_client=mock_http_client,
            use_cache=False,
            api_key="test-api-key",
        )

    @pytest.fixture
    def sample_query(self):
        """Create a sample patent query."""
        return PatentQuery(
            inn="ibuprofen",
            inn_russian="ибупрофен",
            inn_synonyms=["ibuprofenum"],
            indication="pain",
        )

    def test_connector_name(self, connector):
        """Test connector name is set correctly."""
        assert connector.connector_name == "rospatent"

    def test_jurisdiction(self, connector):
        """Test jurisdiction is RU."""
        assert connector.jurisdiction == "RU"

    def test_has_api_key_without_key(self, connector):
        """Test _has_api_key returns False without key."""
        assert connector._has_api_key() is False

    def test_has_api_key_with_key(self, connector_with_api_key):
        """Test _has_api_key returns True with key."""
        assert connector_with_api_key._has_api_key() is True

    def test_search_without_api_key_returns_warning(self, connector, sample_query):
        """Test search without API key returns informational warning."""
        result = connector.search_patents(sample_query, run_id="test-run")

        assert result.connector_name == "rospatent"
        assert result.source_available is True
        assert len(result.warnings) > 0
        assert any("API key not configured" in w for w in result.warnings)

    def test_search_with_empty_query(self, connector):
        """Test search with empty INN."""
        query = PatentQuery(inn="")
        result = connector.search_patents(query, run_id="test-run")

        assert result.connector_name == "rospatent"
        # Should still work but with no terms

    def test_normalize_ru_patent_number(self, connector):
        """Test normalization of RU patent numbers."""
        assert connector._normalize_ru_patent_number("2123456") == "RU2123456"
        assert connector._normalize_ru_patent_number("RU2123456") == "RU2123456"
        assert connector._normalize_ru_patent_number("ru2123456") == "RU2123456"
        assert connector._normalize_ru_patent_number(" RU2123456 ") == "RU2123456"

    def test_normalize_date_yyyymmdd(self, connector):
        """Test date normalization from YYYYMMDD format."""
        assert connector._normalize_date("20231215") == "2023-12-15"

    def test_normalize_date_already_formatted(self, connector):
        """Test date normalization when already in correct format."""
        assert connector._normalize_date("2023-12-15") == "2023-12-15"

    def test_normalize_date_dd_mm_yyyy(self, connector):
        """Test date normalization from DD.MM.YYYY format."""
        assert connector._normalize_date("15.12.2023") == "2023-12-15"

    def test_normalize_date_none(self, connector):
        """Test date normalization with None."""
        assert connector._normalize_date(None) is None

    def test_expand_query_terms(self, connector, sample_query):
        """Test query term expansion."""
        terms = connector.expand_query_terms(sample_query)

        assert "ibuprofen" in terms
        assert "ибупрофен" in terms
        assert "ibuprofenum" in terms
        assert "pain" in terms

    def test_generate_manual_search_urls(self, connector):
        """Test generation of manual search URLs."""
        terms = ["ibuprofen", "aspirin"]
        urls = connector._generate_manual_search_urls(terms)

        assert len(urls) == 2
        assert "searchplatform.rospatent.gov.ru" in urls[0]
        assert "ibuprofen" in urls[0]

    def test_parse_legal_status_active(self, connector):
        """Test parsing active status."""
        assert connector._parse_legal_status("действует") == LegalStatus.active
        assert connector._parse_legal_status("active") == LegalStatus.active

    def test_parse_legal_status_expired(self, connector):
        """Test parsing expired status."""
        assert connector._parse_legal_status("истек") == LegalStatus.expired

    def test_parse_legal_status_unknown(self, connector):
        """Test parsing unknown status."""
        assert connector._parse_legal_status("something else") == LegalStatus.unknown

    def test_extract_list_field_list_value(self, connector):
        """Test extracting list field with list value."""
        data = {"applicants": ["Company A", "Company B"]}
        result = connector._extract_list_field(data, ["applicants"])
        assert result == ["Company A", "Company B"]

    def test_extract_list_field_string_value(self, connector):
        """Test extracting list field with string value."""
        data = {"applicant": "Company A"}
        result = connector._extract_list_field(data, ["applicants", "applicant"])
        assert result == ["Company A"]

    def test_extract_list_field_missing(self, connector):
        """Test extracting list field when missing."""
        data = {"other": "value"}
        result = connector._extract_list_field(data, ["applicants"])
        assert result == []


class TestRospatentConnectorAPI:
    """Tests for Rospatent connector API calls."""

    @pytest.fixture
    def mock_http_client(self):
        """Create a mock HTTP client."""
        client = Mock(spec=httpx.Client)
        return client

    @pytest.fixture
    def connector(self, mock_http_client):
        """Create connector with mocked HTTP and API key."""
        return RospatentConnector(
            http_client=mock_http_client,
            use_cache=False,
            api_key="test-api-key",
        )

    @pytest.fixture
    def sample_query(self):
        """Create a sample patent query."""
        return PatentQuery(inn="ibuprofen")

    def test_api_search_success(self, connector, mock_http_client, sample_query):
        """Test successful API search."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "hits": [
                {
                    "id": "RU2123456",
                    "title": "Test Patent",
                    "applicants": ["Test Company"],
                    "filing_date": "20200101",
                    "publication_date": "20210101",
                }
            ]
        }
        mock_http_client.post.return_value = mock_response

        result = connector.search_patents(sample_query, run_id="test-run")

        assert result.connector_name == "rospatent"
        assert result.source_available is True

    def test_api_search_401_unauthorized(self, connector, mock_http_client, sample_query):
        """Test API search with 401 response."""
        mock_response = Mock()
        mock_response.status_code = 401
        mock_http_client.post.return_value = mock_response

        patents, warnings = connector._search_via_api(["ibuprofen"], 10)

        assert patents == []
        assert any("Invalid or expired API key" in w for w in warnings)

    def test_api_search_403_forbidden(self, connector, mock_http_client, sample_query):
        """Test API search with 403 response."""
        mock_response = Mock()
        mock_response.status_code = 403
        mock_http_client.post.return_value = mock_response

        patents, warnings = connector._search_via_api(["ibuprofen"], 10)

        assert patents == []
        assert any("Access forbidden" in w for w in warnings)

    def test_api_search_timeout(self, connector, mock_http_client, sample_query):
        """Test API search with timeout."""
        mock_http_client.post.side_effect = httpx.TimeoutException("Timeout")

        patents, warnings = connector._search_via_api(["ibuprofen"], 10)

        assert patents == []
        assert any("timed out" in w for w in warnings)
