# RRSI: Regularized Recursive Self-Improvement of Agent Harnesses

ID: `arxiv:2609.24972`. Авторы Peng Xia и соавторы.
Исходная публикация 21.09.2026; рассмотрена v3 от 04.10.2026, разбор 03.11.2026.
[Статья v3](https://arxiv.org/html/2609.24972v3),
[код](https://github.com/google-research/rrsi),
[проект и explorer траекторий](https://regularized-rsi.com/).

## Глубина проверки

`reading_scope=partial`: Sections 2–4, Tables 1–7, выбранные Appendices B, D.3,
E.1–E.2, F, README, selection.py и schedule.py на commit
`be50316e1db05914068a973f322770ef08ed7ba1`. Снимки HTML и selection.py сохранены
с хешами; полный чужой текст не публикуется. Полного аудита всех prompts и кода,
воспроизведения benchmark/search и измерения полной стоимости не было.

## Вопрос статьи

Как улучшать обвязку замороженной LLM, чтобы правки переносились на новые задачи,
а многократное использование одного evolve set не превращалось в подгонку под него?
Harness включает prompts, control flow, tools, memory, skills, subagents и context
management. Оптимизируются файлы и поведение системы, веса policy остаются фиксированы.

RRSI регулирует траекторию поиска по открытому пространству изменений: сколько
правок предложить, какие механизмы исследовать и что принять на основании конечных,
шумных измерений. Это regularized harness search на доступном scored evolve set.

## Один раунд

1. Запустить текущий harness, собрать траектории, score и policy-token cost.
2. Предложить кандидатов с ограничением числа независимо атрибутируемых правок.
3. Проверить diff critic-ом на benchmark-specific content и бесполезную механику.
4. Оценить прошедшие варианты на evolve set.
5. Применить performance floor, cost rule и domain guards; среди допустимых выбрать
   максимальный score, иначе сохранить incumbent.
6. Записать component, hypothesis, diff, score/cost delta и verdict в историю.
   Использовать её в следующих раундах для exploration и pruning proposals.

В коде кандидаты изолируются git worktrees. Принятый harness фиксируется commit-ом.
Это способ воспроизводимо сохранять изменения, а не дополнительный результат качества.

## Proposal-side regularization

Бюджет атомарных правок уменьшается по cosine schedule:

`b_t = ceil(b_min + (b_max-b_min)*(1+cos(pi*t/T))/2)`.

Независимые изменения легче атрибутировать, чем пакет несвязанных переписей.
История сохраняет и отрицательные результаты, чтобы не предлагать ту же
опровергнутую идею снова. Если продвижение в окне последних раундов остаётся в
пределах noise band, часть предложения резервируется для ещё не затронутых
компонентов. Неиспользованность компонента — технический критерий exploration,
а не доказательство научной новизны.

## Selector и аналогии с L0/L1/L2

Critic до оценивания читает именно shipped diff и отсеивает явные имена задач,
ответы, специфичные значения и логику под evolve benchmark. Это эвристический
контроль утечки, а не доказательство её невозможности.

Noise band delta оценивается по исходной системе. Performance floor:

`S(candidate) >= S_best_so_far - delta`.

Он ограничивает накопление последовательных малых регрессий. Для кандидата с
`DeltaS > delta` рост относительной стоимости должен удовлетворять:

`DeltaC <= beta0 + beta1*DeltaS`.

В noise band применяется другая ветка:

`w_s*DeltaS - w_c*DeltaC + w_n*novelty > 0`,

после того же floor и domain guards. Значит, правило не сводится к «принимать
только score gains больше delta»: внутри band допускается полезное снижение
стоимости или структурное exploration. Novelty считает новые для accepted path
типы client_tool/skill/memory/subagent, не любую текстовую правку. Это сверено с
[selection.py на pinned commit](https://github.com/google-research/rrsi/blob/be50316e1db05914068a973f322770ef08ed7ba1/rrsi/selection.py).

Pruning помечает компоненты без положительного измеренного вклада за окно как
цели удаления в следующих предложениях. Удаление проходит общий цикл проверки.
Бюджет правок похож на L0, удаление компонентов — на L1, ограничение footprint —
на L2. Это функциональные аналогии; loss со штрафом нормы весов не оптимизируется.

## Эксперименты и основные результаты

Основной policy, proposer, analyst и critic — Claude Opus 4.8 с фиксированными
весами. Три исходных harness: Terminus-2 для coding; ReAct/MCP, dynamic toolbelt,
ReSum-style context management для workspace и engineering. Baselines начинают
с того же H0, используют тот же evolve set и candidate budget.

Восемь benchmarks в трёх доменах: Terminal-Bench 2.1 → SWE-bench Verified;
Harvey LAB → ID held-out LAB и JobBench/GDPval/APEX-Agents;
EngDesign → Frontier-Eng. OOD benchmarks не участвуют в поиске и отборе правок.

Workspace ablation (Table 2), policy Claude Opus 4.8:

| Вариант | LAB evolve score | OOD average | Policy tokens/trial, млн |
| --- | ---: | ---: | ---: |
| H0 | 89.4 | 39.7 | 1.56 |
| Unregularized evolution | 92.8 | 40.3 | 3.80 |
| RRSI | 90.5 | 43.6 | 2.42 |

OOD average — среднее опубликованных scores JobBench, GDPval и APEX-Agents,
не pooled accuracy всех задач. Таблица показывает меньшую подгонку evolve set
и лучший перенос. 3.80→2.42 — около 36.3% снижения executor tokens относительно
unregularized, но RRSI дороже H0. Это стоимость выполнения финальной обвязки;
полная стоимость proposer/critic/search и wall time этим числом не измеряются.

Конкретный OOD перенос workspace harness (Table 1): JobBench 36.0→40.7,
GDPval 48.8→52.3, APEX-Agents 34.2→37.9. Harvey LAB ID held-out: 86.9→89.2.
LAB evolve — 120 задач, ID held-out — 40; основной workspace search 20 раундов,
2 кандидата/раунд, 2 trials/task (Appendices B, E.1).

В coding (Table 3): Claude Opus 4.8 даёт Terminal-Bench 74.2→80.2 и
SWE-bench Verified 82.0→83.8. Отдельный поиск с Gemini 3.5 Flash:
64.6→78.7 и 76.8→79.0 соответственно. Перенос найденного Gemini harness на
невиданную при поиске Gemini 3.1 Flash Lite даёт 11.2→14.6 на Terminal-Bench
(Table 4). Это отдельные модели и постановки, не один объединённый результат.

Авторы сообщают task-paired bootstrap из 10000 resamples (Table 6) и второе
независимое повторение поиска (Table 7). Workspace OOD average — 43.6 и 43.7
в двух поисках. Пары trials одной задачи не считаются независимыми при bootstrap.
Два запуска поддерживают устойчивость в проверенной настройке, но не устанавливают
распределение исходов длительной эволюции.

## Что дают качественные примеры

Appendix F показывает принятую bounded pre-completion verification audit и
небольшой recovery hint для workdir error. Более дорогой кандидат с небольшим
score gain отклоняется; экономия токенов не спасает вариант ниже performance floor.
У workspace RRSI принял лишь несколько правок с проверкой готового deliverable:
полезный механизм может быть процедурой read-back/verification без привязки
к лексике конкретной rubric. Это авторские случаи, не наша реализация.

## Несогласованности источника

- HF-аннотация показывает up to 14.1 evolve / 4.7 OOD points. В аннотации v3 —
  6.0 / 4.3. При этом Table 3 v3 сохраняет +14.1 для Gemini; +6.0 относится к
  основному Claude run. Table 1 и Table 6 v3 содержат JobBench +4.7, так что
  даже внутри v3 abstract не согласован со всеми таблицами. Используем конкретные
  model/dataset/table, а не общий рекламный максимум.
- Аннотация говорит о 30% меньших policy tokens, проект — 36%; Table 2 даёт
  3.80M→2.42M, то есть 36.3% при прямом вычислении. Не заменяем это полной
  стоимостью обучения/поиска или экономией относительно H0.
- Appendix B.5 называет 185 GDPval tasks и 204 comparisons per judge; без логов
  точный denominator/число сравнений требует уточнения. В этой карточке сохранены
  опубликованные score и CI, а спорное число задач не используется как основание.
- Для schedule ceil при формульном t=0..T−1 минимум b_min=1 может быть достигнут
  только на границе t=T; фактические индексы round и журнал нужны для проверки
  описания «final-round budget». Само по себе имя b_min не доказывает, что последний
  реально выполняемый раунд имеет ровно одну правку.

## Ограничения и связь с Harness²

RRSI нуждается в достаточно полезном verifier signal и evolve dataset. Не все
задачи можно свести к надёжному score; critic и policy могут разделять ошибки.
Сдерживание длины diff и tokens не гарантирует семантической корректности или
переноса на произвольный домен. Для части workspace datasets качество измеряется
LLM judges, для coding/engineering используются tests/simulators; метрики различны.

Наше сопоставление: [Harness²](../arxiv-2610.14224/review.md) улучшает текущую
задачу по поведенческим контрастам без доступного benchmark score и сохраняет опыт.
RRSI отбирает по scored evolve set, затем переносит фиксированный harness на OOD.
У них общий объект изменений, но разные режимы обратной связи. Прямое сравнение
RRSI против Harness² в проверенных таблицах не проводилось.

Для [исследовательской картотеки](../../../notes/syntheses/synthesis-prompt-harness-orchestration/review.md)
интересны история гипотез, раздельный учёт качества и стоимости, и критерий
удаления правил, потерявших полезность. Возможная интеграция с нашим workflow —
предложение ассистента, не проверенная гипотеза о превосходстве и не установленная
новизна. Эксперимент с автоматической эволюцией нашего репозитория не запускался.
