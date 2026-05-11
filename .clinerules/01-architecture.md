# Architecture rules: deterministic skeleton

## Architectural style

Build an orchestrated pipeline, not a group of uncontrolled autonomous agents.

The orchestrator owns:
- Run creation.
- Stage ordering.
- Stage status transitions.
- Persistence.
- Logging.
- Human gates.
- Error handling.

Agents are pure-ish components that receive explicit inputs and return validated outputs.
Agents must not directly mutate global state. The orchestrator persists their outputs.

## MVP 1 pipeline

Implement this flow:

```text
1. Create run
2. Collect input: INN / МНН, optional disease, two PDF paths
3. Register PDFs and compute hashes
4. Detect whether PDFs are new, unchanged, or updated
5. Extract initial PDF text/chunks
6. Run intake enrichment through OpenRouter structured output
7. Save enrichment result
8. Present human verification packet
9. Store human decision: approved, rejected, or needs_revision
10. If approved, create placeholder downstream stage records
11. Write Obsidian notes
12. Finalize run status
```

## Stage status model

Use explicit statuses:

```text
created
input_collected
pdfs_registered
pdfs_ingested
intake_enriched
awaiting_human_verification
human_approved
human_rejected
needs_revision
completed
failed
```

Never infer status only from file presence.

## Core modules

Use these responsibilities:

```text
app/orchestrator.py
  Coordinates the run and calls all services.

app/schemas/
  Pydantic models for inputs, outputs, decisions, audit events, and database records.

app/agents/intake_enrichment_agent.py
  Calls OpenRouter and returns a structured normalized input packet.

app/tools/pdf_reader.py
  Extracts PDF text and metadata. Later can be upgraded to multimodal extraction.

app/tools/pdf_watcher.py
  Computes file hashes and detects updates.

app/tools/obsidian.py
  Writes Markdown notes with YAML frontmatter.

app/storage/db.py
  SQLite initialization, migrations, repositories.

app/storage/audit_log.py
  Append-only JSONL logging.

app/tools/openrouter.py
  OpenRouter client wrapper with structured output support.

app/ui/cli.py
  Terminal input and human verification flow.
```

## Persistence boundaries

Store structured run data in SQLite.
Store append-only audit events in JSONL.
Store human-readable knowledge in Obsidian Markdown.

Do not treat Markdown as the only source of truth.
Do not treat the vector index as the source of truth.
Do not store raw secrets in any persistence layer.

## Runtime prompts

Do not put runtime LLM prompts inside `.clinerules`.
Runtime prompts must live in `/prompts`, for example:

```text
prompts/intake_enrichment.system.md
prompts/intake_enrichment.user.md
```

The code should load these prompt files and log their exact content or prompt hash for reproducibility.

## Structured outputs

Every LLM agent call must request and validate structured output.

If structured parsing fails:
- Log the raw response.
- Log validation errors.
- Retry only according to a small explicit retry policy.
- Do not silently coerce unsafe or incomplete data.

## OpenRouter abstraction

All OpenRouter calls must go through a single wrapper.
The wrapper must support:
- model selection from config;
- timeout;
- retry policy;
- structured output schema;
- token/cost metadata when available;
- full audit logging;
- redaction of secrets.

No direct OpenRouter calls from business logic.

## PDF handling

PDFs are untrusted inputs.

For MVP 1:
- Compute SHA-256 for each PDF.
- Store filename, path, hash, size, modified timestamp, and ingestion timestamp.
- Extract page-level text when possible.
- Store chunk references with PDF hash and page numbers.
- Detect PDF updates by hash, not only by modified timestamp.

Future multimodal PDF extraction must be implemented behind the same `PdfReader` interface.

## Human verification

Human verification is mandatory after intake enrichment.

The system must present:
- normalized INN;
- normalized disease / indication;
- synonyms;
- ambiguities;
- PDF extraction status;
- LLM assumptions;
- questions for the user.

A run must not proceed past the gate until the user explicitly approves.
