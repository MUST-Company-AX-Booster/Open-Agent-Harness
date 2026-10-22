import asyncio
import http.client
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock
import urllib.error
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location("pm_router", Path(__file__).parent / "pm_router" / "__init__.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

# The tests run against one real ladder: Neuralwatt deepseek/glm, and gpt-5.6-sol over Codex OAuth for the hard rungs.
EXAMPLE_RUNGS = {
    "quick": {"model": "deepseek-v4.1-flash", "effort": "none"},
    "lookup": {"model": "deepseek-v4.1-flash", "effort": "low"},
    "reasoned": {"model": "deepseek-v4.1-flash", "effort": "medium"},
    "routine": {"model": "glm-5.3-flash", "effort": "low"},
    "multi_step": {"model": "glm-5.3-flash", "effort": "high"},
    "deep": {"model": "gpt-5.6-sol", "effort": "low", "provider": "openai-codex"},
    "hardest": {"model": "gpt-5.6-sol", "effort": "medium", "provider": "openai-codex"},
}
assert pr.set_rungs(EXAMPLE_RUNGS)

# Efforts each model accepts: Neuralwatt /v1/models reasoning.accepted_efforts (2026-10-09; "medium" runs as
# "high" there) and, for Codex,
# hermes-agent agent/reasoning_effort.py. The default model (glm-5.3-flash) runs a Sol rung's effort when Codex
# is unavailable, so Sol efforts must be ones it accepts too.
SUPPORTED = {"deepseek-v4.1-flash": {"max", "xhigh", "high", "medium", "low", "minimal", "none"},
             "glm-5.3-flash": {"max", "xhigh", "high", "medium", "low", "minimal"},
             "gpt-5.6-sol": {"none", "low", "medium", "high", "xhigh", "max"}}
DEFAULT_MODEL = "glm-5.3-flash"


class RulesTest(unittest.TestCase):
    def test_every_rung_effort_is_accepted_by_its_model_and_by_the_fallback(self):
        for _, model, effort, _ in pr.RUNGS:
            self.assertIn(effort, SUPPORTED[model])
            if model in pr.PROVIDER:
                self.assertIn(effort, SUPPORTED[DEFAULT_MODEL])

    def test_incomplete_or_bad_ladder_is_refused_and_changes_nothing(self):
        before = list(pr.RUNGS)
        for bad in (None, {}, {**EXAMPLE_RUNGS, "deep": {"effort": "low"}},
                    {**EXAMPLE_RUNGS, "quick": {"model": "m", "effort": "turbo"}},
                    {k: v for k, v in EXAMPLE_RUNGS.items() if k != "hardest"}):
            self.assertFalse(pr.set_rungs(bad), bad)
            self.assertEqual(pr.RUNGS, before)

    def test_options_every_rung_most_expensive_first(self):
        self.assertEqual(list(pr.OPTIONS), ["hardest", "deep", "multi_step", "routine", "reasoned", "lookup", "quick"])

    def test_confident_choice_is_taken_as_is(self):
        self.assertEqual(pr.choose("quick", 0.9), ("deepseek-v4.1-flash", "none"))
        self.assertEqual(pr.choose("deep", 0.9), ("gpt-5.6-sol", "low"))

    def test_low_confidence_moves_one_rung_up(self):
        self.assertEqual(pr.choose("quick", 0.5), ("deepseek-v4.1-flash", "low"))
        self.assertEqual(pr.choose("hardest", 0.1), ("gpt-5.6-sol", "medium"))

    def test_below_the_floor_only_when_sure_and_not_continuing(self):
        self.assertEqual(pr.choose("routine", 0.82, 5), ("glm-5.3-flash", "low"))
        self.assertEqual(pr.choose("routine", 0.66, 5), ("glm-5.3-flash", "low"))
        self.assertEqual(pr.choose("routine", 0.82, 5, continues=True), ("gpt-5.6-sol", "low"))
        # unsure: the floor, not a bump past it
        self.assertEqual(pr.choose("quick", 0.5, 5), ("gpt-5.6-sol", "low"))
        # above the floor: untouched
        self.assertEqual(pr.choose("hardest", 0.9, 5, continues=True), ("gpt-5.6-sol", "medium"))

    def test_sol_goes_through_codex_oauth_others_stay_on_default_route(self):
        self.assertEqual(pr.model_override("gpt-5.6-sol"), {"model": "gpt-5.6-sol", "provider": "openai-codex"})
        self.assertEqual(pr.model_override("glm-5.3-flash"), {"model": "glm-5.3-flash"})

    def test_reasoning_shape(self):
        self.assertEqual(pr.reasoning("none"), {"enabled": False})
        self.assertEqual(pr.reasoning("high"), {"enabled": True, "effort": "high"})


class JevCallTest(unittest.TestCase):
    def setUp(self):
        self.real_post = pr._post

    def tearDown(self):
        pr._post = self.real_post

    def test_request_shape_and_parsed_answer(self):
        sent = {}

        def fake_post(body, timeout):
            sent.update(body=body, timeout=timeout)
            return {"answers": {"route": {"choice": "routine", "confidence": 0.8, "probabilities": {"routine": 0.8}}},
                    "usage": {"input_tokens": 321}}

        pr._post = fake_post
        out = pr.ask_jev("x" * 5000, "earlier message", 2.0)
        self.assertEqual(sent["body"]["model"], "jev-1.13.0")
        self.assertEqual(len(sent["body"]["state"]["request"]), 4000)
        self.assertEqual(sent["body"]["state"]["recent_context"], "earlier message")
        self.assertEqual(sent["body"]["questions"]["route"]["criteria"], pr.OPTIONS)
        self.assertEqual(sent["body"]["questions"]["kind"]["criteria"], pr.KIND)
        self.assertTrue(out["continues"])  # no kind answer: counts as continuing
        self.assertEqual((out["rung"], out["confidence"], out["jev_tokens"]), ("routine", 0.8, 321))

    def test_continues_only_on_a_sure_continues_answer_or_a_missing_one(self):
        route = {"choice": "routine", "confidence": 0.8}
        for kind, continues in (({"choice": "standalone", "confidence": 0.22}, False),
                                ({"choice": "continues", "confidence": 0.94}, True),
                                ({"choice": "continues", "confidence": 0.55}, False),
                                ({"choice": "continues", "confidence": float("nan")}, True),
                                ({"choice": "maybe", "confidence": 1.0}, True), (None, True), (["standalone"], True)):
            pr._post = lambda body, timeout, kind=kind: {"answers": {"route": route, "kind": kind}}
            self.assertEqual(pr.ask_jev("hi", "", 2.0)["continues"], continues, kind)

    def test_invented_choice_is_ignored(self):
        pr._post = lambda body, timeout: {"answers": {"route": {"choice": "gpt-9", "confidence": 1.0}}}
        self.assertIsNone(pr.ask_jev("hi", "", 2.0))

    def test_http_errors_and_timeouts_fail_open(self):
        for exc in (urllib.error.HTTPError(pr.JEV_URL, 429, "busy", {}, io.BytesIO()), TimeoutError("slow"),
                    urllib.error.URLError("dns"), http.client.IncompleteRead(b"par"), http.client.BadStatusLine("")):
            def boom(body, timeout, exc=exc):
                raise exc
            pr._post = boom
            self.assertIsNone(pr.ask_jev("hi", "", 2.0))

    def test_malformed_answers_fail_open(self):
        route = {"choice": "routine"}
        for bad in ({}, {"answers": {}}, {"answers": None}, {"answers": {"route": None}}, [],
                    {"answers": {"route": {"confidence": 0.9}}}, {"answers": {"route": {"choice": ["routine"]}}},
                    {"answers": {"route": {**route, "confidence": "high"}}}):
            pr._post = lambda body, timeout, bad=bad: bad
            self.assertIsNone(pr.ask_jev("hi", "", 2.0), bad)
        pr._post = lambda body, timeout: {"answers": {"route": {**route, "confidence": None}}}  # missing -> 0.0
        self.assertEqual(pr.ask_jev("hi", "", 2.0)["confidence"], 0.0)
        for bad in (float("nan"), float("inf"), 1.5, -0.1):  # not a probability: fail open, never "confident"
            pr._post = lambda body, timeout, bad=bad: {"answers": {"route": {**route, "confidence": bad}}}
            self.assertIsNone(pr.ask_jev("hi", "", 2.0), bad)
        pr._post = lambda body, timeout: {"answers": {"route": route}, "usage": None}
        self.assertIsNone(pr.ask_jev("hi", "", 2.0)["jev_tokens"])


class FakeGateway:
    """Just the GatewayRunner surface pm_router touches."""

    def __init__(self, running=False, authorized=True):
        self.conv = SimpleNamespace(model_override=None, reasoning_override=None)
        self.running, self.authorized = running, authorized
        self.persisted = None  # a /model override saved in the session store before a restart or /resume

    def _session_key_for_source(self, source):
        return "agent:main:slack:group:C1:T1"

    def _session_state(self, key):
        return SimpleNamespace(conversation=self.conv)

    def _is_session_running(self, key):
        return self.running

    def _is_user_authorized_for_source(self, source):
        return self.authorized

    def _set_session_reasoning_override(self, key, cfg):
        self.conv.reasoning_override = cfg

    def _rehydrate_session_model_override(self, key):
        if self.persisted and self.conv.model_override is None:  # Hermes does this later, inside the turn
            self.conv.model_override = dict(self.persisted)


class FakeStore:
    def __init__(self, session_id=None):
        self.session_id = session_id

    def peek_session_id(self, key):
        return self.session_id


def event(text="", command=None):
    return SimpleNamespace(text=text, source=object(), is_command=lambda: command is not None,
                           get_command=lambda: command)


def jev(rung, confidence=0.9, continues=False):
    """Fake ask_jev."""
    return lambda text, context, timeout: {"rung": rung, "confidence": confidence, "continues": continues,
                                           "probabilities": {}, "jev_ms": 5, "jev_tokens": 100}


LONG = "Compare this sprint's Jira tickets with the GitHub PRs and tell me what slipped and why. " * 3


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "pm_router.jsonl"
        self.real = (pr._log_path, pr.ask_jev)
        pr._log_path = lambda: self.log
        pr._threads.clear()
        self.settings = dict(pr._settings)
        pr._settings.update(mode="apply", timeout_s=2.0)

    def tearDown(self):
        pr._log_path, pr.ask_jev = self.real
        pr._settings.update(self.settings)
        self.tmp.cleanup()

    def send(self, gw, ev, store=None):
        async def go():
            await pr.on_dispatch(event=ev, gateway=gw, session_store=store)
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            await asyncio.gather(*pending)  # shadow mode decides in a background task
        asyncio.run(go())

    def lines(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_apply_sets_model_and_effort(self):
        gw, pr.ask_jev = FakeGateway(), jev("multi_step")
        self.send(gw, event(LONG))
        self.assertEqual(gw.conv.model_override, {"model": "glm-5.3-flash"})
        self.assertEqual(gw.conv.reasoning_override, {"enabled": True, "effort": "high"})
        self.assertEqual(self.lines()[-1]["model"], "glm-5.3-flash")

    def test_hard_request_goes_to_sol_over_codex(self):
        gw, pr.ask_jev = FakeGateway(), jev("hardest")
        self.send(gw, event(LONG))
        self.assertEqual(gw.conv.model_override, {"model": "gpt-5.6-sol", "provider": "openai-codex"})
        self.assertEqual(gw.conv.reasoning_override, {"enabled": True, "effort": "medium"})
        pr.ask_jev = jev("deep")  # same thread, next long message: our own provider override is not a user pin
        self.send(gw, event(LONG + " And who owns each?"))
        self.assertEqual(gw.conv.reasoning_override, {"enabled": True, "effort": "low"})
        self.assertNotIn("skipped", self.lines()[-1])

    def test_long_message_drops_effort_freely_and_the_model_only_when_sure(self):
        gw, pr.ask_jev = FakeGateway(), jev("deep")
        self.send(gw, event(LONG))
        pr.ask_jev = jev("routine", 0.6)  # not sure it's easier: stay on Sol at its cheapest rung
        self.send(gw, event(LONG + " Also list owners."))
        self.assertEqual(gw.conv.model_override, {"model": "gpt-5.6-sol", "provider": "openai-codex"})
        self.assertEqual(gw.conv.reasoning_override, {"enabled": True, "effort": "low"})
        pr.ask_jev = jev("routine", 0.85)  # sure: down to the cheaper model
        self.send(gw, event(LONG + " Now just list the ticket keys."))
        self.assertEqual(gw.conv.model_override, {"model": "glm-5.3-flash"})
        self.assertEqual(gw.conv.reasoning_override, {"enabled": True, "effort": "low"})
        pr.ask_jev = jev("routine", 0.7)  # effort within the thread's model needs no extra confidence
        self.send(gw, event(LONG))
        self.assertEqual(gw.conv.reasoning_override, {"enabled": True, "effort": "low"})

    def test_hard_question_after_hi_upgrades_model_even_when_jev_is_unsure(self):
        gw, pr.ask_jev = FakeGateway(), jev("quick")
        self.send(gw, event("hi"))
        pr.ask_jev = jev("multi_step", confidence=0.55)  # unsure: bumped one rung up, and the model still switches
        self.send(gw, event(LONG))
        self.assertEqual(gw.conv.model_override, {"model": "gpt-5.6-sol", "provider": "openai-codex"})
        self.assertEqual(gw.conv.reasoning_override, {"enabled": True, "effort": "low"})

    def test_summarise_follow_up_on_a_sol_thread_goes_to_a_cheaper_model(self):
        # the Slack bug: Jev was offered only the Sol rungs. Real Jev with context: routine at 0.77-0.88, standalone
        gw, pr.ask_jev = FakeGateway(), jev("deep")
        self.send(gw, event(LONG))
        pr.ask_jev = jev("routine", 0.77)
        self.send(gw, event("summarise the thread @PM-Agent"))
        self.assertEqual((gw.conv.model_override, gw.conv.reasoning_override),
                         ({"model": "glm-5.3-flash"}, {"enabled": True, "effort": "low"}))
        self.assertTrue(self.lines()[-1]["follow_up"])
        self.assertFalse(self.lines()[0]["follow_up"])  # the long first message is not a follow-up

    def test_approval_never_lowers_the_thread(self):
        # real Jev with context: "ok 고마워, 그걸로 진행해" after a research ask -> quick at 0.92, continues at 1.00
        gw, pr.ask_jev = FakeGateway(), jev("deep")
        self.send(gw, event(LONG))
        pr.ask_jev = jev("quick", 0.92, continues=True)
        self.send(gw, event("ok 고마워, 그걸로 진행해"))
        self.assertEqual((gw.conv.model_override["model"], gw.conv.reasoning_override["effort"]),
                         ("gpt-5.6-sol", "low"))
        self.assertTrue(self.lines()[-1]["continues"])
        pr.ask_jev = jev("quick", 1.0)  # "thanks!" is not continuing anything: it may go down
        self.send(gw, event("thanks!"))
        self.assertEqual(gw.conv.model_override, {"model": "deepseek-v4.1-flash"})

    def test_short_follow_up_keeps_effort_on_the_same_model(self):
        gw, pr.ask_jev = FakeGateway(), jev("multi_step")
        self.send(gw, event(LONG))
        pr.ask_jev = jev("routine", 0.9, continues=True)  # "yes do it" is the multi-step work it continues
        self.send(gw, event("yes do it"))
        self.assertEqual(gw.conv.reasoning_override, {"enabled": True, "effort": "high"})

    def test_jev_sees_the_threads_earlier_messages(self):
        sent = []
        pr.ask_jev = lambda text, context, timeout: sent.append(context) or jev("deep")(text, context, timeout)
        gw = FakeGateway()
        self.send(gw, event(LONG))
        self.send(gw, event("yes do it"))
        self.send(gw, event("x" * 3000))
        self.send(gw, event("thanks"))
        self.assertEqual(sent[:2], ["", LONG.strip()])  # router strips the text
        self.assertEqual(sent[2], LONG.strip() + "\n\nyes do it")
        self.assertEqual(sent[3], ("x" * 3000)[-pr.CONTEXT_CHARS:])  # capped, newest kept
        self.assertEqual(self.lines()[1]["context_chars"], len(LONG.strip()))
        self.send(gw, event("/new", command="new"))
        gw.conv.model_override = gw.conv.reasoning_override = None
        self.send(gw, event("hi"))
        self.assertEqual(sent[-1], "")  # a new thread starts without context

    def test_unsure_short_follow_up_does_not_escalate(self):
        # real Jev, offered only the rungs above a Sol thread, answered "ok 고마워, 그걸로 진행해" with hardest @0.42
        gw, pr.ask_jev = FakeGateway(), jev("deep")
        self.send(gw, event(LONG))
        pr.ask_jev = jev("hardest", 0.42)
        self.send(gw, event("ok 고마워, 그걸로 진행해"))
        self.assertEqual(gw.conv.reasoning_override, {"enabled": True, "effort": "low"})  # not medium

    def test_short_follow_up_can_go_up(self):
        gw, pr.ask_jev = FakeGateway(), jev("quick")
        self.send(gw, event("hi"))
        self.assertEqual(gw.conv.model_override, {"model": "deepseek-v4.1-flash"})
        pr.ask_jev = jev("deep", 0.5)  # unsure on a follow-up: no evidence it got harder, keep the thread's rung
        self.send(gw, event("이번 분기 로드맵 리스크 분석해줘"))
        self.assertEqual((gw.conv.model_override, gw.conv.reasoning_override),
                         ({"model": "deepseek-v4.1-flash"}, {"enabled": False}))
        self.assertEqual((self.lines()[-1]["follow_up"], self.lines()[-1]["kept"]), (True, "unsure"))  # rung 0 thread
        pr.ask_jev = jev("deep", 0.9)  # confident: up to Sol
        self.send(gw, event("이번 분기 로드맵 리스크 분석해줘"))
        self.assertEqual(gw.conv.model_override, {"model": "gpt-5.6-sol", "provider": "openai-codex"})
        self.assertEqual(gw.conv.reasoning_override, {"enabled": True, "effort": "low"})

    def test_user_model_choice_wins(self):
        gw, pr.ask_jev = FakeGateway(), jev("routine")
        self.send(gw, event(LONG))
        gw.conv.model_override = {"model": "deepseek-v4.1-flash", "provider": "custom"}  # what /model writes
        pr.ask_jev = jev("hardest")
        self.send(gw, event(LONG))
        self.assertEqual(gw.conv.model_override["model"], "deepseek-v4.1-flash")
        self.assertEqual(self.lines()[-1]["skipped"], "user_pinned")

    def test_new_thread_reset_forgets_choice(self):
        gw, pr.ask_jev = FakeGateway(), jev("deep")
        self.send(gw, event(LONG))
        self.send(gw, event("/new", command="new"))
        gw.conv.model_override = gw.conv.reasoning_override = None  # Hermes clears the conversation on /new
        pr.ask_jev = jev("quick")
        self.send(gw, event("hi"))
        self.assertEqual(gw.conv.model_override, {"model": "deepseek-v4.1-flash"})

    def test_not_routed(self):
        cases = [(FakeGateway(), event("/model glm-5.3", command="model")), (FakeGateway(), event("")),
                 (FakeGateway(running=True), event(LONG)), (FakeGateway(authorized=False), event(LONG))]
        for gw, ev in cases:
            calls = []
            pr.ask_jev = lambda *a: calls.append(a)
            self.send(gw, ev)
            self.assertEqual((calls, gw.conv.model_override), ([], None))

    def test_jev_down_changes_nothing(self):
        gw, pr.ask_jev = FakeGateway(), lambda *a: None
        self.send(gw, event(LONG))
        self.assertEqual((gw.conv.model_override, gw.conv.reasoning_override), (None, None))
        self.assertEqual(self.lines()[-1]["skipped"], "jev_unavailable")

    def test_shadow_logs_but_changes_nothing(self):
        pr._settings["mode"] = "shadow"
        gw, pr.ask_jev = FakeGateway(), jev("deep")
        self.send(gw, event(LONG))
        self.assertEqual((gw.conv.model_override, gw.conv.reasoning_override), (None, None))
        line = self.lines()[-1]
        self.assertEqual((line["mode"], line["model"], line["applied"]), ("shadow", "gpt-5.6-sol", False))

    def test_log_never_contains_message_text(self):
        gw, pr.ask_jev = FakeGateway(), jev("routine")
        self.send(gw, event("비밀 프로젝트 일정 " + LONG))
        self.send(gw, event("yes do it"))  # the first message now travels as context
        self.assertNotIn("비밀", self.log.read_text())
        self.assertNotIn("yes do it", self.log.read_text())

    def test_broken_gateway_never_raises(self):
        pr.ask_jev = jev("routine")
        asyncio.run(pr.on_dispatch(event=event(LONG), gateway=object(), session_store=None))

    def test_apply_shows_routing_status_then_clears_it(self):
        statuses = []
        adapter = SimpleNamespace(supports_status_text=True, set_status_text=lambda chat, t: statuses.append((chat, t)))
        gw, pr.ask_jev = FakeGateway(), jev("routine")
        gw.adapters = {"slack": adapter}  # the other tests' FakeGateway has none: routing works without a status
        ev = event(LONG)
        ev.source = SimpleNamespace(platform="slack", chat_id="D1")
        self.send(gw, ev)  # send_typing missing here and gateway.platforms absent in CI: swallowed, never fails routing
        self.assertEqual(statuses, [("D1", "is routing with Jev…"), ("D1", None)])
        self.assertEqual(gw.conv.model_override, {"model": "glm-5.3-flash"})

    def test_turn_end_line(self):
        pr.on_session_end(session_id="s1", turn_id="t1", model="glm-5.3-flash", completed=False, interrupted=True)
        self.assertEqual(self.lines()[-1]["interrupted"], True)
        self.assertEqual(self.lines()[-1]["session_key"], "")  # CI has no Hermes: the import fails, the line still writes

    def test_subagent_turn_end_writes_nothing(self):
        with unittest.mock.patch.object(pr, "_write", lambda e: self.fail("subagent must not log")):
            pr.on_session_end(session_id="s1", turn_id="t1", platform="subagent")

    def test_plan_follows_this_messages_jev_pick_not_the_threads_rung(self):
        gw, pr.ask_jev = FakeGateway(), jev("multi_step")
        self.send(gw, event(LONG))
        self.assertTrue(pr._threads[gw._session_key_for_source(None)]["plan"])
        pr.ask_jev = jev("routine", 0.6)  # short unsure follow-up: thread keeps multi_step, this message is not large
        self.send(gw, event("and the owners?"))
        key = gw._session_key_for_source(None)
        self.assertEqual(pr.RUNGS[pr._threads[key]["rung"]][0], "multi_step")
        self.assertFalse(pr._threads[key]["plan"])
        pr.ask_jev = jev("deep")
        self.send(gw, event(LONG))
        self.assertTrue(pr._threads[key]["plan"])
        pr.ask_jev = lambda *a: None  # skipped route: the previous message's plan never carries over
        self.send(gw, event(LONG))
        self.assertFalse(pr._threads[key]["plan"])

    def test_turn_end_carries_the_thread_key_when_hermes_provides_it(self):
        gateway, module = types.ModuleType("gateway"), types.ModuleType("gateway.session_context")
        module.get_session_env = lambda name, default="": "agent:main:slack:group:C1:T1" if name == "HERMES_SESSION_KEY" else default
        with unittest.mock.patch.dict(sys.modules, {"gateway": gateway, "gateway.session_context": module}):
            pr.on_session_end(session_id="s1", turn_id="t1")
        self.assertEqual(self.lines()[-1]["session_key"], "agent:main:slack:group:C1:T1")

    def test_footer_shows_jev_pick_and_what_ran(self):
        gateway, module = types.ModuleType("gateway"), types.ModuleType("gateway.session_context")
        env = {"HERMES_SESSION_KEY": "agent:main:slack:group:C1:T1", "HERMES_SESSION_CHAT_ID": "C1"}
        module.get_session_env = lambda name, default="": env.get(name, default)
        gw, pr.ask_jev = FakeGateway(), jev("multi_step")
        slack = SimpleNamespace()
        platform = __import__("enum").Enum("Platform", {"TELEGRAM": "telegram", "SLACK": "slack"})
        gw.adapters = {platform.TELEGRAM: SimpleNamespace(), platform.SLACK: slack}
        gw._resolve_session_reasoning_config = lambda session_key, model: gw.conv.reasoning_override or {"enabled": False}
        pr._settings["mode"] = "shadow"
        self.send(gw, event(LONG))  # shadow: Jev picks glm high, the default (no override) runs
        with unittest.mock.patch.dict(sys.modules, {"gateway": gateway, "gateway.session_context": module}):
            self.assertIsNone(pr.on_llm_output(response_text="Done.", model="glm-5.3-flash", platform="slack"))  # text untouched
            self.assertEqual(slack.pm_route_lines, {"C1": "Jev picked: glm-5.3-flash, high  ·  Ran: glm-5.3-flash, none"})
            gw.conv.reasoning_override = {"enabled": True, "effort": "high"}  # same as Jev's pick: one short line
            pr.on_llm_output(response_text="Done.", model="glm-5.3-flash", platform="slack")
            self.assertEqual(slack.pm_route_lines, {"C1": "Jev picked: glm-5.3-flash, high  ·  Ran: glm-5.3-flash, high"})
            slack.pm_route_lines.clear()
            pr.on_llm_output(response_text="Done.", model="m", platform="cli")
            pr._gateway = object()  # broken gateway: no line, reply untouched
            self.assertIsNone(pr.on_llm_output(response_text="Done.", model="m", platform="slack"))
            self.assertEqual(slack.pm_route_lines, {})

    # --- edge cases beyond the plan ---
    def test_missing_text_is_not_routed(self):
        gw, pr.ask_jev = FakeGateway(), lambda *a: self.fail("Jev must not be called")
        self.send(gw, event(None))
        self.assertEqual(gw.conv.model_override, None)

    def test_unknown_mode_acts_as_shadow(self):
        pr._settings["mode"] = "bogus"
        gw, pr.ask_jev = FakeGateway(), jev("deep")
        self.send(gw, event(LONG))
        self.assertEqual((gw.conv.model_override, gw.conv.reasoning_override), (None, None))
        self.assertFalse(self.lines()[-1]["applied"])

    def test_slow_jev_times_out_and_changes_nothing(self):
        pr._settings["timeout_s"] = 0.1  # wait_for gives it 0.1 + 0.5s
        gw = FakeGateway()

        def slow(text, opts, timeout):
            time.sleep(0.8)
            return jev("deep")(text, opts, timeout)

        pr.ask_jev = slow
        self.send(gw, event(LONG))
        self.assertEqual((gw.conv.model_override, gw.conv.reasoning_override), (None, None))
        self.assertEqual(self.lines()[-1]["skipped"], "jev_unavailable")

    def test_idle_auto_reset_without_a_command_starts_fresh(self):
        gw, pr.ask_jev = FakeGateway(), jev("deep")
        self.send(gw, event(LONG))
        gw.conv.model_override = gw.conv.reasoning_override = None  # Hermes idle auto-reset clears the overrides
        pr.ask_jev = jev("quick")
        self.send(gw, event("hi"))  # short, but a fresh thread: not a follow-up, not a user pin
        self.assertEqual(gw.conv.model_override, {"model": "deepseek-v4.1-flash"})
        self.assertNotIn("skipped", self.lines()[-1])

    def test_user_reasoning_choice_wins(self):
        gw, pr.ask_jev = FakeGateway(), jev("routine")
        self.send(gw, event(LONG))
        gw.conv.reasoning_override = {"enabled": True, "effort": "max"}  # what /reasoning writes
        pr.ask_jev = jev("quick")
        self.send(gw, event(LONG))
        self.assertEqual(gw.conv.reasoning_override, {"enabled": True, "effort": "max"})
        self.assertEqual(self.lines()[-1]["skipped"], "user_pinned")

    # --- review fixes ---
    def test_persisted_user_model_survives_restart(self):
        gw, pr.ask_jev = FakeGateway(), lambda *a: self.fail("Jev must not be called")
        gw.persisted = {"model": "deepseek-v4.1-flash", "provider": "custom"}
        self.send(gw, event(LONG))
        self.assertEqual(gw.conv.model_override, gw.persisted)
        self.assertEqual(self.lines()[-1]["skipped"], "user_pinned")

    def test_shadow_notices_session_reset_by_changed_session_id(self):
        pr._settings["mode"] = "shadow"
        gw, store, pr.ask_jev = FakeGateway(), FakeStore("s1"), jev("deep")
        self.send(gw, event(LONG), store)
        store.session_id = "s2"  # idle reset / expiry / /resume / /branch
        pr.ask_jev = jev("quick")
        self.send(gw, event("hi"), store)
        self.assertEqual(self.lines()[-1]["model"], "deepseek-v4.1-flash")
        self.assertNotIn("skipped", self.lines()[-1])

    def test_session_id_appearing_on_message_two_is_not_a_reset(self):
        pr._settings["mode"] = "shadow"
        gw, store, pr.ask_jev = FakeGateway(), FakeStore(None), jev("deep")
        self.send(gw, event(LONG), store)  # brand-new thread: Hermes has no session yet
        store.session_id = "s1"
        for _ in range(2):
            self.send(gw, event("yes do it"), store)
            self.assertTrue(self.lines()[-1]["follow_up"])
            self.assertEqual(self.lines()[-1]["pinned_before"], "gpt-5.6-sol")

    def test_new_command_alone_forgets_choice_in_shadow_mode(self):
        pr._settings["mode"] = "shadow"
        gw, pr.ask_jev = FakeGateway(), jev("deep")
        self.send(gw, event(LONG))
        self.send(gw, event("/new", command="new"))
        pr.ask_jev = jev("quick")
        self.send(gw, event("hi"))
        self.assertEqual(self.lines()[-1]["model"], "deepseek-v4.1-flash")
        self.assertNotIn("skipped", self.lines()[-1])

    def test_gateway_gets_copies_not_the_routers_records(self):
        gw, pr.ask_jev = FakeGateway(), jev("hardest")
        self.send(gw, event(LONG))
        gw.conv.model_override["model"] = "mutated"
        gw.conv.reasoning_override["effort"] = "mutated"
        record = next(iter(pr._threads.values()))
        self.assertEqual((record["model_override"]["model"], record["reasoning"]["effort"]), ("gpt-5.6-sol", "medium"))

    def test_jev_runs_on_own_pool_not_the_loops_default_executor(self):
        names = []
        gw = FakeGateway()
        pr.ask_jev = lambda *a: names.append(threading.current_thread().name) or jev("routine")(*a)
        self.send(gw, event(LONG))
        self.assertTrue(names[0].startswith("pm_router"), names)

    def test_user_model_set_during_jev_call_is_not_overwritten(self):
        gw = FakeGateway()

        def user_switches_meanwhile(text, opts, timeout):
            gw.conv.model_override = {"model": "deepseek-v4.1-flash", "provider": "custom"}
            return jev("hardest")(text, opts, timeout)

        pr.ask_jev = user_switches_meanwhile
        self.send(gw, event(LONG))
        self.assertEqual(gw.conv.model_override["model"], "deepseek-v4.1-flash")
        self.assertIsNone(gw.conv.reasoning_override)
        self.assertEqual(self.lines()[-1]["skipped"], "changed_during_jev")
        self.assertEqual(pr._threads, {})

    def test_two_messages_in_flight_first_write_wins(self):
        gw = FakeGateway()

        def jev_by_text(text, opts, timeout):
            time.sleep(0.05 if text.endswith("first") else 0.3)
            return jev("routine" if text.endswith("first") else "hardest")(text, opts, timeout)

        pr.ask_jev = jev_by_text

        async def both():
            await asyncio.gather(pr.on_dispatch(event=event(LONG + "first"), gateway=gw, session_store=None),
                                 pr.on_dispatch(event=event(LONG + "second"), gateway=gw, session_store=None))
        asyncio.run(both())
        self.assertEqual(gw.conv.model_override, {"model": "glm-5.3-flash"})
        self.assertEqual(self.lines()[-1]["skipped"], "changed_during_jev")


    # --- review round 2 ---
    def test_shadow_background_failure_is_logged_not_left_unretrieved(self):
        pr._settings["mode"] = "shadow"

        class Flaky(FakeGateway):
            calls = 0

            def _session_state(self, key):
                self.calls += 1
                if self.calls > 1:  # _route's read works, the re-read after the Jev call fails
                    raise RuntimeError("gone")
                return super()._session_state(key)

        gw, pr.ask_jev = Flaky(), jev("deep")
        with self.assertLogs("pm_router", "WARNING") as logs:
            self.send(gw, event(LONG))  # send() gathers the task: an escaping exception would raise here
        self.assertTrue(any("gone" in line for line in logs.output), logs.output)

    def test_compression_split_keeps_the_threads_routing(self):
        gw, store, pr.ask_jev = FakeGateway(), FakeStore("s1"), jev("multi_step")
        self.send(gw, event(LONG), store)
        store.session_id = "s2"  # compression split / in-place /branch: new id, Hermes keeps the overrides
        sent = []
        pr.ask_jev = lambda text, context, timeout: sent.append(context) or jev("quick", 0.9, True)(
            text, context, timeout)
        self.send(gw, event(LONG), store)
        self.assertEqual(sent[0], LONG.strip())  # the thread's context carries over the split
        self.assertEqual(gw.conv.model_override, {"model": "glm-5.3-flash"})  # continuing: kept the thread's model
        self.assertNotIn("skipped", self.lines()[-1])
        self.send(gw, event("yes do it"), store)
        self.assertTrue(self.lines()[-1]["follow_up"])  # and a short follow-up still knows the thread
        self.assertEqual(gw.conv.model_override, {"model": "glm-5.3-flash"})
        self.assertEqual(pr._threads["agent:main:slack:group:C1:T1"]["session_id"], "s2")

    def test_user_override_survives_a_session_id_change(self):
        gw, store, pr.ask_jev = FakeGateway(), FakeStore("s1"), jev("multi_step")
        self.send(gw, event(LONG), store)
        gw.conv.model_override = {"model": "deepseek-v4.1-flash", "provider": "custom"}  # user's /model
        store.session_id = "s2"
        pr.ask_jev = jev("hardest")
        self.send(gw, event(LONG), store)
        self.assertEqual(gw.conv.model_override["model"], "deepseek-v4.1-flash")
        self.assertEqual(self.lines()[-1]["skipped"], "user_pinned")


class PlanTest(unittest.TestCase):
    KEY = "agent:main:slack:group:C1:T1"

    def plan(self, flag=True, mode="apply", platform="slack", key=KEY):
        record = {"rung": len(pr.RUNGS) - 1, "plan": flag}  # the thread's rung alone never plans
        with unittest.mock.patch.object(pr, "_session_key", lambda: key), \
                unittest.mock.patch.dict(pr._settings, mode=mode), \
                unittest.mock.patch.dict(pr._threads, {self.KEY: record}):
            return pr.on_llm_call(platform=platform, session_id="s1", user_message="x")

    def test_plans_only_when_this_message_was_large(self):
        self.assertEqual(self.plan(True), {"context": pr.PLAN})
        self.assertIsNone(self.plan(False))

    def test_shadow_other_platforms_and_unknown_threads_do_not_plan(self):
        self.assertIsNone(self.plan(mode="shadow"))
        for platform in ("subagent", "cron", None):
            self.assertIsNone(self.plan(platform=platform), platform)
        self.assertIsNone(self.plan(key="agent:main:slack:group:C9:T9"))
        self.assertIsNone(self.plan(key=""))

    def test_broken_record_never_raises(self):
        with unittest.mock.patch.object(pr, "_session_key", lambda: self.KEY), \
                unittest.mock.patch.dict(pr._settings, mode="apply"), \
                unittest.mock.patch.dict(pr._threads, {self.KEY: {}}):
            self.assertIsNone(pr.on_llm_call(platform="slack"))


class RegisterTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(pr._settings.update, dict(pr._settings))

    def register(self, **config):
        ctx = SimpleNamespace(get_config=lambda k, d=None: config.get(k, d), register_hook=lambda name, cb: None)
        with self.assertLogs("pm_router", "WARNING") as logs:
            pr.register(ctx)
        return logs.output

    def test_unknown_mode_warns_and_uses_shadow(self):
        out = self.register(mode="aply")
        self.assertEqual(pr._settings["mode"], "shadow")
        self.assertTrue(any("'aply'" in line and "shadow" in line for line in out), out)

    def test_bad_timeout_warns_and_uses_default(self):
        for bad in ("fast", -1, float("nan"), float("inf"), None):
            pr._settings["timeout_s"] = 99
            out = self.register(timeout_s=bad)
            self.assertEqual(pr._settings["timeout_s"], 2.0, bad)
            self.assertTrue(any("timeout_s" in line for line in out), bad)
        self.register(timeout_s="3")
        self.assertEqual(pr._settings["timeout_s"], 3.0)

    def test_registers_hooks_only_with_key(self):
        hooks = []
        ctx = SimpleNamespace(get_config=lambda k, d=None: {"mode": "apply", "rungs": EXAMPLE_RUNGS}.get(k, d),
                              register_hook=lambda name, cb: hooks.append(name))
        old = os.environ.pop("TYPESAFE_API_KEY", None)
        try:
            pr.register(ctx)
            self.assertEqual(hooks, [])
            os.environ["TYPESAFE_API_KEY"] = "k"
            pr.register(ctx)
            self.assertEqual(hooks, ["pre_gateway_dispatch", "on_session_end", "pre_llm_call"])  # footer off by default
            self.assertEqual(pr._settings["mode"], "apply")
            hooks.clear()
            pr.register(SimpleNamespace(get_config=lambda k, d=None: {"footer": True, "rungs": EXAMPLE_RUNGS}.get(k, d),
                                        register_hook=lambda name, cb: hooks.append(name)))
            self.assertIn("transform_llm_output", hooks)
            hooks.clear()
            with self.assertLogs("pm_router", "WARNING"):
                pr.register(SimpleNamespace(get_config=lambda k, d=None: {"mode": "apply"}.get(k, d),
                                            register_hook=lambda name, cb: hooks.append(name)))
            self.assertEqual(hooks, [])  # key but no ladder: routing off
        finally:
            os.environ.pop("TYPESAFE_API_KEY", None)
            if old is not None:
                os.environ["TYPESAFE_API_KEY"] = old

    def test_empty_reply_goes_to_waiting_fallback_at_once(self):
        guard = types.ModuleType("agent.empty_response_guard")
        guard.empty_retry_budget = lambda agent, response: 3
        package = types.ModuleType("agent")
        package.empty_response_guard = guard
        with unittest.mock.patch.dict(sys.modules, {"agent": package, "agent.empty_response_guard": guard}):
            pr._fallback_on_first_empty()
            pr._fallback_on_first_empty()  # a second register doesn't wrap twice
            agent = SimpleNamespace(_fallback_chain=[{"provider": "openai-codex"}], _fallback_index=0)
            self.assertEqual(guard.empty_retry_budget(agent, None), 0)
            agent._fallback_index = 1  # chain used up: normal retries
            self.assertEqual(guard.empty_retry_budget(agent, None), 3)
            self.assertEqual(guard.empty_retry_budget(SimpleNamespace(), None), 3)  # no chain configured

    def test_fallback_notice_is_short(self):
        sent = []
        helpers = types.ModuleType("agent.chat_completion_helpers")
        helpers._buffer_fallback_notice = lambda agent, notice: sent.append(notice)
        package = types.ModuleType("agent")
        package.chat_completion_helpers = helpers
        with unittest.mock.patch.dict(sys.modules, {"agent": package, "agent.chat_completion_helpers": helpers}):
            pr._short_fallback_notice()
            pr._short_fallback_notice()  # a second register doesn't wrap twice
            helpers._buffer_fallback_notice(None, "⚠️ Model fallback: glm-5.3-flash via custom unavailable "
                                                  "(provider failure); using gpt-5.6-sol via openai-codex.")
            helpers._buffer_fallback_notice(None, "⚠️ Model fallback: gpt-5.6-sol via openai-codex unavailable "
                                                  "(rate limit); using glm-5.3-flash via custom. Primary retry "
                                                  "eligible in ~60 s; recovery is not guaranteed.")
            helpers._buffer_fallback_notice(None, "something else")
        self.assertEqual(sent, ["⚠️ glm-5.3-flash failed → using gpt-5.6-sol",
                                "⚠️ gpt-5.6-sol failed → using glm-5.3-flash", "something else"])


if __name__ == "__main__":
    unittest.main()
