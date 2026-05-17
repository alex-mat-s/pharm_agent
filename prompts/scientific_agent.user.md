Analyze the scientific and clinical rationale for developing **{inn}** for **{disease}**.

Region: {region}
Synonyms: {synonyms}

## Data source coverage

{coverage_text}

## Evidence by category

{evidence_text}

## Full source list (use these source_ids in your output)

{sources_text}

---

**Instructions reminder:**
1. Fill ALL sections of ScientificAgentOutput — especially executive_summary, mechanistic_rationale, existing_evidence, standard_of_care, approved_therapies, clinical_trial_landscape, unmet_medical_need, scientific_risks, and assumptions.
2. For `clinical_trial_landscape`: extract nct_id from ct:* source_ids (e.g., "ct:NCT05604638" → nct_id="NCT05604638"), and fill title, phase, status from the evidence findings.
3. For `approved_therapies`: extract name and regulatory_status from ema:* and fda:* sources.
4. Every claim must reference source_ids from the list above.
5. Return ONLY the JSON object.
