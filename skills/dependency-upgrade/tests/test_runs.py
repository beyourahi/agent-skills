import json
import hashlib
import os
import unittest
from unittest import mock

from tests.support import TempCase, depup, git_init, sh, write


class RunLifecycleTests(TempCase):
    def make_repo(self, name='repo', dirty=True):
        root = os.path.join(self.tmp, name)
        write(os.path.join(root, 'package.json'), {'name': name, 'dependencies': {'a': '^1.0.0'}})
        write(os.path.join(root, 'package-lock.json'), '{}')
        git_init(root)
        if dirty:
            write(os.path.join(root, 'package.json'), {'name': name, 'dependencies': {'a': '^1.0.0'}, 'userEdit': True})
            write(os.path.join(root, 'notes-untracked.txt'), 'user work')
        return root

    def snapshot(self, root):
        files = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != '.git']
            for f in filenames:
                p = os.path.join(dirpath, f)
                with open(p, 'rb') as handle:
                    files[os.path.relpath(p, root)] = handle.read()
        status = sh(['git', 'status', '--porcelain'], root).stdout
        head = sh(['git', 'rev-parse', 'HEAD'], root).stdout
        branches = sh(['git', 'branch', '--list'], root).stdout
        return files, status, head, branches

    def test_plan_run_is_read_only_and_reports_plan(self):
        root = self.make_repo()
        before = self.snapshot(root)
        code, start = self.cli('run', 'start', '--repo', root)
        self.assertEqual(code, 0)
        self.assertIn('notes-untracked.txt', start['dirty_files_preserved'])
        self.assertTrue(start['run_dir'].startswith(self.state))
        code, out = self.cli('run', 'worktree', start['run_ref'])
        self.assertEqual(code, 1, 'plan runs must not create worktrees')
        code, rep = self.cli('report', start['run_ref'])
        self.assertEqual(rep['status'], 'PLAN')
        path = os.path.join(self.tmp, 'in.json')
        write(path, {'status': 'PASS', 'verification': [{'check': 't', 'result': 'pass'}]})
        _, rep = self.cli('report', start['run_ref'], '--input', path)
        self.assertEqual(rep['status'], 'PLAN')
        self.assertEqual(self.snapshot(root), before, 'plan mode must not change the repository')

    def test_upgrade_lock_blocks_concurrent_mutation(self):
        root = self.make_repo(dirty=False)
        code, first = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        self.assertEqual(code, 0)
        code, second = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        self.assertEqual(code, 1)
        self.assertIn('mutation lock', second['error'])
        code, plan = self.cli('run', 'start', '--repo', root, '--mode', 'plan')
        self.assertEqual(code, 0, 'read-only plan runs may proceed while an upgrade holds the lock')
        _, fin = self.cli('run', 'finish', first['run_ref'])
        self.assertTrue(fin['lock_released'])
        code, _ = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        self.assertEqual(code, 0)

    def test_upgrade_requires_git_and_no_operation_in_progress(self):
        plain = os.path.join(self.tmp, 'plain')
        write(os.path.join(plain, 'package.json'), {'name': 'x'})
        code, out = self.cli('run', 'start', '--repo', plain, '--mode', 'upgrade')
        self.assertEqual(code, 1)
        root = self.make_repo('merging', dirty=False)
        git_dir = sh(['git', 'rev-parse', '--absolute-git-dir'], root).stdout.strip()
        write(os.path.join(git_dir, 'MERGE_HEAD'), '0' * 40)
        code, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        self.assertEqual(code, 0, 'a checkout operation only matters when its HEAD would be the base')
        code, out = self.cli('run', 'worktree', start['run_ref'])
        self.assertEqual(code, 1)
        self.assertIn('merge', out['error'])

    def test_worktree_isolation_dirty_checkout_untouched(self):
        root = self.make_repo()
        before = self.snapshot(root)
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        code, wt = self.cli('run', 'worktree', start['run_ref'])
        self.assertEqual(code, 0)
        self.assertTrue(wt['worktree'].startswith(start['run_dir']))
        with open(os.path.join(wt['worktree'], 'package.json')) as handle:
            worktree_pkg = json.load(handle)
        self.assertNotIn('userEdit', worktree_pkg, 'uncommitted user edits must not leak into the worktree')
        self.assertFalse(os.path.exists(os.path.join(wt['worktree'], 'notes-untracked.txt')))
        write(os.path.join(wt['worktree'], 'package.json'), {'name': 'repo', 'dependencies': {'a': '^2.0.0'}})
        sh(['git', 'commit', '-qam', 'deps: a 2'], wt['worktree'])
        files, status, head, branches = self.snapshot(root)
        self.assertEqual((files, status, head), before[:3], 'user checkout must stay byte-identical')
        self.assertIn('depup/', branches)
        _, again = self.cli('run', 'worktree', start['run_ref'])
        self.assertFalse(again['created'])

    def test_existing_branch_is_not_adopted(self):
        root = self.make_repo(dirty=False)
        sh(['git', 'branch', 'depup/fixed-id'], root)
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade', '--run-id', 'fixed-id')
        code, out = self.cli('run', 'worktree', start['run_ref'])
        self.assertEqual(code, 1)
        self.assertIn('not owned', out['error'])

    def test_steps_resume_and_dangerous_guard(self):
        root = self.make_repo(dirty=False)
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        ref = start['run_ref']
        _, worktree = self.cli('run', 'worktree', ref)
        self.cli('run', 'step', ref, '--id', 'baseline', '--status', 'done', '--evidence', 'logs/b.log')
        self.cli('run', 'step', ref, '--id', 'batch-1', '--status', 'done', '--dangerous')
        self.cli('run', 'step', ref, '--id', 'batch-2', '--status', 'pending', '--title', 'react 19')
        code, status = self.cli('run', 'status', '--repo', root)
        self.assertEqual(code, 0)
        self.assertEqual(status['next']['id'], 'batch-2')
        self.assertEqual(status['user_checkout_changes_since_start']['new_or_changed_paths'], [])
        write(os.path.join(root, '.gitignore'), 'x\n')
        _, status = self.cli('run', 'status', '--repo', root)
        self.assertEqual(status['user_checkout_changes_since_start']['new_or_changed_paths'], ['.gitignore'])
        self.assertEqual(status['completed_dangerous'], ['batch-1'])
        code, out = self.cli('run', 'step', ref, '--id', 'batch-1', '--status', 'running')
        self.assertEqual(code, 1)
        self.assertIn('do not repeat', out['error'])
        code, out = self.cli('run', 'step', ref, '--id', 'x', '--status', 'bogus')
        self.assertEqual(code, 1)
        code, _ = self.cli('run', 'status', start['run_dir'])
        self.assertEqual(code, 0, 'run_dir path is accepted as a run reference')

    def test_run_reference_traversal_refused(self):
        code, out = self.cli('run', 'status', '../../etc')
        self.assertEqual(code, 1)
        code, out = self.cli('rollback', '/etc')
        self.assertEqual(code, 1)
        outside = os.path.join(self.tmp, 'fake-run')
        write(os.path.join(outside, 'journal.json'), {'run_id': 'x'})
        code, out = self.cli('rollback', outside)
        self.assertEqual(code, 1)
        self.assertIn('outside the state root', out['error'])

    def test_rollback_preserves_commits_and_dirty_worktrees(self):
        root = self.make_repo()
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        _, wt = self.cli('run', 'worktree', start['run_ref'])
        write(os.path.join(wt['worktree'], 'index.js'), 'x')
        _, dry = self.cli('rollback', start['run_ref'])
        self.assertTrue(dry['dry_run'])
        self.assertEqual(dry['would_do'], [])
        self.assertTrue(any('uncommitted' in p.get('reason', '') for p in dry['preserved']))
        self.assertTrue(any(p.get('branch') == wt['branch'] for p in dry['preserved']))
        sh(['git', 'add', '-A'], wt['worktree'])
        sh(['git', 'commit', '-qm', 'owned work'], wt['worktree'])
        _, out = self.cli('rollback', start['run_ref'], '--apply')
        self.assertFalse(os.path.exists(wt['worktree']))
        kept = [p for p in out['preserved'] if p.get('branch')]
        self.assertNotIn('discard_command_for_user', kept[0])
        self.assertIn(wt['branch'], sh(['git', 'branch', '--list'], root).stdout)
        self.assertTrue(os.path.exists(os.path.join(root, 'notes-untracked.txt')))
        self.assertTrue(out['lock_released'])

    def test_rollback_clean_run_removes_owned_state(self):
        root = self.make_repo(dirty=False)
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        _, wt = self.cli('run', 'worktree', start['run_ref'])
        _, out = self.cli('rollback', start['run_ref'], '--apply')
        self.assertEqual([d['rc'] for d in out['done']], [0, 0])
        self.assertNotIn('depup/', sh(['git', 'branch', '--list'], root).stdout)
        self.assertFalse(os.path.exists(wt['worktree']))
        _, status = self.cli('run', 'status', start['run_ref'])
        self.assertEqual(status['status'], 'rolled-back')

    def test_report_downgrades_unsupported_pass(self):
        root = self.make_repo(dirty=False)
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        ref = start['run_ref']
        self.cli('run', 'worktree', ref)
        path = os.path.join(self.tmp, 'in.json')
        write(path, {'status': 'PASS'})
        _, rep = self.cli('report', ref, '--input', path)
        self.assertEqual(rep['status'], 'PARTIAL')
        write(path, {'status': 'PASS', 'verification': [{'check': 'tests', 'result': 'pass'},
                                                        {'check': 'build', 'result': 'baseline-fail'}]})
        _, rep = self.cli('report', ref, '--input', path)
        self.assertEqual(rep['status'], 'PARTIAL')
        write(os.path.join(start['run_dir'], 'logs', 'tests.log'), 'tests passed\n')
        passing = {'status': 'PASS', 'verification': [{'check': 'tests', 'command': 'test command',
                                                       'result': 'pass', 'log': 'logs/tests.log'}]}
        self.cli('run', 'step', ref, '--id', 'batch-1', '--status', 'failed')
        write(path, passing)
        _, rep = self.cli('report', ref, '--input', path)
        self.assertEqual(rep['status'], 'PARTIAL')
        self.cli('run', 'step', ref, '--id', 'batch-1', '--status', 'done')
        write(path, passing)
        _, rep = self.cli('report', ref, '--input', path)
        self.assertEqual(rep['status'], 'PARTIAL')
        with open(rep['summary']) as handle:
            summary = handle.read()
        self.assertIn('# Dependency upgrade: PARTIAL', summary)
        write(path, {'status': 'GREAT'})
        _, rep = self.cli('report', ref, '--input', path)
        self.assertEqual(rep['status'], 'PARTIAL')
        write(path, '[1,2]')
        code, _ = self.cli('report', ref, '--input', path)
        self.assertEqual(code, 1)


class ReviewRegressionTests(TempCase):
    """Round-one independent review findings (2026-11-02)."""

    def make_repo(self):
        root = os.path.join(self.tmp, 'r')
        write(os.path.join(root, 'package.json'), {'name': 'r'})
        write(os.path.join(root, 'package-lock.json'), '{}')
        git_init(root)
        return root

    def test_detached_head_commits_are_never_removed(self):
        root = self.make_repo()
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        _, wt = self.cli('run', 'worktree', start['run_ref'])
        sh(['git', 'checkout', '-q', '--detach'], wt['worktree'])
        write(os.path.join(wt['worktree'], 'x.txt'), 'detached work')
        sh(['git', 'add', '-A'], wt['worktree'])
        sh(['git', 'commit', '-qm', 'detached'], wt['worktree'])
        _, out = self.cli('rollback', start['run_ref'], '--apply')
        self.assertTrue(os.path.isdir(wt['worktree']))
        self.assertTrue(any('detached' in p.get('reason', '') for p in out['preserved']))
        self.assertEqual(out['status'], 'rolled-back-partial')

    def test_rebase_in_progress_preserved(self):
        root = self.make_repo()
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        _, wt = self.cli('run', 'worktree', start['run_ref'])
        git_dir = sh(['git', 'rev-parse', '--absolute-git-dir'], wt['worktree']).stdout.strip()
        os.makedirs(os.path.join(git_dir, 'rebase-merge'))
        _, dry = self.cli('rollback', start['run_ref'])
        self.assertTrue(any('operation in progress' in p.get('reason', '') for p in dry['preserved']))

    def test_parallel_workers_create_and_roll_back(self):
        root = self.make_repo()
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        ref = start['run_ref']
        code, _ = self.cli('run', 'worker', ref, '--domain', 'api')
        self.assertEqual(code, 1, 'integration worktree must exist first')
        _, wt = self.cli('run', 'worktree', ref)
        code, w1 = self.cli('run', 'worker', ref, '--domain', 'api')
        self.assertEqual(code, 0, w1)
        code, w2 = self.cli('run', 'worker', ref, '--domain', 'ui')
        self.assertEqual(code, 0, w2)
        self.assertNotEqual(w1['branch'], wt['branch'])
        code, bad = self.cli('run', 'worker', ref, '--domain', '../x')
        self.assertEqual(code, 1)
        write(os.path.join(w2['worktree'], 'ui.txt'), 'ui work')
        sh(['git', 'add', '-A'], w2['worktree'])
        sh(['git', 'commit', '-qm', 'ui'], w2['worktree'])
        _, out = self.cli('rollback', ref, '--apply')
        self.assertFalse(os.path.exists(w1['worktree']))
        self.assertFalse(os.path.exists(wt['worktree']))
        self.assertFalse(os.path.exists(w2['worktree']), 'clean worker worktree removed; its branch keeps the commit')
        branches = sh(['git', 'branch', '--list'], root).stdout
        self.assertIn(w2['branch'], branches)
        self.assertNotIn(w1['branch'], branches)
        self.assertEqual(out['status'], 'rolled-back-partial')

    def test_clone_isolation_for_read_only_git(self):
        root = self.make_repo()
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        code, wt = self.cli('run', 'worktree', start['run_ref'], '--isolation', 'clone')
        self.assertEqual(code, 0, wt)
        self.assertEqual(wt['isolation'], 'clone')
        self.assertNotIn('depup/', sh(['git', 'branch', '--list'], root).stdout, 'no ref written to the user repo')
        write(os.path.join(wt['worktree'], 'n.txt'), 'x')
        sh(['git', 'add', '-A'], wt['worktree'])
        sh(['git', 'commit', '-qm', 'work'], wt['worktree'])
        _, out = self.cli('rollback', start['run_ref'], '--apply')
        self.assertTrue(os.path.isdir(wt['worktree']), 'clone with commits is preserved')
        self.assertIn('fetch', out['preserved'][0]['deliver_with'])
        sh(['git', 'reset', '-q', '--hard', 'HEAD~1'], wt['worktree'])
        _, start2 = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        _, wt2 = self.cli('run', 'worktree', start2['run_ref'], '--isolation', 'clone')
        _, out2 = self.cli('rollback', start2['run_ref'], '--apply')
        self.assertFalse(os.path.exists(wt2['worktree']))
        self.assertEqual(out2['status'], 'rolled-back')

    def test_clone_with_stash_or_scratch_branch_is_preserved(self):
        root = self.make_repo()
        for setup in ('stash', 'branch', 'reset'):
            _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
            _, wt = self.cli('run', 'worktree', start['run_ref'], '--isolation', 'clone')
            w = wt['worktree']
            write(os.path.join(w, 'n.txt'), setup)
            if setup == 'stash':
                sh(['git', 'add', '-A'], w)
                sh(['git', 'stash', '-q'], w)
            elif setup == 'branch':
                sh(['git', 'checkout', '-q', '-b', 'scratch'], w)
                sh(['git', 'add', '-A'], w)
                sh(['git', 'commit', '-qm', 'alt'], w)
                sh(['git', 'checkout', '-q', wt['branch']], w)
            else:
                sh(['git', 'add', '-A'], w)
                sh(['git', 'commit', '-qm', 'tmp'], w)
                sh(['git', 'reset', '-q', '--hard', 'HEAD~1'], w)
            _, out = self.cli('rollback', start['run_ref'], '--apply')
            self.assertTrue(os.path.isdir(w), setup)
            self.assertEqual(out['status'], 'rolled-back-partial', setup)

    def test_worker_refused_under_clone_isolation(self):
        root = self.make_repo()
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        self.cli('run', 'worktree', start['run_ref'], '--isolation', 'clone')
        code, out = self.cli('run', 'worker', start['run_ref'], '--domain', 'api')
        self.assertEqual(code, 1)
        self.assertIn('worktree isolation', out['error'])

    def test_journal_lock_not_released_by_non_owner(self):
        root = self.make_repo()
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        first = depup.JournalLock(start['run_dir'])
        first.__enter__()
        intruder = depup.JournalLock(start['run_dir'])
        intruder.__exit__(None, None, None)
        self.assertTrue(os.path.exists(first.path), 'only the holder may release the lock')
        first.__exit__(None, None, None)
        self.assertFalse(os.path.exists(first.path))

    def test_finished_run_cannot_create_worktrees(self):
        root = self.make_repo()
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        self.cli('run', 'finish', start['run_ref'])
        code, out = self.cli('run', 'worktree', start['run_ref'])
        self.assertEqual(code, 1)
        self.assertIn('not active', out['error'])

    def test_dangerous_step_cannot_be_laundered(self):
        root = self.make_repo()
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        ref = start['run_ref']
        _, worktree = self.cli('run', 'worktree', ref)
        self.cli('run', 'step', ref, '--id', 'codemod', '--status', 'done', '--dangerous')
        for status in ('failed', 'blocked', 'skipped', 'pending', 'running'):
            code, _ = self.cli('run', 'step', ref, '--id', 'codemod', '--status', status)
            self.assertEqual(code, 1, status)
        code, out = self.cli('run', 'step', ref, '--id', 'codemod', '--status', 'running', '--force')
        self.assertEqual(code, 1)
        self.assertIn('requires --note', out['error'])
        code, out = self.cli('run', 'step', ref, '--id', 'codemod', '--status', 'running', '--force',
                             '--note', 'Verified preconditions, backup, and idempotent rerun path.')
        self.assertEqual(code, 1)
        self.assertIn('requires structured --evidence', out['error'])
        outside_evidence = os.path.join(self.tmp, 'repeat-review.md')
        write(outside_evidence, 'Verified preconditions and recovery.')
        code, out = self.cli('run', 'step', ref, '--id', 'codemod', '--status', 'running', '--force',
                             '--note', 'Verified preconditions, backup, and idempotent rerun path.',
                             '--evidence', outside_evidence)
        self.assertEqual(code, 1, 'evidence outside this run must not unlock a repeat')
        evidence = os.path.join(start['run_dir'], 'logs', 'repeat-review.json')
        write(evidence, {'step_id': 'codemod', 'decision': 'safe-to-repeat',
                         'candidate_head': depup.git_state(worktree['worktree'])['head'],
                         'preconditions': ['worktree is clean', 'backup verified'],
                         'reason': 'The exact transformation is idempotent for this candidate.',
                         'recovery': 'Restore from the verified backup if the rerun fails.'})
        code, out = self.cli('run', 'step', ref, '--id', 'codemod', '--status', 'running', '--force',
                             '--note', 'Verified preconditions, backup, and idempotent rerun path.',
                             '--evidence', 'logs/repeat-review.json')
        self.assertEqual(code, 0, out)

    def test_lock_message_names_holder_state(self):
        root = self.make_repo()
        _, first = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        _, second = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        self.assertIn(first['run_ref'].split('/')[1], second['error'])
        self.assertIn('journal status: active', second['error'])

    def test_report_enforces_documented_pass_rules(self):
        root = self.make_repo()
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        ref = start['run_ref']
        _, worktree = self.cli('run', 'worktree', ref)
        path = os.path.join(self.tmp, 'in.json')
        write(path, {'status': 'PASS', 'verification': [{'check': 'tests', 'result': 'pass'}]})
        _, no_evidence = self.cli('report', ref, '--input', path)
        self.assertEqual(no_evidence['status'], 'PARTIAL')
        ok = [{'check': 'tests', 'result': 'pass'}]
        for extra in ({'risks': [{'name': 'db', 'summary': 'db', 'severity': 'high',
                                 'disposition': 'proceed'}]},
                      {'risks': [{'name': 'db', 'summary': 'db', 'severity': 'high',
                                 'disposition': 'blocked'}]},
                      {'upgrades': [{'name': 'db', 'impact_tier': 'high', 'status': 'done'}]},
                      {'upgrades': [{'name': 'db', 'impact_tier': 'high', 'status': 'done'}],
                       'risks': [{'name': 'db', 'summary': 'lower-tier record', 'severity': 'medium',
                                  'disposition': 'proceed'}]},
                      {'skipped_checks': [{'check': 'e2e', 'required': True}]},
                      {'upgrades': [{'name': 'x', 'status': 'blocked'}]}):
            payload = {'status': 'PASS', 'verification': ok}
            payload.update(extra)
            write(path, payload)
            _, rep = self.cli('report', ref, '--input', path)
            self.assertEqual(rep['status'], 'PARTIAL', extra)
        write(path, {'status': 'PASS', 'verification': ok,
                     'upgrades': [{'name': 'db', 'impact_tier': 'high', 'status': 'done'}],
                     'risks': [{'name': 'db', 'summary': 'db', 'severity': 'high',
                                'disposition': 'proceed', 'analysis': 'Impact mapped; migration is additive.',
                                'sources': [{'name': 'official guide', 'url': 'https://example.test/guide',
                                             'version': '2', 'retrieved': '2026-11-03'}],
                                'alternatives': ['stay on current', 'use supported intermediate'],
                                'controls': ['backup', 'staged verification'],
                                'recovery': 'Revert code on owned branch; forward-fix persisted state.',
                                'reviews': [{'type': 'independent', 'status': 'pass',
                                             'evidence': 'logs/review-independent.txt'},
                                            {'type': 'security', 'status': 'pass',
                                             'evidence': 'logs/review-security.txt'}]}],
                     'skipped_checks': [{'check': 'browser', 'reason': 'n/a for CLI'}],
                     'rollback': {'bogus': 1}, 'mode': 'plan', 'branch': 'evil'})
        write(os.path.join(start['run_dir'], 'logs', 'tests.log'), 'test command: pass\n')
        write(os.path.join(start['run_dir'], 'logs', 'review-independent.txt'), 'Independent review passed.\n')
        write(os.path.join(start['run_dir'], 'logs', 'review-security.txt'), 'Security review passed.\n')
        with open(start['journal'], encoding='utf-8') as handle:
            run_id = json.load(handle)['run_id']
        candidate = depup.git_state(worktree['worktree'])['head']

        def receipt(filename, kind, subject, artifact, reviewer):
            artifact_path = os.path.join(start['run_dir'], artifact)
            with open(artifact_path, 'rb') as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            write(os.path.join(start['run_dir'], 'logs', filename), {
                'run_id': run_id, 'candidate_head': candidate, 'kind': kind, 'subject': subject,
                'result': 'pass', 'reviewer': reviewer, 'artifact': artifact, 'artifact_sha256': digest})
            return 'logs/' + filename

        independent_evidence = receipt('review-independent.json', 'independent-review', 'db',
                                       'logs/review-independent.txt', 'fresh-reviewer')
        security_evidence = receipt('review-security.json', 'security-review', 'db',
                                    'logs/review-security.txt', 'security-reviewer')
        test_evidence = receipt('test-evidence.json', 'verification', 'python -m unittest',
                                'logs/tests.log', 'test-runner')
        with open(path, encoding='utf-8') as handle:
            payload = json.load(handle)
        payload['risks'][0]['reviews'][0]['evidence'] = independent_evidence
        payload['risks'][0]['reviews'][1]['evidence'] = security_evidence
        payload['verification'] = [{'check': 'tests', 'command': 'python -m unittest',
                                    'result': 'pass', 'log': 'logs/tests.log',
                                    'evidence': test_evidence}]
        write(path, payload)
        _, rep = self.cli('report', ref, '--input', path)
        self.assertEqual(rep['status'], 'PARTIAL', 'PASS needs the baseline/verify/review steps recorded')
        self.assertIn('baseline', ' '.join(rep['corrections']))
        for step in ('baseline', 'verify', 'review'):
            self.cli('run', 'step', ref, '--id', step, '--status', 'done')
        _, rep = self.cli('report', ref, '--input', path)
        self.assertEqual(rep['status'], 'PASS')
        critical = json.loads(json.dumps(payload))
        critical['risks'].append({'name': 'auth', 'summary': 'session format', 'severity': 'critical',
                                  'disposition': 'blocked'})
        write(path, critical)
        _, crit_rep = self.cli('report', ref, '--input', path)
        self.assertEqual(crit_rep['status'], 'PARTIAL', 'a blocked critical risk cannot pass')
        write(path, payload)
        with open(path, encoding='utf-8') as handle:
            valid_payload = json.load(handle)
        reused = json.loads(json.dumps(valid_payload))
        reused['risks'][0]['reviews'][1]['evidence'] = independent_evidence
        write(path, reused)
        _, reused_rep = self.cli('report', ref, '--input', path)
        self.assertEqual(reused_rep['status'], 'PARTIAL', 'one receipt cannot satisfy both review roles')
        mismatched_log = json.loads(json.dumps(valid_payload))
        mismatched_log['verification'][0]['log'] = 'logs/review-independent.txt'
        write(path, mismatched_log)
        _, mismatch_rep = self.cli('report', ref, '--input', path)
        self.assertEqual(mismatch_rep['status'], 'PARTIAL', 'receipt must hash the report-named log')
        stale = json.loads(json.dumps(valid_payload))
        stale_receipt_path = os.path.join(start['run_dir'], test_evidence)
        with open(stale_receipt_path, encoding='utf-8') as handle:
            stale_receipt = json.load(handle)
        stale_receipt['candidate_head'] = '0' * len(candidate)
        write(stale_receipt_path, stale_receipt)
        write(path, stale)
        _, stale_rep = self.cli('report', ref, '--input', path)
        self.assertEqual(stale_rep['status'], 'PARTIAL', 'stale candidate evidence cannot satisfy PASS')
        with open(rep['report']) as handle:
            data = json.load(handle)
        self.assertEqual(data['mode'], 'upgrade')
        self.assertTrue(data['branch'].startswith('depup/'))
        self.assertEqual(data['candidate_commit'], candidate)
        self.assertEqual(data['rollback']['run_ref'], ref)


class AutonomyTests(TempCase):
    """Zero-touch execution: stale state, upstream base and delivery facts (2026-11-03)."""

    def origin_and_checkout(self):
        origin = os.path.join(self.tmp, 'origin.git')
        seed = os.path.join(self.tmp, 'seed')
        write(os.path.join(seed, 'package.json'), {'name': 'app', 'dependencies': {'a': '^1.0.0'}})
        write(os.path.join(seed, 'package-lock.json'), '{}')
        git_init(seed)
        sh(['git', 'init', '-q', '--bare', '-b', 'main', origin], self.tmp)
        sh(['git', 'push', '-q', origin, 'main'], seed)
        checkout = os.path.join(self.tmp, 'checkout')
        sh(['git', 'clone', '-q', origin, checkout], self.tmp)
        # upstream moves on after the user's clone
        write(os.path.join(seed, 'later.txt'), 'upstream change')
        sh(['git', 'add', '-A'], seed)
        sh(['git', 'commit', '-qm', 'upstream later'], seed)
        sh(['git', 'push', '-q', origin, 'main'], seed)
        upstream_tip = sh(['git', 'rev-parse', 'HEAD'], seed).stdout.strip()
        # the user sits on a local feature branch with an unpushed commit
        sh(['git', 'checkout', '-q', '-b', 'feature'], checkout)
        write(os.path.join(checkout, 'feature.txt'), 'wip')
        sh(['git', 'add', '-A'], checkout)
        sh(['git', 'commit', '-qm', 'feature work'], checkout)
        return checkout, upstream_tip

    def test_worktree_starts_from_fetched_upstream_default(self):
        checkout, tip = self.origin_and_checkout()
        before = sh(['git', 'rev-parse', 'HEAD', 'feature'], checkout).stdout
        _, start = self.cli('run', 'start', '--repo', checkout, '--mode', 'upgrade')
        code, wt = self.cli('run', 'worktree', start['run_ref'])
        self.assertEqual(code, 0, wt)
        self.assertEqual(wt['base'], tip, 'base is the freshly fetched upstream default, not the feature HEAD')
        self.assertEqual(wt['base_source'], 'fetched origin/main')
        self.assertEqual(wt['checkout_commits_not_in_base'], 1)
        self.assertTrue(os.path.exists(os.path.join(wt['worktree'], 'later.txt')))
        self.assertFalse(os.path.exists(os.path.join(wt['worktree'], 'feature.txt')))
        self.assertIn('push origin ' + wt['branch'], wt['push_with'])
        self.assertEqual(sh(['git', 'rev-parse', 'HEAD', 'feature'], checkout).stdout, before,
                         'fetch never moves the user branches')
        _, legacy = self.cli('run', 'start', '--repo', checkout, '--mode', 'upgrade')
        self.assertTrue(legacy.get('refused'), 'first run still holds the lock')
        self.cli('run', 'finish', start['run_ref'])
        _, second = self.cli('run', 'start', '--repo', checkout, '--mode', 'upgrade')
        _, wt2 = self.cli('run', 'worktree', second['run_ref'], '--base-source', 'head')
        self.assertEqual(wt2['base'], sh(['git', 'rev-parse', 'HEAD'], checkout).stdout.strip())

    def test_clone_isolation_fetches_upstream_and_can_push(self):
        checkout, tip = self.origin_and_checkout()
        _, start = self.cli('run', 'start', '--repo', checkout, '--mode', 'upgrade')
        code, wt = self.cli('run', 'worktree', start['run_ref'], '--isolation', 'clone')
        self.assertEqual(code, 0, wt)
        self.assertEqual(wt['base'], tip)
        self.assertIn('push upstream ' + wt['branch'], wt['push_with'])
        _, dry = self.cli('rollback', start['run_ref'])
        self.assertTrue(any(a.get('action') == 'remove clone' for a in dry['would_do']),
                        'an untouched clone is still removable after the upstream fetch')

    def test_push_with_works_for_relative_path_remotes(self):
        checkout, _ = self.origin_and_checkout()
        sh(['git', 'remote', 'set-url', 'origin', '../origin.git'], checkout)
        for isolation in ('worktree', 'clone'):
            _, start = self.cli('run', 'start', '--repo', checkout, '--mode', 'upgrade')
            _, wt = self.cli('run', 'worktree', start['run_ref'], '--isolation', isolation)
            write(os.path.join(wt['worktree'], 'change-%s.txt' % isolation), 'x')
            sh(['git', 'add', '-A'], wt['worktree'])
            sh(['git', 'commit', '-qm', 'deps: change'], wt['worktree'])
            proc = sh(wt['push_with'].split(), self.tmp, check=False)
            self.assertEqual(proc.returncode, 0, (isolation, proc.stderr))
            listed = sh(['git', 'branch', '--list', wt['branch']], os.path.join(self.tmp, 'origin.git')).stdout
            self.assertIn(wt['branch'], listed, isolation)
            self.cli('run', 'finish', start['run_ref'])

    def test_report_copies_evidence_outside_repo(self):
        root = os.path.join(self.tmp, 'ev')
        write(os.path.join(root, 'package.json'), {'name': 'ev'})
        git_init(root)
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'plan')
        write(os.path.join(start['run_dir'], 'logs', 'a.log'), 'log')
        target = os.path.join(self.tmp, 'dev-evidence', 'task')
        code, rep = self.cli('report', start['run_ref'], '--copy-to', target)
        self.assertEqual(code, 0, rep)
        for rel in ('report.json', 'summary.md', os.path.join('logs', 'a.log')):
            self.assertTrue(os.path.isfile(os.path.join(target, rel)), rel)
        code, refused = self.cli('report', start['run_ref'], '--copy-to', os.path.join(root, 'evidence'))
        self.assertEqual(code, 1, 'never copy evidence into the repository')

    def test_no_remote_falls_back_to_head_with_warning(self):
        root = os.path.join(self.tmp, 'local')
        write(os.path.join(root, 'package.json'), {'name': 'local'})
        git_init(root)
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        _, wt = self.cli('run', 'worktree', start['run_ref'])
        self.assertEqual(wt['base'], start['base_commit'])
        self.assertEqual(wt['base_source'], 'checkout HEAD (no upstream)')
        self.assertTrue(wt['warnings'])
        self.assertIsNone(wt['push_with'])

    def test_unreachable_remote_falls_back_without_crashing(self):
        root = os.path.join(self.tmp, 'gone')
        write(os.path.join(root, 'package.json'), {'name': 'gone'})
        git_init(root)
        sh(['git', 'remote', 'add', 'origin', os.path.join(self.tmp, 'missing.git')], root)
        _, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        code, wt = self.cli('run', 'worktree', start['run_ref'])
        self.assertEqual(code, 0, wt)
        self.assertEqual(wt['base'], start['base_commit'])
        self.assertEqual(wt['base_source'], 'checkout HEAD (no upstream)')

    def test_stale_lock_from_ended_run_is_reclaimed(self):
        root = os.path.join(self.tmp, 'r')
        write(os.path.join(root, 'package.json'), {'name': 'r'})
        git_init(root)
        _, first = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        journal_file = first['journal']
        with open(journal_file, encoding='utf-8') as handle:
            data = json.load(handle)
        data['status'] = 'finished'  # ended without releasing (e.g. killed after finish wrote)
        write(journal_file, data)
        code, second = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        self.assertEqual(code, 0, second)
        self.assertEqual(second['reclaimed_stale_lock']['run_id'], data['run_id'])
        os.unlink(second['journal'])  # holder journal vanished entirely
        code, third = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        self.assertEqual(code, 0, third)
        self.assertEqual(third['reclaimed_stale_lock']['journal_status'], 'missing')
        code, fourth = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        self.assertEqual(code, 1, 'an active holder is never displaced')

    def test_stale_plan_runs_are_closed(self):
        root = os.path.join(self.tmp, 'p')
        write(os.path.join(root, 'package.json'), {'name': 'p'})
        git_init(root)
        _, old = self.cli('run', 'start', '--repo', root, '--mode', 'plan')
        _, new = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        self.assertIn(old['run_ref'].split('/')[1], new['closed_stale_plan_runs'])
        _, pre = self.cli('preflight', '--repo', root)
        self.assertEqual([r['run_id'] for r in pre['active_runs']], [new['run_ref'].split('/')[1]])
        self.assertTrue(pre['active_runs'][0]['holds_lock'])

    def test_preflight_names_ignored_local_config_without_values(self):
        root = os.path.join(self.tmp, 'envapp')
        write(os.path.join(root, 'package.json'), {'name': 'envapp'})
        write(os.path.join(root, '.gitignore'), '.env\n.dev.vars\n')
        git_init(root)
        write(os.path.join(root, '.env'), 'API_TOKEN=SUPER-SECRET-VALUE\n')
        write(os.path.join(root, 'apps', 'web', '.dev.vars'), 'X=SECRET2\n')
        _, pre = self.cli('preflight', '--repo', root)
        self.assertEqual(pre['local_only_config'], ['.env', os.path.join('apps', 'web', '.dev.vars')])
        self.assertNotIn('SUPER-SECRET-VALUE', json.dumps(pre))


class StatePathTests(unittest.TestCase):
    def test_state_root_resolution(self):
        with mock.patch.dict(os.environ, {'DEPUP_STATE_DIR': '/x/y'}, clear=False):
            self.assertEqual(depup.state_root(), '/x/y')
        env = {k: v for k, v in os.environ.items() if k not in ('DEPUP_STATE_DIR',)}
        env['XDG_STATE_HOME'] = '/xdg'
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(depup.state_root(), '/xdg/dependency-upgrade')
        env.pop('XDG_STATE_HOME')
        env['LOCALAPPDATA'] = 'C:\\Users\\u\\AppData\\Local'
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(depup.os, 'name', 'nt'):
            self.assertIn('dependency-upgrade', depup.state_root())
            self.assertTrue(depup.state_root().startswith('C:\\Users\\u\\AppData\\Local'))

    def test_repo_key_is_stable_and_safe(self):
        key = depup.repo_key('/tmp/My Repo!!')
        self.assertRegex(key, r'^[A-Za-z0-9._-]+-[0-9a-f]{8}$')
        self.assertEqual(key, depup.repo_key('/tmp/My Repo!!'))


if __name__ == '__main__':
    unittest.main()
