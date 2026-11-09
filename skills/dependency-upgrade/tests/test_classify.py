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

    def test_pnpm_config_walk_global_pnpmfile_and_manager_download_guard(self):
        root = os.path.join(self.tmp, 'walk')
        child = os.path.join(root, 'packages', 'app')
        write(os.path.join(child, 'package.json'), {'name': 'app'})
        write(os.path.join(root, '.npmrc'), 'global-pnpmfile=tools/hooks.cjs\n')
        self.assertTrue(depup.native_query_hazards(child, root, 'pnpm', {}),
                        'a parent .npmrc global-pnpmfile is still loaded by pnpm')
        write(os.path.join(root, '.npmrc'), 'registry=https://registry.example.test/\n'
                                            '@scope:registry=https://scope.example.test/\n')
        self.assertEqual(depup.native_query_hazards(child, root, 'pnpm', {}), [],
                         'private registries are legitimate, not code-execution hazards')
        write(os.path.join(root, 'pnpm-workspace.yaml'), 'configDependencies:\n  my-hooks: 1.0.0\n')
        self.assertTrue(depup.native_query_hazards(child, root, 'pnpm', {}),
                        'a parent pnpm-workspace.yaml is loaded by pnpm')
        self.assertEqual(depup.NPM_SAFE_ENV['npm_config_manage_package_manager_versions'], 'false')
        self.assertEqual(depup.NPM_SAFE_ENV['pnpm_config_manage_package_manager_versions'], 'false')
        self.assertEqual(depup.NPM_SAFE_ENV['pnpm_config_ignore_pnpmfile'], 'true')
        self.assertEqual(depup.NPM_SAFE_ENV['COREPACK_ENABLE_NETWORK'], '0',
                         'Corepack must not download a package manager at all')
        self.assertNotIn('COREPACK_ENABLE_DOWNLOAD_PROMPT', depup.NPM_SAFE_ENV)

    def test_cargo_config_hazards_skip_native_query(self):
        root = os.path.join(self.tmp, 'cargo-cfg')
        project = os.path.join(root, 'crates', 'app')
        write(os.path.join(project, 'Cargo.toml'), '[package]\nname="x"\n[dependencies]\nserde="1"\n')
        flagged = [
            ('config.toml', '[registry]\ncredential-provider = "cargo:token"\n'),
            ('config.toml', '[registry]\nglobal-credential-providers = ["cargo:token"]\n'),
            ('config.toml', '[registries.private]\nindex = "https://example.test/index"\n'),
            ('config.toml', '[source.crates-io]\nreplace-with = "vendored"\n'),
            ('config.toml', '[net]\ngit-fetch-with-cli = true\n'),
            ('config', '[env]\nFOO = "bar"\n'),
        ]
        for filename, snippet in flagged:
            with self.subTest(snippet=snippet):
                write(os.path.join(root, '.cargo', 'config'), '')
                write(os.path.join(root, '.cargo', 'config.toml'), '')
                write(os.path.join(root, '.cargo', filename), snippet)
                called = []

                def fake(argv, **kw):
                    called.append(argv[0])
                    return {'rc': 0, 'stdout': '', 'stderr': '', 'error': None}

                with mock.patch.object(depup, 'run_cmd', side_effect=fake), \
                        mock.patch.object(depup.shutil, 'which', return_value='/usr/bin/cargo'):
                    out = depup.outdated(root, 10)
                self.assertNotIn('cargo', called, snippet)
                self.assertTrue(out.get('hazards'), out)
                self.assertTrue(any('cargo query skipped' in (q.get('error') or '') for q in out['queries']), out)
        write(os.path.join(root, '.cargo', 'config'), '')
        write(os.path.join(root, '.cargo', 'config.toml'),
              '[build]\nrustflags = ["-C", "target-cpu=native"]\n'
              '[target.x86_64-unknown-linux-gnu]\nlinker = "clang"\n')
        called = []

        def fake_ok(argv, **kw):
            called.append(argv[0])
            return {'rc': 0, 'stdout': '', 'stderr': '', 'error': None}

        with mock.patch.object(depup, 'run_cmd', side_effect=fake_ok), \
                mock.patch.object(depup.shutil, 'which', return_value='/usr/bin/cargo'):
            out = depup.outdated(root, 10)
        if depup._tomllib:  # 3.9/3.10 have no TOML parser and fail closed on every cargo config
            self.assertIn('cargo', called)
            self.assertFalse(out.get('hazards'), out)
            self.assertFalse([q for q in out['queries'] if q.get('error')], out)
        else:
            self.assertNotIn('cargo', called)
            self.assertTrue(out.get('hazards'), out)

    def test_cargo_config_toml_bypass_forms_are_hazards(self):
        """Every valid-TOML spelling of a dangerous cargo setting must be flagged (fail closed)."""
        path = os.path.join(self.tmp, 'cargo-bypass', '.cargo', 'config.toml')
        bypasses = [
            'source.crates-io.replace-with = "vendored"\n',           # dotted keys
            'source."crates-io".replace-with = "vendored"\n',         # quoted segment in a dotted key
            'registries.private.index = "https://example.test/index"\n',
            'registry.credential-provider = "cargo:token"\n',
            'registry.credential-alias = "cargo:token"\n',
            'registry.global-credential-providers = ["cargo:token"]\n',
            'net.git-fetch-with-cli = true\n',
            'env.FOO = "bar"\n',
            '[source]\ncrates-io = { replace-with = "vendored" }\n',  # inline table
            'env = { FOO = "bar" }\n',
            '[source.crates-io]\n"replace-with" = "vendored"\n',       # quoted key
            '["registries".private]\nindex = "https://example.test/index"\n',
            '[registries . private]\nindex = "https://example.test/index"\n',
            '[env.FOO]\nvalue = "bar"\n',                              # subtable
            '["source"."crates#io"]\nreplace-with = "vendored"\n',     # '#' inside a quoted header
            '[build]\nrustc = "/tmp/evil"\n',
            '[build]\nrustc-wrapper = "/tmp/evil"\n',
            '[build]\nrustc-workspace-wrapper = "/tmp/evil"\n',
        ]
        for snippet in bypasses:
            with self.subTest(snippet=snippet):
                write(path, snippet)
                self.assertTrue(depup._cargo_config_hazard(path), snippet)

    @unittest.skipUnless(depup._tomllib, 'benign cargo configs can only be cleared with a TOML parser (3.11+)')
    def test_cargo_config_benign_settings_stay_allowed(self):
        path = os.path.join(self.tmp, 'cargo-benign', '.cargo', 'config.toml')
        for snippet in ('[build]\nrustflags = ["-C", "target-cpu=native"]\n',
                        '[target.x86_64-unknown-linux-gnu]\nlinker = "clang"\nrustflags = ["-C", "link-arg=-fuse-ld=lld"]\n',
                        '[alias]\nxtask = "run --package xtask --"\n',
                        '[term]\nverbose = false\n',
                        '[profile.release]\nlto = true\n',
                        '[net]\nretry = 3\n',
                        '[registry]\ndefault = "crates-io"\n'):
            with self.subTest(snippet=snippet):
                write(path, snippet)
                self.assertIsNone(depup._cargo_config_hazard(path), snippet)

    def test_cargo_config_fails_closed_when_it_cannot_be_inspected(self):
        directory = os.path.join(self.tmp, 'cargo-failclosed', '.cargo')
        path = os.path.join(directory, 'config.toml')
        write(path, '[build]\nrustflags = ["-C", "target-cpu=native"]\n')
        with mock.patch.object(depup, '_tomllib', None):
            self.assertTrue(depup._cargo_config_hazard(path), 'no TOML parser: skip the native query')
        write(path, '[source.crates-io\nreplace-with = "vendored"\n')  # invalid TOML
        self.assertTrue(depup._cargo_config_hazard(path), 'unparseable config: skip the native query')
        write(path, '# pad\n' * 40000)  # 240 KB, larger than the read cap
        hazard = depup._cargo_config_hazard(path)
        self.assertTrue(hazard and 'read cap' in hazard, hazard)
        write(path, 'a = ' + '[' * 2000 + ']' * 2000 + '\n')  # the parser recurses to death
        self.assertTrue(depup._cargo_config_hazard(path), 'parser recursion: skip the native query')
        self.assertIsNone(depup._cargo_config_hazard(os.path.join(directory, 'missing.toml')),
                          'an absent config is nothing to load')

    @unittest.skipUnless(depup._tomllib, 'the parsed-key spelling can only be checked with a TOML parser (3.11+)')
    def test_cargo_config_include_and_unstable_are_hazards(self):
        """`include`/`[[include]]` load config files the walk never scans; `[unstable]` can enable them."""
        path = os.path.join(self.tmp, 'cargo-include', '.cargo', 'config.toml')
        for snippet in ('include = ["../shared/config.toml"]\n',
                        '[[include]]\npath = "../shared/config.toml"\n',
                        '[unstable]\nconfig-include = true\n',
                        'unstable.config-include = true\n'):
            with self.subTest(snippet=snippet):
                write(path, snippet)
                hazard = depup._cargo_config_hazard(path)
                self.assertTrue(hazard and ('include' in hazard or 'unstable' in hazard), hazard)

    def test_rust_toolchain_files_are_parsed_not_line_matched(self):
        """Every TOML spelling of toolchain.path is a hazard; a bare channel name is not."""
        directory = os.path.join(self.tmp, 'toolchain')
        toml = os.path.join(directory, 'rust-toolchain.toml')
        legacy = os.path.join(directory, 'rust-toolchain')
        for snippet in ('[toolchain]\npath = "./x"\n',
                        'toolchain.path = "./x"\n',
                        'toolchain = { path = "./x" }\n',
                        '["toolchain"]\n"path" = "./x"\n'):
            with self.subTest(snippet=snippet):
                write(toml, snippet)
                self.assertTrue(depup._rust_toolchain_hazard(toml), snippet)
                write(legacy, snippet)  # the extensionless file may be TOML too
                self.assertTrue(depup._rust_toolchain_hazard(legacy), snippet)
        write(toml, '# padding\n' * 3000 + '[toolchain]\npath = "./x"\n')  # past the old 20 KB read
        self.assertTrue(depup._rust_toolchain_hazard(toml), 'a padded file is read whole or reported')
        os.remove(toml)
        for snippet in ('stable\n', '1.74.0\n', 'nightly-2024-01-01\n', 'stable\r\n'):
            with self.subTest(snippet=snippet):
                write(legacy, snippet)
                self.assertIsNone(depup._rust_toolchain_hazard(legacy), snippet)
        write(legacy, '[toolchain]\nchannel = "stable"\ncomponents = ["clippy"]\n')
        if depup._tomllib:  # 3.9/3.10 fail closed on every TOML toolchain file by design
            self.assertIsNone(depup._rust_toolchain_hazard(legacy), 'TOML without toolchain.path is fine')
        else:
            self.assertTrue(depup._rust_toolchain_hazard(legacy), 'no parser: skip the native query')
        write(legacy, 'this is not a channel and not toml\n')
        self.assertTrue(depup._rust_toolchain_hazard(legacy), 'anything else fails closed')
        self.assertIsNone(depup._rust_toolchain_hazard(os.path.join(directory, 'absent')),
                          'an absent toolchain file is nothing to load')

    def test_rust_toolchain_fails_closed_without_a_toml_parser(self):
        directory = os.path.join(self.tmp, 'toolchain-noparser')
        toml = os.path.join(directory, 'rust-toolchain.toml')
        legacy = os.path.join(directory, 'rust-toolchain')
        write(toml, '[toolchain]\nchannel = "stable"\n')
        write(legacy, 'stable\n')
        with mock.patch.object(depup, '_tomllib', None):
            self.assertTrue(depup._rust_toolchain_hazard(toml), 'no TOML parser: skip the native query')
            self.assertIsNone(depup._rust_toolchain_hazard(legacy),
                              'a bare channel name needs no parser')
            write(legacy, '[toolchain]\nchannel = "stable"\n')
            self.assertTrue(depup._rust_toolchain_hazard(legacy),
                            'a TOML legacy file needs the parser too')

    def test_oversized_pnpm_config_is_a_hazard_not_partially_inspected(self):
        root = os.path.join(self.tmp, 'pnpm-padded')
        write(os.path.join(root, 'package.json'), {'name': 'padded', 'packageManager': 'pnpm@9.0.0',
                                                   'dependencies': {'a': '^1.0.0'}})
        write(os.path.join(root, 'pnpm-lock.yaml'), '')
        padded = '# harmless padding\n' * 14000 + 'pnpmfile=hooks.cjs\n'
        self.assertGreater(len(padded), 200000)
        write(os.path.join(root, '.npmrc'), padded)
        hazards = depup.native_query_hazards(root, root, 'pnpm', {})
        self.assertTrue(any('read cap' in hazard for hazard in hazards), hazards)
        called = []

        def fake(argv, **kw):
            called.append(argv[0])
            return {'rc': 0, 'stdout': '{}', 'stderr': '', 'error': None}

        with mock.patch.object(depup, 'run_cmd', side_effect=fake), \
                mock.patch.object(depup.shutil, 'which', return_value='/usr/bin/pnpm'):
            out = depup.outdated(root, 10)
        self.assertNotIn('pnpm', called)
        self.assertTrue(out.get('hazards'), out)

    def test_pnpm_config_keys_are_matched_case_quote_and_camel_case_insensitively(self):
        root = os.path.join(self.tmp, 'pnpm-keys')
        package_json = os.path.join(root, 'package.json')
        write(package_json, {'name': 'keys'})
        npmrc = os.path.join(root, '.npmrc')
        for snippet in ('"pnpmfile" = "hooks.cjs"\n',
                        'globalPnpmfile: hooks.cjs\n',
                        'GLOBAL-PNPMFILE=hooks.cjs\n',
                        'PNPMFILE = hooks.cjs\n'):
            with self.subTest(snippet=snippet):
                write(npmrc, snippet)
                self.assertTrue(depup.native_query_hazards(root, root, 'pnpm', {}), snippet)
        for snippet in ('ManagePackageManagerVersions: true\n',
                        'manage-package-manager-versions=true\n',
                        'manage_package_manager_versions: "true"\n'):
            with self.subTest(snippet=snippet):
                write(npmrc, snippet)
                self.assertTrue(depup.native_query_hazards(root, root, 'pnpm', {}), snippet)
        write(npmrc, 'manage-package-manager-versions=false\n')
        self.assertEqual(depup.native_query_hazards(root, root, 'pnpm', {}), [])
        write(package_json, '{"pnpm":{"ignorePnpmfile":true,"managePackageManagerVersions":false}}')
        self.assertEqual(depup.native_query_hazards(root, root, 'pnpm', {}), [],
                         'minified but benign pnpm settings stay allowed')
        for document in ('{"pnpm":{"pnpmfile":"./hooks.cjs"}}',
                         '{"pnpm":{"managePackageManagerVersions":true}}'):
            with self.subTest(document=document):
                write(package_json, document)
                self.assertTrue(depup.native_query_hazards(root, root, 'pnpm', {}),
                                'minified JSON on one line is still inspected')
        write(package_json, {'name': 'keys'})
        write(os.path.join(root, 'pnpm-workspace.yaml'), 'managePackageManagerVersions: true\n')
        self.assertTrue(depup.native_query_hazards(root, root, 'pnpm', {}),
                        'a repo pnpm-workspace.yaml can turn package-manager downloads back on')

    def test_corepack_env_file_on_the_walk_is_a_hazard(self):
        root = os.path.join(self.tmp, 'corepack-env')
        child = os.path.join(root, 'packages', 'app')
        write(os.path.join(child, 'package.json'), {'name': 'app'})
        write(os.path.join(root, '.corepack.env'), 'COREPACK_ENABLE_NETWORK=1\n')
        hazards = depup.native_query_hazards(child, root, 'pnpm', {})
        self.assertTrue(any('.corepack.env' in hazard for hazard in hazards), hazards)

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
