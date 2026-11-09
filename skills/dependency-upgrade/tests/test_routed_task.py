"""routed_task.py: the shared router applied to consolidate and frontend-ui-ux-overhaul.

Planning and review tasks for other skills run through the same review_router.Router (priority, probes,
fallback, isolation). Every CLI is mocked through the review-router fakes; no test makes a model call.
The real skill profiles (<skills>/<skill>/routing.json) are loaded.
"""
import contextlib
import io
import json
import os

from tests.support import SKILL_DIR, TempCase, git_init, load, sh, write
from tests.test_review_router import (GOOD, FakeHarnesses, claude_result, fail, make_router)
from tests.test_planning_router import PLAN_TEXT

rt = load('routed_task_under_test', 'scripts/routed_task.py')
rr = rt.rr
SKILLS = os.path.dirname(SKILL_DIR)
FAIL = ('Found a lost change.\n%s\n{"verdict": "fail", "summary": "lost work", "findings": [{"severity": '
        '"Important", "file": "feature", "line": 1, "summary": "stash@{0} not in main", "scenario": "dropped"}]}\n%s\n'
        % (rr.RESULT_BEGIN, rr.RESULT_END))
SESSION = ('--session-harness', 'claude-code', '--session-model', 'claude-opus-5-5', '--session-effort', 'high')


class RoutedCase(TempCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, rt, 'ROUTER_FACTORY', None)
        self._rt_env = {k: os.environ.get(k) for k in ('AGENT_ROUTING_STATE_DIR', 'AGENT_ROUTING_SKILLS_DIR')}
        os.environ['AGENT_ROUTING_STATE_DIR'] = os.path.join(self.tmp, 'routing-state')
        os.environ.pop('AGENT_ROUTING_SKILLS_DIR', None)
        self.repo = os.path.join(self.tmp, 'repo')
        write(os.path.join(self.repo, 'app.js'), 'export const a = 1;\n')
        git_init(self.repo)
        self.base = sh(['git', 'rev-parse', 'HEAD'], self.repo).stdout.strip()

    def tearDown(self):
        for key, value in self._rt_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def rt(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = rt.main(list(argv))
        return code, json.loads(buffer.getvalue())

    def use(self, fake):
        rt.ROUTER_FACTORY = lambda **kw: make_router(fake, max_retries=kw.get('max_retries', 1))

    def start(self, skill):
        code, run = self.rt('start', '--skill', skill, '--repo', self.repo)
        self.assertEqual(code, 0, run)
        return run['run_dir']

    def handoff(self, text='requirements: keep main design\ntoken=SECRET-TOKEN-VALUE\n'):
        path = os.path.join(self.tmp, 'handoff.md')
        write(path, text)
        return path

    def commit(self, name, text, message='change'):
        write(os.path.join(self.repo, name), text)
        sh(['git', 'add', '-A'], self.repo)
        sh(['git', 'commit', '-q', '-m', message], self.repo)


class ProfileTests(RoutedCase):

    def test_both_skills_ship_valid_profiles_and_document_the_router(self):
        for skill, kinds in (('consolidate', {'reconciliation', 'code'}),
                             ('frontend-ui-ux-overhaul', {'code', 'ui-ux', 'accessibility'})):
            profile = rt.load_profile(skill)
            self.assertEqual(profile['skill'], skill)
            self.assertEqual(set(profile['review_kinds']), kinds)
            self.assertEqual(set(profile['plan_kinds']), {'plan', 'architecture'})
            with open(os.path.join(SKILLS, skill, 'SKILL.md'), encoding='utf-8') as handle:
                text = handle.read()
            self.assertIn('routed_task.py', text)
            for model in ('Opus 5.5', 'DeepSeek V4.1 Flash 1017'):
                self.assertIn(model, text)
        self.assertEqual(rt.load_profile('consolidate')['review_kinds']['reconciliation']['evidence'], 'git-inventory')

    def test_unknown_skill_or_kind_refused(self):
        code, out = self.rt('start', '--skill', 'no-such-skill', '--repo', self.repo)
        self.assertEqual(code, 1)
        self.assertIn('no routing profile', out['error'])
        run = self.start('consolidate')
        code, out = self.rt('review', run, '--kind', 'ui-ux', '--base', self.base, *SESSION)
        self.assertEqual(code, 1)
        self.assertIn('--kind must be one of', out['error'])


class PlanningTests(RoutedCase):

    def test_1_5_opus_medium_plans_once_read_only_with_isolated_redacted_handoff(self):
        run = self.start('consolidate')
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(PLAN_TEXT)},
                                                                {'stdout': claude_result(PLAN_TEXT)}]})
        self.use(fake)
        code, entry = self.rt('plan', run, '--context', self.handoff(), *SESSION)
        self.assertEqual(code, 0, entry)
        self.assertEqual(entry['status'], 'completed')
        self.assertTrue(entry['advisory'])
        self.assertEqual((entry['selected']['harness'], entry['selected']['model'], entry['selected']['effort']),
                         ('claude-code', 'claude-opus-5-5', 'medium'))
        calls = fake.review_calls()
        self.assertEqual(len(calls), 1, 'exactly one model for the task')
        argv = calls[0]
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Grep,Glob')
        self.assertEqual(argv[argv.index('--effort') + 1], 'medium')
        self.assertIn('--strict-mcp-config', argv, 'no user MCP servers in reviewer/planner sessions')
        task_dir = os.path.dirname(entry['entry'])
        self.assertTrue(fake.cwds[fake.calls.index(argv)].startswith(task_dir), 'fresh session outside the repo')
        self.assertIn(os.path.realpath(self.repo), argv[argv.index('--add-dir') + 1:])
        with open(os.path.join(task_dir, 'prompt.md'), encoding='utf-8') as handle:
            prompt = handle.read()
        self.assertIn('authorizes nothing', prompt)
        self.assertIn('consolidate skill', prompt)
        self.assertIn('Mandatory user approval', prompt, 'binding rules are handed off')
        with open(os.path.join(task_dir, 'context.md'), encoding='utf-8') as handle:
            context = handle.read()
        self.assertIn('keep main design', context)
        self.assertNotIn('SECRET-TOKEN-VALUE', context)
        self.assertEqual(sh(['git', 'status', '--porcelain'], self.repo).stdout, '', 'planner never writes')
        _, again = self.rt('plan', run, '--context', self.handoff(), *SESSION)
        self.assertTrue(again['reused'])
        self.assertEqual(len(fake.review_calls()), 1, 'unchanged tree + handoff: no replan')
        _, changed = self.rt('plan', run, '--context', self.handoff('requirements: new scope\n'), *SESSION)
        self.assertFalse(changed.get('reused'), 'material change replans')

    def test_6_high_risk_architecture_task_needs_a_subject(self):
        run = self.start('frontend-ui-ux-overhaul')
        code, out = self.rt('plan', run, '--context', self.handoff(), '--kind', 'architecture', *SESSION)
        self.assertEqual(code, 1)
        self.assertIn('--subject', out['error'])
        self.use(FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(PLAN_TEXT)}]}))
        code, entry = self.rt('plan', run, '--context', self.handoff(), '--kind', 'architecture',
                              '--subject', 'shared Button primitive', *SESSION)
        self.assertEqual((code, entry['status'], entry['kind']), (0, 'completed', 'architecture'))
        with open(os.path.join(os.path.dirname(entry['entry']), 'prompt.md'), encoding='utf-8') as handle:
            self.assertIn('shared design-system primitive', handle.read())

    def test_2_every_fallback_stage_including_the_invoking_session(self):
        run = self.start('consolidate')
        self.use(FakeHarnesses(self.tmp, missing={'claude'}, reviews={'opencode': [{'stdout': json.dumps(
            {'type': 'text', 'part': {'text': PLAN_TEXT}})}]}))
        _, entry = self.rt('plan', run, '--context', self.handoff('a\n'), *SESSION)
        self.assertEqual((entry['selected']['harness'], entry['selected']['model'], entry['selected']['effort']),
                         ('opencode', 'deepseek-v4.1-flash', 'high'))
        self.assertEqual(entry['candidates'][0]['reason'], 'missing-cli')

        fake = FakeHarnesses(self.tmp, missing={'claude', 'opencode'},
                             reviews={'pi': [{'stdout': PLAN_TEXT}]})
        self.use(fake)
        _, entry = self.rt('plan', run, '--context', self.handoff('c\n'), '--session-harness', 'pi',
                           '--session-model', 'some-provider/session-model', '--session-effort', 'high')
        self.assertEqual((entry['selected']['priority'], entry['selected']['harness'], entry['selected']['model']),
                         (4, 'pi', 'some-provider/session-model'))
        argv = fake.review_calls()[-1]
        self.assertEqual(argv[argv.index('--thinking') + 1], 'high', 'session effort preserved')


class RoutingFailureTests(RoutedCase):

    def review(self, run, fake, *extra):
        self.use(fake)
        return self.rt('review', run, '--kind', 'code', '--base', self.base, *(extra + SESSION))

    def test_3_unknown_quota_is_attempted_not_skipped(self):
        run = self.start('frontend-ui-ux-overhaul')
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD)}]})
        _, entry = self.review(run, fake)
        self.assertEqual(entry['candidates'][0]['status'], 'selected')
        self.assertFalse([c for c in fake.calls if c[:3] == ['opencode', 'api', 'model.list']],
                         'no lower probe, no preliminary call')

    def test_3_auth_failure_exhaustion_and_unsupported_model_fall_back_without_substitution(self):
        run = self.start('frontend-ui-ux-overhaul')
        models = {'data': [{'id': 'deepseek-v4.1-flash-preview', 'providerID': 'opencode-go', 'status': 'active',
                            'variants': [{'id': 'high'}]}]}
        fake = FakeHarnesses(self.tmp, claude_logged_in=False, opencode_models=models,
                             reviews={'claude-code': [{'stdout': claude_result(GOOD)}]})
        _, entry = self.review(run, fake)
        reasons = [c['reason'] for c in entry['candidates']]
        self.assertEqual(reasons[0], 'not-authenticated')
        self.assertEqual(reasons[1], 'unsupported-model', 'a near id is never substituted')
        self.assertEqual(entry['selected']['priority'], 4, 'invoking session (Claude Code, its own model/effort)')
        argv = fake.review_calls()[-1]
        self.assertEqual((argv[argv.index('--model') + 1], argv[argv.index('--effort') + 1]),
                         ('claude-opus-5-5', 'high'))

    def test_4_mid_task_exhaustion_and_bounded_transient_retries(self):
        run = self.start('frontend-ui-ux-overhaul')
        rejected = claude_result('partial', error=True, status='rejected')
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': rejected, 'rc': 1}],
                                                'opencode': [{'stdout': json.dumps(
                                                    {'type': 'text', 'part': {'text': GOOD}})}]})
        _, entry = self.review(run, fake)
        self.assertEqual(entry['selected']['model'], 'deepseek-v4.1-flash')
        self.assertEqual(entry['candidates'][0]['attempts'][0]['category'], 'exhausted')
        attempts = os.path.join(os.path.dirname(entry['entry']), 'attempts')
        self.assertTrue(any(os.path.isfile(os.path.join(attempts, d, 'DISCARDED')) for d in os.listdir(attempts)))

        run = self.start('frontend-ui-ux-overhaul')  # fresh run: the exhaustion above is remembered per run
        busy = fail(stderr='529 overloaded')
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [busy, busy],
                                                'opencode': [{'stdout': json.dumps(
                                                    {'type': 'text', 'part': {'text': GOOD}})}]})
        _, entry = self.review(run, fake)
        self.assertEqual(len(entry['candidates'][0]['attempts']), 2, 'one retry, then fall back')
        self.assertEqual(entry['selected']['model'], 'deepseek-v4.1-flash')
        self.assertEqual(len(fake.sleeps), 1)

    def test_11_harness_compatibility(self):
        run = self.start('consolidate')
        code, out = self.rt('review', run, '--kind', 'code', '--base', self.base, '--session-harness', 'pi')
        self.assertEqual(code, 1)
        self.assertIn('--session-model', out['error'], 'adapter harness must name its model')
        fake = FakeHarnesses(self.tmp, missing={'claude', 'opencode'})
        self.use(fake)
        code, entry = self.rt('review', run, '--kind', 'code', '--base', self.base, '--session-harness', 'freebuff',
                              '--session-model', 'x')
        self.assertEqual(entry['status'], 'handoff', 'a harness without an adapter hands off to a native subagent')
        self.assertIn('routed_task.py record', entry['next'])
        out = os.path.join(self.tmp, 'native.md')
        write(out, GOOD)
        code, recorded = self.rt('record', run, '--task-id', entry['id'], '--output', out, '--by', 'freebuff/x@high')
        self.assertEqual((code, recorded['status'], recorded['verdict']), (0, 'completed', 'pass'))
        code, again = self.rt('record', run, '--task-id', entry['id'], '--output', out, '--by', 'freebuff/x@high')
        self.assertEqual(code, 1, 'only a pending handoff can be recorded')

    def test_nested_orchestration_refused(self):
        run = self.start('consolidate')
        os.environ[rr.GUARD_ENV] = '1'
        self.addCleanup(os.environ.pop, rr.GUARD_ENV, None)
        rt.ROUTER_FACTORY = None
        code, out = self.rt('review', run, '--kind', 'code', '--base', self.base, *SESSION)
        self.assertEqual(code, 1)
        self.assertIn('nested', out['error'])


class ReviewGateTests(RoutedCase):

    def test_7_8_gate_blocks_until_required_review_passes_on_the_current_candidate(self):
        run = self.start('frontend-ui-ux-overhaul')
        self.commit('app.js', 'export const a = 2;\n')
        code, gate = self.rt('gate', run, '--require', 'code')
        self.assertEqual(code, 1)
        self.assertIn('not run', gate['problems'][0])

        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(FAIL)}]})
        self.use(fake)
        _, entry = self.rt('review', run, '--kind', 'code', '--base', self.base, '--required', *SESSION)
        self.assertEqual(entry['verdict'], 'fail')
        code, gate = self.rt('gate', run)
        self.assertEqual(code, 1, 'required review declared once stays required')

        self.commit('app.js', 'export const a = 3;\n', 'fix finding')  # fix in the execution session
        _, stale = self.rt('gate', run)
        self.assertIn('not run', stale['problems'][0], 'the old candidate\'s review never counts')
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD)}]})
        self.use(fake)
        _, entry = self.rt('review', run, '--kind', 'code', '--base', self.base, '--required', *SESSION)
        self.assertEqual(entry['verdict'], 'pass')
        code, gate = self.rt('gate', run)
        self.assertEqual((code, gate['ok']), (0, True))
        _, again = self.rt('review', run, '--kind', 'code', '--base', self.base, '--required', *SESSION)
        self.assertTrue(again['reused'])
        self.assertEqual(len(fake.review_calls()), 1, 'unchanged candidate: no second review')

        write(os.path.join(self.repo, 'new.js'), 'x\n')  # untracked edit is a new candidate
        self.assertEqual(self.rt('gate', run)[0], 1)

    def test_7_unavailable_required_review_blocks(self):
        run = self.start('frontend-ui-ux-overhaul')
        bad = fail(stderr='not logged in')
        self.use(FakeHarnesses(self.tmp, missing={'opencode'},
                               reviews={'claude-code': [bad, bad]}))
        _, entry = self.rt('review', run, '--kind', 'code', '--base', self.base, '--required', *SESSION)
        self.assertEqual(entry['status'], 'unavailable')
        self.assertEqual(self.rt('gate', run)[0], 1)

    def test_tampered_review_artifact_does_not_satisfy_the_gate(self):
        run = self.start('frontend-ui-ux-overhaul')
        self.use(FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD)}]}))
        _, entry = self.rt('review', run, '--kind', 'code', '--base', self.base, '--required', *SESSION)
        with open(os.path.join(os.path.dirname(entry['entry']), 'review.md'), 'a', encoding='utf-8') as handle:
            handle.write('edited by the author\n')
        self.assertEqual(self.rt('gate', run)[0], 1)


class SkillSpecificTests(RoutedCase):

    def test_10_frontend_visual_reviews_need_rendered_evidence_or_an_honest_absence(self):
        run = self.start('frontend-ui-ux-overhaul')
        for kind in ('ui-ux', 'accessibility'):
            code, out = self.rt('review', run, '--kind', kind, '--base', self.base, *SESSION)
            self.assertEqual(code, 1)
            self.assertIn('--rendered-evidence', out['error'])
        manifest = os.path.join(self.tmp, 'rendered.md')
        write(manifest, 'mobile 390x844: /tmp/shot-mobile.png\ndesktop 1440: /tmp/shot-desktop.png\nkeyboard: tab order ok\n')
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD)},
                                                                {'stdout': claude_result(GOOD)}]})
        self.use(fake)
        _, entry = self.rt('review', run, '--kind', 'ui-ux', '--base', self.base, '--rendered-evidence', manifest,
                           '--required', *SESSION)
        self.assertEqual((entry['status'], entry['rendered_evidence']), ('completed', 'manifest'))
        with open(os.path.join(os.path.dirname(entry['entry']), 'prompt.md'), encoding='utf-8') as handle:
            prompt = handle.read()
        self.assertIn('Rendered/browser evidence manifest', prompt)
        self.assertIn('never claim that check happened', prompt)
        _, entry = self.rt('review', run, '--kind', 'accessibility', '--base', self.base,
                           '--no-rendered-evidence', 'no browser in this sandbox', *SESSION)
        self.assertEqual(entry['rendered_evidence'], 'none: no browser in this sandbox')
        with open(os.path.join(os.path.dirname(entry['entry']), 'rendered.txt'), encoding='utf-8') as handle:
            self.assertIn('Visual verification did not happen', handle.read())
        self.assertNotEqual(*[e['id'] for e in self.rt('status', run)[1]['reviews']], 'distinct review categories')

    def test_9_consolidate_reconciliation_review_sees_helper_collected_git_facts(self):
        sh(['git', 'checkout', '-q', '-b', 'feature'], self.repo)
        self.commit('feature.js', 'still needed\n', 'feature work')
        sh(['git', 'checkout', '-q', 'main'], self.repo)
        write(os.path.join(self.repo, 'app.js'), 'export const a = 9;\n')
        sh(['git', 'stash', 'push', '-q', '-m', 'wip'], self.repo)
        clone = os.path.join(self.tmp, 'clone')
        sh(['git', 'clone', '-q', self.repo, clone], self.tmp)
        run = self.start('consolidate')
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD)}]})
        self.use(fake)
        ledger = self.handoff('feature: discard approved by user 2026-11-04\nstash@{0}: discard approved\n')
        code, entry = self.rt('review', run, '--kind', 'reconciliation', '--base', self.base, '--context', ledger,
                              '--location', clone, '--required', *SESSION)
        self.assertEqual((code, entry['status']), (0, 'completed'), entry)
        with open(os.path.join(os.path.dirname(entry['entry']), 'git-inventory.md'), encoding='utf-8') as handle:
            inventory = handle.read()
        self.assertIn('refs/heads/feature ahead=1 unique=1', inventory)
        self.assertIn('wip', inventory)
        self.assertIn('## location %s' % clone, inventory)
        self.assertEqual(self.rt('gate', run)[0], 0)
        sh(['git', 'branch', 'late-branch', 'feature'], self.repo)  # a new resource appears before cleanup
        code, gate = self.rt('gate', run)
        self.assertEqual(code, 1)
        self.assertIn('changed since the review', ' '.join(gate['problems']))

    def test_12_dependency_upgrade_router_is_shared_not_copied(self):
        self.assertEqual(os.path.realpath(rr.__file__), os.path.join(SKILL_DIR, 'scripts', 'review_router.py'))
        self.assertEqual([(c['model'], c['effort'], c['harness']) for c in rr.PREFERRED],
                         [('claude-opus-5-5', 'medium', 'claude-code'),
                          ('deepseek-v4.1-flash', 'high', 'opencode')])
        with open(os.path.join(SKILL_DIR, 'scripts', 'routed_task.py'), encoding='utf-8') as handle:
            source = handle.read()
        self.assertNotIn('class Router', source, 'no second router implementation')


class ReviewFindingRegressionTests(RoutedCase):
    """Independent review 2026-11-04: each Important/Nit finding pinned by a test."""

    def test_rerun_without_required_never_downgrades_and_unavailable_latest_blocks(self):
        run = self.start('frontend-ui-ux-overhaul')
        self.use(FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD)}]}))
        self.rt('review', run, '--kind', 'code', '--required', *SESSION)
        self.assertEqual(self.rt('gate', run)[0], 0)
        bad = fail(stderr='not logged in')
        self.use(FakeHarnesses(self.tmp, missing={'opencode'},
                               reviews={'claude-code': [bad, bad]}))
        _, entry = self.rt('review', run, '--kind', 'code', '--rerun', *SESSION)  # forgot --required
        self.assertEqual(entry['status'], 'unavailable')
        self.assertTrue(entry['required'], 'rerun keeps the required flag')
        code, gate = self.rt('gate', run)
        self.assertEqual(code, 1, 'the newest review of a required kind could not run')

    def test_newer_context_that_cannot_be_reviewed_blocks_an_older_pass(self):
        run = self.start('consolidate')
        self.use(FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD)}]}))
        self.rt('review', run, '--kind', 'reconciliation', '--required', '--context', self.handoff('ledger A\n'),
                *SESSION)
        self.use(FakeHarnesses(self.tmp, missing={'claude', 'opencode'}))
        self.rt('review', run, '--kind', 'reconciliation', '--required', '--context', self.handoff('ledger B\n'),
                '--session-harness', 'freebuff', '--session-model', 'x')
        self.assertEqual(self.rt('gate', run)[0], 1)

    def test_base_is_bound_to_the_run(self):
        run = self.start('frontend-ui-ux-overhaul')
        self.commit('app.js', 'export const a = 5;\n')
        code, out = self.rt('review', run, '--kind', 'code', '--base', 'HEAD', *SESSION)
        self.assertEqual(code, 1)
        self.assertIn('differs from the run base', out['error'])

    def test_inventory_is_never_truncated_and_carries_patch_contents_and_read_access(self):
        sh(['git', 'checkout', '-q', '-b', 'ported'], self.repo)
        self.commit('ported.js', 'UNIQUE-PATCH-LINE\n', 'hand-port me')
        sh(['git', 'checkout', '-q', 'main'], self.repo)
        write(os.path.join(self.repo, 'app.js'), 'STASHED-LINE\n')
        sh(['git', 'stash', 'push', '-q', '-m', 'wip'], self.repo)
        wt = os.path.join(self.tmp, 'wt')
        sh(['git', 'worktree', 'add', '-q', '--detach', wt], self.repo)
        write(os.path.join(wt, 'app.js'), 'WORKTREE-EDIT\n')
        big = ''.join('f%06d.txt\n' % i for i in range(60000))  # far beyond the old 400k cut
        inventory = rt.git_inventory(self.repo, 'main', [], False) + big
        self.assertGreater(len(inventory), 400000)
        run = self.start('consolidate')
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD)}]})
        self.use(fake)
        _, entry = self.rt('review', run, '--kind', 'reconciliation', '--required', '--location', 'wt', *SESSION)
        with open(os.path.join(os.path.dirname(entry['entry']), 'git-inventory.md'), encoding='utf-8') as handle:
            text = handle.read()
        for marker in ('UNIQUE-PATCH-LINE', 'STASHED-LINE', 'WORKTREE-EDIT'):
            self.assertIn(marker, text)
        self.assertNotIn('truncated', text)
        self.assertEqual(entry['inventory']['locations'], [os.path.abspath('wt')], 'locations stored absolute')
        argv = fake.review_calls()[0]
        self.assertIn(os.path.realpath(wt), argv[argv.index('--add-dir') + 1:], 'reviewer may read other worktrees')
        self.assertEqual(entry['inventory']['main_ref'], 'main', 'default branch detected')

    def test_rendered_files_are_copied_where_the_reviewer_can_read_them(self):
        shot = os.path.join(self.tmp, 'evidence', 'mobile.png')
        write(shot, 'PNGDATA')
        manifest = os.path.join(self.tmp, 'rendered.md')
        secret = os.path.join(self.tmp, 'proj', '.dev.vars')
        write(secret, 'API_TOKEN=SECRET-TOKEN-VALUE\n')
        write(manifest, 'mobile 390x844: %s\nmissing: /nonexistent/x.png\nbuilt with env from %s\n' % (shot, secret))
        run = self.start('frontend-ui-ux-overhaul')
        self.use(FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD)}] * 2}))
        _, entry = self.rt('review', run, '--kind', 'ui-ux', '--rendered-evidence', manifest, *SESSION)
        task_dir = os.path.dirname(entry['entry'])
        copied = os.listdir(os.path.join(task_dir, 'rendered-files'))
        self.assertEqual(copied, ['01-mobile.png'])  # the secret file named in the manifest is never copied
        write(shot, 'NEW-SCREENSHOT')  # new screenshot = new review, not a reuse
        _, again = self.rt('review', run, '--kind', 'ui-ux', '--rendered-evidence', manifest, *SESSION)
        self.assertFalse(again.get('reused'))
