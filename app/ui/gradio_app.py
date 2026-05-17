"""Gradio Blocks UI for pharm_agent MVP1 + MVP2.

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
    "⚕️ Данный анализ предназначен исключительно для R&D и инвестиционных исследований. "
    "Он не является медицинской рекомендацией или клиническим руководством."
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
    gr.Markdown("## Новый анализ\n\nВведите данные о препарате и загрузите два PDF-документа.")

    with gr.Row():
        with gr.Column(scale=2):
            inn_input = gr.Textbox(label="МНН / INN *", placeholder="ацетилсалициловая кислота")
            disease_input = gr.Textbox(label="Заболевание / Indication", placeholder="ишемический инсульт")
            with gr.Row():
                region_input = gr.Dropdown(label="Регион", choices=REGION_CHOICES, value="global")
                stage_input = gr.Dropdown(label="Стадия разработки", choices=STAGE_CHOICES, value="")
            analyst_notes = gr.Textbox(label="Заметки аналитика", lines=2, placeholder="Опционально")

        with gr.Column(scale=1):
            pdf1_input = gr.File(label="PDF 1 *", file_types=[".pdf"], type="filepath")
            pdf2_input = gr.File(label="PDF 2 *", file_types=[".pdf"], type="filepath")

    submit_btn = gr.Button("🚀 Создать run и обогатить", variant="primary", size="lg")

    with gr.Row():
        run_id_output = gr.Textbox(label="Run ID", interactive=False)
        status_output = gr.Textbox(label="Статус", interactive=False)

    warning_output = gr.Textbox(label="Предупреждения / Ошибки", interactive=False, lines=3)
    enrichment_summary = gr.Markdown(label="Результат обогащения")
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
        except Exception:
            tb = traceback.format_exc()
            logger.error("Tab1 exception:\n%s", tb)
            return "", "failed", f"Исключение:\n{tb}", "", None, state

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
            "✅ Run создан. Перейдите на вкладку «Верификация».",
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
        "## 🔍 Верификация\n\n"
        "**Обязательный шаг.** Scientific Agent не запустится без вашего решения."
    )

    with gr.Row():
        verify_run_id = gr.Textbox(label="Run ID", scale=3)
        load_btn = gr.Button("Загрузить", scale=1)

    packet_display = gr.Markdown("*Введите Run ID и нажмите «Загрузить».*")

    gr.Markdown("---\n### Решение")

    with gr.Row():
        drug_fields = gr.Textbox(label="Нормализованные поля препарата (для редактирования)", lines=3)
        disease_fields = gr.Textbox(label="Нормализованные поля заболевания", lines=3)

    with gr.Row():
        ambiguities_box = gr.Textbox(label="Неоднозначности", lines=2, interactive=False)
        corrections_box = gr.Textbox(label="Коррекции (JSON)", lines=2, placeholder='{"inn_raw": "..."}')

    comments_box = gr.Textbox(label="Комментарий рецензента", lines=2)
    decision_radio = gr.Radio(
        choices=["approve", "approve_with_edits", "reject"],
        label="Решение",
        value="approve",
    )
    save_btn = gr.Button("💾 Сохранить верификацию", variant="primary")
    decision_result = gr.Textbox(label="Результат", interactive=False, lines=3)

    def handle_load(run_id):
        logger.info("Tab2: load run_id=%r", run_id)
        run_id = (run_id or "").strip()
        if not run_id:
            return "*Введите Run ID.*", "", "", "", ""

        packet = services.load_verification_packet(run_id)
        if packet is None:
            return (
                f"❌ Run `{run_id}` не найден или не в статусе `awaiting_human_verification`.",
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
                lines.append(f"- Русский: {inn_data['russian_name']}")
            if inn_data.get("synonyms"):
                lines.append(f"- Синонимы: {', '.join(inn_data['synonyms'])}")
            lines.append(f"- Confidence: {inn_data.get('confidence', '?')}")

        if dis_data:
            lines.append(f"\n**Normalized Disease:** {dis_data.get('preferred_name', '?')}")
            if dis_data.get("synonyms"):
                lines.append(f"- Синонимы: {', '.join(dis_data['synonyms'])}")

        if packet.questions:
            lines.append("\n**Вопросы:**")
            for q in packet.questions:
                lines.append(f"- ❓ {q}")

        if packet.completeness == "low":
            lines.append("\n> ⚠️ **LOW completeness** — рекомендуется reject или запросить revision.")

        display_md = "\n".join(lines)
        drug_str = json.dumps(inn_data, indent=2, ensure_ascii=False) if inn_data else ""
        disease_str = json.dumps(dis_data, indent=2, ensure_ascii=False) if dis_data else ""
        ambiguities_str = "\n".join(packet.ambiguities) if packet.ambiguities else ""

        return display_md, drug_str, disease_str, ambiguities_str, ""

    def handle_save(run_id, decision, comments, corrections_raw):
        logger.info("Tab2: save run_id=%r decision=%r", run_id, decision)
        run_id = (run_id or "").strip()
        if not run_id:
            return "❌ Укажите Run ID."

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
            return f"❌ Исключение:\n{tb}"

        if not result.success:
            return f"❌ {result.error}"
        return f"✅ Решение «{decision}» сохранено.\nСтатус: {result.status}\n➡️ {result.next_action}"

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
    gr.Markdown("## 📊 Прогресс выполнения")

    with gr.Row():
        progress_run_id = gr.Textbox(label="Run ID", scale=3)
        refresh_btn = gr.Button("🔄 Обновить статус", scale=1)
        run_scientific_btn = gr.Button("🧬 Запустить Scientific Agent", variant="primary", scale=1)

    status_md = gr.Markdown("")
    steps_table = gr.Dataframe(
        headers=["step", "status", "details"],
        label="Этапы пайплайна",
        interactive=False,
    )
    warning_output = gr.Textbox(label="Предупреждения", interactive=False, lines=3)

    def handle_refresh(run_id):
        logger.info("Tab3: refresh run_id=%r", run_id)
        run_id = (run_id or "").strip()
        if not run_id:
            return "Введите Run ID.", [], ""

        data = services.get_run_status(run_id)
        if "error" in data and not data.get("run_id"):
            return data["error"], [], ""

        md = (
            f"**Run:** `{data['run_id']}`\n"
            f"**Статус:** `{data['status']}`\n"
            f"**Создан:** {data['created_at']}\n"
            f"**Обновлён:** {data['updated_at']}"
        )

        steps = data.get("steps", [])
        warnings = []
        for s in steps:
            if "⚠️" in s.get("details", ""):
                warnings.append(f"{s['step']}: {s['details']}")

        table_data = [[s["step"], s["status"], s["details"]] for s in steps]
        warn_text = "\n".join(warnings) if warnings else "Нет предупреждений."

        if data.get("error"):
            warn_text = f"❌ Ошибка run: {data['error']}\n\n{warn_text}"

        return md, table_data, warn_text

    def handle_run_scientific(run_id):
        logger.info("Tab3: run_scientific run_id=%r", run_id)
        run_id = (run_id or "").strip()
        if not run_id:
            return "Введите Run ID.", [], "❌ Введите Run ID."

        msg = services.run_scientific_agent(run_id)

        # Refresh status after
        data = services.get_run_status(run_id)
        if "error" in data and not data.get("run_id"):
            return msg, [], msg

        md = (
            f"**Run:** `{data['run_id']}`\n"
            f"**Статус:** `{data['status']}`\n"
            f"**Обновлён:** {data['updated_at']}"
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
    gr.Markdown("## 🔬 Evidence Explorer\n\nПросмотр нормализованных источников для текущего run.")

    with gr.Row():
        evi_run_id = gr.Textbox(label="Run ID", scale=3)
        evi_filter = gr.Dropdown(label="Фильтр по типу", choices=SOURCE_TYPES, value="all", scale=1)
        evi_load_btn = gr.Button("Загрузить", scale=1)

    evi_table = gr.Dataframe(
        headers=[
            "source_id", "source_type", "title", "external_id",
            "publication_date", "query_used", "relevance", "warning",
        ],
        label="Источники",
        interactive=False,
    )
    evi_detail = gr.Markdown("")

    def handle_load_evidence(run_id, source_filter):
        logger.info("Tab4: load evidence run_id=%r filter=%r", run_id, source_filter)
        run_id = (run_id or "").strip()
        if not run_id:
            return [], "Введите Run ID."

        table = services.get_evidence_table(run_id, source_filter)
        if not table:
            return [], "Источники не найдены. Возможно, Scientific Agent ещё не запускался."

        col_keys = [
            "source_id", "source_type", "title", "external_id",
            "publication_date", "query_used", "relevance", "warning",
        ]
        rows = [[r.get(h, "") for h in col_keys] for r in table]
        summary = f"**Найдено источников:** {len(table)}"
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
        memo_load_btn = gr.Button("Загрузить memo", scale=1)

    memo_display = gr.Markdown("*Введите Run ID и нажмите «Загрузить».*")
    memo_json = gr.JSON(label="ScientificAgentOutput (JSON)", visible=False)

    with gr.Row():
        show_json_btn = gr.Button("Показать JSON")
        download_btn = gr.Button("📥 Скачать Markdown")

    download_file = gr.File(label="Файл для скачивания", visible=False)

    def handle_load_memo(run_id):
        logger.info("Tab5: load memo run_id=%r", run_id)
        run_id = (run_id or "").strip()
        if not run_id:
            return "*Введите Run ID.*", None

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
        mm_load_btn = gr.Button("Загрузить market memo", scale=1)

    mm_display = gr.Markdown("*Введите Run ID и нажмите «Загрузить».*")
    mm_json = gr.JSON(label="MarketAgentOutput (JSON)", visible=False)

    with gr.Row():
        mm_show_json_btn = gr.Button("Показать JSON")
        mm_download_btn = gr.Button("📥 Скачать Markdown")

    mm_download_file = gr.File(label="Файл для скачивания", visible=False)

    def handle_load_market(run_id):
        logger.info("Tab6: load market memo run_id=%r", run_id)
        run_id = (run_id or "").strip()
        if not run_id:
            return "*Введите Run ID.*", None

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
# Tab 7: Logs
# ═══════════════════════════════════════════════════════════════════════════════

def _tab_logs():
    gr.Markdown("## 📋 Audit Logs\n\nПросмотр событий для текущего run. Секреты маскируются.")

    with gr.Row():
        logs_run_id = gr.Textbox(label="Run ID", scale=3)
        logs_count = gr.Number(label="Последние N событий", value=50, minimum=1, maximum=500, scale=1)
        logs_filter = gr.Dropdown(label="Фильтр", choices=EVENT_TYPES, value="all", scale=1)
        logs_load_btn = gr.Button("Загрузить", scale=1)

    logs_output = gr.Textbox(label="Audit Events (JSON)", lines=20, interactive=False)

    def handle_load_logs(run_id, count, event_filter):
        logger.info("Tab6: load logs run_id=%r count=%r filter=%r", run_id, count, event_filter)
        run_id = (run_id or "").strip()
        if not run_id:
            return "Введите Run ID."

        return services.get_audit_events(run_id, int(count or 50), event_filter or "all")

    logs_load_btn.click(fn=handle_load_logs, inputs=[logs_run_id, logs_count, logs_filter], outputs=[logs_output])


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 7: Settings / Healthcheck
# ═══════════════════════════════════════════════════════════════════════════════

def _tab_settings():
    gr.Markdown("## ⚙️ Settings / Healthcheck\n\nПроверка доступности всех компонентов системы.")

    check_btn = gr.Button("🩺 Запустить healthcheck", variant="primary")
    health_output = gr.Markdown("")

    def handle_healthcheck():
        logger.info("Tab7: running healthcheck")
        try:
            return services.run_healthcheck()
        except Exception:
            tb = traceback.format_exc()
            return f"❌ Ошибка healthcheck:\n```\n{tb}\n```"

    check_btn.click(fn=handle_healthcheck, outputs=[health_output])


# ═══════════════════════════════════════════════════════════════════════════════
# App assembly
# ═══════════════════════════════════════════════════════════════════════════════

def build_app() -> gr.Blocks:
    """Construct the full Gradio Blocks application."""
    with gr.Blocks(
        title="Pharm Agent",
        theme=gr.themes.Soft(),
    ) as app:
        gr.Markdown(f"# 💊 Pharm Agent — MVP1 + MVP2\n\n{DISCLAIMER}")

        # Shared state across tabs
        state = gr.State({})

        with gr.Tabs():
            with gr.TabItem("1. Новый анализ"):
                submit_btn, inputs, outputs, handler = _tab_new_analysis()
                submit_btn.click(
                    fn=handler,
                    inputs=inputs + [state],
                    outputs=outputs + [state],
                )

            with gr.TabItem("2. Верификация"):
                _tab_verification()

            with gr.TabItem("3. Прогресс"):
                _tab_run_progress()

            with gr.TabItem("4. Evidence"):
                _tab_evidence()

            with gr.TabItem("5. Scientific Memo"):
                _tab_memo()

            with gr.TabItem("6. Market Memo"):
                _tab_market_memo()

            with gr.TabItem("7. Логи"):
                _tab_logs()

            with gr.TabItem("8. Healthcheck"):
                _tab_settings()

    return app


def main() -> None:
    """Launch the Gradio server."""
    app = build_app()
    app.launch(server_name="127.0.0.1", server_port=7860)


if __name__ == "__main__":
    main()
