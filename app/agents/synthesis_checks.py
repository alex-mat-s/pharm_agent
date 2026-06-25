"""Deterministic contradiction detection helpers for the Synthesis Agent.

These pre-checks run before the LLM call to identify common contradictions
between previous stage outputs. The results are passed to the LLM and also
logged for audit purposes.
"""

from __future__ import annotations

import json
from typing import Any


def _safe_json_load(json_str: str | None) -> dict[str, Any]:
    """Safely parse JSON string, returning empty dict on failure."""
    if not json_str:
        return {}
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return {}


def _get_confidence(output: dict[str, Any]) -> str:
    """Extract confidence level from agent output."""
    return output.get("confidence", "medium")


def _has_weak_evidence(output: dict[str, Any]) -> bool:
    """Check if scientific output indicates weak evidence."""
    confidence = _get_confidence(output)
    evidence_gaps = output.get("evidence_gaps", [])
    scientific_risks = output.get("scientific_risks", [])
    
    if confidence == "low":
        return True
    if len(evidence_gaps) >= 3:
        return True
    if len(scientific_risks) >= 3:
        return True
    return False


def _has_high_fto_risk(output: dict[str, Any]) -> bool:
    """Check if patent/finance output indicates high FTO risk."""
    fto_risks = output.get("freedom_to_operate_risks", [])
    for risk in fto_risks:
        if isinstance(risk, dict) and risk.get("severity") == "high":
            return True
    return False


def _assumes_premium_adoption(output: dict[str, Any]) -> bool:
    """Check if market output assumes premium or rapid adoption."""
    market_summary = output.get("market_summary", "").lower()
    payer_value = (output.get("payer_value") or "").lower()
    
    premium_indicators = [
        "premium pric",
        "high adoption",
        "rapid uptake",
        "strong demand",
        "high willingness to pay",
        "unmet need is high",
    ]
    
    text = f"{market_summary} {payer_value}"
    return any(ind in text for ind in premium_indicators)


def _assumes_near_term_launch(output: dict[str, Any]) -> bool:
    """Check if patent/finance output assumes near-term launch."""
    timeline = output.get("money_timeline", {})
    if not timeline:
        return False
    
    earliest = (timeline.get("earliest_value_inflection") or "").lower()
    revenue = (timeline.get("revenue_window") or "").lower()
    
    near_term_indicators = ["1-2 year", "2-3 year", "near-term", "immediate", "soon"]
    text = f"{earliest} {revenue}"
    return any(ind in text for ind in near_term_indicators)


def _relies_on_fda_approval(output: dict[str, Any]) -> bool:
    """Check if conclusions rely heavily on FDA approval data."""
    assumptions = output.get("assumptions", [])
    missing = output.get("missing_information", [])
    
    for item in assumptions + missing:
        if isinstance(item, str) and "fda" in item.lower():
            return True
    
    # Check sources
    sources = output.get("sources", [])
    fda_count = sum(1 for s in sources if isinstance(s, dict) and "fda" in s.get("source_type", "").lower())
    return fda_count >= 2


def _has_missing_investment_assumptions(output: dict[str, Any]) -> bool:
    """Check if investment range exists but has no assumptions."""
    inv_range = output.get("investment_range", {})
    if not inv_range:
        return False
    
    # Check if any case has assumptions
    for case_name in ["low_case", "base_case", "high_case"]:
        case = inv_range.get(case_name, {})
        if isinstance(case, dict):
            assumptions = case.get("assumptions", [])
            if assumptions:
                return False
    
    return True


# ============================================================================
# Public contradiction detection functions
# ============================================================================


def detect_scientific_market_contradictions(
    scientific_json: str | None,
    market_json: str | None,
) -> list[dict[str, Any]]:
    """Detect contradictions between scientific and market outputs.
    
    Returns a list of contradiction candidates.
    """
    contradictions: list[dict[str, Any]] = []
    
    sci = _safe_json_load(scientific_json)
    mkt = _safe_json_load(market_json)
    
    if not sci or not mkt:
        return contradictions
    
    # Check: weak scientific evidence but premium market adoption assumed
    if _has_weak_evidence(sci) and _assumes_premium_adoption(mkt):
        contradictions.append({
            "area": "scientific_vs_market",
            "description": (
                "Scientific evidence is weak (low confidence or multiple evidence gaps), "
                "but market analysis assumes premium pricing or rapid adoption."
            ),
            "affected_conclusion": "commercial_attractiveness",
            "severity": "medium",
            "source_agent_outputs": ["scientific", "market"],
        })
    
    # Check: high scientific risks but optimistic market summary
    sci_risks = sci.get("scientific_risks", [])
    mkt_confidence = _get_confidence(mkt)
    if len(sci_risks) >= 3 and mkt_confidence == "high":
        contradictions.append({
            "area": "scientific_vs_market",
            "description": (
                "Scientific analysis identifies 3+ significant risks, "
                "but market analysis has high confidence."
            ),
            "affected_conclusion": "overall_viability",
            "severity": "medium",
            "source_agent_outputs": ["scientific", "market"],
        })
    
    # Check: unmet need assessment mismatch
    sci_unmet = sci.get("unmet_medical_need", {})
    if isinstance(sci_unmet, dict):
        sci_unmet_claim = sci_unmet.get("claim", "").lower()
    else:
        sci_unmet_claim = str(sci_unmet).lower()
    
    mkt_summary = mkt.get("market_summary", "").lower()
    
    if "low unmet" in sci_unmet_claim and "high demand" in mkt_summary:
        contradictions.append({
            "area": "scientific_vs_market",
            "description": (
                "Scientific analysis suggests low unmet medical need, "
                "but market analysis claims high demand."
            ),
            "affected_conclusion": "market_opportunity",
            "severity": "high",
            "source_agent_outputs": ["scientific", "market"],
        })
    
    return contradictions


def detect_patent_market_contradictions(
    patent_finance_json: str | None,
    market_json: str | None,
) -> list[dict[str, Any]]:
    """Detect contradictions between patent/finance and market outputs.
    
    Returns a list of contradiction candidates.
    """
    contradictions: list[dict[str, Any]] = []
    
    pat = _safe_json_load(patent_finance_json)
    mkt = _safe_json_load(market_json)
    
    if not pat or not mkt:
        return contradictions
    
    # Check: high FTO risk but market assumes strong competitive position
    if _has_high_fto_risk(pat):
        mkt_summary = mkt.get("market_summary", "").lower()
        diff_opps = mkt.get("differentiation_opportunities", [])
        
        if "strong position" in mkt_summary or "competitive advantage" in mkt_summary:
            contradictions.append({
                "area": "patent_vs_market",
                "description": (
                    "Patent analysis identifies high FTO risks, "
                    "but market analysis assumes strong competitive position."
                ),
                "affected_conclusion": "commercial_viability",
                "severity": "high",
                "source_agent_outputs": ["patent_finance", "market"],
            })
        
        if len(diff_opps) >= 3:
            contradictions.append({
                "area": "patent_vs_market",
                "description": (
                    "Patent analysis identifies high FTO risks, "
                    "but market analysis lists multiple differentiation opportunities "
                    "that may be blocked by patents."
                ),
                "affected_conclusion": "differentiation_strategy",
                "severity": "medium",
                "source_agent_outputs": ["patent_finance", "market"],
            })
    
    # Check: near-term launch assumed but high patent risk
    blocking_patents = pat.get("blocking_patent_candidates", [])
    if len(blocking_patents) >= 2 and _assumes_near_term_launch(pat):
        contradictions.append({
            "area": "patent_vs_timeline",
            "description": (
                "Multiple blocking patent candidates exist, "
                "but timeline assumes near-term value inflection."
            ),
            "affected_conclusion": "monetization_timeline",
            "severity": "high",
            "source_agent_outputs": ["patent_finance"],
        })
    
    return contradictions


def detect_finance_evidence_contradictions(
    patent_finance_json: str | None,
    scientific_json: str | None,
) -> list[dict[str, Any]]:
    """Detect contradictions between finance assumptions and evidence base.
    
    Returns a list of contradiction candidates.
    """
    contradictions: list[dict[str, Any]] = []
    
    pat = _safe_json_load(patent_finance_json)
    sci = _safe_json_load(scientific_json)
    
    if not pat:
        return contradictions
    
    # Check: investment range without assumptions
    if _has_missing_investment_assumptions(pat):
        contradictions.append({
            "area": "finance_evidence",
            "description": (
                "Investment range is provided but lacks explicit assumptions. "
                "This makes the financial estimate unverifiable."
            ),
            "affected_conclusion": "investment_range",
            "severity": "medium",
            "source_agent_outputs": ["patent_finance"],
        })
    
    # Check: optimistic timeline but weak evidence
    if sci and _has_weak_evidence(sci) and _assumes_near_term_launch(pat):
        contradictions.append({
            "area": "finance_vs_science",
            "description": (
                "Scientific evidence is weak, but financial projections "
                "assume near-term monetization."
            ),
            "affected_conclusion": "monetization_timeline",
            "severity": "high",
            "source_agent_outputs": ["patent_finance", "scientific"],
        })
    
    # Check: high financial risks but high confidence
    fin_risks = pat.get("key_financial_risks", [])
    pat_confidence = _get_confidence(pat)
    high_severity_risks = sum(
        1 for r in fin_risks
        if isinstance(r, dict) and r.get("severity") == "high"
    )
    
    if high_severity_risks >= 2 and pat_confidence == "high":
        contradictions.append({
            "area": "finance_confidence",
            "description": (
                "Multiple high-severity financial risks identified, "
                "but overall confidence is rated high."
            ),
            "affected_conclusion": "financial_viability",
            "severity": "medium",
            "source_agent_outputs": ["patent_finance"],
        })
    
    return contradictions


def detect_source_coverage_gaps(
    source_warnings: list[str],
    scientific_json: str | None = None,
    market_json: str | None = None,
    patent_finance_json: str | None = None,
) -> list[dict[str, Any]]:
    """Detect contradictions arising from source availability issues.
    
    Returns a list of contradiction candidates.
    """
    contradictions: list[dict[str, Any]] = []
    
    # Check for FDA unavailability
    fda_unavailable = any("fda" in w.lower() and "unavailable" in w.lower() for w in source_warnings)
    
    if fda_unavailable:
        pat = _safe_json_load(patent_finance_json)
        if pat and _relies_on_fda_approval(pat):
            contradictions.append({
                "area": "source_coverage",
                "description": (
                    "FDA data source was unavailable, but patent/finance analysis "
                    "relies on FDA approval assumptions."
                ),
                "affected_conclusion": "regulatory_pathway",
                "severity": "high",
                "source_agent_outputs": ["patent_finance"],
            })
    
    # Check for PubMed unavailability affecting scientific conclusions
    pubmed_unavailable = any("pubmed" in w.lower() and "unavailable" in w.lower() for w in source_warnings)
    
    if pubmed_unavailable:
        sci = _safe_json_load(scientific_json)
        sci_confidence = _get_confidence(sci) if sci else "unknown"
        if sci_confidence in ("high", "medium"):
            contradictions.append({
                "area": "source_coverage",
                "description": (
                    "PubMed was unavailable, but scientific analysis confidence "
                    f"is rated '{sci_confidence}'. Evidence base may be incomplete."
                ),
                "affected_conclusion": "scientific_rationale",
                "severity": "medium",
                "source_agent_outputs": ["scientific"],
            })
    
    # Check for clinical trials unavailability
    ct_unavailable = any(
        ("clinicaltrials" in w.lower() or "clinical trials" in w.lower())
        and "unavailable" in w.lower()
        for w in source_warnings
    )
    
    if ct_unavailable:
        sci = _safe_json_load(scientific_json)
        if sci:
            trials = sci.get("clinical_trial_landscape", [])
            if not trials:
                contradictions.append({
                    "area": "source_coverage",
                    "description": (
                        "ClinicalTrials.gov was unavailable and no trial data was found. "
                        "Pipeline assessment may be incomplete."
                    ),
                    "affected_conclusion": "clinical_pipeline",
                    "severity": "medium",
                    "source_agent_outputs": ["scientific"],
                })
    
    # General warning if many sources unavailable
    unavailable_count = sum(1 for w in source_warnings if "unavailable" in w.lower())
    if unavailable_count >= 3:
        contradictions.append({
            "area": "source_coverage",
            "description": (
                f"{unavailable_count} data sources were unavailable. "
                "Overall analysis reliability is significantly reduced."
            ),
            "affected_conclusion": "overall_conclusion",
            "severity": "high",
            "source_agent_outputs": ["scientific", "market", "patent_finance"],
        })
    
    return contradictions


def run_all_contradiction_checks(
    scientific_json: str | None,
    market_json: str | None,
    patent_finance_json: str | None,
    source_warnings: list[str],
) -> list[dict[str, Any]]:
    """Run all contradiction detection checks and return combined results.
    
    This is the main entry point for pre-LLM contradiction detection.
    """
    all_contradictions: list[dict[str, Any]] = []
    
    all_contradictions.extend(
        detect_scientific_market_contradictions(scientific_json, market_json)
    )
    all_contradictions.extend(
        detect_patent_market_contradictions(patent_finance_json, market_json)
    )
    all_contradictions.extend(
        detect_finance_evidence_contradictions(patent_finance_json, scientific_json)
    )
    all_contradictions.extend(
        detect_source_coverage_gaps(
            source_warnings,
            scientific_json,
            market_json,
            patent_finance_json,
        )
    )
    
    return all_contradictions
