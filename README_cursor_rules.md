# Cursor Rules for Pharma Agent MVP 1

This archive rewrites the previous Cline `.clinerules/` set into Cursor Project Rules.

## Where to put the files

Copy the `.cursor/` directory into the root of your project:

```text
your-project/
  .cursor/
    rules/
      00-project.mdc
      01-architecture.mdc
      02-pharma-agent-behavior.mdc
      03-logging.mdc
      04-obsidian-kb.mdc
      05-testing.mdc
      06-security.mdc
```

## How these rules are scoped

Always applied:

- `00-project.mdc`
- `01-architecture.mdc`
- `02-pharma-agent-behavior.mdc`
- `06-security.mdc`

Context/file-scoped:

- `03-logging.mdc`
- `04-obsidian-kb.mdc`
- `05-testing.mdc`

## First prompt to use in Cursor

```text
Read the Cursor project rules in .cursor/rules first.

We are building MVP 1 of the pharma agent deterministic skeleton.

Scope:
- CLI only, no web UI yet.
- Input: INN / МНН, optional disease, and exactly two local PDFs.
- PDF hash watcher.
- Obsidian vault writer.
- SQLite storage for runs and PDF versions.
- JSONL audit logs.
- OpenRouter client.
- Structured outputs using JSON Schema and Pydantic validation.
- Mandatory human verification through terminal.

Do not implement the full scientific, market, patent, or financial agents yet.
Create an implementation plan first and wait for approval before writing code.
```
