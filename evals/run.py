#!/usr/bin/env python3
"""Run eval cases against the hermes container. Usage: python3 evals/run.py [case_id ...]  (from the repo root)"""
import json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
COMPOSE = os.path.dirname(HERE)


def load(path):
    cases, cur = [], None
    for line in open(path, encoding="utf-8"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("- "):
            cur = {}
            cases.append(cur)
            s = s[2:]
        k, v = s.split(":", 1)
        cur[k.strip()] = json.loads(v)
    return cases


def sh(*a, **kw):
    return subprocess.run(["docker", "compose", *a], cwd=COMPOSE, capture_output=True, text=True, **kw)


def ask(c):
    cmd = ["exec", "-T", "hermes", "hermes", "chat", "-q", c["question"], "--oneshot", "-Q", "-s", "team-history"]
    if c["image"]:
        dst = "/tmp/eval-" + os.path.basename(c["image"])
        sh("cp", os.path.join(HERE, c["image"]), "hermes:" + dst)
        cmd += ["--image", dst]
    r = sh(*cmd, timeout=600)
    out = "\n".join(l for l in r.stdout.splitlines() if not l.startswith("session_id:"))
    return (out + ("\n[stderr]\n" + r.stderr if not r.stdout.strip() else "")).strip()


def check(c, ans):
    low = ans.lower()
    miss = [g for g in c["must_contain"] if not any(t.lower() in low for t in g)]
    bad = [t for t in c["must_not_contain"] if t.lower() in low]
    return miss, bad


def main():
    want = sys.argv[1:]
    cases = [c for c in load(os.path.join(HERE, "cases.yaml")) if not want or c["id"] in want]
    out = [f"# Eval {time.strftime('%F %T')}\n"]
    passed = 0
    for c in cases:
        ans = ask(c)
        miss, bad = check(c, ans)
        ok = not miss and not bad
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'} {c['id']}  missing={miss} forbidden={bad}\n  {ans[:300]!r}\n")
        out.append(f"## {c['id']}: {'PASS' if ok else 'FAIL'}\nQ: {c['question']}\nmissing={miss} forbidden={bad}\n\n{ans}\n")
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    p = os.path.join(HERE, "results", time.strftime("%Y%m%d-%H%M%S") + ".md")
    open(p, "w", encoding="utf-8").write("\n".join(out))
    print(f"{passed}/{len(cases)} passed -> {p}")


if __name__ == "__main__":
    main()
