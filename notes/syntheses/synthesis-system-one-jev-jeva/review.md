# JEV → Jeva: интерактивный учебник

ID: `synthesis:system-one-jev-jeva`.
[Открыть автономный HTML](../../../learning/jev-jeva.html).
В GitHub нужно скачать файл и открыть его в браузере; сырой HTML в просмотрщике
репозитория не выполняет интерактивные блоки. Сервер и интернет не нужны.

Учебная адаптация [разбора Jeva](../../../papers/2026/arxiv-2610.18773/review.md),
подготовлена 03.11.2026. Это не новая публикация и не benchmark reproduction.
JEV от TypeSafe и открытая Jeva разделены: публичный анонс JEV не раскрывает
архитектуру, достаточную для переноса на него механики Jeva.

## Содержание и учебный маршрут

1. System One как прямое типизированное решение; границы гарантий корректности.
2. Конструктор реальных request/option templates: список, mapping, Noul, Score.
3. Анимированный проход через Qwen3.5-4B, pooling, проекции 2560→768, attention,
   residual/SwiGLU и readout; формы тензоров и сокращённый PyTorch forward.
4. Каузальная маска как иллюстрация information flow и last-token pooling.
5. Точная двумерная конструкция Proposition 1: замена C меняет A/B,
   перестановка сохраняет scores, выключение refinement оставляет ничью.
6. Competition, coverage, open/closed Choice, Noul probability, Score expectation.
7. Подсчёт кодирований для none/set/hybrid cache, включая перестановку набора.
8. Лосс публичного fine-tuner: CE и optional coverage BCE, мягкие цели Noul/Score,
   локальный градиентный шаг по scores при фиксированных температурах и пороге.
9. Результаты авторов с протоколами и ограничениями, самопроверка, первоисточники.

Входной текст не отправляется в модель. Лаборатории — арифметические демонстрации;
они не используют вымышленные latency и не выдают числа за качество Jeva.
Обновляются только scores в браузере, а не веса нейросети. Упрощения подписаны.
Attention weights отделены от answer probabilities; Noul отделён от null;
порог coverage 0.5 не подменяет argmax совместных вероятностей.

## Основания

- [TypeSafe: Introducing System One Models & Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev),
  15.09.2026 — интерфейс и заявления производителя, включая RLCD.
- [Jeva, arXiv:2610.18773v2](https://arxiv.org/html/2610.18773v2),
  18.10.2026 — Sections 3–4, Proposition 1, Appendices B.5–B.8, C.2–C.6.
- [Код commit e2104e0](https://github.com/LiQiiiii/Jeva/tree/e2104e0720100f979f2cdd456a1cbbee96da0abd/src/jeva)
  — backbone, collate, inference, matrix decision, fine-tuner objective.
- [Checkpoint revision 25ca454](https://huggingface.co/LIQIIIII/Jeva-4B/tree/25ca454b3522615a243c06703ba410ecf152a128)
  — размеры и параметры Jeva-4B.

Глубина чтения исходной статьи — partial, как в её карточке. Полного аудита
доказательств, обучения и воспроизведения метрик не было. В учебнике явно сохранено
расхождение протокола MLDR: Section 4.4 / Figure 9 — 50 novel-set запросов на reuse
level, Appendix C.5 — 46. Уточнение авторов не получено.

## Проверка артефакта

В headless Chromium / Playwright проверены локальная загрузка без сетевых запросов,
встроенный шрифт, отсутствие JavaScript errors, уникальность ID и якоря, управление
лабораториями, ширины 320–1440 px без горизонтального overflow, мобильное меню,
reduced motion и print stylesheet. Desktop/mobile скриншоты просмотрены.

Численно проверены нормировка q/p, 2D Proposition 1, перестановки и ничьи,
Noul/Score readout, null при rho>0.5, hard-null competition CE=0, мягкие цели,
градиент конечными разностями и убывание loss после локальных шагов, счётчики кэша.
Это проверка учебного JavaScript, а не тест исходной нейросети.

Шрифт Golos Text встроен вместе с SIL OFL 1.1. SVG-схемы нарисованы заново.
При изменении оснований в картотеке учебник должен пройти содержательный пересмотр;
зависимость записана в `extra.research.depends_on`.
