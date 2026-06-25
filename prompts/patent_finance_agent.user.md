Analyze the patent landscape and financial viability for the following drug-indication pair.

Drug (INN): {inn_preferred}
English INN: {inn_english}
Synonyms: {inn_synonyms}
Disease / Indication: {disease_preferred}
Disease synonyms: {disease_synonyms}
Region: {region}
Molecule type: {molecule_type}
Development stage: {stage}

Scientific context from prior analysis:
<scientific_context>
Summary: {scientific_summary}
Mechanism of action: {mechanism_of_action}
Approved therapies: {approved_therapies}
</scientific_context>

Market context from prior analysis:
<market_context>
Summary: {market_summary}
Competitors: {competitors}
Market size estimate: {market_size_estimate}
</market_context>

Available evidence from source searches:
<evidence>
{evidence_context}
</evidence>

PDF documents provided for analysis (may contain patent documents, financial reports, due diligence materials):
<pdf_documents>
{pdf_documents}
</pdf_documents>

Return a JSON object matching the PatentFinanceAgentOutput schema.

Focus on:
1. Patent landscape: Are there blocking patents? What patent types are relevant? Who are the main assignees?
2. Freedom to operate (FTO): What are the key IP risks? What mitigation strategies exist?
3. Patent fence opportunities: How can market exclusivity be extended?
4. Investment scenarios: What are realistic low/base/high investment ranges for this molecule and indication?
5. Cost structure: What are the major development cost buckets?
6. Money timeline: When can value be realized (licensing, partnering, approval, revenue)?
7. Financial risks: What are the key development, regulatory, market, and competitive risks?

Remember:
- This is a preliminary analysis requiring legal and financial expert review.
- Always set legal_review_required: true.
- Flag missing patent data sources (Orange Book, Purple Book, EPO OPS) in missing_information.
- Use investment ranges, not precise numbers.
- Clearly separate known patents (with source_ids) from hypothetical risks and opportunities.
- If PDF documents contain relevant patent or financial data, cite them with source_ids like "pdf:source_1:p5".
