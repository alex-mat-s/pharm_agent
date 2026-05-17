from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.config import config
from app.schemas.audit import AuditEvent
from app.schemas.human_decision import HumanDecision
from app.schemas.input import RawInput
from app.schemas.intake_output import IntakeEnrichmentOutput
from app.schemas.pdf import PDFMetadata
from app.schemas.run import RunRecord, RunStatus, StageOutput, is_valid_transition


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    """SQLite persistence layer for MVP 1.

    Tables:
    - runs               : one row per pipeline run
    - run_steps          : named stages/statuses within a run
    - pdf_files          : PDF header-level metadata (one per physical file)
    - pdf_versions       : per-hash snapshots of each PDF (one per version)
    - human_decisions    : explicit human verification decisions
    - structured_outputs : validated LLM/stage output payloads
    - audit_events       : mirror of JSONL audit log for easier querying
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or config.db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            # -----------------------------------------------------------------
            # runs
            # -----------------------------------------------------------------
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    raw_input_json TEXT NOT NULL,
                    input_hash TEXT,
                    enrichment_output_json TEXT,
                    human_decision_json TEXT,
                    final_summary_json TEXT,
                    error_message TEXT
                )
                """
            )
            # -----------------------------------------------------------------
            # run_steps
            # -----------------------------------------------------------------
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS run_steps (
                    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    step_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    details_json TEXT,
                    UNIQUE(run_id, step_name)
                )
                """
            )
            # -----------------------------------------------------------------
            # pdf_files  — one row per physical file (hash is the foreign key)
            # -----------------------------------------------------------------
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pdf_files (
                    pdf_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    page_count INTEGER,
                    modified_timestamp TEXT,
                    ingested_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY (pdf_id, sha256)
                )
                """
            )
            self._migrate_pdf_files_table(conn)
            # -----------------------------------------------------------------
            # pdf_versions — many-to-many between runs and pdf_files via sha256
            # -----------------------------------------------------------------
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pdf_versions (
                    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    pdf_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    version_label TEXT,  -- e.g. 'unchanged', 'updated', 'new'
                    registered_at TEXT NOT NULL
                )
                """
            )
            # -----------------------------------------------------------------
            # stage_outputs (legacy, still used by orchestrator for raw stage dumps)
            # -----------------------------------------------------------------
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stage_outputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stage TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata TEXT
                )
                """
            )
            # -----------------------------------------------------------------
            # human_decisions
            # -----------------------------------------------------------------
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS human_decisions (
                    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id) ON DELETE CASCADE,
                    decision TEXT NOT NULL,
                    corrections_json TEXT,
                    comments TEXT,
                    reviewer_name TEXT,
                    timestamp TEXT NOT NULL
                )
                """
            )
            # -----------------------------------------------------------------
            # structured_outputs — payload jsons produced by agents/stages
            # -----------------------------------------------------------------
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS structured_outputs (
                    output_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    output_json TEXT NOT NULL,
                    schema_name TEXT,
                    created_at TEXT NOT NULL,
                    metadata TEXT
                )
                """
            )
            # -----------------------------------------------------------------
            # audit_events — mirror of JSONL log (flat, queryable)
            # -----------------------------------------------------------------
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_ref TEXT,
                    output_ref TEXT,
                    metadata TEXT
                )
                """
            )
            conn.commit()

    def _migrate_pdf_files_table(self, conn: sqlite3.Connection) -> None:
        """Upgrade legacy pdf_files tables to the composite primary key schema.

        Older databases used `sha256` as the single primary key. That schema
        breaks when the same physical PDF is attached to both `source_1` and
        `source_2`, because a later insert overwrites the earlier slot mapping.

        This migration preserves all existing rows and recreates `pdf_files`
        with the current `(pdf_id, sha256)` primary key.
        """
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pdf_files'"
        ).fetchone()
        if row is None:
            return

        create_sql = row["sql"] or ""
        normalized_sql = " ".join(create_sql.lower().split())
        if "primary key (pdf_id, sha256)" in normalized_sql:
            return

        if "sha256 text primary key" not in normalized_sql:
            raise RuntimeError(
                "Unsupported legacy schema for pdf_files; automatic migration aborted."
            )

        conn.execute("ALTER TABLE pdf_files RENAME TO pdf_files_legacy")
        conn.execute(
            """
            CREATE TABLE pdf_files (
                pdf_id TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                filename TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                page_count INTEGER,
                modified_timestamp TEXT,
                ingested_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (pdf_id, sha256)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO pdf_files (
                pdf_id, sha256, filename, size_bytes, page_count,
                modified_timestamp, ingested_at, last_seen_at
            )
            SELECT
                pdf_id, sha256, filename, size_bytes, page_count,
                modified_timestamp, ingested_at, last_seen_at
            FROM pdf_files_legacy
            """
        )
        conn.execute("DROP TABLE pdf_files_legacy")

    # =====================================================================
    # Runs
    # =====================================================================

    def create_run(self, raw_input: RawInput, input_hash: str = "") -> RunRecord:
        run_id = f"run_{_now_iso().replace(':', '').replace('+', '')}"
        now = _now_iso()
        raw_json = json.dumps(raw_input.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, status, created_at, updated_at, raw_input_json, input_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, RunStatus.created.value, now, now, raw_json, input_hash),
            )
            conn.commit()
        return RunRecord(
            run_id=run_id,
            status=RunStatus.created,
            created_at=now,
            updated_at=now,
            raw_input_json=raw_json,
            input_hash=input_hash,
        )

    def update_run_status(self, run_id: str, new_status: RunStatus, error: str | None = None) -> RunRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Run {run_id} not found")
            current = RunStatus(row["status"])
            if not is_valid_transition(current, new_status):
                raise ValueError(
                    f"Invalid status transition: {current.value} -> {new_status.value}"
                )
            now = _now_iso()
            if error:
                conn.execute(
                    "UPDATE runs SET status = ?, updated_at = ?, error_message = ? WHERE run_id = ?",
                    (new_status.value, now, error, run_id),
                )
            else:
                conn.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    (new_status.value, now, run_id),
                )
            conn.commit()
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            return self._row_to_run(row)

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            return self._row_to_run(row)

    def _row_to_run(self, row: sqlite3.Row) -> RunRecord:
        columns = row.keys()
        return RunRecord(
            run_id=row["run_id"],
            status=RunStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            raw_input_json=row["raw_input_json"],
            input_hash=row["input_hash"] if "input_hash" in columns else None,
            enrichment_output_json=row["enrichment_output_json"],
            human_decision_json=None,
            final_summary_json=row["final_summary_json"],
            error_message=row["error_message"],
        )

    # =====================================================================
    # Run steps
    # =====================================================================

    def upsert_run_step(self, run_id: str, step_name: str, status: str, details: dict | None = None) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO run_steps (run_id, step_name, status, started_at, details_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, step_name) DO UPDATE SET
                    status=excluded.status,
                    finished_at=excluded.started_at,
                    details_json=COALESCE(excluded.details_json, run_steps.details_json)
                """,
                (run_id, step_name, status, now, json.dumps(details, ensure_ascii=False) if details else None),
            )
            conn.commit()

    def get_run_steps(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM run_steps WHERE run_id = ? ORDER BY step_id", (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    # =====================================================================
    # Enrichment output (still mirrored on runs row for fast lookup)
    # =====================================================================

    def save_enrichment_output(self, run_id: str, output: IntakeEnrichmentOutput) -> None:
        """Persists to both `runs` and `structured_outputs` tables."""
        out_json = json.dumps(output.model_dump(mode="json"), ensure_ascii=False)
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET enrichment_output_json = ? WHERE run_id = ?",
                (out_json, run_id),
            )
            conn.execute(
                """
                INSERT INTO structured_outputs (run_id, stage, output_json, schema_name, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "intake_enrichment",
                    out_json,
                    "IntakeEnrichmentOutput",
                    now,
                    json.dumps({"source": "llm"}, ensure_ascii=False),
                ),
            )
            conn.commit()

    # =====================================================================
    # Human decisions
    # =====================================================================

    def save_human_decision(self, run_id: str, decision: HumanDecision) -> None:
        """Persist to dedicated table AND keep JSON snapshot on runs row."""
        dec_json = json.dumps(decision.model_dump(mode="json"), ensure_ascii=False)
        with self._connect() as conn:
            # Runs snapshot
            conn.execute(
                "UPDATE runs SET human_decision_json = ? WHERE run_id = ?",
                (dec_json, run_id),
            )
            # Dedicated table
            conn.execute(
                """
                INSERT INTO human_decisions (run_id, decision, corrections_json, comments, reviewer_name, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    decision=excluded.decision,
                    corrections_json=excluded.corrections_json,
                    comments=excluded.comments,
                    reviewer_name=excluded.reviewer_name,
                    timestamp=excluded.timestamp
                """,
                (
                    run_id,
                    decision.decision,
                    json.dumps(decision.corrections, ensure_ascii=False) if decision.corrections else None,
                    decision.comments,
                    decision.reviewer_name,
                    decision.timestamp,
                ),
            )
            conn.commit()

    def get_human_decision(self, run_id: str) -> HumanDecision | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM human_decisions WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                return None
            return HumanDecision(
                run_id=row["run_id"],
                decision=row["decision"],
                corrections=json.loads(row["corrections_json"]) if row["corrections_json"] else {},
                comments=row["comments"],
                reviewer_name=row["reviewer_name"],
                timestamp=row["timestamp"],
            )

    # =====================================================================
    # Final summary
    # =====================================================================

    def save_final_summary(self, run_id: str, summary_json: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET final_summary_json = ? WHERE run_id = ?",
                (summary_json, run_id),
            )
            conn.commit()

    # =====================================================================
    # PDFs
    # =====================================================================

    def get_pdf_by_sha256(self, sha256: str) -> PDFMetadata | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pdf_files WHERE sha256 = ?", (sha256,)
            ).fetchone()
            if row is None:
                return None
            return PDFMetadata(
                pdf_id=row["pdf_id"],
                filename=row["filename"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                page_count=row["page_count"] if row["page_count"] else 0,
                modified_timestamp=row["modified_timestamp"] or "",
                ingested_at=row["ingested_at"],
                last_seen_at=row["last_seen_at"],
            )

    def get_pdf_by_pdf_id(self, pdf_id: str) -> PDFMetadata | None:
        """Return the latest registered metadata for a given pdf_id (e.g. source_1)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pdf_files WHERE pdf_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                (pdf_id,),
            ).fetchone()
            if row is None:
                return None
            return PDFMetadata(
                pdf_id=row["pdf_id"],
                filename=row["filename"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                page_count=row["page_count"] if row["page_count"] else 0,
                modified_timestamp=row["modified_timestamp"] or "",
                ingested_at=row["ingested_at"],
                last_seen_at=row["last_seen_at"],
            )

    def register_pdf(self, meta: PDFMetadata) -> None:
        """Upsert into pdf_files keyed by (pdf_id, sha256)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pdf_files
                (pdf_id, sha256, filename, size_bytes, page_count, modified_timestamp, ingested_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pdf_id, sha256) DO UPDATE SET
                    filename=excluded.filename,
                    size_bytes=excluded.size_bytes,
                    last_seen_at=excluded.last_seen_at,
                    page_count=excluded.page_count,
                    modified_timestamp=excluded.modified_timestamp
                """,
                (
                    meta.pdf_id,
                    meta.sha256,
                    meta.filename,
                    meta.size_bytes,
                    meta.page_count,
                    meta.modified_timestamp,
                    meta.ingested_at,
                    meta.last_seen_at,
                ),
            )
            conn.commit()

    def register_pdf_version(self, run_id: str, pdf_id: str, sha256: str, version_label: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pdf_versions (run_id, pdf_id, sha256, version_label, registered_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, pdf_id, sha256, version_label, _now_iso()),
            )
            conn.commit()

    def get_pdf_versions_for_run(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pdf_versions WHERE run_id = ? ORDER BY version_id", (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    # =====================================================================
    # Stage outputs (legacy + structured_outputs table)
    # =====================================================================

    def save_stage_output(self, stage: StageOutput) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO stage_outputs (stage, run_id, output_json, created_at, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    stage.stage,
                    stage.run_id,
                    stage.output_json,
                    stage.created_at,
                    json.dumps(stage.metadata, ensure_ascii=False),
                ),
            )
            conn.commit()

    def get_stage_outputs(self, run_id: str) -> list[StageOutput]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM stage_outputs WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
            return [
                StageOutput(
                    stage=r["stage"],
                    run_id=r["run_id"],
                    output_json=r["output_json"],
                    created_at=r["created_at"],
                    metadata=json.loads(r["metadata"]) if r["metadata"] else {},
                )
                for r in rows
            ]

    # =====================================================================
    # Audit events (SQLite mirror of JSONL)
    # =====================================================================

    def save_audit_event(self, event: AuditEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events
                (event_id, run_id, stage, event_type, timestamp, status, input_ref, output_ref, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.run_id,
                    event.stage,
                    event.event_type,
                    event.timestamp,
                    event.status,
                    event.input_ref,
                    event.output_ref,
                    json.dumps(event.metadata, ensure_ascii=False),
                ),
            )
            conn.commit()

    def get_audit_events(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events WHERE run_id = ? ORDER BY timestamp", (run_id,)
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if d.get("metadata"):
                    d["metadata"] = json.loads(d["metadata"])
                results.append(d)
            return results
