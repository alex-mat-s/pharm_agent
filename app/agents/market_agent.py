"""Market Attractiveness Agent (MVP 3).

Analyzes market size, patient segments, dynamics, payer value,
pricing logic, competitor benchmarks, and commercial risks.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import config
from app.llm.structured_client import StructuredLLMClient
from app.schemas.evidence import EvidenceItem, SourceRecord
from app.schemas.market import MarketAgentInput, MarketAgentOutput

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"market_agent.{name}.md"
    return path.read_text(encoding="utf-8")


def _format_evidence_for_market(items: list[EvidenceItem]) -> str:
    """Format evidence items relevant to market analysis."""
    if not items:
        return "(No evidence items available)"

    lines: list[str] = []
    for item in items:
        findings = "; ".join(item.key_findings) if item.key_findings else ""
        line = f"- [{item.source_id}] {item.summary}"
        if findings:
            line += f"\n  Key findings: {findings}"
        lines.append(line)

    return "\n".join(lines[:50])


def _format_scientific_context(agent_input: MarketAgentInput) -> dict[str, str]:
    """Extract scientific context fields for the prompt."""
    approved = "(not available)"
    pipeline = "(not available)"

    if agent_input.approved_therapies_json:
        try:
            data = json.loads(agent_input.approved_therapies_json)
            if isinstance(data, list):
                items = []
                for t in data[:10]:
                    name = t.get("drug_name", t.get("name", "?"))
                    status = t.get("status", "")
                    items.append(f"- {name} ({status})")
                approved = "\n".join(items) if items else "(none found)"
        except json.JSONDecodeError:
            pass

    if agent_input.clinical_pipeline_json:
        try:
            data = json.loads(agent_input.clinical_pipeline_json)
            if isinstance(data, list):
                items = []
                for t in data[:15]:
                    name = t.get("drug_name", t.get("title", "?"))
                    phase = t.get("phase", "?")
                    status = t.get("status", "?")
                    items.append(f"- {name} (Phase {phase}, {status})")
                pipeline = "\n".join(items) if items else "(none found)"
        except json.JSONDecodeError:
            pass

    return {
        "scientific_summary": agent_input.scientific_summary or "(not available)",
        "unmet_need": agent_input.unmet_need or "(not assessed)",
        "approved_therapies": approved,
        "clinical_pipeline": pipeline,
    }


class MarketAgent:
    """Market attractiveness analysis agent."""

    def __init__(self, client: StructuredLLMClient | None = None) -> None:
        self.client = client or StructuredLLMClient()

    def run(
        self,
        agent_input: MarketAgentInput,
        sources: list[SourceRecord],
        evidence_items: list[EvidenceItem],
    ) -> MarketAgentOutput:
        """Run market analysis and return validated structured output.

        Raises:
            StructuredOutputError: if validation fails even after repair retry.
        """
        system_prompt = _load_prompt("system")
        user_template = _load_prompt("user")

        sci_ctx = _format_scientific_context(agent_input)
        evidence_text = _format_evidence_for_market(evidence_items)

        user_prompt = user_template.format(
            inn_preferred=agent_input.inn_preferred,
            inn_english=agent_input.inn_english or "N/A",
            inn_synonyms=", ".join(agent_input.inn_synonyms) or "N/A",
            disease_preferred=agent_input.disease_preferred or "N/A",
            disease_synonyms=", ".join(agent_input.disease_synonyms) or "N/A",
            region=agent_input.region or "global",
            molecule_type=agent_input.molecule_type,
            stage=agent_input.stage or "unknown",
            scientific_summary=sci_ctx["scientific_summary"],
            unmet_need=sci_ctx["unmet_need"],
            approved_therapies=sci_ctx["approved_therapies"],
            clinical_pipeline=sci_ctx["clinical_pipeline"],
            evidence_context=evidence_text,
        )

        result = self.client.call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_model=MarketAgentOutput,
            model=config.default_openrouter_model,
            run_id=agent_input.run_id,
        )
        output: MarketAgentOutput = result  # type: ignore[assignment]

        self._validate_output(output, {s.source_id for s in sources})
        return output

    @staticmethod
    def _validate_output(output: MarketAgentOutput, known_ids: set[str]) -> None:
        """Post-validation: check source references and flag gaps."""
        all_referenced: set[str] = set()
        for comp in output.competitors:
            all_referenced.update(comp.source_ids)
        for dyn in output.market_dynamics:
            all_referenced.update(dyn.source_ids)
        for bench in output.competitor_price_benchmarks:
            all_referenced.update(bench.source_ids)
        for risk in output.commercial_risks:
            all_referenced.update(risk.source_ids)
        for src in output.sources:
            all_referenced.add(src.source_id)

        # Collect source_ids from price sensitivity analysis
        if output.price_sensitivity_analysis:
            psa = output.price_sensitivity_analysis
            all_referenced.update(psa.source_ids)
            for scenario in psa.scenarios:
                all_referenced.update(scenario.source_ids)

        orphan_ids = all_referenced - known_ids - {""}
        if orphan_ids:
            output.missing_information.append(
                f"LLM referenced unknown source_ids: {', '.join(sorted(orphan_ids))}"
            )

        if not output.competitors:
            output.missing_information.append("No competitors identified — data may be incomplete.")

        if not output.commercial_risks:
            output.missing_information.append("No commercial risks identified — review needed.")

        # Validate price sensitivity analysis completeness
        if not output.price_sensitivity_analysis:
            output.missing_information.append(
                "Price sensitivity analysis not provided — unable to assess premium pricing viability."
            )
        elif not output.price_sensitivity_analysis.scenarios:
            output.missing_information.append(
                "Price sensitivity scenarios not provided — pricing strategy analysis incomplete."
            )
