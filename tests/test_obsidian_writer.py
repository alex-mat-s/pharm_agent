"""Tests for Obsidian writer, including scientific memo and patent notes generation."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.obsidian.writer import (
    ensure_vault_structure,
    slugify,
    write_scientific_memo,
    write_run_note,
    write_patent_evidence_note,
    write_patent_family_note,
    write_patent_aggregator_report,
)
from app.schemas.evidence import SourceRecord, SourceType
from app.schemas.ru_patent import (
    AggregatedPatentResult,
    BlockingRisk,
    LegalStatus,
    PatentEvidence,
    PatentFamilyEvidence,
    PatentQuery,
    PatentSearchResult,
    PatentType,
)
from app.schemas.run import RunRecord, RunStatus
from app.schemas.scientific import ScientificAgentOutput, SourceClaim, ApprovedTherapy


@pytest.fixture
def vault_dir(tmp_path):
    return tmp_path / "vault"


@pytest.fixture(autouse=True)
def _patch_config(vault_dir, monkeypatch):
    mock_config = MagicMock()
    mock_config.vault_dir = vault_dir
    monkeypatch.setattr("app.obsidian.writer.config", mock_config)


def test_ensure_vault_structure_creates_dirs(vault_dir):
    result = ensure_vault_structure(vault_dir)
    assert (vault_dir / "04_reports").exists()
    assert (vault_dir / "02_sources" / "pubmed").exists()
    assert (vault_dir / "02_sources" / "clinicaltrials").exists()


def test_slugify():
    assert slugify("Acetylsalicylic Acid") == "acetylsalicylic-acid"
    assert slugify("") == "unknown"
    assert slugify("123-test") == "123-test"


def test_write_scientific_memo_creates_file(vault_dir):
    output = ScientificAgentOutput(
        executive_summary="Aspirin shows promise for stroke prevention.",
        mechanism_of_action=SourceClaim(claim="COX-1 inhibition", source_ids=["pubmed:123"]),
        approved_therapies=[ApprovedTherapy(name="Aspirin", regulatory_status="Approved", source_ids=["fda:NDA001"])],
        evidence_gaps=["No long-term safety data"],
        confidence="medium",
    )
    sources = [
        SourceRecord(
            source_id="pubmed:123",
            source_type=SourceType.pubmed,
            title="Aspirin Study",
            retrieved_at="2026-01-01T00:00:00+00:00",
            query_used="aspirin",
            citation_label="Smith A. Aspirin Study. 2024.",
        ),
    ]

    path = write_scientific_memo(
        run_id="run_test_001",
        output=output,
        sources=sources,
        coverage={"pubmed": "ok (1 results)"},
        pdf_hashes={"source_1": "abc123"},
        vault_dir=vault_dir,
    )

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Scientific Memo" in content
    assert "Aspirin shows promise" in content
    assert "COX-1 inhibition" in content
    assert "pubmed:123" in content
    assert "No long-term safety data" in content
    assert "Disclaimer" in content
    assert "run_test_001" in content


def test_scientific_memo_preserves_manual_sections(vault_dir):
    output = ScientificAgentOutput(executive_summary="First version")
    path = write_scientific_memo(
        run_id="run_test_002",
        output=output,
        vault_dir=vault_dir,
    )

    content = path.read_text(encoding="utf-8")
    path.write_text(content + "\n## My Manual Notes\n\nKeep this!\n", encoding="utf-8")

    output2 = ScientificAgentOutput(executive_summary="Updated version")
    path2 = write_scientific_memo(
        run_id="run_test_002",
        output=output2,
        vault_dir=vault_dir,
    )

    updated = path2.read_text(encoding="utf-8")
    assert "Updated version" in updated
    assert "Keep this!" in updated


def test_write_run_note(vault_dir):
    record = RunRecord(
        run_id="run_test_003",
        status=RunStatus.completed,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        raw_input_json='{"inn_raw": "aspirin"}',
    )
    path = write_run_note(record, vault_dir=vault_dir)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "run_test_003" in content
    assert "completed" in content


def test_scientific_memo_with_empty_output(vault_dir):
    output = ScientificAgentOutput()
    path = write_scientific_memo(
        run_id="run_empty",
        output=output,
        vault_dir=vault_dir,
    )
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Not assessed" in content or "No executive summary" in content


# ---------------------------------------------------------------------------
# Patent Obsidian writer tests
# ---------------------------------------------------------------------------


def test_write_patent_evidence_note(vault_dir):
    patent = PatentEvidence(
        source_id="rospatent:RU2123456",
        source_type="rospatent",
        jurisdiction="RU",
        document_number="RU2123456",
        title="Test Patent for Ibuprofen Composition",
        applicants=["Pharma Corp"],
        patent_holders=["Pharma Corp"],
        inventors=["Dr. Smith"],
        filing_date="2020-01-01",
        priority_date="2019-12-01",
        publication_date="2021-06-15",
        grant_date="2021-12-01",
        legal_status=LegalStatus.active,
        ipc_codes=["A61K31/192", "C07C69/96"],
        cpc_codes=["A61K31/192"],
        blocking_risk_preliminary=BlockingRisk.medium,
        relevance_reason="Composition of matter covers ibuprofen salt",
        patent_types=[PatentType.composition_of_matter, PatentType.formulation],
        abstract="A novel ibuprofen composition...",
        source_url="https://searchplatform.rospatent.gov.ru/doc/RU2123456",
        retrieved_at="2024-01-01T00:00:00Z",
        warnings=["Legal status verified manually"],
    )

    path = write_patent_evidence_note(patent, run_id="run_patent_001", vault_dir=vault_dir)

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Test Patent for Ibuprofen Composition" in content
    assert "RU2123456" in content
    assert "active" in content
    assert "medium" in content
    assert "Pharma Corp" in content
    assert "A61K31/192" in content
    assert "Legal status verified manually" in content
    assert "run_patent_001" in content
    assert "02_sources/patents/rospatent" in str(path)


def test_write_patent_family_note(vault_dir):
    member1 = PatentEvidence(
        source_id="rospatent:RU2123456",
        source_type="rospatent",
        jurisdiction="RU",
        document_number="RU2123456",
        title="Ibuprofen Composition",
        legal_status=LegalStatus.active,
        blocking_risk_preliminary=BlockingRisk.high,
        retrieved_at="2024-01-01T00:00:00Z",
    )
    member2 = PatentEvidence(
        source_id="epo_ops:EP1234567",
        source_type="epo_ops",
        jurisdiction="EP",
        document_number="EP1234567",
        title="Ibuprofen Composition",
        legal_status=LegalStatus.active,
        blocking_risk_preliminary=BlockingRisk.medium,
        retrieved_at="2024-01-01T00:00:00Z",
    )

    family = PatentFamilyEvidence(
        family_id="family:abc123def456",
        members=[member1, member2],
        jurisdictions=["RU", "EP"],
        earliest_priority_date="2019-12-01",
        main_applicants=["Pharma Corp"],
        highest_blocking_risk=BlockingRisk.high,
        blocking_jurisdictions=["RU"],
        patent_types=[PatentType.composition_of_matter],
    )

    path = write_patent_family_note(family, run_id="run_patent_002", vault_dir=vault_dir)

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "family:abc123def456" in content
    assert "RU" in content
    assert "EP" in content
    assert "Pharma Corp" in content
    assert "HIGH" in content or "high" in content
    assert "RU2123456" in content
    assert "EP1234567" in content
    assert "02_sources/patents/families" in str(path)


def test_write_patent_aggregator_report(vault_dir):
    query = PatentQuery(inn="ibuprofen", indication="pain")

    patent = PatentEvidence(
        source_id="rospatent:RU2123456",
        source_type="rospatent",
        jurisdiction="RU",
        document_number="RU2123456",
        title="Test Patent",
        legal_status=LegalStatus.active,
        blocking_risk_preliminary=BlockingRisk.medium,
        retrieved_at="2024-01-01T00:00:00Z",
    )

    result = AggregatedPatentResult(
        query=query,
        all_patents=[patent],
        sources_queried=["rospatent", "fips", "eapo"],
        sources_available=["rospatent"],
        sources_unavailable=["fips", "eapo"],
        requires_manual_review=True,
        manual_review_reasons=["Primary RU sources unavailable"],
        total_warnings=["FIPS search requires manual verification"],
    )

    path = write_patent_aggregator_report(
        run_id="run_patent_003",
        result=result,
        vault_dir=vault_dir,
    )

    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Patent Search Report" in content
    assert "ibuprofen" in content
    assert "pain" in content
    assert "run_patent_003" in content
    assert "Manual Review Required" in content
    assert "Primary RU sources unavailable" in content
    assert "FIPS search requires manual verification" in content
    assert "04_reports/patent_finance" in str(path)


def test_patent_evidence_note_preserves_manual_sections(vault_dir):
    patent = PatentEvidence(
        source_id="rospatent:RU9999999",
        source_type="rospatent",
        jurisdiction="RU",
        document_number="RU9999999",
        title="Test",
        legal_status=LegalStatus.unknown,
        blocking_risk_preliminary=BlockingRisk.unknown,
        retrieved_at="2024-01-01T00:00:00Z",
    )

    path = write_patent_evidence_note(patent, run_id="run_test", vault_dir=vault_dir)
    content = path.read_text(encoding="utf-8")
    path.write_text(content + "\n## My Manual Notes\n\nKeep this!\n", encoding="utf-8")

    patent2 = PatentEvidence(
        source_id="rospatent:RU9999999",
        source_type="rospatent",
        jurisdiction="RU",
        document_number="RU9999999",
        title="Updated Test",
        legal_status=LegalStatus.active,
        blocking_risk_preliminary=BlockingRisk.low,
        retrieved_at="2024-01-02T00:00:00Z",
    )
    path2 = write_patent_evidence_note(patent2, run_id="run_test", vault_dir=vault_dir)

    updated = path2.read_text(encoding="utf-8")
    assert "Updated Test" in updated
    assert "Keep this!" in updated
