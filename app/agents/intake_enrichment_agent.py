from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import config
from app.llm.structured_client import StructuredLLMClient, StructuredOutputError
from app.schemas.input import RawInput
from app.schemas.intake_output import IntakeEnrichmentOutput
from app.schemas.pdf import PDFExtractionResult


_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"intake_enrichment.{name}.md"
    return path.read_text(encoding="utf-8")


def _build_pdf_context(results: list[PDFExtractionResult]) -> str:
    """Summarize PDF chunks for the prompt."""
    lines = []
    for result in results:
        lines.append(f"PDF: {result.pdf_id} ({result.page_count} pages, sha256={result.sha256})")
        for chunk in result.chunks:
            preview = chunk.text[:300].replace("\n", " ")
            lines.append(f"  Page {chunk.page_number}: {preview}...")
    return "\n".join(lines)


def _build_schema() -> dict[str, Any]:
    """Build a JSON Schema dict from the IntakeEnrichmentOutput model."""
    return IntakeEnrichmentOutput.model_json_schema()


class IntakeEnrichmentAgent:
    """Intake enrichment agent using StructuredLLMClient for guaranteed validation."""

    def __init__(self, client: StructuredLLMClient | None = None) -> None:
        self.client = client or StructuredLLMClient()

    def run(
        self,
        raw_input: RawInput,
        pdf_results: list[PDFExtractionResult],
        run_id: str,
    ) -> IntakeEnrichmentOutput:
        """Run intake enrichment and return validated structured output.

        Raises:
            StructuredOutputError: if validation fails even after repair retry.
        """
        system_prompt = _load_prompt("system")
        user_template = _load_prompt("user")

        pdf_context = _build_pdf_context(pdf_results)
        user_prompt = user_template.format(
            inn_raw=raw_input.inn_raw,
            disease_raw=raw_input.disease_raw or "N/A",
            region=raw_input.region or "N/A",
            molecule_type=raw_input.molecule_type or "N/A",
            pdf_context=pdf_context,
        )

        result = self.client.call(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_model=IntakeEnrichmentOutput,
            model=config.default_openrouter_model,
            run_id=run_id,
        )
        # Guaranteed to be a validated IntakeEnrichmentOutput
        return result  # type: ignore[return-value]
