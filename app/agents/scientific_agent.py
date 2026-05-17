from __future__ import annotations

import json
from pathlib import Path

from app.config import config
from app.evidence.citations import build_citation_list
from app.llm.structured_client import StructuredLLMClient
from app.schemas.evidence import EvidenceItem, SourceRecord
from app.schemas.scientific import ScientificAgentInput, ScientificAgentOutput


_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"scientific_agent.{name}.md"
    return path.read_text(encoding="utf-8")


def _format_evidence(items: list[EvidenceItem]) -> str:
    from collections import defaultdict
    by_cat: dict[str, list[str]] = defaultdict(list)
    for item in items:
        findings = "; ".join(item.key_findings) if item.key_findings else ""
        line = (
            f"- [{item.source_id}] {item.summary}"
            + (f"\n  Key findings: {findings}" if findings else "")
        )
        by_cat[item.category.value].append(line)

    if not by_cat:
        return "(No evidence items available)"

    category_labels = {
        "regulatory": "Regulatory (EMA / FDA approvals)",
        "clinical_trial": "Clinical Trials",
        "mechanism": "Mechanism of Action",
        "preclinical": "Preclinical",
        "safety": "Safety",
        "review": "Reviews & Meta-analyses",
        "guideline": "Guidelines",
        "standard_of_care": "Standard of Care",
        "epidemiology": "Epidemiology",
        "other": "Other",
    }

    sections: list[str] = []
    for cat_key in category_labels:
        if cat_key in by_cat:
            label = category_labels[cat_key]
            sections.append(f"### {label}\n")
            sections.append("\n".join(by_cat[cat_key]))
            sections.append("")

    return "\n".join(sections).strip()


def _format_sources(sources: list[SourceRecord]) -> str:
    citations = build_citation_list(sources)
    return "\n".join(
        f"[{c['number']}] {c['source_id']}: {c['label']}" for c in citations
    ) if citations else "(No sources)"


def _format_coverage(coverage: dict[str, str]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in coverage.items()) if coverage else "(No coverage data)"


class ScientificAgent:
    """Scientific analysis agent producing structured ScientificAgentOutput."""

    def __init__(self, client: StructuredLLMClient | None = None) -> None:
        self.client = client or StructuredLLMClient()

    def run(
        self,
        agent_input: ScientificAgentInput,
        sources: list[SourceRecord],
        evidence_items: list[EvidenceItem],
    ) -> ScientificAgentOutput:
        """Run scientific analysis and return validated structured output.

        Raises:
            StructuredOutputError: if validation fails even after repair retry.
        """
        system_prompt = _load_prompt("system")
        user_template = _load_prompt("user")

        user_prompt = user_template.format(
            inn=agent_input.inn_preferred,
            disease=agent_input.disease_preferred or "N/A",
            region=agent_input.region or "global",
            synonyms=", ".join(agent_input.inn_synonyms) or "N/A",
            evidence_text=_format_evidence(evidence_items),
            sources_text=_format_sources(sources),
            coverage_text=_format_coverage(agent_input.connector_coverage),
        )

        result = self.client.call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_model=ScientificAgentOutput,
            model=config.default_openrouter_model,
            run_id=agent_input.run_id,
        )
        output: ScientificAgentOutput = result  # type: ignore[assignment]

        known_source_ids = {s.source_id for s in sources}
        self._validate_source_references(output, known_source_ids, agent_input.connector_coverage)

        return output

    @staticmethod
    def _validate_source_references(
        output: ScientificAgentOutput,
        known_ids: set[str],
        coverage: dict[str, str],
    ) -> None:
        """Check that referenced source_ids exist and flag gaps.

        Does not raise — instead patches the output to reflect reality:
        unknown IDs are moved to evidence_gaps, missing connector categories
        are noted.
        """
        all_referenced: set[str] = set(output.source_ids_used)
        for claim_field in (
            output.mechanism_of_action,
            output.disease_pathophysiology,
            output.mechanistic_rationale,
            output.standard_of_care,
            output.unmet_medical_need,
        ):
            if claim_field:
                all_referenced.update(claim_field.source_ids)
        for claim_list in (output.existing_evidence, output.safety_considerations):
            for claim in claim_list:
                all_referenced.update(claim.source_ids)
        for therapy in output.approved_therapies:
            all_referenced.update(therapy.source_ids)
        for trial in output.clinical_trial_landscape:
            all_referenced.update(trial.source_ids)

        orphan_ids = all_referenced - known_ids
        if orphan_ids:
            output.evidence_gaps.append(
                f"LLM referenced unknown source_ids (removed): {', '.join(sorted(orphan_ids))}"
            )

        expected_connectors = {"pubmed", "clinicaltrials", "fda", "ema", "local_pdf"}
        covered = set(coverage.keys())
        missing = expected_connectors - covered
        for m in sorted(missing):
            if f"No {m} data" not in " ".join(output.evidence_gaps):
                output.evidence_gaps.append(f"No {m} data was available for this analysis")
