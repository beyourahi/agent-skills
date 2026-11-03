"""Shared fixtures for the dependency-upgrade tests (standard library only)."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SKILL_DIR, rel))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


depup = load('depup', 'scripts/depup.py')
admin = load('depup_admin', 'admin/depup-admin.py')


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(content if isinstance(content, str) else json.dumps(content, indent=2) + '\n')


def sh(argv, cwd, check=True, env=None):
    full_env = dict(os.environ)
    full_env.update({'GIT_AUTHOR_NAME': 'fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                     'GIT_COMMITTER_NAME': 'fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid',
                     'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'})
    if env:
        full_env.update(env)
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, env=full_env)
    if check and proc.returncode != 0:
        raise AssertionError('%s failed (%s): %s %s' % (argv, proc.returncode, proc.stdout, proc.stderr))
    return proc


def git_init(path):
    os.makedirs(path, exist_ok=True)
    sh(['git', 'init', '-q', '-b', 'main'], path)
    sh(['git', 'add', '-A'], path)
    sh(['git', 'commit', '-q', '-m', 'fixture', '--allow-empty'], path)


class TempCase(unittest.TestCase):
    """Isolated temp dir + isolated state root for every test."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='depup-test-'))
        self.state = os.path.join(self.tmp, 'state')
        self._env = {k: os.environ.get(k) for k in ('DEPUP_STATE_DIR', 'GIT_CONFIG_GLOBAL',
                                                   'GIT_CONFIG_NOSYSTEM', 'GIT_AUTHOR_NAME',
                                                   'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME',
                                                   'GIT_COMMITTER_EMAIL')}
        os.environ.update({'DEPUP_STATE_DIR': self.state, 'GIT_CONFIG_GLOBAL': os.devnull,
                           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_AUTHOR_NAME': 'fixture',
                           'GIT_AUTHOR_EMAIL': 'fixture@example.invalid', 'GIT_COMMITTER_NAME': 'fixture',
                           'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'})

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *argv):
        """Run depup.py in-process; return (exit code, parsed JSON)."""
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = depup.main(list(argv))
        text = buffer.getvalue()
        return code, (json.loads(text) if text.strip().startswith(('{', '[')) else text)


def js_monorepo(root):
    """npm workspaces monorepo with a mix of dependency kinds."""
    write(os.path.join(root, 'package.json'), {
        'name': 'mono', 'private': True, 'packageManager': 'npm@10.9.0',
        'workspaces': ['packages/*'], 'engines': {'node': '>=20'},
        'devDependencies': {'typescript': '^5.4.0', 'eslint': '^8.57.0', '@typescript-eslint/parser': '^7.0.0'},
        'overrides': {'semver': '7.6.0'},
        'scripts': {'postinstall': 'node scripts/setup.js', 'test': 'node --test'},
    })
    write(os.path.join(root, 'package-lock.json'), {'lockfileVersion': 3, 'packages': {}})
    write(os.path.join(root, 'packages', 'app', 'package.json'), {
        'name': '@mono/app', 'dependencies': {
            'react': '^18.2.0', 'react-dom': '^18.2.0', '@mono/lib': 'workspace:*',
            'local-thing': 'file:../../vendor/local-thing', 'gitdep': 'github:user/repo#v1',
            'next-canary': '15.0.0-canary.1', 'anything': '*', 'aliased': 'npm:lodash@^4.17.0'},
        'peerDependencies': {'react': '>=18'},
    })
    write(os.path.join(root, 'packages', 'lib', 'package.json'), {
        'name': '@mono/lib', 'version': '1.0.0', 'dependencies': {'uuid': '^9.0.0'}})
    write(os.path.join(root, '.nvmrc'), '20\n')
    write(os.path.join(root, '.npmrc'), '//registry.npmjs.org/:_authToken=SECRET-TOKEN-VALUE\n')
    write(os.path.join(root, '.github', 'workflows', 'ci.yml'), 'on: push\n')
