"""Research entities, evidence, novelty audits and dependency review."""
import hashlib
import json
from datetime import datetime, timezone

STAGES = {
    'idea': {'proposed': ['literature_checked', 'rejected'], 'literature_checked': ['planned', 'rejected'],
             'planned': ['running', 'rejected'], 'running': ['supported', 'rejected', 'inconclusive'],
             'supported': ['proposed'], 'rejected': ['proposed'], 'inconclusive': ['planned', 'proposed']},
    'question': {'open': ['investigating'], 'investigating': ['answered', 'open'], 'answered': ['open']},
    'experiment': {'planned': ['running', 'abandoned'], 'running': ['completed', 'abandoned'],
                   'completed': [], 'abandoned': ['planned']},
}
LINK_TYPES = ('supports', 'contradicts', 'depends_on', 'qualifies', 'tests')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def extension(record):
    return record['extra'].get('research', {})


def split_ref(ref):
    parts = ref.split('#', 1)
    return parts[0], parts[1] if len(parts) == 2 else None


def validate(docs, canonical_id):
    records = {r['id']: r for r, _, _ in docs}
    aliases = {a: r['id'] for r, _, _ in docs for a in r['aliases']}

    def need(ok, message):
        if not ok:
            raise ValueError(message)

    def reference(ref):
        need(isinstance(ref, str), 'Ссылка должна быть строкой')
        owner, claim = split_ref(ref)
        need(owner == canonical_id(owner), 'Неканоническая ссылка: ' + ref)
        owner = aliases.get(owner, owner)
        need(owner in records, 'Отсутствует локальное основание: ' + ref)
        if claim is not None:
            need(any(c['id'] == claim for c in records[owner]['claims']), 'Отсутствует утверждение: ' + ref)

    for record, _, _ in docs:
        e = extension(record)
        need(isinstance(e, dict), record['id'] + ': extra.research должен быть объектом')
        if record['kind'] in STAGES:
            need(e.get('stage', next(iter(STAGES[record['kind']]))) in STAGES[record['kind']], 'Неизвестная стадия')
        for key in ('claim_links', 'measurements', 'novelty_checks', 'depends_on', 'history'):
            need(isinstance(e.get(key, []), list), key + ' должен быть массивом')
        for ref in e.get('depends_on', []):
            reference(ref)
            need(split_ref(ref)[0] != record['id'], 'Зависимость от собственной записи')
        for edge in e.get('claim_links', []):
            need(set(edge) == {'from', 'to', 'type', 'status', 'source', 'locator', 'note'}, 'Неверные поля claim_links')
            need(all(isinstance(v, str) for v in edge.values()), 'Поля claim_links должны быть строками')
            reference(edge['from']); reference(edge['to'])
            need('#' in edge['from'] and '#' in edge['to'], 'Связь утверждений требует #claim с обеих сторон')
            need(split_ref(edge['from'])[0] == record['id'], 'from должен принадлежать текущей записи')
            need(edge['from'] != edge['to'] and edge['type'] in LINK_TYPES, 'Неверная связь утверждений')
            need(edge['status'] in ('verified', 'inferred', 'hypothesis') and edge['note'].strip(), 'Нужно основание связи')
            if edge['status'] == 'verified':
                need(edge['source'] and edge['locator'], 'Проверенная связь требует source/locator')
        for measurement in e.get('measurements', []):
            required = {'id', 'claim', 'group', 'model', 'train_data', 'test_data', 'method', 'metric', 'unit',
                        'value', 'baseline', 'budget', 'uncertainty', 'source', 'locator', 'conditions'}
            need(set(measurement) == required, 'Неверные поля measurements')
            need(type(measurement['value']) in (float, int), 'Численное значение value обязательно')
            import math
            need(math.isfinite(measurement['value']), 'value должен быть конечным числом')
            need(isinstance(measurement['budget'], dict) and isinstance(measurement['conditions'], dict), 'budget/conditions должны быть объектами')
            for key in required - {'value', 'budget', 'conditions'}:
                need(isinstance(measurement[key], str) and bool(measurement[key].strip()), 'Пустое поле измерения: ' + key)
            reference(measurement['claim'])
        for check in e.get('novelty_checks', []):
            need(set(check) == {'date', 'provider', 'query', 'url', 'scope', 'closest', 'difference', 'conclusion'}, 'Неверные поля novelty_checks')
            from datetime import date
            date.fromisoformat(check['date'])
            need(isinstance(check['closest'], list), 'closest должен быть массивом')
            for ref in check['closest']:
                reference(ref)
            need(all(isinstance(check[k], str) and check[k].strip() for k in check if k != 'closest'), 'Неполная проверка новизны')
        if e.get('stage') == 'literature_checked':
            need(bool(e.get('novelty_checks')), 'literature_checked требует журнал поиска')
        if record['kind'] == 'experiment':
            protocol = e.get('protocol', {})
            need(isinstance(protocol, dict), 'protocol должен быть объектом')
            if e.get('stage', 'planned') in ('running', 'completed'):
                for key in ('hypothesis', 'baselines', 'primary_metric', 'budget', 'falsification', 'seeds'):
                    need(bool(protocol.get(key)), 'Не заполнен protocol.' + key)
                reference(protocol['hypothesis'])
                need(protocol.get('ready') is True, 'Протокол не отмечен как готовый')
                need(isinstance(protocol['budget'], dict) and type(protocol['budget'].get('amount')) in (int, float)
                     and protocol['budget']['amount'] > 0 and protocol['budget'].get('unit'), 'Нужен численный бюджет и единица')
                need(isinstance(protocol['seeds'], list) and all(type(seed) is int for seed in protocol['seeds']), 'Нужны точные seeds')
            if e.get('stage') == 'completed':
                need(e.get('measurements') and e.get('artifacts') and e.get('outcome') in ('supported', 'rejected', 'inconclusive'),
                     'Завершённому эксперименту нужны measurements, artifacts и outcome')
    # Dependency cycles make ordered review impossible; citations can remain cyclic.
    graph = {r['id']: {aliases.get(split_ref(dep)[0], split_ref(dep)[0]) for dep in dependencies(r)
                       if split_ref(dep)[0] != r['id']} for r, _, _ in docs}
    visiting, visited = set(), set()
    def visit(key):
        need(key not in visiting, 'Цикл зависимостей: ' + key)
        if key in visited:
            return
        visiting.add(key)
        for parent in graph.get(key, set()):
            visit(parent)
        visiting.remove(key); visited.add(key)
    for key in graph:
        visit(key)


def catalog(root):
    import science
    docs, _ = science.collect(root)
    return docs, {r['id']: (r, path, body) for r, path, body in docs}


def normalize_ref(ref, records):
    owner, claim = split_ref(ref)
    for r, _, _ in records.values():
        if owner in r['aliases']:
            owner = r['id']
            break
    return owner + ('#' + claim if claim else '')


def ref_hash(ref, records, root=None):
    ref = normalize_ref(ref, records)
    owner, cid = split_ref(ref)
    if owner not in records:
        return 'missing'
    record, path, body = records[owner]
    snapshots = []
    if root is not None:
        from preservation import load_manifest
        snapshots = sorted((s['version'], s['sha256']) for s in load_manifest(root)['snapshots'] if s['document'] == owner)
    if cid:
        item = next((c for c in record['claims'] if c['id'] == cid), None)
        # Changes to source version also require a review of the claim.
        return sha([item, record['source_version'], snapshots]) if item else 'missing'
    return sha([record, body, snapshots])


def dependencies(record):
    e = extension(record)
    refs = list(e.get('depends_on', []))
    refs += [edge['to'] for edge in e.get('claim_links', []) if edge['type'] == 'depends_on']
    return sorted(set(refs))


def save(root, path, record):
    file = root / path
    target = file.with_name('record.json')
    record['updated'] = datetime.now(timezone.utc).date().isoformat()
    tmp = target.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(target)


def review_queue(root):
    _, records = catalog(root)
    reasons = {}
    for identity, (r, _, _) in records.items():
        state = extension(r).get('dependency_state', {})
        for ref in dependencies(r):
            if state.get(ref) != ref_hash(ref, records, root):
                reasons.setdefault(identity, []).append({'target': ref, 'reason': 'changed' if ref in state else 'not_reviewed'})
    # Transitive dependencies are flagged even when the intermediate review text is unchanged.
    while True:
        added = False
        for identity, (r, _, _) in records.items():
            if identity in reasons:
                continue
            affected = [ref for ref in dependencies(r) if split_ref(normalize_ref(ref, records))[0] in reasons]
            if affected:
                reasons[identity] = [{'target': ref, 'reason': 'upstream_needs_review'} for ref in affected]
                added = True
        if not added:
            break
    return [{'id': key, 'title': records[key][0]['title'], 'reasons': value} for key, value in sorted(reasons.items())]


def impact(root, ref):
    _, records = catalog(root)
    ref = normalize_ref(ref, records)
    owner, _ = split_ref(ref)
    affected = {ref}
    result = []
    while True:
        added = False
        for key, (r, _, _) in records.items():
            if key in affected or key == owner:
                continue
            hits = [dep for dep in dependencies(r) if normalize_ref(dep, records) in affected or
                    (split_ref(normalize_ref(dep, records))[0] in affected)]
            # A document-wide change can affect any of its claims.
            if hits:
                affected.add(key); result.append({'id': key, 'via': hits}); added = True
        if not added:
            break
    return result


def acknowledge(root, identity, note):
    import science
    _, records = catalog(root)
    identity = normalize_ref(science.canonical_id(identity), records)
    if identity not in records:
        raise ValueError('Запись не найдена: ' + identity)
    r, path, _ = records[identity]
    e = r['extra'].setdefault('research', {})
    upstream = {q['id'] for q in review_queue(root)} - {identity}
    if any(split_ref(normalize_ref(dep, records))[0] in upstream for dep in dependencies(r)):
        raise ValueError('Сначала пересмотрите зависимые основания выше по цепочке')
    e['dependency_state'] = {ref: ref_hash(ref, records, root) for ref in dependencies(r)}
    e.setdefault('history', []).append({'at': stamp(), 'action': 'review_ack', 'note': note})
    save(root, path, r)


def transition(root, identity, stage, note):
    import science
    docs, records = catalog(root)
    identity = normalize_ref(science.canonical_id(identity), records)
    if identity not in records:
        raise ValueError('Запись не найдена: ' + identity)
    r, path, body = records[identity]
    if r['kind'] not in STAGES:
        raise ValueError('Стадии предназначены для idea/question/experiment')
    e = r['extra'].setdefault('research', {})
    previous = e.get('stage', next(iter(STAGES[r['kind']])))
    if stage not in STAGES[r['kind']][previous]:
        raise ValueError(f'Недопустимый переход {previous} → {stage}')
    if not note.strip():
        raise ValueError('Нужно объяснение перехода')
    e['stage'] = stage
    e.setdefault('history', []).append({'at': stamp(), 'action': 'stage', 'from': previous, 'to': stage, 'note': note})
    validate(docs, science.canonical_id)
    save(root, path, r)


def evidence_nodes(docs):
    nodes, edges = [], []
    for r, _, _ in docs:
        for c in r['claims']:
            cid = r['id'] + '#' + c['id']
            nodes.append({'id': cid, 'kind': 'claim', 'title': c['text'], 'status': c['epistemic_status'],
                          'scope': c['verification'], 'source': c['source'], 'stub': 'false'})
            edges.append({'id': sha([r['id'], cid]), 'from_id': r['id'], 'to_id': cid, 'type': 'has_claim',
                          'status': c['verification'], 'source': c['source'], 'locator': c['locator'], 'note': ''})
        for edge in extension(r).get('claim_links', []):
            edges.append({'id': sha(edge), 'from_id': edge['from'], 'to_id': edge['to'], 'type': edge['type'],
                          'status': edge['status'], 'source': edge['source'], 'locator': edge['locator'], 'note': edge['note']})
        for dep in extension(r).get('depends_on', []):
            edges.append({'id': sha([r['id'], dep, 'depends_on']), 'from_id': r['id'], 'to_id': dep,
                          'type': 'depends_on', 'status': 'curated', 'source': r['id'], 'locator': 'extra.research.depends_on', 'note': ''})
    return nodes, edges


def setup_parser(sub):
    for name in ('claims', 'compare', 'novelty', 'research-list'):
        q = sub.add_parser(name)
        q.add_argument('--id')
        if name == 'compare':
            q.add_argument('--group'); q.add_argument('--metric'); q.add_argument('--dataset')
        if name == 'research-list':
            q.add_argument('--stage')
    q = sub.add_parser('claim'); q.add_argument('ref')
    q = sub.add_parser('impact'); q.add_argument('ref')
    sub.add_parser('review-queue')
    q = sub.add_parser('review-ack'); q.add_argument('id'); q.add_argument('--note', required=True)
    q = sub.add_parser('stage'); q.add_argument('id'); q.add_argument('stage'); q.add_argument('--note', required=True)


COMMANDS = ('claims', 'claim', 'compare', 'novelty', 'research-list', 'impact', 'review-queue', 'review-ack', 'stage')


def run(root, args):
    if args.command == 'stage':
        transition(root, args.id, args.stage, args.note)
        return {'updated': args.id, 'stage': args.stage, 'next': 'index'}
    if args.command == 'review-ack':
        acknowledge(root, args.id, args.note)
        return {'reviewed': args.id, 'next': 'index'}
    if args.command == 'review-queue':
        return review_queue(root)
    if args.command == 'impact':
        return impact(root, args.ref)
    docs, records = catalog(root)
    if args.command == 'claim':
        ref = normalize_ref(args.ref, records)
        owner, cid = split_ref(ref)
        if owner not in records or not cid:
            raise ValueError('Нужна существующая ссылка document#claim')
        c = next((c for c in records[owner][0]['claims'] if c['id'] == cid), None)
        if c is None:
            raise ValueError('Утверждение не найдено')
        links = [edge for r, _, _ in docs for edge in extension(r).get('claim_links', []) if ref in (edge['from'], edge['to'])]
        return {'ref': ref, 'claim': c, 'links': links}
    rows = []
    for r, _, _ in docs:
        if args.id and r['id'] != normalize_ref(args.id, records):
            continue
        e = extension(r)
        if args.command == 'claims':
            rows.extend(dict(c, ref=r['id'] + '#' + c['id']) for c in r['claims'])
        elif args.command == 'compare':
            for m in e.get('measurements', []):
                if args.group and args.group != m['group'] or args.metric and args.metric != m['metric'] or args.dataset and args.dataset != m['test_data']:
                    continue
                rows.append(dict(m, document=r['id']))
        elif args.command == 'novelty':
            rows.extend(dict(c, document=r['id']) for c in e.get('novelty_checks', []))
        elif args.command == 'research-list' and r['kind'] in STAGES:
            stage = e.get('stage', next(iter(STAGES[r['kind']])))
            if not args.stage or args.stage == stage:
                rows.append({'id': r['id'], 'title': r['title'], 'stage': stage, 'depends_on': dependencies(r)})
    return rows
