"""Optional local E5 embeddings, hybrid ranking and retrieval evaluation."""
import argparse
import json
import math
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MODEL = 'intfloat/multilingual-e5-small'
REVISION = '614241f622f53c4eeff9890bdc4f31cfecc418b3'
COMMANDS = ('semantic-index', 'semantic-search', 'eval-search')


def deps():
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise ValueError('Семантический поиск требует .venv: uv pip install --python .venv/bin/python -r requirements-semantic.txt') from exc
    return np, SentenceTransformer


def get_model(root):
    _, constructor = deps()
    return constructor(MODEL, revision=REVISION, device='cpu', trust_remote_code=False, cache_folder=str(root / 'data/models'))


def corpus(root, with_sources=False):
    import science
    import preservation
    docs, vocab = science.collect(root)
    sources = []
    for record, path, review in docs:
        title = record['title']
        terms = [name for category in ('topics', 'methods', 'datasets') for term in record[category] for name in vocab[category][term]]
        sources.append({'document': record['id'], 'title': title, 'path': path.replace('review.md', 'record.json'),
                        'text': title + '\n' + record['summary'] + '\n' + ' '.join(terms), 'source': record['source']})
        sources.append({'document': record['id'], 'title': title, 'path': path, 'text': review, 'source': record['source']})
        for claim in record['claims']:
            sources.append({'document': record['id'], 'title': title, 'path': path.replace('review.md', 'record.json') + '#' + claim['id'],
                            'text': claim['text'], 'source': claim['source'] + (' [' + claim['locator'] + ']' if claim['locator'] else '')})
    snapshots = []
    if with_sources:
        preservation.check_sources(root)
        for item in preservation.load_manifest(root)['snapshots']:
            path = preservation.safe_local(root, item['text_path'])
            if path.exists():
                snapshots.append([item['id'], item['text_sha256']])
                sources.append({'document': item['document'], 'title': item['document'], 'path': item['text_path'],
                                'text': path.read_text(encoding='utf-8'), 'source': item['resolved_url'] + ' version=' + item['version']})
    signature = science.digest(json.dumps([science.fingerprint(docs, vocab), snapshots, with_sources], sort_keys=True))
    return sources, signature


def chunks(sources, tokenizer):
    import science
    for src in sources:
        offsets = tokenizer(src['text'], add_special_tokens=False, return_offsets_mapping=True, truncation=False, verbose=False)['offset_mapping']
        for start in range(0, len(offsets), 340):
            end = min(start + 400, len(offsets))
            lo, hi = offsets[start][0], offsets[end - 1][1]
            text = src['text'][lo:hi]
            yield dict(src, text=text, text_hash=science.digest(text),
                       locator=src['path'] + ':L' + str(src['text'][:lo].count('\n') + 1),
                       start_char=lo, end_char=hi)
            if end == len(offsets):
                break


def build(root, with_sources=False):
    np, _ = deps()
    sources, signature = corpus(root, with_sources)
    model = get_model(root)
    entries = list(chunks(sources, model.tokenizer))
    destination = root / 'data/semantic.sqlite3'
    destination.parent.mkdir(parents=True, exist_ok=True)
    cache = {}
    if destination.exists():
        with sqlite3.connect(destination) as old:
            metadata = dict(old.execute('SELECT key,value FROM metadata'))
            if metadata.get('revision') == REVISION and metadata.get('model') == MODEL:
                cache = dict(old.execute('SELECT text_hash,vector FROM chunks'))
    missing = {e['text_hash']: e['text'] for e in entries if e['text_hash'] not in cache}
    if missing:
        keys = list(missing)
        vectors = model.encode(['passage: ' + missing[k] for k in keys], batch_size=16, normalize_embeddings=True,
                               convert_to_numpy=True, show_progress_bar=False)
        for key, vector in zip(keys, vectors):
            cache[key] = np.asarray(vector, dtype='<f4').tobytes()
    fd, temporary = tempfile.mkstemp(prefix='semantic-', suffix='.sqlite3', dir=destination.parent)
    os.close(fd)
    try:
        with sqlite3.connect(temporary) as con:
            con.executescript('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE chunks(id INTEGER PRIMARY KEY,document TEXT,title TEXT,locator TEXT,source TEXT,text TEXT,text_hash TEXT,vector BLOB);')
            with con:
                con.executemany('INSERT INTO metadata VALUES (?,?)', {
                    'model': MODEL, 'revision': REVISION, 'fingerprint': signature, 'with_sources': str(with_sources),
                    'dimension': '384', 'built_at': datetime.now(timezone.utc).isoformat(), 'format': 'e5-400tokens-60overlap-v1'}.items())
                con.executemany('INSERT INTO chunks(document,title,locator,source,text,text_hash,vector) VALUES (?,?,?,?,?,?,?)',
                                [(e['document'], e['title'], e['locator'], e['source'], e['text'], e['text_hash'], cache[e['text_hash']]) for e in entries])
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {'chunks': len(entries), 'new_embeddings': len(missing), 'model': MODEL, 'revision': REVISION, 'with_sources': with_sources}


def rrf(rankings, k=60):
    scores = {}
    for ranking in rankings:
        for position, identity in enumerate(dict.fromkeys(ranking), 1):
            scores[identity] = scores.get(identity, 0) + 1 / (k + position)
    return sorted(scores, key=lambda key: (-scores[key], key)), scores


class Searcher:
    def __init__(self, root):
        self.root = root
        self.np, _ = deps()
        path = root / 'data/semantic.sqlite3'
        if not path.exists():
            raise ValueError('Сначала выполните semantic-index')
        with sqlite3.connect(path) as con:
            con.row_factory = sqlite3.Row
            metadata = {r['key']: r['value'] for r in con.execute('SELECT * FROM metadata')}
            if metadata['model'] != MODEL or metadata['revision'] != REVISION:
                raise ValueError('Модель индекса изменилась: выполните semantic-index')
            _, current = corpus(root, metadata['with_sources'] == 'True')
            if current != metadata['fingerprint']:
                raise ValueError('Семантический индекс устарел: выполните semantic-index')
            self.rows = [dict(r) for r in con.execute('SELECT * FROM chunks ORDER BY id')]
        self.vectors = self.np.stack([self.np.frombuffer(r.pop('vector'), dtype='<f4') for r in self.rows]) if self.rows else self.np.zeros((0,384))
        self.model = get_model(root)

    def search(self, query, limit=10, hybrid=False):
        import science
        if not query.strip() or not 1 <= limit <= 1000:
            raise ValueError('Нужны непустой запрос и limit от 1 до 1000')
        vector = self.model.encode(['query: ' + query], normalize_embeddings=True, convert_to_numpy=True)[0]
        order = self.np.argsort(-(self.vectors @ vector))
        best = {}
        for i in order:
            row = self.rows[int(i)]
            if row['document'] not in best:
                best[row['document']] = dict(row, cosine=float(self.vectors[int(i)] @ vector))
            if len(best) >= max(100, limit):
                break
        ranking = list(best)
        if hybrid:
            # OR-based lexical candidate retrieval complements semantic candidates.
            import re
            terms = re.findall(r'[^\W_]+', query, re.UNICODE)
            literal = ' OR '.join('"' + t + '"' for t in terms)
            lexical = science.query_documents(self.root, argparse.Namespace(fts=True, limit=max(100, limit)), literal) if literal else []
            ranking, scores = rrf([ranking, [r['id'] for r in lexical]])
            for r in lexical:
                best.setdefault(r['id'], {'document': r['id'], 'title': r['title'], 'locator': r['path'], 'source': r['source'], 'text': r['excerpt']})
            for identity in ranking:
                best[identity]['rrf'] = scores[identity]
        return [best[key] for key in ranking[:limit]]


def metrics(ranked, relevant, k):
    hits = [i + 1 for i, key in enumerate(ranked[:k]) if key in relevant]
    return {'recall': len(hits) / len(relevant), 'mrr': 1 / hits[0] if hits else 0,
            'ndcg': sum(1 / math.log2(i + 1) for i in hits) / sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))}


def evaluate(root, file, k=3):
    import science
    cases = json.loads(file.read_text(encoding='utf-8'))
    if not cases or k < 1:
        raise ValueError('Нужен непустой набор запросов и k>=1')
    searcher = Searcher(root)
    details = []
    known = {r['document'] for r in searcher.rows}
    for case in cases:
        relevant = set(case['relevant'])
        if not relevant or not relevant <= known:
            raise ValueError('Некорректная разметка relevant')
        baseline = science.query_documents(root, argparse.Namespace(fts=False,limit=k), case['query'])
        import re
        or_query = ' OR '.join('"' + t + '"' for t in re.findall(r'[^\W_]+', case['query'], re.UNICODE))
        baseline_or = science.query_documents(root, argparse.Namespace(fts=True,limit=k), or_query)
        rankings = {'lexical': [r['id'] for r in baseline],
                    'lexical_or': [r['id'] for r in baseline_or],
                    'semantic': [r['document'] for r in searcher.search(case['query'], k)],
                    'hybrid': [r['document'] for r in searcher.search(case['query'], k, True)]}
        details.append({'query': case['query'], 'relevant': sorted(relevant), 'rankings': rankings,
                        'metrics': {name: metrics(ranks, relevant, k) for name, ranks in rankings.items()}})
    average = {name: {metric: sum(d['metrics'][name][metric] for d in details) / len(details)
                      for metric in ('recall', 'mrr', 'ndcg')} for name in ('lexical', 'lexical_or', 'semantic', 'hybrid')}
    report = {'k': k, 'queries': len(cases), 'corpus_documents': len(known), 'model': MODEL, 'revision': REVISION,
              'dataset_sha256': science.digest(file.read_text()), 'average': average, 'details': details,
              'limitation': 'Small curated development set; not an independent generalization benchmark.'}
    output = root / 'exports/search-evaluation.json'; output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def setup_parser(sub):
    q = sub.add_parser('semantic-index'); q.add_argument('--with-sources', action='store_true')
    q = sub.add_parser('semantic-search'); q.add_argument('query'); q.add_argument('--limit', type=int, default=10); q.add_argument('--hybrid', action='store_true')
    q = sub.add_parser('eval-search'); q.add_argument('--queries', type=Path); q.add_argument('--k', type=int, default=3)


def run(root, args):
    if args.command == 'semantic-index':
        return build(root, args.with_sources)
    if args.command == 'semantic-search':
        return Searcher(root).search(args.query, args.limit, args.hybrid)
    return evaluate(root, args.queries or root / 'evals/search-queries.json', args.k)
