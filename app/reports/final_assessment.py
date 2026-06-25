"""Final Assessment Markdown Report Generator — Three-Pillar Structure.

Generates the final pharma opportunity assessment report from FinalSynthesisOutput.
This module is responsible ONLY for formatting — no LLM calls, no business logic.

Three pillars:
1. Коммерческая привлекательность — What exists on the market
2. Научная обоснованность / Спрос — Is there demand?
3. Финансовая жизнеспособность — Patents, FTO, investment
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import config
from app.schemas.synthesis import FinalSynthesisOutput


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _frontmatter(data: dict[str, Any]) -> str:
    """Generate YAML frontmatter block."""
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            for v in value:
                lines.append(f"  - {v}")
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for k2, v2 in value.items():
                lines.append(f"  {k2}: {v2}")
        elif isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def _severity_icon(severity: str) -> str:
    return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(severity.lower(), "⚪")


def _threat_icon(threat: str) -> str:
    return {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(threat.lower(), "⚪")


def _priority_icon(priority: str) -> str:
    return {"high": "❗", "medium": "⚠️", "low": "ℹ️"}.get(priority.lower(), "•")


def _go_no_go_icon(interpretation: str) -> str:
    return {
        "go": "✅",
        "conditional_go": "⚠️",
        "no_go": "❌",
        "insufficient_evidence": "❓",
    }.get(interpretation.lower(), "❓")


def _direction_icon(direction: str) -> str:
    return {"growing": "📈", "stable": "➡️", "declining": "📉", "unknown": "❓"}.get(direction.lower(), "❓")


def _fto_icon(level: str) -> str:
    return {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴", "unknown": "❓"}.get(level.lower(), "❓")


def generate_final_assessment_markdown(
    run_id: str,
    output: FinalSynthesisOutput,
    vault_dir: Path | None = None,
) -> Path:
    """Generate the final assessment Markdown report.

    Args:
        run_id: The run identifier.
        output: Validated FinalSynthesisOutput from the synthesis agent.
        vault_dir: Optional override for vault directory.

    Returns:
        Path to the written Markdown file.
    """
    vd = vault_dir or config.vault_dir
    reports_dir = vd / "04_reports" / "final"
    reports_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{run_id}_final_assessment.md"
    path = reports_dir / filename

    inn = output.input_summary.inn_preferred
    disease = output.input_summary.disease_preferred or "N/A"

    front = {
        "type": "final_assessment",
        "run_id": run_id,
        "inn": inn,
        "disease": disease,
        "status": "completed",
        "human_verified": output.input_summary.human_verification_status,
        "go_no_go": output.overall_conclusion.go_no_go_interpretation,
        "created_at": output.created_at,
        "review_required": len(output.manual_review_required) > 0,
        "report_type": "three_pillar_synthesis",
        "contradictions_count": len(output.contradictions),
        "manual_review_items": len(output.manual_review_required),
    }

    lines: list[str] = [
        _frontmatter(front),
        "",
        f"# Комплексный анализ: {inn} / {disease}",
        "",
        f"**Run ID:** `{run_id}`  ",
        f"**Дата генерации:** {output.created_at}",
        "",
        "---",
        "",
    ]

    # Section 1: Executive Summary
    lines.extend(_section_executive_summary(output))

    # Section 2: Input and Scope
    lines.extend(_section_input_scope(output))

    # PILLAR 1: Commercial Attractiveness
    lines.extend(_section_pillar1_commercial(output))

    # PILLAR 2: Scientific Rationale / Demand
    lines.extend(_section_pillar2_demand(output))

    # PILLAR 3: Financial Viability
    lines.extend(_section_pillar3_financial(output))

    # Integrated Assessment
    lines.extend(_section_integrated_assessment(output))

    # Monetization
    lines.extend(_section_monetization(output))

    # Contradictions
    lines.extend(_section_contradictions(output))

    # Expert Review
    lines.extend(_section_expert_review(output))

    # Sources
    lines.extend(_section_sources(output))

    # Disclaimers
    lines.extend(_section_disclaimers(output))

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# Section generators
# ═══════════════════════════════════════════════════════════════════════════════


def _section_executive_summary(output: FinalSynthesisOutput) -> list[str]:
    conclusion = output.overall_conclusion
    icon = _go_no_go_icon(conclusion.go_no_go_interpretation)

    lines = [
        "## 1. Резюме / Executive Summary",
        "",
        f"### Итоговая оценка: {icon} **{conclusion.go_no_go_interpretation.upper().replace('_', ' ')}**",
        "",
        conclusion.summary,
        "",
        f"**Главная причина:** {conclusion.main_reason}",
        "",
        "**Критические зависимости:**",
        "",
    ]

    if conclusion.critical_dependencies:
        for dep in conclusion.critical_dependencies:
            lines.append(f"- {dep}")
    else:
        lines.append("- Не выявлены")

    lines.extend(["", "---", ""])
    return lines


def _section_input_scope(output: FinalSynthesisOutput) -> list[str]:
    inp = output.input_summary

    lines = [
        "## 2. Входные данные и контекст",
        "",
        f"- **Препарат (МНН/INN):** {inp.inn_preferred}",
    ]

    if inp.inn_english:
        lines.append(f"- **English INN:** {inp.inn_english}")
    if inp.inn_russian:
        lines.append(f"- **Русское название:** {inp.inn_russian}")

    lines.append(f"- **Тип молекулы:** {inp.molecule_type}")
    lines.append(f"- **Заболевание:** {inp.disease_preferred or 'N/A'}")

    if inp.disease_synonyms:
        lines.append(f"- **Синонимы заболевания:** {', '.join(inp.disease_synonyms)}")
    if inp.target_patient_segment:
        lines.append(f"- **Целевой сегмент пациентов:** {inp.target_patient_segment}")

    lines.append(f"- **Регион:** {inp.region or 'Глобальный'}")
    lines.append(f"- **Стадия разработки:** {inp.development_stage or 'Не указана'}")
    lines.append(f"- **Верификация человеком:** {inp.human_verification_status}")

    lines.extend(["", "---", ""])
    return lines


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 1: Коммерческая привлекательность
# ═══════════════════════════════════════════════════════════════════════════════


def _section_pillar1_commercial(output: FinalSynthesisOutput) -> list[str]:
    comm = output.commercial_attractiveness
    disease = output.input_summary.disease_preferred or "данного заболевания"

    lines = [
        "## 3. 💼 КОММЕРЧЕСКАЯ ПРИВЛЕКАТЕЛЬНОСТЬ",
        "",
        f"*Главный вопрос: Что уже есть на рынке для лечения {disease}?*",
        "",
        "### Общий вывод",
        "",
        comm.summary,
        "",
    ]

    # 3.1 Existing drugs on market
    lines.extend([
        f"### 3.1 Какие лекарства врачи выписывают сейчас для {disease}",
        "",
    ])

    if comm.existing_drugs_summary:
        lines.extend([comm.existing_drugs_summary, ""])

    if comm.existing_drugs:
        lines.extend([
            "| Препарат | Механизм | Сильные стороны | Слабые стороны | Позиция |",
            "|----------|----------|-----------------|----------------|---------|",
        ])
        for drug in comm.existing_drugs:
            strengths = "; ".join(drug.strengths[:3]) if drug.strengths else "—"
            weaknesses = "; ".join(drug.weaknesses[:3]) if drug.weaknesses else "—"
            lines.append(
                f"| {drug.drug_name} | {drug.mechanism or '—'} | {strengths} | {weaknesses} | {drug.market_position or '—'} |"
            )
        lines.append("")
    else:
        lines.extend(["*Данные о текущих препаратах не найдены.*", ""])

    # 3.2 Pipeline competitors
    lines.extend([
        "### 3.2 Что скоро появится (конкуренты в разработке)",
        "",
    ])

    if comm.pipeline_threat_assessment:
        lines.extend([comm.pipeline_threat_assessment, ""])

    if comm.pipeline_competitors:
        lines.extend([
            "| Препарат | Компания | Фаза | Сроки | Угроза | Обоснование |",
            "|----------|----------|------|-------|--------|-------------|",
        ])
        for comp in comm.pipeline_competitors:
            icon = _threat_icon(comp.competitive_threat)
            lines.append(
                f"| {comp.drug_name} | {comp.company or '—'} | {comp.phase or '—'} "
                f"| {comp.expected_timeline or '—'} | {icon} {comp.competitive_threat} "
                f"| {(comp.threat_rationale or '—')[:60]} |"
            )
        lines.append("")
    else:
        lines.extend(["*Данные о конкурентах в разработке не найдены.*", ""])

    # 3.3 Gold standard
    lines.extend([
        "### 3.3 Золотой стандарт лечения",
        "",
    ])

    std = comm.treatment_standard
    if std:
        lines.append(f"**Текущий золотой стандарт:** {std.standard_name}")
        lines.append("")
        if std.description:
            lines.append(std.description)
            lines.append("")
        if std.efficacy_bar:
            lines.append(f"**Планка эффективности:** {std.efficacy_bar}")
        if std.key_limitations:
            lines.append("")
            lines.append("**Ограничения текущего стандарта:**")
            for lim in std.key_limitations:
                lines.append(f"- {lim}")
        if std.what_our_drug_must_beat:
            lines.append("")
            lines.append(f"**Что наш препарат должен превзойти:** {std.what_our_drug_must_beat}")
        lines.append("")
    else:
        lines.extend(["*Золотой стандарт не определён.*", ""])

    if comm.our_drug_vs_standard:
        lines.extend([
            "### Наш препарат vs стандарт лечения",
            "",
            comm.our_drug_vs_standard,
            "",
        ])

    # Commercial risks
    if comm.commercial_risks:
        lines.extend([
            "### Коммерческие риски",
            "",
        ])
        for r in comm.commercial_risks:
            lines.append(f"- ⚠️ {r}")
        lines.append("")

    lines.extend(["---", ""])
    return lines


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 2: Научная обоснованность / Спрос
# ═══════════════════════════════════════════════════════════════════════════════


def _section_pillar2_demand(output: FinalSynthesisOutput) -> list[str]:
    sci = output.scientific_rationale
    disease = output.input_summary.disease_preferred or "данного заболевания"

    lines = [
        "## 4. 🔬 НАУЧНАЯ ОБОСНОВАННОСТЬ / СПРОС",
        "",
        f"*Главный вопрос: Есть ли спрос на новое лекарство от {disease}? Сколько пациентов?*",
        "",
        "### Общий вывод",
        "",
        sci.summary,
        "",
    ]

    # 4.1 Disease prevalence
    lines.extend([
        f"### 4.1 Размер и объём рынка: сколько болеют {disease}?",
        "",
    ])

    prev = sci.disease_prevalence
    if prev:
        if prev.global_prevalence:
            lines.append(f"- **Глобальная распространённость:** {prev.global_prevalence}")
        if prev.regional_prevalence:
            lines.append(f"- **Региональная распространённость:** {prev.regional_prevalence}")
        if prev.incidence_rate:
            lines.append(f"- **Заболеваемость (новые случаи/год):** {prev.incidence_rate}")
        icon = _direction_icon(prev.trend)
        lines.append(f"- **Тренд:** {icon} {prev.trend}")
        if prev.trend_drivers:
            lines.append("- **Факторы тренда:**")
            for d in prev.trend_drivers:
                lines.append(f"  - {d}")
        lines.append("")
    else:
        lines.extend(["*Данные о распространённости не доступны.*", ""])

    # 4.2 Target segment
    lines.extend([
        "### 4.2 Целевой сегмент пациентов",
        "",
    ])

    seg = sci.target_patient_segment
    if seg:
        lines.append(f"**Описание сегмента:** {seg.segment_description}")
        if seg.segment_size_vs_total:
            lines.append(f"**Доля от всех пациентов:** {seg.segment_size_vs_total}")
        if seg.selection_criteria:
            lines.append("**Критерии отбора:**")
            for c in seg.selection_criteria:
                lines.append(f"- {c}")
        if seg.rationale:
            lines.append(f"**Обоснование:** {seg.rationale}")
        lines.append("")
    else:
        lines.extend(["*Целевой сегмент не определён.*", ""])

    if sci.realistic_patient_pool:
        lines.extend([
            f"**Реалистичный пул пациентов (после сегментации):** {sci.realistic_patient_pool}",
            "",
        ])

    # 4.3 Market dynamics
    lines.extend([
        "### 4.3 Динамика рынка: рынок растёт или падает?",
        "",
    ])

    dyn = sci.market_dynamics
    if dyn:
        icon = _direction_icon(dyn.market_direction)
        lines.append(f"**Направление рынка:** {icon} {dyn.market_direction}")
        lines.append("")
        if dyn.key_drivers:
            lines.append("**Драйверы роста:**")
            for d in dyn.key_drivers:
                lines.append(f"- 📈 {d}")
        if dyn.key_barriers:
            lines.append("")
            lines.append("**Барьеры:**")
            for b in dyn.key_barriers:
                lines.append(f"- 📉 {b}")
        if dyn.diagnostic_improvement_impact:
            lines.append(f"\n**Влияние улучшения диагностики:** {dyn.diagnostic_improvement_impact}")
        if dyn.standard_of_care_shifts:
            lines.append(f"**Изменение стандартов лечения:** {dyn.standard_of_care_shifts}")
        lines.append("")
    else:
        lines.extend(["*Данные о динамике рынка не доступны.*", ""])

    # 4.4 Payer value
    lines.extend([
        "### 4.4 Ценность для плательщиков: кто и за что заплатит?",
        "",
    ])

    pv = sci.payer_value
    if pv:
        if pv.value_for_physician:
            lines.append(f"**Для врача:** {pv.value_for_physician}")
        if pv.value_for_patient:
            lines.append(f"**Для пациента:** {pv.value_for_patient}")
        if pv.value_for_payer:
            lines.append(f"**Для страховой/государства:** {pv.value_for_payer}")
        if pv.health_economics_argument:
            lines.append(f"**Фармакоэкономический аргумент:** {pv.health_economics_argument}")
        lines.append("")
    else:
        lines.extend(["*Оценка ценности для плательщиков не проведена.*", ""])

    # 4.5 Pricing
    lines.extend([
        "### 4.5 Прогноз продаж и ценообразование",
        "",
    ])

    pf = sci.pricing_forecast
    if pf:
        if pf.competitor_price_range:
            lines.append(f"**Цены конкурентов:** {pf.competitor_price_range}")
        if pf.our_price_rationale:
            lines.append(f"**Обоснование нашей цены:** {pf.our_price_rationale}")
        if pf.premium_justification:
            lines.append(f"**Обоснование премиальной цены:** {pf.premium_justification}")
        if pf.price_sensitivity_conclusion:
            lines.append(f"**Вывод о чувствительности к цене:** {pf.price_sensitivity_conclusion}")
        if pf.market_size_estimate:
            lines.append(f"**Оценка размера рынка:** {pf.market_size_estimate}")
        lines.append("")
    else:
        lines.extend(["*Данные о ценообразовании не доступны.*", ""])

    # Mechanism & unmet need
    if sci.mechanism_summary:
        lines.extend([
            "### Механизм действия",
            "",
            sci.mechanism_summary,
            "",
        ])

    if sci.unmet_need_summary:
        lines.extend([
            "### Неудовлетворённая медицинская потребность",
            "",
            sci.unmet_need_summary,
            "",
        ])

    # Risks and gaps
    if sci.risks:
        lines.extend(["### Научные риски", ""])
        for r in sci.risks:
            lines.append(f"- ⚠️ {r}")
        lines.append("")

    if sci.evidence_gaps:
        lines.extend(["### Пробелы в доказательной базе", ""])
        for g in sci.evidence_gaps:
            lines.append(f"- ❓ {g}")
        lines.append("")

    lines.extend(["---", ""])
    return lines


# ═══════════════════════════════════════════════════════════════════════════════
# PILLAR 3: Финансовая жизнеспособность
# ═══════════════════════════════════════════════════════════════════════════════


def _section_pillar3_financial(output: FinalSynthesisOutput) -> list[str]:
    pat = output.patent_and_financial_viability
    inn = output.input_summary.inn_preferred

    lines = [
        "## 5. 🛡️ ФИНАНСОВАЯ ЖИЗНЕСПОСОБНОСТЬ",
        "",
        "*Главный вопрос: Свободен ли путь для разработки? Как защитить монополию?*",
        "",
        "### Общий вывод",
        "",
        pat.summary,
        "",
    ]

    # 5.1 FTO Check
    lines.extend([
        f"### 5.1 Проверка FTO: не нарушаем ли мы чужие патенты?",
        "",
    ])

    fto = pat.fto_check
    if fto:
        icon = _fto_icon(fto.fto_risk_level)
        lines.append(f"**Уровень риска FTO:** {icon} **{fto.fto_risk_level.upper()}**")
        lines.append("")
        if fto.total_relevant_patents is not None:
            lines.append(f"- **Всего релевантных патентов:** {fto.total_relevant_patents}")
        if fto.active_blocking_patents_found is not None:
            lines.append(f"- **Потенциально блокирующих:** {fto.active_blocking_patents_found}")
        if fto.composition_patents:
            lines.append(f"- **Патенты на вещество:** {fto.composition_patents}")
        if fto.process_patents:
            lines.append(f"- **Патенты на способ получения:** {fto.process_patents}")
        if fto.indication_patents:
            lines.append(f"- **Патенты на показание:** {fto.indication_patents}")
        if fto.formulation_patents:
            lines.append(f"- **Патенты на форму выпуска:** {fto.formulation_patents}")
        lines.append("")
        if fto.fto_conclusion:
            lines.extend([f"**Вывод FTO:** {fto.fto_conclusion}", ""])
        if fto.more_patents_means_less_attractive:
            lines.extend([f"**Оценка привлекательности:** {fto.more_patents_means_less_attractive}", ""])
    else:
        lines.extend(["*Анализ FTO не проведён.*", ""])

    # 5.2 Patent expiry impacts
    lines.extend([
        "### 5.2 Истечение патентов конкурентов",
        "",
    ])

    if pat.patent_expiry_impacts:
        lines.extend([
            "| Препарат | Истечение патента | Дженерики | Влияние на рынок | Для нас |",
            "|----------|-------------------|-----------|------------------|---------|",
        ])
        for exp in pat.patent_expiry_impacts:
            opp_icon = {"opportunity": "🟢", "threat": "🔴", "neutral": "⚪", "unknown": "❓"}.get(
                exp.opportunity_or_threat, "❓"
            )
            lines.append(
                f"| {exp.drug_name} | {exp.patent_expiry_date or '—'} "
                f"| {exp.generic_entry_expected or '—'} "
                f"| {(exp.market_impact or '—')[:50]} "
                f"| {opp_icon} {exp.opportunity_or_threat} |"
            )
        lines.append("")
    else:
        lines.extend(["*Данные об истечении патентов конкурентов не доступны.*", ""])

    # 5.3 Patent fence strategy
    lines.extend([
        f"### 5.3 Патентный забор: как защитить {inn}",
        "",
    ])

    fence = pat.patent_fence
    if fence:
        if fence.primary_patent:
            lines.append(f"**Основной патент на молекулу:** {fence.primary_patent}")
        if fence.secondary_patents:
            lines.append("**Вторичные патенты (продление защиты):**")
            for sp in fence.secondary_patents:
                lines.append(f"- 🛡️ {sp}")
        if fence.total_protection_window:
            lines.append(f"**Общее окно защиты:** {fence.total_protection_window}")
        feas_icon = {"low": "🔴", "medium": "🟡", "high": "🟢", "unknown": "❓"}.get(
            fence.patent_fence_feasibility, "❓"
        )
        lines.append(f"**Осуществимость патентного забора:** {feas_icon} {fence.patent_fence_feasibility}")
        if fence.strategy_summary:
            lines.extend(["", fence.strategy_summary])
        lines.append("")
    else:
        lines.extend(["*Стратегия патентного забора не разработана.*", ""])

    # 5.4 Investment range
    if pat.investment_range:
        inv = pat.investment_range
        lines.extend([
            "### 5.4 Инвестиционный профиль",
            "",
            "| Сценарий | Сумма | Предпосылки |",
            "|----------|-------|-------------|",
            f"| Низкий | {inv.low_case} | {'; '.join(inv.assumptions[:2]) if inv.assumptions else '—'} |",
            f"| Базовый | {inv.base_case} | — |",
            f"| Высокий | {inv.high_case} | — |",
            "",
            f"**Валюта:** {inv.currency}",
            "",
        ])

        if inv.assumptions:
            lines.extend([
                "**Ключевые предпосылки:**",
                "",
                *[f"- {a}" for a in inv.assumptions],
                "",
            ])

    # Legacy FTO risks
    if pat.fto_risks:
        lines.extend([
            "### Риски Freedom-to-Operate",
            "",
            *[f"- {_severity_icon('high')} {r}" for r in pat.fto_risks],
            "",
        ])

    if pat.patent_fence_opportunities:
        lines.extend([
            "### Возможности для патентной защиты",
            "",
            *[f"- 🛡️ {o}" for o in pat.patent_fence_opportunities],
            "",
        ])

    lines.extend(["---", ""])
    return lines


# ═══════════════════════════════════════════════════════════════════════════════
# Integrated assessment, monetization, contradictions, review, sources, disclaimers
# ═══════════════════════════════════════════════════════════════════════════════


def _section_integrated_assessment(output: FinalSynthesisOutput) -> list[str]:
    lines = [
        "## 6. Интегрированная оценка",
        "",
        "### Что поддерживает разработку",
        "",
    ]

    if output.scientific_rationale.strengths:
        for s in output.scientific_rationale.strengths[:3]:
            lines.append(f"- ✅ {s}")
    else:
        lines.append("- (Явные научные преимущества не выявлены)")

    lines.extend(["", "### Что блокирует разработку", ""])

    blockers = []
    if output.scientific_rationale.risks:
        blockers.extend(output.scientific_rationale.risks[:2])
    if output.patent_and_financial_viability.fto_risks:
        blockers.extend(output.patent_and_financial_viability.fto_risks[:2])

    if blockers:
        for b in blockers:
            lines.append(f"- ❌ {b}")
    else:
        lines.append("- (Критические блокеры не выявлены)")

    lines.extend(["", "### Что остаётся неопределённым", ""])

    uncertainties = []
    if output.scientific_rationale.evidence_gaps:
        uncertainties.extend(output.scientific_rationale.evidence_gaps[:3])

    if uncertainties:
        for u in uncertainties:
            lines.append(f"- ❓ {u}")
    else:
        lines.append("- (Ключевые неопределённости не выявлены)")

    lines.extend(["", "---", ""])
    return lines


def _section_monetization(output: FinalSynthesisOutput) -> list[str]:
    pat = output.patent_and_financial_viability
    timeline = pat.monetization_timeline

    lines = [
        "## 7. Когда можно получить деньги?",
        "",
    ]

    if timeline:
        if timeline.earliest_value_inflection:
            lines.extend([
                "### Ближайшая точка создания стоимости",
                "",
                f"**{timeline.earliest_value_inflection}**",
                "",
            ])

        if timeline.licensing_window:
            lines.extend(["### Окно лицензирования", "", f"{timeline.licensing_window}", ""])

        if timeline.revenue_window:
            lines.extend(["### Окно выручки", "", f"{timeline.revenue_window}", ""])

        if timeline.required_evidence_for_monetization:
            lines.extend([
                "### Необходимые доказательства для монетизации",
                "",
                *[f"- {e}" for e in timeline.required_evidence_for_monetization],
                "",
            ])

        if timeline.key_risks:
            lines.extend([
                "### Ключевые риски для таймлайна",
                "",
                *[f"- ⚠️ {r}" for r in timeline.key_risks],
                "",
            ])
    else:
        lines.extend([
            "**Таймлайн монетизации не оценён.** Недостаточно данных от предыдущих этапов.",
            "",
        ])

    lines.extend(["---", ""])
    return lines


def _section_contradictions(output: FinalSynthesisOutput) -> list[str]:
    lines = [
        "## 8. Противоречия и неразрешённые допущения",
        "",
    ]

    if output.contradictions:
        lines.extend([
            "### Выявленные противоречия",
            "",
            "| Область | Серьёзность | Описание | Затронутый вывод |",
            "|---------|-------------|----------|------------------|",
        ])

        for c in output.contradictions:
            icon = _severity_icon(c.severity)
            desc = c.description[:80] + ("..." if len(c.description) > 80 else "")
            lines.append(f"| {c.area} | {icon} {c.severity} | {desc} | {c.affected_conclusion} |")
        lines.append("")
    else:
        lines.extend(["**Противоречий не выявлено** между выходами предыдущих этапов.", ""])

    if output.source_availability_warnings:
        lines.extend(["### Предупреждения о доступности источников", ""])
        for w in output.source_availability_warnings:
            icon = {"unavailable": "❌", "partial": "⚠️", "stale": "🕐", "error": "💥"}.get(w.warning_type, "⚠️")
            lines.append(f"- {icon} **{w.source_name}**: {w.description}")
            if w.impact_on_analysis:
                lines.append(f"  - Влияние: {w.impact_on_analysis}")
        lines.append("")

    lines.extend(["---", ""])
    return lines


def _section_expert_review(output: FinalSynthesisOutput) -> list[str]:
    lines = [
        "## 9. Чек-лист экспертной проверки",
        "",
    ]

    if output.manual_review_required:
        lines.extend([
            "Следующие пункты требуют экспертной проверки:",
            "",
            "| Область | Приоритет | Тип эксперта | Причина |",
            "|---------|-----------|--------------|---------|",
        ])

        for item in output.manual_review_required:
            icon = _priority_icon(item.priority)
            reason = item.reason[:60] + ("..." if len(item.reason) > 60 else "")
            lines.append(
                f"| {item.area} | {icon} {item.priority} | {item.recommended_expert_type} | {reason} |"
            )
        lines.append("")
    else:
        lines.extend(["**Специфические пункты для экспертной проверки не выявлены.** Стандартная due diligence рекомендуется.", ""])

    if output.next_steps:
        lines.extend(["### Рекомендованные следующие шаги", ""])
        for i, step in enumerate(output.next_steps, 1):
            icon = _priority_icon(step.priority)
            lines.extend([
                f"**{i}. {step.action}** {icon}",
                "",
                f"- Обоснование: {step.rationale}",
            ])
            if step.responsible_party:
                lines.append(f"- Ответственный: {step.responsible_party}")
            if step.timeline:
                lines.append(f"- Сроки: {step.timeline}")
            lines.append("")

    lines.extend(["---", ""])
    return lines


def _section_sources(output: FinalSynthesisOutput) -> list[str]:
    lines = [
        "## 10. Таблица источников",
        "",
    ]

    if output.source_references:
        lines.extend([
            "| Source ID | Тип | Название | Использован в |",
            "|-----------|-----|----------|---------------|",
        ])

        for ref in output.source_references[:30]:
            used_in = ", ".join(ref.used_in_sections[:3]) if ref.used_in_sections else "—"
            title_short = (ref.title or "—")[:40]
            lines.append(f"| `{ref.source_id}` | {ref.source_type} | {title_short} | {used_in} |")

        if len(output.source_references) > 30:
            lines.append(f"| ... | ... | +{len(output.source_references) - 30} ещё | ... |")
        lines.append("")
    else:
        lines.extend(["**Источники не указаны.** Возможно, неполная цитация при синтезе.", ""])

    lines.extend(["---", ""])
    return lines


def _section_disclaimers(output: FinalSynthesisOutput) -> list[str]:
    lines = [
        "## 11. Дисклеймеры",
        "",
    ]

    disclaimers = output.disclaimers or []
    categories_present = {d.category for d in disclaimers}

    default_disclaimers = [
        ("medical", "⚕️ **Медицинский дисклеймер**: Данный анализ предназначен исключительно для целей R&D и инвестиционных исследований. Он не является медицинским советом, клиническим руководством или заменой квалифицированной профессиональной экспертизы."),
        ("patent", "⚖️ **Патентный дисклеймер**: Это предварительный AI-анализ патентного ландшафта, не являющийся юридическим заключением о свободе действий (FTO). Перед принятием бизнес-решений требуется проверка квалифицированным патентным поверенным."),
        ("financial", "💰 **Финансовый дисклеймер**: Диапазоны инвестиций и финансовые прогнозы являются сценарными оценками для целей планирования. Они не являются инвестиционным советом и требуют валидации финансовыми аналитиками."),
    ]

    for category, text in default_disclaimers:
        if category not in categories_present:
            lines.extend([f"> {text}", ""])

    for d in disclaimers:
        icon = {"medical": "⚕️", "patent": "⚖️", "financial": "💰", "legal": "📜", "general": "ℹ️"}.get(d.category, "ℹ️")
        lines.extend([f"> {icon} **{d.category.title()}**: {d.text}", ""])

    lines.extend([
        "",
        "---",
        "",
        f"*Отчёт сгенерирован модулем синтеза Pharm Agent в {_now_iso()}*",
        "",
    ])

    return lines
