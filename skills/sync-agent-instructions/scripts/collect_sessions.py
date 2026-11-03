#!/usr/bin/env python3
"""Collect a compact digest of past AI-agent sessions for one repository.

Scans local transcript stores of common coding agents (Claude Code, Codex CLI,
Gemini CLI, plus any extra dirs you pass), keeps only sessions whose working
directory is inside the repo, and prints the high-signal parts: user messages
(corrections flagged first) and failed tool calls. Secrets are redacted.

Stdlib only. Usage:
  python3 collect_sessions.py [--repo PATH] [--days 60] [--max-chars 60000]
                              [--extra-dir DIR ...]
"""
import argparse, hashlib, json, os, re, subprocess, sys, time
from pathlib import Path

HOME = Path.home()
CORRECTION = re.compile(
    r"\b(no[,.!]|don'?t|do not|never|always|stop|wrong|instead|actually|"
    r"not like that|i told you|again|should(?:n'?t)?|must|prefer|remember)\b", re.I)
SECRETS = [
    re.compile(r"(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|gh[ousr]_[A-Za-z0-9]{20,}|"
               r"xox[abpr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})"),
    re.compile(r"(?i)\b(bearer|token|password|passwd|secret|api[_-]?key)\b\s*[:=]?\s*\S{6,}"),
]
NOISE = re.compile(r"^\s*<(system-reminder|command-|local-command|user_instructions|environment_context)", re.I)


def redact(s):
    for p in SECRETS:
        s = p.sub("[REDACTED]", s)
    return s


def clip(s, n):
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def repo_root(arg):
    if arg:
        return Path(arg).resolve()
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
        return Path(out.stdout.strip()).resolve()
    except Exception:
        return Path.cwd().resolve()


def inside(cwd, root):
    try:
        Path(cwd).resolve().relative_to(root)
        return True
    except Exception:
        return False


def read_lines(path):
    """Yield JSON records from .jsonl, .jsonl.zst, or a single .json file."""
    try:
        if path.suffix == ".zst":
            raw = subprocess.run(["zstd", "-dc", str(path)], capture_output=True, check=True).stdout.decode("utf-8", "replace")
        else:
            raw = path.read_text("utf-8", "replace")
    except Exception:
        return
    if path.suffix == ".json":
        try:
            data = json.loads(raw)
        except Exception:
            return
        items = data if isinstance(data, list) else data.get("messages") or data.get("history") or [data]
        yield from (i for i in items if isinstance(i, dict))
        return
    for line in raw.splitlines():
        try:
            rec = json.loads(line)
            if isinstance(rec, dict):
                yield rec
        except Exception:
            continue


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, dict) and c.get("type") in (None, "text", "input_text") and isinstance(c.get("text"), str):
                parts.append(c["text"])
        return "\n".join(parts)
    if isinstance(content, dict):
        return text_of(content.get("content") or content.get("text") or content.get("parts"))
    return ""


def find_cwd(rec):
    for key in ("cwd", "workdir", "projectRoot", "project_root"):
        if isinstance(rec.get(key), str):
            return rec[key]
    p = rec.get("payload")
    if isinstance(p, dict):
        return find_cwd(p)
    return None


def extract(rec):
    """Return list of (kind, text). kind in {'user', 'error'}."""
    out = []
    node = rec.get("payload") if isinstance(rec.get("payload"), dict) else rec
    msg = node.get("message") if isinstance(node.get("message"), dict) else node
    role = msg.get("role") or node.get("role") or node.get("type") or rec.get("type")
    content = msg.get("content", msg.get("parts", msg.get("text")))
    # failed tool results (Claude Code / generic)
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                out.append(("error", text_of(c.get("content"))))
    if node.get("type") in ("function_call_output", "exec_command_end"):
        o = node.get("output") or node
        code = o.get("exit_code") if isinstance(o, dict) else None
        if code not in (None, 0):
            out.append(("error", text_of(o.get("output") or o.get("stderr") or "")))
    if role in ("user", "human", "user_message"):
        t = text_of(content) or (node.get("message") if isinstance(node.get("message"), str) else "")
        if t and not NOISE.match(t):
            out.append(("user", t))
    return out


def candidate_files(root, extra_dirs, cutoff):
    claude_home = Path(os.environ.get("CLAUDE_CONFIG_DIR", HOME / ".claude"))
    codex_home = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
    gemini_home = HOME / ".gemini"
    enc = re.sub(r"[^A-Za-z0-9]", "-", str(root))
    found = []
    # Claude Code: ~/.claude/projects/<encoded-cwd>/*.jsonl (subdirs of repo encode with same prefix)
    proj = claude_home / "projects"
    if proj.is_dir():
        for d in proj.iterdir():
            if d.is_dir() and d.name.startswith(enc):
                found += [("claude-code", f, True) for f in d.glob("*.jsonl")]
    # Codex CLI: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl[.zst]
    for pat in ("**/rollout-*.jsonl", "**/rollout-*.jsonl.zst"):
        found += [("codex", f, False) for f in (codex_home / "sessions").glob(pat)]
    # Gemini CLI: ~/.gemini/tmp/<sha256(project_root)>/...
    gdir = gemini_home / "tmp" / hashlib.sha256(str(root).encode()).hexdigest()
    if gdir.is_dir():
        found += [("gemini", f, True) for f in gdir.rglob("*.json*")]
    for d in extra_dirs:
        found += [("extra", f, False) for f in Path(d).expanduser().rglob("*.json*")]
    return [(s, f, known) for s, f, known in found if f.is_file() and f.stat().st_mtime >= cutoff]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--max-chars", type=int, default=60000)
    ap.add_argument("--extra-dir", action="append", default=[])
    a = ap.parse_args()

    root = repo_root(a.repo)
    cutoff = time.time() - a.days * 86400
    sessions = []
    for source, f, known in candidate_files(root, a.extra_dir, cutoff):
        matched, users, errors = known, [], []
        for rec in read_lines(f):
            if not matched:
                cwd = find_cwd(rec)
                if cwd is None:
                    continue
                if not inside(cwd, root):
                    break
                matched = True
            for kind, t in extract(rec):
                (users if kind == "user" else errors).append(redact(t))
        if matched and (users or errors):
            sessions.append((f.stat().st_mtime, source, f, users, errors))

    sessions.sort(key=lambda s: s[0], reverse=True)
    out = [f"# Session digest for {root}", f"_{len(sessions)} sessions in last {a.days} days, newest first. Corrections marked ⚑._\n"]
    budget = a.max_chars - len("\n".join(out))
    for mtime, source, f, users, errors in sessions:
        lines = [f"## {time.strftime('%Y-%m-%d', time.localtime(mtime))} · {source} · {f.name}"]
        flagged = [u for u in users if CORRECTION.search(u)]
        lines += [f"- ⚑ {clip(u, 400)}" for u in flagged]
        lines += [f"- {clip(u, 250)}" for u in users if u not in flagged][:15]
        lines += [f"- ✗ error: {clip(e, 200)}" for e in errors][:8]
        block = "\n".join(lines) + "\n"
        if len(block) > budget:
            out.append(f"_…truncated; {len(sessions)} sessions total. Re-run with --days or --max-chars to adjust._")
            break
        out.append(block)
        budget -= len(block)
    print("\n".join(out))


if __name__ == "__main__":
    sys.exit(main())
