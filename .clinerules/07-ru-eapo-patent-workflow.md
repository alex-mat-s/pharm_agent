# Russian and Eurasian Patent Workflow Rules

## Purpose

This project includes a patent-analysis layer for preliminary pharmaceutical and biotechnology due diligence. The Russian/Eurasian patent workflow is used to identify potentially relevant patent documents, verify their legal status, connect them to international patent families, and produce a preliminary IP-risk evidence packet.

This is not a legal freedom-to-operate opinion. All outputs must clearly state that automated patent analysis requires review by a qualified patent attorney.

## Scope

Implement Russian and Eurasian patent coverage using the following source hierarchy:

1. Rospatent Open Data / Open API
2. FIPS information search system
3. FIPS Open Registers
4. EAPO Patent Registry and Bulletin
5. EAPATIS, if accessible
6. WIPO PATENTSCOPE
7. Espacenet / EPO OPS
8. Google Patents as discovery fallback
9. Manual expert review flag

Do not rely on unstable HTML scraping as the primary data source. Prefer official APIs, downloadable open data, local cache, and explicit manual-review warnings.

## Input terms

The patent workflow must start from normalized query terms produced by the upstream pipeline:

- INN / МНН
- English INN
- Russian INN
- synonyms
- brand names
- molecular target
- disease / indication
- disease synonyms
- known companies / assignees
- molecule type: small molecule, biologic, antibody, combination, unknown

The connector must expand the query before searching. For example:

- drug name
- brand name
- target name
- indication
- disease subtype
- assignee candidates
- IPC/CPC classes if available

## Required source behavior

### Rospatent / FIPS

Use Rospatent/FIPS sources for Russian patent discovery and legal-status checks.

Collect, when available:

- patent number
- application number
- publication number
- title
- abstract
- claims or claim summary, if available
- applicants
- patent holders
- inventors
- filing date
- priority date
- publication date
- grant date
- legal status
- IPC / CPC / МПК
- source URL
- retrieved_at timestamp
- raw source metadata

### FIPS Open Registers

Use FIPS Open Registers primarily for document-by-number verification and legal-status confirmation.

Do not treat lack of search results as proof of absence of patent risk. Return an explicit warning if registry lookup is unavailable or incomplete.

### EAPO

Use EAPO Registry and Bulletin to identify Eurasian patents and applications that may be relevant to Russia and the Eurasian region.

Collect, when available:

- EA application number
- EA patent number
- title
- applicant
- patent holder
- filing date
- grant date
- publication date
- maintenance / validity status
- countries or territorial status, if available
- source URL

### WIPO / EPO / Google Patents

Use international sources for patent-family discovery, PCT applications, family clustering, priority dates, and cross-jurisdiction validation.

Do not use WIPO website scraping as a required runtime dependency. If WIPO web pages return HTTP 403, log a source availability warning and continue.

Prefer EPO OPS for structured international patent data when credentials are configured.

## Patent evidence schema

Every patent source must be normalized into a structured PatentEvidence object before being used by any LLM.

Required normalized fields:

- source_id
- source_type
- jurisdiction
- document_number
- application_number
- publication_number
- title
- abstract
- applicants
- patent_holders
- inventors
- filing_date
- priority_date
- publication_date
- grant_date
- legal_status
- ipc_codes
- cpc_codes
- patent_type
- relevance_reason
- blocking_risk_preliminary
- source_url
- retrieved_at
- raw_metadata
- warnings

Do not pass raw HTML directly into LLM synthesis.

## Patent-type classification

Classify each relevant patent into one or more of the following categories:

- composition_of_matter
- antibody_or_biologic_sequence
- salt_polymorph_or_crystal_form
- formulation
- method_of_manufacture
- method_of_treatment_or_indication
- dosing_regimen
- combination_therapy
- biomarker_defined_subgroup
- delivery_device
- process_or_intermediate
- unknown

Blocking-risk assessment must be preliminary and conservative.

Use:

- high
- medium
- low
- unknown

Never state that the development path is legally free unless a human patent expert has confirmed it.

## Legal-status handling

For every potentially relevant RU or EA document, attempt to verify legal status.

Legal status values:

- active
- expired
- lapsed
- terminated
- pending
- withdrawn
- rejected
- unknown

If legal status cannot be verified, set legal_status = "unknown" and add a warning.

## Patent-family clustering

Deduplicate and cluster results by:

- priority number
- publication number
- application number
- patent family ID, if available
- title similarity
- applicant / assignee similarity
- INPADOC family, if available

Output PatentFamilyEvidence where possible.

## Fallback logic

The patent workflow must not fail the whole benchmark run if one source is unavailable.

Fallback order:

1. Try Rospatent Open API / Open Data
2. Try local Rospatent open data cache
3. Try FIPS search or registers
4. Try EAPO registry / bulletin
5. Try EPO OPS if credentials are configured
6. Try WIPO / Google Patents as discovery fallbacks
7. Return patent_source_unavailable warning and require manual review

Every unavailable source must be logged explicitly.

## Logging requirements

For every patent-source request, log:

- run_id
- connector name
- source name
- endpoint or URL
- query terms
- HTTP method
- status code
- latency
- result count
- warnings
- errors
- retrieved_at

Never log API keys, session cookies, credentials, or secrets.

## Environment variables

Use these environment variables when relevant:

ROSPATENT_BASE_URL=https://online.rospatent.gov.ru
ROSPATENT_API_KEY=
FIPS_BASE_URL=https://www.fips.ru
FIPS_REGISTERS_BASE_URL=https://www.fips.ru/registers-web
EAPO_BASE_URL=https://www.eapo.org
EAPO_REGISTRY_URL=https://www.eapo.org/pubservices/info/registry/inventions/patents
EPO_OPS_CONSUMER_KEY=
EPO_OPS_CONSUMER_SECRET=
WIPO_PATENTSCOPE_BASE_URL=https://patentscope.wipo.int
GOOGLE_PATENTS_BASE_URL=https://patents.google.com
PATENT_CACHE_DIR=./data/cache/patents
RUSSIAN_PATENT_CACHE_DIR=./data/cache/patents/ru
EAPO_PATENT_CACHE_DIR=./data/cache/patents/eapo

## Output disclaimer

Every patent report or memo must include:

"This automated patent analysis is preliminary and does not constitute a legal freedom-to-operate opinion. The results must be reviewed by a qualified patent attorney before any development, licensing, or commercialization decision."

Russian version:

"Данный автоматизированный патентный анализ является предварительным и не является юридическим заключением о свободе действий. Результаты должны быть проверены квалифицированным патентным поверенным до принятия решений о разработке, лицензировании или коммерциализации."
