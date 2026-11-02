"""OpenRouter multilingual embeddings, Qdrant retrieval and evaluation."""
import argparse
import fcntl
import json
import math
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from search_backend import Config, Embedder, Qdrant, MANIFEST_ID

COMMANDS = ('semantic-index', 'semantic-search', 'semantic-status', 'semantic-prune', 'eval-search')
CHUNK_FORMAT = 'chars-2000-overlap-200-v1'


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


def chunks(sources):
    import science
    for src in sources:
        for start in range(0, len(src['text']), 1800):
            end = min(start + 2000, len(src['text']))
            text = src['text'][start:end]
            if text.strip():
                locator = src['path'] + ':L' + str(src['text'][:start].count('\n') + 1)
                identity = json.dumps([src['document'], src['path'], start, end], ensure_ascii=False)
                yield dict(src, kind='chunk', text=text, text_hash=science.digest(text),
                           point_id=str(uuid.uuid5(uuid.NAMESPACE_URL, identity)),
                           locator=locator, start_char=start, end_char=end)
            if end == len(src['text']):
                break


@contextmanager
def writer_lock(root):
    folder = root / 'data'; folder.mkdir(exist_ok=True)
    with (folder / 'semantic.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Индексация уже запущена в этом репозитории') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def build(root, with_sources=False):
    with writer_lock(root):
        return _build(root, with_sources)


def _build(root, with_sources):
    config = Config.load(root)
    store = Qdrant(config)
    sources, signature = corpus(root, with_sources)
    aliases = store.aliases()  # Check the DB before sending any paid API requests.
    if config.collection in aliases:
        old = store.manifest(aliases[config.collection])
        if (old['fingerprint'] == signature and old['embedding'] == config.identity()
                and old['chunk_format'] == CHUNK_FORMAT):
            return {'chunks': old['chunks'], 'collection': aliases[config.collection],
                    'model': config.model, 'new_embeddings': 0, 'unchanged': True}
    entries = list(chunks(sources))
    if not entries:
        raise ValueError('Нет текста для семантического индекса')
    collection = config.collection + '_build_' + uuid.uuid4().hex
    embedder = Embedder(root, config)
    # A generation remains invisible until every batch and the manifest are durable.
    store.create(collection)
    try:
        for offset in range(0, len(entries), 64):
            batch = entries[offset:offset + 64]
            vectors = embedder.encode([row['text'] for row in batch])
            store.upload(collection, [{'id': row['point_id'], 'vector': vector, 'payload': row}
                                      for row, vector in zip(batch, vectors)])
        count = store.call('/collections/' + collection + '/points/count', 'POST', {'exact': True})['count']
        if count != len(entries):
            raise ValueError('Qdrant: неполное поколение индекса')
        _, current = corpus(root, with_sources)
        if current != signature:
            raise ValueError('Исходники изменились во время индексации; повторите semantic-index')
        metadata = {'kind': 'science-manifest', 'owner': config.collection, 'fingerprint': signature,
                    'with_sources': with_sources, 'chunks': len(entries), 'embedding': config.identity(),
                    'documents': sorted({row['document'] for row in entries}), 'chunk_format': CHUNK_FORMAT,
                    'built_at': datetime.now(timezone.utc).isoformat()}
        store.upload(collection, [{'id': MANIFEST_ID, 'vector': {}, 'payload': metadata}])
    except Exception:
        # Only this unpublished generation is ours to delete. Never delete after an
        # ambiguous alias-update timeout: it may already have become the live index.
        try:
            store.call('/collections/' + collection, 'DELETE')
        except ValueError:
            pass
        raise
    store.switch(collection)
    return {'chunks': len(entries), 'documents': len(metadata['documents']), 'collection': collection,
            'model': config.model, 'dimension': config.dimension, 'with_sources': with_sources,
            'new_embeddings': embedder.new_embeddings, 'api_requests': embedder.requests,
            'prompt_tokens': embedder.prompt_tokens, 'unchanged': False}


def status(root):
    config = Config.load(root)
    store = Qdrant(config)
    collection = store.aliases().get(config.collection)
    if not collection:
        raise ValueError('Сначала выполните semantic-index')
    metadata = store.manifest(collection)
    _, signature = corpus(root, metadata['with_sources'])
    return {'collection': collection, 'alias': config.collection, 'metadata': metadata,
            'current': signature == metadata['fingerprint'] and metadata['embedding'] == config.identity()
                       and metadata['chunk_format'] == CHUNK_FORMAT}


def prune(root):
    """Explicit maintenance; retain all aliased generations, including previous."""
    with writer_lock(root):
        config = Config.load(root)
        store = Qdrant(config)
        active = set(store.aliases().values())
        removed = []
        for item in store.call('/collections')['collections']:
            name = item['name']
            if name in active or not name.startswith(config.collection + '_build_'):
                continue
            try:
                metadata = store.manifest(name)
            except ValueError:
                continue
            if metadata.get('owner') == config.collection:
                store.call('/collections/' + name, 'DELETE')
                removed.append(name)
        return {'removed': removed, 'retained': sorted(active)}


def rrf(rankings, k=60):
    scores = {}
    for ranking in rankings:
        for position, identity in enumerate(dict.fromkeys(ranking), 1):
            scores[identity] = scores.get(identity, 0) + 1 / (k + position)
    return sorted(scores, key=lambda key: (-scores[key], key)), scores


class Searcher:
    def __init__(self, root):
        self.root = root
        self.config = Config.load(root)
        self.store = Qdrant(self.config)
        current = status(root)
        if not current['current']:
            raise ValueError('Семантический индекс устарел или модель изменилась: выполните semantic-index')
        # Pin the physical generation so an alias switch cannot mix model spaces.
        self.collection = current['collection']
        self.metadata = current['metadata']
        self.embedder = Embedder(root, self.config)

    def search(self, query, limit=10, hybrid=False):
        import re
        import science
        if not query.strip() or not 1 <= limit <= 1000:
            raise ValueError('Нужны непустой запрос и limit от 1 до 1000')
        vector = self.embedder.encode([query])[0]
        groups = self.store.groups(self.collection, vector, max(100, limit) if hybrid else limit)
        best = {group['id']: dict(group['hits'][0]['payload'], cosine=group['hits'][0]['score'])
                for group in groups}
        ranking = list(best)
        if hybrid:
            terms = re.findall(r'[^\W_]+', query, re.UNICODE)
            literal = ' OR '.join('"' + t + '"' for t in terms)
            lexical = science.query_documents(self.root, argparse.Namespace(fts=True, limit=max(100, limit)), literal) if literal else []
            ranking, scores = rrf([ranking, [r['id'] for r in lexical]])
            for row in lexical:
                best.setdefault(row['id'], {'document': row['id'], 'title': row['title'], 'locator': row['path'],
                                            'source': row['source'], 'text': row['excerpt']})
            for identity in ranking:
                best[identity]['rrf'] = scores[identity]
        return [{key: value for key, value in best[identity].items()
                 if key not in ('kind', 'point_id', 'text_hash', 'path', 'start_char', 'end_char')}
                for identity in ranking[:limit]]


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
    known = set(searcher.metadata['documents'])
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
    report = {'k': k, 'queries': len(cases), 'corpus_documents': len(known), 'embedding': searcher.config.identity(), 'backend': 'qdrant',
              'fingerprint': searcher.metadata['fingerprint'], 'chunk_format': CHUNK_FORMAT,
              'built_at': searcher.metadata['built_at'],
              'dataset_sha256': science.digest(file.read_text()), 'average': average, 'details': details,
              'limitation': 'Small curated development set; not an independent generalization benchmark.'}
    output = root / 'exports/search-evaluation.json'; output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def setup_parser(sub):
    q = sub.add_parser('semantic-index'); q.add_argument('--with-sources', action='store_true')
    q = sub.add_parser('semantic-search'); q.add_argument('query'); q.add_argument('--limit', type=int, default=10); q.add_argument('--hybrid', action='store_true')
    sub.add_parser('semantic-status')
    sub.add_parser('semantic-prune')
    q = sub.add_parser('eval-search'); q.add_argument('--queries', type=Path); q.add_argument('--k', type=int, default=3)


def run(root, args):
    if args.command == 'semantic-index':
        return build(root, args.with_sources)
    if args.command == 'semantic-search':
        return Searcher(root).search(args.query, args.limit, args.hybrid)
    if args.command == 'semantic-status':
        return status(root)
    if args.command == 'semantic-prune':
        return prune(root)
    return evaluate(root, args.queries or root / 'evals/search-queries.json', args.k)
