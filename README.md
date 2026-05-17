# pharm-agent MVP 1

Deterministic AI-assisted pharma analysis skeleton.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env and add your OPENROUTER_API_KEY
```

## CLI Usage

The primary command runs the full end-to-end MVP 1 flow in a single terminal session:
input collection, PDF hashing, LLM enrichment, inline human approval, and artifact writing.

```bash
python -m app.cli run \
  --inn "ацетилсалициловая кислота" \
  --disease "инсульт" \
  --pdf1 ./pdfs/source_1.pdf \
  --pdf2 ./pdfs/source_2.pdf
```

The command will:
1. Create a run and compute an input hash.
2. Register both PDFs with SHA-256 hashes.
3. Call the intake-enrichment LLM with structured output.
4. Print the enriched summary for review directly in the terminal.
5. Ask for your approval (`a`) or rejection (`r`) inline.
6. On approval — save the decision, write Obsidian notes and a final MVP 1 summary, complete the run.
7. On rejection — persist the rejected outcome and stop.

Additional commands:

```bash
# Submit a verification decision for an existing run (alternative to inline approval)
python -m app.cli verify --run-id <RUN_ID> --decision approved

# Check status
python -m app.cli status --run-id <RUN_ID>
```

## Environment Variables

See `.env.example` for the full list. Required:

- `OPENROUTER_API_KEY` — your OpenRouter API key.
- `OPENROUTER_BASE_URL` — defaults to `https://openrouter.ai/api/v1`.
- `DEFAULT_OPENROUTER_MODEL` — e.g. `openai/gpt-4o-mini`.

Optional paths (have sensible defaults):

- `PDFS_DIR`, `VAULT_DIR`, `LOGS_DIR`, `DB_PATH`.

## MVP 1 Scope

- CLI interface (Typer), no web UI.
- Input: МНН + optional disease + exactly two PDFs.
- PDF SHA-256 hash watcher / change detection.
- Obsidian vault writer with auto-generated markers and medical disclaimer.
- SQLite storage for runs, input hash, PDF versions, enrichment, decisions, and final summary.
- JSONL + SQLite audit logs with secret redaction.
- OpenRouter client with structured output validation and repair retry.
- Mandatory human verification via terminal (inline approval).

## Tests

```bash
pytest
ruff check .
ruff format --check .
```

## Disclaimers

- This analysis is for R&D and investment research only. It is not medical advice, clinical guidance, or a substitute for qualified professional review.
- Patent and freedom-to-operate analysis, when implemented, will be preliminary and not a legal FTO opinion. Review by a qualified patent attorney is required before business decisions.
