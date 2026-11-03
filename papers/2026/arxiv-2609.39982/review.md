# Mid-Harness: Scaling Actions Between Model and Harness for Terminal Agents

ID: `arxiv:2609.39982`. Minki Kang и соавторы, NVIDIA. Версия v1 от 30.09.2026.
Разбор 03–04.11.2026. [Статья](https://arxiv.org/html/2609.39982v1),
[проект](https://byungkwanlee.github.io/MidHarness-page/).

## Глубина проверки

`reading_scope=partial`: Sections 2–6; выбранные Appendices B.1–B.4, C.1–C.4;
Tables 1–8, 10 для обсуждаемых результатов. HTML v1 сохранён с хешами.
Полный аудит кода, обучение, benchmark inference и измерение стоимости нами
не выполнялись. Результаты ниже reported_result, а не reproduced.

Дополнительно проверены `hf papers read 2609.39982` и `hf papers info ... --json`
через установленный HF CLI 2.4.0. Markdown содержит полный основной текст, но
теряет некоторые табличные ячейки: 52.38 и 49.66 присутствуют в arXiv HTML и
отсутствуют в полученном Markdown. Поэтому численные результаты сверены с HTML v1.
Время Published Time в шапке Markdown не использовалось как дата подачи статьи.

## Идея

На каждом шаге терминального агента модель может предложить полезное действие,
но случайно выбрать неудачную команду, после которой окружение станет хуже.
Mid-Harness расходует дополнительные вычисления до исполнения: генерирует несколько
кандидатов из одной истории, проверяет их и пропускает в harness только один.
Исполняется одна ветвь, остальные кандидаты отбрасываются. Не нужны отдельные
sandbox-окружения для каждого кандидата, поскольку они не исполняются при отборе.

`history → N sampled actions → verifier → selected action → harness/environment → observation`.

В notation статьи: `a_i ~ pi(.|h_t)`, затем `a* = Verify_psi(h_t,{a_i})`.
После реального исполнения только a* обновляется история. Verifier получает
задание, наблюдаемую историю и команды кандидатов, но не reasoning, который
генератор написал перед этими командами. Неудача генератора может заключаться
не в отсутствии полезного варианта, а в ненадёжном выборе варианта из доступных.

## Что именно добавляется

Mid-Harness реализуется на границе model-call wrapper и существующего harness.
Основной generator TMAX-9B (terminal-RL модель от Qwen3.5-9B) и Vanillux2 сохраняются.
В zero-shot режиме та же модель используется и как verifier с другой инструкцией.
В distilled режиме LoRA включается только у verifier: action generator не обучают.

Это action-level test-time scaling. Его можно сочетать с параллельным Best-of-T
целых запусков и последовательными перезапусками Sequential Refine (SR).
Сами файлы обвязки не эволюционируют от шага к шагу, как в RRSI/Harness².

## Три механизма проверки

- Listwise: все кандидаты в одном запросе, выбор одного. Дешевле по числу вызовов,
  но трудно сопоставлять много альтернатив одновременно.
- Pointwise: независимо оценить каждый вариант и взять max. Возможна параллельная
  обработка, однако scores действий разного назначения должны быть сопоставимы.
- Pairwise: сравнивать пары по истории, затем агрегировать предпочтения.
  В default варианте выводятся rationale, scores 1–10 и A/B/TIE; используется
  margin-weighted normalized win rate, а не просто один победитель случайной пары.

При N=4 сравниваются все 6 пар. При N=8 по умолчанию используется ring + 4 pivots,
два последовательных этапа с параллельными сравнениями внутри: для восьми разных
кандидатов получается 22–25 сравнений. Число 28 в основном тексте иллюстрирует
полный all-pairs, а не фактическое число default N8. Парсинг, deduplication и
fallback при некорректных judgments описаны в Appendix B.2.

## Качество на TerminalBench-Lite

Основной протокол: TMAX-9B generator, Vanillux2, 98 из 100 задач (две исключены
из-за sandbox service errors), три runs на задачу, максимум 64 шага. Pass@1 —
средний exact-success по 294 runs; Pass@3 — доля задач с успехом хотя бы в одном
из трёх runs. Это итоговый task success, не точность classification каждого действия.

| Проверка | Кандидатов/шаг | Pass@1 | Pass@3 |
| --- | ---: | ---: | ---: |
| Base agent | 1 | 50.00% | 69.39% |
| First-runnable (только parsing) | 8 | 49.66% | 66.33% |
| Zero-shot listwise TMAX-9B | 8 | 51.02% | 67.35% |
| Zero-shot pointwise TMAX-9B | 8 | 52.38% | 67.35% |
| Zero-shot pairwise TMAX-9B | 8 | 54.76% | 71.43% |
| Distilled pairwise TMAX-9B | 8 | 57.14% | 75.51% |
| Frontier listwise GPT-5.6 Sol | 8 | 68.03% | 80.61% |

Источник: Appendix C.1 Table 5; Sections 3–4. Сильный verifier подтверждает наличие
используемых альтернатив, но не является oracle для измерения всего candidate coverage.
Увеличение N с 4 до 8 при слабом listwise verifier даёт лишь 49.32→51.02%.

По task-paired bootstrap (100000 resamples, 98 task blocks, Table 10) у zero-shot
9B прибавка +4.76 п.п. имеет 95% CI [-0.68,10.20], у distilled +7.14 п.п. —
[1.36,12.93]. Повторные runs одной задачи сохраняются блоком; не все положительные
point estimates статистически разрешены этим анализом. Интервалы marginal,
без simultaneous correction по всем сравнениям.

## Дистилляция и один токен A/B

Teacher GPT-5.6 Sol размечает пары. Для reasoning distillation: 117631 примеров
из 244 training tasks TMAX-15k, 732 trajectories; LoRA rank/alpha 64/128,
2 epochs, learning rate 1e-4, 4 H200. Отдельные 21 task используются для offline
анализа, не для checkpoint selection. Генератор остаётся исходным (Appendix B.3).

Decision-only verifier выдаёт один токен A или B. Из их logprobs получается
`p_A = exp(l_A)/(exp(l_A)+exp(l_B))`; candidate score — средняя preference
probability в его парах. Это меняет не только длину rationale, но и aggregation,
training targets и corpus (TIE исключается). Поэтому нельзя приписать весь эффект
только удалению reasoning.

На 9B/N8 distilled: Pass@1 57.14→59.18%, reference-priced end-to-end cost
0.207→0.157 USD/output (−24.1%). При этом Pass@3 падает 75.51→73.47%.
У zero-shot 9B/N8 Pass@1 54.76→56.12%. На 4B и 27B decision-only дешевле,
но имеет ниже Pass@1, а при 9B/N4 reasoning тоже сильнее (Tables 5, 7, 8).
Следовательно, «один токен всегда лучше рассуждения» из статьи не следует.

Связь с JEV/Jeva — наша интерпретация роли отдельного прямого решателя. В этих
экспериментах не использовали Jev или Jeva: A/B выдаёт обычная LLM либо её
LoRA-adapted verifier. Проверка семантики команд требует доменной компетентности.

## Композиция, перенос и стоимость

При 9B distilled Mid-Harness N8 + Best-of-3 получает 66.33% Pass@1 против
55.10% у Best-of-3 без action scaling. + SR R1 получает 60.20% против 55.10%.
Это одинаковое число environment executions в парном сравнении, но дополнительные
model/verifier calls и токены (Section 6.1; Table 6).

Есть перенос на 4B/27B, другие backbones и Terminus-2. Table 2 дополнительно
проверяет Terminal-Bench 2.1 (89 tasks), SWE-bench Verified Mini (50, не весь
Verified) и FeatureBench-Mini CPU-only (23 tasks). Дистилляция не везде сильнее
zero-shot: на Terminal-Bench 2.1 у TMAX-9B 26.59% против zero-shot 27.34%.
Агрегированные прибавки также сосуществуют с регрессиями в отдельных доменах.

POT (parallelized output tokens) — сумма максимумов decoding length по параллельным
этапам, идеализированный latency proxy. Reference-priced cost использует фиксированные
ставки и допущение shared generator prefill; verifier prompts учитываются по каждому
вызову. Это не реальный счёт сервиса и не измеренная wall-clock latency. Дистилляционное
обучение — отдельная стоимость. Короткий вывод verifier не отменяет стоимость
чтения истории и многократных pairwise сравнений (Appendix B.4).

## Что остаётся неизвестным

Gold action labels отсутствуют. Teacher agreement показывает сходство с teacher,
а не семантическую истинность оценки. Основные остающиеся ошибки — неверное понимание
эффекта команды и её исполнимости в текущем окружении; verifier предсказывает это
без запуска каждого кандидата. Пул может не содержать хорошего действия.

Для нашего направления возникают вопросы о независимом аудите отвергнутых действий,
адаптивном N, числе pairwise сравнений и маршрутизации сложных состояний сильному
verifier. Это предложения ассистента: новизна не установлена, интеграция и эксперименты
не выполнялись. Есть связи с [Jeva](../arxiv-2610.18773/review.md),
[RRSI](../arxiv-2609.24972/review.md),
[Harness²](../arxiv-2610.14224/review.md) и
[картой условных вычислений](../../../notes/topics/topic-conditional-computation/review.md).
