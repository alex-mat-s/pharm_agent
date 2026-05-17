"""Tests for market agent schemas (MVP 3)."""

from __future__ import annotations

import pytest

from app.schemas.market import (
    CommercialRisk,
    CompetitorEntry,
    MarketAgentInput,
    MarketAgentOutput,
    MarketDynamic,
    PatientPopulation,
    PriceBenchmark,
)


def test_market_agent_input_minimal():
    inp = MarketAgentInput(run_id="r1", inn_preferred="metformin")
    assert inp.run_id == "r1"
    assert inp.inn_preferred == "metformin"
    assert inp.molecule_type == "unknown"
    assert inp.pdf_hashes == {}


def test_market_agent_input_full():
    inp = MarketAgentInput(
        run_id="r2",
        inn_preferred="metformin",
        inn_english="metformin",
        inn_synonyms=["glucophage"],
        disease_preferred="type 2 diabetes",
        disease_synonyms=["T2DM"],
        region="EU",
        molecule_type="small_molecule",
        stage="approved",
        scientific_summary="summary text",
        approved_therapies_json='[{"drug_name": "insulin"}]',
        clinical_pipeline_json='[{"drug_name": "sema", "phase": "3"}]',
        unmet_need="cardiovascular risk reduction",
        pdf_hashes={"f1.pdf": "abc123"},
    )
    assert inp.region == "EU"
    assert len(inp.inn_synonyms) == 1


def test_market_agent_output_minimal():
    out = MarketAgentOutput(
        market_summary="Test summary",
        patient_population=PatientPopulation(),
    )
    assert out.confidence == "medium"
    assert out.competitors == []
    assert out.commercial_risks == []


def test_market_agent_output_full():
    out = MarketAgentOutput(
        market_summary="Large market",
        patient_population=PatientPopulation(
            global_estimate="~400M",
            target_segment="T2DM inadequately controlled",
            segmentation_logic="Based on HbA1c > 7%",
        ),
        treatment_landscape="Metformin is first-line",
        competitors=[
            CompetitorEntry(drug_name="sitagliptin", company="Merck", status="approved"),
            CompetitorEntry(drug_name="empagliflozin", company="BI", status="approved"),
        ],
        market_dynamics=[
            MarketDynamic(description="Growing obesity epidemic", direction="positive"),
            MarketDynamic(description="Patent cliff for DPP-4i", direction="negative"),
        ],
        payer_value="Strong HEOR data",
        pricing_logic="Reference pricing in EU",
        competitor_price_benchmarks=[
            PriceBenchmark(drug_name="sitagliptin", price_description="~$350/month"),
        ],
        commercial_risks=[
            CommercialRisk(risk="Generic erosion", severity="high", mitigation="Reformulation"),
        ],
        differentiation_opportunities=["CV benefit", "Weight loss"],
        market_size_estimate="$50B globally",
        confidence="high",
        assumptions=["Stable regulatory environment"],
        missing_information=["No RU pricing data"],
        sources=[],
    )
    assert len(out.competitors) == 2
    assert out.confidence == "high"
    assert out.market_size_estimate == "$50B globally"


def test_competitor_entry_defaults():
    c = CompetitorEntry(drug_name="test")
    assert c.status == "unknown"
    assert c.source_ids == []


def test_commercial_risk_severity():
    r = CommercialRisk(risk="Patent expiry")
    assert r.severity == "medium"

    r2 = CommercialRisk(risk="Biosimilar entry", severity="high")
    assert r2.severity == "high"


def test_market_dynamic_direction():
    d = MarketDynamic(description="Price erosion", direction="negative")
    assert d.direction == "negative"

    with pytest.raises(Exception):
        MarketDynamic(description="bad", direction="invalid")  # type: ignore[arg-type]
