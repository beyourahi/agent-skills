"""Reviewer routing: one selected model per review, usage-aware fallback, honest PASS gating.

Every CLI (claude, opencode, pi) is mocked; no test makes a model call.
"""
import hashlib
import json
import os
import re

from tests.support import TempCase, depup, git_init, load, sh, write

rr = load('review_router_under_test', 'scripts/review_router.py')

GOOD = ('Looked at every hunk.\n%s\n{"verdict": "pass", "summary": "clean", "findings": '
        '[{"severity": "Nit", "file": "a.js", "line": 1, "summary": "naming", "scenario": "none"}]}\n%s\n'
        % (rr.RESULT_BEGIN, rr.RESULT_END))
PARTIAL_FINDING = 'Important: src/db.js:3 drops a column (partial, cut off)'
CLAUDE_HELP = '  --effort <level>    Effort level for the current session\n    (low, medium, high, xhigh, max)\n'
OPENCODE_MODELS = {'data': [
    {'id': 'deepseek-v4.1-flash', 'providerID': 'opencode-go', 'name': 'DeepSeek V4.1 Flash', 'status': 'active',
     'variants': [{'id': 'low'}, {'id': 'high'}, {'id': 'max'}]},
    {'id': 'deepseek-v4.1-flash-preview', 'providerID': 'opencode-go', 'name': 'DeepSeek V4.1 Flash Preview',
     'status': 'active', 'variants': [{'id': 'high'}, {'id': 'max'}]},
]}


def ok(stdout='', stderr=''):
    return {'rc': 0, 'stdout': stdout, 'stderr': stderr, 'error': None}


def fail(stdout='', stderr='', rc=1):
    return {'rc': rc, 'stdout': stdout, 'stderr': stderr, 'error': None}


def claude_result(text, model='claude-opus-5-5', error=False, status='allowed', api_status=None):
    events = [{'type': 'system', 'subtype': 'init'},
              {'type': 'rate_limit_event', 'rate_limit_info': {'status': status, 'resetsAt': 1893456000,
                                                               'rateLimitType': 'five_hour', 'utilization': 0.4}},
              {'type': 'result', 'subtype': 'error_during_execution' if error else 'success', 'is_error': error,
               'api_error_status': api_status, 'result': text, 'modelUsage': {model: {'outputTokens': 10}}}]
    return '\n'.join(json.dumps(e) for e in events) + '\n'


class FakeHarnesses:
    """Scripted CLIs. `reviews[harness]` is a list of responses consumed one per review run."""

    def __init__(self, tmp, missing=(), claude_logged_in=True, claude_help=CLAUDE_HELP,
                 opencode_models=OPENCODE_MODELS, reviews=None):
        self.missing = set(missing)
        self.claude_logged_in, self.claude_help = claude_logged_in, claude_help
        self.opencode_models = opencode_models
        self.reviews = {k: list(v) for k, v in (reviews or {}).items()}
        self.calls = []
        self.cwds = []
        self.sleeps = []

    def which(self, name):
        return None if name in self.missing else '/usr/bin/' + name

    def review_calls(self):
        return [c for c in self.calls if c[:2] in (['claude', '-p'], ['opencode', 'run'])
                or (c[0] == 'pi' and '-p' in c)]

    def runner(self, argv, cwd=None, env=None, timeout=60):
        self.calls.append(list(argv))
        self.cwds.append(cwd)
        name = argv[0]
        if argv[1:] == ['--version']:
            return ok('%s 1.0\n' % name)
        if argv[:2] == ['claude', '--help']:
            return ok(self.claude_help)
        if argv[:3] == ['claude', 'auth', 'status']:
            return ok(json.dumps({'loggedIn': self.claude_logged_in, 'authMethod': 'claude.ai',
                                  'email': 'someone@example.invalid', 'subscriptionType': 'pro'}))
        if argv[:3] == ['opencode', 'api', 'model.list']:
            return ok(json.dumps(self.opencode_models))
        harness = {'claude': 'claude-code', 'opencode': 'opencode', 'pi': 'pi'}[name]
        assert env and env.get(rr.GUARD_ENV) == '1', 'reviewers run with the recursion guard set'
        response = self.reviews[harness].pop(0)
        return {k: response.get(k, d) for k, d in (('rc', 0), ('stdout', ''), ('stderr', ''), ('error', None))}


def make_router(fake, environ=None, max_retries=1):
    return rr.Router(runner=fake.runner, which=fake.which, sleep=fake.sleeps.append, environ=environ or {},
                     timeout=60, max_retries=max_retries, retry_delay=7)


class RouterCase(TempCase):
    def ctx(self):
        reviews_dir = os.path.join(self.tmp, 'run', 'reviews')
        review_dir = os.path.join(reviews_dir, 'critical-full-diff-abc')
        os.makedirs(review_dir, exist_ok=True)
        return {'review_id': 'critical-full-diff-abc', 'review_dir': review_dir, 'reviews_dir': reviews_dir,
                'worktree': self.tmp, 'prompt': 'PROMPT', 'redact': depup.redact_text}

    def route(self, fake, session=None, environ=None, max_retries=1):
        return make_router(fake, environ, max_retries).route(self.ctx(), session or {})


class RouterUnitTests(RouterCase):

    def test_1_opus_available_selects_opus_with_one_call(self):
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD)}]})
        evidence, selected, done = self.route(fake)
        self.assertEqual((selected['harness'], selected['model'], selected['effort']),
                         ('claude-code', 'claude-opus-5-5', 'medium'))
        self.assertEqual(len(fake.review_calls()), 1, 'exactly one reviewer call')
        argv = fake.review_calls()[0]
        self.assertEqual(argv[argv.index('--model') + 1], 'claude-opus-5-5')
        self.assertEqual(argv[argv.index('--effort') + 1], 'medium')
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Grep,Glob', 'reviewer is read-only')
        self.assertNotIn('--fallback-model', argv)
        review_cwd = fake.cwds[fake.calls.index(argv)]
        self.assertTrue(review_cwd.startswith(self.ctx()['review_dir']), 'reviewers never run inside the repo')
        self.assertIn(self.tmp, argv[argv.index('--add-dir') + 1:], 'the worktree is granted read access')
        self.assertEqual(done[1]['verdict'], 'pass')
        self.assertEqual(evidence['candidates'][0]['usage'], 'confirmed', 'rate_limit_event confirms usage')
        self.assertFalse([c for c in fake.calls if c[:3] == ['opencode', 'api', 'model.list']],
                         'lower candidates are not even probed')

    def test_2_opus_confirmed_exhausted_selects_deepseek_without_calling_opus(self):
        fake = FakeHarnesses(self.tmp, reviews={'opencode': [{'stdout': json.dumps(
            {'type': 'text', 'part': {'text': GOOD}})}]})
        router = make_router(fake)
        ctx = self.ctx()
        router.remember(ctx['reviews_dir'], rr.PREFERRED[0], rr.EXHAUSTED, 'run failed: exhausted', 'earlier',
                        router.clock() + 600)
        evidence, selected, _ = router.route(ctx, {})
        self.assertEqual((selected['model'], selected['effort']), ('deepseek-v4.1-flash', 'high'))
        self.assertFalse([c for c in fake.calls if c[0] == 'claude'], 'confirmed exhaustion is not re-tried')
        self.assertEqual(evidence['candidates'][0]['status'], rr.EXHAUSTED)
        argv = fake.review_calls()[0]
        self.assertEqual(argv[argv.index('--model') + 1], 'opencode-go/deepseek-v4.1-flash#high')
        self.assertEqual(argv[argv.index('--agent') + 1], 'depup-reviewer')

    def test_3_claude_unavailable_selects_deepseek_flash_on_opencode_go(self):
        fake = FakeHarnesses(self.tmp, missing={'claude'},
                             reviews={'opencode': [{'stdout': json.dumps({'type': 'text', 'part': {'text': GOOD}})}]})
        evidence, selected, done = self.route(fake)
        self.assertEqual(selected['priority'], 3)
        argv = fake.review_calls()[0]
        self.assertEqual(argv[argv.index('--model') + 1], 'opencode-go/deepseek-v4.1-flash#high')
        self.assertEqual(argv[argv.index('--agent') + 1], 'depup-reviewer')
        self.assertEqual(evidence['candidates'][0]['status'], rr.UNAVAILABLE)
        self.assertEqual(done[1]['verdict'], 'pass')

    def test_4_all_routes_unavailable_uses_invoking_session_model_and_effort(self):
        fake = FakeHarnesses(self.tmp, missing={'claude', 'opencode'}, reviews={'pi': [{'stdout': GOOD}]})
        evidence, selected, _ = self.route(fake, {'harness': 'pi', 'model': 'session-model', 'effort': 'xhigh'})
        self.assertEqual((selected['priority'], selected['model'], selected['effort']),
                         (4, 'session-model', 'xhigh'))
        argv = fake.review_calls()[0]
        self.assertEqual(argv[argv.index('--model') + 1], 'session-model')
        self.assertEqual(argv[argv.index('--thinking') + 1], 'xhigh')
        # Unknown harness or model: the invoking session must run it natively, never a guessed CLI.
        fake2 = FakeHarnesses(self.tmp, missing={'claude', 'opencode'})
        evidence2, selected2, done2 = self.route(fake2, {'harness': 'freebuff', 'model': 'x'})
        self.assertEqual(evidence2['outcome'], 'handoff')
        self.assertIsNone(done2)
        self.assertEqual(fake2.review_calls(), [])

    def test_5_unknown_usage_attempts_preferred_model_without_preliminary_calls(self):
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD, status=None)}]})
        evidence, selected, _ = self.route(fake)
        self.assertEqual(selected['priority'], 1)
        self.assertEqual(evidence['candidates'][0]['usage'], 'unknown', 'unknown is reported, not assumed')
        self.assertEqual(len(fake.review_calls()), 1, 'no preliminary model call, only the review itself')

    def test_6_mid_review_exhaustion_discards_partial_and_restarts_on_next(self):
        partial = claude_result(PARTIAL_FINDING + '\nClaude AI usage limit reached', error=True, status='rejected',
                                api_status=429)
        opencode_done = {'stdout': json.dumps({'type': 'text', 'part': {'text': GOOD}})}
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': partial, 'rc': 1}],
                                                'opencode': [dict(opencode_done)]})
        router = make_router(fake)
        ctx = self.ctx()
        evidence, selected, done = router.route(ctx, {})
        self.assertEqual(selected['model'], 'deepseek-v4.1-flash')
        first = evidence['candidates'][0]
        self.assertEqual(first['status'], rr.EXHAUSTED)
        self.assertEqual(first['attempts'][0]['category'], 'exhausted')
        self.assertTrue(os.path.isfile(os.path.join(first['attempts'][0]['attempt_dir'], 'DISCARDED')))
        self.assertNotIn('drops a column', done[0], 'partial findings never reach the accepted review')
        self.assertEqual(len(fake.review_calls()), 2)
        # The exhaustion is remembered: the next review of the run skips Opus without calling it.
        fake.reviews['opencode'] = [dict(opencode_done)]
        router.route(ctx, {})
        self.assertEqual(len([c for c in fake.review_calls() if c[0] == 'claude']), 1)

    def test_7_transient_errors_retry_once_then_fall_back(self):
        overloaded = {'stdout': claude_result('API Error: 529 overloaded', error=True, api_status=529), 'rc': 1}
        opencode_done = {'stdout': json.dumps({'type': 'text', 'part': {'text': GOOD}})}
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [overloaded, dict(overloaded)],
                                                'opencode': [dict(opencode_done)]})
        evidence, selected, _ = self.route(fake)
        self.assertEqual(selected['model'], 'deepseek-v4.1-flash')
        self.assertEqual([a['category'] for a in evidence['candidates'][0]['attempts']], ['transient', 'transient'])
        self.assertEqual(fake.sleeps, [7], 'one bounded retry with backoff')
        recovered = FakeHarnesses(self.tmp, reviews={'claude-code': [dict(overloaded), {'stdout': claude_result(GOOD)}]})
        _, selected2, _ = self.route(recovered)
        self.assertEqual(selected2['priority'], 1, 'a transient error that clears keeps the preferred model')
        timed_out = FakeHarnesses(self.tmp, reviews={'claude-code': [{'rc': None, 'error': 'timeout after 60s'}],
                                                     'opencode': [dict(opencode_done)]})
        evidence3, selected3, _ = self.route(timed_out)
        self.assertEqual(selected3['priority'], 3)
        self.assertEqual(len(evidence3['candidates'][0]['attempts']), 1, 'a timeout is not retried')

    def test_8_unsupported_model_or_effort_moves_on_and_never_substitutes(self):
        no_medium = CLAUDE_HELP.replace('medium, ', '')
        only_other_build = {'data': [OPENCODE_MODELS['data'][1]]}  # another build only: not the configured model
        fake = FakeHarnesses(self.tmp, claude_help=no_medium, opencode_models=only_other_build,
                             reviews={'pi': [{'stdout': GOOD}]})
        evidence, selected, _ = self.route(fake, {'harness': 'pi', 'model': 'anthropic/claude-sonnet-5-5',
                                                  'effort': 'high'})
        self.assertEqual([c['reason'] for c in evidence['candidates'][:2]],
                         ['unsupported-effort', 'unsupported-model'])
        self.assertEqual(selected['priority'], 4)
        self.assertFalse([c for c in fake.calls if c[:2] == ['opencode', 'run']],
                         'another build id is never used in place of deepseek-v4.1-flash')
        os.remove(os.path.join(self.ctx()['reviews_dir'], 'provider-state.json'))  # unsupported is cached per run
        substituted = FakeHarnesses(self.tmp, reviews={
            'claude-code': [{'stdout': claude_result(GOOD, model='claude-sonnet-5-5')}],
            'opencode': [{'stdout': json.dumps({'type': 'text', 'part': {'text': GOOD}})}]})
        evidence2, selected2, _ = self.route(substituted)
        self.assertEqual(evidence2['candidates'][0]['attempts'][0]['category'], 'substituted')
        self.assertEqual(selected2['priority'], 3)

    def test_9_all_routes_fail_reports_unavailable(self):
        fake = FakeHarnesses(self.tmp, missing={'claude', 'opencode', 'pi'})
        evidence, selected, done = self.route(fake, {'harness': 'pi', 'model': 'm', 'effort': 'high'})
        self.assertEqual(evidence['outcome'], 'unavailable')
        self.assertIsNone(selected)
        self.assertEqual(fake.review_calls(), [])

    def test_10_same_priorities_from_every_harness(self):
        orders = []
        for environ in ({'CLAUDECODE': '1'}, {'OPENCODE': '1'}, {}):
            fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result('x', error=True,
                                                                                            status='rejected')}],
                                                    'opencode': [{'stdout': json.dumps({'type': 'text',
                                                                                        'part': {'text': GOOD}})}]})
            evidence, selected, _ = self.route(fake, {'model': 'session-model', 'effort': 'high'}, environ=environ)
            orders.append(([c['priority'] for c in evidence['candidates']], selected['model'],
                           [c[0] for c in fake.review_calls()]))
            os.remove(os.path.join(self.ctx()['reviews_dir'], 'provider-state.json'))
        self.assertEqual(len({json.dumps(o) for o in orders}), 1, orders)
        self.assertEqual(orders[0], ([1, 3], 'deepseek-v4.1-flash', ['claude', 'opencode']))

    def test_session_fallback_skips_a_definitively_failed_duplicate(self):
        fake = FakeHarnesses(self.tmp, missing={'opencode'},
                             reviews={'claude-code': [{'stdout': claude_result('x', error=True, status='rejected')}]})
        evidence, selected, _ = self.route(fake, {'harness': 'claude-code', 'model': 'claude-opus-5-5',
                                                  'effort': 'medium'})
        self.assertIsNone(selected)
        self.assertIn('already failed', evidence['candidates'][-1]['reason'])
        self.assertEqual(len(fake.review_calls()), 1)

    def test_retired_codex_or_gpt_session_is_refused_without_any_call(self):
        for session in ({'harness': 'codex', 'model': 'any-model'},
                        {'harness': 'pi', 'model': 'openai-codex/gpt-6.1-sol', 'effort': 'high'}):
            fake = FakeHarnesses(self.tmp, missing={'claude', 'opencode'})
            evidence, selected, done = self.route(fake, session)
            self.assertEqual(evidence['outcome'], 'unavailable', session)
            self.assertIsNone(selected)
            self.assertIsNone(done)
            self.assertIn('retired', evidence['candidates'][-1]['reason'])
            self.assertEqual(fake.review_calls(), [], 'no reviewer run, no handoff to a retired route')

    def test_preferred_candidates_never_use_codex_or_a_gpt_model(self):
        self.assertEqual([c['priority'] for c in rr.PREFERRED], [1, 3])
        self.assertEqual([c['harness'] for c in rr.PREFERRED], ['claude-code', 'opencode'])
        self.assertFalse([c for c in rr.PREFERRED if c['model'].lower().startswith('gpt-')])
        self.assertNotIn('codex', rr.HARNESS_CLI)
        self.assertNotIn('codex', rr.HARNESS_ALIASES)

    def test_nested_orchestration_refused(self):
        fake = FakeHarnesses(self.tmp)
        with self.assertRaises(RuntimeError):
            self.route(fake, environ={rr.GUARD_ENV: '1'})
        self.assertEqual(fake.calls, [])

    def test_result_parsing_is_strict(self):
        self.assertIsNone(rr.parse_result('no block')[0])
        placeholder = '%s\n{"verdict": "<pass|fail>", "findings": []}\n%s' % (rr.RESULT_BEGIN, rr.RESULT_END)
        self.assertIsNone(rr.parse_result(placeholder)[0], 'the prompt template never counts as a result')
        lying = '%s\n{"verdict": "pass", "findings": [{"severity": "Important", "summary": "bug"}]}\n%s' % (
            rr.RESULT_BEGIN, rr.RESULT_END)
        self.assertEqual(rr.parse_result(lying)[0]['verdict'], 'fail')


class IntegrationCase(TempCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, depup, 'ROUTER_FACTORY', None)

    def upgrade_run(self):
        root = os.path.join(self.tmp, 'repo')
        write(os.path.join(root, 'package.json'), {'name': 'r', 'dependencies': {'a': '^1.0.0'}})
        write(os.path.join(root, 'package-lock.json'), {'lockfileVersion': 3, 'packages': {'a': '1.0.0'}})
        git_init(root)
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        _, worktree = self.cli('run', 'worktree', start['run_ref'])
        wt = worktree['worktree']
        write(os.path.join(wt, 'package.json'), {'name': 'r', 'dependencies': {'a': '^2.0.0'}})
        write(os.path.join(wt, 'package-lock.json'), {'lockfileVersion': 3, 'packages': {'a': '2.0.0'}})
        sh(['git', 'add', '-A'], wt)
        sh(['git', 'commit', '-q', '-m', 'deps: a 1 -> 2'], wt)
        return start, wt

    def use(self, fake):
        depup.ROUTER_FACTORY = lambda **kw: make_router(fake, max_retries=kw.get('max_retries', 1))


class ReviewIntegrationTests(IntegrationCase):

    def test_review_run_binds_receipt_and_reuses_completed_review(self):
        start, wt = self.upgrade_run()
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD)}]})
        self.use(fake)
        code, entry = self.cli('review', 'run', start['run_ref'], '--kind', 'critical', '--required')
        self.assertEqual(code, 0, entry)
        self.assertEqual((entry['status'], entry['verdict']), ('completed', 'pass'))
        self.assertEqual(entry['selected']['model'], 'claude-opus-5-5')
        review_dir = os.path.dirname(entry['entry'])
        with open(os.path.join(review_dir, 'lockfile.diff'), encoding='utf-8') as handle:
            self.assertIn('2.0.0', handle.read(), 'the lockfile diff is reviewed in full')
        with open(os.path.join(review_dir, 'code.diff'), encoding='utf-8') as handle:
            code_diff = handle.read()
        self.assertIn('package.json', code_diff)
        self.assertNotIn('package-lock.json', code_diff)
        candidate = depup.git_state(wt)['head']
        self.assertTrue(depup.bound_evidence(start['run_dir'], entry['evidence'], entry['candidate_head'] and
                                             start['run_ref'].split('/')[1], candidate, 'critical-review',
                                             'full-diff', 'pass'))
        with open(os.path.join(review_dir, 'routing.json'), encoding='utf-8') as handle:
            routing = handle.read()
        self.assertNotIn('someone@example.invalid', routing, 'no account data in evidence')
        _, again = self.cli('review', 'run', start['run_ref'], '--kind', 'critical')
        self.assertTrue(again['reused'])
        self.assertEqual(len(fake.review_calls()), 1, 'no duplicate review of the same candidate')
        write(os.path.join(review_dir, 'review.lock'), {'pid': os.getpid()})
        code, busy = self.cli('review', 'run', start['run_ref'], '--kind', 'critical', '--rerun')
        self.assertEqual(code, 1)
        self.assertIn('already running', busy['error'])
        dead = sh(['python3', '-c', 'import os; print(os.getpid())'], self.tmp).stdout.strip()
        write(os.path.join(review_dir, 'review.lock'), {'pid': int(dead)})
        fake.reviews['claude-code'] = [{'stdout': claude_result(GOOD)}]
        code, rerun = self.cli('review', 'run', start['run_ref'], '--kind', 'critical', '--rerun')
        self.assertEqual((code, rerun['status']), (0, 'completed'), 'a dead holder\'s lock is reclaimed')

    def test_9_required_review_unavailable_prevents_pass(self):
        start, wt = self.upgrade_run()
        ref, run_dir = start['run_ref'], start['run_dir']
        self.use(FakeHarnesses(self.tmp, missing={'claude', 'opencode', 'pi'}))
        _, sec = self.cli('review', 'run', ref, '--kind', 'security', '--subject', 'db', '--required',
                          '--session-harness', 'pi', '--session-model', 'm', '--session-effort', 'high')
        self.assertEqual(sec['status'], 'unavailable')
        self.assertTrue(sec['required'])
        with open(start['journal'], encoding='utf-8') as handle:
            run_id = json.load(handle)['run_id']
        candidate = depup.git_state(wt)['head']
        write(os.path.join(run_dir, 'logs', 'tests.log'), 'pass\n')
        digest = hashlib.sha256(b'pass\n').hexdigest()
        write(os.path.join(run_dir, 'logs', 'tests.json'), {
            'run_id': run_id, 'candidate_head': candidate, 'kind': 'verification', 'subject': 'npm test',
            'result': 'pass', 'reviewer': 'runner', 'artifact': 'logs/tests.log', 'artifact_sha256': digest})
        for step in ('baseline', 'verify', 'review'):
            self.cli('run', 'step', ref, '--id', step, '--status', 'done')
        payload = {'status': 'PASS', 'verification': [{'check': 'tests', 'command': 'npm test', 'result': 'pass',
                                                       'log': 'logs/tests.log', 'evidence': 'logs/tests.json'}]}
        path = os.path.join(self.tmp, 'in.json')
        write(path, payload)
        _, rep = self.cli('report', ref, '--input', path)
        self.assertEqual(rep['status'], 'PARTIAL', 'leaving the unavailable required review out cannot hide it')
        self.assertTrue(any('required review' in c for c in rep['corrections']), rep['corrections'])
        payload['reviews'] = [sec]
        write(path, payload)
        _, rep = self.cli('report', ref, '--input', path)
        self.assertEqual(rep['status'], 'PARTIAL')
        self.assertTrue(any('required review' in c for c in rep['corrections']), rep['corrections'])
        with open(rep['summary'], encoding='utf-8') as handle:
            self.assertIn('## Reviews', handle.read())
        # A forged "completed" entry without a candidate-bound receipt cannot unlock PASS either.
        forged = dict(sec, status='completed', verdict='pass', evidence='logs/tests.json')
        payload['reviews'] = [forged]
        write(path, payload)
        _, rep = self.cli('report', ref, '--input', path)
        self.assertEqual(rep['status'], 'PARTIAL')

    def test_session_handoff_records_native_review(self):
        start, wt = self.upgrade_run()
        self.use(FakeHarnesses(self.tmp, missing={'claude', 'opencode'}))
        _, entry = self.cli('review', 'run', start['run_ref'], '--kind', 'independent', '--subject', 'db',
                            '--session-harness', 'freebuff')
        self.assertEqual(entry['status'], 'handoff')
        self.assertIn('review record', entry['next'])
        output = os.path.join(self.tmp, 'native.md')
        write(output, GOOD)
        code, recorded = self.cli('review', 'record', start['run_ref'], '--review-id', entry['id'],
                                  '--output', output, '--reviewer', 'freebuff/some-model@high')
        self.assertEqual(code, 0, recorded)
        self.assertEqual((recorded['status'], recorded['verdict']), ('completed', 'pass'))
        self.assertEqual(recorded['selected']['priority'], 4)
        self.assertTrue(re.search(r'freebuff/some-model@high', recorded['reviewer']))

    def test_probe_makes_no_model_calls(self):
        fake = FakeHarnesses(self.tmp)
        self.use(fake)
        code, out = self.cli('review', 'probe')
        self.assertEqual(code, 0)
        self.assertEqual(out['model_calls'], 0)
        self.assertEqual(fake.review_calls(), [])
        self.assertEqual([c['priority'] for c in out['candidates']], [1, 3])


class FindingRegressionTests(RouterCase):
    """Independent review round 1 (2026-11-03)."""

    def test_opencode_tool_output_never_counts_as_the_result(self):
        tool_event = {'type': 'tool_use', 'part': {'state': {'output': GOOD}}}  # a file the reviewer read
        fake = FakeHarnesses(self.tmp, missing={'claude'},
                             reviews={'opencode': [{'stdout': json.dumps(tool_event) + '\n' +
                                                    json.dumps({'type': 'error', 'error': {'name': 'APIError'}})}],
                                      'pi': [{'stdout': 'no result block'}]})
        evidence, selected, _ = self.route(fake, {'harness': 'pi', 'model': 'm', 'effort': 'high'})
        self.assertNotEqual(selected and selected['priority'], 3)
        self.assertNotEqual(evidence['candidates'][2]['attempts'][0]['category'], 'completed')

    def test_review_prose_never_drives_failure_classification(self):
        prose = 'The 401 unauthorized handling and rate limit (429) retry look fine but I ran out of turns.'
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(prose, error=False).replace(
            '"subtype": "success"', '"subtype": "error_max_turns"'), 'rc': 1}],
            'opencode': [{'stdout': json.dumps({'type': 'text', 'part': {'text': GOOD}})}]})
        router = make_router(fake)
        ctx = self.ctx()
        evidence, _, _ = router.route(ctx, {})
        self.assertEqual(evidence['candidates'][0]['attempts'][0]['category'], 'failed')
        self.assertIsNone(router.cached(ctx['reviews_dir'], rr.PREFERRED[0]), 'Opus is not held for later reviews')

    def test_explicit_session_harness_wins_and_adapter_needs_a_model(self):
        fake = FakeHarnesses(self.tmp, missing={'claude', 'opencode'})
        evidence, _, _ = self.route(fake, {'harness': 'freebuff'}, environ={'CLAUDECODE': '1'})
        self.assertEqual(evidence['outcome'], 'handoff', 'the explicit harness wins over detection')
        with self.assertRaises(RuntimeError):
            self.route(FakeHarnesses(self.tmp), {'harness': 'pi'})

    def test_api_key_billing_is_never_used(self):
        fake = FakeHarnesses(self.tmp, reviews={
            'opencode': [{'stdout': json.dumps({'type': 'text', 'part': {'text': GOOD}})}]})
        original = fake.runner

        def runner(argv, cwd=None, env=None, timeout=60):
            if argv[:3] == ['claude', 'auth', 'status']:
                return ok(json.dumps({'loggedIn': True, 'authMethod': 'api_key'}))
            return original(argv, cwd=cwd, env=env, timeout=timeout)
        fake.runner = runner
        router = make_router(fake, environ={'ANTHROPIC_API_KEY': 'x'})
        _, selected, _ = router.route(self.ctx(), {})
        self.assertEqual((selected['priority'], selected['harness']), (3, 'opencode'))
        self.assertFalse([c for c in fake.review_calls() if c[0] == 'claude'],
                         'API-key billing is never used for Claude')
        self.assertNotIn('ANTHROPIC_API_KEY', router.child_env('claude-code'))


class DossierTests(IntegrationCase):
    def test_routed_independent_and_security_reviews_satisfy_a_high_risk_dossier(self):
        start, wt = self.upgrade_run()
        ref, run_dir = start['run_ref'], start['run_dir']
        fake = FakeHarnesses(self.tmp, reviews={'claude-code': [{'stdout': claude_result(GOOD)},
                                                                {'stdout': claude_result(GOOD)}]})
        self.use(fake)
        _, ind = self.cli('review', 'run', ref, '--kind', 'independent', '--subject', 'db', '--required',
                          '--session-harness', 'pi', '--session-model', 'm')
        _, sec = self.cli('review', 'run', ref, '--kind', 'security', '--subject', 'db', '--required',
                          '--session-harness', 'pi', '--session-model', 'm')
        self.assertNotEqual(ind['reviewer'], sec['reviewer'], 'same model, distinct fresh sessions')
        with open(start['journal'], encoding='utf-8') as handle:
            run_id = json.load(handle)['run_id']
        candidate = depup.git_state(wt)['head']
        write(os.path.join(run_dir, 'logs', 'tests.log'), 'pass\n')
        write(os.path.join(run_dir, 'logs', 'tests.json'), {
            'run_id': run_id, 'candidate_head': candidate, 'kind': 'verification', 'subject': 'npm test',
            'result': 'pass', 'reviewer': 'runner', 'artifact': 'logs/tests.log',
            'artifact_sha256': hashlib.sha256(b'pass\n').hexdigest()})
        for step in ('baseline', 'verify', 'review'):
            self.cli('run', 'step', ref, '--id', step, '--status', 'done')
        payload = {'status': 'PASS', 'reviews': [ind, sec],
                   'verification': [{'check': 'tests', 'command': 'npm test', 'result': 'pass',
                                     'log': 'logs/tests.log', 'evidence': 'logs/tests.json'}],
                   'upgrades': [{'name': 'db', 'impact_tier': 'high', 'status': 'done'}],
                   'risks': [{'name': 'db', 'summary': 'db', 'severity': 'high', 'disposition': 'proceed',
                              'analysis': 'mapped', 'recovery': 'revert on owned branch',
                              'sources': [{'name': 'guide', 'url': 'https://example.test', 'retrieved': '2026-11-03'}],
                              'alternatives': ['stay'], 'controls': ['staged'],
                              'reviews': [{'type': 'independent', 'status': 'pass', 'evidence': ind['evidence']},
                                          {'type': 'security', 'status': 'pass', 'evidence': sec['evidence']}]}]}
        path = os.path.join(self.tmp, 'in.json')
        write(path, payload)
        _, rep = self.cli('report', ref, '--input', path)
        self.assertEqual(rep['status'], 'PASS', rep['corrections'])
