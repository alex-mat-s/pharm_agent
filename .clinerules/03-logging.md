# Logging and audit rules

## Logging goal

The system must be reproducible and auditable.
A reviewer should be able to reconstruct what happened in a run from stored input, PDF hashes, prompts, model metadata, LLM responses, human decisions, and stage outputs.

## Required log types

Implement three layers:

```text
logs/audit.jsonl
  Append-only structured events.

logs/debug.log
  Developer-readable technical diagnostics.

vault/03_runs/<run_id>.md
  Human-readable run summary for Obsidian.
```

## Audit log format

Every audit event must be one JSON object per line.
Include at least:

```json
{
  "event_id": "uuid",
  "run_id": "run_...",
  "stage": "intake_enrichment",
  "event_type": "llm_call|tool_call|state_change|human_decision|error",
  "timestamp": "ISO-8601",
  "status": "started|succeeded|failed",
  "input_ref": "optional reference or hash",
  "output_ref": "optional reference or hash",
  "metadata": {}
}
```

## LLM call logging

Every LLM call must log:
- provider;
- model;
- endpoint or logical operation name;
- system prompt or prompt hash;
- user prompt or prompt hash;
- structured output schema name and version;
- request parameters, excluding secrets;
- tool definitions if used;
- raw response;
- parsed response;
- validation errors;
- retry count;
- latency;
- token usage if available;
- estimated cost if available.

Do not log API keys, authorization headers, cookies, or credentials.

## Tool call logging

Every important tool call must log:
- tool name;
- input parameters, with sensitive values redacted;
- output summary;
- errors;
- duration;
- source file hashes where applicable.

Important tools include:
- PDF hash computation;
- PDF extraction;
- SQLite writes;
- Obsidian writes;
- OpenRouter calls;
- human verification submission.

## State transition logging

Every run status change must be logged as a `state_change` event:

```json
{
  "from_status": "pdfs_ingested",
  "to_status": "intake_enriched",
  "reason": "Intake enrichment output validated"
}
```

Invalid transitions must raise an error and be logged.

## Error logging

On failure:
- log exception type;
- log sanitized error message;
- log stage;
- log run_id;
- log whether the operation is retryable;
- store enough context to debug without leaking secrets.

Never swallow exceptions silently.

## Redaction rules

Redact:
- API keys;
- bearer tokens;
- cookies;
- database passwords;
- local absolute paths if configured as sensitive;
- personal or confidential user data if marked sensitive.

Use a central redaction utility.

## Append-only rule

Audit logs are append-only.
Do not edit or rewrite previous audit events.
If a correction is needed, write a new correction event.

## Human-readable run log

After each major stage, update the Obsidian run note with:
- stage status;
- timestamp;
- summary;
- links to generated notes;
- human decision if available;
- warnings;
- next expected action.
