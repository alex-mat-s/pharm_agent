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
    PriceSensitivityAnalysis,
    PriceSensitivityScenario,
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


def test_price_sensitivity_scenario_minimal():
    """Test minimal PriceSensitivityScenario creation."""
    scenario = PriceSensitivityScenario(
        scenario_name="parity",
        price_vs_competitor="1x",
        expected_adoption="moderate",
        adoption_rationale="Same price as competitor, adoption depends on differentiation",
    )
    assert scenario.scenario_name == "parity"
    assert scenario.viability == "viable"  # default
    assert scenario.target_payers == []
    assert scenario.source_ids == []


def test_price_sensitivity_scenario_full():
    """Test full PriceSensitivityScenario with all fields."""
    scenario = PriceSensitivityScenario(
        scenario_name="premium_2x",
        price_vs_competitor="2x",
        expected_adoption="low",
        adoption_rationale="Premium pricing limits adoption to specialty markets",
        target_payers=["private_insurance", "self_pay"],
        viability="marginal",
        source_ids=["src:1", "src:2"],
    )
    assert scenario.expected_adoption == "low"
    assert scenario.viability == "marginal"
    assert len(scenario.target_payers) == 2


def test_price_sensitivity_scenario_adoption_values():
    """Test that expected_adoption accepts valid literal values."""
    for adoption in ["very_low", "low", "moderate", "high", "very_high"]:
        scenario = PriceSensitivityScenario(
            scenario_name="test",
            price_vs_competitor="1x",
            expected_adoption=adoption,  # type: ignore[arg-type]
            adoption_rationale="test",
        )
        assert scenario.expected_adoption == adoption


def test_price_sensitivity_scenario_viability_values():
    """Test that viability accepts valid literal values."""
    for viability in ["not_viable", "marginal", "viable", "attractive"]:
        scenario = PriceSensitivityScenario(
            scenario_name="test",
            price_vs_competitor="1x",
            expected_adoption="moderate",
            adoption_rationale="test",
            viability=viability,  # type: ignore[arg-type]
        )
        assert scenario.viability == viability


def test_price_sensitivity_analysis_minimal():
    """Test minimal PriceSensitivityAnalysis creation."""
    psa = PriceSensitivityAnalysis(
        conclusion="Premium pricing is not viable due to generic competition.",
    )
    assert psa.reference_drug is None
    assert psa.scenarios == []
    assert psa.confidence == "low"  # default
    assert psa.key_price_drivers == []
    assert psa.price_barriers == []


def test_price_sensitivity_analysis_full():
    """Test full PriceSensitivityAnalysis with scenarios."""
    psa = PriceSensitivityAnalysis(
        reference_drug="metformin",
        reference_price="$50/month",
        scenarios=[
            PriceSensitivityScenario(
                scenario_name="discount_20%",
                price_vs_competitor="0.8x",
                expected_adoption="high",
                adoption_rationale="Lower price drives adoption",
                viability="attractive",
            ),
            PriceSensitivityScenario(
                scenario_name="parity",
                price_vs_competitor="1x",
                expected_adoption="moderate",
                adoption_rationale="Competitive with standard of care",
                viability="viable",
            ),
            PriceSensitivityScenario(
                scenario_name="premium_2x",
                price_vs_competitor="2x",
                expected_adoption="very_low",
                adoption_rationale="Only specialty payers would accept",
                viability="not_viable",
            ),
        ],
        price_ceiling="$80/month",
        key_price_drivers=["Superior efficacy", "Better safety profile"],
        price_barriers=["Generic alternatives available", "Budget constraints"],
        willingness_to_pay_assessment="Payers willing to pay 20% premium for proven CV benefit",
        conclusion="Moderate premium (1.2-1.5x) is viable; 2x premium is not.",
        confidence="medium",
        assumptions=["Stable generic pricing", "No new entrants in 2 years"],
        source_ids=["pubmed:123", "fda:456"],
    )
    assert psa.reference_drug == "metformin"
    assert len(psa.scenarios) == 3
    assert psa.confidence == "medium"
    assert len(psa.key_price_drivers) == 2
    assert len(psa.price_barriers) == 2
    assert psa.price_ceiling == "$80/month"


def test_market_agent_output_with_price_sensitivity():
    """Test MarketAgentOutput with price_sensitivity_analysis field."""
    psa = PriceSensitivityAnalysis(
        reference_drug="sitagliptin",
        reference_price="$350/month",
        scenarios=[
            PriceSensitivityScenario(
                scenario_name="parity",
                price_vs_competitor="1x",
                expected_adoption="moderate",
                adoption_rationale="Competitive pricing",
            ),
        ],
        conclusion="Parity pricing is viable.",
        confidence="medium",
    )
    
    out = MarketAgentOutput(
        market_summary="Large market with price sensitivity",
        patient_population=PatientPopulation(target_segment="T2DM"),
        price_sensitivity_analysis=psa,
    )
    
    assert out.price_sensitivity_analysis is not None
    assert out.price_sensitivity_analysis.reference_drug == "sitagliptin"
    assert len(out.price_sensitivity_analysis.scenarios) == 1


def test_market_agent_output_without_price_sensitivity():
    """Test MarketAgentOutput without price_sensitivity_analysis (optional field)."""
    out = MarketAgentOutput(
        market_summary="Test summary",
        patient_population=PatientPopulation(),
    )
    assert out.price_sensitivity_analysis is None
