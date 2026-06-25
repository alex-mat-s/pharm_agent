"""Tests for patent/finance agent schemas (MVP 4)."""

from __future__ import annotations

import pytest

from app.schemas.patent_finance import (
    BlockingPatent,
    FinancialRisk,
    FTORisk,
    InvestmentRange,
    InvestmentScenario,
    MoneyTimeline,
    PatentAssignee,
    PatentFenceOpportunity,
    PatentFinanceAgentInput,
    PatentFinanceAgentOutput,
)


def test_patent_finance_agent_input_minimal():
    inp = PatentFinanceAgentInput(run_id="r1", inn_preferred="aspirin")
    assert inp.run_id == "r1"
    assert inp.inn_preferred == "aspirin"
    assert inp.molecule_type == "unknown"
    assert inp.pdf_hashes == {}


def test_patent_finance_agent_input_full():
    inp = PatentFinanceAgentInput(
        run_id="r2",
        inn_preferred="aspirin",
        inn_english="aspirin",
        inn_synonyms=["acetylsalicylic acid"],
        disease_preferred="cardiovascular disease",
        disease_synonyms=["CVD"],
        region="US",
        molecule_type="small_molecule",
        stage="approved",
        scientific_summary="Anti-platelet agent",
        mechanism_of_action="COX inhibition",
        approved_therapies_json='[{"name": "aspirin"}]',
        market_summary="Large established market",
        competitors_json='[{"drug_name": "clopidogrel"}]',
        market_size_estimate="$10B globally",
        pdf_hashes={"f1.pdf": "abc123"},
    )
    assert inp.region == "US"
    assert inp.stage == "approved"


def test_investment_scenario():
    scenario = InvestmentScenario(
        amount_usd="$10M-$30M",
        assumptions=["Phase 2 only", "No major CMC work"],
    )
    assert scenario.amount_usd == "$10M-$30M"
    assert len(scenario.assumptions) == 2


def test_investment_range():
    inv_range = InvestmentRange(
        low_case=InvestmentScenario(amount_usd="$5M", assumptions=["Minimal"]),
        base_case=InvestmentScenario(amount_usd="$20M", assumptions=["Standard"]),
        high_case=InvestmentScenario(amount_usd="$50M", assumptions=["Full program"]),
    )
    assert inv_range.low_case.amount_usd == "$5M"
    assert inv_range.base_case.amount_usd == "$20M"
    assert inv_range.high_case.amount_usd == "$50M"


def test_money_timeline():
    timeline = MoneyTimeline(
        earliest_value_inflection="After Phase 1",
        licensing_window="Phase 2 readout",
        approval_window="2028-2030",
        revenue_window="2030+",
        monetization_scenarios=["License to pharma", "Continue to Phase 3"],
    )
    assert timeline.earliest_value_inflection == "After Phase 1"
    assert len(timeline.monetization_scenarios) == 2


def test_blocking_patent():
    pat = BlockingPatent(
        patent_number="US12345678",
        title="Method of treating CVD",
        assignee="Big Pharma Inc",
        priority_date="2015-01-01",
        expiration_date="2035-01-01",
        patent_type="method_of_treatment",
        blocking_rationale="Covers same indication",
        source_ids=["src-1"],
    )
    assert pat.patent_number == "US12345678"
    assert pat.patent_type == "method_of_treatment"


def test_fto_risk():
    risk = FTORisk(
        risk_description="Broad method claim",
        severity="high",
        mitigation_strategy="Design around with different dosing",
        source_ids=["src-1"],
    )
    assert risk.severity == "high"
    assert risk.requires_legal_review is True


def test_patent_fence_opportunity():
    opp = PatentFenceOpportunity(
        opportunity_description="Novel formulation",
        patent_type="formulation",
        feasibility="high",
    )
    assert opp.patent_type == "formulation"
    assert opp.feasibility == "high"


def test_financial_risk():
    risk = FinancialRisk(
        risk="Phase 3 failure",
        severity="high",
        mitigation="Adaptive trial design",
    )
    assert risk.severity == "high"


def test_patent_finance_agent_output_minimal():
    out = PatentFinanceAgentOutput(
        patent_landscape_summary="Test summary",
        investment_range=InvestmentRange(
            low_case=InvestmentScenario(amount_usd="$5M"),
            base_case=InvestmentScenario(amount_usd="$20M"),
            high_case=InvestmentScenario(amount_usd="$50M"),
        ),
        money_timeline=MoneyTimeline(),
    )
    assert out.confidence == "medium"
    assert out.legal_review_required is True
    assert out.blocking_patent_candidates == []


def test_patent_finance_agent_output_full():
    out = PatentFinanceAgentOutput(
        patent_landscape_summary="Complex IP landscape",
        blocking_patent_candidates=[
            BlockingPatent(patent_number="US111", title="Composition", blocking_rationale="Covers active"),
        ],
        patent_count_by_family={"family_1": 5, "family_2": 3},
        main_assignees=[PatentAssignee(name="Pharma Co", patent_count=5)],
        earliest_priority_dates=["2015-01-01"],
        expected_expirations=["2035-01-01"],
        freedom_to_operate_risks=[
            FTORisk(risk_description="Formulation patent", severity="medium"),
        ],
        patent_fence_opportunities=[
            PatentFenceOpportunity(opportunity_description="Salt form", patent_type="composition"),
        ],
        generic_or_biosimilar_risk="High after 2035",
        investment_range=InvestmentRange(
            low_case=InvestmentScenario(amount_usd="$10M"),
            base_case=InvestmentScenario(amount_usd="$30M"),
            high_case=InvestmentScenario(amount_usd="$100M"),
        ),
        major_cost_buckets=["Preclinical", "Phase 1", "Phase 2", "Phase 3", "Regulatory"],
        money_timeline=MoneyTimeline(
            earliest_value_inflection="Phase 1 completion",
            licensing_window="Phase 2",
        ),
        key_financial_risks=[
            FinancialRisk(risk="Development failure", severity="high"),
        ],
        confidence="medium",
        assumptions=["No major surprises"],
        missing_information=["No Orange Book data"],
        legal_review_required=True,
    )
    assert len(out.blocking_patent_candidates) == 1
    assert len(out.freedom_to_operate_risks) == 1
    assert out.confidence == "medium"
    assert out.legal_review_required is True
