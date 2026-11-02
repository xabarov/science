import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import science
import semantic
import search_backend as backend


class BackendTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_env_is_literal_and_environment_wins(self):
        (self.root / '.env').write_text('OPENROUTER_API_KEY=secret\nEMBEDDING_MODEL=baai/bge-m3\nEMBEDDING_VERSION=$(touch /tmp/should-not-run)\n')
        with patch.dict(os.environ, {'EMBEDDING_MODEL': 'test/model'}, clear=True):
            config = backend.Config.load(self.root)
        self.assertEqual(config.model, 'test/model')
        self.assertEqual(config.version, '$(touch /tmp/should-not-run)')
        self.assertNotIn('secret', repr(config))
        (self.root / '.env').write_text('export KEY=value')
        with self.assertRaisesRegex(ValueError, 'KEY=value'):
            backend.Config.load(self.root)

    def test_reordered_response_cache_and_version_isolation(self):
        config = backend.Config(dimension=2, openrouter_key='test-secret')
        result = {'data': [{'index': 1, 'embedding': [0., 1.]}, {'index': 0, 'embedding': [1., 0.]}]}
        with patch('search_backend.request_json', return_value=result) as request:
            first = backend.Embedder(self.root, config)
            self.assertEqual(first.encode(['alpha', 'beta', 'alpha']), [[1., 0.], [0., 1.], [1., 0.]])
            self.assertEqual(backend.Embedder(self.root, config).encode(['beta']), [[0., 1.]])
            self.assertEqual(request.call_count, 1)
        config.version = '2'
        with patch('search_backend.Embedder.remote', return_value=[[1., 1.]]) as remote:
            self.assertEqual(backend.Embedder(self.root, config).encode(['beta']), [[1., 1.]])
            remote.assert_called_once()

    def test_malformed_embeddings_are_not_cached(self):
        embedder = backend.Embedder(self.root, backend.Config(dimension=2, openrouter_key='test'))
        cases = [[], [{'index': 1, 'embedding': [1., 0.]}],
                 [{'index': 0, 'embedding': [float('nan'), 0.]}],
                 [{'index': 0, 'embedding': [0., 0.]}],
                 [{'index': 0, 'embedding': [1.]}]]
        for rows in cases:
            with self.subTest(rows=rows), patch('search_backend.request_json', return_value={'data': rows}):
                with self.assertRaises(ValueError):
                    embedder.encode(['invalid'])
        self.assertEqual(embedder.new_embeddings, 0)

    def test_service_error_does_not_expose_body_key_or_text(self):
        error = HTTPError('https://openrouter.ai/api/v1/embeddings', 401, 'private text', {}, io.BytesIO(b'private key'))
        with patch('search_backend.urlopen', side_effect=error):
            with self.assertRaises(backend.ServiceError) as caught:
                backend.request_json('OpenRouter', 'https://openrouter.ai/api/v1/embeddings',
                                     'POST', {'input': 'private text'}, {'Authorization': 'private key'})
        self.assertNotIn('private', str(caught.exception))
        self.assertEqual(caught.exception.status, 401)

    def test_transient_failure_retries(self):
        error = HTTPError('https://openrouter.ai', 429, 'retry', {}, io.BytesIO())
        with patch('search_backend.urlopen', side_effect=[error, io.BytesIO(b'{"data": []}')]) as call, patch('search_backend.time.sleep'):
            self.assertEqual(backend.request_json('OpenRouter', 'https://openrouter.ai'), {'data': []})
            self.assertEqual(call.call_count, 2)

    def test_chunks_cover_unicode_source_and_stable_locators(self):
        source = {'document': 'test', 'path': 'review.md', 'text': 'Привет\n' * 1000}
        chunks = list(semantic.chunks([source]))
        covered = set()
        for row in chunks:
            self.assertLessEqual(len(row['text']), 2000)
            covered.update(range(row['start_char'], row['end_char']))
        self.assertEqual(covered, set(range(len(source['text']))))
        self.assertEqual(chunks, list(semantic.chunks([source])))
        self.assertEqual(len({row['point_id'] for row in chunks}), len(chunks))


@unittest.skipUnless(os.environ.get('SCIENCE_TEST_QDRANT_URL'), 'Set SCIENCE_TEST_QDRANT_URL for real Qdrant integration tests')
class QdrantIntegrationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        shutil.copy(science.ROOT / 'vocab.json', self.root)
        shutil.copytree(science.ROOT / 'templates', self.root / 'templates')
        self.paper = science.create_record(self.root, 'arxiv:2610.11111', 'Alpha research')
        self.other = science.create_record(self.root, 'arxiv:2610.22222', 'Beta research')
        (self.paper / 'review.md').write_text('alpha research\n' * 500)
        self.config = backend.Config(url=os.environ['SCIENCE_TEST_QDRANT_URL'],
                                     collection='test_' + uuid.uuid4().hex, dimension=3)
        config_patch = patch('semantic.Config.load', return_value=self.config)
        config_patch.start(); self.addCleanup(config_patch.stop)
        embedding_patch = patch('search_backend.Embedder.remote', side_effect=self.vectors)
        self.remote = embedding_patch.start(); self.addCleanup(embedding_patch.stop)
        self.store = backend.Qdrant(self.config)
        self.addCleanup(self.remove_collections)

    def vectors(self, texts):
        return [[float((sum(t.encode()) + i) % 13 + 1) for i in range(self.config.dimension)] for t in texts]

    def remove_collections(self):
        for alias in self.store.aliases():
            if alias.startswith(self.config.collection):
                self.store.call('/collections/aliases', 'POST', {'actions': [{'delete_alias': {'alias_name': alias}}]})
        for row in self.store.call('/collections')['collections']:
            if row['name'].startswith(self.config.collection):
                self.store.call('/collections/' + row['name'], 'DELETE')

    def test_grouped_search_cache_and_deleted_document(self):
        initial = semantic.build(self.root)
        calls = self.remote.call_count
        self.assertTrue(semantic.build(self.root)['unchanged'])
        self.assertEqual(calls, self.remote.call_count)
        results = semantic.Searcher(self.root).search('research', 2)
        self.assertEqual({r['document'] for r in results}, {'arxiv:2610.11111', 'arxiv:2610.22222'})
        shutil.rmtree(self.other)
        with self.assertRaisesRegex(ValueError, 'устарел'):
            semantic.Searcher(self.root)
        semantic.build(self.root)
        self.assertEqual(self.store.aliases()[self.config.collection + '_previous'], initial['collection'])
        self.assertEqual([r['document'] for r in semantic.Searcher(self.root).search('research', 10)], ['arxiv:2610.11111'])

    def test_failed_embedding_preserves_active_generation(self):
        initial = semantic.build(self.root)
        (self.paper / 'review.md').write_text('changed source')
        with patch('search_backend.Embedder.remote', side_effect=ValueError('API failure')):
            with self.assertRaisesRegex(ValueError, 'API failure'):
                semantic.build(self.root)
        self.assertEqual(self.store.aliases()[self.config.collection], initial['collection'])
        names = [r['name'] for r in self.store.call('/collections')['collections'] if r['name'].startswith(self.config.collection)]
        self.assertEqual(names, [initial['collection']])

    def test_model_change_rebuild_and_prune_keep_current_and_previous(self):
        first = semantic.build(self.root)
        self.config.dimension = 4
        with self.assertRaisesRegex(ValueError, 'модель изменилась'):
            semantic.Searcher(self.root)
        second = semantic.build(self.root)
        self.config.version = '2'
        third = semantic.build(self.root)
        result = semantic.prune(self.root)
        self.assertEqual(result['removed'], [first['collection']])
        self.assertIn(second['collection'], result['retained'])
        self.assertIn(third['collection'], result['retained'])
        self.assertEqual(len(semantic.Searcher(self.root).search('query', 2)), 2)


if __name__ == '__main__':
    unittest.main()
