import hashlib
import os
import unittest
from unittest import mock

from tests.support import TempCase, depup, write


def digest(path):
    with open(path, 'rb') as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class AdapterFixtureTests(TempCase):
    def test_cargo_go_and_uv_outdated_adapters_preserve_lockfiles(self):
        root = os.path.join(self.tmp, 'polyglot')
        write(os.path.join(root, 'Cargo.toml'),
              '[package]\nname = "fixture-rust"\nversion = "0.1.0"\n\n'
              '[dependencies]\nserde = "1"\n')
        write(os.path.join(root, 'Cargo.lock'),
              'version = 3\n\n[[package]]\nname = "serde"\nversion = "1.0.190"\n')
        write(os.path.join(root, 'go.mod'),
              'module example.com/fixture\n\ngo 1.22\n\nrequire github.com/a/b v1.0.0\n')
        write(os.path.join(root, 'go.sum'), 'github.com/a/b v1.0.0 h1:fixture\n')
        write(os.path.join(root, 'pyproject.toml'),
              '[project]\nname = "fixture-python"\nversion = "0.1.0"\n'
              'requires-python = ">=3.10"\ndependencies = ["sample-py>=1.0"]\n')
        write(os.path.join(root, 'uv.lock'),
              'version = 1\nrequires-python = ">=3.10"\n')
        protected_files = ('Cargo.toml', 'Cargo.lock', 'go.mod', 'go.sum', 'pyproject.toml', 'uv.lock')
        before = {name: digest(os.path.join(root, name)) for name in protected_files}
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[0] == 'cargo':
                return {'rc': 0, 'stdout': '', 'stderr':
                        '    Updating serde v1.0.190 -> v1.0.210\n', 'error': None}
            if argv[0] == 'go':
                return {'rc': 0, 'stdout':
                        '{"Path":"example.com/fixture","Main":true}\n'
                        '{"Path":"github.com/a/b","Version":"v1.0.0",'
                        '"Update":{"Version":"v1.2.0"}}\n', 'stderr': '', 'error': None}
            if argv[0] == 'uv':
                return {'rc': 0, 'stdout': '├── sample-py v1.0.0 (latest: v1.4.0)\n',
                        'stderr': '', 'error': None}
            raise AssertionError('unexpected executable: %s' % argv[0])

        with mock.patch.object(depup.shutil, 'which', side_effect=lambda name: '/fixture/' + name), \
                mock.patch.object(depup, 'run_cmd', side_effect=fake_run):
            result = depup.outdated(root, 10)

        found = {(record['ecosystem'], record['name'], record['latest'])
                 for record in result['records']}
        self.assertEqual(found, {
            ('rust', 'serde', '1.0.210'),
            ('go', 'github.com/a/b', 'v1.2.0'),
            ('python', 'sample-py', '1.4.0'),
        })
        by_executable = {argv[0]: (argv, kwargs) for argv, kwargs in calls}
        self.assertIn('--dry-run', by_executable['cargo'][0])
        self.assertEqual(by_executable['go'][1]['env_extra']['GOFLAGS'], '-mod=readonly')
        self.assertEqual(by_executable['go'][1]['env_extra']['GOTOOLCHAIN'], 'local')
        self.assertIn('--frozen', by_executable['uv'][0])
        after = {name: digest(os.path.join(root, name)) for name in protected_files}
        self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
