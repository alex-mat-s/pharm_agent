"""Tests for Patent Aggregator."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from app.connectors.patent_aggregator import PatentAggregator
from app.schemas.ru_patent import (
    AggregatedPatentResult,
    BlockingRisk,
    LegalStatus,
    PatentEvidence,
    PatentFamilyEvidence,
    PatentQuery,
    PatentSearchResult,
)


class TestPatentAggregator:
    """Tests for PatentAggregator."""

    @pytest.fixture
    def aggregator(self):
        """Create a patent aggregator with caching disabled."""
        return PatentAggregator(use_cache=False)

    @pytest.fixture
    def sample_query(self):
        """Create a sample patent query."""
        return PatentQuery(
            inn="ibuprofen",
            inn_russian="ибупрофен",
            inn_synonyms=["ibuprofenum"],
            indication="pain",
        )

    @pytest.fixture
    def sample_patent(self):
        """Create a sample patent evidence."""
        return PatentEvidence(
            source_id="rospatent:RU2123456",
            source_type="rospatent",
            jurisdiction="RU",
            document_number="RU2123456",
            title="Test Patent",
            applicants=["Test Company"],
            filing_date="2020-01-01",
            priority_date="2019-12-01",
            retrieved_at="2024-01-01T00:00:00Z",
        )

    def test_get_disclaimers(self, aggregator):
        """Test that disclaimers are returned."""
        en, ru = aggregator.get_disclaimers()

        assert "not constitute a legal freedom-to-operate opinion" in en
        assert "не является юридическим заключением" in ru

    def test_cluster_by_family_empty(self, aggregator):
        """Test clustering with empty list."""
        families = aggregator.cluster_by_family([])
        assert families == []

    def test_cluster_by_family_single_patent(self, aggregator, sample_patent):
        """Test clustering with single patent."""
        families = aggregator.cluster_by_family([sample_patent])

        assert len(families) == 1
        assert len(families[0].members) == 1
        assert families[0].members[0].source_id == sample_patent.source_id

    def test_cluster_by_family_related_patents(self, aggregator):
        """Test clustering of related patents."""
        patent1 = PatentEvidence(
            source_id="rospatent:RU2123456",
            source_type="rospatent",
            jurisdiction="RU",
            document_number="RU2123456",
            title="Ibuprofen Composition",
            applicants=["Pharma Corp"],
            priority_date="2019-01-01",
            retrieved_at="2024-01-01T00:00:00Z",
        )
        patent2 = PatentEvidence(
            source_id="epo_ops:EP1234567",
            source_type="epo_ops",
            jurisdiction="EP",
            document_number="EP1234567",
            title="Ibuprofen Composition",
            applicants=["Pharma Corp"],
            priority_date="2019-01-01",
            retrieved_at="2024-01-01T00:00:00Z",
        )

        families = aggregator.cluster_by_family([patent1, patent2])

        # Should be clustered together due to same priority date and applicant
        assert len(families) == 1
        assert len(families[0].members) == 2
        assert set(families[0].jurisdictions) == {"RU", "EP"}

    def test_cluster_by_family_unrelated_patents(self, aggregator):
        """Test that unrelated patents stay separate."""
        patent1 = PatentEvidence(
            source_id="rospatent:RU2123456",
            source_type="rospatent",
            jurisdiction="RU",
            document_number="RU2123456",
            title="Patent A",
            applicants=["Company A"],
            priority_date="2019-01-01",
            retrieved_at="2024-01-01T00:00:00Z",
        )
        patent2 = PatentEvidence(
            source_id="rospatent:RU2654321",
            source_type="rospatent",
            jurisdiction="RU",
            document_number="RU2654321",
            title="Patent B",
            applicants=["Company B"],
            priority_date="2020-06-15",
            retrieved_at="2024-01-01T00:00:00Z",
        )

        families = aggregator.cluster_by_family([patent1, patent2])

        # Should be in separate families
        assert len(families) == 2

    def test_are_related_same_priority_and_applicant(self, aggregator):
        """Test _are_related with same priority date and applicant."""
        patent1 = PatentEvidence(
            source_id="p1",
            source_type="rospatent",
            jurisdiction="RU",
            document_number="RU1",
            title="Test",
            applicants=["Company X"],
            priority_date="2020-01-01",
            retrieved_at="2024-01-01T00:00:00Z",
        )
        patent2 = PatentEvidence(
            source_id="p2",
            source_type="rospatent",
            jurisdiction="RU",
            document_number="RU2",
            title="Test",
            applicants=["Company X"],
            priority_date="2020-01-01",
            retrieved_at="2024-01-01T00:00:00Z",
        )

        assert aggregator._are_related(patent1, patent2) is True

    def test_are_related_different_priority(self, aggregator):
        """Test _are_related with different priority dates."""
        patent1 = PatentEvidence(
            source_id="p1",
            source_type="rospatent",
            jurisdiction="RU",
            document_number="RU1",
            title="Different Title A",
            applicants=["Company X"],
            priority_date="2020-01-01",
            retrieved_at="2024-01-01T00:00:00Z",
        )
        patent2 = PatentEvidence(
            source_id="p2",
            source_type="rospatent",
            jurisdiction="RU",
            document_number="RU2",
            title="Different Title B",
            applicants=["Company Y"],
            priority_date="2021-06-15",
            retrieved_at="2024-01-01T00:00:00Z",
        )

        assert aggregator._are_related(patent1, patent2) is False

    def test_assess_manual_review_no_sources(self, aggregator, sample_query):
        """Test manual review assessment when no sources available."""
        result = AggregatedPatentResult(
            query=sample_query,
            sources_available=[],
            sources_queried=["rospatent", "fips"],
        )

        aggregator._assess_manual_review(result)

        assert result.requires_manual_review is True
        assert len(result.manual_review_reasons) > 0
        assert any("No patent sources" in r for r in result.manual_review_reasons)

    def test_assess_manual_review_no_patents(self, aggregator, sample_query):
        """Test manual review assessment when no patents found."""
        result = AggregatedPatentResult(
            query=sample_query,
            sources_available=["rospatent"],
            sources_queried=["rospatent"],
            all_patents=[],
        )

        aggregator._assess_manual_review(result)

        assert result.requires_manual_review is True
        assert any("No patents found" in r for r in result.manual_review_reasons)

    def test_assess_manual_review_unknown_status(self, aggregator, sample_query, sample_patent):
        """Test manual review assessment with unknown legal status."""
        sample_patent.legal_status = LegalStatus.unknown
        result = AggregatedPatentResult(
            query=sample_query,
            sources_available=["rospatent"],
            sources_queried=["rospatent"],
            all_patents=[sample_patent],
        )

        aggregator._assess_manual_review(result)

        assert result.requires_manual_review is True
        assert any("unknown legal status" in r for r in result.manual_review_reasons)

    def test_create_family_basic(self, aggregator, sample_patent):
        """Test _create_family with basic patent."""
        family = aggregator._create_family([sample_patent])

        assert family.family_id.startswith("family:")
        assert len(family.members) == 1
        assert family.earliest_priority_date == sample_patent.priority_date
        assert "Test Company" in family.main_applicants


class TestPatentAggregatorIntegration:
    """Integration tests for PatentAggregator with mocked connectors."""

    @pytest.fixture
    def aggregator(self):
        """Create aggregator with caching disabled."""
        return PatentAggregator(use_cache=False)

    @pytest.fixture
    def sample_query(self):
        """Create sample query."""
        return PatentQuery(inn="aspirin")

    @patch("app.connectors.patent_aggregator.RospatentConnector")
    @patch("app.connectors.patent_aggregator.FIPSSearchConnector")
    @patch("app.connectors.patent_aggregator.EAPORegistryConnector")
    def test_search_all_sources_basic(
        self,
        mock_eapo,
        mock_fips,
        mock_rospatent,
        aggregator,
        sample_query,
    ):
        """Test basic search_all_sources flow."""
        # Setup mocks
        mock_rospatent_instance = Mock()
        mock_rospatent_instance.search_patents.return_value = PatentSearchResult(
            connector_name="rospatent",
            query=sample_query,
            patents=[],
            source_available=True,
            warnings=["No API key"],
        )
        mock_rospatent.return_value = mock_rospatent_instance

        mock_fips_instance = Mock()
        mock_fips_instance.search_patents.return_value = PatentSearchResult(
            connector_name="fips",
            query=sample_query,
            patents=[],
            source_available=True,
            warnings=["Manual search required"],
        )
        mock_fips.return_value = mock_fips_instance

        mock_eapo_instance = Mock()
        mock_eapo_instance.search_patents.return_value = PatentSearchResult(
            connector_name="eapo",
            query=sample_query,
            patents=[],
            source_available=True,
            warnings=["EAPO search info"],
        )
        mock_eapo.return_value = mock_eapo_instance

        # Execute
        result = aggregator.search_all_sources(
            sample_query,
            run_id="test-run",
            include_international=False,
        )

        # Verify
        assert "rospatent" in result.sources_queried
        assert "fips" in result.sources_queried
        assert "eapo" in result.sources_queried
        assert result.requires_manual_review is True  # No patents found

    def test_search_all_sources_handles_exceptions(self, aggregator, sample_query):
        """Test that exceptions from connectors don't crash the aggregator."""
        with patch.object(
            aggregator,
            "_get_rospatent",
            side_effect=Exception("Connection error"),
        ):
            # Should not raise
            result = aggregator.search_all_sources(
                sample_query,
                run_id="test-run",
                include_international=False,
            )

            assert "rospatent" in result.sources_unavailable
            assert any("error" in w.lower() for w in result.total_warnings)
