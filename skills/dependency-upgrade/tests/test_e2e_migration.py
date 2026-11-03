"""Offline end-to-end: a breaking major forces a real code migration.

greeter 1.x: greet(name) -> "Hello, NAME!", farewell(name)
greeter 2.x: greet({name}) (throws on a string), farewell renamed to bye(name)

The fixture's own tests must fail after the bare version bump, still fail after a
partial (wrong) migration, and pass only after the full migration.
"""
import json
import hashlib
import os
import shutil
import unittest


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()

from tests.support import TempCase, depup, git_init, sh, write

NPM = shutil.which('npm')
NODE = shutil.which('node')

GREETER_V1 = ("exports.greet = function (name) { return 'Hello, ' + name + '!'; };\n"
              "exports.farewell = function (name) { return 'Bye, ' + name + '.'; };\n")
GREETER_V2 = ("exports.greet = function (opts) {\n"
              "  if (typeof opts !== 'object' || opts === null) throw new TypeError('greet() now takes {name}');\n"
              "  return 'Hello, ' + opts.name + '!';\n};\n"
              "exports.bye = function (name) { return 'Bye, ' + name + '.'; };\n")
APP_V1 = ("const { greet, farewell } = require('greeter');\n"
          "exports.welcome = (n) => greet(n);\nexports.leave = (n) => farewell(n);\n")
APP_PARTIAL = ("const { greet, farewell } = require('greeter');\n"
               "exports.welcome = (n) => greet({ name: n });\nexports.leave = (n) => farewell(n);\n")
APP_FULL = ("const { greet, bye } = require('greeter');\n"
            "exports.welcome = (n) => greet({ name: n });\nexports.leave = (n) => bye(n);\n")
APP_TEST = ("const test = require('node:test');\nconst assert = require('node:assert');\n"
            "const app = require('./index.js');\n"
            "test('welcome', () => assert.strictEqual(app.welcome('Ada'), 'Hello, Ada!'));\n"
            "test('leave', () => assert.strictEqual(app.leave('Ada'), 'Bye, Ada.'));\n")


@unittest.skipUnless(NPM and NODE, 'npm/node not installed')
class OfflineMigrationE2E(TempCase):
    def npm(self, *args, cwd, check=True):
        env = {'npm_config_cache': os.path.join(self.tmp, 'npm-cache'), 'npm_config_ignore_scripts': 'true',
               'npm_config_audit': 'false', 'npm_config_fund': 'false', 'npm_config_update_notifier': 'false'}
        return sh([NPM] + list(args), cwd, check=check, env=env)

    def build_fixture(self):
        pkgs = os.path.join(self.tmp, 'pkgsrc')
        root = os.path.join(self.tmp, 'app')
        vendor = os.path.join(root, 'vendor')
        os.makedirs(vendor)
        for version, body in (('1.0.0', GREETER_V1), ('2.0.0', GREETER_V2)):
            src = os.path.join(pkgs, version)
            write(os.path.join(src, 'package.json'), {'name': 'greeter', 'version': version, 'main': 'index.js'})
            write(os.path.join(src, 'index.js'), body)
            self.npm('pack', '--pack-destination', vendor, cwd=src)
        write(os.path.join(root, 'package.json'), {
            'name': 'fixture-app', 'version': '1.0.0', 'private': True,
            'scripts': {'test': 'node --test test.js'},
            'dependencies': {'greeter': 'file:vendor/greeter-1.0.0.tgz'}})
        write(os.path.join(root, 'index.js'), APP_V1)
        write(os.path.join(root, 'test.js'), APP_TEST)
        write(os.path.join(root, '.gitignore'), 'node_modules/\n')
        write(os.path.join(root, 'README.md'), 'fixture\n')
        self.npm('install', '--offline', cwd=root)
        git_init(root)
        return root

    def test_breaking_upgrade_requires_full_migration(self):
        root = self.build_fixture()
        write(os.path.join(root, 'README.md'), 'fixture\nuser edit in progress\n')  # dirty user checkout
        user_lock = read(os.path.join(root, 'package-lock.json'))

        code, start = self.cli('run', 'start', '--repo', root, '--mode', 'upgrade')
        self.assertEqual(code, 0)
        ref = start['run_ref']
        self.assertIn('README.md', start['dirty_files_preserved'])
        _, wt = self.cli('run', 'worktree', ref)
        work = wt['worktree']

        self.npm('ci', '--offline', cwd=work)
        self.assertEqual(self.npm('test', cwd=work, check=False).returncode, 0, 'baseline must pass')
        self.cli('run', 'step', ref, '--id', 'baseline', '--status', 'done')

        self.cli('run', 'step', ref, '--id', 'batch-1', '--status', 'running', '--dangerous')
        self.npm('install', '--offline', 'greeter@file:vendor/greeter-2.0.0.tgz', cwd=work)
        self.assertNotEqual(self.npm('test', cwd=work, check=False).returncode, 0,
                            'tests must catch the unmigrated breaking change')
        write(os.path.join(work, 'index.js'), APP_PARTIAL)
        self.assertNotEqual(self.npm('test', cwd=work, check=False).returncode, 0,
                            'tests must catch a partial (wrong) migration')
        write(os.path.join(work, 'index.js'), APP_FULL)
        self.assertEqual(self.npm('test', cwd=work, check=False).returncode, 0, 'full migration passes')

        lock_after_upgrade = read(os.path.join(work, 'package-lock.json'))
        shutil.rmtree(os.path.join(work, 'node_modules'))
        self.npm('ci', '--offline', cwd=work)
        self.assertEqual(read(os.path.join(work, 'package-lock.json')), lock_after_upgrade,
                         'frozen install must not rewrite the lockfile')
        self.assertEqual(self.npm('test', cwd=work, check=False).returncode, 0)
        sh(['git', 'add', '-A'], work)
        sh(['git', 'commit', '-qm', 'deps: greeter 1.0.0 -> 2.0.0 (greet({name}), farewell -> bye)'], work)
        self.cli('run', 'step', ref, '--id', 'batch-1', '--status', 'done')

        manifest = json.loads(read(os.path.join(work, 'package.json')))
        self.assertEqual(manifest['dependencies']['greeter'], 'file:vendor/greeter-2.0.0.tgz')
        findings = os.path.join(self.tmp, 'findings.json')
        write(os.path.join(start['run_dir'], 'logs', 'frozen-install.log'), 'npm ci --offline: passed\n')
        write(os.path.join(start['run_dir'], 'logs', 'tests.log'), 'npm test: 2 tests passed\n')
        _, status = self.cli('run', 'status', ref)
        candidate = status['worktree']['head']
        with open(start['journal'], encoding='utf-8') as handle:
            run_id = json.load(handle)['run_id']

        def receipt(filename, command, artifact):
            artifact_path = os.path.join(start['run_dir'], artifact)
            with open(artifact_path, 'rb') as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            path = os.path.join(start['run_dir'], 'logs', filename)
            write(path, {'run_id': run_id, 'candidate_head': candidate, 'kind': 'verification',
                         'subject': command, 'result': 'pass', 'reviewer': 'test-runner',
                         'artifact': artifact, 'artifact_sha256': digest})
            return 'logs/' + filename

        install_receipt = receipt('frozen-install-evidence.json', 'npm ci --offline',
                                  'logs/frozen-install.log')
        test_receipt = receipt('tests-evidence.json', 'npm test', 'logs/tests.log')
        write(findings, {'status': 'PASS',
                         'upgrades': [{'name': 'greeter', 'from': '1.0.0', 'to': '2.0.0', 'risk': 'high'}],
                         'verification': [{'check': 'frozen install', 'command': 'npm ci --offline',
                                            'result': 'pass', 'log': 'logs/frozen-install.log',
                                            'evidence': install_receipt},
                                           {'check': 'tests', 'command': 'npm test', 'result': 'pass',
                                            'log': 'logs/tests.log', 'evidence': test_receipt}]})
        for step in ('verify', 'review'):
            self.cli('run', 'step', ref, '--id', step, '--status', 'done')
        _, rep = self.cli('report', ref, '--input', findings)
        self.assertEqual(rep['status'], 'PASS')

        self.assertEqual(read(os.path.join(root, 'package-lock.json')), user_lock)
        self.assertEqual(read(os.path.join(root, 'index.js')), APP_V1)
        self.assertIn('user edit in progress', read(os.path.join(root, 'README.md')))
        _, dry = self.cli('rollback', ref)
        self.assertTrue(any(p.get('branch') == wt['branch'] for p in dry['preserved']),
                        'owned commits are preserved on rollback')
        _, fin = self.cli('run', 'finish', ref)
        self.assertTrue(fin['lock_released'])


if __name__ == '__main__':
    unittest.main()
