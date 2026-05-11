# Project rules: Pharma AI agent MVP 1

## Product goal

Build a deterministic AI-assisted pharma analysis skeleton.

The system accepts:
- INN / МНН as required input.
- Disease / indication as optional but strongly recommended input.
- Exactly two local PDF documents for MVP 1.

The system produces:
- A normalized and enriched input packet.
- A mandatory human verification packet.
- Structured intermediate outputs for later scientific, market, patent, and finance agents.
- Obsidian Markdown notes.
- SQLite run records.
- JSONL audit logs for every important operation, especially LLM calls.

## MVP 1 scope

Implement only the deterministic skeleton:
- CLI or simple local web form.
- Input collection: МНН + disease + two PDF files.
- PDF hash watcher / change detector.
- PDF ingestion interface with an initial local extractor implementation.
- Obsidian vault writer.
- SQLite storage for runs, files, decisions, and stage outputs.
- JSONL audit logs.
- OpenRouter client wrapper.
- Structured LLM outputs validated with schemas.
- Mandatory human verification via terminal or local web UI.

## Explicit non-goals for MVP 1

Do not implement full scientific, market, patent, or finance intelligence yet.
For MVP 1, create interfaces, schemas, stubs, and deterministic flow points that later agents can use.

Do not implement paid data integrations.
Do not implement autonomous background execution without explicit user action.
Do not skip human verification.
Do not create medical advice functionality.
Do not present patent analysis as legal advice.

## Preferred stack

Use Python unless the user explicitly asks otherwise.

Recommended defaults:
- Python 3.11+
- Typer for CLI
- FastAPI only if a simple web form is requested
- Pydantic for schemas
- SQLite for MVP storage
- SQLAlchemy or sqlite-utils for database access
- pathlib for filesystem paths
- python-dotenv for local environment loading
- httpx for API calls
- pytest for tests
- ruff for linting

Keep dependencies minimal and documented.

## Repository structure

Prefer this structure:

```text
pharm-agent/
  app/
    main.py
    orchestrator.py
    agents/
    tools/
    schemas/
    storage/
    ui/
  prompts/
  config/
  pdfs/
  vault/
  logs/
  tests/
  .clinerules/
  .env.example
  README.md
```

## Coding principles

- Keep the pipeline deterministic and inspectable.
- Prefer explicit state transitions over hidden agent autonomy.
- Validate every external or LLM-derived output with Pydantic schemas.
- Keep runtime agent prompts in `/prompts`, not in `.clinerules`.
- Keep configuration in `/config`, not hardcoded in business logic.
- Make every stage resumable from saved state.
- Make every stage reproducible from stored input, PDF hashes, prompts, model name, and outputs.
- Fail closed when data is missing, ambiguous, or unvalidated.

## Language rules

- Code, identifiers, and comments may be in English.
- User-facing CLI messages, Obsidian reports, and README examples may be in Russian unless the user requests English.
- Preserve pharma terms in both Russian and English when useful, for example: `МНН / INN`, `заболевание / indication`.

## Done definition for MVP 1

A run is considered complete when:
- The raw input is stored.
- Both PDFs are registered with hashes.
- PDF changes can be detected.
- The intake enrichment stage can call OpenRouter and parse a structured response.
- The user must approve or reject the normalized input.
- The human decision is stored in SQLite and Obsidian.
- All LLM calls and important system events are written to JSONL audit logs.
- A run note exists in the Obsidian vault.
- Unit tests cover the critical deterministic components.
