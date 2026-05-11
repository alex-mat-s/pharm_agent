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

```bash
# Create a run (requires exactly 2 PDFs)
python -m app.cli run \
  --inn "ацетилсалициловая кислота" \
  --disease "инсульт" \
  --pdf1 ./pdfs/source_1.pdf \
  --pdf2 ./pdfs/source_2.pdf

# After the run reaches awaiting_human_verification, verify:
python -m app.cli verify --run-id <RUN_ID> --decision approved

# Check status
python -m app.cli status --run-id <RUN_ID>
```

## MVP 1 Scope

- CLI interface (Typer)
- Input: МНН + optional disease + exactly two PDFs
- PDF hash watcher / change detection
- Obsidian vault writer
- SQLite storage for runs and PDF versions
- JSONL audit logs
- OpenRouter client with structured output validation
- Mandatory human verification via terminal

## Tests

```bash
pytest
ruff check .
ruff format --check .
```

## Notes

- This analysis is for R&D and investment research only. It is not medical advice.
- Patent outputs, when implemented, will be preliminary and not legal FTO opinions.
