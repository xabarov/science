#!/usr/bin/env python3
"""Local, file-backed research catalog. Python 3.9+, no third-party packages."""
import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
import research
from datetime import date
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
KINDS = ('paper', 'topic', 'idea', 'synthesis', 'question', 'experiment')
STATUSES = ('inbox', 'triaged', 'reviewed', 'revisit', 'archived')
SCOPES = ('unread', 'abstract', 'partial', 'full', 'not_applicable')
EPISTEMIC = ('reported_result', 'theoretical_result', 'hypothesis', 'interpretation', 'open_question')
VERIFICATION = ('not_checked', 'source_checked', 'reproduced')
RELATIONS = ('cites', 'supports', 'contradicts', 'extends', 'uses', 'related', 'motivates', 'tests')
FIELDS = {
    'schema_version', 'id', 'kind', 'title', 'year', 'status', 'priority',
    'topics', 'authors', 'methods', 'datasets', 'aliases', 'source',
    'source_version', 'accessed', 'reading_scope', 'paper_type', 'summary',
    'created', 'updated', 'claims', 'relations', 'extra',
}


def canonical_id(value):
    value = unquote(value.strip())
    arxiv = re.fullmatch(
        r'(?:(?:https?://)?(?:www\.)?(?:arxiv\.org/(?:abs|pdf|html)/|huggingface\.co/papers/)|arxiv:)?'
        r'(\d{4}\.\d{4,5}|[a-zA-Z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?(?:\.pdf)?/?', value)
    if arxiv:
        return 'arxiv:' + arxiv.group(1).lower()
    doi = re.fullmatch(r'(?:(?:https?://)?(?:dx\.)?doi\.org/|doi:)?(10\.\d{4,9}/\S+)', value, re.I)
    if doi:
        return 'doi:' + doi.group(1).lower()
    if re.fullmatch(r'(?:paper|topic|idea|synthesis|question|experiment):[a-z0-9][a-z0-9._-]*', value):
        return value
    raise ValueError('Ожидается arXiv ID/URL, DOI или ID вида paper:slug / topic:slug / idea:slug / synthesis:slug')


def load_vocab(root):
    value = json.loads((root / 'vocab.json').read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('vocab.json должен содержать объект')
    for field in ('topics', 'methods', 'datasets'):
        if not isinstance(value.get(field), dict):
            raise ValueError('vocab.json: отсутствует словарь ' + field)
        for key, names in value[field].items():
            if not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', key):
                raise ValueError('Некорректный термин: ' + key)
            if not isinstance(names, list) or not names or any(not isinstance(n, str) or not n.strip() for n in names):
                raise ValueError('Некорректные синонимы: ' + key)
    return value


def checked_record(record, path, vocab):
    def require(ok, message):
        if not ok:
            raise ValueError(f'{path}: {message}')
    require(isinstance(record, dict), 'record должен быть объектом')
    require(set(record) == FIELDS, 'неизвестные/пропущенные поля: ' + str(sorted(set(record) ^ FIELDS)))
    require(record['schema_version'] == 1, 'неподдерживаемая schema_version')
    require(record['kind'] in KINDS, 'неверный kind')
    require(record['status'] in STATUSES, 'неверный status')
    require(record['reading_scope'] in SCOPES, 'неверный reading_scope')
    require(type(record['year']) is int and 1000 <= record['year'] <= 9999, 'неверный year')
    require(type(record['priority']) is int and 1 <= record['priority'] <= 3, 'priority: 1 (высокий), 2 или 3')
    for field in ('id', 'title', 'source', 'source_version', 'accessed', 'paper_type', 'summary', 'created', 'updated'):
        require(isinstance(record[field], str), field + ' должен быть строкой')
    require(bool(record['title'].strip()), 'пустой title')
    require(canonical_id(record['id']) == record['id'], 'id должен быть каноническим')
    expected_kind = record['id'].split(':', 1)[0]
    require((record['kind'] == 'paper' and expected_kind in ('arxiv', 'doi', 'paper')) or expected_kind == record['kind'],
            'kind не соответствует ID')
    if record['kind'] == 'paper':
        require(record['reading_scope'] != 'not_applicable', 'для paper нужна глубина чтения')
    for field in ('created', 'updated', 'accessed'):
        if record[field] or field != 'accessed':
            try:
                date.fromisoformat(record[field])
            except ValueError:
                require(False, 'неверная ISO-дата: ' + field)
    for field in ('topics', 'methods', 'datasets', 'authors', 'aliases'):
        require(isinstance(record[field], list) and all(isinstance(v, str) and v.strip() for v in record[field]),
                field + ' должен быть массивом непустых строк')
        require(len(record[field]) == len(set(record[field])), 'повторы в ' + field)
    for field in ('topics', 'methods', 'datasets'):
        require(all(term in vocab[field] for term in record[field]), 'неизвестное понятие в ' + field)
    for alias in record['aliases']:
        require(canonical_id(alias) == alias, 'alias должен быть каноническим')
    require(isinstance(record['extra'], dict), 'extra должен быть объектом')
    require(isinstance(record['claims'], list), 'claims должен быть массивом')
    claim_ids = set()
    for claim in record['claims']:
        require(isinstance(claim, dict) and set(claim) == {'id', 'text', 'epistemic_status', 'verification', 'source', 'locator'},
                'неверные поля claim')
        require(all(isinstance(claim[k], str) for k in claim), 'поля claim должны быть строками')
        require(bool(re.fullmatch(r'[a-z0-9][a-z0-9_-]*', claim['id'])) and claim['id'] not in claim_ids, 'неверный/повторный claim id')
        claim_ids.add(claim['id'])
        require(bool(claim['text'].strip()), 'пустой claim')
        require(claim['epistemic_status'] in EPISTEMIC, 'неверный epistemic_status')
        require(claim['verification'] in VERIFICATION, 'неверный verification')
        if claim['epistemic_status'] in ('reported_result', 'theoretical_result') or claim['verification'] != 'not_checked':
            require(bool(claim['source'].strip()) and bool(claim['locator'].strip()), 'результату/проверке нужны source и locator')
    require(isinstance(record['relations'], list), 'relations должен быть массивом')
    for edge in record['relations']:
        require(isinstance(edge, dict) and set(edge) == {'type', 'target', 'status', 'source', 'locator', 'note'}, 'неверные поля relation')
        require(all(isinstance(edge[k], str) for k in edge), 'поля relation должны быть строками')
        require(edge['type'] in RELATIONS, 'неверный тип relation')
        require(edge['status'] in ('verified', 'inferred', 'hypothesis'), 'неверный status связи')
        require(canonical_id(edge['target']) == edge['target'], 'target должен быть каноническим')
        require(edge['target'] != record['id'], 'ссылка на саму запись')
        require(bool(edge['note'].strip()), 'у связи нужно пояснение')
        if edge['status'] == 'verified':
            require(bool(edge['source'].strip()) and bool(edge['locator'].strip()), 'проверенной связи нужны source и locator')
    return record


def collect(root):
    vocab = load_vocab(root)
    docs, owners = [], {}
    for base in ('papers', 'notes'):
        for path in sorted((root / base).rglob('record.json')):
            try:
                record = checked_record(json.loads(path.read_text(encoding='utf-8')), path, vocab)
            except (TypeError, KeyError, json.JSONDecodeError) as exc:
                raise ValueError(f'{path}: {exc}') from exc
            review = path.with_name('review.md')
            if not review.is_file():
                raise ValueError(f'{path}: отсутствует review.md')
            for identity in [record['id']] + record['aliases']:
                if identity in owners:
                    raise ValueError(f'Дубликат {identity}: {owners[identity]} и {path}')
                owners[identity] = path
            docs.append((record, review.relative_to(root).as_posix(), review.read_text(encoding='utf-8')))
    research.validate(docs, canonical_id)
    return docs, vocab


SCHEMA = '''
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, year INTEGER NOT NULL,
 status TEXT NOT NULL, priority INTEGER NOT NULL, scope TEXT NOT NULL,
 summary TEXT NOT NULL, source TEXT NOT NULL, path TEXT NOT NULL, record TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aliases (alias TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id));
CREATE TABLE IF NOT EXISTS concepts (
 document_id TEXT NOT NULL REFERENCES documents(id), category TEXT NOT NULL, term TEXT NOT NULL,
 PRIMARY KEY(document_id, category, term)
);
CREATE INDEX IF NOT EXISTS concepts_lookup ON concepts(category,term);
CREATE TABLE IF NOT EXISTS relations (
 edge_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES documents(id), target_id TEXT NOT NULL,
 type TEXT NOT NULL, status TEXT NOT NULL, source_url TEXT NOT NULL, locator TEXT NOT NULL, note TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS relations_target ON relations(target_id);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
 id UNINDEXED, title, summary, terms, body, tokenize='unicode61', prefix='2 3 4'
);
'''


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def fingerprint(docs, vocab):
    payload = [vocab, [(r, p, body) for r, p, body in docs]]
    return digest(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def rebuild(root):
    # Validate every source before touching the last good index.
    docs, vocab = collect(root)
    db_path = root / 'data' / 'catalog.sqlite3'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=30)
    try:
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript(SCHEMA)
        identities = {i: r['id'] for r, _, _ in docs for i in [r['id']] + r['aliases']}
        with con:
            for table in ('aliases', 'concepts', 'relations', 'documents', 'search_index', 'metadata'):
                con.execute('DELETE FROM ' + table)
            for record, path, body in docs:
                con.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)', (
                    record['id'], record['kind'], record['title'], record['year'], record['status'],
                    record['priority'], record['reading_scope'], record['summary'], record['source'], path,
                    json.dumps(record, ensure_ascii=False)))
                con.executemany('INSERT INTO aliases VALUES (?,?)', [(a, record['id']) for a in record['aliases']])
                terms = [record['id'], record['source_version']] + list(record['authors'])
                for category in ('topics', 'methods', 'datasets'):
                    for term in record[category]:
                        con.execute('INSERT INTO concepts VALUES (?,?,?)', (record['id'], category, term))
                        terms.extend([term] + vocab[category][term])
                claims_text = '\n'.join(c['text'] for c in record['claims'])
                relation_text = '\n'.join(e['target'] + ' ' + e['note'] for e in record['relations'])
                con.execute('INSERT INTO search_index VALUES (?,?,?,?,?)', (
                    record['id'], record['title'], record['summary'], ' '.join(terms),
                    body + '\n' + claims_text + '\n' + relation_text + '\n' + ' '.join(record['aliases']) +
                    '\n' + json.dumps(research.extension(record), ensure_ascii=False)))
                for edge in record['relations']:
                    target = identities.get(edge['target'], edge['target'])
                    edge_id = digest(json.dumps([record['id'], target, edge], sort_keys=True, ensure_ascii=False))
                    con.execute('INSERT OR IGNORE INTO relations VALUES (?,?,?,?,?,?,?,?)', (
                        edge_id, record['id'], target, edge['type'], edge['status'], edge['source'], edge['locator'], edge['note']))
            con.execute('INSERT INTO metadata VALUES (?,?)', ('fingerprint', fingerprint(docs, vocab)))
            con.execute('PRAGMA user_version=1')
    finally:
        con.close()
    return len(docs)


def open_index(root):
    path = root / 'data' / 'catalog.sqlite3'
    if not path.exists():
        raise ValueError('Нет индекса. Выполните: python3 science.py index')
    con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    if con.execute('PRAGMA user_version').fetchone()[0] != 1:
        con.close()
        raise ValueError('Версия индекса несовместима. Выполните index.')
    return con


def filters(args):
    clauses, values = [], []
    for flag, column in [('kind', 'kind'), ('status', 'status'), ('year', 'year')]:
        value = getattr(args, flag, None)
        if value is not None:
            clauses.append(f'd.{column}=?')
            values.append(value)
    for category, flag in [('topics', 'topic'), ('methods', 'method'), ('datasets', 'dataset')]:
        if getattr(args, flag, None):
            clauses.append('EXISTS(SELECT 1 FROM concepts c WHERE c.document_id=d.id AND c.category=? AND c.term=?)')
            values.extend([category, getattr(args, flag)])
    if getattr(args, 'epistemic', None):
        clauses.append("EXISTS(SELECT 1 FROM json_each(d.record,'$.claims') j WHERE json_extract(j.value,'$.epistemic_status')=?)")
        values.append(args.epistemic)
    return clauses, values


def query_documents(root, args, query=None):
    clauses, values = filters(args)
    if query is None:
        sql = 'SELECT d.*, d.summary AS excerpt FROM documents d'
        order = 'd.priority,d.year DESC,d.id'
    else:
        if not query.strip():
            raise ValueError('Пустой поисковый запрос')
        # Literal by default; advanced FTS syntax is explicitly opt-in.
        if not args.fts:
            words = re.findall(r'[^\W_]+', query, flags=re.UNICODE)
            if not words:
                raise ValueError('В запросе нет слов')
            query = ' AND '.join('"' + w.replace('"', '""') + '"' for w in words)
        sql = "SELECT d.*, snippet(search_index,-1,'[',']',' … ',28) AS excerpt FROM search_index JOIN documents d ON d.id=search_index.id"
        clauses.insert(0, 'search_index MATCH ?')
        values.insert(0, query)
        order = 'bm25(search_index,0,5,3,2,1),d.id'
    if clauses:
        sql += ' WHERE ' + ' AND '.join(clauses)
    sql += ' ORDER BY ' + order + ' LIMIT ?'
    values.append(args.limit)
    with open_index(root) as con:
        return [dict(row) for row in con.execute(sql, values)]


def resolve(con, identity):
    identity = canonical_id(identity)
    row = con.execute('SELECT document_id FROM aliases WHERE alias=?', (identity,)).fetchone()
    return row[0] if row else identity


def graph_neighbors(root, identity, depth=1):
    with open_index(root) as con:
        identity = resolve(con, identity)
        rows = con.execute('''WITH RECURSIVE
          links(a,b) AS (SELECT source_id,target_id FROM relations UNION SELECT target_id,source_id FROM relations),
          walk(id,depth) AS (SELECT ?,0 UNION SELECT links.b,walk.depth+1 FROM walk JOIN links ON links.a=walk.id WHERE walk.depth<?)
          SELECT id,min(depth) AS depth FROM walk GROUP BY id ORDER BY depth,id''', (identity, depth)).fetchall()
        output = []
        for row in rows:
            if row['depth'] == 0:
                continue
            target = con.execute('SELECT title,path,kind FROM documents WHERE id=?', (row['id'],)).fetchone()
            output.append({'id': row['id'], 'depth': row['depth'], 'title': target['title'] if target else '(внешняя ссылка; разбора нет)',
                           'path': target['path'] if target else None})
        edges = [dict(row) for row in con.execute('SELECT * FROM relations WHERE source_id=? OR target_id=? ORDER BY type,target_id', (identity, identity))]
        return {'id': identity, 'neighbors': output, 'direct_relations': edges}


def export_neo4j(root, output):
    # Full snapshot; import only into a database dedicated to this catalog.
    docs, vocab = collect(root)
    output.mkdir(parents=True, exist_ok=True)
    identities = {i: r['id'] for r, _, _ in docs for i in [r['id']] + r['aliases']}
    nodes, edges = {}, []
    for record, path, _ in docs:
        nodes[record['id']] = {'id': record['id'], 'kind': record['kind'], 'title': record['title'], 'year': record['year'],
                               'status': record['status'], 'scope': record['reading_scope'], 'source': record['source'],
                               'path': path, 'stub': 'false', 'aliases_json': json.dumps(record['aliases'], ensure_ascii=False)}
    for record, _, _ in docs:
        for edge in record['relations']:
            target = identities.get(edge['target'], edge['target'])
            nodes.setdefault(target, {'id': target, 'kind': 'reference', 'title': target, 'stub': 'true'})
            edges.append({'id': digest(json.dumps([record['id'], target, edge], sort_keys=True)), 'from_id': record['id'],
                          'to_id': target, 'type': edge['type'], 'status': edge['status'], 'source': edge['source'],
                          'locator': edge['locator'], 'note': edge['note']})
        for category in ('topics', 'methods', 'datasets'):
            for term in record[category]:
                target = 'concept:' + category + ':' + term
                nodes[target] = {'id': target, 'kind': category, 'title': vocab[category][term][0], 'stub': 'false',
                                 'aliases_json': json.dumps(vocab[category][term], ensure_ascii=False)}
                edges.append({'id': digest(record['id'] + '\0' + target), 'from_id': record['id'], 'to_id': target,
                              'type': 'tagged', 'status': 'curated', 'source': record['id'],
                              'locator': 'record.json:' + category, 'note': 'Классификация картотеки; не научное утверждение'})
    claim_nodes, claim_edges = research.evidence_nodes(docs)
    nodes.update({n['id']: n for n in claim_nodes})
    for edge in claim_edges:
        for key in ('from_id', 'to_id'):
            owner, claim = research.split_ref(edge[key])
            edge[key] = identities.get(owner, owner) + ('#' + claim if claim else '')
    edges.extend(claim_edges)
    for name, fields, rows in [
        ('nodes.csv', ['id', 'kind', 'title', 'year', 'status', 'scope', 'source', 'path', 'stub', 'aliases_json'], sorted(nodes.values(), key=lambda n: n['id'])),
        ('edges.csv', ['id', 'from_id', 'to_id', 'type', 'status', 'source', 'locator', 'note'], sorted(edges, key=lambda e: e['id'])),
    ]:
        with (output / name).open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    (output / 'import.cypher').write_text('''// FULL SNAPSHOT. Dedicated science database only.
// Removes only nodes owned by this importer. Copy CSV files into Neo4j import directory.
MATCH (n:ScienceNode {catalog: 'science-local-v1'}) DETACH DELETE n;
CREATE CONSTRAINT science_node_id IF NOT EXISTS FOR (n:ScienceNode) REQUIRE n.id IS UNIQUE;
LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
MERGE (n:ScienceNode {id: row.id})
SET n.catalog='science-local-v1', n.kind=row.kind, n.title=row.title,
    n.year=toInteger(row.year), n.status=row.status, n.scope=row.scope,
    n.source=row.source, n.path=row.path, n.stub=(row.stub='true'), n.aliases_json=row.aliases_json;
LOAD CSV WITH HEADERS FROM 'file:///edges.csv' AS row
MATCH (a:ScienceNode {id: row.from_id}), (b:ScienceNode {id: row.to_id})
MERGE (a)-[r:SCIENCE_LINK {id: row.id}]->(b)
SET r.type=row.type, r.status=row.status, r.source=row.source, r.locator=row.locator, r.note=row.note;
''', encoding='utf-8')
    return len(nodes), len(edges)


def create_record(root, identity, title, kind='paper', year=None, source=''):
    identity = canonical_id(identity)
    existing, _ = collect(root)
    for record, path, _ in existing:
        if identity in [record['id']] + record['aliases']:
            raise ValueError(f'Запись уже существует: {path}')
    if not title.strip():
        raise ValueError('Укажите непустой --title')
    today = date.today().isoformat()
    if year is None:
        match = re.fullmatch(r'arxiv:(\d{2})\d{2}\.\d{4,5}', identity)
        year = 2000 + int(match.group(1)) if match else date.today().year
    if not source and identity.startswith('arxiv:'):
        source = 'https://arxiv.org/abs/' + identity.split(':', 1)[1]
    if not source and identity.startswith('doi:'):
        source = 'https://doi.org/' + identity.split(':', 1)[1]
    record = dict(schema_version=1, id=identity, kind=kind, title=title.strip(), year=year,
                  status='inbox', priority=2, topics=[], authors=[], methods=[], datasets=[], aliases=[],
                  source=source, source_version='', accessed='', reading_scope='unread' if kind == 'paper' else 'not_applicable',
                  paper_type='unknown' if kind == 'paper' else '', summary='', created=today, updated=today,
                  claims=[], relations=[], extra={})
    checked_record(record, identity, load_vocab(root))
    key = re.sub(r'[^a-zA-Z0-9._-]', '-', identity)
    if len(key) > 96:
        key = key[:70] + '-' + digest(identity)[:16]
    section = {'topic': 'topics', 'idea': 'ideas', 'synthesis': 'syntheses', 'question': 'questions', 'experiment': 'experiments'}
    folder = root / ('papers' if kind == 'paper' else 'notes') / (str(year) if kind == 'paper' else section[kind]) / key
    folder.mkdir(parents=True, exist_ok=False)
    template_path = root / 'templates' / (kind + '.md')
    if not template_path.exists():
        template_path = root / 'templates' / 'note.md'
    template = template_path.read_text(encoding='utf-8')
    if kind in research.STAGES:
        record['extra']['research'] = {'stage': next(iter(research.STAGES[kind]))}
    (folder / 'record.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (folder / 'review.md').write_text(template.replace('{{title}}', title).replace('{{id}}', identity), encoding='utf-8')
    return folder


def print_rows(rows, as_json=False):
    if as_json:
        print(json.dumps([{k: v for k, v in r.items() if k != 'record'} for r in rows], ensure_ascii=False, indent=2))
        return
    for row in rows:
        print(f"{row['id']}  [{row['kind']}; {row['status']}; {row['scope']}]")
        print('  ' + row['title'])
        print('  ' + ' '.join((row.get('excerpt') or '').split()))
        print('  ' + row['path'])
    if not rows:
        print('Ничего не найдено.')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=ROOT, help='Корень картотеки')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('validate', 'index', 'check', 'stats'):
        sub.add_parser(name)
    for name in ('search', 'list'):
        q = sub.add_parser(name)
        if name == 'search':
            q.add_argument('query')
            q.add_argument('--fts', action='store_true', help='Расширенный синтаксис FTS5')
        q.add_argument('--kind', choices=KINDS)
        q.add_argument('--status', choices=STATUSES)
        q.add_argument('--year', type=int)
        q.add_argument('--topic')
        q.add_argument('--method')
        q.add_argument('--dataset')
        q.add_argument('--epistemic', choices=EPISTEMIC)
        q.add_argument('--limit', type=int, default=20)
        q.add_argument('--json', action='store_true')
    q = sub.add_parser('new')
    q.add_argument('id')
    q.add_argument('--title', required=True)
    q.add_argument('--year', type=int)
    q.add_argument('--source', default='')
    q = sub.add_parser('note')
    q.add_argument('kind', choices=KINDS[1:])
    q.add_argument('slug')
    q.add_argument('--title', required=True)
    q = sub.add_parser('show')
    q.add_argument('id')
    q = sub.add_parser('related')
    q.add_argument('id')
    q.add_argument('--depth', type=int, choices=range(1, 5), default=1)
    q = sub.add_parser('export-neo4j')
    q.add_argument('--output', type=Path)
    research.setup_parser(sub)
    import preservation
    preservation.setup_parser(sub)
    import semantic
    semantic.setup_parser(sub)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command in research.COMMANDS:
            print(json.dumps(research.run(root, args), ensure_ascii=False, indent=2))
        elif args.command == 'validate':
            docs, _ = collect(root)
            print(f'OK: {len(docs)} записей; ID, словарь, утверждения и связи проверены.')
        elif args.command == 'index':
            print(f'Индекс обновлён: {rebuild(root)} записей.')
        elif args.command == 'check':
            docs, vocab = collect(root)
            with open_index(root) as con:
                row = con.execute("SELECT value FROM metadata WHERE key='fingerprint'").fetchone()
            if not row or row[0] != fingerprint(docs, vocab):
                raise ValueError('Индекс устарел. Выполните: python3 science.py index')
            print('Индекс соответствует файлам.')
        elif args.command in ('search', 'list'):
            if not 1 <= args.limit <= 10000:
                raise ValueError('--limit должен быть от 1 до 10000')
            print_rows(query_documents(root, args, args.query if args.command == 'search' else None), args.json)
        elif args.command == 'new':
            print(create_record(root, args.id, args.title, year=args.year, source=args.source))
            print('Создана заготовка inbox. После редактирования выполните index.')
        elif args.command == 'note':
            print(create_record(root, args.kind + ':' + args.slug, args.title, kind=args.kind))
        elif args.command == 'show':
            with open_index(root) as con:
                identity = resolve(con, args.id)
                row = con.execute('SELECT path FROM documents WHERE id=?', (identity,)).fetchone()
            if not row:
                raise ValueError('Запись не найдена')
            print((root / row['path']).read_text(encoding='utf-8'))
        elif args.command == 'related':
            print(json.dumps(graph_neighbors(root, args.id, args.depth), ensure_ascii=False, indent=2))
        elif args.command == 'export-neo4j':
            output = args.output.resolve() if args.output else root / 'exports' / 'neo4j'
            n, e = export_neo4j(root, output)
            print(f'{output}: {n} узлов, {e} связей. Экспорт создан; Neo4j не запускался.')
        elif args.command == 'stats':
            with open_index(root) as con:
                for row in con.execute('SELECT kind,status,scope,count(*) AS n FROM documents GROUP BY kind,status,scope ORDER BY kind,status,scope'):
                    print(f"{row['kind']:10} {row['status']:10} {row['scope']:15} {row['n']}")
                print('Связей:', con.execute('SELECT count(*) FROM relations').fetchone()[0])
        else:
            import preservation
            import semantic
            handler = preservation if args.command in preservation.COMMANDS else semantic
            print(json.dumps(handler.run(root, args), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print('Ошибка: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
