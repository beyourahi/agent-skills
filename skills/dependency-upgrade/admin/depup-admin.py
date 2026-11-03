#!/usr/bin/env python3
"""Installer maintenance for the dependency-upgrade skill (not a project command).

  depup-admin.py doctor    [--home H] [--run-tests] [--json]
  depup-admin.py install   [--home H] [--no-reconcile]
  depup-admin.py update    [--home H] [--no-reconcile]   (= install + doctor; idempotent)
  depup-admin.py uninstall [--home H] [--no-reconcile] [--remove-canonical]

The canonical skill lives in <home>/.agents/skills/dependency-upgrade. Codex, OpenCode,
and Pi discover that root natively. Claude Code reads only <home>/.claude/skills,
so install adds one relative per-skill symlink there (the machine's existing convention).
Only links recorded in the ownership manifest are ever removed. Standard library only.

Exit codes: 0 ok, 1 doctor failure, 2 usage, 3 refused (collision / foreign state).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

NAME = 'dependency-upgrade'
SCHEMA = 1
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
DEFAULT_ROOTS = {
    # Verified against vendor docs and installed binaries on 2026-11-02.
    'claude': ['~/.claude/skills'],
    'codex': ['~/.agents/skills', '~/.codex/skills'],
    'opencode': ['~/.config/opencode/skills', '~/.claude/skills', '~/.agents/skills'],
    'pi': ['~/.pi/agent/skills', '~/.agents/skills'],
}
HARNESS_BINARIES = {'claude': 'claude', 'codex': 'codex', 'opencode': 'opencode', 'pi': 'pi'}


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Paths:
    def __init__(self, home):
        self.home = os.path.abspath(os.path.expanduser(home))
        self.canonical = os.path.join(self.home, '.agents', 'skills', NAME)
        self.claude_link = os.path.join(self.home, '.claude', 'skills', NAME)
        self.claude_target = os.path.join('..', '..', '.agents', 'skills', NAME)
        self.state = os.path.join(self.home, '.local', 'state', NAME)
        self.manifest = os.path.join(self.state, 'install-manifest.json')
        self.integrations = os.path.join(self.home, '.agents', 'integrations.json')

    def expand(self, value):
        return os.path.join(self.home, value[2:]) if value.startswith('~/') else value


# ------------------------------------------------------------------ frontmatter

def parse_frontmatter(text):
    """Minimal YAML subset: top-level scalars and one-level string maps."""
    if not text.startswith('---\n'):
        raise ValueError('SKILL.md must start with a --- frontmatter line')
    end = text.find('\n---\n', 4)
    if end < 0:
        raise ValueError('frontmatter is not closed')
    data, current = {}, None
    for number, line in enumerate(text[4:end].splitlines(), 2):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        nested = re.match(r'^\s{2,}([A-Za-z0-9_.-]+):\s*(.*)$', line)
        if nested and current is not None:
            data[current][nested.group(1)] = unquote(nested.group(2))
            continue
        top = re.match(r'^([A-Za-z0-9_-]+):\s*(.*)$', line)
        if not top:
            raise ValueError('line %d is not "key: value"' % number)
        key, value = top.group(1), top.group(2)
        if value == '':
            data[key], current = {}, key
        else:
            data[key], current = unquote(value), None
    return data, text[end + 5:]


def unquote(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
        return value[1:-1]
    if value in ('true', 'false'):
        return value == 'true'
    return value


NAME_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')


def validate_skill(skill_dir):
    """Return a list of problems (empty = valid) per the Agent Skills specification."""
    problems = []
    path = os.path.join(skill_dir, 'SKILL.md')
    try:
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
    except OSError as exc:
        return ['cannot read SKILL.md: %s' % exc]
    try:
        meta, body = parse_frontmatter(text)
    except ValueError as exc:
        return [str(exc)]
    name = meta.get('name')
    if not isinstance(name, str) or not (1 <= len(name) <= 64) or not NAME_RE.match(name):
        problems.append('name must be 1-64 chars of a-z0-9 and single hyphens')
    if name != os.path.basename(os.path.realpath(skill_dir)):
        problems.append('name %r must match directory %r' % (name, os.path.basename(os.path.realpath(skill_dir))))
    desc = meta.get('description')
    if not isinstance(desc, str) or not (1 <= len(desc) <= 1024):
        problems.append('description must be 1-1024 characters')
    comp = meta.get('compatibility')
    if comp is not None and (not isinstance(comp, str) or not (1 <= len(comp) <= 500)):
        problems.append('compatibility must be 1-500 characters')
    md = meta.get('metadata')
    if md is not None and (not isinstance(md, dict) or not all(isinstance(v, str) for v in md.values())):
        problems.append('metadata must be a map of string to string')
    if len(body.splitlines()) > 500:
        problems.append('SKILL.md body exceeds 500 lines')
    for target in re.findall(r'\]\(([^)#\s]+)\)', body):
        if re.match(r'^[a-z]+://', target):
            continue
        resolved = os.path.realpath(os.path.join(skill_dir, target))
        if not resolved.startswith(os.path.realpath(skill_dir) + os.sep):
            problems.append('reference escapes the skill directory: %s' % target)
        elif not os.path.exists(resolved):
            problems.append('broken reference: %s' % target)
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [d for d in dirnames if d not in ('__pycache__',)]
        for entry in dirnames + filenames:
            full = os.path.join(dirpath, entry)
            if os.path.islink(full):
                problems.append('symlink inside skill (not allowed): %s' % os.path.relpath(full, skill_dir))
    return problems


# --------------------------------------------------------------------- manifest

def load_manifest(paths):
    try:
        with open(paths.manifest, encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def save_manifest(paths, data):
    os.makedirs(paths.state, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=paths.state, prefix='.manifest-')
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2)
        handle.write('\n')
    os.replace(tmp, paths.manifest)


def link_points_to(link, expected_abs):
    return os.path.islink(link) and os.path.realpath(link) == os.path.realpath(expected_abs)


def reconcile(paths, enabled):
    """Refresh the machine's universal-skills catalogue when present (real HOME only)."""
    if not enabled:
        return {'ran': False, 'reason': 'disabled'}
    if os.path.realpath(paths.home) != os.path.realpath(os.path.expanduser('~')):
        return {'ran': False, 'reason': 'non-default home (test)'}
    tool = shutil.which('universal-skills') or os.path.join(paths.home, '.local', 'bin', 'universal-skills')
    if not os.path.exists(tool):
        return {'ran': False, 'reason': 'universal-skills not installed'}
    results = {}
    for sub in ('reconcile', 'check'):
        try:
            proc = subprocess.run([tool, sub], capture_output=True, text=True, timeout=300)
            results[sub] = {'rc': proc.returncode, 'tail': (proc.stdout or proc.stderr).strip()[-400:]}
        except (OSError, subprocess.TimeoutExpired) as exc:
            results[sub] = {'rc': None, 'tail': str(exc)}
    return {'ran': True, 'results': results}


# ---------------------------------------------------------------------- actions

def cmd_install(paths, args, quiet=False):
    problems = validate_skill(paths.canonical) if os.path.isdir(paths.canonical) else ['canonical skill missing: ' + paths.canonical]
    if problems:
        print('refused: canonical skill invalid:\n  - ' + '\n  - '.join(problems))
        return 3
    manifest = load_manifest(paths) or {'schema': SCHEMA, 'skill': NAME, 'canonical': paths.canonical,
                                        'owned_links': [], 'preexisting_links': [], 'history': []}
    owned_paths = {l['path'] for l in manifest['owned_links']}
    actions = []
    link = paths.claude_link
    if os.path.lexists(link):
        if link_points_to(link, paths.canonical):
            if link not in owned_paths and link not in manifest['preexisting_links']:
                manifest['preexisting_links'].append(link)
            actions.append('claude link present (ok)')
        else:
            print('refused: %s exists and is not a link to %s; not touching it' % (link, paths.canonical))
            return 3
    else:
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(paths.claude_target, link)
        manifest['owned_links'].append({'path': link, 'target': paths.claude_target, 'created': now()})
        actions.append('created claude link')
    manifest['reconcile'] = reconcile(paths, not args.no_reconcile)
    manifest['history'].append({'at': now(), 'action': 'install', 'actions': actions})
    manifest['history'] = manifest['history'][-20:]
    save_manifest(paths, manifest)
    if not quiet:
        print('install: ' + '; '.join(actions) + '; manifest ' + paths.manifest)
        if manifest['reconcile'].get('ran'):
            rc = {k: v['rc'] for k, v in manifest['reconcile']['results'].items()}
            print('universal-skills: %s' % rc)
    return 0


def cmd_uninstall(paths, args):
    manifest = load_manifest(paths)
    removed, kept = [], []
    if manifest:
        for entry in manifest.get('owned_links', []):
            link = entry['path']
            if os.path.islink(link) and os.readlink(link) == entry['target']:
                os.unlink(link)
                removed.append(link)
            elif os.path.lexists(link):
                kept.append('%s (changed since install; left alone)' % link)
        for link in manifest.get('preexisting_links', []):
            kept.append('%s (pre-existing, not owned)' % link)
    else:
        print('no install manifest; nothing owned to remove')
    if args.remove_canonical:
        trash = shutil.which('trash') or ('/usr/bin/trash' if os.path.exists('/usr/bin/trash') else None)
        if not trash:
            print('refused: no reversible trash command; remove %s by hand if intended' % paths.canonical)
            return 3
        proc = subprocess.run([trash, paths.canonical], check=False, capture_output=True, text=True)
        if proc.returncode != 0 or os.path.exists(paths.canonical):
            print('canonical NOT removed (trash rc=%s): %s' % (proc.returncode, (proc.stderr or '').strip()[:200]))
            return 1
        removed.append(paths.canonical + ' (moved to Trash)')
    if os.path.exists(paths.manifest):
        os.unlink(paths.manifest)
    rec = reconcile(paths, not args.no_reconcile)
    print('uninstall: removed %s; kept %s; universal-skills ran=%s'
          % (removed or 'nothing', kept or 'nothing', rec.get('ran')))
    return 0


def harness_roots(paths):
    roots = {h: list(r) for h, r in DEFAULT_ROOTS.items()}
    try:
        with open(paths.integrations, encoding='utf-8') as handle:
            data = json.load(handle)
        for harness, host in (data.get('harnesses') or {}).items():
            declared = list(host.get('skill_roots') or []) + list(host.get('extra_skill_roots') or [])
            if declared:
                merged = roots.get(harness, []) + declared
                roots[harness] = [r for i, r in enumerate(merged) if r not in merged[:i]]
    except (OSError, ValueError):
        pass
    return roots


def find_definitions(root):
    """Every directory in root that defines a skill named NAME (by dir or frontmatter)."""
    hits = []
    if not os.path.isdir(root):
        return hits
    for entry in sorted(os.listdir(root)):
        skill_md = os.path.join(root, entry, 'SKILL.md')
        if not os.path.isfile(skill_md):
            continue
        if entry == NAME:
            hits.append(os.path.join(root, entry))
            continue
        try:
            with open(skill_md, encoding='utf-8', errors='replace') as handle:
                head = handle.read(600)
        except OSError:
            continue
        if re.search(r'^name:\s*["\']?%s["\']?\s*$' % re.escape(NAME), head, re.M):
            hits.append(os.path.join(root, entry))
    return hits


def doctor(paths, run_tests=False):
    checks = []
    def add(name, ok, detail='', level='FAIL'):
        checks.append({'check': name, 'result': 'PASS' if ok else level, 'detail': detail})

    add('canonical directory', os.path.isdir(paths.canonical) and not os.path.islink(paths.canonical), paths.canonical)
    problems = validate_skill(paths.canonical) if os.path.isdir(paths.canonical) else ['missing']
    add('Agent Skills frontmatter/spec', not problems, '; '.join(problems) or 'valid')
    policy = os.path.join(paths.canonical, 'agents', 'openai.yaml')
    text = ''
    if os.path.isfile(policy):
        with open(policy, encoding='utf-8') as handle:
            text = handle.read()
    add('codex explicit-only policy',
        bool(re.search(r'^policy:\s*\n(?:[ \t]+.*\n)*?[ \t]+allow_implicit_invocation:\s*false\s*$', text + '\n', re.M)),
        policy)
    try:
        with open(os.path.join(paths.canonical, 'SKILL.md'), encoding='utf-8') as handle:
            meta, _ = parse_frontmatter(handle.read())
        add('claude/pi explicit-only flag', meta.get('disable-model-invocation') is True, 'disable-model-invocation')
    except (OSError, ValueError) as exc:
        add('claude/pi explicit-only flag', False, str(exc))
    compiled = []
    scripts = ('scripts/depup.py', 'scripts/review_router.py', 'admin/depup-admin.py')
    for rel in scripts:
        try:
            py_compile.compile(os.path.join(paths.canonical, rel), doraise=True,
                               cfile=os.path.join(tempfile.gettempdir(), 'depup-doctor-%d.pyc' % os.getpid()))
            compiled.append(rel)
        except (py_compile.PyCompileError, OSError) as exc:
            add('compile ' + rel, False, str(exc))
    add('scripts compile', len(compiled) == len(scripts), ', '.join(compiled))
    stray = []
    for dirpath, dirnames, _ in os.walk(paths.canonical):
        for name in list(dirnames):
            if name in ('__pycache__', 'graft', '.serena', '.trail', 'node_modules'):
                stray.append(os.path.relpath(os.path.join(dirpath, name), paths.canonical))
                dirnames.remove(name)
    add('no stray tool caches in the skill', not stray,
        ', '.join(stray) + ' (move to Trash; they are distributed to every harness)' if stray else 'clean',
        level='WARN')
    add('python >= 3.9', sys.version_info >= (3, 9), sys.version.split()[0])
    add('git available', bool(shutil.which('git')), shutil.which('git') or 'missing')
    add('claude per-skill link', link_points_to(paths.claude_link, paths.canonical), paths.claude_link)
    canonical_real = os.path.realpath(paths.canonical)
    for harness, roots in sorted(harness_roots(paths).items()):
        visible, foreign = [], []
        for root in roots:
            for hit in find_definitions(paths.expand(root)):
                (visible if os.path.realpath(hit) == canonical_real else foreign).append(hit)
        installed = bool(shutil.which(HARNESS_BINARIES.get(harness, harness)))
        detail = 'visible via %s' % (', '.join(sorted(set(visible))) or 'nothing')
        if foreign:
            add('%s: no conflicting definition' % harness, False, 'conflicts: ' + ', '.join(foreign))
        add('%s discovery (static)%s' % (harness, '' if installed else ' [binary not installed]'),
            bool(visible), detail, level='FAIL' if installed else 'WARN')
    codex_cfg = os.path.join(paths.home, '.codex', 'config.toml')
    if os.path.isfile(codex_cfg):
        with open(codex_cfg, encoding='utf-8', errors='replace') as handle:
            cfg = handle.read()
        blocked = re.search(r'\[\[skills\.config\]\][^\[]*(name|path)\s*=\s*"[^"]*%s[^"]*"[^\[]*enabled\s*=\s*false' % NAME, cfg)
        add('codex not disabled in config', not blocked, codex_cfg)
    oc_cfg = os.path.join(paths.home, '.config', 'opencode', 'opencode.json')
    if os.path.isfile(oc_cfg):
        try:
            with open(oc_cfg, encoding='utf-8') as handle:
                rules = ((json.load(handle).get('permission') or {}).get('skill') or {})
            denied = [p for p, v in rules.items() if v == 'deny' and fnmatch.fnmatch(NAME, p)] if isinstance(rules, dict) else []
            add('opencode permission not deny', not denied, ', '.join(denied) or 'no deny rule')
        except ValueError:
            add('opencode config readable', False, oc_cfg, level='WARN')
    manifest = load_manifest(paths)
    add('install manifest', manifest is not None, paths.manifest, level='WARN')
    if run_tests:
        proc = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s',
                               os.path.join(paths.canonical, 'tests'), '-t', paths.canonical, '-q'],
                              capture_output=True, text=True, cwd=paths.canonical, timeout=1800)
        tail = (proc.stderr or proc.stdout).strip().splitlines()
        add('test suite', proc.returncode == 0, tail[-1] if tail else 'no output')
    return checks


def cmd_doctor(paths, args):
    checks = doctor(paths, args.run_tests)
    failed = [c for c in checks if c['result'] == 'FAIL']
    if args.json:
        print(json.dumps({'ok': not failed, 'checks': checks}, indent=2))
    else:
        for c in checks:
            print('%-4s %s%s' % (c['result'], c['check'], (' — ' + c['detail']) if c['detail'] else ''))
        print('doctor: %s (%d checks, %d failed)' % ('OK' if not failed else 'FAIL', len(checks), len(failed)))
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='depup-admin.py', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('doctor', 'install', 'update', 'uninstall'))
    parser.add_argument('--home', default=os.environ.get('DEPUP_ADMIN_HOME', '~'))
    parser.add_argument('--no-reconcile', action='store_true')
    parser.add_argument('--remove-canonical', action='store_true')
    parser.add_argument('--run-tests', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    paths = Paths(args.home)
    if args.command == 'install':
        return cmd_install(paths, args)
    if args.command == 'update':
        rc = cmd_install(paths, args)
        return rc if rc else cmd_doctor(paths, args)
    if args.command == 'uninstall':
        return cmd_uninstall(paths, args)
    return cmd_doctor(paths, args)


if __name__ == '__main__':
    sys.exit(main())
