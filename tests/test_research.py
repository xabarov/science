import argparse
import io
import json
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import preservation
import research
import science
import semantic


class ResearchTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'catalog'; self.root.mkdir()
        shutil.copy(science.ROOT / 'vocab.json', self.root)
        shutil.copytree(science.ROOT / 'templates', self.root / 'templates')
        self.paper = science.create_record(self.root, 'arxiv:2610.12345', 'Paper')
        self.update(self.paper, claims=[{'id':'c1','text':'Claim','epistemic_status':'hypothesis','verification':'not_checked','source':'','locator':''}])
        self.idea = science.create_record(self.root, 'idea:test', 'Idea', 'idea')
        self.update(self.idea, extra={'research': {'stage':'proposed','depends_on':['arxiv:2610.12345#c1']}})

    def update(self, folder, **fields):
        path = folder / 'record.json'; r = json.loads(path.read_text()); r.update(fields)
        path.write_text(json.dumps(r, ensure_ascii=False), encoding='utf-8')

    def test_claim_reference_validation(self):
        self.update(self.idea, extra={'research':{'depends_on':['arxiv:2610.12345#missing']}})
        with self.assertRaisesRegex(ValueError, 'Отсутствует утверждение'):
            science.collect(self.root)

    def test_dependency_review_and_transitive_impact(self):
        q = science.create_record(self.root,'question:test','Question','question')
        self.update(q, extra={'research':{'depends_on':['idea:test']}})
        self.assertEqual(len(research.review_queue(self.root)),2)
        with self.assertRaisesRegex(ValueError,'Сначала'):
            research.acknowledge(self.root,'question:test','review')
        research.acknowledge(self.root,'idea:test','Read claim')
        research.acknowledge(self.root,'question:test','Read idea')
        self.assertEqual(research.review_queue(self.root),[])
        self.update(self.paper, claims=[{'id':'c1','text':'Changed','epistemic_status':'hypothesis','verification':'not_checked','source':'','locator':''}])
        self.assertEqual({r['id'] for r in research.review_queue(self.root)}, {'idea:test','question:test'})
        self.assertEqual({r['id'] for r in research.impact(self.root,'arxiv:2610.12345#c1')}, {'idea:test','question:test'})

    def test_new_snapshot_invalidates_dependencies(self):
        research.acknowledge(self.root,'idea:test','read')
        preservation.atomic_json(self.root/'sources/manifest.json',{'snapshots':[{'document':'arxiv:2610.12345','version':'v1','sha256':'new'}]})
        self.assertEqual(research.review_queue(self.root)[0]['id'],'idea:test')

    def test_dependency_cycles_rejected(self):
        q = science.create_record(self.root,'question:test','Question','question')
        self.update(q, extra={'research':{'depends_on':['idea:test']}})
        self.update(self.idea,extra={'research':{'depends_on':['question:test']}})
        with self.assertRaisesRegex(ValueError,'Цикл'):
            science.collect(self.root)

    def test_stage_requires_search_log_and_preserves_file(self):
        original = (self.idea/'record.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'журнал поиска'):
            research.transition(self.root,'idea:test','literature_checked','Checked')
        self.assertEqual((self.idea/'record.json').read_bytes(),original)
        research.transition(self.root,'idea:test','rejected','Already known')
        self.assertEqual(json.loads((self.idea/'record.json').read_text())['extra']['research']['stage'],'rejected')
        with self.assertRaisesRegex(ValueError,'Недопустимый переход'):
            research.transition(self.root,'idea:test','running','go')

    def test_experiment_cannot_start_without_protocol(self):
        folder = science.create_record(self.root,'experiment:test','Experiment','experiment')
        with self.assertRaisesRegex(ValueError,'protocol'):
            research.transition(self.root,'experiment:test','running','Launch')
        r=json.loads((folder/'record.json').read_text())
        self.assertEqual(r['extra']['research']['stage'],'planned')

    def test_exact_claim_links_exported(self):
        self.update(self.idea,claims=[{'id':'h1','text':'Hypothesis','epistemic_status':'hypothesis','verification':'not_checked','source':'','locator':''}],
                    extra={'research':{'claim_links':[{'from':'idea:test#h1','to':'arxiv:2610.12345#c1','type':'depends_on','status':'inferred','source':'','locator':'','note':'Motivation'}]}})
        docs,_=science.collect(self.root)
        nodes,edges=research.evidence_nodes(docs)
        self.assertIn('idea:test#h1',{n['id'] for n in nodes})
        self.assertTrue(any(e['from_id']=='idea:test#h1' and e['to_id']=='arxiv:2610.12345#c1' for e in edges))

    def test_snapshot_archive_is_immutable_and_checked(self):
        class Response(io.BytesIO):
            url='https://example.org/v1'
            from email.message import Message
            headers=Message(); headers['Content-Type']='text/html; charset=utf-8'
        data='<h1>Тест</h1><script>ignore()</script><p>Evidence.</p>'.encode()
        with patch('preservation.urlopen', return_value=Response(data)):
            first=preservation.snapshot(self.root,'arxiv:2610.12345','https://example.org/v1','v1')
        with patch('preservation.urlopen', return_value=Response(data)):
            second=preservation.snapshot(self.root,'arxiv:2610.12345','https://example.org/v1','v1')
        self.assertEqual(first['id'],second['id'])
        self.assertEqual(len(preservation.load_manifest(self.root)['snapshots']),1)
        self.assertNotIn('ignore()', (self.root/first['text_path']).read_text())
        self.assertEqual(preservation.check_sources(self.root,True)[0]['status'],'ok')
        (self.root/first['local_path']).write_text('tampered')
        with self.assertRaisesRegex(ValueError,'целостность'):
            preservation.check_sources(self.root)

    def test_backup_restore_preserves_sources_excludes_secrets(self):
        (self.root/'.env').write_text('secret')
        (self.root/'attachments').mkdir(); (self.root/'attachments/example.html').write_text('source')
        (self.root/'data').mkdir(); (self.root/'data/cache').write_text('cache')
        result=preservation.backup(self.root,self.root.parent/'backups')
        archive=Path(result['archive'])
        self.assertTrue(preservation.verify_backup(archive)['verified'])
        target=self.root.parent/'restored'
        preservation.restore(archive,target)
        self.assertFalse((target/'.env').exists()); self.assertFalse((target/'data').exists())
        self.assertEqual((target/'attachments/example.html').read_text(),'source')
        self.assertEqual(science.rebuild(target),2)
        with self.assertRaisesRegex(ValueError,'новый каталог'):
            preservation.restore(archive,target)
        with archive.open('ab') as f: f.write(b'corruption')
        with self.assertRaisesRegex(ValueError,'сумма'):
            preservation.verify_backup(archive)

    def test_restore_rejects_path_traversal(self):
        archive=self.root.parent/'unsafe.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            member=tarfile.TarInfo('../outside'); member.size=1; tar.addfile(member,io.BytesIO(b'x'))
        with self.assertRaisesRegex(ValueError,'Небезопасный'):
            preservation.verify_backup(archive)
        with self.assertRaisesRegex(ValueError,'путь'):
            preservation.safe_local(self.root,'../outside')

    def test_hybrid_ranking_and_metrics(self):
        ranking,_=semantic.rrf([['a','b','b'],['b','c']])
        self.assertEqual(ranking[0],'b')
        self.assertEqual(semantic.metrics(['a','b'],{'b'},2)['mrr'],.5)
        self.assertEqual(semantic.metrics(['a','b'],{'b','c'},2)['recall'],.5)

    def test_measurement_requires_conditions_and_provenance(self):
        self.update(self.paper,extra={'research':{'measurements':[{'value':33.33}]}})
        with self.assertRaisesRegex(ValueError,'measurements'):
            science.collect(self.root)


if __name__=='__main__':
    unittest.main()
