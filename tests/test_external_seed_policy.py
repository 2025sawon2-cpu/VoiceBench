import json
import unittest
from unittest.mock import patch

import test_continuity_runner as fixtures
from voicebench.service import QWEN_EXPRESSIVE_SLUG


class ExternalSeedPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ContinuityRunnerTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def execute(self, *, failed):
        f=self.fixture
        request=f.service.create_studio_request('전체 원고입니다. 댓글에 나도 남겨주세요.',
            source='external_api',engine_slug=QWEN_EXPRESSIVE_SLUG)
        def inspect(text,path):
            result=f.passed(text,path)
            if failed:result.update(retry_recommended=True,reasons=['ending_missing'],score=1.)
            return result
        with (patch('voicebench.runner.run_engine',side_effect=f.fake_engine),patch.object(
            f.runner.quality_verifier,'inspect',side_effect=inspect),patch('voicebench.runner.GpuSampler') as sampler,
            patch('voicebench.runner.system_snapshot',return_value={})):
            sampler.return_value.summary.return_value={}
            job=f.runner.claim()
            self.assertEqual(job['studio_request_source'],'external_api')
            f.runner.execute_job(job)
        return job,request

    def test_failed_external_qwen_never_changes_seed_or_promotes_failed_audio(self):
        job,request=self.execute(failed=True);f=self.fixture
        self.assertEqual([r.seed for r in f.calls],[13])
        saved=f.db.fetch_one('SELECT status,error FROM jobs WHERE id=?',(job['id'],))
        self.assertEqual(saved['status'],'failed')
        self.assertIn('다른 Seed로 자동 대체하지 않습니다',saved['error'])
        self.assertEqual(f.db.fetch_one('SELECT COUNT(*) n FROM runs')['n'],0)
        self.assertTrue(list(f.paths.outputs.rglob('*quality-failed.json')))

    def test_passed_external_request_records_fixed_policy(self):
        job,_=self.execute(failed=False);f=self.fixture
        metrics=json.loads(f.db.fetch_one('SELECT metrics_json FROM runs')['metrics_json'])
        self.assertEqual(metrics['quality_control']['seed_policy'],'fixed_primary_v1')
        self.assertEqual(metrics['quality_control']['selected_seed'],13)
        self.assertEqual(metrics['quality_control']['attempt_count'],1)


if __name__=='__main__':unittest.main()
