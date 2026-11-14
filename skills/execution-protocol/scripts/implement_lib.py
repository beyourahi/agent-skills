# implement_lib.py - helpers for scripts/implement.sh (run as: python3 -I implement_lib.py <command> ...); stdlib only.
import fnmatch, hashlib, json, os, re, shutil, stat, subprocess, sys, time
a = sys.argv[1:]; cmd = a[0]
DS, DSID, SN = "opencode-go/deepseek-v4.1-flash", "deepseek-v4.1-flash", "claude-sonnet-5-5"
FIELDS = ("step", "round", "plan_sha256", "tier", "route", "model", "effort", "status", "reason", "session_id", "files", "input_tokens", "output_tokens", "cost_usd")
LOCKS = {"bun.lock", "bun.lockb", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "Cargo.lock", "poetry.lock", "uv.lock", "go.sum"}
CFG = re.compile(r"(^|/)(opencode\.jsonc?$|\.opencode/|\.claude/|\.agents/|AGENTS\.md$|CLAUDE\.md$)", re.I)
TEST = re.compile(r"(^|/)(tests?|__tests__|spec)/|\.(test|spec)\.[^/]+$|(^|/)test_[^/]+\.py$|_test\.[^/]+$")
SKIP = re.compile(r"\.skip\(|\bxit\(|\bxdescribe\(|\bit\.todo\b|\btest\.todo\b|@pytest\.mark\.skip|@Disabled\b|\bt\.Skip\(")
def rows(p):
    out = []
    for l in (open(p, errors="replace") if os.path.exists(p) else []):
        if not l.strip(): continue
        try: out.append(json.loads(l))
        except Exception: out.append(None)
    return out
def sha(f): return hashlib.sha256(open(f, "rb").read()).hexdigest()
def one(s): return " ".join(str(s).split())[:300]
def num(x):
    try: return float(x or 0)
    except Exception: return 0.0
def logrow(ev, **d):
    r = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for k in FIELDS: r[k] = d.get(k, [] if k == "files" else "")
    for k in ("round", "input_tokens", "output_tokens"): r[k] = int(num(r[k]))
    r["cost_usd"] = num(r["cost_usd"]); open(os.path.join(ev, "impl.log"), "a").write(json.dumps(r) + "\n")
def plan(pf):
    o = json.load(open(pf)); o = o.get("structured_output", o) if isinstance(o, dict) else {}
    return o if isinstance(o, dict) else {}
if cmd == "check":
    bad = []; seen = set()
    for i, d in enumerate(rows(os.path.join(a[1], "impl.log"))):
        if not isinstance(d, dict): bad.append("impl.log line %d: not JSON" % (i + 1)); continue
        r, s, k = d.get("route"), d.get("status"), (d.get("step"), d.get("round"))
        if s in ("start", "discarded") and r in ("", None): continue
        if r == "deepseek":
            if d.get("model") != DS or d.get("effort") != "high": bad.append("impl.log line %d: deepseek row is not %s high" % (i + 1, DS))
            if s == "unavailable": seen.add(k)
        elif r == "sonnet":
            if d.get("model") != SN or d.get("effort") != "high": bad.append("impl.log line %d: sonnet row is not %s high" % (i + 1, SN))
            if k not in seen: bad.append("impl.log line %d: sonnet without an earlier deepseek unavailable row for the same step and round" % (i + 1))
        else: bad.append("impl.log line %d: route %r is not an implementer route" % (i + 1, r))
    print("\n".join(bad) or "impl.log routes ok"); sys.exit(1 if bad else 0)
if cmd == "laststart":
    r = [d for d in rows(os.path.join(a[1], "impl.log"))]
    sys.exit(0 if r and isinstance(r[-1], dict) and r[-1].get("status") == "start" else 1)
if cmd == "plan":
    ev, step = a[1:3]; pl = [d for d in rows(os.path.join(ev, "gates.log")) if isinstance(d, dict) and d.get("gate") == "plan" and d.get("status") == "ok"]
    f = os.path.join(ev, os.path.basename(str(pl[-1].get("file") or "-"))) if pl else ""
    if not pl or not os.path.isfile(f) or sha(f) != pl[-1].get("file_sha256"): sys.exit(1)
    try: plan(f)
    except Exception: sys.exit(1)
    ps = sha(f); n = len([d for d in rows(os.path.join(ev, "impl.log")) if isinstance(d, dict) and d.get("step") == step and d.get("plan_sha256") == ps and d.get("status") in ("done", "failed", "escalate")])
    print("\t".join([f, ps, str(n + 1), str(n)])); sys.exit()
if cmd == "brief":
    tpl, pf, wt, notes, out = a[1:6]; o = plan(pf); L = lambda xs: "\n".join("- " + str(x) for x in (xs or []))
    t = open(tpl).read().replace("{WORKTREE}", wt).replace("{RESULT}", os.path.join(wt, ".impl/result.json")).replace("{ESCALATION}", os.path.join(wt, ".impl/escalation.md"))
    t += "\n## Worktree\n%s (follow its AGENTS.md/CLAUDE.md)\n\n## Acceptance criteria\n%s\n\n## Design\n%s\n\n## Files (the only paths you may change)\n%s\n\n## Tests (write them first and see them fail)\n%s\n" % (
        wt, L(o.get("acceptance_criteria")), o.get("design", ""), L(o.get("files")), L(o.get("tests")))
    if notes != "-": t += "\n## Notes from the conductor\n" + open(notes, errors="replace").read() + "\n"
    open(out, "w").write(t); sys.exit()
if cmd == "sid":
    for l in (open(a[1], errors="replace") if os.path.exists(a[1]) else []):
        try: s = json.loads(l).get("sessionID")
        except Exception: s = None
        if isinstance(s, str) and re.fullmatch(r"[A-Za-z0-9_-]+", s): print(s); break
    sys.exit()
if cmd == "dsclass":
    # dsclass EXPORT EVENTS SID: ok, or unavailable with the reason (AC11). Prints kind, reason, input, output, cost.
    ex, evf, sid = a[1:4]
    try: d = json.load(open(ex))
    except Exception: d = {}
    d = d if isinstance(d, dict) else {}; info = d.get("info") if isinstance(d.get("info"), dict) else {}
    ms = [m for m in d.get("messages") or [] if isinstance(m, dict) and m.get("type") == "assistant"]
    mo = lambda m: m.get("model") if isinstance(m.get("model"), dict) else {}
    obs = sorted(set("%s/%s#%s" % (mo(m).get("providerID"), mo(m).get("id"), mo(m).get("variant")) for m in ms))
    errs = [str(m["error"].get("type", "")) for m in ms if isinstance(m.get("error"), dict)]
    steps = []
    for l in (open(evf, errors="replace") if os.path.exists(evf) else []):
        try: e = json.loads(l)
        except Exception: continue
        if isinstance(e, dict) and e.get("type") == "step_finish" and isinstance(e.get("part"), dict): steps.append(num(e["part"].get("cost")))
    tk = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}; cost = max([num(info.get("cost"))] + steps)
    why = ("no session" if sid == "-" else "export is not this session" if info.get("id") != sid else "no assistant message" if not ms
           else "model " + ",".join(obs) if obs != ["opencode-go/%s#high" % DSID]
           else "provider error: " + ",".join(e for e in errs if e.startswith("provider.")) if any(e.startswith("provider.") for e in errs)
           else "cost: %s" % cost if cost > 0 else "")
    print("\t".join(["unavailable" if why else "ok", one(why) or "-", str(int(num(tk.get("input")))), str(int(num(tk.get("output")))), str(cost)])); sys.exit()
if cmd == "snclass":
    try: d = json.loads(open(a[1], errors="replace").read() or "null")
    except Exception: d = None
    if not isinstance(d, dict): print("unavailable\tno JSON from claude\t0\t0\t0"); sys.exit()
    mu = d.get("modelUsage") if isinstance(d.get("modelUsage"), dict) else {}; bad = [k for k in mu if SN not in k]
    why = "is_error: " + one(d.get("result") or "") if d.get("is_error") else "modelUsage: " + (",".join(mu) or "none") if bad or not mu else ""
    t = mu.get(SN) if isinstance(mu.get(SN), dict) else {}
    print("\t".join(["unavailable" if why else "ok", one(why) or "-", str(int(num(t.get("inputTokens")))), str(int(num(t.get("outputTokens")))), str(num(d.get("total_cost_usd")))])); sys.exit()
if cmd == "row":
    ev = a[1]; logrow(ev, **dict(zip([f for f in FIELDS if f != "files"], a[2:]))); sys.exit()
if cmd == "sonnetcfg":
    # Claude Code permission rules for the Sonnet route (AC15): deny rules beat --allowedTools, and the Bash rules derive from
    # opencode.json so the two implementer routes cannot drift.
    o = json.load(open(a[1])); deny = []
    for p in ((o.get("agents") or {}).get("implementer") or {}).get("permissions") or []:
        if not isinstance(p, dict) or p.get("action") != "shell" or p.get("effect") != "deny": continue
        r = str(p.get("resource") or ""); c = r.rstrip("*").strip()
        deny.append("Bash(%s:*)" % c if r.endswith("*") else "Bash(%s)" % c)
    deny += ["Read(**/*.env)", "Read(**/*.env.*)", "Edit(**/*.env)", "Edit(**/*.env.*)"]
    print(json.dumps({"permissions": {"deny": deny}})); sys.exit()
if cmd in ("pids", "reap"):
    # pids WT PID: this user's processes whose cwd is WT or under it, minus PID and its ancestors.
    # reap WT PID: TERM the targets, wait 3 s, SIGSTOP until no unstopped target is left (a stopped process cannot fork; at most
    # 20 passes), SIGKILL, rescan. Exit 1 with the survivors' PIDs when any target is left: the caller treats the worktree as tainted.
    import signal; wt, me = os.path.realpath(a[1]), a[2]; keep = set(); p = me
    while p and p not in keep and p != "0":
        keep.add(p); r = subprocess.run(["ps", "-o", "ppid=", "-p", p], capture_output=True, text=True); p = r.stdout.strip()
    def targets():
        r = subprocess.run(["lsof", "-a", "-u", str(os.getuid()), "-d", "cwd", "-Fpn"], capture_output=True, text=True, errors="replace"); pid = None; out = set()
        for l in r.stdout.splitlines():
            if l.startswith("p"): pid = l[1:]
            elif l.startswith("n") and pid and pid not in keep and (l[1:] == wt or l[1:].startswith(wt + "/")): out.add(pid)
        return out
    def sig(qs, s):
        for q in qs:
            try: os.kill(int(q), s)
            except OSError: pass
    t = targets()
    if cmd == "pids": print(" ".join(sorted(t))); sys.exit()
    if not t: sys.exit()
    sig(t, signal.SIGTERM); time.sleep(3); capped = True
    for _ in range(20):
        t = targets(); st = subprocess.run(["ps", "-o", "pid=,stat=", "-p", ",".join(t)], capture_output=True, text=True).stdout.split("\n") if t else []
        un = [q for q in t if not any(l.split()[:1] == [q] and "T" in l.split()[1] for l in st if len(l.split()) > 1)]
        if not un: capped = False; break
        sig(un, signal.SIGSTOP)
    sig(targets(), signal.SIGKILL); time.sleep(0.5); left = targets()
    if left or capped: print(" ".join(sorted(left)) or "pass cap"); sys.exit(1)
    sys.exit()
if cmd == "final":
    # final EVID WT PF TAG STEP ROUND PS TIER ROUTE MODEL EFFORT SID IN OUT COST FORCED BASE: AC13 outcome checks; logs the row, prints status and reason.
    ev, wt, pf, tag, step, rnd, ps, tier, route, model, effort, sid, tin, tout, cost, forced, base = a[1:18]
    git = lambda *x: subprocess.run(("git", "-C", wt) + x, capture_output=True).stdout.decode("utf-8", "surrogateescape")
    allow = [f[2:] if f.startswith("./") else f for f in (str(x) for x in plan(pf).get("files") or [])]
    def okp(p):
        if p.startswith(".github/") or CFG.search(p): return p in allow
        return any(p == f or (f.endswith("/") and p.startswith(f)) or fnmatch.fnmatchcase(p, f) for f in allow)
    ch = [(e[:2], e[3:]) for e in git("status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all").split("\0") if len(e) > 3 and not e[3:].startswith(".impl/")]
    def deps(t):
        try: j = json.loads(t)
        except Exception: return set()
        return set((s, k) for s in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies") for k in (j.get(s) or {}) if isinstance(j.get(s), dict)) if isinstance(j, dict) else set()
    def added(x, p):
        if x == "??": return open(os.path.join(wt, p), errors="replace").read() if reg(os.path.join(wt, p)) else ""
        return "\n".join(l[1:] for l in git("diff", "-U0", "HEAD", "--", p).splitlines() if l.startswith("+") and not l.startswith("+++"))
    imp = os.path.join(wt, ".impl"); rp, ep = os.path.join(imp, "result.json"), os.path.join(imp, "escalation.md"); res = None
    reg = lambda f: stat.S_ISREG(os.lstat(f).st_mode) if os.path.lexists(f) else None
    why = "" if forced == "-" else forced
    if not why and git("rev-parse", "HEAD").strip() != base: why = "HEAD moved: the implementer changed git history"
    if not why and (os.path.islink(imp) or not os.path.isdir(imp) or reg(rp) is False or reg(ep) is False): why = ".impl is not a real dir, or result.json/escalation.md is not a regular file"
    if not why:
        try: res = json.load(open(rp)) if reg(rp) else None
        except Exception: res = None
        if not isinstance(res, dict) or res.get("status") not in ("done", "escalate"): why = "missing or invalid .impl/result.json"
    out = [p for x, p in ch if not okp(p) or p.endswith("/")]
    if not why and out: why = "scope: outside the plan's files: " + ",".join(out[:10])
    if not why and [p for x, p in ch if os.path.basename(p) in LOCKS]: why = "lockfile changed: " + ",".join(p for x, p in ch if os.path.basename(p) in LOCKS)
    for x, p in ([] if why else ch):
        if os.path.basename(p) == "package.json" and "D" not in x:
            if not reg(os.path.join(wt, p)): why = "package.json is not a regular file: " + p; break
            new = sorted(k for s, k in deps(open(os.path.join(wt, p), errors="replace").read()) - (deps(git("show", "HEAD:" + p)) if x != "??" else set()))
            if new: why = "new dependency: " + ",".join(new); break
    if not why and [p for x, p in ch if "D" in x and TEST.search(p)]: why = "deleted test: " + ",".join(p for x, p in ch if "D" in x and TEST.search(p))
    if not why and [p for x, p in ch if "D" not in x and TEST.search(p) and SKIP.search(added(x, p))]: why = "skip marker added in a test file"
    if not why and res.get("status") == "escalate" and not reg(ep): why = "escalate without .impl/escalation.md"
    st = "failed" if why else res["status"]; rd, ed = os.path.join(ev, "impl-%s-result.json" % tag), os.path.join(ev, "escalation-%s.md" % tag)
    if reg(rp): shutil.copyfile(rp, rd)
    if reg(ep): shutil.copyfile(ep, ed)
    files = sorted(p for x, p in ch); tests = res.get("tests_run", []) if isinstance(res, dict) else []
    rec = dict(zip(FIELDS, (step, rnd, ps, tier, route, model, effort, st, why, sid, files, tin, tout, cost)))
    json.dump(dict(rec, tests_run=tests, escalation=ed if os.path.isfile(ed) else ""), open(os.path.join(ev, "impl-%s.json" % tag), "w"), indent=1)
    logrow(ev, **rec); print("%s\t%s" % (st, one(why) or "-")); sys.exit()
sys.exit("implement_lib.py: unknown command " + cmd)
