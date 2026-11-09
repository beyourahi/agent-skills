"""Architecture/planning routing: same router, priority and fallback as reviews; read-only, advisory.

Every CLI is mocked through the review-router fakes; no test makes a model call.
"""
import json
import os

from tests.support import depup, git_init, sh, write
from tests.test_review_router import (GOOD, FakeHarnesses, IntegrationCase, claude_result, make_router, rr)

PLAN = {'summary': 'Upgrade a 1 -> 2 in one batch.',
        'decisions': [{'decision': 'single batch', 'rationale': 'no coupled family'}],
        'steps': [{'id': 'batch-1', 'summary': 'npm install a@2', 'checks': ['npm test']}],
        'risks': [], 'holds': []}
PLAN_TEXT = 'Plan follows.\n%s\n%s\n%s\n' % (rr.PLAN_BEGIN, json.dumps(PLAN), rr.PLAN_END)
EXHAUSTED_EVENTS = '\n'.join(json.dumps(e) for e in (
    {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'resetsAt': 4102444800}},
    {'type': 'result', 'subtype': 'error_during_execution', 'is_error': True, 'result': 'usage limit reached',
     'modelUsage': {}})) + '\n'


class PlanningTests(IntegrationCase):

    def plan_run(self):
        root = os.path.join(self.tmp, 'repo')
        write(os.path.join(root, 'package.json'), {'name': 'r', 'dependencies': {'a': '^1.0.0'}})
        git_init(root)
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'plan')
        return start, root

    def handoff(self, text='scope: all-stable\noutdated: a 1.0.0 -> 2.0.0 (major)\ntoken=SECRET-TOKEN-VALUE\n'):
        path = os.path.join(self.tmp, 'handoff.md')
        write(path, text)
        return path

    def run_plan(self, ref, *extra):
        return self.cli('planning', 'run', ref, '--context', self.handoff(), *extra)

    def test_opus_plans_once_read_only_outside_the_repo_with_the_handoff(self):
        start, root = self.plan_run()
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(PLAN_TEXT)}]})
        self.use(fake)
        code, entry = self.run_plan(start['run_ref'])
        self.assertEqual(code, 0, entry)
        self.assertEqual(entry['status'], 'completed')
        self.assertEqual((entry['selected']['model'], entry['selected']['effort']), ('claude-opus-5-5', 'medium'))
        self.assertEqual(entry['target'], root, 'plan mode reads the repository (no worktree yet)')
        self.assertEqual(entry['plan']['steps'][0]['id'], 'batch-1')
        calls = fake.review_calls()
        self.assertEqual(len(calls), 1, 'one planner call')
        argv = calls[0]
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Grep,Glob', 'planner is read-only')
        plan_dir = os.path.dirname(entry['entry'])
        self.assertTrue(fake.cwds[fake.calls.index(argv)].startswith(plan_dir), 'planner cwd is outside the repo')
        self.assertIn(root, argv[argv.index('--add-dir') + 1:])
        with open(os.path.join(plan_dir, 'prompt.md'), encoding='utf-8') as handle:
            prompt = handle.read()
        self.assertIn(os.path.join(plan_dir, 'context.md'), prompt, 'prompt points at the handoff')
        self.assertIn('Do not edit or create files', prompt)
        with open(os.path.join(plan_dir, 'context.md'), encoding='utf-8') as handle:
            context = handle.read()
        self.assertIn('a 1.0.0 -> 2.0.0', context)
        self.assertNotIn('SECRET-TOKEN-VALUE', context, 'handoff is redacted')
        self.assertTrue(os.path.isfile(os.path.join(plan_dir, 'receipt.json')))
        self.assertTrue(depup.git_state(root)['clean'], 'planning never writes into the repository')
        _, again = self.run_plan(start['run_ref'])
        self.assertTrue(again['reused'])
        self.assertEqual(len(fake.review_calls()), 1, 'same head + handoff: no second model call')

    def test_fallback_order_matches_reviews(self):
        start, _ = self.plan_run()
        self.use(FakeHarnesses(self.tmp, missing={'claude'}, reviews={'opencode': [{'stdout': json.dumps(
            {'type': 'text', 'part': {'text': PLAN_TEXT}})}]}))
        _, entry = self.run_plan(start['run_ref'])
        self.assertEqual((entry['selected']['harness'], entry['selected']['model'], entry['selected']['effort']),
                         ('opencode', 'deepseek-v4.1-flash', 'high'))
        self.assertEqual(entry['selected']['resolved_model'], 'opencode-go/deepseek-v4.1-flash')
        self.assertEqual(entry['candidates'][0]['reason'], 'missing-cli', 'fallback reason recorded')

        start, _ = self.plan_run()
        self.use(FakeHarnesses(self.tmp, missing={'claude', 'opencode'}, reviews={'pi': [{'stdout': PLAN_TEXT}]}))
        _, entry = self.run_plan(start['run_ref'], '--session-harness', 'pi', '--session-model', 'pm',
                                 '--session-effort', 'high')
        self.assertEqual((entry['selected']['priority'], entry['selected']['model']), (4, 'pm'))

    def test_exhausted_opus_is_remembered_across_planning_and_reviews(self):
        start, _ = self.plan_run()
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': EXHAUSTED_EVENTS, 'rc': 1}],
                                                'opencode': [{'stdout': json.dumps(
                                                    {'type': 'text', 'part': {'text': PLAN_TEXT}})}]})
        self.use(fake)
        _, entry = self.run_plan(start['run_ref'])
        self.assertEqual(entry['selected']['harness'], 'opencode')
        self.assertEqual(entry['candidates'][0]['status'], 'exhausted')
        self.assertTrue(os.path.isfile(os.path.join(start['run_dir'], 'plans', entry['id'], 'attempts',
                                                    '01-p1-claude-code', 'DISCARDED')))
        state = os.path.join(start['run_dir'], 'reviews', 'provider-state.json')
        with open(state, encoding='utf-8') as handle:
            self.assertEqual(json.load(handle)['claude-code/claude-opus-5-5@medium']['status'], 'exhausted')
        router = make_router(fake)
        probe = router.probe(rr.PREFERRED[0], os.path.join(start['run_dir'], 'reviews'), 'x')
        self.assertTrue(probe['reason'].startswith('cached'), 'a later review skips the exhausted model')

    def test_invalid_plan_falls_back_and_all_unavailable_hands_off_to_the_session(self):
        start, _ = self.plan_run()
        fake = FakeHarnesses(self.tmp, missing={'opencode'},
                             reviews={'claude-code': [{'stdout': claude_result(GOOD)}],  # a review block, not a plan
                                      'pi': [{'stdout': PLAN_TEXT}]})
        self.use(fake)
        _, entry = self.run_plan(start['run_ref'], '--session-harness', 'pi', '--session-model', 'pm',
                                 '--session-effort', 'high')
        self.assertEqual(entry['candidates'][0]['attempts'][0]['category'], 'incomplete')
        self.assertEqual((entry['selected']['priority'], entry['selected']['model']), (4, 'pm'),
                         'final fallback: the session model and effort exactly')
        start, _ = self.plan_run()
        self.use(FakeHarnesses(self.tmp, missing={'claude', 'opencode'}))
        _, entry = self.run_plan(start['run_ref'], '--session-harness', 'freebuff')
        self.assertEqual(entry['status'], 'handoff')
        self.assertIn('planning record', entry['next'])
        out = os.path.join(self.tmp, 'native-plan.md')
        write(out, PLAN_TEXT)
        code, rec = self.cli('planning', 'record', start['run_ref'], '--plan-id', entry['id'], '--output', out,
                             '--planner', 'freebuff/m@high')
        self.assertEqual((code, rec['status'], rec['selected']['priority']), (0, 'completed', 4))
        self.use(FakeHarnesses(self.tmp, missing={'claude', 'opencode', 'pi'}))
        _, none = self.cli('planning', 'run', start['run_ref'], '--context', self.handoff('other'),
                           '--session-harness', 'pi', '--session-model', 'm')
        self.assertEqual(none['status'], 'unavailable')

    def test_planner_that_writes_to_the_tree_is_discarded(self):
        start, root = self.plan_run()
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(PLAN_TEXT)}]})
        original = fake.runner

        def mutating(argv, **kw):
            if argv[:2] == ['claude', '-p']:
                write(os.path.join(root, 'package.json'), {'name': 'tampered'})
            return original(argv, **kw)
        fake.runner = mutating
        self.use(fake)
        _, entry = self.run_plan(start['run_ref'])
        self.assertEqual(entry['status'], 'unavailable')
        self.assertIn('changed during planning', entry['reason'])
        self.assertNotIn('plan', entry)

    def test_architecture_task_on_the_worktree_and_report_records_planners(self):
        start, wt = self.upgrade_run()
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(PLAN_TEXT)},
                                                                {'stdout': claude_result(GOOD)}]})
        self.use(fake)
        code, refused = self.cli('planning', 'run', start['run_ref'], '--kind', 'architecture',
                                 '--context', self.handoff())
        self.assertEqual(code, 1)
        self.assertIn('--subject', refused['error'])
        _, arch = self.cli('planning', 'run', start['run_ref'], '--kind', 'architecture', '--subject', 'db',
                           '--context', self.handoff())
        self.assertEqual((arch['status'], arch['target']), ('completed', wt))
        _, review = self.cli('review', 'run', start['run_ref'], '--kind', 'critical')
        self.assertEqual((review['status'], review['verdict']), ('completed', 'pass'), 'reviews unaffected')
        path = os.path.join(self.tmp, 'in.json')
        write(path, {'status': 'PARTIAL', 'reviews': [review], 'planning': []})
        _, rep = self.cli('report', start['run_ref'], '--input', path)
        with open(rep['report'], encoding='utf-8') as handle:
            report = json.load(handle)
        self.assertEqual([p['kind'] for p in report['planning']], ['architecture'], 'disk entries are authoritative')
        self.assertEqual(report['planning'][0]['selected']['model'], 'claude-opus-5-5')
        with open(rep['summary'], encoding='utf-8') as handle:
            summary = handle.read()
        self.assertIn('## Architecture and planning', summary)
        self.assertIn('architecture db: completed — claude-code/claude-opus-5-5@medium', summary)
        self.assertIn('## Reviews', summary)

    def test_handoff_summary_record_guard_and_dirty_tree_reuse_key(self):
        start, root = self.plan_run()
        self.use(FakeHarnesses(self.tmp, missing={'claude', 'opencode'}))
        _, entry = self.run_plan(start['run_ref'], '--session-harness', 'freebuff')
        _, rep = self.cli('report', start['run_ref'])
        with open(rep['summary'], encoding='utf-8') as handle:
            summary = handle.read()
        self.assertIn('plan upgrade: handoff — no routed planner completed', summary)
        self.assertNotIn('handoff — None', summary)
        write(os.path.join(root, 'b.txt'), 'x')
        sh(['git', 'add', '-A'], root)
        sh(['git', 'commit', '-q', '-m', 'move head'], root)
        out = os.path.join(self.tmp, 'native-plan.md')
        write(out, PLAN_TEXT)
        code, refused = self.cli('planning', 'record', start['run_ref'], '--plan-id', entry['id'], '--output', out,
                                 '--planner', 'freebuff/m@high')
        self.assertEqual(code, 1)
        self.assertIn('head changed', refused['error'])
        _, first = self.run_plan(start['run_ref'], '--session-harness', 'freebuff')
        write(os.path.join(root, 'package.json'), {'name': 'r', 'dependencies': {'a': '^1.5.0'}})  # uncommitted
        _, second = self.run_plan(start['run_ref'], '--session-harness', 'freebuff')
        self.assertNotEqual(first['id'], second['id'], 'uncommitted work changes the reuse key')

    def test_checkout_change_during_planning_is_reported(self):
        start, root = self.plan_run()
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(PLAN_TEXT)}]})
        original = fake.runner

        def mutating(argv, **kw):
            if argv[:2] == ['claude', '-p']:
                write(os.path.join(root, 'stray.txt'), 'x')
            return original(argv, **kw)
        fake.runner = mutating
        self.use(fake)
        _, entry = self.run_plan(start['run_ref'])
        self.assertTrue(entry['tree_changed'])
        _, rep = self.cli('report', start['run_ref'])
        with open(rep['report'], encoding='utf-8') as handle:
            report = json.load(handle)
        self.assertFalse(report['rollback']['user_checkout_untouched'])
        self.assertTrue(any('user checkout changed' in c for c in rep['corrections']))

    def test_nested_planning_is_refused_inside_a_routed_session(self):
        router = rr.Router(environ={rr.GUARD_ENV: '1'})
        with self.assertRaises(RuntimeError):
            router.route({'review_id': 'x'}, {})

    def test_plan_parser_is_strict(self):
        self.assertIsNone(rr.parse_plan_result('no block')[0])
        empty = dict(PLAN, steps=[])
        text = '%s\n%s\n%s' % (rr.PLAN_BEGIN, json.dumps(empty), rr.PLAN_END)
        self.assertIsNone(rr.parse_plan_result(text, 'plan')[0], 'a plan needs steps')
        self.assertIsNotNone(rr.parse_plan_result(text, 'architecture')[0], 'architecture needs decisions only')
        nodecision = '%s\n%s\n%s' % (rr.PLAN_BEGIN, json.dumps(dict(PLAN, decisions=[])), rr.PLAN_END)
        self.assertIsNone(rr.parse_plan_result(nodecision, 'architecture')[0])
        self.assertIsNone(rr.parse_result(PLAN_TEXT)[0], 'a plan never counts as a review result')


if __name__ == '__main__':
    import unittest
    unittest.main()
