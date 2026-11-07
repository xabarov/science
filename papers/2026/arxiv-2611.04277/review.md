# Harness-S обучение policy через вспомогательные обвязки

ID: `arxiv:2611.04277`. Harness-S переносит полезное поведение специализированных
harnesses в одну обучаемую policy. Основная проверка — обученная модель лучше
решает задачи под исходным H0, когда вспомогательная обвязка убрана.

## Глубина и источники

Частичный разбор [v1 от 3 ноября 2026](https://arxiv.org/html/2611.04277v1),
проверка 2026-11-07. Прочитаны основные методические и экспериментальные разделы,
Appendices A–C и выбранные D/F/G. PDF Figure2 и Tables1/2/9/10 проверены визуально:
в Markdown от hf papers read часть табличных ячеек отсутствует. Полный код,
исходные run logs и benchmark reproduction не проверялись. Код зафиксирован на
commit `889eabc467be2db17156293e835a5ace850b42c7`.

## Проблема и метод

Обвязка задаёт prompts, skills, context/memory, hooks и представление инструментов.
Правка часто исправляет одну группу ошибок и ухудшает другую. Разные правки
дополняют друг друга, а их полезность меняется по мере обучения модели.

Directional Harness Evolution ветвится по диагностированным причинам ошибок.
Следующих родителей выбирают по общему validation coverage, но все исполнимые
protocol-compliant варианты сохраняются в пуле. Затем implementations фиксируют.
Пул статьи — H0 плюс19 siblings; общий verifier и environment interfaces сохраняются.

Для обучающей задачи текущая policy делает восемь попыток под H0. Если хотя бы
одна успешна, эта группа остаётся. Если все провалены, router выбирает восемь
разных siblings; та же policy делает по одной новой попытке с исходного состояния.
Rescue group целиком заменяет исходную, включая случай повторного полного провала.
Это новые on-policy rollouts, не исправление старой траектории и не teacher answers.
[Sections3.1–3.3](https://arxiv.org/html/2611.04277v1#S3).

Например, вместо восьми нулевых rewards получаем две удачные попытки и шесть
неудачных. Возникает reward contrast внутри задачи, полезный для GRPO. Успешные
поведения вроде lookup API schema или полного обхода pagination могут затем
воспроизводиться моделью под H0. Это иллюстрация механизма, не отдельный результат.

## Что обучается и что делает router

GRPO обновляет параметры общей policy. Для каждого policy token считается clipped
ratio текущей вероятности к pre-update policy в исходном generating harness context.
Loss не включает injected инструкции и tool observations. Есть detached importance
correction между rollout/training backends, reference-model KL отсутствует.
Группы с одинаковым reward не содержат внутри-task контраста; это не утверждение,
что весь optimizer step при этом обязательно равен нулю. [Appendix B](https://arxiv.org/html/2611.04277v1#A2).

В статье router использует prefer/avoid behavior profiles, результаты validation
на похожих задачах и weighted voting LLM. Это не отдельная RL-policy выбора,
обучаемая вместе с executor градиентом. Для AppWorld 4B профили обновляются каждые
десять policy updates и в конце. Test outcomes в выбор не входят.

На inference можно оставить H0 или заранее выбрать один Rank1 harness на задачу.
Один выбранный harness используется во всех трёх попытках; после неудачи повторного
выбора по test feedback нет. Равное число executor rollouts не включает стоимость
API router и получения validation evidence.

## Проверенные результаты

Table1, Qwen3.5-9B, Avg@3 в процентах:

| Задача | GRPO под H0 | GRPO с H0 rescue | Harness-S под H0 | Harness-S Rank1 |
| --- | ---: | ---: | ---: | ---: |
| AppWorld | 53.31 | 54.83 | 62.34±1.28 | 67.05±1.20 |
| SWE-Gym | 25.63 | 26.88 | 32.50±1.51 | 36.25±1.45 |
| DBBench | 67.00 | 67.78 | 73.44±1.67 | 75.67±1.55 |

Это средние по трём randomized runs; ± — SD между runs, не CI. В каждом run
по три попытки на задачу. Avg@3 усредняет успех по попыткам; Pass@3 засчитывает
задачу при хотя бы одной успешной попытке. [Table1, PDF p. 7](https://arxiv.org/html/2611.04277v1#S3.T1).

Train/validation/test splits: AppWorld 320/150/262, SWE-Gym 240/100/160,
DBBench 1600/200/300. Held-out tasks не означают автоматически новые task families:
публичная документация AppWorld допускает пересечение семейств.

Повторные H0 попытки — существенный control: Harness-S лучше них под тем же H0.
Обычное смешивание harnesses тоже не достигает того же результата. Это поддерживает
объяснение через выборочную complementary exploration, хотя не доказывает единственный
возможный механизм переноса. Behavior frequencies размечены LLM и не равны проверке
истинности каждого изменения внутренней стратегии.

На AppWorld 4B Top8 с adaptive profiles имеет final H0 Avg@3 56.87 против 52.16
с fixed profiles: +4.71п.п. Per-trigger бюджет одинаков, по одной попытке в восьми
sibling harnesses; общий расход зависит от числа all-failure triggers.
[Table2, PDF p. 9](https://arxiv.org/html/2611.04277v1#S4.T2).

## Цена и границы сравнения

AppWorld 4B за 40 updates: plain GRPO — 2560 rollouts, 50.58 reported GPU-hours,
Avg@3 48.98; Harness-S — 3032,67.19 и 56.87. Поэтому +7.89п.п. получены при
+18.44% sampling, а не одинаковом полном бюджете. H0 rescue использует 3616
rollouts и 97.77 GPU-hours, имея Avg@3 51.91. [Table9](https://arxiv.org/html/2611.04277v1#A7.T9).

Full pipeline отдельно сообщает 4010 search rollouts, 3032 training rollouts,
75.79 GPU-hours и 18.69M API tokens. В статье используются восемь H800.
Нельзя выводить цену дешёвого однокарточного опыта из одного training rollout count.
Определение учёта GPU-hours независимо не проверялось.
[Table10](https://arxiv.org/html/2611.04277v1#A7.T10).

## Что фактически опубликовано

Первый релиз фокусируется на AppWorld. По умолчанию используется TF-IDF Rule Router
без profiles и LLM voting; это другой протокол, чем полный метод статьи. README
раскрывает разницу 20-member public pool и historical 10-member training subset,
checkpoint selection и runtime placement. Новые accuracy numbers для defaults
не заявлены. Полный GPU end-to-end portable launcher ещё не подтверждён.
[Pinned reproduction notes](https://github.com/DeepExperience/Harness-S/blob/889eabc467be2db17156293e835a5ace850b42c7/docs/reproduction.md).

Статически проверенный rescue.py подтверждает all-failure trigger, замену всей
группы и сброс session для fresh attempts. Это не подтверждает работоспособность
всей RL-системы на нашем оборудовании. [Репозиторий](https://github.com/DeepExperience/Harness-S).

## Связь с предыдущими работами и идеи

RRSI и Harness² изменяют runtime при фиксированной policy; Harness-S использует
runtime diversity как источник обучающих траекторий для policy. В R²Orch обучается
оркестрация и снимаются skills, здесь обновляется executor policy. В RecursiveMAS
связываются latent states через adapters; здесь коммуникация и действия обычные,
а разнообразие создаётся обвязками. Это разные уровни вмешательства.

Для наших идей особенно важен policy-aware re-anchoring: периодическая актуализация
свидетельств при изменении policy уже входит в метод. Поэтому «обновлять профиль,
потому что модель обучилась» не новый вклад. Возможный узкий вопрос — дешевле выбирать,
что перекалибровать, при ограниченном бюджете проверок. Нужны controls против простого
периодического обновления и Rule Router; новизна и выигрыш не установлены.

2026-11-07 — первый частичный разбор v1 и сверка публичного релиза.
