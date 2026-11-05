# Science — исследовательская картотека

Разборы публикаций, проверяемые утверждения, исследовательские вопросы, гипотезы и
эксперименты. Рабочий язык — русский; оригинальные названия и идентификаторы сохраняются.

Исходные данные — Markdown + JSON. SQLite, поисковые векторы и Neo4j — восстанавливаемые
проекции. Python CLI работает на Python 3.9+ без внешних пакетов. Поиск по смыслу использует
мультиязычную baai/bge-m3 через OpenRouter и отдельный сервер Qdrant.

## Быстрый старт

Для изучения System One: [интерактивный учебник JEV → Jeva](learning/jev-jeva.html).
Скачайте HTML и откройте в браузере: схемы, анимация и семь лабораторий работают
без сервера и интернета. Включены формулы и фрагменты Python. [Путеводитель и границы демонстраций](notes/syntheses/synthesis-system-one-jev-jeva/review.md).

```sh
git clone https://github.com/xabarov/science.git
cd science
python3 science.py validate
python3 science.py index
python3 science.py search 'MoPPS'
python3 science.py list --topic conditional-computation
python3 science.py related arxiv:2610.39497 --depth 2
python3 science.py show arxiv:2610.39497
```

Поиск возвращает пути к заметкам, открываемым в редакторе или Obsidian.
У search/list есть `--json`, фильтры kind/status/year/topic/method/dataset/epistemic.
Обычный запрос ищет все слова; `--fts` включает фразы, OR, NOT и префиксы:

```sh
python3 science.py search 'предиктор* OR predictor*' --fts
```

## Последние разборы

- [Early exit после RecursiveMAS: prior art и выбор идей](notes/syntheses/synthesis-latent-compute-ideas/review.md)
  — RecurTrace, frozen-Ouro halting probe и пересмотр приоритета исходного пилота.
- [RecursiveMAS, v2](papers/2026/arxiv-2604.25917/review.md) — обмен скрытыми
  состояниями между LLM, обучаемые RecursiveLink, повторные раунды и границы результатов.
  [План ранней остановки и смета RunPod](notes/experiments/experiment-recursivemas-early-exit/review.md)
  — эксперимент пока не запущен.
- [DINOv3, v1](papers/2025/arxiv-2508.10104/review.md) — визуальные признаки,
  Gram anchoring, прикладные головы и отличия от DINOv2 и SAM 1/2/3.
- [Hyperagents / DGM-H, v2](papers/2026/arxiv-2603.19461/review.md) — редактируемый
  метаагент, архивный поиск и перенос процедур улучшения между областями.
- [Mid-Harness, v1](papers/2026/arxiv-2609.39982/review.md) — выбор действия
  до исполнения, pairwise verification, дистилляция и decision-only A/B.
- [RRSI, v3](papers/2026/arxiv-2609.24972/review.md) — регуляризация поиска
  агентной обвязки, перенос правок, стоимость и несогласованности результатов в источнике.
- [LimiX-2, v1](papers/2026/arxiv-2609.17488/review.md) — табличная foundation model,
  контекстное предсказание, восстановление пропусков и causal skeleton recovery.
- [Jeva, v2](papers/2026/arxiv-2610.18773/review.md) — архитектура System One,
  типизированные выходы, coverage, кэширование и обучение;
  [интерактивный учебник](learning/jev-jeva.html).
- [CEPO, Harness², RRSI и R²Orch](notes/syntheses/synthesis-prompt-harness-orchestration/review.md)
  — сравнение объектов изменений, обратной связи и требований к проверке.

Глубина чтения и границы проверки указаны в каждой карточке. Численные результаты
последних статей сверены с источниками; benchmark reproduction не выполнялся.

## Исследовательская работа

```sh
python3 science.py note question my-question --title 'Исследовательский вопрос'
python3 science.py note idea my-hypothesis --title 'Проверяемая гипотеза'
python3 science.py note experiment my-test --title 'Минимальный эксперимент'
python3 science.py research-list
python3 science.py claim 'arxiv:2610.39497#c3'
python3 science.py compare --group mopps-v2-table1-r1distill1.5b-aime24
python3 science.py novelty --id idea:domain-shift-audit
python3 science.py impact 'arxiv:2610.39497#c3'
python3 science.py review-queue
```

Для новой статьи: `python3 science.py new ARXIV_ID_OR_DOI --title 'Название'`.
Заготовка inbox не считается разбором. Заполнять record.json и review.md,
затем `validate`, `index`, `check`. Обычный поиск читает последний успешный индекс.

```mermaid
flowchart LR
  P[Статья и версия источника] --> C[Утверждение и условия]
  C --> Q[Исследовательский вопрос]
  C --> H[Гипотеза]
  Q --> H
  H --> N[Проверка новизны]
  N --> E[План эксперимента]
  E --> R[Измерения и вывод]
  R --> Q
```

Связи поддерживают адреса `document#claim`. Зависимости пересматриваются при изменении
основания; научные выводы не меняются автоматически. В источниках и наших идеях
различаются экспериментальные сообщения, теоретические результаты и гипотезы.

## Поиск по смыслу

```sh
cp .env.example .env
chmod 600 .env
# Укажите OPENROUTER_API_KEY в .env через редактор.
docker compose up -d
python3 science.py semantic-index --with-sources
python3 science.py semantic-search 'как обнаружить уверенные ошибки в новом домене' --limit 5
python3 science.py semantic-search 'predictive prompt selection' --hybrid
python3 science.py semantic-status
python3 science.py eval-search --k 3
```

На исходном Mac `.env` уже заполнен, Qdrant запущен и индекс построен.
Фрагменты текстов и поисковые запросы отправляются в OpenRouter для эмбеддингов;
поиск по векторам выполняется в локальном Qdrant. `--with-sources` добавляет
сохранённые первоисточники. Ключ и базы не публикуются. Повторные тексты используют
дисковый кэш; переключение на новую версию индекса атомарно.
[Устройство и эксплуатация](docs/semantic-search.md), [протокол оценки](docs/search-evaluation.json).

## Хранение

```text
papers/<year>/<id>/record.json + review.md
notes/topics/          Карты направлений
notes/questions/       Исследовательские вопросы
notes/ideas/           Гипотезы и проверка новизны
notes/experiments/     Протоколы, измерения, исходы
notes/syntheses/       Сопоставления работ
learning/             Автономные интерактивные учебники HTML
sources/manifest.json Версии, URL и хеши снимков
attachments/          Локальные первоисточники; вне Git
vocab.json            Темы, методы, датасеты и синонимы
research.py           Утверждения, измерения, стадии и пересмотр
preservation.py       Снимки, архивы и восстановление
semantic.py           Индексация Qdrant, поиск и оценка
search_backend.py     OpenRouter, кэш эмбеддингов и Qdrant REST
compose.yaml          Локальный сервер Qdrant
science.py            CLI, SQLite и экспорт графа
```

SQLite, кэши и хранилище Qdrant находятся в data/, выгрузки — в exports/; всё восстанавливается.
Версии одной работы объединяются стабильным ID и aliases. У каждой карточки есть
reading_scope: abstract/partial/full; source_checked не означает воспроизведения.

## Первые исследования

- [Обсуждавшаяся концептуальная работа](papers/2026/arxiv-2610.39497/review.md).
- [Часть экспериментов MoPPS](papers/2025/arxiv-2507.04632/review.md).
- [Вопрос об аудите при смене домена](notes/questions/question-auditing-under-shift/review.md).
- [Гипотеза и первичный поиск новизны](notes/ideas/idea-domain-shift-audit/review.md).
- [План эксперимента — пока не выполнен](notes/experiments/experiment-auditing-replay/review.md).
- [CEPO, Harness², RRSI и R²Orch: сравнение способов улучшать агента](notes/syntheses/synthesis-prompt-harness-orchestration/review.md).

## Сохранность и проверки

```sh
python3 science.py check-sources
python3 science.py backup --destination /path/outside/repository
python3 science.py export-neo4j
python3 -m unittest discover -s tests -v
```

GitHub хранит код и записи. Снимки статей не публикуются; архивы резервируют их
вместе с записями и Git-историей. На исходном Mac установлен ежедневный backup в
23:30; установка на другом Mac — через install_backup.py. Локальный архив на том
же диске не защищает от потери диска; для вложений нужен отдельный носитель.
Полного сервера Neo4j и автоматического ежедневного чтения статей нет.

Документация: [процесс](docs/workflow.md), [формат](docs/format.md),
[исследовательский слой](docs/research.md), [архитектура](docs/architecture.md),
[резервирование](docs/preservation.md), [Neo4j](docs/neo4j.md).
Проверки формата и кода запускаются GitHub Actions при push/PR.
