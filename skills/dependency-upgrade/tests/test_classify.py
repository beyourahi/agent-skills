import json
import os
import unittest
from unittest import mock

from tests.support import TempCase, depup, write


def c(current, latest, **extra):
    record = {'name': extra.pop('name', 'pkg'), 'current': current, 'latest': latest}
    record.update(extra)
    return depup.classify_record(record)


class ClassifyTests(unittest.TestCase):
    def test_semver_bumps(self):
        self.assertEqual((c('1.2.3', '1.2.4')['bump'], c('1.2.3', '1.2.4')['risk']), ('patch', 'low'))
        self.assertEqual((c('1.2.3', '1.3.0')['bump'], c('1.2.3', '1.3.0')['risk']), ('minor', 'medium'))
        self.assertEqual((c('1.2.3', '2.0.0')['bump'], c('1.2.3', '2.0.0')['risk']), ('major', 'high'))
        self.assertEqual(c('1.2.3', '1.2.3')['category'], 'current')

    def test_zerover_rules(self):
        r = c('0.3.1', '0.4.0')
        self.assertEqual(r['bump'], 'major')
        self.assertIn('zerover-minor-is-breaking', r['flags'])
        r = c('0.0.3', '0.0.4')
        self.assertIn('zerover-patch-is-breaking', r['flags'])
        self.assertEqual(c('0.3.1', '0.3.2')['bump'], 'patch')

    def test_prerelease_downgrade_unknown(self):
        r = c('1.0.0', '2.0.0-beta.1')
        self.assertEqual(r['risk'], 'high')
        self.assertIn('prerelease-target', r['flags'])
        self.assertEqual(c('3.0.0', '2.9.0')['bump'], 'downgrade')
        u = c(None, '1.0.0')
        self.assertEqual((u['bump'], u['risk']), ('unknown', 'high'))
        self.assertEqual(c('v1.2.3', 'v1.2.4')['bump'], 'patch')
        self.assertEqual(c('^1.2.3', '1.9.0')['bump'], 'minor')
        self.assertEqual(c('1.2.3+build.5', '1.2.3')['bump'], 'none')

    def test_deprecated_and_errors(self):
        r = c('1.0.0', '1.0.0', deprecated=True)
        self.assertEqual((r['category'], r['risk']), ('deprecated', 'medium'))
        r = c('1.0.0', None, error='E404')
        self.assertEqual(r['category'], 'blocked')

    def test_go_major_and_peer(self):
        self.assertIn('go-major-needs-module-path-change', c('v1.9.0', 'v2.0.0', ecosystem='go')['flags'])
        self.assertIn('peer-range-change-affects-consumers', c('1.0.0', '2.0.0', type='peerDependencies')['flags'])

    def test_families_and_sort(self):
        result = depup.classify([
            {'name': 'react', 'current': '18.2.0', 'latest': '19.0.0'},
            {'name': 'react-dom', 'current': '18.2.0', 'latest': '19.0.0'},
            {'name': '@types/react', 'current': '18.2.0', 'latest': '19.0.1'},
            {'name': 'lodash', 'current': '4.17.20', 'latest': '4.17.21'},
            {'name': '@sveltejs/kit', 'current': '2.0.0', 'latest': '2.5.0'},
            {'name': 'svelte', 'current': '4.2.0', 'latest': '5.0.0'},
        ])
        self.assertEqual(result['families']['react'], ['@types/react', 'react', 'react-dom'])
        self.assertEqual(result['families']['svelte'], ['@sveltejs/kit', 'svelte'])
        self.assertEqual(result['records'][-1]['name'], 'lodash')
        self.assertEqual(result['counts']['major'], 4)


class OutdatedParsingTests(TempCase):
    def test_npm_outdated_exit_one_is_parsed(self):
        payload = {'left-pad': {'current': '1.0.0', 'wanted': '1.3.0', 'latest': '1.3.0',
                                'type': 'dependencies', 'dependent': 'x'}}
        fake = {'rc': 1, 'stdout': json.dumps(payload), 'stderr': '', 'error': None}
        with mock.patch.object(depup, 'run_cmd', return_value=fake) as run:
            out = depup._npm_outdated(self.tmp, 'npm', self.tmp, 10, False)
        self.assertIsNone(out.get('error'))
        self.assertEqual(out['records'][0]['latest'], '1.3.0')
        env = run.call_args.kwargs['env_extra']
        self.assertEqual(env['npm_config_ignore_scripts'], 'true')

    def test_pnpm_list_form_and_garbage(self):
        payload = [{'packageName': 'vite', 'current': '5.0.0', 'latest': '6.0.0', 'dependencyType': 'devDependencies'}]
        with mock.patch.object(depup, 'run_cmd', return_value={'rc': 1, 'stdout': json.dumps(payload),
                                                               'stderr': '', 'error': None}):
            out = depup._npm_outdated(self.tmp, 'pnpm', self.tmp, 10, True)
        self.assertEqual(out['records'][0]['name'], 'vite')
        with mock.patch.object(depup, 'run_cmd', return_value={'rc': 1, 'stdout': 'garbage<html>',
                                                               'stderr': '', 'error': None}):
            out = depup._npm_outdated(self.tmp, 'npm', self.tmp, 10, False)
        self.assertIn('unparseable', out['error'])
        with mock.patch.object(depup, 'run_cmd', return_value={'rc': None, 'stdout': '', 'stderr': '',
                                                               'error': 'timeout after 10s'}):
            out = depup._npm_outdated(self.tmp, 'npm', self.tmp, 10, False)
        self.assertIn('timeout', out['error'])

    def test_cargo_and_go_parsers(self):
        cargo_err = ('    Updating crates.io index\n     Locking 1 package to latest compatible version\n'
                     '    Updating serde v1.0.190 -> v1.0.210\n'
                     '   Unchanged rand v0.8.5 (available: v0.9.0)\n')
        with mock.patch.object(depup, 'run_cmd', return_value={'rc': 0, 'stdout': '', 'stderr': cargo_err, 'error': None}):
            out = depup._cargo_outdated(self.tmp, self.tmp, 10)
        self.assertEqual([(r['name'], r['latest'], r['type']) for r in out['records']],
                         [('serde', '1.0.210', 'lock'), ('rand', '0.9.0', 'requires-manifest-change')])
        go_out = ('{"Path":"example.com/x","Main":true}\n'
                  '{"Path":"github.com/a/b","Version":"v1.0.0","Update":{"Version":"v1.2.0"}}\n'
                  '{"Path":"github.com/c/d","Version":"v0.1.0","Indirect":true}\n')
        with mock.patch.object(depup, 'run_cmd', return_value={'rc': 0, 'stdout': go_out, 'stderr': '', 'error': None}):
            out = depup._go_outdated(self.tmp, self.tmp, 10)
        self.assertEqual([(r['name'], r['latest']) for r in out['records']], [('github.com/a/b', 'v1.2.0')])

    def test_go_query_is_readonly(self):
        with mock.patch.object(depup, 'run_cmd', return_value={'rc': 0, 'stdout': '', 'stderr': '', 'error': None}) as run:
            depup._go_outdated(self.tmp, self.tmp, 10)
        env = run.call_args.kwargs['env_extra']
        self.assertEqual(env['GOFLAGS'], '-mod=readonly')
        self.assertEqual(env['GOTOOLCHAIN'], 'local')

    def test_injected_package_names_never_reach_npm(self):
        pkg = {'path': 'package.json', 'name': 'x', 'dependencies': [
            {'name': '--cache=./pwn', 'spec': '^1.0.0', 'type': 'dependencies', 'kind': 'semver'},
            {'name': '--cache', 'spec': '^1.0.0', 'type': 'dependencies', 'kind': 'semver'},
            {'name': '-x', 'spec': '^1.0.0', 'type': 'dependencies', 'kind': 'semver'},
            {'name': '@-scope/--x', 'spec': '^1.0.0', 'type': 'dependencies', 'kind': 'semver'},
            {'name': '../../../../etc', 'spec': '^1.0.0', 'type': 'dependencies', 'kind': 'semver'},
            {'name': 'left-pad', 'spec': '^1.0.0', 'type': 'dependencies', 'kind': 'semver'}]}
        calls = []
        def fake(argv, **kw):
            calls.append(argv)
            return {'rc': 0, 'stdout': '{"dist-tags": {"latest": "1.3.0"}}', 'stderr': '', 'error': None}
        with mock.patch.object(depup, 'run_cmd', side_effect=fake), \
                mock.patch.object(depup.shutil, 'which', return_value='/usr/bin/npm'):
            out = depup._registry_outdated(pkg, self.tmp, 'bun', self.tmp, 10)
        self.assertEqual([c[2] for c in calls], ['left-pad'])
        errors = {r['name']: r.get('error') for r in out['records']}
        self.assertIn('invalid package name', errors['--cache=./pwn'])
        self.assertIn('invalid package name', errors['../../../../etc'])
        for bad in ('--cache', '-x', '@-scope/--x'):
            self.assertIn('invalid package name', errors[bad])
        for good in ('left-pad', '@scope/pkg', 'a.b_c~d', 'x-'):
            self.assertTrue(depup.NPM_NAME.match(good), good)

    def test_repo_config_that_executes_code_skips_native_queries(self):
        root = os.path.join(self.tmp, 'haz')
        write(os.path.join(root, 'package.json'), {'name': 'h', 'packageManager': 'pnpm@9.0.0',
                                                   'dependencies': {'a': '^1.0.0'}})
        write(os.path.join(root, 'pnpm-lock.yaml'), '')
        write(os.path.join(root, '.pnpmfile.cjs'), 'require("fs").writeFileSync("PWNED","x")')
        write(os.path.join(root, 'Cargo.toml'), '[package]\nname="x"\n[dependencies]\nserde="1"\n')
        write(os.path.join(root, 'rust-toolchain.toml'), '[toolchain]\npath = "./evil"\n')
        called = []
        def fake(argv, **kw):
            called.append(argv[0])
            return {'rc': 0, 'stdout': '{}', 'stderr': '', 'error': None}
        with mock.patch.object(depup, 'run_cmd', side_effect=fake):
            out = depup.outdated(root, 10)
        self.assertNotIn('pnpm', called)
        self.assertNotIn('cargo', called)
        self.assertEqual(len(out['hazards']), 2)
        npmrc_root = os.path.join(self.tmp, 'npmrc-hook')
        write(os.path.join(npmrc_root, '.npmrc'), 'pnpmfile=tools/hooks.cjs\n')
        self.assertTrue(depup.native_query_hazards(npmrc_root, npmrc_root, 'pnpm', {}))
        cfg_root = os.path.join(self.tmp, 'cfgdeps')
        write(os.path.join(cfg_root, 'pnpm-workspace.yaml'), 'configDependencies:\n  my-hooks: 1.0.0\n')
        self.assertTrue(depup.native_query_hazards(cfg_root, cfg_root, 'pnpm', {}))
        self.assertEqual(depup.NPM_SAFE_ENV['npm_config_ignore_pnpmfile'], 'true')
        url = depup.native_query_hazards(root, root, 'npm', {'packageManager': 'npm@https://evil.example/x.tgz'})
        self.assertTrue(url and 'Corepack' in url[0])
        self.assertEqual(depup.native_query_hazards(root, root, 'npm', {'packageManager': 'npm@10.9.0+sha512.abc'}), [])

    def test_classify_cli_reads_file(self):
        path = os.path.join(self.tmp, 'records.json')
        write(path, {'records': [{'name': 'a', 'current': '1.0.0', 'latest': '2.0.0'}]})
        code, out = self.cli('classify', '--input', path)
        self.assertEqual(code, 0)
        self.assertEqual(out['records'][0]['bump'], 'major')
        write(path, 'not json')
        code, out = self.cli('classify', '--input', path)
        self.assertEqual(code, 1)
        self.assertTrue(out['refused'])


if __name__ == '__main__':
    unittest.main()
