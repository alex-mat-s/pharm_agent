"""Patent/Finance Viability Agent (MVP 4).

Analyzes patent landscape, FTO risks, patent fence opportunities,
investment scenarios, cost structure, money timeline, and financial risks.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import config
from app.llm.structured_client import StructuredLLMClient
from app.schemas.evidence import EvidenceItem, SourceRecord
from app.schemas.patent_finance import PatentFinanceAgentInput, PatentFinanceAgentOutput

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"patent_finance_agent.{name}.md"
    return path.read_text(encoding="utf-8")


def _format_evidence_for_patent_finance(items: list[EvidenceItem]) -> str:
    """Format evidence items relevant to patent/finance analysis."""
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


def _format_scientific_context(agent_input: PatentFinanceAgentInput) -> dict[str, str]:
    """Extract scientific context fields for the prompt."""
    approved = "(not available)"

    if agent_input.approved_therapies_json:
        try:
            data = json.loads(agent_input.approved_therapies_json)
            if isinstance(data, list):
                items = []
                for t in data[:10]:
                    name = t.get("name", t.get("drug_name", "?"))
                    status = t.get("regulatory_status", t.get("status", ""))
                    items.append(f"- {name} ({status})")
                approved = "\n".join(items) if items else "(none found)"
        except json.JSONDecodeError:
            pass

    return {
        "scientific_summary": agent_input.scientific_summary or "(not available)",
        "mechanism_of_action": agent_input.mechanism_of_action or "(not available)",
        "approved_therapies": approved,
    }


def _format_market_context(agent_input: PatentFinanceAgentInput) -> dict[str, str]:
    """Extract market context fields for the prompt."""
    competitors = "(not available)"

    if agent_input.competitors_json:
        try:
            data = json.loads(agent_input.competitors_json)
            if isinstance(data, list):
                items = []
                for c in data[:10]:
                    name = c.get("drug_name", "?")
                    company = c.get("company", "")
                    status = c.get("status", "")
                    items.append(f"- {name} ({company}, {status})")
                competitors = "\n".join(items) if items else "(none found)"
        except json.JSONDecodeError:
            pass

    return {
        "market_summary": agent_input.market_summary or "(not available)",
        "competitors": competitors,
        "market_size_estimate": agent_input.market_size_estimate or "(not available)",
    }


def _format_pdf_context(agent_input: PatentFinanceAgentInput) -> str:
    """Format PDF context for patent/finance analysis.

    PDFs may contain patent documents, financial reports, due diligence materials,
    market research, or technical documentation relevant to IP and investment analysis.
    """
    if not agent_input.pdf_context:
        return "(No PDF documents provided for this analysis)"

    return agent_input.pdf_context


class PatentFinanceAgent:
    """Patent landscape and financial viability analysis agent."""

    def __init__(self, client: StructuredLLMClient | None = None) -> None:
        self.client = client or StructuredLLMClient()

    def run(
        self,
        agent_input: PatentFinanceAgentInput,
        sources: list[SourceRecord],
        evidence_items: list[EvidenceItem],
    ) -> PatentFinanceAgentOutput:
        """Run patent/finance analysis and return validated structured output.

        Raises:
            StructuredOutputError: if validation fails even after repair retry.
        """
        system_prompt = _load_prompt("system")
        user_template = _load_prompt("user")

        sci_ctx = _format_scientific_context(agent_input)
        market_ctx = _format_market_context(agent_input)
        evidence_text = _format_evidence_for_patent_finance(evidence_items)

        pdf_text = _format_pdf_context(agent_input)

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
            mechanism_of_action=sci_ctx["mechanism_of_action"],
            approved_therapies=sci_ctx["approved_therapies"],
            market_summary=market_ctx["market_summary"],
            competitors=market_ctx["competitors"],
            market_size_estimate=market_ctx["market_size_estimate"],
            evidence_context=evidence_text,
            pdf_documents=pdf_text,
        )

        result = self.client.call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_model=PatentFinanceAgentOutput,
            model=config.default_openrouter_model,
            run_id=agent_input.run_id,
        )
        output: PatentFinanceAgentOutput = result  # type: ignore[assignment]

        self._validate_output(output, {s.source_id for s in sources})
        return output

    @staticmethod
    def _validate_output(output: PatentFinanceAgentOutput, known_ids: set[str]) -> None:
        """Post-validation: check source references and flag gaps."""
        all_referenced: set[str] = set()
        for pat in output.blocking_patent_candidates:
            all_referenced.update(pat.source_ids)
        for risk in output.freedom_to_operate_risks:
            all_referenced.update(risk.source_ids)
        for opp in output.patent_fence_opportunities:
            all_referenced.update(opp.source_ids)
        for risk in output.key_financial_risks:
            all_referenced.update(risk.source_ids)
        for src in output.sources:
            all_referenced.add(src.source_id)

        orphan_ids = all_referenced - known_ids - {""}
        if orphan_ids:
            output.missing_information.append(
                f"LLM referenced unknown source_ids: {', '.join(sorted(orphan_ids))}"
            )

        if not output.freedom_to_operate_risks:
            output.missing_information.append(
                "No FTO risks identified — review needed or no patent data available."
            )

        if not output.key_financial_risks:
            output.missing_information.append("No financial risks identified — review needed.")

        if not output.major_cost_buckets:
            output.missing_information.append("No cost buckets identified — default assumptions used.")
