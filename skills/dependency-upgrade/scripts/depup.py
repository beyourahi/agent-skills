#!/usr/bin/env python3
"""Deterministic helpers for the dependency-upgrade skill.

Standard library only (Python 3.9+). Every command prints JSON on stdout.
Read-only commands never write inside the target repository. Mutating
commands (``run worktree``, ``rollback --apply``) only touch skill-owned Git
state recorded in the run journal, which lives outside the repository;
``run worktree`` also runs a plain ``git fetch`` of the upstream default branch
(remote-tracking refs only; never the user's branches, index or files).

Commands:
  preflight  [--repo P] [--check-network]   repo/Git/tool state, dirty files
  inventory  [--repo P]                     manifests, lockfiles, package managers
  outdated   [--repo P] [--timeout S]       native read-only outdated queries
  classify   --input FILE|-                 bump/risk classification of records
  run start  --repo P --mode plan|upgrade   create a run journal (+ repo lock)
  run step   RUN --id ID --status S [...]   record a checkpoint
  run status RUN | --repo P                 journal summary and next step
  run list   --repo P                       runs for a repository
  run worktree RUN [--base-source upstream|head] owned worktree from the fetched upstream default
  run worker RUN --domain D                 owned worktree for one parallel migration domain
  run finish RUN                            release the repository mutation lock
  rollback   RUN [--apply]                  remove only clean, owned Git state
  report     RUN [--input FILE] [--copy-to DIR]  write report.json + summary.md (+ evidence copy)
  review run RUN --kind K [--subject S] [--required] [--session-*]  one routed reviewer per review task
  review record RUN --review-id ID --output F --reviewer R  bind a session-native (handoff) review
  review probe [--run RUN]                  reviewer availability, no model calls
  planning run RUN --context F [--kind plan|architecture] [--subject S] [--session-*]
                                            architecture/planning on the same routed models, read-only
  planning record RUN --plan-id ID --output F --planner P  bind a session-native (handoff) plan
  capabilities                              local toolchain availability

Exit codes: 0 ok, 1 command failed / refused, 2 usage error.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import uuid

try:  # TOML configs (cargo) are inspected with the stdlib parser where it exists
    import tomllib as _tomllib
except ImportError:  # Python 3.9/3.10: no parser, so a cargo config cannot be cleared (fail closed)
    _tomllib = None

SCHEMA_VERSION = 1
SKIP_DIRS = {
    '.git', 'node_modules', '.pnpm-store', 'vendor', 'target', 'dist', 'build',
    '.venv', 'venv', '__pycache__', '.tox', '.next', '.svelte-kit', '.turbo',
    '.cache', 'Pods', '.build', 'bin', 'obj', '.gradle', '.yarn', '.output',
    'coverage', '.wrangler', '.vercel', '.terraform', '.dependency-upgrade',
}
MAX_WALK_FILES = 200000
CONFIG_READ_CAP = 200000  # a repo config is read whole or reported as a hazard, never truncated
STEP_STATUSES = ('pending', 'running', 'done', 'failed', 'skipped', 'blocked')
REPORT_STATUSES = ('PLAN', 'PASS', 'PARTIAL', 'BLOCKED', 'FAIL')
REQUIRED_PASS_STEPS = ('baseline', 'verify', 'review')  # upgrade PASS needs these journal steps done
NPM_SAFE_ENV = {
    'npm_config_ignore_scripts': 'true',
    'npm_config_update_notifier': 'false',
    'npm_config_fund': 'false',
    'npm_config_audit': 'false',
    'NO_UPDATE_NOTIFIER': '1',
    # pnpm reads its own settings from pnpm_config_* as well as npm_config_* (pnpm.io/npmrc):
    # never load repo hooks and never download a different package-manager binary during a query.
    'npm_config_ignore_pnpmfile': 'true',
    'pnpm_config_ignore_pnpmfile': 'true',
    'npm_config_manage_package_manager_versions': 'false',
    'pnpm_config_manage_package_manager_versions': 'false',
    'COREPACK_ENABLE_NETWORK': '0',  # Corepack may not fetch a package manager at all (a prompt setting does not stop it)
    'CI': '1',
}


class Refusal(Exception):
    """A safety rule blocked the command; message explains why."""


# --------------------------------------------------------------------- helpers

def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def emit(payload) -> None:
    sys.stdout.write(json.dumps(redact_output(payload), indent=2, sort_keys=False) + '\n')


def run_cmd(argv, cwd=None, timeout=120, env_extra=None):
    """Run without a shell. Returns dict(rc, stdout, stderr, error)."""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        return {'rc': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr, 'error': None}
    except FileNotFoundError:
        return {'rc': None, 'stdout': '', 'stderr': '', 'error': 'not-found: ' + argv[0]}
    except subprocess.TimeoutExpired:
        return {'rc': None, 'stdout': '', 'stderr': '', 'error': 'timeout after %ss' % timeout}
    except OSError as exc:
        return {'rc': None, 'stdout': '', 'stderr': '', 'error': str(exc)}


def git(repo, *args, timeout=60):
    # Never block on an interactive credential prompt: fail fast and report instead.
    return run_cmd(['git', '-C', repo] + list(args), timeout=timeout, env_extra={'GIT_TERMINAL_PROMPT': '0'})


def read_text(path, limit=4_000_000):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            return handle.read(limit)
    except OSError:
        return None


def read_json(path):
    text = read_text(path)
    if text is None:
        return None, 'unreadable'
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, 'invalid json: %s' % exc


def atomic_write(path, text):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.tmp-', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def within(root, path):
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    return path == root or path.startswith(root + os.sep)


URL_USERINFO_RE = re.compile(r'(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@')
URL_SECRET_QUERY_RE = re.compile(
    r'(?i)([?&#;](?:x-amz-)?(?:access[_-]?token|auth[_-]?token|refresh[_-]?token|npm[_-]?token|token|auth|'
    r'password|passwd|x[_-]?api[_-]?key|api[_-]?key|client[_-]?secret|private[_-]?key|secret|signature|sig|credential)=)[^&#;\s]+'
)
SECRET_ASSIGNMENT_RE = re.compile(
    r'(?i)(\b(?:[a-z0-9_.-]*(?:token|secret|credential|password|passwd|api[_-]?key|private[_-]?key|'
    r'access[_-]?key|signature)|authorization|proxy-authorization|cookie|set-cookie)\s*[=:]\s*)'
    r'("[^"\r\n]*"|\'[^\'\r\n]*\'|[^\s,;]+)'
)
AUTH_SCHEME_SECRET_RE = re.compile(r'(?i)\b(Basic|Bearer)\s+[^\s,;]+')
AUTH_HEADER_SECRET_RE = re.compile(
    r'(?i)\b((?:proxy-)?authorization\s*[:=]\s*)(?:Basic|Bearer)\s+[^\s,;]+'
)
SECRET_FIELD_NAMES = {'accesstoken', 'authtoken', 'refreshtoken', 'npmtoken', 'token', 'password', 'passwd',
                      'apikey', 'xapikey', 'clientsecret', 'privatekey', 'secret', 'authorization',
                      'credential', 'credentials', 'cookie', 'setcookie', 'proxyauthorization'}


def redact_text(value):
    value = URL_USERINFO_RE.sub(r'\1[REDACTED]@', value)
    value = URL_SECRET_QUERY_RE.sub(r'\1[REDACTED]', value)
    value = AUTH_HEADER_SECRET_RE.sub(r'\1[REDACTED]', value)
    value = SECRET_ASSIGNMENT_RE.sub(r'\1[REDACTED]', value)
    return AUTH_SCHEME_SECRET_RE.sub(r'\1 [REDACTED]', value)


def redact_output(value):
    """Redact credentials from all helper JSON and report output."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_output(item) for item in value]
    if isinstance(value, dict):
        return {key: '[REDACTED]' if is_secret_field_name(key)
                else redact_output(item) for key, item in value.items()}
    return value


def is_secret_field_name(key):
    normalized = re.sub(r'[^a-z0-9]', '', str(key).lower())
    return (normalized in SECRET_FIELD_NAMES or
            re.search(r'(?:token|secret|credential|password|passwd|apikey|privatekey|accesskey|'
                      r'authorization|proxyauthorization|cookie)$', normalized) is not None)


def evidence_artifact(run_dir, reference):
    """Accept only a nonempty evidence file owned by this run directory."""
    if not isinstance(reference, str) or not reference.strip():
        return False
    path = os.path.expanduser(reference)
    if not os.path.isabs(path):
        path = os.path.join(run_dir, path)
    try:
        return within(run_dir, path) and os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def file_sha256(path):
    digest = hashlib.sha256()
    try:
        with open(path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def candidate_head(journal):
    worktree = journal.get('worktree')
    if not worktree or not os.path.isdir(worktree):
        return None
    state = git_state(worktree)
    return state.get('head') if state.get('clean') and not state.get('operation_in_progress') else None


def bound_evidence(run_dir, reference, run_id, candidate, kind, subject, result=None,
                   expected_artifact=None):
    """Validate a run-local JSON receipt bound to this exact candidate and source artifact."""
    if not evidence_artifact(run_dir, reference):
        return False
    path = reference if os.path.isabs(reference) else os.path.join(run_dir, reference)
    data, error = read_json(path)
    if error or not isinstance(data, dict):
        return False
    artifact = data.get('artifact')
    if not evidence_artifact(run_dir, artifact):
        return False
    artifact_path = artifact if os.path.isabs(artifact) else os.path.join(run_dir, artifact)
    if expected_artifact is not None:
        expected_path = expected_artifact if os.path.isabs(expected_artifact) else os.path.join(run_dir, expected_artifact)
        if os.path.realpath(artifact_path) != os.path.realpath(expected_path):
            return False
    return (data.get('run_id') == run_id and data.get('candidate_head') == candidate and
            data.get('kind') == kind and data.get('subject') == subject and
            (result is None or data.get('result') == result) and
            isinstance(data.get('reviewer'), str) and bool(data['reviewer'].strip()) and
            data.get('artifact_sha256') == file_sha256(artifact_path))


def safe_repeat_evidence(run_dir, journal, step_id, references):
    """Require a structured, candidate-bound safety analysis before replaying a dangerous step."""
    worktree = journal.get('worktree')
    if not worktree or not os.path.isdir(worktree):
        return False
    state = git_state(worktree)
    if not state.get('clean') or state.get('operation_in_progress') or not state.get('head'):
        return False
    for reference in references:
        if not evidence_artifact(run_dir, reference):
            continue
        path = reference if os.path.isabs(reference) else os.path.join(run_dir, reference)
        data, error = read_json(path)
        if error or not isinstance(data, dict):
            continue
        preconditions = data.get('preconditions')
        if (data.get('step_id') == step_id and data.get('decision') == 'safe-to-repeat' and
                data.get('candidate_head') == state['head'] and
                isinstance(data.get('reason'), str) and data['reason'].strip() and
                isinstance(preconditions, list) and any(isinstance(item, str) and item.strip()
                                                        for item in preconditions) and
                isinstance(data.get('recovery'), str) and data['recovery'].strip()):
            return True
    return False


def tool_version(name, args=('--version',)):
    exe = shutil.which(name)
    if not exe:
        return None
    result = run_cmd([exe] + list(args), timeout=20)
    text = (result['stdout'] or result['stderr'] or '').strip().splitlines()
    return {'path': exe, 'version': text[0][:120] if text else None}


# ------------------------------------------------------------------ state dirs

def state_root() -> str:
    if os.environ.get('DEPUP_STATE_DIR'):
        return os.path.abspath(os.path.expanduser(os.environ['DEPUP_STATE_DIR']))
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~\\AppData\\Local')
        return os.path.join(base, 'dependency-upgrade', 'state')
    base = os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state')
    return os.path.join(base, 'dependency-upgrade')


def repo_key(root: str) -> str:
    real = os.path.realpath(root)
    slug = re.sub(r'[^A-Za-z0-9._-]+', '-', os.path.basename(real)).strip('-') or 'repo'
    return '%s-%s' % (slug[:40], hashlib.sha256(real.encode()).hexdigest()[:8])


def repo_state_dir(root: str) -> str:
    return os.path.join(state_root(), repo_key(root))


SAFE_RUN_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$')


def run_dir_for(run_ref: str) -> str:
    """Resolve RUN as a journal path, a run directory, or ``<repo-key>/<run-id>``."""
    candidates = []
    expanded = os.path.expanduser(run_ref)
    if os.path.isfile(expanded) and os.path.basename(expanded) == 'journal.json':
        candidates.append(os.path.dirname(os.path.abspath(expanded)))
    elif os.path.isdir(expanded):
        candidates.append(os.path.abspath(expanded))
    else:
        parts = run_ref.split('/')
        if len(parts) == 2 and all(SAFE_RUN_ID.match(p) for p in parts):
            candidates.append(os.path.join(state_root(), parts[0], parts[1]))
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, 'journal.json')):
            if not within(state_root(), candidate):
                raise Refusal('run directory is outside the state root: %s' % candidate)
            return candidate
    raise Refusal('unknown run: %s (use the run_dir printed by "run start")' % run_ref)


# ------------------------------------------------------------------- repo root

def resolve_repo(path):
    path = os.path.abspath(os.path.expanduser(path or os.getcwd()))
    if not os.path.isdir(path):
        raise Refusal('not a directory: %s' % path)
    result = git(path, 'rev-parse', '--show-toplevel')
    if result['rc'] == 0:
        return os.path.realpath(result['stdout'].strip()), True
    return os.path.realpath(path), False


def git_state(root):
    info = {}
    def val(*args):
        result = git(root, *args)
        return result['stdout'].strip() if result['rc'] == 0 else None
    info['head'] = val('rev-parse', 'HEAD')
    info['branch'] = val('symbolic-ref', '--short', '-q', 'HEAD')
    info['detached'] = info['branch'] is None
    info['upstream'] = val('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
    git_dir = val('rev-parse', '--absolute-git-dir')
    info['git_dir'] = git_dir
    info['is_linked_worktree'] = bool(git_dir and os.sep + 'worktrees' + os.sep in git_dir)
    status = git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    entries = []
    if status['rc'] == 0:
        chunks = status['stdout'].split('\0')
        i = 0
        while i < len(chunks):
            chunk = chunks[i]
            if not chunk:
                i += 1
                continue
            code, name = chunk[:2], chunk[3:]
            entry = {'code': code, 'path': name}
            if code[0] in 'RC':
                i += 1
                entry['from'] = chunks[i] if i < len(chunks) else None
            entries.append(entry)
            i += 1
    info['dirty'] = [e for e in entries if e['code'] != '??']
    info['untracked'] = [e['path'] for e in entries if e['code'] == '??']
    info['clean'] = not entries
    stash = git(root, 'stash', 'list')
    info['stash_count'] = len([l for l in stash['stdout'].splitlines() if l]) if stash['rc'] == 0 else None
    ops = []
    if git_dir:
        for marker, label in (('MERGE_HEAD', 'merge'), ('rebase-merge', 'rebase'),
                              ('rebase-apply', 'rebase/am'), ('CHERRY_PICK_HEAD', 'cherry-pick'),
                              ('REVERT_HEAD', 'revert'), ('BISECT_LOG', 'bisect')):
            if os.path.exists(os.path.join(git_dir, marker)):
                ops.append(label)
    info['operation_in_progress'] = ops
    wt = git(root, 'worktree', 'list', '--porcelain')
    worktrees = []
    if wt['rc'] == 0:
        current = {}
        for line in wt['stdout'].splitlines():
            if not line:
                if current:
                    worktrees.append(current)
                current = {}
                continue
            key, _, value = line.partition(' ')
            current[key] = value or True
        if current:
            worktrees.append(current)
    info['worktrees'] = worktrees
    info['submodules'] = os.path.exists(os.path.join(root, '.gitmodules'))
    info['shallow'] = val('rev-parse', '--is-shallow-repository') == 'true'
    return info


# ------------------------------------------------------------------ inventory

JS_LOCKFILES = {
    'package-lock.json': 'npm', 'npm-shrinkwrap.json': 'npm', 'pnpm-lock.yaml': 'pnpm',
    'yarn.lock': 'yarn', 'bun.lock': 'bun', 'bun.lockb': 'bun',
}
DEP_FIELDS = ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies')
LIFECYCLE = ('preinstall', 'install', 'postinstall', 'prepare', 'prepublish')

ECOSYSTEM_FILES = {
    # filename -> (ecosystem, role)
    'Cargo.toml': ('rust', 'manifest'), 'Cargo.lock': ('rust', 'lockfile'),
    'pyproject.toml': ('python', 'manifest'), 'uv.lock': ('python', 'lockfile'),
    'poetry.lock': ('python', 'lockfile'), 'Pipfile': ('python', 'manifest'),
    'Pipfile.lock': ('python', 'lockfile'), 'setup.py': ('python', 'manifest'),
    'setup.cfg': ('python', 'manifest'), 'pdm.lock': ('python', 'lockfile'),
    'go.mod': ('go', 'manifest'), 'go.sum': ('go', 'lockfile'), 'go.work': ('go', 'workspace'),
    'pom.xml': ('java-maven', 'manifest'), 'build.gradle': ('java-gradle', 'manifest'),
    'build.gradle.kts': ('java-gradle', 'manifest'), 'settings.gradle': ('java-gradle', 'workspace'),
    'settings.gradle.kts': ('java-gradle', 'workspace'), 'libs.versions.toml': ('java-gradle', 'catalog'),
    'gradle.lockfile': ('java-gradle', 'lockfile'),
    'Gemfile': ('ruby', 'manifest'), 'Gemfile.lock': ('ruby', 'lockfile'),
    'composer.json': ('php', 'manifest'), 'composer.lock': ('php', 'lockfile'),
    'Directory.Packages.props': ('dotnet', 'manifest'), 'packages.lock.json': ('dotnet', 'lockfile'),
    'global.json': ('dotnet', 'runtime-pin'), 'Package.swift': ('swift', 'manifest'),
    'Package.resolved': ('swift', 'lockfile'), 'Podfile': ('cocoapods', 'manifest'),
    'Podfile.lock': ('cocoapods', 'lockfile'), 'pubspec.yaml': ('dart', 'manifest'),
    'pubspec.lock': ('dart', 'lockfile'), 'mix.exs': ('elixir', 'manifest'),
    'mix.lock': ('elixir', 'lockfile'), 'deno.json': ('deno', 'manifest'),
    'deno.jsonc': ('deno', 'manifest'), 'deno.lock': ('deno', 'lockfile'),
}
RUNTIME_PINS = ('.nvmrc', '.node-version', '.tool-versions', '.python-version', 'rust-toolchain',
                'rust-toolchain.toml', 'mise.toml', '.mise.toml', '.ruby-version', '.java-version',
                '.go-version', '.bun-version', '.npmrc', '.yarnrc.yml', '.yarnrc', 'bunfig.toml')
# Only plain version-pin files are echoed; config files (.npmrc, bunfig.toml, mise.toml...) may hold tokens.
ECHO_SAFE_PINS = ('.nvmrc', '.node-version', '.tool-versions', '.python-version', 'rust-toolchain',
                  'rust-toolchain.toml', '.ruby-version', '.java-version', '.go-version', '.bun-version')
NPM_NAME = re.compile(r'^(@[a-z0-9~][a-z0-9._~-]*/)?[a-z0-9~][a-z0-9._~-]*$')  # never starts with '-'
SUPPORT = {
    'javascript': 'full', 'rust': 'adapter', 'go': 'adapter', 'python': 'adapter',
    'java-maven': 'advisory', 'java-gradle': 'advisory', 'ruby': 'advisory', 'php': 'advisory',
    'dotnet': 'advisory', 'swift': 'advisory', 'cocoapods': 'advisory', 'dart': 'advisory',
    'elixir': 'advisory', 'deno': 'advisory',
}


def spec_kind(spec):
    if not isinstance(spec, str):
        return 'invalid'
    s = spec.strip()
    if s.startswith('workspace:'):
        return 'workspace'
    if s.startswith('catalog:'):
        return 'catalog'
    if s.startswith(('file:', 'link:', 'portal:')) or s.startswith(('./', '../', '/', '~/')):
        return 'path'
    if s.startswith('npm:'):
        return 'alias'
    if s.startswith(('git+', 'git:', 'github:', 'gitlab:', 'bitbucket:')) or re.match(r'^[\w.-]+/[\w.-]+(#.*)?$', s):
        return 'git'
    if s.startswith(('http://', 'https://')):
        return 'tarball-url'
    if s in ('*', 'latest', '') or s.startswith(('>=', '>')) and '<' not in s:
        return 'unbounded'
    if re.search(r'-(alpha|beta|rc|next|canary|pre|dev|nightly|insiders)', s, re.I):
        return 'prerelease'
    return 'semver'


def parse_package_json(path, root):
    data, error = read_json(path)
    rel = os.path.relpath(path, root)
    record = {'path': rel, 'error': error}
    if data is None or not isinstance(data, dict):
        record['error'] = error or 'not an object'
        return record
    record['name'] = data.get('name') if isinstance(data.get('name'), str) else None
    record['private'] = bool(data.get('private'))
    record['packageManager'] = data.get('packageManager') if isinstance(data.get('packageManager'), str) else None
    record['engines'] = data.get('engines') if isinstance(data.get('engines'), dict) else {}
    record['type'] = data.get('type')
    workspaces = data.get('workspaces')
    if isinstance(workspaces, dict):
        workspaces = workspaces.get('packages')
    record['workspaces'] = workspaces if isinstance(workspaces, list) else []
    deps = []
    for field in DEP_FIELDS:
        block = data.get(field)
        if isinstance(block, dict):
            for name, spec in sorted(block.items()):
                deps.append({'name': name, 'spec': spec if isinstance(spec, str) else repr(spec),
                             'type': field, 'kind': spec_kind(spec)})
    bundled = data.get('bundledDependencies', data.get('bundleDependencies'))
    record['bundled'] = bundled if isinstance(bundled, list) else []
    record['dependencies'] = deps
    pnpm = data.get('pnpm') if isinstance(data.get('pnpm'), dict) else {}
    record['overrides'] = {
        k: v for k, v in (('overrides', data.get('overrides')), ('resolutions', data.get('resolutions')),
                          ('pnpm.overrides', pnpm.get('overrides')),
                          ('pnpm.peerDependencyRules', pnpm.get('peerDependencyRules')))
        if v
    }
    patched = data.get('patchedDependencies') or pnpm.get('patchedDependencies')
    record['patched'] = sorted(patched) if isinstance(patched, dict) else []
    scripts = data.get('scripts') if isinstance(data.get('scripts'), dict) else {}
    record['scripts'] = sorted(scripts)
    record['lifecycle_scripts'] = [s for s in LIFECYCLE if s in scripts]
    record['volta'] = data.get('volta') if isinstance(data.get('volta'), dict) else None
    return record


def detect_js_manager(directory, pkg, root):
    """Return (manager, evidence, conflicts) for a JS project directory."""
    evidence, conflicts = [], []
    declared = None
    if pkg and pkg.get('packageManager'):
        declared = pkg['packageManager'].split('@', 1)[0]
        evidence.append('packageManager=' + pkg['packageManager'])
    found = []
    probe = directory
    while True:
        for lock, manager in JS_LOCKFILES.items():
            if os.path.isfile(os.path.join(probe, lock)):
                found.append((manager, os.path.relpath(os.path.join(probe, lock), root)))
        if found or os.path.realpath(probe) == os.path.realpath(root):
            break
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    managers = sorted({m for m, _ in found})
    evidence.extend('lockfile=' + p for _, p in found)
    if len(managers) > 1:
        conflicts.append('mixed lockfiles: ' + ', '.join(p for _, p in found))
    if declared and managers and declared not in managers:
        conflicts.append('packageManager says %s but lockfile is %s' % (declared, ','.join(managers)))
    manager = declared or (managers[0] if len(managers) == 1 else None)
    if manager is None and not managers:
        evidence.append('no lockfile: manager undetermined (infer from repo docs/CI/scripts; otherwise hold '
                        'mutation for this project and continue the rest)')
    return manager, evidence, conflicts


def parse_simple_toml_deps(text, sections):
    """Tiny TOML fallback: dependency names inside the given [section] headers."""
    deps, current = [], None
    for line in text.splitlines():
        stripped = line.strip()
        header = re.match(r'^\[([^\]]+)\]$', stripped)
        if header:
            current = header.group(1).strip()
            continue
        if current in sections:
            kv = re.match(r'^([A-Za-z0-9_.\-"]+)\s*=\s*(.+)$', stripped)
            if kv:
                deps.append({'name': kv.group(1).strip('"'), 'spec': kv.group(2)[:120], 'type': current})
    return deps


def load_toml(text):
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        return None
    try:
        return tomllib.loads(text)
    except Exception:  # noqa: BLE001 - malformed user TOML is data, not a crash
        return None


def parse_cargo(path, root):
    text = read_text(path) or ''
    data = load_toml(text)
    record = {'path': os.path.relpath(path, root), 'ecosystem': 'rust'}
    if data is not None:
        deps = []
        for section in ('dependencies', 'dev-dependencies', 'build-dependencies'):
            for name, spec in sorted((data.get(section) or {}).items()):
                deps.append({'name': name, 'spec': spec if isinstance(spec, str) else json.dumps(spec)[:120],
                             'type': section})
        ws = data.get('workspace') or {}
        for name, spec in sorted((ws.get('dependencies') or {}).items()):
            deps.append({'name': name, 'spec': spec if isinstance(spec, str) else json.dumps(spec)[:120],
                         'type': 'workspace.dependencies'})
        record['workspace_members'] = ws.get('members') or []
        record['rust_version'] = (data.get('package') or {}).get('rust-version')
        record['patch'] = sorted((data.get('patch') or {}).keys())
    else:
        deps = parse_simple_toml_deps(text, {'dependencies', 'dev-dependencies', 'build-dependencies',
                                             'workspace.dependencies'})
        record['parser'] = 'fallback'
    record['dependencies'] = deps
    return record


def parse_pyproject(path, root):
    text = read_text(path) or ''
    data = load_toml(text)
    record = {'path': os.path.relpath(path, root), 'ecosystem': 'python'}
    deps = []
    if data is not None:
        project = data.get('project') or {}
        for spec in project.get('dependencies') or []:
            deps.append({'name': re.split(r'[\s<>=!~;\[(]', spec, maxsplit=1)[0], 'spec': spec, 'type': 'project'})
        for group, items in sorted((project.get('optional-dependencies') or {}).items()):
            for spec in items:
                deps.append({'name': re.split(r'[\s<>=!~;\[(]', spec, maxsplit=1)[0], 'spec': spec, 'type': 'optional:' + group})
        for group, items in sorted((data.get('dependency-groups') or {}).items()):
            for spec in items:
                if isinstance(spec, str):
                    deps.append({'name': re.split(r'[\s<>=!~;\[(]', spec, maxsplit=1)[0], 'spec': spec, 'type': 'group:' + group})
        poetry = (data.get('tool') or {}).get('poetry') or {}
        for name, spec in sorted((poetry.get('dependencies') or {}).items()):
            deps.append({'name': name, 'spec': spec if isinstance(spec, str) else json.dumps(spec)[:120], 'type': 'poetry'})
        record['requires_python'] = project.get('requires-python')
        tool = data.get('tool') or {}
        record['tools'] = sorted(k for k in ('uv', 'poetry', 'pdm', 'hatch') if k in tool)
    else:
        record['parser'] = 'fallback'
        deps = parse_simple_toml_deps(text, {'tool.poetry.dependencies'})
        section = None
        for match in re.finditer(r'^\[([^\]]+)\]\s*$|^([A-Za-z0-9_-]+)\s*=\s*\[(.*?)\]\s*(?:#.*)?$', text, re.M | re.S):
            if match.group(1):
                section = match.group(1).strip()
                continue
            key = match.group(2)
            if (section, key) == ('project', 'dependencies') or section in ('project.optional-dependencies',
                                                                            'dependency-groups'):
                kind = 'project' if key == 'dependencies' and section == 'project' else '%s:%s' % (section, key)
                for spec in re.findall(r'["\']([^"\']+)["\']', match.group(3)):
                    deps.append({'name': re.split(r'[\s<>=!~;\[(]', spec, maxsplit=1)[0], 'spec': spec, 'type': kind})
        rp = re.search(r'^requires-python\s*=\s*["\']([^"\']+)', text, re.M)
        record['requires_python'] = rp.group(1) if rp else None
    record['dependencies'] = deps
    return record


def parse_requirements(path, root):
    deps = []
    for line in (read_text(path) or '').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line or line.startswith('-'):
            continue
        deps.append({'name': re.split(r'[\s<>=!~;\[(@]', line, maxsplit=1)[0], 'spec': line, 'type': 'requirements'})
    return {'path': os.path.relpath(path, root), 'ecosystem': 'python', 'dependencies': deps}


def parse_go_mod(path, root):
    text = read_text(path) or ''
    deps, replaces = [], []
    block = None
    for raw in text.splitlines():
        line = raw.split('//', 1)[0].strip()
        indirect = '// indirect' in raw
        if not line:
            continue
        if line.endswith('('):
            block = line.split()[0]
            continue
        if line == ')':
            block = None
            continue
        if line.startswith('require ') or block == 'require':
            parts = line.replace('require ', '', 1).split()
            if len(parts) >= 2:
                deps.append({'name': parts[0], 'spec': parts[1], 'type': 'indirect' if indirect else 'direct'})
        elif line.startswith('replace ') or block == 'replace':
            replaces.append(line.replace('replace ', '', 1))
    go_line = re.search(r'^go\s+(\S+)', text, re.M)
    toolchain = re.search(r'^toolchain\s+(\S+)', text, re.M)
    return {'path': os.path.relpath(path, root), 'ecosystem': 'go', 'dependencies': deps,
            'replace': replaces, 'go': go_line.group(1) if go_line else None,
            'toolchain': toolchain.group(1) if toolchain else None}


def inventory(root):
    result = {
        'root': root, 'js_projects': [], 'other_manifests': [], 'lockfiles': [], 'runtime_pins': [],
        'ci': [], 'symlinks': [], 'warnings': [], 'unsupported': [], 'ecosystems': {},
        'pnpm_workspaces': [], 'vendored': [],
    }
    count = 0
    package_jsons = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        kept = []
        for name in dirnames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                target = os.path.realpath(full)
                result['symlinks'].append({'path': os.path.relpath(full, root), 'kind': 'dir',
                                           'escapes_root': not within(root, target)})
                continue
            if name in SKIP_DIRS:
                if name in ('vendor', 'third_party') and rel_dir == '.':
                    result['vendored'].append(os.path.relpath(full, root))
                continue
            if name.startswith('.') and name not in ('.github', '.gitlab', '.circleci', '.buildkite'):
                continue
            kept.append(name)
        dirnames[:] = sorted(kept)
        for name in sorted(filenames):
            count += 1
            if count > MAX_WALK_FILES:
                result['warnings'].append('walk stopped after %d files' % MAX_WALK_FILES)
                dirnames[:] = []
                break
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if os.path.islink(full):
                target = os.path.realpath(full)
                escapes = not within(root, target)
                result['symlinks'].append({'path': rel, 'kind': 'file', 'escapes_root': escapes})
                if escapes:
                    result['warnings'].append('symlink escapes repository, not read: ' + rel)
                    continue
            if name == 'package.json':
                package_jsons.append(full)
            elif name in JS_LOCKFILES:
                result['lockfiles'].append({'path': rel, 'manager': JS_LOCKFILES[name]})
            elif name == 'pnpm-workspace.yaml':
                text = read_text(full) or ''
                result['pnpm_workspaces'].append({
                    'path': rel,
                    'packages': re.findall(r'^\s*-\s*["\']?([^"\'\n]+)["\']?\s*$', text.split('catalog', 1)[0], re.M),
                    'has_catalog': bool(re.search(r'^catalogs?:', text, re.M)),
                })
            elif name in ECOSYSTEM_FILES or name.endswith(('.csproj', '.fsproj', '.vbproj')) or \
                    (name.startswith('requirements') and name.endswith('.txt')):
                eco, role = ECOSYSTEM_FILES.get(name, ('dotnet', 'manifest') if name.endswith('proj') else ('python', 'manifest'))
                result['ecosystems'].setdefault(eco, {'support': SUPPORT.get(eco, 'advisory'), 'files': []})
                result['ecosystems'][eco]['files'].append({'path': rel, 'role': role})
                if role == 'lockfile':
                    result['lockfiles'].append({'path': rel, 'manager': eco})
                parsed = None
                if name == 'Cargo.toml':
                    parsed = parse_cargo(full, root)
                elif name == 'pyproject.toml':
                    parsed = parse_pyproject(full, root)
                elif name.startswith('requirements') and name.endswith('.txt'):
                    parsed = parse_requirements(full, root)
                elif name == 'go.mod':
                    parsed = parse_go_mod(full, root)
                if parsed:
                    result['other_manifests'].append(parsed)
            if name in RUNTIME_PINS:
                result['runtime_pins'].append({'path': rel, 'content': (read_text(full, 2000) or '').strip()[:300]
                                               if name in ECHO_SAFE_PINS else '(not echoed: config may hold tokens)'})
            if rel.startswith(('.github' + os.sep + 'workflows', '.gitlab-ci', '.circleci', '.buildkite')) or \
                    name in ('.gitlab-ci.yml', 'azure-pipelines.yml', 'Jenkinsfile', '.travis.yml', 'bitbucket-pipelines.yml'):
                result['ci'].append(rel)
    for full in package_jsons:
        pkg = parse_package_json(full, root)
        directory = os.path.dirname(full)
        manager, evidence, conflicts = detect_js_manager(directory, pkg, root)
        pkg['manager'] = manager
        pkg['manager_evidence'] = evidence
        pkg['conflicts'] = conflicts
        result['warnings'].extend('%s: %s' % (pkg['path'], c) for c in conflicts)
        if pkg.get('lifecycle_scripts'):
            pkg['note'] = 'own lifecycle scripts run on install: ' + ', '.join(pkg['lifecycle_scripts'])
        result['js_projects'].append(pkg)
    if result['js_projects']:
        result['ecosystems']['javascript'] = {'support': SUPPORT['javascript'],
                                              'files': [{'path': p['path'], 'role': 'manifest'} for p in result['js_projects']]}
    for eco, info in result['ecosystems'].items():
        if info['support'] == 'advisory':
            result['unsupported'].append(eco)
    result['summary'] = {
        'js_projects': len(result['js_projects']),
        'js_managers': sorted({p.get('manager') or 'unknown' for p in result['js_projects']}),
        'ecosystems': {k: v['support'] for k, v in sorted(result['ecosystems'].items())},
        'lockfiles': len(result['lockfiles']),
        'warnings': len(result['warnings']),
    }
    return result


# -------------------------------------------------------------------- outdated

def _npm_outdated(project_dir, manager, root, timeout, workspace_root):
    if manager == 'npm':
        argv = ['npm', 'outdated', '--json', '--long']
        if workspace_root:
            argv += ['--workspaces', '--include-workspace-root']
    elif manager == 'pnpm':
        argv = ['pnpm', 'outdated', '--format', 'json']
        if workspace_root:
            argv.append('--recursive')
    else:
        return None
    res = run_cmd(argv, cwd=project_dir, timeout=timeout, env_extra=NPM_SAFE_ENV)
    out = {'command': ' '.join(argv), 'cwd': os.path.relpath(project_dir, root), 'records': []}
    if res['error']:
        out['error'] = res['error']
        return out
    # npm/pnpm exit 1 when something is outdated; that is not a failure.
    text = res['stdout'].strip()
    if not text:
        out['error'] = None if res['rc'] in (0, 1) else (res['stderr'].strip()[:500] or 'rc=%s' % res['rc'])
        return out
    try:
        data = json.loads(text)
    except ValueError:
        out['error'] = 'unparseable output: ' + text[:300]
        return out
    if isinstance(data, list):  # pnpm recursive may return a list
        data = {(d.get('packageName') or d.get('name')): d for d in data if isinstance(d, dict)}
    for name, item in sorted((data or {}).items()):
        entries = item if isinstance(item, list) else [item]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            out['records'].append({
                'ecosystem': 'javascript', 'manager': manager, 'name': name,
                'current': entry.get('current'), 'wanted': entry.get('wanted'), 'latest': entry.get('latest'),
                'type': entry.get('type') or entry.get('dependencyType'),
                'workspace': entry.get('dependent') or entry.get('location') or (entry.get('dependentPackages') or [{}])[0].get('name'),
                'deprecated': bool(entry.get('isDeprecated')),
            })
    return out


def _registry_view(name, timeout, cwd):
    if not NPM_NAME.match(name):
        return {'error': 'invalid package name'}
    res = run_cmd(['npm', 'view', name, 'dist-tags', 'deprecated', '--json'], cwd=cwd, timeout=timeout,
                  env_extra=NPM_SAFE_ENV)
    if res['rc'] != 0:
        return {'error': (res['error'] or res['stderr'].strip()[:200] or 'rc=%s' % res['rc'])}
    try:
        data = json.loads(res['stdout'] or '{}')
    except ValueError:
        return {'error': 'unparseable npm view output'}
    tags = data.get('dist-tags') if isinstance(data, dict) and 'dist-tags' in data else data
    deprecated = data.get('deprecated') if isinstance(data, dict) else None
    return {'latest': (tags or {}).get('latest') if isinstance(tags, dict) else None,
            'deprecated': bool(deprecated)}


def _registry_outdated(pkg, project_dir, manager, root, timeout):
    """Fallback for bun/yarn/unknown: read-only registry lookup of direct deps."""
    out = {'command': 'npm view <name> dist-tags deprecated --json (registry fallback)',
           'cwd': os.path.relpath(project_dir, root), 'records': []}
    if not shutil.which('npm'):
        out['error'] = 'npm not found; registry fallback unavailable'
        return out
    deps = []
    for dep in pkg.get('dependencies', []):
        if dep['kind'] not in ('semver', 'prerelease', 'unbounded'):
            continue
        if not NPM_NAME.match(dep['name']) or len(dep['name']) > 214:
            out['records'].append({'ecosystem': 'javascript', 'manager': manager, 'name': dep['name'],
                                   'declared': dep['spec'], 'current': None, 'latest': None, 'type': dep['type'],
                                   'error': 'invalid package name: not queried (possible injection)'})
            continue
        deps.append(dep)
    def lookup(dep):
        installed = None
        meta, _ = read_json(os.path.join(project_dir, 'node_modules', dep['name'], 'package.json'))
        if isinstance(meta, dict):
            installed = meta.get('version')
        view = _registry_view(dep['name'], timeout, project_dir)
        return dep, installed, view
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for dep, installed, view in pool.map(lookup, deps):
            out['records'].append({
                'ecosystem': 'javascript', 'manager': manager, 'name': dep['name'],
                'declared': dep['spec'], 'current': installed, 'wanted': None,
                'latest': view.get('latest'), 'type': dep['type'],
                'workspace': pkg.get('name') or pkg['path'], 'deprecated': view.get('deprecated', False),
                'error': view.get('error'),
            })
    return out


def _cargo_outdated(directory, root, timeout):
    argv = ['cargo', 'update', '--dry-run', '--verbose']
    res = run_cmd(argv, cwd=directory, timeout=timeout)
    out = {'command': ' '.join(argv), 'cwd': os.path.relpath(directory, root), 'records': [],
           'note': 'lists semver-compatible lockfile updates plus "Unchanged ... (available: X)" incompatible ones'}
    if res['error'] or res['rc'] not in (0,):
        out['error'] = res['error'] or res['stderr'].strip()[-500:]
        return out
    for line in res['stderr'].splitlines():
        m = re.match(r'\s*(Updating|Downgrading)\s+(\S+)\s+v(\S+)\s+->\s+v(\S+)', line)
        if m:
            out['records'].append({'ecosystem': 'rust', 'name': m.group(2), 'current': m.group(3),
                                   'wanted': m.group(4), 'latest': m.group(4), 'type': 'lock'})
            continue
        m = re.match(r'\s*Unchanged\s+(\S+)\s+v(\S+)\s+\(available:\s+v([^)]+)\)', line)
        if m:
            out['records'].append({'ecosystem': 'rust', 'name': m.group(1), 'current': m.group(2),
                                   'wanted': m.group(2), 'latest': m.group(3), 'type': 'requires-manifest-change'})
    return out


def _go_outdated(directory, root, timeout):
    argv = ['go', 'list', '-m', '-u', '-json', 'all']
    res = run_cmd(argv, cwd=directory, timeout=timeout,
                  env_extra={'GOFLAGS': '-mod=readonly', 'GOTOOLCHAIN': 'local', 'GOWORK': 'off'})
    out = {'command': 'GOFLAGS=-mod=readonly GOTOOLCHAIN=local ' + ' '.join(argv),
           'cwd': os.path.relpath(directory, root), 'records': [],
           'note': 'read-only module mode: may fill the Go module cache (outside the repo), never edits go.mod/go.sum'}
    if res['error'] or res['rc'] != 0:
        out['error'] = res['error'] or res['stderr'].strip()[-500:]
        return out
    decoder = json.JSONDecoder()
    text, idx = res['stdout'], 0
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        obj, idx = decoder.raw_decode(text, idx)
        if obj.get('Main') or not obj.get('Update'):
            continue
        out['records'].append({'ecosystem': 'go', 'name': obj['Path'], 'current': obj.get('Version'),
                               'wanted': obj['Update'].get('Version'), 'latest': obj['Update'].get('Version'),
                               'type': 'indirect' if obj.get('Indirect') else 'direct',
                               'deprecated': bool(obj.get('Deprecated'))})
    return out


def _python_outdated(directory, root, timeout):
    if os.path.isfile(os.path.join(directory, 'uv.lock')) and shutil.which('uv'):
        argv = ['uv', 'tree', '--outdated', '--frozen', '--depth', '1']
        res = run_cmd(argv, cwd=directory, timeout=timeout)
        out = {'command': ' '.join(argv), 'cwd': os.path.relpath(directory, root), 'records': []}
        if res['error'] or res['rc'] != 0:
            out['error'] = res['error'] or res['stderr'].strip()[-500:]
            return out
        for line in res['stdout'].splitlines():
            m = re.search(r'([A-Za-z0-9_.\-]+) v(\S+)(?: \[[^\]]*\])?.*\(latest: v([^)]+)\)', line)
            if m:
                out['records'].append({'ecosystem': 'python', 'name': m.group(1), 'current': m.group(2),
                                       'latest': m.group(3), 'wanted': None, 'type': 'direct'})
        return out
    return {'command': None, 'cwd': os.path.relpath(directory, root), 'records': [],
            'error': 'no uv.lock (or uv missing): use the project\'s own tool (poetry show --outdated, '
                     'pip list --outdated inside its venv); see references/ecosystems.md'}


PNPM_CONFIG_HAZARD_RE = re.compile(
    r'(?<![\w-])["\']?(?:global[\s_-]?)?pnpmfile["\']?\s*[=:]'       # pnpmfile / globalPnpmfile hooks
    r'|(?<![\w-])config[\s_-]?dependencies\b'                         # configDependencies
    r'|(?<![\w-])["\']?manage[\s_-]?package[\s_-]?manager[\s_-]?versions["\']?\s*[=:]\s*["\']?true\b',
    re.I)
COREPACK_ENV_FILE = '.corepack.env'


def _read_config(path, limit=CONFIG_READ_CAP):
    """(text, hazard reason): the whole file, or why it must be treated as a hazard.

    Checking only the first ``limit`` bytes of a larger file would hide settings below the cut, so an
    oversized (or unreadable) config is reported instead of partially inspected.
    """
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, 'rb') as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        return None, 'could not be read (%s)' % exc.__class__.__name__
    if len(data) > limit:
        return None, 'is larger than the %d-byte read cap (not inspected)' % limit
    return data.decode('utf-8', 'replace'), None


def native_query_hazards(directory, root, manager, pkg):
    """Repo-controlled config that would make a native read-only query execute repo code."""
    hazards = []
    probe = directory
    while True:
        for name in ('.pnpmfile.cjs', '.pnpmfile.mjs', 'pnpmfile.js'):
            if manager == 'pnpm' and os.path.exists(os.path.join(probe, name)):
                hazards.append('%s executes when pnpm loads config' % os.path.relpath(os.path.join(probe, name), root))
        if os.path.exists(os.path.join(probe, COREPACK_ENV_FILE)):
            hazards.append('%s is Corepack configuration (the repo controls package-manager downloads)'
                           % os.path.relpath(os.path.join(probe, COREPACK_ENV_FILE), root))
        if os.path.realpath(probe) == os.path.realpath(root) or os.path.dirname(probe) == probe:
            break
        probe = os.path.dirname(probe)
    if manager == 'pnpm':
        # pnpm loads .npmrc and pnpm-workspace.yaml from every directory up to the repo root.
        configs = [os.path.join(directory, 'package.json')]
        probe = directory
        while True:
            configs.extend(os.path.join(probe, name) for name in ('.npmrc', 'pnpm-workspace.yaml'))
            if os.path.realpath(probe) == os.path.realpath(root) or os.path.dirname(probe) == probe:
                break
            probe = os.path.dirname(probe)
        for path in configs:
            text, problem = _read_config(path)
            rel = os.path.relpath(path, root)
            if problem:
                hazards.append('%s %s' % (rel, problem))
            elif text and PNPM_CONFIG_HAZARD_RE.search(text):
                hazards.append('%s declares pnpm configDependencies/pnpmfile or enables package-manager '
                               'version downloads (code loaded by pnpm)' % rel)
    declared = (pkg or {}).get('packageManager') or ''
    if declared and not re.match(r'^[a-z]+@\d+\.\d+\.\d+[\w.+-]*$', declared):
        hazards.append('packageManager %r is not name@version (Corepack may download and run it)' % declared[:120])
    return hazards


CARGO_CONFIG_FILES = ('config', 'config.toml')
CARGO_CREDENTIAL_KEYS = ('credential-provider', 'global-credential-providers', 'credential-alias')
CARGO_PROGRAM_KEYS = ('net.git-fetch-with-cli', 'build.rustc', 'build.rustc-wrapper',
                      'build.rustc-workspace-wrapper')
# `include` pulls in config files the walk never sees; `[unstable]` can enable that same loader.
CARGO_LOADER_KEYS = ('include', 'unstable')
RUST_TOOLCHAIN_FILES = ('rust-toolchain.toml', 'rust-toolchain')
BARE_CHANNEL_RE = re.compile(r'^[A-Za-z0-9._-]+$')  # a legacy rust-toolchain file may be just `stable`


def _cargo_config_hazard(path):
    """First repo-controlled cargo setting that can run code or use credentials during a query.

    Parsed as TOML (dotted keys, inline tables, quoted keys and headers all reach the same structure);
    anything not inspectable - no parser, invalid TOML, a file past the read cap - is a hazard.
    """
    text, problem = _read_config(path)
    if problem:
        return problem
    if text is None:
        return None
    if _tomllib is None:
        return 'cannot be parsed safely (TOML support needs Python 3.11+); not inspected'
    try:
        data = _tomllib.loads(text)
    except (ValueError, TypeError, RecursionError) as exc:
        return 'is not valid TOML (%s); not inspected' % exc.__class__.__name__
    return _cargo_settings_hazard(data)


def _cargo_settings_hazard(data):
    """Walk the parsed config and return the first dangerous setting, in any valid TOML spelling."""
    def walk(node, path):
        if not isinstance(node, dict):
            return None
        for key in node:
            name = str(key)
            dotted = path + [name]
            joined = '.'.join(dotted)
            if not path and name in CARGO_LOADER_KEYS:
                return 'sets top-level %s (cargo loads extra config files or enables unstable features)' % name
            if name in ('registries', 'env'):
                return 'defines [%s] (registry credentials/sources or repository-controlled env load)' % joined
            if name in CARGO_CREDENTIAL_KEYS:
                return 'sets %s (cargo may run a credential helper)' % joined
            if dotted[0] == 'source' and name == 'replace-with':
                return 'replaces a cargo source (%s)' % joined
            if joined in CARGO_PROGRAM_KEYS:
                return 'sets %s (cargo may run a repository-supplied program)' % joined
            found = walk(node[key], dotted)
            if found:
                return found
        return None
    return walk(data, [])


def _rust_toolchain_hazard(path):
    """Hazard reason for a repo-controlled toolchain file, or None when it is safe.

    ``rust-toolchain.toml`` is TOML; any ``toolchain.path`` points cargo at a repository-supplied
    toolchain. The legacy extensionless ``rust-toolchain`` file is either a bare channel name or the
    same TOML, so anything else (invalid TOML, no parser) is a hazard.
    """
    text, problem = _read_config(path)
    if problem:
        return problem
    if text is None:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if os.path.basename(path) == 'rust-toolchain' and len(lines) == 1 and BARE_CHANNEL_RE.match(lines[0]):
        return None  # `stable`, `1.74.0`, `nightly-2024-01-01`: nothing to load
    if _tomllib is None:
        return 'cannot be parsed safely (TOML support needs Python 3.11+); not inspected'
    try:
        data = _tomllib.loads(text)
    except (ValueError, TypeError, RecursionError) as exc:
        return 'is not valid TOML (%s); not inspected' % exc.__class__.__name__
    toolchain = data.get('toolchain') if isinstance(data, dict) else None
    if isinstance(toolchain, dict) and 'path' in toolchain:
        return 'sets toolchain.path (repo-supplied cargo would run)'
    return None


def rust_toolchain_hazard(directory, root):
    """Repo-controlled toolchain or cargo config that makes a native cargo query unsafe."""
    probe = directory
    while True:
        for name in RUST_TOOLCHAIN_FILES:
            path = os.path.join(probe, name)
            hazard = _rust_toolchain_hazard(path)
            if hazard:
                return '%s %s' % (os.path.relpath(path, root), hazard)
        for name in CARGO_CONFIG_FILES:
            path = os.path.join(probe, '.cargo', name)
            hazard = _cargo_config_hazard(path)
            if hazard:
                return '%s %s' % (os.path.relpath(path, root), hazard)
        if os.path.realpath(probe) == os.path.realpath(root) or os.path.dirname(probe) == probe:
            return None
        probe = os.path.dirname(probe)


def outdated(root, timeout):
    inv = inventory(root)
    results = {'root': root, 'queries': [], 'records': [], 'skipped': []}
    covered = set()
    projects = inv['js_projects']
    workspace_roots = {os.path.dirname(os.path.join(root, p['path'])) for p in projects
                       if p.get('workspaces')} | {os.path.dirname(os.path.join(root, w['path'])) for w in inv['pnpm_workspaces']}
    by_dir = {os.path.dirname(os.path.join(root, p['path'])): p for p in projects}
    hazards_of = {d: native_query_hazards(d, root, p.get('manager'), p) for d, p in by_dir.items()}

    def native_root(directory):
        """True when a native recursive query at this workspace root covers its members."""
        pkg = by_dir.get(directory)
        manager = (pkg or {}).get('manager')
        return bool(pkg and manager in ('npm', 'pnpm') and shutil.which(manager) and not hazards_of[directory])

    for pkg in projects:
        directory = os.path.dirname(os.path.join(root, pkg['path']))
        manager = pkg.get('manager')
        if any(within(ws, directory) and ws != directory and native_root(ws) for ws in workspace_roots):
            continue  # covered by the native workspace root query
        hazards = hazards_of[directory]
        if hazards:
            results['skipped'].append('%s: native %s query skipped: %s; used registry fallback'
                                      % (pkg['path'], manager, '; '.join(hazards)))
            results.setdefault('hazards', []).extend(hazards)
            query = _registry_outdated(pkg, directory, manager or 'unknown', root, timeout)
        elif manager in ('npm', 'pnpm') and shutil.which(manager):
            query = _npm_outdated(directory, manager, root, timeout, directory in workspace_roots)
        else:
            if manager in ('npm', 'pnpm'):
                results['skipped'].append('%s: %s not installed; using registry fallback' % (pkg['path'], manager))
            query = _registry_outdated(pkg, directory, manager or 'unknown', root, timeout)
        results['queries'].append(query)
    for manifest in inv['other_manifests']:
        directory = os.path.dirname(os.path.join(root, manifest['path']))
        key = (manifest['ecosystem'], directory)
        if key in covered:
            continue
        covered.add(key)
        eco = manifest['ecosystem']
        if eco == 'rust':
            if any(k[0] == 'rust' and within(k[1], directory) and k[1] != directory for k in covered):
                continue
            hazard = rust_toolchain_hazard(directory, root)
            if hazard:
                results.setdefault('hazards', []).append(hazard)
                query = {'cwd': manifest['path'], 'records': [], 'error': 'cargo query skipped: ' + hazard}
            elif shutil.which('cargo'):
                query = _cargo_outdated(directory, root, timeout)
            else:
                query = {'cwd': manifest['path'], 'records': [], 'error': 'cargo not installed'}
        elif eco == 'go':
            query = _go_outdated(directory, root, timeout) if shutil.which('go') else \
                {'cwd': manifest['path'], 'records': [], 'error': 'go not installed'}
        elif eco == 'python':
            query = _python_outdated(directory, root, timeout)
        else:
            continue
        results['queries'].append(query)
    for eco in inv['unsupported']:
        results['skipped'].append('%s: advisory support only (no automated outdated query)' % eco)
    if results.get('hazards'):
        results['hazards'] = sorted(set(results['hazards']))
    for query in results['queries']:
        results['records'].extend(query.get('records', []))
    results['errors'] = [{'cwd': q.get('cwd'), 'error': q['error']} for q in results['queries'] if q.get('error')]
    return results


# -------------------------------------------------------------------- classify

VERSION_RE = re.compile(r'(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-.]?((?:alpha|beta|rc|pre|preview|dev|next|canary|nightly|a|b|c)[\w.]*))?', re.I)


def parse_version(value):
    if not isinstance(value, str):
        return None
    text = value.strip().lstrip('^~=<>!v ').split('||')[0].strip().lstrip('v')
    text = text.split('+', 1)[0]
    m = VERSION_RE.match(text)
    if not m:
        return None
    major, minor, patch = (int(m.group(i)) if m.group(i) else 0 for i in (1, 2, 3))
    pre = m.group(4) or ''
    if not pre and '-' in text:
        pre = text.split('-', 1)[1]
    return (major, minor, patch, pre)


FAMILIES = [
    ('react', re.compile(r'^(react|react-dom|react-is|scheduler|@types/react(-dom)?)$')),
    ('svelte', re.compile(r'^(svelte|svelte-check|@sveltejs/.+|svelte-.+|@types/svelte)$')),
    ('typescript', re.compile(r'^(typescript|@typescript-eslint/.+|typescript-eslint|ts-node|tsx|@types/node)$')),
    ('eslint', re.compile(r'^(eslint|@eslint/.+|eslint-(plugin|config)-.+)$')),
    ('vite', re.compile(r'^(vite|vitest|@vitest/.+|@vitejs/.+|vite-plugin-.+)$')),
    ('babel', re.compile(r'^(@babel/.+|babel-.+)$')),
    ('jest', re.compile(r'^(jest|ts-jest|babel-jest|@jest/.+|jest-.+|@types/jest)$')),
    ('next', re.compile(r'^(next|eslint-config-next|@next/.+)$')),
    ('vue', re.compile(r'^(vue|@vue/.+|vue-.+)$')),
    ('angular', re.compile(r'^@angular(-devkit)?/.+$')),
    ('tailwind', re.compile(r'^(tailwindcss|@tailwindcss/.+|prettier-plugin-tailwindcss)$')),
]


def family_of(name):
    for family, pattern in FAMILIES:
        if pattern.match(name or ''):
            return family
    if name and name.startswith('@') and '/' in name:
        return name.split('/', 1)[0]
    return None


def classify_record(record):
    current = parse_version(record.get('current') or record.get('declared') or record.get('spec'))
    target = parse_version(record.get('latest'))
    flags = []
    if record.get('deprecated'):
        flags.append('deprecated')
    if record.get('error'):
        flags.append('lookup-error')
    if current is None or target is None:
        bump, risk = 'unknown', 'high'
        flags.append('unparseable-version')
    elif current[:3] == target[:3] and current[3] == target[3]:
        bump, risk = 'none', 'none'
    elif target[:3] < current[:3]:
        bump, risk = 'downgrade', 'high'
        flags.append('latest-is-lower')
    elif target[0] != current[0]:
        bump, risk = 'major', 'high'
    elif target[0] == 0 and target[1] != current[1]:
        bump, risk = 'major', 'high'
        flags.append('zerover-minor-is-breaking')
    elif target[0] == 0 and target[1] == 0 and target[2] != current[2]:
        bump, risk = 'major', 'high'
        flags.append('zerover-patch-is-breaking')
    elif target[1] != current[1]:
        bump, risk = 'minor', 'medium'
    elif target[2] != current[2]:
        bump, risk = 'patch', 'low'
    else:
        bump, risk = 'prerelease', 'high'
    if target and target[3]:
        flags.append('prerelease-target')
        risk = 'high'
    if record.get('ecosystem') == 'go' and bump == 'major':
        flags.append('go-major-needs-module-path-change')
    if record.get('type') in ('peerDependencies', 'peer'):
        flags.append('peer-range-change-affects-consumers')
    if record.get('deprecated') and risk in ('none', 'low'):
        risk = 'medium'
    if bump == 'none' and not record.get('deprecated'):
        category = 'current'
    elif record.get('deprecated'):
        category = 'deprecated'
    elif 'lookup-error' in flags:
        category = 'blocked'
    else:
        category = 'outdated'
    out = dict(record)
    out.update({'bump': bump, 'risk': risk, 'category': category, 'flags': flags,
                'family': family_of(record.get('name'))})
    return out


def classify(records):
    classified = [classify_record(r) for r in records if isinstance(r, dict)]
    order = {'high': 0, 'medium': 1, 'low': 2, 'none': 3}
    classified.sort(key=lambda r: (order.get(r['risk'], 9), r.get('family') or '', r.get('name') or ''))
    families = {}
    for r in classified:
        if r['family'] and r['bump'] not in ('none',):
            families.setdefault(r['family'], []).append(r['name'])
    counts = {}
    for r in classified:
        counts[r['bump']] = counts.get(r['bump'], 0) + 1
    return {'records': classified, 'families': {k: sorted(set(v)) for k, v in families.items() if len(set(v)) > 1},
            'counts': counts}


# ---------------------------------------------------------------------- journal

def journal_path(run_dir):
    return os.path.join(run_dir, 'journal.json')


def load_journal(run_dir):
    data, error = read_json(journal_path(run_dir))
    if data is None:
        raise Refusal('cannot read journal: %s' % error)
    return data


def save_journal(run_dir, journal):
    journal['updated'] = now()
    atomic_write(journal_path(run_dir), json.dumps(journal, indent=2) + '\n')


def exclusive_create(path, text):
    """Create ``path`` with full content atomically; FileExistsError if it exists."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.lock-', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.link(tmp, path)  # atomic; never exposes a half-written lock
    finally:
        os.unlink(tmp)


class JournalLock:
    """Serialise read-modify-write of one journal across concurrent helpers."""

    STALE_SECONDS = 1800  # longer than any command run while holding it (clone: 600 s)

    def __init__(self, run_dir, attempts=200):
        self.path = os.path.join(run_dir, 'journal.lock')
        self.attempts = attempts
        self.token = uuid.uuid4().hex

    def __enter__(self):
        import time
        for _ in range(self.attempts):
            try:
                exclusive_create(self.path, json.dumps({'token': self.token, 'pid': os.getpid(), 'at': now()}))
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > self.STALE_SECONDS:
                        os.unlink(self.path)  # a helper call never holds it this long
                        continue
                except OSError:
                    continue
                time.sleep(0.1)
        try:
            age = int(time.time() - os.path.getmtime(self.path))
        except OSError:
            age = 0
        raise Refusal('journal is busy (lock %s, held %ds; treated as stale after %ds). Retry; if no other '
                      'depup.py process is running, the holder was killed and the lock file may be removed.'
                      % (self.path, age, self.STALE_SECONDS))

    def __exit__(self, *exc):
        held, _ = read_json(self.path)
        if held and held.get('token') == self.token:  # never remove a lock someone else now holds
            try:
                os.unlink(self.path)
            except OSError:
                pass
        return False


def acquire_repo_lock(root, run_id):
    """One mutating run per repository: the lock serialises lockfile/manifest mutation."""
    lock = os.path.join(repo_state_dir(root), 'mutation.lock')
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    payload = json.dumps({'run_id': run_id, 'host': socket.gethostname(), 'created': now()})
    reclaimed = None
    for _ in range(2):
        try:
            exclusive_create(lock, payload)
            return lock, reclaimed
        except FileExistsError:
            held, _ = read_json(lock)
            held = held or {}
            holder = held.get('run_id')
            holder_dir = os.path.join(repo_state_dir(root), holder) if holder and SAFE_RUN_ID.match(holder) else None
            journal, _ = read_json(journal_path(holder_dir)) if holder_dir else (None, None)
            state = (journal or {}).get('status')
            if state == 'active' or reclaimed is not None:
                raise Refusal('repository mutation lock held by run %s (created %s, journal status: %s). Resume it '
                              '(run status --repo), or finish/roll back that run first.'
                              % (holder, held.get('created'), state))
            # The holder ended (or its journal is gone) without releasing: the lock protects nothing.
            reclaimed = {'run_id': holder, 'journal_status': state or 'missing', 'created': held.get('created')}
            try:
                os.unlink(lock)
            except FileNotFoundError:
                pass
    raise Refusal('repository mutation lock is contended; retry')


def holds_repo_lock(root, run_id):
    held, _ = read_json(os.path.join(repo_state_dir(root), 'mutation.lock'))
    return bool(held and held.get('run_id') == run_id)


def require_active_upgrade(journal):
    if journal['mode'] != 'upgrade':
        raise Refusal('worktrees are only created for upgrade runs; plan runs are read-only')
    if journal.get('status') != 'active' or not holds_repo_lock(journal['repo_root'], journal['run_id']):
        raise Refusal('run %s is not active or no longer holds the repository mutation lock' % journal['run_id'])


def release_repo_lock(root, run_id):
    lock = os.path.join(repo_state_dir(root), 'mutation.lock')
    held, _ = read_json(lock)
    if held and held.get('run_id') == run_id:
        os.unlink(lock)
        return True
    return False


def cmd_run_start(args):
    root, is_git = resolve_repo(args.repo)
    if args.mode == 'upgrade' and not is_git:
        raise Refusal('upgrade mode needs a Git repository for isolation and rollback')
    run_id = args.run_id or ('%s-%s' % (_dt.datetime.now().strftime('%Y%m%d-%H%M%S'), uuid.uuid4().hex[:6]))
    if not SAFE_RUN_ID.match(run_id):
        raise Refusal('invalid run id')
    run_dir = os.path.join(repo_state_dir(root), run_id)
    if os.path.exists(run_dir):
        raise Refusal('run already exists: %s (use "run status" to resume)' % run_dir)
    gs = git_state(root) if is_git else {}
    lock, reclaimed = acquire_repo_lock(root, run_id) if args.mode == 'upgrade' else (None, None)
    closed = close_stale_plan_runs(root)
    journal = {
        'schema': SCHEMA_VERSION, 'run_id': run_id, 'mode': args.mode, 'repo_root': root,
        'repo_key': repo_key(root), 'created': now(), 'status': 'active',
        'base_commit': gs.get('head'), 'base_branch': gs.get('branch'),
        'base_dirty': [e['path'] for e in gs.get('dirty', [])], 'base_untracked': gs.get('untracked', []),
        'worktree': None, 'branch': None, 'lock': lock,
        'owned': {'worktrees': [], 'branches': [], 'paths': []},
        'steps': [], 'notes': [],
        'checkout_operation_in_progress': gs.get('operation_in_progress', []),
    }
    if reclaimed:
        journal['notes'].append({'at': now(), 'reclaimed_stale_lock': reclaimed})
    os.makedirs(run_dir, exist_ok=True)
    save_journal(run_dir, journal)
    return {'run_dir': run_dir, 'run_ref': '%s/%s' % (journal['repo_key'], run_id), 'journal': journal_path(run_dir),
            'mode': args.mode, 'base_commit': journal['base_commit'],
            'dirty_files_preserved': journal['base_dirty'] + journal['base_untracked'],
            'reclaimed_stale_lock': reclaimed, 'closed_stale_plan_runs': closed,
            'warning': ('the user checkout is dirty: never modify it; work only in the owned worktree'
                        if journal['base_dirty'] or journal['base_untracked'] else None)}


def close_stale_plan_runs(root):
    """Plan runs own no Git state; an earlier one left active is closed so it never blocks or confuses resume."""
    closed = []
    for run in list_runs(root):
        if run.get('mode') != 'plan' or run.get('status') != 'active':
            continue
        try:
            with JournalLock(run['run_dir'], attempts=20):
                journal = load_journal(run['run_dir'])
                if journal.get('mode') == 'plan' and journal.get('status') == 'active':
                    journal['status'] = 'abandoned'
                    journal['notes'].append({'at': now(), 'closed': 'superseded by a newer run'})
                    save_journal(run['run_dir'], journal)
                    closed.append(journal['run_id'])
        except Refusal:
            continue
    return closed


def cmd_run_step(args):
    run_dir = run_dir_for(args.run)
    if args.status not in STEP_STATUSES:
        raise Refusal('status must be one of ' + ', '.join(STEP_STATUSES))
    with JournalLock(run_dir):
        return _run_step_locked(run_dir, args)


def _run_step_locked(run_dir, args):
    journal = load_journal(run_dir)
    steps = journal['steps']
    step = next((s for s in steps if s['id'] == args.id), None)
    if step is None:
        step = {'id': args.id, 'title': args.title or args.id, 'status': 'pending', 'dangerous': bool(args.dangerous),
                'created': now(), 'evidence': [], 'notes': []}
        steps.append(step)
    elif step['status'] == 'done' and step.get('dangerous') and args.status != 'done':
        if not args.force:
            raise Refusal('step %s is a completed dangerous step; do not repeat it without rechecking '
                          'preconditions and recovery (a safe repeat requires --force, --note and '
                          'run-local evidence)' % args.id)
        if not (args.note or '').strip():
            raise Refusal('--force requires --note with evidence that repeating this exact dangerous '
                          'step is safe')
        if not safe_repeat_evidence(run_dir, journal, args.id, args.evidence or []):
            raise Refusal('--force requires structured --evidence inside this run, tied to the current '
                          'clean worktree HEAD, with a safe-to-repeat decision, verified preconditions, '
                          'reason and recovery path')
    if args.title:
        step['title'] = args.title
    if args.dangerous:
        step['dangerous'] = True
    step['status'] = args.status
    step['%s_at' % args.status] = now()
    if args.evidence:
        step['evidence'].extend(args.evidence)
    if args.note:
        step['notes'].append(args.note)
    save_journal(run_dir, journal)
    return {'run_dir': run_dir, 'step': step}


def summarize_journal(run_dir, journal):
    steps = journal.get('steps', [])
    pending = [s for s in steps if s['status'] in ('pending', 'running', 'failed', 'blocked')]
    worktree_state = None
    if journal.get('worktree') and os.path.isdir(journal['worktree']):
        gs = git_state(journal['worktree'])
        worktree_state = {'path': journal['worktree'], 'head': gs.get('head'), 'clean': gs.get('clean'),
                          'dirty': [e['path'] for e in gs.get('dirty', [])], 'untracked': gs.get('untracked')}
    checkout_changes = None
    if os.path.isdir(journal['repo_root']) and journal.get('base_commit'):
        now_state = git_state(journal['repo_root'])
        before = set(journal.get('base_dirty', [])) | set(journal.get('base_untracked', []))
        after = {e['path'] for e in now_state.get('dirty', [])} | set(now_state.get('untracked', []))
        checkout_changes = {'new_or_changed_paths': sorted(after - before),
                            'head_moved': now_state.get('head') != journal['base_commit'],
                            'note': 'the skill never writes here; report these as changes by other tools or the user'}
    return {
        'user_checkout_changes_since_start': checkout_changes,
        'run_dir': run_dir, 'run_id': journal['run_id'], 'mode': journal['mode'], 'status': journal['status'],
        'repo_root': journal['repo_root'], 'base_commit': journal['base_commit'],
        'branch': journal.get('branch'), 'worktree': worktree_state,
        'counts': {s: len([x for x in steps if x['status'] == s]) for s in STEP_STATUSES},
        'completed_dangerous': [s['id'] for s in steps if s['status'] == 'done' and s.get('dangerous')],
        'next': pending[0] if pending else None,
        'steps': [{'id': s['id'], 'title': s['title'], 'status': s['status'], 'dangerous': s.get('dangerous', False)}
                  for s in steps],
        'report': os.path.join(run_dir, 'report.json') if os.path.exists(os.path.join(run_dir, 'report.json')) else None,
    }


def list_runs(root):
    base = repo_state_dir(root)
    runs = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            run_dir = os.path.join(base, name)
            if os.path.isfile(journal_path(run_dir)):
                journal, _ = read_json(journal_path(run_dir))
                if journal:
                    runs.append({'run_dir': run_dir, 'run_id': journal.get('run_id'), 'mode': journal.get('mode'),
                                 'status': journal.get('status'), 'created': journal.get('created')})
    runs.sort(key=lambda r: r.get('created') or '')
    return runs


def cmd_run_status(args):
    if args.run:
        run_dir = run_dir_for(args.run)
        return summarize_journal(run_dir, load_journal(run_dir))
    root, _ = resolve_repo(args.repo)
    runs = list_runs(root)
    if not runs:
        return {'repo_root': root, 'runs': [], 'message': 'no runs recorded for this repository'}
    latest = runs[-1]['run_dir']
    return summarize_journal(latest, load_journal(latest))


def cmd_run_worktree(args):
    run_dir = run_dir_for(args.run)
    with JournalLock(run_dir):
        return _run_worktree_locked(run_dir, args)


def _run_worktree_locked(run_dir, args):
    journal = load_journal(run_dir)
    require_active_upgrade(journal)
    if journal.get('worktree'):
        return {'worktree': journal['worktree'], 'branch': journal['branch'], 'created': False}
    root = journal['repo_root']
    branch = 'depup/%s' % journal['run_id']
    if git(root, 'rev-parse', '--verify', '--quiet', 'refs/heads/' + branch)['rc'] == 0:
        raise Refusal('branch %s already exists and is not owned by this run' % branch)
    path = os.path.join(run_dir, 'worktree')
    if os.path.exists(path):
        raise Refusal('path already exists: %s' % path)
    upstream = upstream_default(root)
    if args.base:
        base, base_source = args.base, 'explicit --base'
    elif args.base_source == 'head':
        base, base_source = journal['base_commit'], 'checkout HEAD (--base-source head)'
    else:
        base, base_source = None, None
    warnings = []
    if args.isolation == 'clone':
        # For sandboxes where the repository's .git is read-only: a private clone inside the run dir.
        res = run_cmd(['git', 'clone', '--quiet', '--no-hardlinks', root, path], timeout=600)
        if res['rc'] == 0 and upstream.get('url'):
            # Fetch the real upstream into the clone, so delivery never needs the read-only .git.
            git(path, 'remote', 'add', 'upstream', upstream['url'])
            if base is None:
                fetched = git(path, 'fetch', '--quiet', 'upstream', upstream['branch'], timeout=300)
                tip = git(path, 'rev-parse', '--verify', '--quiet', 'refs/remotes/upstream/' + upstream['branch'])
                if fetched['rc'] == 0 and tip['rc'] == 0:
                    base, base_source = tip['stdout'].strip(), 'fetched upstream/%s' % upstream['branch']
                else:
                    warnings.append('upstream fetch failed: ' + (fetched['stderr'] or fetched['error'] or '').strip()[:200])
        if res['rc'] == 0 and base is None:
            base, base_source = fallback_base(root, journal, upstream, warnings)
        if res['rc'] == 0:
            res = git(path, 'checkout', '-q', '-b', branch, base)
        if res['rc'] != 0:
            raise Refusal('isolated clone failed: ' + (res['stderr'] or res['error'] or '').strip())
        journal['isolation'] = 'clone'
        journal['owned']['clones'] = [path]
        fetch = 'git -C %s fetch %s %s:%s' % (root, path, branch, branch)
        push = ('git -C %s push upstream %s' % (path, branch)) if upstream.get('url') else None
    else:
        if base is None and upstream.get('remote') and upstream.get('branch'):
            fetched = git(root, 'fetch', '--quiet', upstream['remote'], upstream['branch'], timeout=300)
            tip = git(root, 'rev-parse', '--verify', '--quiet',
                      'refs/remotes/%s/%s' % (upstream['remote'], upstream['branch']))
            if fetched['rc'] == 0 and tip['rc'] == 0:
                base, base_source = tip['stdout'].strip(), 'fetched %s/%s' % (upstream['remote'], upstream['branch'])
            else:
                warnings.append('upstream fetch failed: ' + (fetched['stderr'] or fetched['error'] or '').strip()[:200])
        if base is None:
            base, base_source = fallback_base(root, journal, upstream, warnings)
        # Push from the repository root: a relative remote URL resolves against it, not the worktree.
        push = ('git -C %s push %s %s' % (root, upstream['remote'], branch)) if upstream.get('remote') else None
        res = git(root, 'worktree', 'add', '-b', branch, path, base, timeout=300)
        if res['rc'] != 0:
            err = (res['stderr'] or res['error'] or '').strip()
            hint = (' The repository .git looks read-only (sandbox); retry with "--isolation clone".'
                    if re.search(r'Operation not permitted|Permission denied|Read-only file system', err) else '')
            raise Refusal('git worktree add failed: ' + err + hint)
        journal['isolation'] = 'worktree'
        journal['owned']['worktrees'].append(path)
        journal['owned']['branches'].append(branch)
        fetch = None
    submodules = None
    if os.path.isfile(os.path.join(path, '.gitmodules')):
        sub = git(path, 'submodule', 'update', '--init', '--recursive', timeout=600)
        submodules = 'initialized' if sub['rc'] == 0 else 'failed: ' + (sub['stderr'] or sub['error'] or '').strip()[:200]
    excluded = git(root, 'rev-list', '--count', '%s..%s' % (base, journal['base_commit'])) \
        if journal.get('base_commit') and base != journal['base_commit'] else None
    journal['worktree'] = path
    journal['branch'] = branch
    journal['worktree_base'] = base
    journal['base_source'] = base_source
    journal['upstream'] = {k: upstream.get(k) for k in ('remote', 'branch')}
    save_journal(run_dir, journal)
    return {'worktree': path, 'branch': branch, 'base': base, 'base_source': base_source, 'created': True,
            'isolation': journal['isolation'], 'deliver_with': fetch, 'push_with': push,
            'upstream': journal['upstream'], 'submodules': submodules, 'warnings': warnings,
            'checkout_commits_not_in_base': int(excluded['stdout'].strip() or 0)
            if excluded and excluded['rc'] == 0 else None}


def upstream_default(root):
    """Remote and default branch the upgrade should start from (no network unless the ref is unknown)."""
    remotes = [r for r in (git(root, 'remote')['stdout'] or '').split() if r]
    if not remotes:
        return {}
    upstream = git(root, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
    preferred = upstream['stdout'].strip().split('/', 1)[0] if upstream['rc'] == 0 else None
    remote = 'origin' if 'origin' in remotes else (preferred if preferred in remotes else remotes[0])
    branch = None
    head = git(root, 'symbolic-ref', '--quiet', '--short', 'refs/remotes/%s/HEAD' % remote)
    if head['rc'] == 0 and head['stdout'].strip().startswith(remote + '/'):
        branch = head['stdout'].strip()[len(remote) + 1:]
    if not branch:
        listed = git(root, 'ls-remote', '--symref', remote, 'HEAD', timeout=60)
        m = re.search(r'^ref:\s+refs/heads/(\S+)\s+HEAD', listed['stdout'] or '', re.M)
        branch = m.group(1) if m else None
    if not branch:
        for name in ('main', 'master'):
            if git(root, 'rev-parse', '--verify', '--quiet', 'refs/remotes/%s/%s' % (remote, name))['rc'] == 0:
                branch = name
                break
    url = git(root, 'remote', 'get-url', remote)
    url = url['stdout'].strip() if url['rc'] == 0 else None
    if url and not re.match(r'^[A-Za-z][A-Za-z0-9+.-]*://|^[^/]+@[^/]+:', url) and not os.path.isabs(url):
        url = os.path.normpath(os.path.join(root, url))  # local path remote, relative to the repository
    return {'remote': remote, 'branch': branch, 'url': url} \
        if branch else {'remote': remote, 'branch': None, 'url': None}


def fallback_base(root, journal, upstream, warnings):
    """Offline or no remote: last known remote-tracking tip, else the checkout HEAD (never mid-operation)."""
    if upstream.get('remote') and upstream.get('branch'):
        tip = git(root, 'rev-parse', '--verify', '--quiet',
                  'refs/remotes/%s/%s' % (upstream['remote'], upstream['branch']))
        if tip['rc'] == 0:
            warnings.append('using the last fetched %s/%s; it may be stale' % (upstream['remote'], upstream['branch']))
            return tip['stdout'].strip(), 'stale %s/%s' % (upstream['remote'], upstream['branch'])
    if journal.get('checkout_operation_in_progress'):
        raise Refusal('no upstream base available and the checkout has a Git operation in progress (%s); '
                      'its HEAD is not a safe base' % ', '.join(journal['checkout_operation_in_progress']))
    warnings.append('no reachable upstream default branch; based on the checkout HEAD')
    return journal['base_commit'], 'checkout HEAD (no upstream)'


SAFE_DOMAIN = re.compile(r'^[a-z0-9][a-z0-9-]{0,40}$')


def cmd_run_worker(args):
    """Owned worktree + branch for one parallel migration domain (never touches lockfiles by contract)."""
    run_dir = run_dir_for(args.run)
    with JournalLock(run_dir):
        journal = load_journal(run_dir)
        require_active_upgrade(journal)
        if not SAFE_DOMAIN.match(args.domain):
            raise Refusal('domain must match %s' % SAFE_DOMAIN.pattern)
        if not journal.get('worktree'):
            raise Refusal('create the integration worktree first ("run worktree")')
        if journal.get('isolation') == 'clone':
            raise Refusal('parallel workers need worktree isolation; with --isolation clone work serially')
        root = journal['repo_root']
        branch = 'depup/%s-%s' % (journal['run_id'], args.domain)
        path = os.path.join(run_dir, 'workers', args.domain)
        if branch in journal['owned']['branches']:
            return {'worktree': path, 'branch': branch, 'created': False}
        if git(root, 'rev-parse', '--verify', '--quiet', 'refs/heads/' + branch)['rc'] == 0 or os.path.exists(path):
            raise Refusal('branch %s or path %s already exists and is not owned by this run' % (branch, path))
        base = args.base or journal.get('worktree_base') or journal['base_commit']
        res = git(root, 'worktree', 'add', '-b', branch, path, base, timeout=300)
        if res['rc'] != 0:
            raise Refusal('git worktree add failed: ' + (res['stderr'] or res['error'] or '').strip())
        journal['owned']['worktrees'].append(path)
        journal['owned']['branches'].append(branch)
        journal.setdefault('workers', []).append({'domain': args.domain, 'worktree': path, 'branch': branch,
                                                  'base': base, 'created': now()})
        save_journal(run_dir, journal)
    return {'worktree': path, 'branch': branch, 'base': base, 'created': True}


def cmd_run_finish(args):
    run_dir = run_dir_for(args.run)
    with JournalLock(run_dir):
        journal = load_journal(run_dir)
        released = release_repo_lock(journal['repo_root'], journal['run_id'])
        journal['status'] = args.status or 'finished'
        save_journal(run_dir, journal)
    return {'run_dir': run_dir, 'status': journal['status'], 'lock_released': released}


# --------------------------------------------------------------------- rollback

def _rollback_plan(run_dir, journal):
    """Decide per owned worktree/branch: remove (provably safe) or preserve (with reason)."""
    root = journal['repo_root']
    actions, preserved = [], []
    base = journal.get('worktree_base') or journal.get('base_commit')
    owned_branches = list(journal['owned']['branches'])
    branch_of = {journal.get('worktree'): journal.get('branch')}
    for worker in journal.get('workers', []):
        branch_of[worker['worktree']] = worker['branch']
    blocked_branches = set()
    for path in journal['owned']['worktrees']:
        if not os.path.isdir(path):
            continue
        expected = branch_of.get(path)
        if not within(run_dir, path):
            preserved.append({'path': path, 'reason': 'worktree outside run dir; not touched'})
            blocked_branches.add(expected)
            continue
        gs = git_state(path)
        reason = None
        if gs.get('operation_in_progress'):
            reason = 'git operation in progress: ' + ', '.join(gs['operation_in_progress'])
        elif gs.get('branch') != expected:
            reason = 'worktree HEAD is %s, not the owned branch %s (detached commits would be lost)' % (
                gs.get('branch') or 'detached at ' + (gs.get('head') or '?')[:12], expected)
        elif not gs.get('clean'):
            reason = 'worktree has uncommitted changes'
        if reason:
            preserved.append({'path': path, 'reason': reason,
                              'dirty': [e['path'] for e in gs.get('dirty', [])] + gs.get('untracked', [])})
            blocked_branches.add(expected)
        else:
            actions.append({'action': 'git worktree remove', 'path': path})
    for branch in owned_branches:
        if branch in blocked_branches:
            preserved.append({'branch': branch, 'reason': 'its worktree is preserved'})
            continue
        ahead = git(root, 'rev-list', '--count', '%s..%s' % (base, branch))
        count = int(ahead['stdout'].strip() or 0) if ahead['rc'] == 0 else None
        if count == 0:
            actions.append({'action': 'git branch -d', 'branch': branch})
        else:
            preserved.append({'branch': branch, 'reason': '%s commit(s) beyond base %s are preserved'
                              % (count if count is not None else 'unknown', (base or '')[:12])})
    for clone in journal['owned'].get('clones', []):
        if not os.path.isdir(clone):
            continue
        gs = git_state(clone)
        branch = journal.get('branch')
        ahead = git(clone, 'rev-list', '--count', '%s..%s' % (base, branch))
        count = ahead['stdout'].strip() if ahead['rc'] == 0 else None
        if not within(run_dir, clone):
            preserved.append({'path': clone, 'reason': 'clone outside run dir; not touched'})
            continue
        # commits reachable from any local branch/tag/reflog entry but not from base or the remotes
        extra = git(clone, 'rev-list', '--count', '--reflog', '--branches', '--tags', '--not', base, '--remotes')
        unique = extra['stdout'].strip() if extra['rc'] == 0 else None
        has_stash = git(clone, 'rev-parse', '--verify', '--quiet', 'refs/stash')['rc'] == 0
        if gs.get('operation_in_progress') or gs.get('branch') != branch or not gs.get('clean') or count != '0' \
                or unique != '0' or has_stash:
            preserved.append({'path': clone, 'reason': 'isolated clone holds work (commits, stash, reflog-only '
                                                       'commits, changes or detached HEAD)',
                              'deliver_with': 'git -C %s fetch %s %s:%s' % (root, clone, branch, branch)})
        else:
            actions.append({'action': 'remove clone', 'path': clone})
    return actions, preserved


def cmd_rollback(args):
    run_dir = run_dir_for(args.run)
    with JournalLock(run_dir):
        journal = load_journal(run_dir)
        root = journal['repo_root']
        actions, preserved = _rollback_plan(run_dir, journal)
        user_state = git_state(root) if os.path.isdir(root) else {}
        if not args.apply:
            return {'dry_run': True, 'would_do': actions, 'preserved': preserved, 'user_checkout_untouched': True,
                    'user_checkout_dirty': [e['path'] for e in user_state.get('dirty', [])]}
        done, failed = [], []
        failed_worktree_branches = set()
        branch_of = {journal.get('worktree'): journal.get('branch')}
        for worker in journal.get('workers', []):
            branch_of[worker['worktree']] = worker['branch']
        for act in actions:
            if act['action'] == 'git branch -d' and act['branch'] in failed_worktree_branches:
                act.update({'rc': 1, 'stderr': 'kept: its worktree could not be removed'})
                failed.append(act)
                continue
            if act['action'] == 'git worktree remove':
                res = git(root, 'worktree', 'remove', act['path'])  # never --force
            elif act['action'] == 'remove clone':
                try:
                    shutil.rmtree(act['path'])  # proven clean, on the owned branch, no commits beyond base
                    res = {'rc': 0, 'stderr': '', 'error': None}
                    journal['owned']['clones'] = [c for c in journal['owned']['clones'] if c != act['path']]
                except OSError as exc:
                    res = {'rc': 1, 'stderr': str(exc), 'error': None}
            else:
                # -d (never -D); compare against the base explicitly so a moved user HEAD doesn't matter
                tip = git(root, 'rev-parse', '--verify', '--quiet', 'refs/heads/' + act['branch'])
                ahead = git(root, 'rev-list', '--count', '%s..%s' % (journal.get('worktree_base') or
                                                                     journal.get('base_commit'),
                                                                     tip['stdout'].strip() or act['branch']))
                if ahead['rc'] == 0 and ahead['stdout'].strip() == '0' and tip['rc'] == 0:
                    res = git(root, 'update-ref', '-d', 'refs/heads/' + act['branch'], tip['stdout'].strip())
                else:
                    res = {'rc': 1, 'stderr': 'branch gained commits; kept', 'error': None}
            act['rc'] = res['rc']
            act['stderr'] = (res['stderr'] or res['error'] or '').strip()[:300]
            (done if res['rc'] == 0 else failed).append(act)
            if act['action'] == 'git worktree remove' and res['rc'] != 0:
                failed_worktree_branches.add(branch_of.get(act['path']))
            if act['action'] == 'git worktree remove' and res['rc'] == 0:
                journal['owned']['worktrees'] = [p for p in journal['owned']['worktrees'] if p != act['path']]
            if act['action'] == 'git branch -d' and res['rc'] == 0:
                journal['owned']['branches'] = [b for b in journal['owned']['branches'] if b != act['branch']]
        complete = not preserved and not failed
        released = release_repo_lock(root, journal['run_id']) if complete or not failed else False
        journal['status'] = 'rolled-back' if complete else ('rolled-back-partial' if not failed else 'rollback-failed')
        journal['notes'].append({'at': now(), 'rollback': done, 'failed': failed, 'preserved': preserved})
        save_journal(run_dir, journal)
    return {'dry_run': False, 'done': done, 'failed': failed, 'preserved': preserved, 'status': journal['status'],
            'lock_released': released, 'user_checkout_untouched': True}


# ----------------------------------------------------------------------- report

def build_report(run_dir, journal, supplied):
    report = {
        'schema': SCHEMA_VERSION, 'generated': now(), 'run_id': journal['run_id'], 'mode': journal['mode'],
        'repo_root': journal['repo_root'], 'base_commit': journal['base_commit'], 'branch': journal.get('branch'),
        'candidate_commit': candidate_head(journal),
        'worktree': journal.get('worktree'), 'status': 'PLAN' if journal['mode'] == 'plan' else 'PARTIAL',
        'upgrades': [], 'migrations': [], 'files_changed': [], 'scripts_executed': [], 'risks': [],
        'security': [], 'verification': [], 'skipped_checks': [], 'links': [], 'remaining_actions': [],
        'capabilities': {}, 'rollback': {'run_ref': '%s/%s' % (journal['repo_key'], journal['run_id']),
                                         'user_checkout_untouched': True},
        'steps': journal.get('steps', []),
        'planning': [{k: e.get(k) for k in ('id', 'kind', 'subject', 'status', 'head', 'target', 'tree_changed',
                                             'planner', 'selected', 'candidates', 'reason', 'summary', 'counts',
                                             'result', 'evidence')
                      if e.get(k) is not None} for e in planning_entries(run_dir)],
    }
    protected = ('schema', 'generated', 'steps', 'run_id', 'repo_root', 'mode', 'base_commit', 'branch',
                 'worktree', 'candidate_commit', 'rollback', 'status_corrections', 'planning')
    for key, value in (supplied or {}).items():
        if key not in protected:
            report[key] = value
    corrections = []
    if any(p.get('tree_changed') and p.get('target') == journal['repo_root'] for p in report['planning']):
        report['rollback']['user_checkout_untouched'] = False
        corrections.append('the user checkout changed during a planning task; inspect it (not attributed to the upgrade)')
    status = report.get('status')
    if status not in REPORT_STATUSES:
        corrections.append('status %r invalid; set to PARTIAL' % status)
        status = 'PARTIAL'
    if journal['mode'] == 'plan' and status not in ('PLAN', 'BLOCKED'):
        corrections.append('plan runs cannot report %s; set to PLAN' % status)
        status = 'PLAN'
    if status == 'PASS':
        checks = report.get('verification') or []
        failing = [c for c in checks if not isinstance(c, dict) or c.get('result') != 'pass']
        candidate = report.get('candidate_commit')
        missing_check_evidence = [c for c in checks if not isinstance(c, dict) or
                                  not isinstance(c.get('command'), str) or not c['command'].strip() or
                                  not evidence_artifact(run_dir, c.get('log')) or not candidate or
                                  not bound_evidence(run_dir, c.get('evidence'), journal['run_id'], candidate,
                                                     'verification', c.get('command'), c.get('result'),
                                                     expected_artifact=c.get('log'))]
        open_steps = [s['id'] for s in journal.get('steps', []) if s['status'] in ('pending', 'running', 'failed', 'blocked')]
        done_steps = {s['id'] for s in journal.get('steps', []) if s['status'] == 'done'}
        missing_steps = [s for s in REQUIRED_PASS_STEPS if s not in done_steps]
        if not checks:
            corrections.append('PASS requires recorded verification results; downgraded to PARTIAL')
            status = 'PARTIAL'
        elif failing:
            corrections.append('PASS requires every verification result == "pass" (%d not); downgraded to PARTIAL'
                               % len(failing))
            status = 'PARTIAL'
        elif missing_check_evidence:
            corrections.append('PASS requires each verification to name its command and a nonempty log '
                               'inside the run directory (%d missing); downgraded to PARTIAL'
                               % len(missing_check_evidence))
            status = 'PARTIAL'
        elif open_steps:
            corrections.append('PASS requires no open steps (%s); downgraded to PARTIAL' % ', '.join(open_steps))
            status = 'PARTIAL'
        elif missing_steps:
            corrections.append('PASS requires completed journal steps %s; downgraded to PARTIAL'
                               % ', '.join(missing_steps))
            status = 'PARTIAL'
        else:
            def items(key):
                return [x for x in (report.get(key) or []) if isinstance(x, dict)]
            high_risks = [r for r in items('risks') if r.get('severity') in ('high', 'critical')]
            blocked_risks = [r for r in high_risks if r.get('disposition') == 'blocked']

            def complete_dossier(risk):
                def nonempty_text(value):
                    return isinstance(value, str) and bool(value.strip())

                sources = risk.get('sources')
                alternatives = risk.get('alternatives')
                controls = risk.get('controls')
                reviews = risk.get('reviews')
                review_receipts = {}
                if isinstance(reviews, list) and candidate:
                    for review in reviews:
                        if not isinstance(review, dict) or review.get('status') != 'pass':
                            continue
                        review_type = review.get('type')
                        if review_type not in ('independent', 'security'):
                            continue
                        kind = review_type + '-review'
                        if bound_evidence(run_dir, review.get('evidence'), journal['run_id'], candidate,
                                          kind, risk.get('name'), 'pass'):
                            receipt_path = review.get('evidence')
                            receipt_path = receipt_path if os.path.isabs(receipt_path) else os.path.join(run_dir, receipt_path)
                            receipt, _ = read_json(receipt_path)
                            review_receipts[review_type] = receipt
                review_types = set(review_receipts)
                distinct_reviews = (
                    review_receipts.get('independent', {}).get('reviewer') !=
                    review_receipts.get('security', {}).get('reviewer') and
                    review_receipts.get('independent', {}).get('artifact_sha256') !=
                    review_receipts.get('security', {}).get('artifact_sha256')
                )
                has_source = isinstance(sources, list) and any(
                    isinstance(source, dict) and nonempty_text(source.get('name')) and
                    isinstance(source.get('url'), str) and source['url'].startswith('https://') and
                    nonempty_text(source.get('retrieved')) for source in sources
                )
                return (nonempty_text(risk.get('name')) and risk.get('disposition') == 'proceed' and
                        nonempty_text(risk.get('analysis')) and nonempty_text(risk.get('recovery')) and
                        has_source and isinstance(alternatives, list) and
                        any(nonempty_text(option) for option in alternatives) and
                        isinstance(controls, list) and any(nonempty_text(control) for control in controls) and
                        {'independent', 'security'}.issubset(review_types) and distinct_reviews)

            incomplete_dossiers = [r for r in high_risks if r.get('disposition') != 'blocked' and
                                   not complete_dossier(r)]
            known_risk_names = {r.get('name') for r in high_risks}
            missing_dossiers = [u for u in items('upgrades') if u.get('impact_tier') == 'high' and
                                u.get('name') not in known_risk_names]
            required_skips = [c for c in items('skipped_checks') if c.get('required')]
            held = [u for u in items('upgrades') if u.get('status') in ('held', 'blocked', 'failed')]

            def review_passed(entry):
                return (entry.get('status') == 'completed' and entry.get('verdict') == 'pass' and
                        bool(candidate) and entry.get('candidate_head') == candidate and
                        bound_evidence(run_dir, entry.get('evidence'), journal['run_id'], candidate,
                                       '%s-review' % entry.get('kind'), entry.get('subject'), 'pass'))

            # Routed review entries on disk are authoritative: leaving one out of the report input, or
            # flipping its required flag, cannot hide an unavailable or failed review of this candidate.
            all_reviews = {r.get('id') or id(r): r for r in items('reviews')}
            for entry in run_review_entries(run_dir):
                if entry.get('candidate_head') == candidate:
                    all_reviews[entry.get('id')] = entry
            unmet_reviews = [r for r in all_reviews.values() if r.get('required') and not review_passed(r)]
            failed_reviews = [r for r in all_reviews.values() if r.get('candidate_head') == candidate and
                              r.get('verdict') == 'fail']
            for found, label in ((unmet_reviews, 'required review(s) not completed and passed for this candidate'),
                                 (failed_reviews, 'review(s) of this candidate with unresolved Important findings'),
                                 (blocked_risks, 'blocked high-risk item(s)'),
                                 (incomplete_dossiers, 'high-risk item(s) without complete research, controls and reviews'),
                                 (missing_dossiers, 'high-impact upgrade(s) without a matching complete risk dossier'),
                                 (required_skips, 'skipped required check(s)'),
                                 (held, 'held/blocked/failed upgrade(s)')):
                if found:
                    corrections.append('PASS not allowed with %d %s; downgraded to PARTIAL' % (len(found), label))
                    status = 'PARTIAL'
    report['status'] = status
    report['status_corrections'] = corrections
    return report


def run_review_entries(run_dir):
    base = os.path.join(run_dir, 'reviews')
    entries = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            entry, error = read_json(os.path.join(base, name, 'entry.json'))
            if not error and isinstance(entry, dict):
                entries.append(entry)
    return entries


def render_summary(report):
    lines = ['# Dependency upgrade: %s' % report['status'], '',
             '- Run: `%s` (%s)' % (report['rollback']['run_ref'], report['mode']),
             '- Repo: `%s` @ `%s`' % (report['repo_root'], (report.get('base_commit') or '')[:12])]
    if report.get('branch'):
        lines.append('- Branch: `%s` (worktree `%s`)' % (report['branch'], report.get('worktree')))
    for note in report.get('status_corrections') or []:
        lines.append('- Status corrected: ' + note)
    if report.get('upgrades'):
        lines += ['', '## Upgrades', '', '| Package | From | To | Risk |', '| --- | --- | --- | --- |']
        for u in report['upgrades']:
            if isinstance(u, dict):
                lines.append('| %s | %s | %s | %s |' % (u.get('name'), u.get('from'), u.get('to'), u.get('risk', '')))
    for title, key in (('Migrations', 'migrations'), ('Verification', 'verification'), ('Security', 'security'),
                       ('Risks', 'risks'), ('Skipped checks', 'skipped_checks'),
                       ('Remaining actions', 'remaining_actions'), ('Sources', 'links')):
        items = report.get(key) or []
        if not items:
            continue
        lines += ['', '## ' + title, '']
        for item in items:
            if isinstance(item, dict):
                text = item.get('summary') or item.get('name') or item.get('check') or json.dumps(item)
                extra = item.get('result') or item.get('severity') or item.get('url') or ''
                lines.append('- %s%s' % (text, (' — ' + extra) if extra else ''))
            else:
                lines.append('- %s' % item)
    plans = [p for p in report.get('planning') or [] if isinstance(p, dict)]
    if plans:
        lines += ['', '## Architecture and planning', '']
        for p in plans:
            sel = p.get('selected') or {}
            fallbacks = ['#%s %s/%s: %s (%s)' % (c.get('priority'), c.get('harness'), c.get('model'), c.get('status'),
                                                 c.get('reason')) for c in p.get('candidates') or []
                         if isinstance(c, dict) and c.get('status') not in ('selected', None)]
            how = ('%s/%s@%s' % (sel.get('harness'), sel.get('resolved_model') or sel.get('model'), sel.get('effort'))
                   if sel else p.get('reason'))
            lines.append('- %s %s: %s — %s%s' % (p.get('kind'), p.get('subject'), p.get('status'), how,
                                                 ('; fallbacks: ' + '; '.join(fallbacks)) if fallbacks else ''))
    reviews = [r for r in report.get('reviews') or [] if isinstance(r, dict)]
    if reviews:
        lines += ['', '## Reviews', '']
        for r in reviews:
            sel = r.get('selected') or {}
            fallbacks = ['#%s %s/%s: %s (%s)' % (c.get('priority'), c.get('harness'), c.get('model'), c.get('status'),
                                                 c.get('reason')) for c in r.get('candidates') or []
                         if isinstance(c, dict) and c.get('status') not in ('selected', None)]
            how = ('%s/%s@%s, usage %s' % (sel.get('harness'), sel.get('resolved_model') or sel.get('model'),
                                           sel.get('effort'), sel.get('usage') or 'unknown')) if sel else r.get('reason')
            lines.append('- %s %s%s: %s %s — %s%s' % (r.get('kind'), r.get('subject'), ' (required)' if r.get('required')
                                                     else '', r.get('status'), r.get('verdict') or '', how,
                                                     ('; fallbacks: ' + '; '.join(fallbacks)) if fallbacks else ''))
    delivery = report.get('delivery')
    if isinstance(delivery, dict) and delivery:
        lines += ['', '## Delivery', '']
        for key in ('mode', 'pushed', 'pr', 'checks', 'merged', 'reason'):
            if delivery.get(key) not in (None, ''):
                lines.append('- %s: %s' % (key, delivery[key]))
    lines += ['', 'Rollback: `depup.py rollback %s` (dry run first). The original checkout was not modified.'
              % report['rollback']['run_ref'], '']
    return '\n'.join(lines)


def cmd_report(args):
    run_dir = run_dir_for(args.run)
    journal = load_journal(run_dir)
    supplied = {}
    if args.input:
        text = sys.stdin.read() if args.input == '-' else read_text(args.input)
        try:
            supplied = json.loads(text or '{}')
        except ValueError as exc:
            raise Refusal('report input is not JSON: %s' % exc)
        if not isinstance(supplied, dict):
            raise Refusal('report input must be a JSON object')
    report = redact_output(build_report(run_dir, journal, supplied))
    atomic_write(os.path.join(run_dir, 'report.json'), json.dumps(report, indent=2) + '\n')
    atomic_write(os.path.join(run_dir, 'summary.md'), render_summary(report))
    out = {'status': report['status'], 'corrections': report['status_corrections'],
           'report': os.path.join(run_dir, 'report.json'), 'summary': os.path.join(run_dir, 'summary.md')}
    if args.copy_to:
        out['copied_to'] = copy_evidence(run_dir, args.copy_to, journal['repo_root'])
    return out


def copy_evidence(run_dir, target, repo_root):
    """Copy report, summary and logs to a policy evidence directory (never into the repository)."""
    target = os.path.abspath(os.path.expanduser(target))
    if within(repo_root, target) or within(run_dir, target):
        raise Refusal('evidence copy target must be outside the repository and the run directory')
    os.makedirs(target, exist_ok=True)
    for name in ('report.json', 'summary.md'):
        shutil.copy2(os.path.join(run_dir, name), os.path.join(target, name))
    logs = os.path.join(run_dir, 'logs')
    if os.path.isdir(logs):
        shutil.copytree(logs, os.path.join(target, 'logs'), symlinks=True, dirs_exist_ok=True)
    return target


# --------------------------------------------------------------------- reviews

ROUTER_FACTORY = None  # tests inject a Router factory; None uses review_router.Router


def review_router():
    """Load the sibling routing module by path (never from the repository or the working directory)."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'review_router.py')
    spec = importlib.util.spec_from_file_location('depup_review_router', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review_slug(text):
    readable = re.sub(r'[^a-z0-9]+', '-', str(text).lower()).strip('-')[:32] or 'subject'
    return '%s-%s' % (readable, hashlib.sha256(str(text).encode('utf-8')).hexdigest()[:8])  # exact subject, no collisions


def write_review_inputs(rr, worktree, base, candidate, review_dir):
    """Complete code diff and lockfile diff of base..candidate, redacted, inside the review directory."""
    lock_specs = [':(glob)**/' + name for name in rr.LOCKFILES]
    diffs = {
        'code.diff': ['diff', '--no-color', '--no-ext-diff', base, candidate, '--', '.'] +
                     [':(exclude,glob)**/' + name for name in rr.LOCKFILES],
        'lockfile.diff': ['diff', '--no-color', '--no-ext-diff', base, candidate, '--'] + lock_specs,
        'diffstat.txt': ['diff', '--stat', '--no-color', base, candidate],
    }
    lines = {}
    for name, argv in diffs.items():
        res = git(worktree, *argv, timeout=300)
        if res['rc'] != 0:
            raise Refusal('git %s failed: %s' % (' '.join(argv[:2]), (res['stderr'] or res['error'] or '').strip()))
        atomic_write(os.path.join(review_dir, name), redact_text(res['stdout']))
        lines[name] = res['stdout'].count('\n')
    return {'code_diff': os.path.join(review_dir, 'code.diff'), 'code_lines': lines['code.diff'],
            'lock_diff': os.path.join(review_dir, 'lockfile.diff'), 'lock_lines': lines['lockfile.diff'],
            'stat': os.path.join(review_dir, 'diffstat.txt')}


def review_target(args, journal, rr):
    if args.kind not in rr.KINDS:
        raise Refusal('--kind must be one of ' + ', '.join(rr.KINDS))
    subject = args.subject or ('full-diff' if args.kind == 'critical' else None)
    if not subject:
        raise Refusal('--subject <risk name> is required for independent and security reviews')
    candidate = candidate_head(journal)
    if not candidate:
        raise Refusal('a review needs the owned worktree clean at a committed candidate')
    return subject, candidate, '%s-%s-%s' % (args.kind, review_slug(subject), candidate[:12])


def compact_candidates(evidence):
    keys = ('priority', 'harness', 'model', 'effort', 'status', 'usage', 'reason', 'resets_at', 'resolved_model',
            'checks')
    out = []
    for record in evidence.get('candidates', []):
        item = {k: record.get(k) for k in keys if record.get(k) not in (None, [], '')}
        item['attempts'] = [{k: a.get(k) for k in ('category', 'reason', 'rc', 'seconds', 'usage',
                                                    'models_reported') if a.get(k) not in (None, '')}
                            for a in record.get('attempts', [])]
        out.append(item)
    return out


def complete_review(run_dir, journal, review_dir, meta, text, result, reviewer, selected):
    review_md = os.path.join(review_dir, 'review.md')
    header = '<!-- depup review %s (%s %s) by %s on %s -->\n' % (meta['id'], meta['kind'], meta['subject'], reviewer,
                                                              meta['candidate_head'])
    atomic_write(review_md, header + redact_text(text))  # each review task's artifact is distinct
    receipt = {'run_id': journal['run_id'], 'candidate_head': meta['candidate_head'],
               'kind': meta['kind'] + '-review', 'subject': meta['subject'], 'result': result['verdict'],
               'reviewer': reviewer, 'artifact': os.path.relpath(review_md, run_dir),
               'artifact_sha256': file_sha256(review_md)}
    atomic_write(os.path.join(review_dir, 'receipt.json'), json.dumps(receipt, indent=2) + '\n')
    counts = {s: len([f for f in result['findings'] if f['severity'] == s]) for s in ('Important', 'Nit', 'Pre-existing')}
    meta.update(status='completed', verdict=result['verdict'], summary=result.get('summary'),
                finding_counts=counts, findings=result['findings'], result_corrections=result.get('corrections'),
                selected=selected, reviewer=reviewer,
                evidence=os.path.relpath(os.path.join(review_dir, 'receipt.json'), run_dir), completed=now())
    return meta


def cmd_review_run(args):
    rr = review_router()
    run_dir = run_dir_for(args.run)
    journal = load_journal(run_dir)
    subject, candidate, review_id = review_target(args, journal, rr)
    reviews_dir = os.path.join(run_dir, 'reviews')
    review_dir = os.path.join(reviews_dir, review_id)
    entry_path = os.path.join(review_dir, 'entry.json')
    existing, _ = read_json(entry_path)
    if isinstance(existing, dict) and existing.get('status') == 'completed' and not args.rerun:
        return dict(existing, reused=True, note='completed review for this exact candidate reused; no model call')
    lock = os.path.join(review_dir, 'review.lock')
    held, _ = read_json(lock)
    if isinstance(held, dict) and not pid_alive(held.get('pid')):
        try:
            os.unlink(lock)  # its holder died (killed by a tool timeout): reclaim instead of blocking forever
        except FileNotFoundError:
            pass
    try:
        exclusive_create(lock, json.dumps({'pid': os.getpid(), 'at': now()}))
    except FileExistsError:
        raise Refusal('review %s is already running (lock %s); never start a duplicate review' % (review_id, lock))
    previous = signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # run `finally`, stop the reviewer
    try:
        files = write_review_inputs(rr, journal['worktree'], journal.get('worktree_base') or journal['base_commit'],
                                    candidate, review_dir)
        context = None
        if args.context:
            context = os.path.join(review_dir, 'context' + (os.path.splitext(args.context)[1] or '.txt'))
            atomic_write(context, redact_text(read_text(args.context) or ''))
        prompt = rr.build_prompt(args.kind, subject, journal['worktree'],
                                 journal.get('worktree_base') or journal['base_commit'], candidate, files, context)
        atomic_write(os.path.join(review_dir, 'prompt.md'), prompt)
        factory = ROUTER_FACTORY or rr.Router
        router = factory(timeout=args.timeout, max_retries=args.max_retries)
        ctx = {'review_id': review_id, 'review_dir': review_dir, 'reviews_dir': reviews_dir,
               'worktree': journal['worktree'], 'prompt': prompt, 'redact': redact_text}
        session = {'harness': args.session_harness, 'model': args.session_model, 'effort': args.session_effort}
        try:
            evidence, selected, done = router.route(ctx, session)
        except RuntimeError as exc:
            raise Refusal(str(exc))
        meta = {'id': review_id, 'kind': args.kind, 'subject': subject, 'required': bool(args.required),
                'candidate_head': candidate, 'routing': os.path.relpath(os.path.join(review_dir, 'routing.json'), run_dir),
                'candidates': compact_candidates(evidence)}
        if done and candidate_head(journal) != candidate:
            done = None
            meta['reason'] = 'the owned worktree changed during the review; result discarded'
        if done:
            text, result, outcome, attempt_dir, probe = done
            sel = {'priority': selected['priority'], 'harness': selected['harness'], 'model': selected['model'],
                   'effort': selected.get('effort') or 'session-default', 'resolved_model': probe.get('resolved_model'),
                   'usage': next((c.get('usage') for c in meta['candidates'] if c.get('status') == 'selected'), None)}
            reviewer = '%s/%s@%s fresh session %s/%s' % (sel['harness'], sel['resolved_model'] or sel['model'],
                                                         sel['effort'], review_id, os.path.basename(attempt_dir))
            meta = complete_review(run_dir, journal, review_dir, meta, text, result, reviewer, sel)
        elif evidence.get('outcome') == 'handoff':
            output = os.path.join(review_dir, 'handoff-output.md')
            meta.update(status='handoff', next=(
                'Run %s in a fresh-context, read-only subagent of this session (same model and effort; never the '
                'authoring conversation). Save its full answer to %s, then run: depup.py review record %s '
                '--review-id %s --output %s --reviewer "<harness>/<model>@<effort>"'
                % (os.path.join(review_dir, 'prompt.md'), output, args.run, review_id, output)))
        else:
            meta.update(status='unavailable', reason=meta.get('reason') or
                        'no reviewer could complete this review (all candidates and the session fallback failed)')
        evidence.update(finished=now(), entry_status=meta['status'])
        atomic_write(os.path.join(review_dir, 'routing.json'), json.dumps(redact_output(evidence), indent=2) + '\n')
        atomic_write(entry_path, json.dumps(redact_output(meta), indent=2) + '\n')
        return dict(meta, entry=entry_path)
    finally:
        signal.signal(signal.SIGTERM, previous)
        try:
            os.unlink(lock)
        except OSError:
            pass


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists but not ours


def cmd_review_record(args):
    """Bind a review the invoking session ran itself (priority-4 handoff) to the candidate."""
    rr = review_router()
    run_dir = run_dir_for(args.run)
    journal = load_journal(run_dir)
    if not SAFE_RUN_ID.match(args.review_id or ''):
        raise Refusal('invalid review id')
    review_dir = os.path.join(run_dir, 'reviews', args.review_id)
    entry_path = os.path.join(review_dir, 'entry.json')
    meta, error = read_json(entry_path)
    if error or not isinstance(meta, dict) or meta.get('status') != 'handoff':
        raise Refusal('review %s is not waiting for a session handoff' % args.review_id)
    if candidate_head(journal) != meta.get('candidate_head'):
        raise Refusal('the candidate changed since the review was prepared; run the review again')
    if not (args.reviewer or '').strip():
        raise Refusal('--reviewer must name the session harness/model@effort')
    result, problem = rr.parse_result(read_text(args.output) or '')
    if result is None:
        meta.update(status='unavailable', reason='session review output invalid: %s' % problem)
    else:
        sel = {'priority': 4, 'harness': 'session-native', 'model': args.reviewer.strip(), 'effort': 'session',
               'usage': 'unknown'}
        meta = complete_review(run_dir, journal, review_dir, meta, read_text(args.output), result,
                               '%s session-subagent %s' % (args.reviewer.strip(), args.review_id), sel)
    meta.pop('next', None)
    atomic_write(entry_path, json.dumps(redact_output(meta), indent=2) + '\n')
    return dict(meta, entry=entry_path)


def cmd_review_probe(args):
    """Availability of the preferred reviewers without any model call."""
    rr = review_router()
    reviews_dir = os.path.join(run_dir_for(args.run), 'reviews') if args.run else os.path.join(state_root(), '.probe')
    router = (ROUTER_FACTORY or rr.Router)()
    out = []
    for candidate in rr.PREFERRED:
        probe = router.probe(candidate, reviews_dir, 'probe')
        out.append({'priority': candidate['priority'], 'harness': candidate['harness'], 'model': candidate['model'],
                    'effort': candidate['effort'], 'status': probe['status'], 'usage': probe['usage'],
                    'reason': probe['reason'], 'checks': probe['checks']})
    selected = next((c for c in out if c['status'] in (rr.AVAILABLE, rr.UNKNOWN)), None)
    return {'candidates': out, 'would_try_first': selected and '%s/%s@%s' % (
        selected['harness'], selected['model'], selected['effort']), 'model_calls': 0}


# --------------------------------------------------------------------- planning
# Architecture and planning tasks reuse the review router unchanged (priority, probes, fallback,
# run-wide provider state in <run_dir>/reviews/provider-state.json); only prompt and parser differ.
# Planners are read-only and advisory: the main session implements and stays bound by this skill.

def planning_target(journal):
    """The tree a planner reads: the owned worktree once it exists, else the repository (plan mode)."""
    worktree = journal.get('worktree')
    return worktree if worktree and os.path.isdir(worktree) else journal['repo_root']


def tree_fingerprint(root):
    """HEAD + status + tracked diff: proves a planner left the tree it read unchanged."""
    digest = hashlib.sha256()
    for argv in (('rev-parse', 'HEAD'), ('status', '--porcelain=v1', '-z', '--untracked-files=all'),
                 ('diff', '--no-color', '--no-ext-diff', '--binary', 'HEAD')):
        res = git(root, *argv, timeout=300)
        digest.update(('%s\0%s\0' % (res['rc'], res['stdout'])).encode('utf-8', 'replace'))
    return digest.hexdigest()


def planning_entries(run_dir):
    base = os.path.join(run_dir, 'plans')
    entries = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            entry, error = read_json(os.path.join(base, name, 'entry.json'))
            if not error and isinstance(entry, dict):
                entries.append(entry)
    return entries


def complete_plan(run_dir, journal, plan_dir, meta, text, result, planner, selected):
    plan_md = os.path.join(plan_dir, 'plan.md')
    atomic_write(plan_md, '<!-- depup %s %s (%s) by %s on %s -->\n' % (
        meta['kind'], meta['id'], meta['subject'], planner, meta['head']) + redact_text(text))
    result_path = os.path.join(plan_dir, 'plan.json')
    atomic_write(result_path, json.dumps(redact_output(result), indent=2) + '\n')
    receipt = {'run_id': journal['run_id'], 'head': meta['head'], 'kind': meta['kind'] + '-plan',
               'subject': meta['subject'], 'context_sha256': meta['context_sha256'], 'planner': planner,
               'artifact': os.path.relpath(plan_md, run_dir), 'artifact_sha256': file_sha256(plan_md)}
    atomic_write(os.path.join(plan_dir, 'receipt.json'), json.dumps(receipt, indent=2) + '\n')
    meta.update(status='completed', summary=result['summary'], planner=planner, selected=selected,
                counts={k: len(result.get(k) or []) for k in ('decisions', 'steps', 'risks', 'holds', 'open_questions')},
                result=os.path.relpath(result_path, run_dir), plan=result,
                evidence=os.path.relpath(os.path.join(plan_dir, 'receipt.json'), run_dir), completed=now())
    return meta


def cmd_planning_run(args):
    rr = review_router()
    run_dir = run_dir_for(args.run)
    journal = load_journal(run_dir)
    if args.kind not in rr.PLAN_KINDS:
        raise Refusal('--kind must be one of ' + ', '.join(rr.PLAN_KINDS))
    subject = args.subject or ('upgrade' if args.kind == 'plan' else None)
    if not subject:
        raise Refusal('--subject <risk name> is required for an architecture task')
    handoff = read_text(args.context)
    if not (handoff or '').strip():
        raise Refusal('--context must name a non-empty handoff file (requirements, scope, constraints, findings)')
    handoff = redact_text(handoff)
    target = planning_target(journal)
    head = git_state(target).get('head') or 'none'
    context_sha = hashlib.sha256(handoff.encode('utf-8')).hexdigest()
    before = tree_fingerprint(target)  # the exact tree read (incl. uncommitted work) is part of the reuse key
    key = hashlib.sha256(('%s\0%s\0%s\0%s\0%s' % (args.kind, subject, target, before, context_sha)
                          ).encode('utf-8')).hexdigest()
    plan_id = '%s-%s-%s' % (args.kind, review_slug(subject), key[:12])
    plan_dir = os.path.join(run_dir, 'plans', plan_id)
    entry_path = os.path.join(plan_dir, 'entry.json')
    existing, _ = read_json(entry_path)
    if isinstance(existing, dict) and existing.get('status') == 'completed' and not args.rerun:
        return dict(existing, reused=True, note='completed plan for this exact head and handoff reused; no model call')
    lock = os.path.join(plan_dir, 'plan.lock')
    held, _ = read_json(lock)
    if isinstance(held, dict) and not pid_alive(held.get('pid')):
        try:
            os.unlink(lock)
        except FileNotFoundError:
            pass
    try:
        exclusive_create(lock, json.dumps({'pid': os.getpid(), 'at': now()}))
    except FileExistsError:
        raise Refusal('planning task %s is already running (lock %s); never start a duplicate' % (plan_id, lock))
    previous = signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        context = os.path.join(plan_dir, 'context' + (os.path.splitext(args.context)[1] or '.txt'))
        atomic_write(context, handoff)
        note = '' if target == journal.get('worktree') else (
            ' (the user checkout; it may hold uncommitted work that the upgrade excludes, and the upgrade '
            'itself starts from the freshly fetched upstream default branch)')
        prompt = rr.build_plan_prompt(args.kind, subject, target, head, context, note)
        atomic_write(os.path.join(plan_dir, 'prompt.md'), prompt)
        router = (ROUTER_FACTORY or rr.Router)(timeout=args.timeout, max_retries=args.max_retries)
        ctx = {'review_id': plan_id, 'review_dir': plan_dir, 'reviews_dir': os.path.join(run_dir, 'reviews'),
               'worktree': target, 'prompt': prompt, 'redact': redact_text,
               'parse': lambda text: rr.parse_plan_result(text, args.kind)}
        session = {'harness': args.session_harness, 'model': args.session_model, 'effort': args.session_effort}
        try:
            evidence, selected, done = router.route(ctx, session)
        except RuntimeError as exc:
            raise Refusal(str(exc))
        meta = {'id': plan_id, 'kind': args.kind, 'subject': subject, 'head': head, 'target': target,
                'context_sha256': context_sha,
                'routing': os.path.relpath(os.path.join(plan_dir, 'routing.json'), run_dir),
                'candidates': compact_candidates(evidence)}
        if done and tree_fingerprint(target) != before:
            done = None
            meta.update(tree_changed=True, reason='the repository tree changed during planning (by the planner or '
                        'another writer); result discarded. Inspect %s before relying on it' % target)
        if done:
            text, result, outcome, attempt_dir, probe = done
            sel = {'priority': selected['priority'], 'harness': selected['harness'], 'model': selected['model'],
                   'effort': selected.get('effort') or 'session-default', 'resolved_model': probe.get('resolved_model'),
                   'usage': next((c.get('usage') for c in meta['candidates'] if c.get('status') == 'selected'), None)}
            planner = '%s/%s@%s fresh session %s/%s' % (sel['harness'], sel['resolved_model'] or sel['model'],
                                                        sel['effort'], plan_id, os.path.basename(attempt_dir))
            meta = complete_plan(run_dir, journal, plan_dir, meta, text, result, planner, sel)
        elif evidence.get('outcome') == 'handoff':
            output = os.path.join(plan_dir, 'handoff-output.md')
            meta.update(status='handoff', reason='no routed planner completed; waiting for a session-native plan',
                        next=(
                'Run %s in a fresh-context, read-only subagent of this session (same model and effort; never the '
                'authoring conversation). Save its full answer to %s, then run: depup.py planning record %s '
                '--plan-id %s --output %s --planner "<harness>/<model>@<effort>"'
                % (os.path.join(plan_dir, 'prompt.md'), output, args.run, plan_id, output)))
        else:
            meta.update(status='unavailable', reason=meta.get('reason') or
                        'no planner could complete this task (all candidates and the session fallback failed)')
        evidence.update(finished=now(), entry_status=meta['status'])
        atomic_write(os.path.join(plan_dir, 'routing.json'), json.dumps(redact_output(evidence), indent=2) + '\n')
        atomic_write(entry_path, json.dumps(redact_output(meta), indent=2) + '\n')
        return dict(meta, entry=entry_path)
    finally:
        signal.signal(signal.SIGTERM, previous)
        try:
            os.unlink(lock)
        except OSError:
            pass


def cmd_planning_record(args):
    """Bind a plan the invoking session produced in a fresh native subagent (priority-4 handoff)."""
    rr = review_router()
    run_dir = run_dir_for(args.run)
    journal = load_journal(run_dir)
    if not SAFE_RUN_ID.match(args.plan_id or ''):
        raise Refusal('invalid plan id')
    plan_dir = os.path.join(run_dir, 'plans', args.plan_id)
    entry_path = os.path.join(plan_dir, 'entry.json')
    meta, error = read_json(entry_path)
    if error or not isinstance(meta, dict) or meta.get('status') != 'handoff':
        raise Refusal('planning task %s is not waiting for a session handoff' % args.plan_id)
    if (git_state(planning_target(journal)).get('head') or 'none') != meta.get('head'):
        raise Refusal('the repository head changed since the task was prepared; run planning again')
    if not (args.planner or '').strip():
        raise Refusal('--planner must name the session harness/model@effort')
    text = read_text(args.output) or ''
    result, problem = rr.parse_plan_result(text, meta.get('kind', 'plan'))
    if result is None:
        meta.update(status='unavailable', reason='session plan output invalid: %s' % problem)
    else:
        sel = {'priority': 4, 'harness': 'session-native', 'model': args.planner.strip(), 'effort': 'session',
               'usage': 'unknown'}
        meta = complete_plan(run_dir, journal, plan_dir, meta, text, result,
                             '%s session-subagent %s' % (args.planner.strip(), args.plan_id), sel)
    meta.pop('next', None)
    atomic_write(entry_path, json.dumps(redact_output(meta), indent=2) + '\n')
    return dict(meta, entry=entry_path)


# ---------------------------------------------------------------- capabilities

def capabilities():
    tools = {}
    for name in ('git', 'node', 'npm', 'pnpm', 'yarn', 'bun', 'deno', 'cargo', 'rustc', 'go', 'uv', 'python3',
                 'pip3', 'poetry', 'mvn', 'gradle', 'ruby', 'bundle', 'composer', 'php', 'dotnet', 'swift'):
        tools[name] = tool_version(name, ('version',) if name in ('go',) else ('--version',))
    return {'platform': sys.platform, 'python': sys.version.split()[0], 'tools': tools,
            'state_root': state_root(),
            'note': 'Harness capabilities (subagents, browser, network policy, write access) are declared by the '
                    'agent; this script only sees local executables.'}


def check_network(host='registry.npmjs.org', port=443, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def cmd_preflight(args):
    root, is_git = resolve_repo(args.repo)
    out = {'root': root, 'is_git': is_git, 'writable': os.access(root, os.W_OK),
           'state_root': state_root(), 'git': git_state(root) if is_git else None}
    inv = inventory(root)
    out['inventory_summary'] = inv['summary']
    out['warnings'] = inv['warnings'][:50]
    if args.check_network:
        out['network'] = {'registry.npmjs.org:443': check_network()}
    blockers = []
    if is_git and out['git']['operation_in_progress']:
        blockers.append('git operation in progress: ' + ', '.join(out['git']['operation_in_progress']))
    if not is_git:
        blockers.append('not a Git repository: upgrade mode unavailable (plan only)')
    out['upgrade_blockers'] = blockers
    if is_git and not out['git']['clean']:
        out['dirty_policy'] = ('user checkout is dirty: leave it untouched; upgrades run in an owned worktree from '
                               'HEAD, so uncommitted changes are NOT included and must be reported')
    out['active_runs'] = [dict(r, holds_lock=holds_repo_lock(root, r.get('run_id')))
                          for r in list_runs(root) if r.get('status') == 'active']
    if is_git:
        upstream = upstream_default(root)
        out['delivery_facts'] = {'remote': upstream.get('remote'), 'default_branch': upstream.get('branch'),
                                 'gh_available': bool(shutil.which('gh-axi') or shutil.which('gh'))}
        out['local_only_config'] = ignored_local_config(root)
    return out


LOCAL_CONFIG_NAMES = ('.env', '.env.local', '.env.development', '.env.development.local', '.env.test',
                      '.env.test.local', '.dev.vars', '.npmrc', '.yarnrc.yml', 'local.properties')


def ignored_local_config(root):
    """Names (never contents) of Git-ignored local config files the owned worktree will not contain."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]
        if os.path.relpath(dirpath, root).count(os.sep) > 3:
            dirnames[:] = []
        for name in filenames:
            if name in LOCAL_CONFIG_NAMES:
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                if git(root, 'check-ignore', '-q', '--', rel)['rc'] == 0:
                    found.append(rel)
    return sorted(found)


# ------------------------------------------------------------------------- main

def build_parser():
    parser = argparse.ArgumentParser(prog='depup.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command')
    p = sub.add_parser('preflight')
    p.add_argument('--repo')
    p.add_argument('--check-network', action='store_true')
    p = sub.add_parser('inventory')
    p.add_argument('--repo')
    p = sub.add_parser('outdated')
    p.add_argument('--repo')
    p.add_argument('--timeout', type=int, default=180)
    p.add_argument('--classify', action='store_true', help='also classify the records')
    p = sub.add_parser('classify')
    p.add_argument('--input', required=True, help='JSON file with a list of records or {"records": [...]}; - for stdin')
    sub.add_parser('capabilities')

    run = sub.add_parser('run')
    rsub = run.add_subparsers(dest='run_command')
    p = rsub.add_parser('start')
    p.add_argument('--repo')
    p.add_argument('--mode', choices=('plan', 'upgrade'), default='plan')
    p.add_argument('--run-id')
    p = rsub.add_parser('step')
    p.add_argument('run')
    p.add_argument('--id', required=True)
    p.add_argument('--status', required=True)
    p.add_argument('--title')
    p.add_argument('--dangerous', action='store_true')
    p.add_argument('--evidence', action='append')
    p.add_argument('--note')
    p.add_argument('--force', action='store_true')
    p = rsub.add_parser('status')
    p.add_argument('run', nargs='?')
    p.add_argument('--repo')
    p = rsub.add_parser('list')
    p.add_argument('--repo')
    p = rsub.add_parser('worktree')
    p.add_argument('run')
    p.add_argument('--base')
    p.add_argument('--isolation', choices=('worktree', 'clone'), default='worktree')
    p.add_argument('--base-source', choices=('upstream', 'head'), default='upstream',
                   help='start from the freshly fetched upstream default branch (default) or the checkout HEAD')
    p = rsub.add_parser('worker')
    p.add_argument('run')
    p.add_argument('--domain', required=True)
    p.add_argument('--base')
    p = rsub.add_parser('finish')
    p.add_argument('run')
    p.add_argument('--status', choices=('finished', 'blocked', 'abandoned'))

    p = sub.add_parser('rollback')
    p.add_argument('run')
    p.add_argument('--apply', action='store_true')
    p = sub.add_parser('report')
    p.add_argument('run')
    p.add_argument('--input')
    p.add_argument('--copy-to', help='also copy report, summary and logs to this evidence directory')

    review = sub.add_parser('review')
    vsub = review.add_subparsers(dest='review_command')
    p = vsub.add_parser('run', help='one review task on one routed model (Opus 5.5 > DeepSeek > session)')
    p.add_argument('run')
    p.add_argument('--kind', required=True, help='critical | independent | security')
    p.add_argument('--subject', help='risk name (independent/security); critical defaults to full-diff')
    p.add_argument('--required', action='store_true', help='PASS is impossible unless this review completes and passes')
    p.add_argument('--context', help='plan or risk dossier file given to the reviewer as data')
    p.add_argument('--session-harness', help='invoking harness (claude-code, opencode, pi, ...)')
    p.add_argument('--session-model', help='invoking session model id (final fallback)')
    p.add_argument('--session-effort', help='invoking session effort (final fallback)')
    p.add_argument('--timeout', type=int, default=1800, help='seconds per reviewer attempt')
    p.add_argument('--max-retries', type=int, default=1, help='retries for transient service errors only')
    p.add_argument('--rerun', action='store_true', help='repeat a completed review of the same candidate')
    p = vsub.add_parser('record', help='bind a session-native (handoff) review result')
    p.add_argument('run')
    p.add_argument('--review-id', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--reviewer', required=True)
    p = vsub.add_parser('probe', help='reviewer availability without model calls')
    p.add_argument('--run')

    planning = sub.add_parser('planning')
    psub = planning.add_subparsers(dest='planning_command')
    p = psub.add_parser('run', help='architecture/planning on the review router\'s models (read-only, advisory)')
    p.add_argument('run')
    p.add_argument('--kind', default='plan', help='plan (whole upgrade, default) | architecture (one high-risk item)')
    p.add_argument('--subject', help='risk name (architecture); plan defaults to upgrade')
    p.add_argument('--context', required=True, help='handoff file: requirements, scope, constraints, findings')
    p.add_argument('--session-harness', help='invoking harness (claude-code, opencode, pi, ...)')
    p.add_argument('--session-model', help='invoking session model id (final fallback)')
    p.add_argument('--session-effort', help='invoking session effort (final fallback)')
    p.add_argument('--timeout', type=int, default=1800, help='seconds per planner attempt')
    p.add_argument('--max-retries', type=int, default=1, help='retries for transient service errors only')
    p.add_argument('--rerun', action='store_true', help='repeat a completed task with the same head and handoff')
    p = psub.add_parser('record', help='bind a session-native (handoff) plan result')
    p.add_argument('run')
    p.add_argument('--plan-id', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--planner', required=True)
    return parser


def dispatch(args):
    if args.command == 'preflight':
        return cmd_preflight(args)
    if args.command == 'inventory':
        return inventory(resolve_repo(args.repo)[0])
    if args.command == 'outdated':
        result = outdated(resolve_repo(args.repo)[0], args.timeout)
        if args.classify:
            result['classified'] = classify(result['records'])
        return result
    if args.command == 'classify':
        text = sys.stdin.read() if args.input == '-' else read_text(args.input)
        try:
            data = json.loads(text or '[]')
        except ValueError as exc:
            raise Refusal('input is not JSON: %s' % exc)
        records = data.get('records', []) if isinstance(data, dict) else data
        return classify(records)
    if args.command == 'capabilities':
        return capabilities()
    if args.command == 'run':
        handler = {'start': cmd_run_start, 'step': cmd_run_step, 'status': cmd_run_status,
                   'worktree': cmd_run_worktree, 'worker': cmd_run_worker, 'finish': cmd_run_finish}.get(args.run_command)
        if args.run_command == 'list':
            return {'runs': list_runs(resolve_repo(args.repo)[0])}
        if handler is None:
            raise SystemExit(2)
        return handler(args)
    if args.command == 'rollback':
        return cmd_rollback(args)
    if args.command == 'report':
        return cmd_report(args)
    if args.command == 'review':
        handler = {'run': cmd_review_run, 'record': cmd_review_record,
                   'probe': cmd_review_probe}.get(args.review_command)
        if handler is None:
            raise SystemExit(2)
        return handler(args)
    if args.command == 'planning':
        handler = {'run': cmd_planning_run, 'record': cmd_planning_record}.get(args.planning_command)
        if handler is None:
            raise SystemExit(2)
        return handler(args)
    raise SystemExit(2)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    try:
        emit(dispatch(args))
        return 0
    except Refusal as exc:
        emit({'error': str(exc), 'refused': True})
        return 1


if __name__ == '__main__':
    sys.exit(main())
