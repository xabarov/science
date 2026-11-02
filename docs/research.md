# От статьи к исследованию

Ядро и новые команды работают на Python 3.9+ без зависимостей. Существующие карточки
совместимы: дополнительные структуры находятся в `extra.research`.

## Объекты и команды

```sh
python3 science.py note question my-question --title 'Исследовательский вопрос'
python3 science.py note idea my-hypothesis --title 'Проверяемая гипотеза'
python3 science.py note experiment my-test --title 'Минимальный эксперимент'
python3 science.py research-list
python3 science.py claims --id arxiv:2610.39497
python3 science.py claim 'arxiv:2610.39497#c3'
python3 science.py compare --group mopps-v2-table1-r1distill1.5b-aime24
python3 science.py novelty --id idea:domain-shift-audit
```

В question хранить `question`, `success_criteria`, `open_gaps` и ссылки на основания.
В idea хранить утверждения типа hypothesis, ближайшие работы и отличие от них.
В experiment хранить протокол, затем результаты и артефакты. Поле `status` по-прежнему
описывает разбор записи; `extra.research.stage` — стадию исследования.

Все структуры редактируются в record.json; `validate` проверяет формат. `index`
включает исследовательские поля в полнотекстовый индекс. Команды claims/compare/
novelty/impact читают актуальные файлы, а не устаревший поисковый снимок.

## Связи утверждений

Адрес утверждения — `document-id#claim-id`. ID утверждения стабилен: не менять его
из-за косметического редактирования. Для нового утверждения создавать новый ID.

```json
{
  "claim_links": [{
    "from": "idea:domain-shift-audit#h1",
    "to": "arxiv:2610.39497#c3",
    "type": "depends_on",
    "status": "inferred",
    "source": "",
    "locator": "",
    "note": "Гипотеза мотивирована открытым вопросом; результат ещё не получен"
  }],
  "depends_on": ["arxiv:2610.39497#c3"]
}
```

Типы claim_links: supports, contradicts, depends_on, qualifies, tests.
Статусы verified/inferred/hypothesis. from принадлежит текущей записи;
оба утверждения должны существовать локально. Для verified нужны source и locator.
Граф Neo4j экспортирует утверждения и эти связи как отдельные узлы/рёбра.

`depends_on` означает, что изменение основания требует пересмотра вывода.
`cites` и `related` не создают эту зависимость автоматически. Зависимости могут
ссылаться на всю карточку или отдельное утверждение. Циклы зависимостей запрещены;
циклы цитирования допустимы. Семантическую верность связи проверяет исследователь.

## Измерения

`extra.research.measurements` — массив с обязательными полями:

- id, claim (`document#claim`), group (явная группа сопоставления);
- model, train_data, test_data, method, baseline;
- metric, unit, value (конечное число);
- budget (объект), conditions (объект), uncertainty (строка);
- source, locator.

Живой пример: `papers/2025/arxiv-2507.04632/record.json`.
`compare` выводит все условия вместе с числами; фильтры: --group, --metric, --dataset.
Он не усредняет разные эксперименты и не считает причинный эффект автоматически.
Общая group — разметка сопоставимости, не её доказательство. Неизвестное обозначать
`not_checked`, а не нулём. Разницу в бюджете сохранять даже внутри одной группы.

## Проверка новизны

`novelty_checks` хранит объекты с date, provider, query, url, scope, closest (массив
локальных ID), difference и conclusion. Записывать фактически выполненные поиски.
Неполный поиск завершать выводом «новизна не установлена». В карточке идеи уже есть
два запроса и кандидаты CAD/ATLAS, прочитанные пока только на уровне аннотаций.

## Стадии

```text
idea: proposed → literature_checked → planned → running → supported / rejected / inconclusive
question: open → investigating → answered
experiment: planned → running → completed; возможен abandoned
```

Допустим возврат идеи на пересмотр и повторное планирование неопределённого результата.
Все разрешённые переходы находятся в research.STAGES.

```sh
python3 science.py stage idea:my-hypothesis literature_checked --note 'Проверены ближайшие работы, отличие описано'
```

Команда проверяет переход, записывает время и причину в history. Для
literature_checked нужен журнал поиска. Для запуска experiment нужен протокол
с hypothesis, baselines, primary_metric, численным budget `{amount, unit}`,
falsification, точным списком seeds и `ready: true`. Для completed дополнительно
нужны measurements, artifacts и outcome=supported/rejected/inconclusive.
Это проверка полноты записи, а не доказательство фактического выполнения или истинности вывода.

Существующий эксперимент `experiment:auditing-replay` — **план**, ready=false.
Он не запускался и не имеет измерений. Стадия идеи не меняется автоматически
по исходу одного эксперимента: нужно оценить применимость результата.

## Пересмотр зависимых выводов

```sh
python3 science.py impact 'arxiv:2610.39497#c3'
python3 science.py review-queue
python3 science.py review-ack idea:domain-shift-audit --note 'Что проверено и почему вывод сохранён/исправлен'
python3 science.py index
```

impact показывает потенциально затронутые записи. review-queue сравнивает хеши
принятых оснований с текущими, включая версию источника и новые снимки. Пересмотр
распространяется транзитивно. Сначала пересматриваются основания, затем производные
выводы. review-ack фиксирует факт нашего содержательного пересмотра, а не выполняет его.
Исчезнувшие утверждения блокируют validate: исправить ссылку осознанно.
