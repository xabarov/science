import argparse
import contextlib
import csv
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import science


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copy(science.ROOT / 'vocab.json', self.root)
        shutil.copytree(science.ROOT / 'templates', self.root / 'templates')

    def add(self, identity='arxiv:2610.12345', title='Исследование неопределённости', kind='paper'):
        return science.create_record(self.root, identity, title, kind, year=2026)

    def patch(self, folder, **values):
        path = folder / 'record.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        record.update(values)
        path.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')

    def search(self, query, **kwargs):
        args = argparse.Namespace(fts=False, limit=20, **kwargs)
        return science.query_documents(self.root, args, query)

    def edge(self, target):
        return {'type': 'cites', 'target': target, 'status': 'verified',
                'source': 'https://example.org/paper', 'locator': 'Section 2',
                'note': 'Проверенная ссылка, с запятой\nи переносом строки'}

    def test_identifiers_and_versions(self):
        for value in ('2610.12345v3', 'https://arxiv.org/pdf/2610.12345v3.pdf',
                      'https://arxiv.org/html/2610.12345v2', 'https://huggingface.co/papers/2610.12345'):
            self.assertEqual(science.canonical_id(value), 'arxiv:2610.12345')
        self.assertEqual(science.canonical_id('https://doi.org/10.1234/ABC'), 'doi:10.1234/abc')
        self.assertEqual(science.canonical_id('hep-th/9901001v2'), 'arxiv:hep-th/9901001')
        with self.assertRaises(ValueError):
            science.canonical_id('../../outside')

    def test_duplicate_version_does_not_overwrite(self):
        folder = self.add()
        with self.assertRaisesRegex(ValueError, 'уже существует'):
            self.add('arxiv:2610.12345v2', 'Другое название')
        self.assertIn('Исследование', (folder / 'review.md').read_text())

    def test_alias_collision_rejected(self):
        a = self.add()
        b = self.add('doi:10.1234/test', 'Published version')
        self.patch(a, aliases=['doi:10.1234/test'])
        with self.assertRaisesRegex(ValueError, 'Дубликат'):
            science.collect(self.root)

    def test_unicode_and_synonyms(self):
        folder = self.add()
        self.patch(folder, topics=['uncertainty-calibration'], methods=['mopps'])
        (folder / 'review.md').write_text('Калибровка вероятностей при смене домена.', encoding='utf-8')
        science.rebuild(self.root)
        for query in ('КАЛИБРОВКА', 'uncertainty', 'MoPPS', 'смене домена'):
            self.assertEqual(len(self.search(query)), 1, query)
        self.assertEqual(len(self.search('домен')), 0)  # No hidden Russian stemming.
        args = argparse.Namespace(fts=True, limit=20)
        self.assertEqual(len(science.query_documents(self.root, args, 'домен*')), 1)

    def test_combined_filters_and_claims(self):
        a = self.add()
        b = self.add('arxiv:2610.11111', 'Other calibration paper')
        self.patch(a, topics=['uncertainty-calibration'], claims=[{
            'id': 'c1', 'text': 'Reported observation', 'epistemic_status': 'reported_result',
            'verification': 'source_checked', 'source': 'https://example.org', 'locator': 'Table 1'}])
        science.rebuild(self.root)
        args = argparse.Namespace(limit=20, kind='paper', topic='uncertainty-calibration',
                                  year=2026, epistemic='reported_result', status='inbox')
        rows = science.query_documents(self.root, args)
        self.assertEqual([r['id'] for r in rows], ['arxiv:2610.12345'])

    def test_invalid_evidence_keeps_previous_index(self):
        folder = self.add()
        science.rebuild(self.root)
        self.patch(folder, claims=[{'id': 'c1', 'text': 'Unsupported result',
                                   'epistemic_status': 'reported_result', 'verification': 'source_checked',
                                   'source': '', 'locator': ''}])
        with self.assertRaisesRegex(ValueError, 'source и locator'):
            science.rebuild(self.root)
        self.assertEqual(len(self.search('Исследование')), 1)

    def test_transaction_rolls_back_on_write_failure(self):
        folder = self.add()
        science.rebuild(self.root)
        path = self.root / 'data' / 'catalog.sqlite3'
        with sqlite3.connect(path) as con:
            con.execute("CREATE TRIGGER reject_new BEFORE INSERT ON documents BEGIN SELECT RAISE(ABORT,'simulated failure'); END")
        self.patch(folder, title='Changed title')
        with self.assertRaises(sqlite3.IntegrityError):
            science.rebuild(self.root)
        self.assertEqual(len(self.search('Исследование')), 1)
        self.assertEqual(len(self.search('Changed')), 0)

    def test_reindex_updates_and_deletes(self):
        a = self.add()
        b = self.add('arxiv:2610.11111', 'Oldtitle')
        science.rebuild(self.root)
        science.rebuild(self.root)
        self.assertEqual(len(science.query_documents(self.root, argparse.Namespace(limit=20))), 2)
        self.patch(a, title='Newtitle')
        shutil.rmtree(b)
        science.rebuild(self.root)
        self.assertEqual(len(self.search('Oldtitle')), 0)
        self.assertEqual(len(self.search('Newtitle')), 1)

    def test_alias_resolution_and_external_graph(self):
        a = self.add()
        b = self.add('arxiv:2610.11111', 'Second')
        self.patch(b, aliases=['doi:10.1234/second'], relations=[self.edge('arxiv:2610.99999')])
        self.patch(a, relations=[self.edge('doi:10.1234/second')])
        science.rebuild(self.root)
        result = science.graph_neighbors(self.root, 'arxiv:2610.12345', 2)
        self.assertEqual({r['id'] for r in result['neighbors']}, {'arxiv:2610.11111', 'arxiv:2610.99999'})
        inverse = science.graph_neighbors(self.root, 'doi:10.1234/second')
        self.assertEqual(inverse['id'], 'arxiv:2610.11111')
        self.assertIn('arxiv:2610.12345', {r['id'] for r in inverse['neighbors']})

    def test_export_csv_escaping_and_no_duplicate_alias_nodes(self):
        a = self.add(title='Название, "с кавычками"\nи переносом')
        b = self.add('arxiv:2610.11111', 'Second')
        self.patch(b, aliases=['doi:10.1234/second'])
        self.patch(a, topics=['conditional-computation'], relations=[self.edge('doi:10.1234/second'), self.edge('arxiv:2610.99999')])
        output = self.root / 'exports'
        n, e = science.export_neo4j(self.root, output)
        with (output / 'nodes.csv').open(encoding='utf-8', newline='') as handle:
            nodes = list(csv.DictReader(handle))
        self.assertEqual(n, 4)
        self.assertEqual(e, 3)
        self.assertNotIn('doi:10.1234/second', {r['id'] for r in nodes})
        node = next(r for r in nodes if r['id'] == 'arxiv:2610.12345')
        self.assertEqual(node['title'], 'Название, "с кавычками"\nи переносом')
        self.assertEqual(next(r for r in nodes if r['id'] == 'arxiv:2610.99999')['stub'], 'true')
        with (output / 'edges.csv').open(encoding='utf-8', newline='') as handle:
            edges = list(csv.DictReader(handle))
        self.assertEqual(next(r for r in edges if r['type'] == 'cites')['note'], self.edge('')['note'])
        science.export_neo4j(self.root, output)
        with (output / 'edges.csv').open(encoding='utf-8', newline='') as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 3)

    def test_stale_index_detects_body_and_vocab_changes(self):
        folder = self.add()
        science.rebuild(self.root)
        base = ['--root', str(self.root), 'check']
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(science.main(base), 0)
            (folder / 'review.md').write_text('Changed content')
            self.assertEqual(science.main(base), 1)
            science.rebuild(self.root)
            self.assertEqual(science.main(base), 0)
            vocab = json.loads((self.root / 'vocab.json').read_text())
            vocab['topics']['active-learning'].append('Extra synonym')
            (self.root / 'vocab.json').write_text(json.dumps(vocab))
            self.assertEqual(science.main(base), 1)

    def test_unknown_term_and_missing_review(self):
        a = self.add()
        self.patch(a, topics=['typo-topic'])
        with self.assertRaisesRegex(ValueError, 'неизвестное понятие'):
            science.collect(self.root)
        self.patch(a, topics=[])
        (a / 'review.md').unlink()
        with self.assertRaisesRegex(ValueError, 'отсутствует review.md'):
            science.collect(self.root)

    def test_own_notes_are_searchable(self):
        for kind in ('topic', 'idea', 'synthesis'):
            self.add(kind + ':test', 'Ownnote ' + kind, kind)
        science.rebuild(self.root)
        self.assertEqual(len(self.search('Ownnote')), 3)
        self.assertTrue((self.root / 'notes' / 'syntheses' / 'synthesis-test').is_dir())

    def test_literal_query_handles_punctuation_and_sql_injection(self):
        folder = self.add(title='System-1 and OOD')
        (folder / 'review.md').write_text('Body without identifiers')
        science.rebuild(self.root)
        self.assertEqual(len(self.search('System-1')), 1)
        self.assertEqual(len(self.search('2610.12345')), 1)
        self.assertEqual(len(self.search("'; DROP TABLE documents; --")), 0)
        self.assertEqual(len(self.search('OOD')), 1)


if __name__ == '__main__':
    unittest.main()
