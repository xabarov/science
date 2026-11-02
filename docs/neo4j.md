# Neo4j как проекция картотеки

Экспорт уже реализован; сервер Neo4j не установлен и не запущен этим проектом.
SQLite позволяет искать и обходить связи без него.

```sh
python3 science.py export-neo4j
```

Создаются `exports/neo4j/nodes.csv`, `edges.csv`, `import.cypher`.
CSV сохраняет кириллицу, кавычки, переносы строк и provenance.

Скопировать CSV в разрешённый import directory **отдельной базы для science**,
затем выполнить import.cypher через cypher-shell. Для самостоятельного сервера
нужна настройка LOAD CSV из локальных файлов; в Aura этот способ недоступен.
См. [официальное руководство LOAD CSV](https://neo4j.com/docs/cypher-manual/current/clauses/load-csv/).

Импорт — полная замена проекции: вначале удаляет узлы `ScienceNode` с
`catalog='science-local-v1'` и их связи. Это позволяет отражать удалённые/исправленные
записи и не оставлять старые рёбра при повторном импорте. Не добавлять ручные
данные к этим узлам: они будут удалены при следующем импорте.
Импорт не является одной транзакцией всего снимка; при ошибке исправить причину
и повторить полный импорт. Не обслуживать пользователей из этой проекции во время обновления.

Узлы: ScienceNode с kind=paper/topic/idea/synthesis, topics/methods/datasets
для словарных понятий и reference для неразобранных ссылок.
Рёбра: SCIENCE_LINK с `type` (cites, uses, …), `status`, `source`, `locator`, `note`.
Типизированное свойство выбрано для переноса без APOC и динамических типов связей.
Классификация по теме имеет type=tagged и status=curated, она не выдаётся за
проверенное научное свидетельство. Authors и claims пока не отдельные узлы.

Примеры запросов после импорта:

```cypher
// Прямые связи обсуждавшейся статьи с сохранённым основанием.
MATCH (a:ScienceNode {id:'arxiv:2610.39497'})-[r:SCIENCE_LINK]-(b)
RETURN a.title, type(r), r.type, r.status, r.source, r.locator, b.id, b.title;

// Работы без разбора, на которые уже ссылается картотека.
MATCH (a)-[r:SCIENCE_LINK]->(b:ScienceNode {stub:true})
RETURN b.id, count(r) AS references ORDER BY references DESC;

// Идеи и их непосредственные основания.
MATCH (i:ScienceNode {kind:'idea'})-[r:SCIENCE_LINK]->(e)
WHERE r.type IN ['uses','tests','related']
RETURN i.title, r.type, r.status, e.title;
```

Выбор версии Neo4j, выделение памяти, контейнер и автоматическая синхронизация
пока не настроены. Текст Cypher сверён с официальным форматом, но фактический
импорт в работающий Neo4j здесь не тестировался.
