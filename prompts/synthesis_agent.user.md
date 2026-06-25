# Final Synthesis Request — Three-Pillar Assessment

Synthesize the following validated outputs into a comprehensive three-pillar final assessment for the drug-disease pair.

**IMPORTANT:** The disease/indication is as important as the drug. Analyze both together in every pillar.

---

## 1. Normalized Input

**Run ID:** {run_id}

**Drug (INN/МНН):**
- Preferred name: {inn_preferred}
- English INN: {inn_english}
- Russian name: {inn_russian}
- Synonyms: {inn_synonyms}
- Molecule type: {molecule_type}

**Disease/Indication:**
- Preferred name: {disease_preferred}
- Synonyms: {disease_synonyms}

**Context:**
- Region: {region}
- Development stage: {stage}
- Target patient segment: {target_patient_segment}

**Human Verification:**
- Status: {human_verification_status}
- Timestamp: {human_verification_timestamp}
- Comments: {human_verification_comments}

**PDF Documents Used:**
{pdf_hashes}

---

## 2. Scientific Agent Output

```json
{scientific_output}
```

---

## 3. Market Agent Output

```json
{market_output}
```

---

## 4. Patent & Finance Agent Output

```json
{patent_finance_output}
```

---

## 5. Source Registry

Available sources for citation:

{source_registry}

---

## 6. Source Availability Warnings

{source_warnings}

---

## 7. Pre-Detected Contradictions

{detected_contradictions}

---

## 8. Audit Summary

- Scientific analysis completed: {scientific_completed}
- Market analysis completed: {market_completed}
- Patent/finance analysis completed: {patent_finance_completed}
- Total sources collected: {total_sources}
- Source warnings count: {source_warnings_count}

---

## Your Task — Three-Pillar Assessment

Based on the above inputs, produce a FinalSynthesisOutput JSON object organized around THREE PILLARS:

### PILLAR 1: commercial_attractiveness — Коммерческая привлекательность

Answer: **What exists on the market NOW for treating {disease_preferred}?**

Fill in these fields from the data above:
- `existing_drugs` — List drugs doctors currently prescribe for {disease_preferred}. For each: name, mechanism, strengths (e.g. high efficacy, low cost), weaknesses (e.g. serious side effects, inconvenient dosing). Use data from scientific agent's approved_therapies + market agent's competitors.
- `existing_drugs_summary` — 2-3 sentence overview of what's available
- `pipeline_competitors` — List drugs in clinical trials from ClinicalTrials.gov data. Flag any competitor 2-3 years ahead as critical threat.
- `pipeline_threat_assessment` — Overall: how threatening is the pipeline?
- `treatment_standard` — What is the gold standard treatment? What must our drug beat?
- `our_drug_vs_standard` — How does {inn_preferred} compare to the gold standard?

### PILLAR 2: scientific_rationale — Научная обоснованность / Спрос

Answer: **Is there DEMAND for a new drug for {disease_preferred}? How many patients?**

Fill in these fields:
- `disease_prevalence` — How many people have {disease_preferred}? Globally, regionally. Growing or declining?
- `target_patient_segment` — Who exactly is our target? Not all {disease_preferred} patients, but our realistic addressable segment (e.g. specific mutation, specific subtype, specific line of therapy)
- `realistic_patient_pool` — After segmentation, how many patients realistically?
- `market_dynamics` — Is this market growing/declining? Why? (aging population, better diagnostics, changing standards)
- `payer_value` — Why would doctors/patients/insurers choose this drug? What's the value proposition?
- `pricing_forecast` — What do competitors cost? Can we charge a premium? If 20% better but 2x price, would the market accept?
- `mechanism_summary` — Brief mechanism of action
- `unmet_need_summary` — What unmet medical need does this address?

### PILLAR 3: patent_and_financial_viability — Финансовая жизнеспособность

Answer: **Is the path clear for development? How to protect our monopoly?**

Fill in these fields:
- `fto_check` — Do we infringe active patents? Check composition/process/indication/formulation patents. How many blocking patents? Overall FTO risk level.
- `patent_expiry_impacts` — When do competitor patents expire? Will generics enter? Is this opportunity or threat?
- `patent_fence` — How can we build patent protection? Primary patent + secondary patents for extended monopoly.
- `investment_range` — Low/base/high investment scenarios with assumptions
- `monetization_timeline` — When can we start making money?

### Also provide:
- `overall_conclusion` — Go/no-go with rationale
- `contradictions` — Including pre-detected ones and any new ones
- `source_availability_warnings` — From the provided list
- `manual_review_required` — Items requiring expert review
- `next_steps` — Recommended actions
- `source_references` — All sources used
- `disclaimers` — Medical, patent, financial disclaimers

Remember:
- Use ONLY the source_ids from the source registry
- Do NOT invent new evidence
- Be explicit about uncertainty
- The disease/indication context is CRITICAL — analyze drug + disease together
- Return ONLY valid JSON, no markdown or commentary
