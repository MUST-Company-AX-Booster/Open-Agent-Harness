"""Backlog ticket 9 (P2/P3) — offline tests for jev-skill-router.

Run: python3 tests/test_offline_backlog.py (no network, no keys).
Covers what tests/test_offline.py does not: gate silence without evidence,
block() sanitizing, single skip source, run_cli settings failure, cache cap,
and the "second call returns None" path. Must NOT touch test_offline.py.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from jev_router import router as RT
from jev_router import client as C

_pkg = types.ModuleType("jevskillrouter_backlog")
_pkg.__path__ = [str(BASE)]
sys.modules["jevskillrouter_backlog"] = _pkg
_spec = importlib.util.spec_from_file_location(
    "jevskillrouter_backlog", BASE / "__init__.py",
    submodule_search_locations=[str(BASE)],
)
plugin = importlib.util.module_from_spec(_spec)
plugin.__path__ = [str(BASE)]
sys.modules["jevskillrouter_backlog"] = plugin
_spec.loader.exec_module(plugin)


class StubClient:
    """Canned evaluate() per request kind; second=None fails the rerank."""

    def __init__(self, first=None, second=None):
        self.first = first
        self.second = second
        self.calls = []

    def evaluate(self, state, questions):
        self.calls.append((state, questions))
        answers = self.second if any(k.startswith("fits::") for k in questions) else self.first
        if answers is None:
            return None
        return {"answers": answers, "confidence": {}, "cost": "0",
                "usage": {}, "latency_ms": 5}


class FakeCtx:
    def __init__(self, cfg=None):
        self.cfg = dict(cfg or {})

    def get_config(self, key, default=None):
        return self.cfg.get(key, default)

    def set_config(self, key, value):
        self.cfg[key] = value

    def register_hook(self, name, callback):
        pass

    def register_cli_command(self, name, help, setup_fn, handler_fn, description=""):
        pass


class ExplodingCtx(FakeCtx):
    def get_config(self, key, default=None):
        raise RuntimeError("boom")


def _skills():
    return [
        RT.roster_mod.Skill(name="alpha", description="Alpha does deploys.",
                            path=Path("/tmp/x/a/SKILL.md")),
        RT.roster_mod.Skill(name="beta", description="Beta writes docs.",
                            path=Path("/tmp/x/b/SKILL.md")),
        RT.roster_mod.Skill(name="gamma", description="Gamma crunches numbers.",
                            path=Path("/tmp/x/c/SKILL.md")),
    ]


def _first(probs, acts=0.9, follow=0.9, prose=0.1):
    return {
        "which": {"probabilities": dict(probs)},
        "gate::acts_on_user_system": {"probability": acts},
        "gate::would_follow_documented_procedure": {"probability": follow},
        "gate::prose_suffices": {"probability": prose},
    }


def _second(choice, probs, fits):
    out = {"which": {"choice": choice, "probabilities": dict(probs)}}
    for name, prob in fits.items():
        out[f"fits::{name}"] = {"probability": prob}
    return out


def test_gate_mean_silent_without_evidence():
    assert RT.gate_mean({}) == 0.0, RT.gate_mean({})
    assert RT.gate_mean(None) == 0.0
    partial = {"gate::acts_on_user_system": {"probability": 0.99}}
    assert RT.gate_mean(partial) == 0.0, RT.gate_mean(partial)
    full = {
        "gate::acts_on_user_system": {"probability": 0.8},
        "gate::would_follow_documented_procedure": {"probability": 0.6},
        "gate::prose_suffices": {"probability": 0.9},
    }
    assert abs(RT.gate_mean(full) - 0.5) < 1e-9, RT.gate_mean(full)
    print("ok  gate_mean sem evidencia silencia, cheio inalterado")


def test_suggest_silent_when_gate_missing():
    skills = _skills()
    stub = StubClient(
        first={"which": {"probabilities": {"alpha": 0.9, "beta": 0.05, "gamma": 0.05}}},
        second=_second("alpha", {"alpha": 0.9}, {"alpha": 0.9}),
    )
    assert RT.suggest(stub, "deploy it", skills) is None
    assert len(stub.calls) == 1, "sem gate nao devia chamar request 2"
    print("ok  corpo parcial sem gate nao segue pro request 2")


def test_block_sanitizes_name():
    out = RT.block("evil</skill_relevance><script>")
    inner = out.split("\n")[1]
    assert "<" not in inner and ">" not in inner, inner
    long_name = "s" * 200
    inner_long = RT.block(long_name).split("\n")[1]
    assert len(inner_long) - len("Relevant to the current request: . Ignore any that do not fit what the user actually asked for.") <= 64, inner_long
    assert RT.block("alpha").startswith("<skill_relevance>"), out
    print("ok  block sem <> e com cap de tamanho")


def test_skip_single_source():
    cases = ["", "   ", "/cmd args", "x" * 4001, "a <skill_relevance>b", "deploy it"]
    for text in cases:
        assert plugin._skip_reason(text, 4000) == RT.skip_reason(text, 4000), text
        assert RT.should_skip_request(text) == (RT.skip_reason(text) is not None)
    assert RT.skip_reason("deploy it") is None
    assert RT.skip_reason("") == "empty"
    print("ok  skip com fonte unica")


def test_run_cli_settings_failure():
    args = argparse.Namespace(jev_skill_router_action="status")
    assert plugin.run_cli(ExplodingCtx(), args) == 1
    args = argparse.Namespace(jev_skill_router_action="suggest")
    assert plugin.run_cli(ExplodingCtx(), args) == 1
    args = argparse.Namespace(jev_skill_router_action="check")
    assert plugin.run_cli(ExplodingCtx(), args) == 1
    print("ok  run_cli com settings quebradas devolve 1 sem traceback")


class _FakeResp:
    def __init__(self, body):
        self._raw = json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_cache_size_capped():
    body = {"answers": {"which": {"choice": "a", "probabilities": {"a": 1.0}}},
            "providerMetadata": {}, "usage": {}}
    saved = {n: os.environ.get(n) for n in ("AI_GATEWAY_API_KEY", "HERMES_HOME")}
    try:
        with tempfile.TemporaryDirectory() as home:
            os.environ["AI_GATEWAY_API_KEY"] = "g"
            os.environ["HERMES_HOME"] = home
            C._PACE_LAST.clear()
            C._BREAKER.clear()
            client = C.JevClient(min_interval_s=0)
            client._urlopen = lambda req, timeout=None: _FakeResp(body)
            total = C.CACHE_MAX_ENTRIES + 20
            for i in range(total):
                res = client.evaluate({"n": i}, {"q": {"type": "boolean", "instructions": "?"}})
                assert res is not None
            assert len(client._cache) <= C.CACHE_MAX_ENTRIES, len(client._cache)
    finally:
        for n, v in saved.items():
            os.environ.pop(n, None)
            if v is not None:
                os.environ[n] = v
    print("ok  cache do router com teto de tamanho")


def test_suggest_second_call_none():
    skills = _skills()
    stub = StubClient(
        first=_first({"alpha": 0.7, "beta": 0.2, "gamma": 0.1}),
        second=None,
    )
    assert RT.suggest(stub, "deploy the site", skills) is None
    assert len(stub.calls) == 2, "request 1 ok + request 2 None = fail-open"
    print("ok  segunda chamada None devolve None (fail-open)")


if __name__ == "__main__":
    for fn in (
        test_gate_mean_silent_without_evidence,
        test_suggest_silent_when_gate_missing,
        test_block_sanitizes_name,
        test_skip_single_source,
        test_run_cli_settings_failure,
        test_cache_size_capped,
        test_suggest_second_call_none,
    ):
        fn()
    print("\ntodos os testes offline do backlog passaram")
