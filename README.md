# pharm-agent

Детерминированный AI-ассистент для фармацевтического анализа: научная обоснованность, коммерческая привлекательность, патентный ландшафт и финансовая жизнеспособность.

## Что это

Система принимает на вход МНН (международное непатентованное наименование) и заболевание, проводит нормализацию и обогащение данных через LLM, запрашивает верификацию у человека, а затем последовательно выполняет научный, рыночный, патентно-финансовый анализ и генерирует итоговый отчёт.

**Текущий статус:** реализован MVP 1 (intake enrichment + human verification) и MVP 2 (scientific agent с коннекторами PubMed, ClinicalTrials.gov, FDA, EMA).

## Быстрый старт

### 1. Клонирование и настройка окружения

```bash
git clone <repo-url>
cd pharm-agent

python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Конфигурация

```bash
cp .env.example .env
```

Откройте `.env` и заполните:

```env
OPENROUTER_API_KEY=sk-or-ваш-ключ
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
DEFAULT_OPENROUTER_MODEL=openai/gpt-4o-mini
```

Опциональные пути (есть значения по умолчанию):

| Переменная | По умолчанию | Описание |
|---|---|---|
| `PDFS_DIR` | `./pdfs` | Директория с PDF-документами |
| `VAULT_DIR` | `./vault` | Obsidian-хранилище заметок |
| `LOGS_DIR` | `./logs` | JSONL audit-логи |
| `DB_PATH` | `./runs.sqlite` | SQLite база данных |

### 3. Подготовка PDF

Положите два PDF-документа в директорию `pdfs/`:

```bash
mkdir -p pdfs
cp /path/to/document1.pdf pdfs/source_1.pdf
cp /path/to/document2.pdf pdfs/source_2.pdf
```

PDF используются как дополнительный контекст для LLM — это могут быть научные статьи, отчёты, обзоры рынка и т.д.

## Использование

### Основной запуск (end-to-end flow)

```bash
.venv/bin/python -m app.cli run \
  --inn "ацетилсалициловая кислота" \
  --disease "ишемический инсульт" \
  --pdf1 ./pdfs/GMPRulesOrderNo916.pdf \
  --pdf2 ./pdfs/PharmaceuticalIndustryDevelopmentStrategy2030.pdf
```

Опциональные параметры:

```bash
.venv/bin/python -m app.cli run \
  --inn "ацетилсалициловая кислота" \
  --disease "ишемический инсульт" \
  --pdf1 ./pdfs/source_1.pdf \
  --pdf2 ./pdfs/source_2.pdf \
  --region "global" \
  --molecule-type "small_molecule" \
  --stage "approved"
```

| Параметр | Описание |
|---|---|
| `--inn` | МНН / INN (обязательный) |
| `--disease` | Заболевание / показание (рекомендуется) |
| `--pdf1`, `--pdf2` | Пути к PDF-документам (обязательные) |
| `--region` | Регион: `global`, `US`, `EU`, `RU`, `custom` |
| `--molecule-type` | Тип молекулы: `small_molecule`, `biologic`, `unknown` |
| `--stage` | Стадия: `idea`, `preclinical`, `phase1`…`phase3`, `approved`, `repurposing` |

### Что происходит при запуске

```
1. Создаётся run, вычисляется хеш входных данных
2. Оба PDF регистрируются с SHA-256 хешами
3. Извлекается текст из PDF (постранично)
4. LLM нормализует и обогащает входные данные (МНН → каноническое название,
   синонимы, ATC-коды, бренды; заболевание → MeSH, ICD, подтипы, биомаркеры)
5. Выводится результат обогащения для проверки человеком
6. Запрашивается решение: approve / reject / needs_revision
7. При approve:
   - Сохраняется решение в SQLite и Obsidian
   - Запускается научный анализ (PubMed, ClinicalTrials.gov, FDA, EMA)
   - Генерируется научное memo в Obsidian
   - Run завершается
8. При reject — сохраняется отказ, run завершается
9. При needs_revision — run переводится в статус ожидания корректировки
```

### Верификация: три варианта решения

При inline-верификации система учитывает уровень полноты данных:

| Completeness | Поведение |
|---|---|
| **high** | Быстрое подтверждение |
| **medium** | Подтверждение + вопросы для уточнения |
| **low** | Предупреждение, рекомендация отправить на revision; при approve — доп. подтверждение |

### Повторный запуск после revision

Если вы выбрали `needs_revision`, используйте команду `revise` с исправленными данными:

```bash
.venv/bin/python -m app.cli revise \
  --run-id "run_2026-05-17T100000000000+0000" \
  --inn "ацетилсалициловая кислота" \
  --disease "ишемический инсульт, NSCLC" \
  --pdf1 ./pdfs/source_1.pdf \
  --pdf2 ./pdfs/source_2.pdf
```

После повторного обогащения система снова запросит верификацию.

### Отдельная верификация

Если вы хотите подтвердить/отклонить run отдельной командой (не inline):

```bash
# Одобрить
.venv/bin/python -m app.cli verify --run-id <RUN_ID> --decision approved

# Отклонить
.venv/bin/python -m app.cli verify --run-id <RUN_ID> --decision rejected

# Отправить на доработку с комментарием
.venv/bin/python -m app.cli verify \
  --run-id <RUN_ID> \
  --decision needs_revision \
  --comment "Уточнить подтип заболевания"
```

### Проверка статуса

```bash
.venv/bin/python -m app.cli status --run-id <RUN_ID>
```

## Архитектура

```
pharm-agent/
├── app/
│   ├── orchestrator.py          # Центральный пайплайн
│   ├── cli.py                   # CLI-интерфейс (Typer)
│   ├── config.py                # Конфигурация (pydantic-settings)
│   ├── agents/
│   │   ├── intake_enrichment_agent.py   # MVP 1: нормализация входных данных
│   │   └── scientific_agent.py          # MVP 2: научный анализ
│   ├── connectors/
│   │   ├── pubmed.py            # PubMed E-utilities
│   │   ├── clinicaltrials.py    # ClinicalTrials.gov API
│   │   ├── fda.py               # openFDA
│   │   └── ema.py               # EMA medicines
│   ├── evidence/
│   │   ├── normalization.py     # Нормализация источников
│   │   ├── ranking.py           # Ранжирование evidence
│   │   └── citations.py         # Формирование цитирований
│   ├── pdf/
│   │   ├── reader.py            # Извлечение текста (PyMuPDF)
│   │   ├── watcher.py           # SHA-256, отслеживание изменений
│   │   └── retrieval.py         # Поиск релевантных чанков
│   ├── llm/
│   │   ├── openrouter_client.py # OpenRouter API wrapper
│   │   └── structured_client.py # Валидация + repair retry
│   ├── schemas/                 # Pydantic-модели
│   ├── storage/
│   │   └── db.py                # SQLite persistence
│   ├── logging/
│   │   └── audit_logger.py      # JSONL + SQLite audit log
│   └── obsidian/
│       └── writer.py            # Obsidian vault writer
├── prompts/                     # Runtime LLM-промпты
├── vault/                       # Obsidian-хранилище (gitignored)
├── pdfs/                        # PDF-документы (gitignored)
├── logs/                        # Audit-логи (gitignored)
└── tests/                       # Тесты (pytest)
```

## Хранение данных

| Слой | Назначение |
|---|---|
| **SQLite** (`runs.sqlite`) | Runs, статусы, PDF-версии, решения, enrichment output, scientific output |
| **JSONL** (`logs/audit.jsonl`) | Append-only audit trail: каждый LLM-вызов, tool call, смена статуса |
| **Obsidian** (`vault/`) | Человекочитаемые заметки: карточки препаратов, заболеваний, run notes, отчёты |

## Статусная модель run

```
created → input_collected → pdfs_registered → pdfs_ingested → intake_enriched
→ awaiting_human_verification
  ├── human_approved → scientific_evidence_collected → scientific_analyzed → completed
  ├── human_rejected → completed
  └── needs_revision → (rerun) → input_collected → ...
```

Любой статус может перейти в `failed` при ошибке.

## MVP Roadmap

| MVP | Описание | Статус |
|---|---|---|
| **MVP 1** | Intake enrichment + human verification | ✅ Реализован |
| **MVP 2** | Scientific agent (PubMed, ClinicalTrials, FDA, EMA) | ✅ Реализован |
| **MVP 3** | Market attractiveness agent | 🔲 Запланирован |
| **MVP 4** | Patent + financial viability agent | 🔲 Запланирован |
| **MVP 5** | Synthesis + QA + final report | 🔲 Запланирован |

## Тесты

```bash
# Запуск тестов
.venv/bin/python -m pytest tests/ -q

# Линтер
.venv/bin/python -m ruff check .

# Форматирование
.venv/bin/python -m ruff format --check .
```

Все внешние зависимости (OpenRouter, PubMed, FDA, EMA) замоканы в тестах — интернет для прогона не нужен.

## Дисклеймеры

> Этот анализ предназначен исключительно для R&D и инвестиционных исследований. Он не является медицинским советом, клиническим руководством или заменой квалифицированной профессиональной проверки.

> Патентный и FTO-анализ (при реализации) будет предварительным и не является юридическим FTO-заключением. Перед принятием бизнес-решений требуется проверка квалифицированным патентным поверенным.
