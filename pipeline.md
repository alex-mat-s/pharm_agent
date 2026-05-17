## Вопросы
1. PDF - необязательный источник - просто доп знания - директория с доп. знанинями, которую можно насыщать - LLM Wiki
2. Еще раз уточнить формат финального отчета - главная задача: сделать MVP системы для анализа рынка, на основе этой системы ручками собрать кейсы - голдены, на основе голденов генерировать синтетику ? Может, имеет смысл выводить как можно больше информации по анализу - да
3. Первый шаг - нормализация, насыщаем наш сырой input - МНН + заболевание, потом с помощью словарей и LLM насыщаем, потом просим человека проверить - проверку делать обязательно или если только установлен флаг неполноты? - обязательный шаг - верификация человеком
4. PDF - обрабатывать не с помощью RAG, а использовать мультимодальную модель(?) - либо OCR? - целую pdf в контекст мультимодальной модели

## Гипотезы
1. Добавить LLM Wiki как базу (своего рода graph retriever) 
2. Чтобы не изобретать велосипед, в качестве графической оболочки можно взять Obsidian
3. Детерминированный пайплайн с агентами-исследователями внутри, где каждый этап имеет строгий JSON-выход, список источников, human gate и воспроизводимые логи

## Общая идея системы
### Input:
МНН + заболевание/показание, опционально регион, тип молекулы, стадия разработки, целевой рынок

### Output:
Единое заключение по трём направлениям:
- Научная обоснованность / clinical-scientific rationale: есть ли биологическая и клиническая логика: механизм действия, доказательная база, стандарты лечения, неудовлетворённая потребность, конкурирующие препараты и клинические испытания
- Коммерческая привлекательность / market attractiveness: размер сегмента, конкуренты, цены, плательщики, драйверы рынка, вероятность дифференциации
- Финансовая жизнеспособность + IP / financial & patent viability: путь к деньгам, патентные риски, свобода действий, варианты "патентного забора", ориентировочный диапазон инвестиций

Критически важно: финальный вывод должен быть не "оценкой по шкале", а текстом с цитируемыми источниками, допущениями и зонами неопределённости.


  00-project.md
  01-architecture.md
  02-pharma-agent-behavior.md
  03-logging.md
  04-obsidian-kb.md
  05-testing.md
  06-security.md

## Архитектура
```bash
pharm-agent
|-- app
|   |-- orchestrator.py
|   |-- agents
|   |   |-- intake_enrichment_agent.py
|   |   |-- scientific_agent.py
|   |   |-- market_agent.py
|   |   |-- patent_finance_agent.py
|   |   |-- synthesis_agent.py
|   |   |-- qa_agent.py
|   |-- tools
|   |   |-- web_search.py
|   |   |-- clinicaltrials.py
|   |   |-- pubmed.py
|   |   |-- fda.py
|   |   |-- ema.py
|   |   |-- patents.py
|   |   |-- pdf_reader.py
|   |   |-- obsidian.py
|   |   |-- pricing.py
|   |-- schemas
|   |   |-- input.py
|   |   |-- evidence.py
|   |   |-- outputs.py
|   |-- storage
|   |   |-- db.py
|   |   |-- vector_index.py
|   |   |-- audit_log.py
|-- vault                  # Obsidian vault
|   |-- 00_inputs
|   |-- 01_entities
|   |-- 02_sources
|   |-- 03_runs
|   |-- 04_reports
|   |-- 05_decisions
|   |-- 99_templates
|-- pdfs
|   |-- source_1.pdf
|   |-- source_2.pdf
|-- logs
|-- .clinerules
```

## Роли

- Markdown в Obsidian — читаемая база знаний: карточки препаратов, заболеваний, патентов, компаний, запусков анализа.
- SQLite/Postgres — структурное хранилище: запуски, статусы, версии input, источники, JSON-выходы агентов.
- Vector index — поиск по PDF, заметкам и web evidence.
- Audit logs — полный журнал: каждый prompt, model, tool call, ответ LLM, стоимость, latency, source IDs.

## Источники данных
### Обязательные источники
Для научного и клинического анализа:
- PubMed / NCBI E-utilities — публичный API для Entrez, включая PubMed и PMC. 
- ClinicalTrials.gov API — для поиска клинических исследований по заболеванию, intervention, phase, sponsor, статусу. 
- FDA Drugs@FDA / openFDA — Drugs@FDA включает большинство препаратов, одобренных FDA с 1939 года, а openFDA даёт машиночитаемый JSON-доступ и обновляется по будням. - пока удалера точка доступа, необходимо обращение через api                                                                                                                                                                           
- EMA medicines / EPAR — EMA публикует данные по лекарствам в табличном виде, обновляемые overnight, а EPAR содержит публичные научные assessment reports по централизованно оцененным препаратам. 

Для рынка и конкурентов:
- FDA / EMA approvals.
- ClinicalTrials.gov pipeline.
-WHO ATC/DDD для классификации и drug-utilization логики; WHO описывает ATC/DDD как gold standard для drug utilization monitoring and research. 
- Публичные годовые отчёты компаний, investor presentations, SEC filings.
- Прайсинг: Red Book, GoodRx, CMS, IQVIA/Clarivate/Evaluate (часть из этого будет платной, поэтому для MVP можно явно отделить "public pricing proxy" от "licensed market data").


Для патентов:
- FDA Orange Book — содержит downloadable data files; в Products.txt есть active ingredient, trade name, applicant, NDA/ANDA type и другие поля. 
- FDA Purple Book — база FDA-licensed biological products, включая biosimilar/interchangeable и reference products. 
- EPO OPS — RESTful web service к данным EPO, включая bibliographic, legal event, full-text и image databases. 
- USPTO Open Data Portal / Patent Assignment Search — для assignment и патентных данных. 
- WIPO PATENTSCOPE / Google Patents / The Lens — как дополнительные поисковые источники.

Важно: агент может сделать patent landscape, но не должен выдавать это как **юридическое FTO-заключение**. Финальный отчёт должен явно маркировать: "предварительный AI-assisted patent landscape; требуется проверка патентным поверенным".

## Пайплайн агентской системы
### Step 0. Intake, нормализация и насыщение input
Это обязательный этап перед любым анализом.

**Input**
(?) что означает каждое поле
```json
{  
  "inn_raw": "ацетилсалициловая кислота",  
  "disease_raw": "инсульт",  
  "region": "global|US|EU|RU|custom",  
  "molecule_type": "unknown",  
  "stage": "idea|preclinical|phase1|phase2|phase3|approved|repurposing",
  "pdf_pack_id": "default"
}
```
**Что делает агент**
1. Нормализует МНН (пояснение к каждому полю):
- русское название;
- английское INN;
- синонимы;
- CAS;
- PubChem CID;
- ATC code;
- brand names;
- molecular target / mechanism if known.

2. Нормализует заболевание (пояснение к каждому полю):
- canonical disease name;
- MeSH;
- ICD-10/ICD-11;
- SNOMED, если доступно;
- подтипы заболевания;
- биомаркеры;
- patient segmentation.

3. Проверяет неоднозначности:
- МНН может иметь разные соли/формы;
- заболевание может быть слишком широким;
- indication может отличаться от disease;
- один препарат может использоваться в разных линиях терапии.

4. Извлекает релевантные фрагменты из двух PDF.

5. Формирует Intake Verification Packet для человека.

6. Human verification: обязательная проверка

Причина:  в фарме ошибка на этом этапе ломает весь downstream-анализ Например, "рак лёгкого" без уточнения NSCLC/SCLC, линии терапии и мутационного статуса — это слишком широкий рынок. То же самое с МНН: соль, форма, биологический препарат, комбинация или repurposing могут полностью менять патенты, рынок и клинические данные.

Флаг неполноты можно использовать следующим образом:
- completeness = high   -> человек быстро подтверждает карточку
- completeness = medium -> человек подтверждает + отвечает на 2–3 вопроса
- completeness = low    -> система блокирует запуск до уточнения

**Output**

```json
{  
  "normalized_inn": {    
    "preferred_name": "...",    
    "english_inn": "...",    
    "synonyms": [],    
    "cas": null,    
    "atc": [],    
    "brand_names": [],    
    "molecule_type": "small_molecule|biologic|unknown"  
  },  
  "normalized_disease": {    
    "preferred_name": "...",    
    "mesh": [],    
    "icd": [],    
    "subtypes": [],    
    "biomarkers": [],    
    "target_population_hypothesis": "..."  
  },  
  "ambiguities": [],  
  "human_questions": [],  
  "pdf_relevant_sections": [],  
  "verification_status": "pending|approved|rejected|needs_revision"
}
```

### Step 1. Scientific & clinical landscape agent
Этот агент отвечает на вопрос: есть ли научный и клинический смысл разрабатывать этот препарат для этого заболевания?

**Input**
- подтверждённый человеком normalized input;
- релевантные фрагменты PDF;
- карточки из Obsidian;
- свежие web/API данные.

**Что ищет**

- Механизм действия
- Как МНН действует?
- Есть ли связь механизма с патогенезом заболевания?
- Есть ли biomarker-defined subgroup?

**Доказательная база**
- PubMed evidence;
- систематические обзоры;
- RCT;
- real-world evidence;
- негативные исследования;
- safety signals.

**Текущие стандарты лечения**
- guidelines;
- FDA/EMA labels;
- clinical practice;
- gold standard;
- unmet need.

**Что есть на рынке**
- approved drugs;
- off-label use;
- generics;
- biologics/biosimilars;
- сильные и слабые стороны текущих препаратов.

**Что скоро появится**
- clinical trials phase 1/2/3;
- компании-спонсоры;
- сроки primary completion;
- конкуренты, которые на 2–3 года впереди.

**Output**
```json
{  
  "scientific_summary": "...",  
  "mechanism_rationale": "...",  
  "current_standard_of_care": "...",  
  "approved_therapies": [],  
  "clinical_pipeline": [],  
  "unmet_need": "...",  
  "major_risks": [],  
  "evidence_gaps": [],  
  "sources": []
}
```

### Step 2. Market attractiveness agent
Этот агент получает:
- МНН;
- заболевание;
- выводы scientific agent;
- список конкурентов;
- текущий стандарт лечения;
- данные по trials и approvals.

Главный вопрос: есть ли спрос и можно ли занять коммерчески значимый сегмент?

Что анализирует

**Размер рынка**
- prevalence;
- incidence;
- diagnosed population;
- treated population;
- eligible population;
- addressable population.

**Сегменты пациентов**
- все пациенты;
- diagnosed;
- treated;
- refractory;
- biomarker-positive;
- конкретная линия терапии;
- geography-specific access.

**Динамика рынка**
- старение населения;
- улучшение диагностики;
- новые guidelines;
- patent cliffs;
- появление generics/biosimilars;
- новые классы терапии.

**Плательщики**
- physician value;
- patient value;
- payer value;
- health-economic argument;
- госпитализации, осложнения, adherence.

**Цена и конкурентная логика**
- цена текущей терапии;
- frequency of administration;
- route of administration;
- monitoring burden;
- safety burden;
- willingness to pay.

**Output**
```json
{  
  "market_summary": "...",  
  "patient_population": {    
    "global": null,    
    "us": null,    
    "eu": null,    
    "target_segment": "..."  
  },  
  "market_dynamics": [],  
  "payer_value": "...",  
  "pricing_logic": "...",  
  "competitor_price_benchmarks": [],  
  "commercial_risks": [],  
  "sources": []
}
```

### Step 3. Patent + financial viability agent
Этот агент получает:
- normalized input;
- disease / indication;
- scientific выводы;
- market выводы;
- PDF;
- список конкурентов;
- known brands;
- known sponsors;
- active ingredient / formulation / indication.

Главные вопросы:
- Свободен ли путь для разработки и коммерции?
- Как можно продлить монополию?
- Сколько денег нужно вложить?
- Когда мы можем получить деньги?

Patent landscape

Ищем патенты на:
- composition of matter — само действующее вещество;
- salt / polymorph / crystal form;
- method of manufacture / synthesis;
- formulation;
- route of administration;
- method of treatment / indication;
- combination therapy;
- d*osing regimen;
- patient subgroup / biomarker;
- device or delivery system.

**Output**
```json
{  
  "patent_landscape_summary": "...",  
  "blocking_patent_candidates": [],  
  "patent_count_by_family": {},  
  "main_assignees": [],  
  "earliest_priority_dates": [],  
  "expected_expirations": [],  
  "freedom_to_operate_risks": [],  
  "patent_fence_opportunities": [],  
  "generic_or_biosimilar_risk": "...",  
  "legal_review_required": true
}
```

Financial viability: вместо одной "магической суммы" агент должен строить диапазон по сценариям:
```json
{  
  "investment_range": {    
    "low_case": {      
      "amount_usd": "...",      
      "assumptions": []    
    },    
    "base_case": {      
      "amount_usd": "...",      
      "assumptions": []    
    },    
    "high_case": {      
      "amount_usd": "...",      
      "assumptions": []    
    }  
  },  
  "major_cost_buckets": [    
    "preclinical",    
    "CMC",    
    "phase_1",    
    "phase_2",    
    "phase_3",    
    "regulatory",    
    "market_access",    
    "patent/legal"  
  ],  
  "money_timeline": {    
    "earliest_value_inflection": "...",    
    "licensing_window": "...",    
    "approval_window": "...",    
    "revenue_window": "..."  
  },  
  "key_financial_risks": []
}
```
Важно: "когда мы получим деньги?" нужно отвечать не одним числом, а сценариями:
- ранняя монетизация: лицензирование после preclinical / phase 1 / phase 2;
- партнёрство: co-development;
- полный путь: регистрация и продажи;
- repurposing: потенциально быстрее;
- generic / 505(b)(2) / hybrid route: зависит от региона и регуляторного пути;
- новая молекула: дольше и дороже.

### Step 4. Synthesis + QA agent
Финальный агент не ищет новые данные, а проверяет консистентность. Он должен:
- найти противоречия между агентами;
- проверить, что каждый важный вывод имеет источник;
- отделить факты от гипотез;
- вынести "critical unknowns";
- сформировать финальный отчет.

**Финальный отчёт**
1. Executive summary
2. Входные данные и нормализация
3. Научная обоснованность
4. Текущий стандарт лечения и конкуренты
5. Pipeline competitors
6. Коммерческая привлекательность
7. Плательщики и цена
8. Патенты и FTO-риск
9. Стратегия патентного забора
10. Финансовая жизнеспособность
11. Когда можно получить деньги
12. Что нужно проверить человеком
13. Источники
14. Приложения

## Obsidian vault как база знаний
**Структура**
```bash
vault
|-- 00_inputs
|   |-- 2026-05-11_aspirin_stroke_input.md
|-- 01_entities
|   |-- drugs
|   |   |-- acetylsalicylic-acid.md
|   |-- diseases
|   |   |-- ischemic-stroke.md
|   |-- companies
|   |   |-- bayer.md
|   |-- targets
|       |-- cox-1.md
|-- 02_sources
|   |-- pdfs
|   |   |-- source_1.md
|   |   |-- source_2.md
|   |-- pubmed
|   |   |-- PMID_123456.md
|   |-- clinicaltrials
|   |   |-- NCT_00000000.md
|   |-- patents
|       |-- US1234567.md
|-- 03_runs
|   |-- 2026-05-11_run_001.md
|-- 04_reports
|   |-- acetylsalicylic-acid_ischemic-stroke_report.md
|-- 05_decisions
|   |-- human_verification_run_001.md
|-- 99_templates
    |-- drug_template.md
    |-- disease_template.md
    |-- patent_template.md
    |-- run_template.md
```

Пример карточки препарата

```
---
type: drug
preferred_name: acetylsalicylic acid
inn_ru: ацетилсалициловая кислота
synonyms:  
  - aspirin
atc:  
  - B01AC06
molecule_type: small_molecule
last_updated: 2026-05-11
source_runs:  
  - run_001  
---

# Acetylsalicylic acid

## Mechanism

## Approved indications

## Known brands

## Safety

## Patent notes

## Linked diseases
  - [[ischemic-stroke]]
```

Пример карточки запуска

```
---
type: analysis_run
run_id: run_001
inn: acetylsalicylic acid
disease: ischemic stroke
status: completed
input_hash: ...
pdf_hashes:
  source_1.pdf: ...  
  source_2.pdf: ...
created_at: 2026-05-11T10:00:00+02:00
---

# Run run_001

## Human-verified input

## Scientific agent output

## Market agent output

## Patent-finance agent output

## Final synthesis

## Open questions
```

## Работа с PDF
Требования к PDF-пайплайну:
- File watcher
- следит за /pdfs;
- считает SHA-256 hash (если hash изменился — запускает re-ingestion);
- обновляет source_1.md, source_2.md;
- помечает все связанные runs как potentially stale.

**PDF ingestion**
- извлечение текста;
- извлечение таблиц;
- извлечение изображений;
- page-level citation;
- chunking по смысловым секциям;
- multimodal model для сложных страниц, таблиц, диаграмм, сканов.

**Versioning**
- каждый PDF получает pdf_id;
- каждая версия получает content_hash;
- agent output всегда хранит, какую версию PDF он видел.

```json
{  
  "pdf_id": "source_1",  
  "filename": "source_1.pdf",  
  "sha256": "...",  
  "pages": 42,  
  "ingested_at": "2026-05-11T10:00:00+02:00",  
  "chunks": []
}

```

## Логгирование
Логи должны быть не "print в консоль", а полноценный audit trail.

Обязательное логгирование:
```json
{  
  "run_id": "run_001",  
  "step": "scientific_agent",  
  "event_type": "llm_call",  
  "timestamp": "...",  
  "model": "openrouter/model-name",  
  "messages": [],  
  "tools_available": [],  
  "tool_calls": [],  
  "raw_response": {},  
  "parsed_response": {},  
  "input_tokens": 1234,  
  "output_tokens": 567,  
  "cost_usd": 0.12,  
  "latency_ms": 9000,  
  "source_refs": [],  
  "errors": []
}
```

Уровни логгирования:
- Audit log — полный JSONL, append-only.
- Debug log — технические ошибки, retries, parsing failures.
- Human-readable run log — Markdown в Obsidian: что делал агент, какие источники нашёл, какие выводы сделал.

## Human-in-the-loop
3 обязательных human gates:
- Gate 1 — после нормализации input. Человек подтверждает:
  - правильный МНН;
  - правильное заболевание;
  - правильный subtype;
  - регион;
  - patient segment;
  - molecule/formulation;
  - что два PDF прочитаны корректно.
- Gate 2 — перед patent/finance stage. Человек подтверждает, что scientific + market выводы не ушли не туда. Например, подтвердите, что целевой сегмент: adult patients with X after failure of Y.
- Gate 3 — перед финальным отчётом. Человек видит:
  - top-10 ключевых выводов; 
  - top-10 источников;
  - unresolved questions;
  - warnings.

## Как формировать "когда мы получим деньги?"
Отдельным блоком в financial agent.

Монетизация возможна в 4 окнах:
1. После подтверждения механизма / preclinical package
2. После Phase 1 safety
3. После Phase 2 proof-of-concept
4. После approval / launch

Для каждого окна агент должен указать:
- что должно быть доказано;
- кому это можно продать/лицензировать;
- почему это окно реалистично;
- какие риски;
- какие данные нужны для следующего шага.

## Минимальный MVP-план

### MVP 1 — deterministic skeleton
- CLI или простая web-форма.
- Input: МНН + заболевание + два PDF.
- PDF hash watcher.
- Obsidian vault writer.
- SQLite для runs.
- JSONL audit logs.
- OpenRouter client.
- Structured outputs.
- Human verification через terminal/web form.

### MVP 2 — scientific agent
- PubMed search.
- ClinicalTrials.gov search.
- FDA / EMA lookup.
- PDF retrieval.
- Scientific memo.

### MVP 3 — market agent
- competitor extraction;
- treatment landscape;
- prevalence/incidence source collection;
- payer/value narrative;
- pricing proxy.

### MVP 4 — patent/finance agent
- Orange Book / Purple Book;
- EPO OPS / patent search;
- patent family clustering;
- rough investment model;
-financial scenarios.

### MVP 5 — QA + report generation
- source coverage check;
- contradiction check;
- stale PDF check;
- final Markdown report;
- export to PDF later, если понадобится.

## Логика оркестратора
```python
def run_analysis(raw_input):
  run = create_run(raw_input)    
  pdf_status = ingest_or_update_pdfs(run.pdf_pack)    
  enriched = intake_enrichment_agent.run(        
    raw_input=raw_input,        
    pdf_context=pdf_status.relevant_chunks    
  )    
  save_to_obsidian(enriched)    
  human_decision = request_human_verification(enriched)    
  if not human_decision.approved:        
    stop_run("Input not approved")    
  scientific = scientific_agent.run(        
    normalized_input=human_decision.normalized_input,        
    pdf_context=pdf_status.relevant_chunks    
  )    
  save_to_obsidian(scientific)    
  market = market_agent.run(        
    normalized_input=human_decision.normalized_input,
    scientific_context=scientific    
  )    
  save_to_obsidian(market)    
  patent_finance = patent_finance_agent.run(        
    normalized_input=human_decision.normalized_input,
    scientific_context=scientific,        
    market_context=market,        
    pdf_context=pdf_status.relevant_chunks    
  )    
  save_to_obsidian(patent_finance)    
  final = synthesis_agent.run(        
    normalized_input=human_decision.normalized_input,
    scientific=scientific,        
    market=market,        
    patent_finance=patent_finance    
  )    
  qa = qa_agent.run(final)    
  save_final_report(final, qa)    
  return final
```