from __future__ import annotations

import unittest

from pydantic import ValidationError

from contribarena.models.surface import (
    RunSummary,
    RuntimeStatus,
    SurfaceAgent,
    SurfaceArtifact,
    SurfaceJudgement,
    SurfaceMaintainerOutcome,
    SurfacePipelineStage,
    SurfacePullRequest,
    SurfaceQualityGate,
    SurfaceRepository,
    SurfaceRubricScore,
    SurfaceSeason,
)


class SurfaceAgentTest(unittest.TestCase):
    def test_defaults(self) -> None:
        agent = SurfaceAgent()
        self.assertEqual(agent.name, 'builtin')
        self.assertEqual(agent.handle, '')
        self.assertEqual(agent.participant_id, '')

    def test_explicit_values(self) -> None:
        agent = SurfaceAgent(
            name='qwen', handle='qwen-3', participant_id='season_0:qwen-3.7-max'
        )
        self.assertEqual(agent.name, 'qwen')
        self.assertEqual(agent.handle, 'qwen-3')
        self.assertEqual(agent.participant_id, 'season_0:qwen-3.7-max')


class RuntimeStatusTest(unittest.TestCase):
    def test_defaults(self) -> None:
        rs = RuntimeStatus()
        self.assertEqual(rs.status, '')
        self.assertEqual(rs.reason, '')
        self.assertEqual(rs.message, '')
        self.assertEqual(rs.attempts, 0)

    def test_explicit_values(self) -> None:
        rs = RuntimeStatus(
            status='retrying', reason='timeout', message='retry in 2s', attempts=3
        )
        self.assertEqual(rs.status, 'retrying')
        self.assertEqual(rs.reason, 'timeout')
        self.assertEqual(rs.message, 'retry in 2s')
        self.assertEqual(rs.attempts, 3)


class SurfaceRepositoryTest(unittest.TestCase):
    def test_defaults(self) -> None:
        repo = SurfaceRepository()
        self.assertEqual(repo.full_name, '')
        self.assertEqual(repo.url, '')

    def test_explicit_values(self) -> None:
        repo = SurfaceRepository(
            full_name='qWaitCrypto/ContribArena',
            url='https://github.com/qWaitCrypto/ContribArena',
        )
        self.assertEqual(repo.full_name, 'qWaitCrypto/ContribArena')
        self.assertEqual(repo.url, 'https://github.com/qWaitCrypto/ContribArena')


class SurfacePipelineStageTest(unittest.TestCase):
    STAGE_IDS = (
        'agent',
        'repo_discovery',
        'workspace',
        'patch_diff',
        'quality_gate',
        'pull_request',
        'maintainer_outcome',
    )

    def test_required_fields(self) -> None:
        stage = SurfacePipelineStage(stage_id='agent')
        self.assertEqual(stage.stage_id, 'agent')
        self.assertEqual(stage.status, 'unknown')

    def test_defaults(self) -> None:
        stage = SurfacePipelineStage(stage_id='workspace')
        self.assertEqual(stage.started_at, '')
        self.assertEqual(stage.completed_at, '')
        self.assertEqual(stage.summary, '')
        self.assertEqual(stage.source_artifacts, [])

    def test_explicit_values(self) -> None:
        stage = SurfacePipelineStage(
            stage_id='pull_request',
            status='passed',
            started_at='2026-05-30T00:00:00Z',
            completed_at='2026-05-30T00:01:00Z',
            summary='PR opened',
            source_artifacts=['patch.diff'],
        )
        self.assertEqual(stage.status, 'passed')
        self.assertEqual(stage.source_artifacts, ['patch.diff'])

    def test_all_stage_id_literals_accepted(self) -> None:
        for sid in self.STAGE_IDS:
            with self.subTest(stage_id=sid):
                stage = SurfacePipelineStage(stage_id=sid)
                self.assertEqual(stage.stage_id, sid)

    def test_invalid_stage_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfacePipelineStage(stage_id='nonexistent_stage')

    def test_all_pipeline_status_literals(self) -> None:
        for ps in (
            'not_started',
            'running',
            'passed',
            'failed',
            'blocked',
            'skipped',
            'pending',
            'unknown',
        ):
            with self.subTest(status=ps):
                stage = SurfacePipelineStage(stage_id='agent', status=ps)
                self.assertEqual(stage.status, ps)

    def test_invalid_pipeline_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfacePipelineStage(stage_id='agent', status='invalid_status')

    def test_missing_required_stage_id_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SurfacePipelineStage()  # type: ignore[call-arg]

    def test_source_artifacts_factory_independence(self) -> None:
        a = SurfacePipelineStage(stage_id='agent')
        b = SurfacePipelineStage(stage_id='agent')
        a.source_artifacts.append('trace.json')
        self.assertEqual(b.source_artifacts, [])


class SurfaceQualityGateTest(unittest.TestCase):
    def test_defaults(self) -> None:
        qg = SurfaceQualityGate()
        self.assertEqual(qg.status, 'unknown')
        self.assertEqual(qg.warnings, [])

    def test_explicit_values(self) -> None:
        qg = SurfaceQualityGate(status='pass', warnings=['minor note'])
        self.assertEqual(qg.status, 'pass')
        self.assertEqual(qg.warnings, ['minor note'])

    def test_all_status_literals_accepted(self) -> None:
        for s in ('pass', 'block', 'fail', 'unknown'):
            with self.subTest(status=s):
                qg = SurfaceQualityGate(status=s)
                self.assertEqual(qg.status, s)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceQualityGate(status='invalid')

    def test_warnings_factory_independence(self) -> None:
        a = SurfaceQualityGate()
        b = SurfaceQualityGate()
        a.warnings.append('w1')
        self.assertEqual(b.warnings, [])


class SurfacePullRequestTest(unittest.TestCase):
    def test_defaults(self) -> None:
        pr = SurfacePullRequest()
        self.assertEqual(pr.url, '')
        self.assertIsNone(pr.number)
        self.assertEqual(pr.state, 'none')

    def test_explicit_values(self) -> None:
        pr = SurfacePullRequest(
            url='https://github.com/x/y/pull/1', number=1, state='open'
        )
        self.assertEqual(pr.number, 1)
        self.assertEqual(pr.state, 'open')

    def test_all_state_literals_accepted(self) -> None:
        for s in ('none', 'open', 'closed', 'merged', 'unknown'):
            with self.subTest(state=s):
                pr = SurfacePullRequest(state=s)
                self.assertEqual(pr.state, s)

    def test_invalid_state_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfacePullRequest(state='draft')


class SurfaceMaintainerOutcomeTest(unittest.TestCase):
    def test_defaults(self) -> None:
        mo = SurfaceMaintainerOutcome()
        self.assertEqual(mo.status, 'pending')
        self.assertEqual(mo.observed_at, '')
        self.assertEqual(mo.source, 'none')

    def test_explicit_values(self) -> None:
        mo = SurfaceMaintainerOutcome(
            status='merged', observed_at='2026-05-30', source='github_pr_state'
        )
        self.assertEqual(mo.status, 'merged')
        self.assertEqual(mo.source, 'github_pr_state')

    def test_all_status_literals(self) -> None:
        for s in (
            'pending',
            'reviewed',
            'changes_requested',
            'merged',
            'closed',
            'stale',
            'unknown',
        ):
            with self.subTest(status=s):
                mo = SurfaceMaintainerOutcome(status=s)
                self.assertEqual(mo.status, s)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceMaintainerOutcome(status='invalid_status')

    def test_all_source_literals(self) -> None:
        for s in (
            'github_pr_state',
            'github_review',
            'github_comment',
            'ci_status',
            'manual_adjudication',
            'none',
        ):
            with self.subTest(source=s):
                mo = SurfaceMaintainerOutcome(source=s)
                self.assertEqual(mo.source, s)

    def test_invalid_source_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceMaintainerOutcome(source='invalid_source')


class SurfaceSeasonTest(unittest.TestCase):
    def test_defaults(self) -> None:
        season = SurfaceSeason()
        self.assertEqual(season.id, '')
        self.assertEqual(season.name, '')
        self.assertEqual(season.phase, 'unknown')

    def test_explicit_values(self) -> None:
        season = SurfaceSeason(
            id='season_0', name='Season 0', phase='owned_repo_calibration'
        )
        self.assertEqual(season.id, 'season_0')
        self.assertEqual(season.phase, 'owned_repo_calibration')

    def test_all_phase_literals(self) -> None:
        for p in ('owned_repo_calibration', 'external_live', 'archived', 'unknown'):
            with self.subTest(phase=p):
                season = SurfaceSeason(phase=p)
                self.assertEqual(season.phase, p)

    def test_invalid_phase_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceSeason(phase='invalid_phase')


class SurfaceRubricScoreTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        rs = SurfaceRubricScore(dimension='code_quality')
        self.assertEqual(rs.dimension, 'code_quality')
        self.assertEqual(rs.score, 0)
        self.assertEqual(rs.max_score, 5)
        self.assertEqual(rs.weight, 0)

    def test_explicit_values(self) -> None:
        rs = SurfaceRubricScore(dimension='impact', score=4.5, max_score=10, weight=2.0)
        self.assertEqual(rs.score, 4.5)
        self.assertEqual(rs.max_score, 10)
        self.assertEqual(rs.weight, 2.0)

    def test_missing_dimension_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceRubricScore()  # type: ignore[call-arg]


class SurfaceJudgementTest(unittest.TestCase):
    def test_defaults(self) -> None:
        j = SurfaceJudgement()
        self.assertEqual(j.status, 'not_judged')
        self.assertIsNone(j.judge_score)
        self.assertEqual(j.real_world_adjustment, 0)
        self.assertIsNone(j.arena_score)
        self.assertEqual(j.rubric_summary, [])
        self.assertEqual(j.source_artifacts, [])

    def test_explicit_values(self) -> None:
        rubric = SurfaceRubricScore(dimension='quality', score=3.0)
        j = SurfaceJudgement(
            status='judged',
            judge_score=4.2,
            real_world_adjustment=1,
            arena_score=5.2,
            rubric_summary=[rubric],
            source_artifacts=['judgement.json'],
        )
        self.assertEqual(j.status, 'judged')
        self.assertEqual(j.judge_score, 4.2)
        self.assertEqual(len(j.rubric_summary), 1)

    def test_all_status_literals(self) -> None:
        for s in (
            'not_judged',
            'judged',
            'partial_fallback',
            'fallback',
            'deferred',
            'failed',
            'unknown',
        ):
            with self.subTest(status=s):
                j = SurfaceJudgement(status=s)
                self.assertEqual(j.status, s)

    def test_invalid_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceJudgement(status='invalid_judgement')

    def test_factory_independence(self) -> None:
        a = SurfaceJudgement()
        b = SurfaceJudgement()
        a.rubric_summary.append(SurfaceRubricScore(dimension='x'))
        a.source_artifacts.append('a.json')
        self.assertEqual(b.rubric_summary, [])
        self.assertEqual(b.source_artifacts, [])


class SurfaceArtifactTest(unittest.TestCase):
    def test_required_fields(self) -> None:
        art = SurfaceArtifact(name='trace.json', kind='json')
        self.assertEqual(art.name, 'trace.json')
        self.assertEqual(art.kind, 'json')
        self.assertEqual(art.visibility, 'internal')
        self.assertEqual(art.url, '')
        self.assertEqual(art.size_bytes, 0)
        self.assertFalse(art.redacted)

    def test_explicit_values(self) -> None:
        art = SurfaceArtifact(
            name='log.jsonl',
            kind='jsonl',
            visibility='public',
            url='https://example.com/log.jsonl',
            size_bytes=1024,
            redacted=True,
        )
        self.assertEqual(art.visibility, 'public')
        self.assertEqual(art.size_bytes, 1024)
        self.assertTrue(art.redacted)

    def test_all_visibility_literals(self) -> None:
        for v in ('public', 'operator', 'internal'):
            with self.subTest(visibility=v):
                art = SurfaceArtifact(name='a', kind='json', visibility=v)
                self.assertEqual(art.visibility, v)

    def test_invalid_visibility_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceArtifact(name='a', kind='json', visibility='secret')

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            SurfaceArtifact(name='a')  # type: ignore[call-arg]
        with self.assertRaises(ValidationError):
            SurfaceArtifact()  # type: ignore[call-arg]


class RunSummaryTest(unittest.TestCase):
    def _required(self) -> dict[str, str]:
        return {'run_id': 'run-001', 'run_mode': 'owned_live', 'model': 'qwen-3.7-max'}

    def test_required_fields(self) -> None:
        rs = RunSummary(**self._required())
        self.assertEqual(rs.run_id, 'run-001')
        self.assertEqual(rs.run_mode, 'owned_live')
        self.assertEqual(rs.model, 'qwen-3.7-max')

    def test_defaults(self) -> None:
        rs = RunSummary(**self._required())
        self.assertEqual(rs.schema_version, '1')
        self.assertEqual(rs.wake_source, 'unranked')
        self.assertIsInstance(rs.agent, SurfaceAgent)
        self.assertIsInstance(rs.repository, SurfaceRepository)
        self.assertIsInstance(rs.season, SurfaceSeason)
        self.assertEqual(rs.opportunity_source, 'none')
        self.assertEqual(rs.opportunity_source_ref, '')
        self.assertEqual(rs.started_at, '')
        self.assertEqual(rs.completed_at, '')
        self.assertIsNone(rs.duration_seconds)
        self.assertEqual(rs.run_status, 'unknown')
        self.assertEqual(rs.terminal_reason, '')
        self.assertEqual(rs.terminal_layer, '')
        self.assertEqual(rs.contribution_class, 'unknown')
        self.assertEqual(rs.pipeline, [])
        self.assertIsInstance(rs.quality_gate, SurfaceQualityGate)
        self.assertIsInstance(rs.pull_request, SurfacePullRequest)
        self.assertIsInstance(rs.maintainer_outcome, SurfaceMaintainerOutcome)
        self.assertIsInstance(rs.judgement, SurfaceJudgement)
        self.assertEqual(rs.artifacts, [])
        self.assertEqual(rs.workspace, {})
        self.assertEqual(rs.replacement, {})
        self.assertEqual(rs.judgement_retry, {})
        self.assertEqual(rs.live_submission_retry, {})
        self.assertEqual(rs.submission_outcome, '')
        self.assertEqual(rs.score_status, 'not_judged')
        self.assertTrue(rs.ranking_eligible)
        self.assertEqual(rs.ranking_exclusion_reason, '')
        self.assertEqual(rs.contribution_thread_id, '')

    def test_explicit_values(self) -> None:
        rs = RunSummary(
            **self._required(),
            wake_source='manual',
            run_status='completed',
            contribution_class='tests',
            score_status='scored',
            ranking_eligible=False,
        )
        self.assertEqual(rs.wake_source, 'manual')
        self.assertEqual(rs.run_status, 'completed')
        self.assertEqual(rs.contribution_class, 'tests')
        self.assertEqual(rs.score_status, 'scored')
        self.assertFalse(rs.ranking_eligible)

    def test_missing_required_fields_raises(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(run_id='r')  # type: ignore[call-arg]
        with self.assertRaises(ValidationError):
            RunSummary()  # type: ignore[call-arg]

    def test_wake_source_literals(self) -> None:
        for w in ('manual', 'auto', 'unranked'):
            with self.subTest(wake=w):
                rs = RunSummary(**self._required(), wake_source=w)
                self.assertEqual(rs.wake_source, w)

    def test_invalid_wake_source_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(**self._required(), wake_source='invalid')

    def test_opportunity_source_literals(self) -> None:
        for o in ('issue_url', 'discovery_event_id', 'none'):
            with self.subTest(opp=o):
                rs = RunSummary(**self._required(), opportunity_source=o)
                self.assertEqual(rs.opportunity_source, o)

    def test_invalid_opportunity_source_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(**self._required(), opportunity_source='invalid')

    def test_contribution_class_literals(self) -> None:
        for c in ('low_risk_code', 'tests', 'docs', 'mixed', 'unknown'):
            with self.subTest(cls=c):
                rs = RunSummary(**self._required(), contribution_class=c)
                self.assertEqual(rs.contribution_class, c)

    def test_invalid_contribution_class_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(**self._required(), contribution_class='invalid')

    def test_score_status_literals(self) -> None:
        for s in ('scored', 'diagnostic_only', 'not_judged', 'deferred', 'failed'):
            with self.subTest(score=s):
                rs = RunSummary(**self._required(), score_status=s)
                self.assertEqual(rs.score_status, s)

    def test_invalid_score_status_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RunSummary(**self._required(), score_status='invalid')

    def test_nested_composition(self) -> None:
        rs = RunSummary(
            **self._required(),
            agent=SurfaceAgent(name='test-agent'),
            repository=SurfaceRepository(full_name='org/repo'),
            season=SurfaceSeason(id='s0', phase='owned_repo_calibration'),
            quality_gate=SurfaceQualityGate(status='pass'),
            pull_request=SurfacePullRequest(number=42, state='open'),
            maintainer_outcome=SurfaceMaintainerOutcome(status='pending'),
            judgement=SurfaceJudgement(status='not_judged'),
            pipeline=[SurfacePipelineStage(stage_id='agent', status='passed')],
            artifacts=[SurfaceArtifact(name='trace.json', kind='json')],
        )
        self.assertEqual(rs.agent.name, 'test-agent')
        self.assertEqual(rs.repository.full_name, 'org/repo')
        self.assertEqual(rs.season.phase, 'owned_repo_calibration')
        self.assertEqual(rs.quality_gate.status, 'pass')
        self.assertEqual(rs.pull_request.number, 42)
        self.assertEqual(rs.maintainer_outcome.status, 'pending')
        self.assertEqual(rs.judgement.status, 'not_judged')
        self.assertEqual(len(rs.pipeline), 1)
        self.assertEqual(len(rs.artifacts), 1)

    def test_factory_independence(self) -> None:
        a = RunSummary(**self._required())
        b = RunSummary(**self._required())
        a.pipeline.append(SurfacePipelineStage(stage_id='agent'))
        a.artifacts.append(SurfaceArtifact(name='x', kind='json'))
        a.workspace['key'] = 'val'
        self.assertEqual(b.pipeline, [])
        self.assertEqual(b.artifacts, [])
        self.assertEqual(b.workspace, {})

    def test_schema_version_literal(self) -> None:
        rs = RunSummary(**self._required())
        self.assertEqual(rs.schema_version, '1')
        with self.assertRaises(ValidationError):
            RunSummary(**self._required(), schema_version='2')  # type: ignore[arg-type]


class SurfaceImportTest(unittest.TestCase):
    def test_run_summary_importable_from_models(self) -> None:
        from contribarena.models import RunSummary as RS

        self.assertIs(RS, RunSummary)


if __name__ == '__main__':
    unittest.main()
