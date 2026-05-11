# Obsidian knowledge base rules

## Purpose

The Obsidian vault is a human-readable knowledge base, not the only source of truth.
SQLite remains the structured source of truth for runs and stage outputs.
JSONL remains the audit source of truth.

## Vault location

Use `/vault` at the project root by default.
Make the path configurable.

## Required vault structure

Create and maintain:

```text
vault/
  00_inputs/
  01_entities/
    drugs/
    diseases/
    companies/
    targets/
  02_sources/
    pdfs/
    pubmed/
    clinicaltrials/
    patents/
    web/
  03_runs/
  04_reports/
  05_decisions/
  99_templates/
```

For MVP 1, only these are mandatory:
- `00_inputs/`
- `01_entities/drugs/`
- `01_entities/diseases/`
- `02_sources/pdfs/`
- `03_runs/`
- `05_decisions/`
- `99_templates/`

## Markdown style

All notes must use YAML frontmatter.
Use stable machine-readable keys.
Use ISO-8601 timestamps.
Use relative Obsidian links when useful.

Example:

```markdown
---
type: analysis_run
run_id: run_20260511_001
status: awaiting_human_verification
created_at: 2026-05-11T10:00:00+02:00
input_hash: abc123
pdf_hashes:
  source_1.pdf: sha256...
  source_2.pdf: sha256...
---

# Run run_20260511_001

## Input

## PDF status

## Intake enrichment

## Human verification

## Next action
```

## Naming conventions

Use slugified lowercase filenames.

Examples:
- `vault/01_entities/drugs/acetylsalicylic-acid.md`
- `vault/01_entities/diseases/ischemic-stroke.md`
- `vault/03_runs/run_20260511_001.md`
- `vault/05_decisions/run_20260511_001_human_verification.md`

Avoid spaces in filenames.

## PDF source notes

For every PDF, create or update a source note:

```markdown
---
type: pdf_source
pdf_id: source_1
filename: source_1.pdf
sha256: ...
size_bytes: 123456
page_count: 42
ingested_at: ...
last_seen_at: ...
status: unchanged
---

# source_1.pdf

## Extraction status

## Page/chunk summary

## Notes
```

Do not paste the full raw PDF content into Obsidian.
Store summaries, chunk references, page numbers, hashes, and links to extracted files if needed.

## Drug entity note

When the INN is normalized and human-approved, create or update a drug note:

```markdown
---
type: drug
preferred_name: ...
inn_ru: ...
inn_en: ...
synonyms: []
molecule_type: unknown
last_updated: ...
source_runs: []
---

# Preferred drug name

## Identity

## Known synonyms

## MVP 1 notes

## Linked runs
```

## Disease entity note

When disease / indication is normalized and human-approved, create or update a disease note:

```markdown
---
type: disease
preferred_name: ...
synonyms: []
subtypes: []
last_updated: ...
source_runs: []
---

# Preferred disease name

## Identity

## Possible subtypes

## MVP 1 notes

## Linked runs
```

## Run notes

Every run must have a note in `vault/03_runs`.
The note must include:
- raw input;
- normalized input;
- PDF hashes;
- enrichment summary;
- human verification status;
- warnings;
- next action.

## Decision notes

Every human verification decision must have a note in `vault/05_decisions`.
Include:
- approved / rejected / needs_revision;
- reviewer name if provided;
- timestamp;
- fields approved;
- corrections made;
- comments.

## Update behavior

When updating notes:
- preserve existing human-written sections when possible;
- update machine-generated sections between clear markers;
- do not delete manual notes unless explicitly requested;
- write atomically when possible.

Recommended markers:

```markdown
<!-- BEGIN AUTO-GENERATED -->
...
<!-- END AUTO-GENERATED -->
```
