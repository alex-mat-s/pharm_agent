"""Gradio Blocks UI for pharm_agent MVP.

Run with: python -m app.ui.gradio_app
Serves at: http://127.0.0.1:7860
"""

from __future__ import annotations

import json
import logging
import traceback

import gradio as gr

from app.ui import services

logger = logging.getLogger("pharm_agent.ui")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

DISCLAIMER = (
    "⚕️ This analysis is intended for R&D and investment research purposes only. "
    "It is not medical advice or clinical guidance."
)

REGION_CHOICES = ["global", "US", "EU", "RU", "custom"]
STAGE_CHOICES = ["", "idea", "preclinical", "phase1", "phase2", "phase3", "approved", "repurposing", "unknown"]
SOURCE_TYPES = ["all", "pubmed", "clinicaltrials", "fda", "dailymed", "ema", "pdf"]
EVENT_TYPES = [
    "all", "llm_call", "tool_call", "validation_error",
    "source_warning", "human_decision", "system_error", "state_change",
]


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 1: New Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def _tab_new_analysis():
    gr.Markdown("## New Analysis\n\nEnter drug information and upload two PDF documents.")

    with gr.Row():
        with gr.Column(scale=2):
            inn_input = gr.Textbox(label="INN *", placeholder="acetylsalicylic acid")
            disease_input = gr.Textbox(label="Disease / Indication", placeholder="ischemic stroke")
            with gr.Row():
                region_input = gr.Dropdown(label="Region", choices=REGION_CHOICES, value="global")
                stage_input = gr.Dropdown(label="Development Stage", choices=STAGE_CHOICES, value="")
            analyst_notes = gr.Textbox(label="Analyst Notes", lines=2, placeholder="Optional")

        with gr.Column(scale=1):
            pdf1_input = gr.File(label="PDF 1 *", file_types=[".pdf"], type="filepath")
            pdf2_input = gr.File(label="PDF 2 *", file_types=[".pdf"], type="filepath")

    submit_btn = gr.Button("🚀 Create run and enrich", variant="primary", size="lg")

    with gr.Row():
        run_id_output = gr.Textbox(label="Run ID", interactive=False)
        status_output = gr.Textbox(label="Status", interactive=False)

    warning_output = gr.Textbox(label="Warnings / Errors", interactive=False, lines=3)
    enrichment_summary = gr.Markdown(label="Enrichment Result")
    enrichment_json_output = gr.JSON(label="Enrichment JSON")

    def handle_create(inn, disease, region, stage, pdf1, pdf2, notes, state):
        logger.info("Tab1: handle_create inn=%r pdf1=%r pdf2=%r", inn, pdf1, pdf2)
        try:
            result = services.create_run_and_enrich(
                inn=inn,
                disease=disease,
                region=region,
                stage=stage,
                pdf1_path=pdf1,
                pdf2_path=pdf2,
                analyst_notes=notes,
            )
        except Exception as exc:
            logger.error("Tab1 exception:\n%s", traceback.format_exc())
            msg = services._format_user_error(exc)
            return "", "failed", f"❌ {msg}", "", None, state

        if not result.success:
            return (
                result.run_id or "",
                result.status or "failed",
                f"❌ {result.error}",
                "",
                None,
                state,
            )

        # Store run_id in state
        state = state or {}
        state["run_id"] = result.run_id

        import contextlib
        enrichment_data = None
        with contextlib.suppress(json.JSONDecodeError):
            enrichment_data = json.loads(result.enrichment_json or "{}")

        return (
            result.run_id,
            result.status,
            "✅ Run created. Go to the «Verification» tab.",
            result.enrichment_summary or "",
            enrichment_data,
            state,
        )

    return (
        submit_btn,
        [inn_input, disease_input, region_input, stage_input, pdf1_input, pdf2_input, analyst_notes],
        [run_id_output, status_output, warning_output, enrichment_summary, enrichment_json_output],
        handle_create,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 2: Human Verification
# ═══════════════════════════════════════════════════════════════════════════════

def _tab_verification():
    gr.Markdown(
        "## 🔍 Verification\n\n"
        "**Mandatory step.** Scientific Agent will not run without your decision."
    )

    with gr.Row():
        verify_run_id = gr.Textbox(label="Run ID", scale=3)
        load_btn = gr.Button("Load", scale=1)

    packet_display = gr.Markdown("*Enter Run ID and click «Load».*")

    gr.Markdown("---\n### Decision")

    with gr.Row():
        drug_fields = gr.Textbox(label="Normalized drug fields (editable)", lines=3)
        disease_fields = gr.Textbox(label="Normalized disease fields", lines=3)

    with gr.Row():
        ambiguities_box = gr.Textbox(label="Ambiguities", lines=2, interactive=False)
        corrections_box = gr.Textbox(label="Corrections (JSON)", lines=2, placeholder='{"inn_raw": "..."}')

    comments_box = gr.Textbox(label="Reviewer comments", lines=2)
    decision_radio = gr.Radio(
        choices=["approve", "approve_with_edits", "reject"],
        label="Decision",
        value="approve",
    )
    save_btn = gr.Button("💾 Save verification", variant="primary")
    decision_result = gr.Textbox(label="Result", interactive=False, lines=3)

    def handle_load(run_id):
        logger.info("Tab2: load run_id=%r", run_id)
        run_id = (run_id or "").strip()
        if not run_id:
            return "*Enter Run ID.*", "", "", "", ""

        packet = services.load_verification_packet(run_id)
        if packet is None:
            return (
                f"❌ Run `{run_id}` not found or not in `awaiting_human_verification` status.",
                "", "", "", "",
            )

        # Format packet display
        lines = [f"**Run:** `{packet.run_id}` | **Completeness:** `{packet.completeness}`\n"]
        lines.append(f"**Raw INN:** {packet.raw_inn}")
        lines.append(f"**Raw disease:** {packet.raw_disease or '—'}\n")

        inn = packet.normalized_inn
        inn_data = inn if isinstance(inn, dict) else inn.model_dump() if hasattr(inn, "model_dump") else {}
        dis = packet.normalized_disease
        dis_data = (
            dis if isinstance(dis, dict)
            else (dis.model_dump() if hasattr(dis, "model_dump") else {})
            if dis else {}
        )

        if inn_data:
            lines.append("**Normalized INN:**")
            lines.append(f"- Preferred: {inn_data.get('preferred_name', '?')}")
            if inn_data.get("english_inn"):
                lines.append(f"- English: {inn_data['english_inn']}")
            if inn_data.get("russian_name"):
                lines.append(f"- Russian: {inn_data['russian_name']}")
            if inn_data.get("synonyms"):
                lines.append(f"- Synonyms: {', '.join(inn_data['synonyms'])}")
            lines.append(f"- Confidence: {inn_data.get('confidence', '?')}")

        if dis_data:
            lines.append(f"\n**Normalized Disease:** {dis_data.get('preferred_name', '?')}")
            if dis_data.get("synonyms"):
                lines.append(f"- Synonyms: {', '.join(dis_data['synonyms'])}")

        if packet.questions:
            lines.append("\n**Questions:**")
            for q in packet.questions:
                lines.append(f"- ❓ {q}")

        if packet.completeness == "low":
            lines.append("\n> ⚠️ **LOW completeness** — recommend reject or request revision.")

        display_md = "\n".join(lines)
        drug_str = json.dumps(inn_data, indent=2, ensure_ascii=False) if inn_data else ""
        disease_str = json.dumps(dis_data, indent=2, ensure_ascii=False) if dis_data else ""
        ambiguities_str = "\n".join(packet.ambiguities) if packet.ambiguities else ""

        return display_md, drug_str, disease_str, ambiguities_str, ""

    def handle_save(run_id, decision, comments, corrections_raw):
        logger.info("Tab2: save run_id=%r decision=%r", run_id, decision)
        run_id = (run_id or "").strip()
        if not run_id:
            return "❌ Please enter Run ID."

        # Map radio to backend decision
        decision_map = {"approve": "approved", "approve_with_edits": "approved", "reject": "rejected"}
        backend_decision = decision_map.get(decision, "approved")

        corrections: dict = {}
        if (corrections_raw or "").strip():
            try:
                corrections = json.loads(corrections_raw)
            except json.JSONDecodeError:
                corrections = {"user_feedback": corrections_raw.strip()}

        try:
            result = services.submit_decision(
                run_id=run_id,
                decision=backend_decision,
                comments=(comments or "").strip() or None,
                corrections=corrections,
            )
        except Exception:
            tb = traceback.format_exc()
            logger.error("Tab2 exception:\n%s", tb)
            return f"❌ Exception:\n{tb}"

        if not result.success:
            return f"❌ {result.error}"
        return f"✅ Decision «{decision}» saved.\nStatus: {result.status}\n➡️ {result.next_action}"

    load_btn.click(
        fn=handle_load,
        inputs=[verify_run_id],
        outputs=[packet_display, drug_fields, disease_fields, ambiguities_box, decision_result],
    )
    save_btn.click(
        fn=handle_save,
        inputs=[verify_run_id, decision_radio, comments_box, corrections_box],
        outputs=[decision_result],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 3: Run Progress
# ═══════════════════════════════════════════════════════════════════════════════

def _tab_run_progress():
    gr.Markdown("## 📊 Run Progress")

    with gr.Row():
        progress_run_id = gr.Textbox(label="Run ID", scale=3)
        refresh_btn = gr.Button("🔄 Refresh Status", scale=1)
        run_scientific_btn = gr.Button("🧬 Run Scientific Agent", variant="primary", scale=1)

    status_md = gr.Markdown("")
    steps_table = gr.Dataframe(
        headers=["step", "status", "details"],
        label="Pipeline Steps",
        interactive=False,
    )
    warning_output = gr.Textbox(label="Warnings", interactive=False, lines=3)

    def handle_refresh(run_id):
        logger.info("Tab3: refresh run_id=%r", run_id)
        run_id = (run_id or "").strip()
        if not run_id:
            return "Enter Run ID.", [], ""

        data = services.get_run_status(run_id)
        if "error" in data and not data.get("run_id"):
            return data["error"], [], ""

        md = (
            f"**Run:** `{data['run_id']}`\n"
            f"**Status:** `{data['status']}`\n"
            f"**Created:** {data['created_at']}\n"
            f"**Updated:** {data['updated_at']}"
        )

        steps = data.get("steps", [])
        warnings = []
        for s in steps:
            if "⚠️" in s.get("details", ""):
                warnings.append(f"{s['step']}: {s['details']}")

        table_data = [[s["step"], s["status"], s["details"]] for s in steps]
        warn_text = "\n".join(warnings) if warnings else "No warnings."

        if data.get("error"):
            warn_text = f"❌ Run error: {data['error']}\n\n{warn_text}"

        return md, table_data, warn_text

    def handle_run_scientific(run_id):
        logger.info("Tab3: run_scientific run_id=%r", run_id)
        run_id = (run_id or "").strip()
        if not run_id:
            return "Enter Run ID.", [], "❌ Enter Run ID."

        msg = services.run_scientific_agent(run_id)

        # Refresh status after
        data = services.get_run_status(run_id)
        if "error" in data and not data.get("run_id"):
            return msg, [], msg

        md = (
            f"**Run:** `{data['run_id']}`\n"
            f"**Status:** `{data['status']}`\n"
            f"**Updated:** {data['updated_at']}"
        )
        steps = data.get("steps", [])
        table_data = [[s["step"], s["status"], s["details"]] for s in steps]
        return md, table_data, msg

    refresh_btn.click(
        fn=handle_refresh, inputs=[progress_run_id], outputs=[status_md, steps_table, warning_output],
    )
    run_scientific_btn.click(
        fn=handle_run_scientific, inputs=[progress_run_id], outputs=[status_md, steps_table, warning_output],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 4: Evidence Explorer
# ═══════════════════════════════════════════════════════════════════════════════

def _tab_evidence():
    gr.Markdown("## 🔬 Evidence Explorer\n\nView normalized sources for the current run.")

    with gr.Row():
        evi_run_id = gr.Textbox(label="Run ID", scale=3)
        evi_filter = gr.Dropdown(label="Filter by type", choices=SOURCE_TYPES, value="all", scale=1)
        evi_load_btn = gr.Button("Load", scale=1)

    evi_table = gr.Dataframe(
        headers=[
            "source_id", "source_type", "title", "external_id",
            "publication_date", "query_used", "relevance", "warning",
        ],
        label="Sources",
        interactive=False,
    )
    evi_detail = gr.Markdown("")

    def handle_load_evidence(run_id, source_filter):
        logger.info("Tab4: load evidence run_id=%r filter=%r", run_id, source_filter)
        run_id = (run_id or "").strip()
        if not run_id:
            return [], "Enter Run ID."

        table = services.get_evidence_table(run_id, source_filter)
        if not table:
            return [], "No sources found. Scientific Agent may not have run yet."

        col_keys = [
            "source_id", "source_type", "title", "external_id",
            "publication_date", "query_used", "relevance", "warning",
        ]
        rows = [[r.get(h, "") for h in col_keys] for r in table]
        summary = f"**Sources found:** {len(table)}"
        type_counts = {}
        for r in table:
            t = r.get("source_type", "?")
            type_counts[t] = type_counts.get(t, 0) + 1
        summary += "\n\n" + " | ".join(f"`{t}`: {c}" for t, c in sorted(type_counts.items()))

        return rows, summary

    evi_load_btn.click(fn=handle_load_evidence, inputs=[evi_run_id, evi_filter], outputs=[evi_table, evi_detail])


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 5: Scientific Memo
# ═══════════════════════════════════════════════════════════════════════════════

def _tab_memo():
    gr.Markdown("## 📝 Scientific Memo")

    with gr.Row():
        memo_run_id = gr.Textbox(label="Run ID", scale=3)
        memo_load_btn = gr.Button("Load memo", scale=1)

    memo_display = gr.Markdown("*Enter Run ID and click «Load».*")
    memo_json = gr.JSON(label="ScientificAgentOutput (JSON)", visible=False)

    with gr.Row():
        show_json_btn = gr.Button("Show JSON")
        download_btn = gr.Button("📥 Download Markdown")

    download_file = gr.File(label="Download file", visible=False)

    def handle_load_memo(run_id):
        logger.info("Tab5: load memo run_id=%r", run_id)
        run_id = (run_id or "").strip()
        if not run_id:
            return "*Enter Run ID.*", None

        memo_text, output_json_str = services.load_scientific_memo(run_id)
        try:
            output_data = json.loads(output_json_str)
        except json.JSONDecodeError:
            output_data = None

        return memo_text, output_data

    def handle_show_json():
        return gr.update(visible=True)

    def handle_download(run_id):
        run_id = (run_id or "").strip()
        if not run_id:
            return gr.update(visible=False)

        from app.config import config as cfg
        memo_dir = cfg.vault_dir / "04_reports" / "scientific"
        if memo_dir.exists():
            for f in sorted(memo_dir.glob(f"*{run_id}*"), reverse=True):
                if f.suffix == ".md":
                    return gr.update(value=str(f), visible=True)
        return gr.update(visible=False)

    memo_load_btn.click(fn=handle_load_memo, inputs=[memo_run_id], outputs=[memo_display, memo_json])
    show_json_btn.click(fn=handle_show_json, outputs=[memo_json])
    download_btn.click(fn=handle_download, inputs=[memo_run_id], outputs=[download_file])


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 6: Market Memo
# ═══════════════════════════════════════════════════════════════════════════════

def _tab_market_memo():
    gr.Markdown("## 📊 Market Memo")

    with gr.Row():
        mm_run_id = gr.Textbox(label="Run ID", scale=3)
        mm_load_btn = gr.Button("Load market memo", scale=1)

    mm_display = gr.Markdown("*Enter Run ID and click «Load».*")
    mm_json = gr.JSON(label="MarketAgentOutput (JSON)", visible=False)

    with gr.Row():
        mm_show_json_btn = gr.Button("Show JSON")
        mm_download_btn = gr.Button("📥 Download Markdown")

    mm_download_file = gr.File(label="Download file", visible=False)

    def handle_load_market(run_id):
        logger.info("Tab6: load market memo run_id=%r", run_id)
        run_id = (run_id or "").strip()
        if not run_id:
            return "*Enter Run ID.*", None

        memo_text, output_json_str = services.load_market_memo(run_id)
        output_data = None
        if output_json_str:
            import contextlib
            with contextlib.suppress(json.JSONDecodeError):
                output_data = json.loads(output_json_str)

        return memo_text, output_data

    def handle_show_market_json():
        return gr.update(visible=True)

    def handle_download_market(run_id):
        run_id = (run_id or "").strip()
        if not run_id:
            return gr.update(visible=False)

        from app.config import config as cfg
        memo_dir = cfg.vault_dir / "04_reports" / "market"
        if memo_dir.exists():
            for f in sorted(memo_dir.glob(f"*{run_id}*"), reverse=True):
                if f.suffix == ".md":
                    return gr.update(value=str(f), visible=True)
        return gr.update(visible=False)

    mm_load_btn.click(fn=handle_load_market, inputs=[mm_run_id], outputs=[mm_display, mm_json])
    mm_show_json_btn.click(fn=handle_show_market_json, outputs=[mm_json])
    mm_download_btn.click(fn=handle_download_market, inputs=[mm_run_id], outputs=[mm_download_file])


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 7: Final Synthesis
# ═══════════════════════════════════════════════════════════════════════════════

def _tab_synthesis():
    gr.Markdown(
        "## 🎯 Final Synthesis\n\n"
        "Three-pillar report: commercial attractiveness, scientific rationale (demand), financial viability."
    )

    with gr.Row():
        synth_run_id = gr.Textbox(label="Run ID", scale=3)
        synth_run_btn = gr.Button("🚀 Run Synthesis", variant="primary", scale=1)
        synth_load_btn = gr.Button("📄 Load Report", scale=1)

    synth_status = gr.Textbox(label="Status", interactive=False)

    with gr.Row():
        with gr.Column(scale=1):
            synth_conclusion = gr.Markdown("")

    # Three-pillar tabs
    with gr.Tabs():
        with gr.TabItem("📋 Full Report"):
            synth_display = gr.Markdown("*Enter a Run ID and run synthesis or load an existing report.*")

        with gr.TabItem("💼 Commercial Attractiveness"):
            pillar1_display = gr.Markdown("*Run synthesis to view.*")

        with gr.TabItem("🔬 Scientific Rationale"):
            pillar2_display = gr.Markdown("*Run synthesis to view.*")

        with gr.TabItem("🛡️ Financial Viability"):
            pillar3_display = gr.Markdown("*Run synthesis to view.*")

    synth_json = gr.JSON(label="FinalSynthesisOutput (JSON)", visible=False)

    with gr.Row():
        synth_show_json_btn = gr.Button("Show JSON")
        synth_download_btn = gr.Button("📥 Download Report")

    synth_download_file = gr.File(label="Download file", visible=False)

    # Warnings and contradictions
    with gr.Accordion("⚠️ Contradictions & Warnings", open=False):
        contradictions_display = gr.Markdown("")
        manual_review_display = gr.Markdown("")

    def _format_pillar_commercial(data: dict | None) -> str:
        """Format Pillar 1: Commercial Attractiveness."""
        if not data:
            return "*No commercial attractiveness data available.*"
        lines = [f"### Commercial Attractiveness\n\n{data.get('summary', 'N/A')}\n"]

        # Existing drugs
        existing = data.get("existing_drugs", [])
        if existing:
            lines.append("#### Existing Drugs on Market\n")
            for d in existing:
                name = d.get("drug_name", "?")
                mech = d.get("mechanism") or "N/A"
                pos = d.get("market_position") or ""
                lines.append(f"**{name}** — {mech} {f'({pos})' if pos else ''}")
                for s in d.get("strengths", []):
                    lines.append(f"  - ✅ {s}")
                for w in d.get("weaknesses", []):
                    lines.append(f"  - ⚠️ {w}")
                lines.append("")
        if data.get("existing_drugs_summary"):
            lines.append(f"**Summary:** {data['existing_drugs_summary']}\n")

        # Pipeline competitors
        pipeline = data.get("pipeline_competitors", [])
        if pipeline:
            lines.append("#### Pipeline Competitors\n")
            lines.append("| Drug | Company | Phase | Threat | Timeline |")
            lines.append("|---|---|---|---|---|")
            for p in pipeline:
                threat_icon = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "❗"}.get(p.get("competitive_threat", ""), "⚪")
                lines.append(f"| {p.get('drug_name', '?')} | {p.get('company') or 'N/A'} | {p.get('phase') or '?'} | {threat_icon} {p.get('competitive_threat', '?')} | {p.get('expected_timeline') or 'N/A'} |")
            lines.append("")
        if data.get("pipeline_threat_assessment"):
            lines.append(f"**Pipeline Threat Assessment:** {data['pipeline_threat_assessment']}\n")

        # Treatment standard
        ts = data.get("treatment_standard")
        if ts:
            lines.append(f"#### Gold Standard: {ts.get('standard_name', '?')}\n")
            lines.append(f"{ts.get('description', '')}")
            if ts.get("key_limitations"):
                lines.append("\n**Limitations:**")
                for lim in ts["key_limitations"]:
                    lines.append(f"- {lim}")
            if ts.get("what_our_drug_must_beat"):
                lines.append(f"\n**Must beat:** {ts['what_our_drug_must_beat']}")
            lines.append("")

        if data.get("our_drug_vs_standard"):
            lines.append(f"**Our drug vs standard:** {data['our_drug_vs_standard']}\n")

        # Commercial risks
        risks = data.get("commercial_risks", [])
        if risks:
            lines.append("#### Commercial Risks\n")
            for r in risks:
                lines.append(f"- ⚠️ {r}")
            lines.append("")

        return "\n".join(lines)

    def _format_pillar_scientific(data: dict | None) -> str:
        """Format Pillar 2: Scientific Rationale / Demand."""
        if not data:
            return "*No scientific rationale data available.*"
        lines = [f"### Scientific Rationale & Demand\n\n{data.get('summary', 'N/A')}\n"]

        # Disease prevalence
        prev = data.get("disease_prevalence")
        if prev:
            lines.append("#### Disease Prevalence\n")
            if prev.get("global_prevalence"):
                lines.append(f"- **Global:** {prev['global_prevalence']}")
            if prev.get("regional_prevalence"):
                lines.append(f"- **Regional:** {prev['regional_prevalence']}")
            if prev.get("incidence_rate"):
                lines.append(f"- **Incidence:** {prev['incidence_rate']}")
            trend_icon = {"growing": "📈", "stable": "➡️", "declining": "📉"}.get(prev.get("trend", ""), "❓")
            lines.append(f"- **Trend:** {trend_icon} {prev.get('trend', 'unknown')}")
            for d in prev.get("trend_drivers", []):
                lines.append(f"  - {d}")
            lines.append("")

        # Target patient segment
        seg = data.get("target_patient_segment")
        if seg:
            lines.append(f"#### Target Patient Segment\n\n{seg.get('segment_description', 'N/A')}")
            if seg.get("segment_size_vs_total"):
                lines.append(f"\n**Segment size:** {seg['segment_size_vs_total']}")
            if seg.get("selection_criteria"):
                lines.append("\n**Selection criteria:**")
                for c in seg["selection_criteria"]:
                    lines.append(f"- {c}")
            lines.append("")

        if data.get("realistic_patient_pool"):
            lines.append(f"**Realistic patient pool:** {data['realistic_patient_pool']}\n")

        # Market dynamics
        md = data.get("market_dynamics")
        if md:
            dir_icon = {"growing": "📈", "stable": "➡️", "declining": "📉"}.get(md.get("market_direction", ""), "❓")
            lines.append(f"#### Market Dynamics: {dir_icon} {md.get('market_direction', 'unknown')}\n")
            if md.get("key_drivers"):
                lines.append("**Drivers:**")
                for d in md["key_drivers"]:
                    lines.append(f"- 📈 {d}")
            if md.get("key_barriers"):
                lines.append("\n**Barriers:**")
                for b in md["key_barriers"]:
                    lines.append(f"- 🚧 {b}")
            lines.append("")

        # Payer value
        pv = data.get("payer_value")
        if pv:
            lines.append("#### Payer Value Proposition\n")
            if pv.get("value_for_physician"):
                lines.append(f"- **Physician:** {pv['value_for_physician']}")
            if pv.get("value_for_patient"):
                lines.append(f"- **Patient:** {pv['value_for_patient']}")
            if pv.get("value_for_payer"):
                lines.append(f"- **Payer:** {pv['value_for_payer']}")
            if pv.get("health_economics_argument"):
                lines.append(f"- **Health economics:** {pv['health_economics_argument']}")
            lines.append("")

        # Pricing
        pf = data.get("pricing_forecast")
        if pf:
            lines.append("#### Pricing & Forecast\n")
            if pf.get("competitor_price_range"):
                lines.append(f"- **Competitor prices:** {pf['competitor_price_range']}")
            if pf.get("our_price_rationale"):
                lines.append(f"- **Our pricing rationale:** {pf['our_price_rationale']}")
            if pf.get("market_size_estimate"):
                lines.append(f"- **Market size:** {pf['market_size_estimate']}")
            if pf.get("price_sensitivity_conclusion"):
                lines.append(f"- **Price sensitivity:** {pf['price_sensitivity_conclusion']}")
            lines.append("")

        # Mechanism & unmet need
        if data.get("mechanism_summary"):
            lines.append(f"#### Mechanism of Action\n\n{data['mechanism_summary']}\n")
        if data.get("unmet_need_summary"):
            lines.append(f"#### Unmet Medical Need\n\n{data['unmet_need_summary']}\n")

        # Evidence gaps
        gaps = data.get("evidence_gaps", [])
        if gaps:
            lines.append("#### Evidence Gaps\n")
            for g in gaps:
                lines.append(f"- ❓ {g}")
            lines.append("")

        return "\n".join(lines)

    def _format_pillar_patent(data: dict | None) -> str:
        """Format Pillar 3: Patent & Financial Viability."""
        if not data:
            return "*No patent/financial viability data available.*"
        lines = [f"### Patent & Financial Viability\n\n{data.get('summary', 'N/A')}\n"]

        # FTO check
        fto = data.get("fto_check")
        if fto:
            risk_icon = {"low": "🟢", "medium": "🟡", "high": "🔴", "critical": "❗", "unknown": "❓"}.get(fto.get("fto_risk_level", ""), "❓")
            lines.append(f"#### Freedom-to-Operate: {risk_icon} {fto.get('fto_risk_level', 'unknown').upper()}\n")
            if fto.get("active_blocking_patents_found") is not None:
                lines.append(f"- **Blocking patents found:** {fto['active_blocking_patents_found']}")
            if fto.get("total_relevant_patents") is not None:
                lines.append(f"- **Total relevant patents:** {fto['total_relevant_patents']}")
            if fto.get("composition_patents"):
                lines.append(f"- **Composition:** {fto['composition_patents']}")
            if fto.get("process_patents"):
                lines.append(f"- **Process:** {fto['process_patents']}")
            if fto.get("indication_patents"):
                lines.append(f"- **Indication:** {fto['indication_patents']}")
            if fto.get("formulation_patents"):
                lines.append(f"- **Formulation:** {fto['formulation_patents']}")
            if fto.get("fto_conclusion"):
                lines.append(f"\n**Conclusion:** {fto['fto_conclusion']}")
            if fto.get("more_patents_means_less_attractive"):
                lines.append(f"\n**Patent density impact:** {fto['more_patents_means_less_attractive']}")
            lines.append("")

        # Patent expiry impacts
        expiries = data.get("patent_expiry_impacts", [])
        if expiries:
            lines.append("#### Competitor Patent Expiries\n")
            lines.append("| Drug | Expiry | Generic Entry | Impact | Opp/Threat |")
            lines.append("|---|---|---|---|---|")
            for e in expiries:
                ot_icon = {"opportunity": "✅", "threat": "⚠️", "neutral": "➡️"}.get(e.get("opportunity_or_threat", ""), "❓")
                lines.append(
                    f"| {e.get('drug_name', '?')} "
                    f"| {e.get('patent_expiry_date') or 'N/A'} "
                    f"| {e.get('generic_entry_expected') or 'N/A'} "
                    f"| {(e.get('market_impact') or 'N/A')[:60]} "
                    f"| {ot_icon} {e.get('opportunity_or_threat', '?')} |"
                )
            lines.append("")

        # Patent fence
        fence = data.get("patent_fence")
        if fence:
            feas_icon = {"low": "🔴", "medium": "🟡", "high": "🟢"}.get(fence.get("patent_fence_feasibility", ""), "❓")
            lines.append(f"#### Patent Fence Strategy: {feas_icon} {fence.get('patent_fence_feasibility', 'unknown')}\n")
            if fence.get("primary_patent"):
                lines.append(f"- **Primary patent:** {fence['primary_patent']}")
            if fence.get("secondary_patents"):
                lines.append("- **Secondary patents:**")
                for sp in fence["secondary_patents"]:
                    lines.append(f"  - {sp}")
            if fence.get("total_protection_window"):
                lines.append(f"- **Total protection:** {fence['total_protection_window']}")
            if fence.get("strategy_summary"):
                lines.append(f"\n**Strategy:** {fence['strategy_summary']}")
            lines.append("")

        # Investment / monetization (legacy)
        inv = data.get("investment_range")
        if inv:
            lines.append("#### Investment Range\n")
            lines.append(f"| Scenario | Amount |")
            lines.append(f"|---|---|")
            lines.append(f"| Low | {inv.get('low_case', 'N/A')} |")
            lines.append(f"| Base | {inv.get('base_case', 'N/A')} |")
            lines.append(f"| High | {inv.get('high_case', 'N/A')} |")
            lines.append("")

        mt = data.get("monetization_timeline")
        if mt:
            lines.append("#### Monetization Timeline\n")
            if mt.get("earliest_value_inflection"):
                lines.append(f"- **Earliest value inflection:** {mt['earliest_value_inflection']}")
            if mt.get("licensing_window"):
                lines.append(f"- **Licensing window:** {mt['licensing_window']}")
            if mt.get("revenue_window"):
                lines.append(f"- **Revenue window:** {mt['revenue_window']}")
            lines.append("")

        # FTO risks & fence opportunities (legacy)
        fto_risks = data.get("fto_risks", [])
        if fto_risks:
            lines.append("#### FTO Risks\n")
            for r in fto_risks:
                lines.append(f"- ⚠️ {r}")
            lines.append("")

        return "\n".join(lines)

    def _extract_pillars(result: dict) -> tuple[str, str, str]:
        """Extract pillar markdown from result's output_json."""
        output = result.get("output_json")
        if not output or not isinstance(output, dict):
            return ("*No data.*", "*No data.*", "*No data.*")
        p1 = _format_pillar_commercial(output.get("commercial_attractiveness"))
        p2 = _format_pillar_scientific(output.get("scientific_rationale"))
        p3 = _format_pillar_patent(output.get("patent_and_financial_viability"))
        return p1, p2, p3

    _empty_result = ("", "*No data.*", "*No data.*", "*No data.*", "", None, "", "")

    def handle_run_synthesis(run_id):
        logger.info("Tab7-Synth: run synthesis run_id=%r", run_id)
        run_id = (run_id or "").strip()
        if not run_id:
            return (
                "❌ Enter Run ID.",
                "*Enter Run ID.*",
                "*Run synthesis to view.*",
                "*Run synthesis to view.*",
                "*Run synthesis to view.*",
                "",
                None,
                "",
                "",
            )

        try:
            result = services.run_synthesis_agent(run_id)
        except Exception:
            tb = traceback.format_exc()
            logger.error("Tab7-Synth exception:\n%s", tb)
            return (
                f"❌ Error: {tb[:200]}",
                f"Exception:\n```\n{tb}\n```",
                "*Error.*",
                "*Error.*",
                "*Error.*",
                "",
                None,
                "",
                "",
            )

        if not result.get("success"):
            return (
                f"❌ {result.get('error', 'Unknown error')}",
                f"**Error:** {result.get('error', 'Unknown')}",
                "*Error.*",
                "*Error.*",
                "*Error.*",
                "",
                None,
                "",
                "",
            )

        # Format conclusion box
        go_no_go = result.get("go_no_go", "?")
        icon = {"go": "✅", "conditional_go": "⚠️", "no_go": "❌", "insufficient_evidence": "❓"}.get(go_no_go, "❓")
        conclusion_md = f"""
### Final Assessment

{icon} **{go_no_go.upper().replace('_', ' ')}**

**Rationale:** {result.get('main_reason', 'N/A')[:100]}...

**Contradictions:** {result.get('contradictions_count', 0)}
**Requires Review:** {result.get('manual_review_count', 0)}
"""

        # Format contradictions
        contradictions = result.get("contradictions", [])
        if contradictions:
            contr_lines = ["### Detected Contradictions\n"]
            for c in contradictions[:5]:
                sev_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(c.get("severity", ""), "⚪")
                contr_lines.append(f"- {sev_icon} **{c.get('area', '?')}**: {c.get('description', '')[:80]}...")
            contr_md = "\n".join(contr_lines)
        else:
            contr_md = "*No contradictions detected.*"

        # Format manual review items
        manual_items = result.get("manual_review_items", [])
        if manual_items:
            review_lines = ["### Expert Review Required\n"]
            for item in manual_items[:5]:
                pri_icon = {"high": "❗", "medium": "⚠️", "low": "ℹ️"}.get(item.get("priority", ""), "•")
                review_lines.append(f"- {pri_icon} **{item.get('area', '?')}** ({item.get('recommended_expert_type', '?')}): {item.get('reason', '')[:60]}...")
            review_md = "\n".join(review_lines)
        else:
            review_md = "*No items require expert review.*"

        # Extract pillar content
        p1, p2, p3 = _extract_pillars(result)

        return (
            f"✅ Synthesis complete. Go/No-Go: {go_no_go}",
            result.get("report_preview", "*Report created.*"),
            p1,
            p2,
            p3,
            conclusion_md,
            result.get("output_json"),
            contr_md,
            review_md,
        )

    def handle_load_synthesis(run_id):
        logger.info("Tab7-Synth: load synthesis run_id=%r", run_id)
        run_id = (run_id or "").strip()
        if not run_id:
            return (
                "❌ Enter Run ID.",
                "*Enter Run ID.*",
                "*Run synthesis to view.*",
                "*Run synthesis to view.*",
                "*Run synthesis to view.*",
                "",
                None,
                "",
                "",
            )

        result = services.load_synthesis_report(run_id)

        if not result.get("success"):
            return (
                f"❌ {result.get('error', 'Report not found')}",
                "*Report not found.*",
                "*No data.*",
                "*No data.*",
                "*No data.*",
                "",
                None,
                "",
                "",
            )

        # Format conclusion box
        go_no_go = result.get("go_no_go", "?")
        icon = {"go": "✅", "conditional_go": "⚠️", "no_go": "❌", "insufficient_evidence": "❓"}.get(go_no_go, "❓")
        conclusion_md = f"""
### Final Assessment

{icon} **{go_no_go.upper().replace('_', ' ')}**

**Rationale:** {result.get('main_reason', 'N/A')[:100]}...
"""

        # Extract pillar content
        p1, p2, p3 = _extract_pillars(result)

        return (
            "✅ Report loaded.",
            result.get("report_content", "*Report not found.*"),
            p1,
            p2,
            p3,
            conclusion_md,
            result.get("output_json"),
            "",
            "",
        )

    def handle_show_synth_json():
        return gr.update(visible=True)

    def handle_download_synthesis(run_id):
        run_id = (run_id or "").strip()
        if not run_id:
            return gr.update(visible=False)

        from app.config import config as cfg
        report_dir = cfg.vault_dir / "04_reports" / "final"
        if report_dir.exists():
            for f in sorted(report_dir.glob(f"*{run_id}*"), reverse=True):
                if f.suffix == ".md":
                    return gr.update(value=str(f), visible=True)
        return gr.update(visible=False)

    _synth_outputs = [
        synth_status, synth_display,
        pillar1_display, pillar2_display, pillar3_display,
        synth_conclusion, synth_json,
        contradictions_display, manual_review_display,
    ]
    synth_run_btn.click(
        fn=handle_run_synthesis,
        inputs=[synth_run_id],
        outputs=_synth_outputs,
    )
    synth_load_btn.click(
        fn=handle_load_synthesis,
        inputs=[synth_run_id],
        outputs=_synth_outputs,
    )
    synth_show_json_btn.click(fn=handle_show_synth_json, outputs=[synth_json])
    synth_download_btn.click(fn=handle_download_synthesis, inputs=[synth_run_id], outputs=[synth_download_file])


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 8: Logs
# ═══════════════════════════════════════════════════════════════════════════════

def _tab_logs():
    gr.Markdown("## 📋 Audit Logs\n\nView events for the current run. Secrets are masked.")

    with gr.Row():
        logs_run_id = gr.Textbox(label="Run ID", scale=3)
        logs_count = gr.Number(label="Last N events", value=50, minimum=1, maximum=500, scale=1)
        logs_filter = gr.Dropdown(label="Filter", choices=EVENT_TYPES, value="all", scale=1)
        logs_load_btn = gr.Button("Load", scale=1)

    logs_output = gr.Textbox(label="Audit Events (JSON)", lines=20, interactive=False)

    def handle_load_logs(run_id, count, event_filter):
        logger.info("Tab8: load logs run_id=%r count=%r filter=%r", run_id, count, event_filter)
        run_id = (run_id or "").strip()
        if not run_id:
            return "Enter Run ID."

        return services.get_audit_events(run_id, int(count or 50), event_filter or "all")

    logs_load_btn.click(fn=handle_load_logs, inputs=[logs_run_id, logs_count, logs_filter], outputs=[logs_output])


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 9: Settings / Healthcheck
# ═══════════════════════════════════════════════════════════════════════════════

def _tab_settings():
    gr.Markdown("## ⚙️ Settings / Healthcheck\n\nCheck availability of all system components.")

    check_btn = gr.Button("🩺 Run healthcheck", variant="primary")
    
    # Progress indicator
    progress_text = gr.Markdown("*Click the button to start healthcheck*")
    health_output = gr.Markdown("")

    def handle_healthcheck_streaming():
        """Generator that yields progress updates during healthcheck."""
        logger.info("Tab9: running healthcheck with progress")
        
        try:
            from app.services.healthcheck import run_all_checks_streaming, HealthStatus
            
            for progress_pct, current_item, partial_results in run_all_checks_streaming():
                # Build progress bar
                filled = int(progress_pct / 5)  # 20 chars total
                bar = "█" * filled + "░" * (20 - filled)
                
                # Build partial results table
                if partial_results:
                    lines = [f"**Progress: [{bar}] {progress_pct}%** — {current_item}\n"]
                    lines.append("| Component | Status | Details |")
                    lines.append("|---|---|---|")
                    for r in partial_results:
                        icon = "✅" if r.ok else ("🔴" if r.fatal else "⚠️")
                        lines.append(f"| {r.name} | {icon} | {r.detail} |")
                    partial_md = "\n".join(lines)
                else:
                    partial_md = f"**Progress: [{bar}] {progress_pct}%** — {current_item}"
                
                yield partial_md, ""
            
            # Final result
            results = partial_results  # Last results from generator
            lines = ["**✅ Healthcheck complete!**\n"]
            lines.append("| Component | Status | Details |")
            lines.append("|---|---|---|")
            for r in results:
                icon = "✅" if r.ok else ("🔴" if r.fatal else "⚠️")
                lines.append(f"| {r.name} | {icon} | {r.detail} |")
            
            fatals = [r for r in results if not r.ok and r.fatal]
            if fatals:
                lines.append(f"\n**🔴 Critical issues ({len(fatals)}):** "
                             + ", ".join(r.name for r in fatals))
            
            warnings = [r for r in results if not r.ok and not r.fatal]
            if warnings:
                lines.append(f"\n**⚠️ Warnings ({len(warnings)}):** "
                             + ", ".join(r.name for r in warnings))
            
            if not fatals and not warnings:
                lines.append("\n**All components are healthy.**")
            
            final_md = "\n".join(lines)
            yield "", final_md
            
        except Exception:
            tb = traceback.format_exc()
            logger.error("Tab9 healthcheck exception:\n%s", tb)
            yield "", f"❌ Healthcheck error:\n```\n{tb}\n```"

    check_btn.click(fn=handle_healthcheck_streaming, outputs=[progress_text, health_output])


# ═══════════════════════════════════════════════════════════════════════════════
# App assembly
# ═══════════════════════════════════════════════════════════════════════════════

def build_app() -> gr.Blocks:
    """Construct the full Gradio Blocks application."""
    with gr.Blocks(
        title="Pharm Agent",
        theme=gr.themes.Soft(),
    ) as app:
        gr.Markdown(f"# 💊 Pharm Agent — MVP\n\n{DISCLAIMER}")

        # Shared state across tabs
        state = gr.State({})

        with gr.Tabs():
            with gr.TabItem("1. New Analysis"):
                submit_btn, inputs, outputs, handler = _tab_new_analysis()
                submit_btn.click(
                    fn=handler,
                    inputs=inputs + [state],
                    outputs=outputs + [state],
                )

            with gr.TabItem("2. Verification"):
                _tab_verification()

            with gr.TabItem("3. Progress"):
                _tab_run_progress()

            with gr.TabItem("4. Evidence"):
                _tab_evidence()

            with gr.TabItem("5. Scientific Memo"):
                _tab_memo()

            with gr.TabItem("6. Market Memo"):
                _tab_market_memo()

            with gr.TabItem("7. Final Synthesis"):
                _tab_synthesis()

            with gr.TabItem("8. Logs"):
                _tab_logs()

            with gr.TabItem("9. Healthcheck"):
                _tab_settings()

    return app


def main() -> None:
    """Launch the Gradio server."""
    app = build_app()
    app.launch(server_name="127.0.0.1", server_port=7860)


if __name__ == "__main__":
    main()
