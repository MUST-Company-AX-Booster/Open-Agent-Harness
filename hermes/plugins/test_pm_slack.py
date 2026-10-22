import asyncio
import importlib.util
import json
import sys
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("pm_slack", Path(__file__).parent / "pm_slack" / "__init__.py")
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)

LINK = "https://example.slack.com/archives/C03PJ3HD1EU/p1791269315370579"


class FakeSlack:
    """Stands in for ps._call: canned replies per method, records every call."""

    def __init__(self, channel=None, info_error=None):
        self.channel, self.info_error, self.calls = channel, info_error, []

    def __call__(self, method, **params):
        self.calls.append(method)
        if method == "conversations.info":
            return {"ok": False, "error": self.info_error} if self.info_error else {"ok": True, "channel": self.channel}
        if method == "conversations.join":
            return {"ok": True}
        if method in ("conversations.replies", "conversations.history"):
            return {"ok": True, "messages": [
                {"ts": "1791269315.370579", "user": "U1", "text": "hi <@U2> see <https://x.io|docs>", "reply_count": 1},
                {"ts": "1791269400.000100", "user": "U2", "text": "ok"}]}
        if method == "conversations.members":
            return {"ok": True, "members": ["U_MEMBER"]}
        if method == "users.info":
            return {"ok": True, "user": {"profile": {"real_name": {"U1": "Tony", "U2": "Lion"}[params["user"]]}}}
        raise AssertionError(method)


class PmSlackTest(unittest.TestCase):
    def use(self, **kw):
        fake = FakeSlack(**kw)
        ps._call, ps._user_names = fake, {}
        return fake

    def test_parse_link(self):
        self.assertEqual(ps.parse_link(LINK), ("C03PJ3HD1EU", "1791269315.370579", "1791269315.370579"))
        reply = LINK.replace("370579", "370999") + "?thread_ts=1791269315.370579&cid=C03PJ3HD1EU"
        self.assertEqual(ps.parse_link(reply), ("C03PJ3HD1EU", "1791269315.370999", "1791269315.370579"))
        self.assertIsNone(ps.parse_link("https://example.com/p1"))

    def test_reads_member_channel_without_joining(self):
        fake = self.use(channel={"name": "blk", "is_channel": True, "is_member": True})
        out = json.loads(ps.slack_read_link({"url": LINK}))
        self.assertNotIn("conversations.join", fake.calls)
        self.assertEqual(out["channel"], "#blk")
        first = out["messages"][0]
        self.assertEqual((first["sender"], first["text"], first["linked"]), ("Tony", "hi @Lion see docs", True))

    def test_joins_public_channel_then_reads(self):
        fake = self.use(channel={"name": "blk", "is_channel": True, "is_member": False, "is_private": False})
        self.assertIn("messages", json.loads(ps.slack_read_link({"url": LINK})))
        self.assertIn("conversations.join", fake.calls)

    def test_private_not_member_gives_invite(self):
        for kw in ({"channel": {"name": "secret", "is_member": False, "is_private": True}},
                   {"info_error": "channel_not_found"}):
            fake = self.use(**kw)
            err = json.loads(ps.slack_read_link({"url": LINK}))["error"]
            self.assertTrue(err.startswith("not_in_channel"), err)
            self.assertIn("/invite @PM-Agent", err)
            self.assertNotIn("conversations.replies", fake.calls)

    def test_private_member_channel_only_for_its_members(self):
        self.use(channel={"name": "secret", "is_member": True, "is_private": True})
        for asker, readable in (("U_MEMBER", True), ("U_OTHER", False), ("", False)):
            ps._requester = lambda: asker
            out = json.loads(ps.slack_read_link({"url": LINK}))
            self.assertEqual("messages" in out, readable, asker)

    def test_never_reads_dms(self):
        for flag in ("is_im", "is_mpim"):
            fake = self.use(channel={flag: True, "is_member": True})
            self.assertIn("direct message", json.loads(ps.slack_read_link({"url": LINK}))["error"])
            self.assertIn("direct message", json.loads(ps.slack_recent({"channel_id": "D0C5UT7M31U"}))["error"])
            self.assertFalse({"conversations.replies", "conversations.history"} & set(fake.calls))

    def test_recent_needs_membership_and_valid_id(self):
        fake = self.use(channel={"name": "blk", "is_member": False, "is_private": False})
        self.assertIn("not_in_channel", json.loads(ps.slack_recent({"channel_id": "C03PJ3HD1EU"}))["error"])
        self.assertNotIn("conversations.join", fake.calls)
        self.assertIn("error", json.loads(ps.slack_recent({"channel_id": "../x"})))
        self.use(channel={"name": "blk", "is_member": True})
        self.assertEqual(len(json.loads(ps.slack_recent({"channel_id": "C03PJ3HD1EU", "limit": "x"}))["messages"]), 2)

    def test_status_phrase(self):
        self.assertEqual(ps._status_phrase("mcp__notion__notion_fetch", {"id": "x"}), "is reading Notion…")
        self.assertEqual(ps._status_phrase("tool_call", {"name": "mcp__jira__search"}), "is checking Jira…")
        self.assertEqual(ps._status_phrase("tool_call", {"calls": [{"name": "slack_recent"}]}), "is reading Slack…")
        self.assertEqual(ps._status_phrase("tool_call", {"calls": {"name": "mcp__github__get_pr"}}), "is checking GitHub…")
        self.assertEqual(ps._status_phrase("tool_search", {"query": "x"}), "is finding the right tool…")
        self.assertIsNone(ps._status_phrase("mcp__other__thing", None))

    def test_status_goes_back_to_text_api(self):
        import sys, types
        fake = types.ModuleType("hermes_plugins.platforms__slack.adapter")
        fake._AGENT_SESSIONS_SUPPORTED = True
        sys.modules[fake.__name__] = fake
        try:
            ps._use_text_status()
            self.assertFalse(fake._AGENT_SESSIONS_SUPPORTED)
        finally:
            del sys.modules[fake.__name__]


    def test_stop_button_sends_stop_and_ends_working_state(self):
        import asyncio, re, sys, types
        base, ev = types.ModuleType("gateway.platforms.base"), types.ModuleType("gateway.platforms.event")
        base.BasePlatformAdapter = type("BasePlatformAdapter", (), {})
        ev.MessageEvent = lambda **kw: types.SimpleNamespace(**kw)
        ev.MessageType = types.SimpleNamespace(COMMAND="command")
        stubs = {"gateway": types.ModuleType("gateway"), "gateway.platforms": types.ModuleType("gateway.platforms"),
                 "gateway.platforms.base": base, "gateway.platforms.event": ev}
        saved = {k: sys.modules.get(k) for k in stubs}
        sys.modules.update(stubs)
        try:
            ps._enable_stop_button()
            ps._enable_stop_button()  # plugin reload must not wrap twice

            class FakeApp:
                def __init__(self):
                    self.listeners = []

                def event(self, kind):
                    return lambda fn: self.listeners.append((kind, fn))

                def action(self, action_id):
                    return lambda fn: None

            class SlackAdapter(base.BasePlatformAdapter):  # defined after register(), like the lazy import
                def __init__(self):
                    self._app, self.handled, self.api, self.teams = FakeApp(), [], [], []

                def _register_bolt_handlers(self):
                    self._app.event(re.compile(".*"))(None)  # the adapter's catch-all comes last

                def set_status_text(self, chat_id, text):
                    pass

                def _remember_channel_team(self, channel, team):
                    self.teams.append((channel, team))

                def build_source(self, **kw):
                    return kw

                async def handle_message(self, event):
                    self.handled.append(event)

                def _get_client(self, channel, team_id=None):
                    async def api_call(method, json):
                        self.api.append((method, json))
                    return types.SimpleNamespace(api_call=api_call)

            self.assertFalse(SlackAdapter.supports_async_delivery)  # delegate_task stays in the turn
            self.assertTrue(SlackAdapter.set_status_text._pm_slack_todo)
            a = SlackAdapter()
            a._register_bolt_handlers()
            kinds = [k for k, _ in a._app.listeners]
            self.assertEqual(kinds[0], "agent_session_stopped")
            self.assertEqual(kinds.count("agent_session_stopped"), 1)
            asyncio.run(a._app.listeners[0][1](
                {"type": "agent_session_stopped", "channel": "C1", "thread_ts": "1.2", "user": "U1"},
                {"team_id": "T1"}))
            (stop,) = a.handled
            self.assertEqual((stop.text, stop.message_type), ("/stop", "command"))
            self.assertEqual((stop.source["chat_id"], stop.source["thread_id"], stop.source["chat_type"],
                              stop.source["scope_id"]), ("C1", "1.2", "group", "T1"))
            self.assertEqual(a.api, [("agents.sessions.setStatus",
                                      {"channel_id": "C1", "thread_ts": "1.2", "status": "active"})])
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

    def test_status_sent_only_on_change_not_every_tick(self):
        sent = []

        class SlackAdapter:
            async def _set_thread_status(self, chat_id, team_id, thread_ts, status, fail_label):
                sent.append(status)

            @staticmethod
            def _default_status_text(started):
                return "still working… (1m05s)"

        ps._quiet_status(SlackAdapter)
        ps._quiet_status(SlackAdapter)  # plugin reload must not wrap twice
        a = SlackAdapter()
        for status in ["is thinking...", "is thinking...", "is checking Jira…", "is checking Jira…",
                       "", "is thinking..."]:  # clear always goes through; next turn shows again
            asyncio.run(a._set_thread_status("C1", "T1", "1.2", status, "failed"))
        self.assertEqual(sent, ["is thinking...", "is checking Jira…", "", "is thinking..."])
        self.assertEqual(SlackAdapter._default_status_text(0.0), "is thinking...")  # no ticking timer

    def test_markdown_block_breaks_broadcast_mentions(self):
        class SlackAdapter:
            def _markdown_block_payload(self, content):
                return [{"type": "markdown", "text": content}]

        ps._wrap_markdown_block(SlackAdapter)
        text = SlackAdapter()._markdown_block_payload("hi <!channel> and @here, mail a@here.io, <!subteam^S1>")[0]["text"]
        self.assertEqual(text, "hi @\u200bchannel and @\u200bhere, mail a@here.io, <!subteam^S1>")

    def test_checklist_items_and_text(self):
        todos = lambda *st: json.dumps({"todos": [
            {"content": "Read  Notion\nroadmap", "status": st[0]}, {"content": "Check <!channel> Jira", "status": st[1]},
            {"content": "dropped", "status": "cancelled"}, {"content": "Write answer", "status": st[2]}]})
        items = ps._checklist_items(todos("completed", "in_progress", "pending"))
        self.assertEqual(items, [("completed", "Read Notion roadmap"), ("in_progress", "Check <!channel> Jira"),
                                 ("pending", "Write answer")])
        lines = ps._render("Plan", items).split("\n")
        self.assertEqual(lines[:4], ["*Plan · 1/3 done*", "✓ Read Notion roadmap", "✱ Check &lt;!channel&gt; Jira", "○ Write answer"])
        self.assertTrue(lines[4].startswith("_todos as of ") and lines[4].endswith("M_"))
        self.assertIsNone(ps._checklist_items(json.dumps({"todos": []})))
        self.assertIsNone(ps._checklist_items("not json"))

    def test_checklist_posted_edited_then_folded_and_toggled(self):
        calls, old = [], ps._call
        ps._call = lambda method, **p: calls.append((method, json.loads(p["blocks"])[0])) or {"ok": True, "ts": "9.9"}
        text = "*Steps · 1/1 done*\n✓ Checking Jira\n_todos as of 4:17 PM_"
        try:
            ps._show_checklist("C1", "1.2", "*Steps · 0/1 done*\n✱ Checking Jira")
            ps._show_checklist("C1", "1.2", text)
            ps._finish_checklist(("C1", "1.2"))
            body = {"channel": {"id": "C1"}, "message": {"ts": "9.9"}}
            ack = lambda: asyncio.sleep(0)
            show = calls[-1][1]["accessory"]
            asyncio.run(ps._toggle_steps(ack, body, show))
            asyncio.run(ps._toggle_steps(ack, body, calls[-1][1]["accessory"]))
        finally:
            ps._call = old
            ps._checklists.clear()
        methods = [m for m, _ in calls]
        self.assertEqual(methods, ["chat.postMessage", "chat.update", "chat.update", "chat.update", "chat.update"])
        self.assertNotIn("accessory", calls[1][1])  # while working: whole list, no button
        self.assertEqual(calls[2][1]["text"]["text"], "*Steps · 1/1 done*")  # turn over: folded to its heading
        self.assertEqual((show["action_id"], show["text"]["text"], show["value"]), ("pm_steps_show", "Show steps", text))
        self.assertEqual(calls[3][1]["text"]["text"], text)  # Show steps: whole list again, with Hide
        self.assertEqual(calls[3][1]["accessory"]["action_id"], "pm_steps_hide")
        self.assertEqual(calls[4][1]["text"]["text"], "*Steps · 1/1 done*")  # Hide steps: folded
        self.assertNotIn(("C1", "1.2"), ps._checklists)

    def test_steps_checklist_until_a_plan_takes_over(self):
        shown, submit = [], ps._slack_pool.submit
        ps._slack_pool.submit = lambda fn, *a: shown.append(a[-1]) if fn is ps._show_checklist else None
        ps._chat_id, ps._thread, saved = (lambda: "C1"), (lambda: "1.2"), (ps._chat_id, ps._thread)
        try:
            ps.on_tool_start(tool_name="mcp__jira__search", args={"jql": "secret"})
            ps.on_tool_done(tool_name="mcp__jira__search", args={"jql": "secret"}, result="{}")
            ps.on_tool_start(tool_name="skill_view", args={})  # no plain-words label: not a row
            ps.on_tool_start(tool_name="tool_call", args={"name": "mcp__notion__notion-fetch"})
            ps.on_tool_done(tool_name="todo_list", result=json.dumps({"todos": [{"content": "A", "status": "pending"}]}))
            ps.on_tool_start(tool_name="mcp__jira__search", args={})  # a plan is showing: steps stop
            ps.on_turn_end(platform="slack")
            self.assertNotIn(("C1", "1.2"), ps._steps)
        finally:
            ps._slack_pool.submit, (ps._chat_id, ps._thread) = submit, saved
            ps._checklists.clear()
        rows = [t.split("\n")[:-1] for t in shown]  # drop the "todos as of" line
        self.assertEqual(rows, [["*Steps · 0/1 done*", "✱ Checking Jira"],
                                ["*Steps · 1/1 done*", "✓ Checking Jira"],
                                ["*Steps · 1/2 done*", "✓ Checking Jira", "✱ Reading Notion"],
                                ["*Plan · 0/1 done*", "○ A"]])

    def test_em_dashes_become_commas(self):
        sent = []

        class SlackAdapter:
            async def send(self, chat_id, content, reply_to=None, metadata=None):
                sent.append(content)

        ps._no_em_dashes(SlackAdapter)
        ps._no_em_dashes(SlackAdapter)  # plugin reload must not wrap twice
        asyncio.run(SlackAdapter().send(chat_id="C1", content="fix 524 timeouts — Huzaifa · Oct 10"))
        asyncio.run(SlackAdapter().send("C1", "a—b"))
        self.assertEqual(sent, ["fix 524 timeouts, Huzaifa · Oct 10", "a, b"])

    def test_route_footer_goes_under_the_reply_once(self):
        posted = []

        class SlackAdapter:
            async def _call_with_block_fallback(self, client_fn, method, kwargs, verb):
                posted.append(kwargs.get("blocks"))

        ps._route_footer(SlackAdapter)
        ps._route_footer(SlackAdapter)  # plugin reload must not wrap twice
        a, md = SlackAdapter(), [{"type": "markdown", "text": "hi"}]
        a.pm_route_lines = {"C1": "Jev picked: m, high  ·  Ran: m, high"}
        asyncio.run(a._call_with_block_fallback(None, "chat_update", {"channel": "C1", "blocks": md}, "edit"))
        asyncio.run(a._call_with_block_fallback(None, "chat_postMessage", {"channel": "C1", "blocks": md}, "send"))
        asyncio.run(a._call_with_block_fallback(None, "chat_postMessage", {"channel": "C1", "blocks": md}, "send"))
        footer = {"type": "context", "elements": [{"type": "plain_text", "text": "Jev picked: m, high  ·  Ran: m, high"}]}
        self.assertEqual(posted, [md, [*md, footer], md])  # only the reply gets it, once
        self.assertEqual(md, [{"type": "markdown", "text": "hi"}])  # caller's blocks not mutated

    def test_todo_phrase(self):
        def todos(*statuses, content="Read Notion"):
            return json.dumps({"todos": [{"id": str(i), "content": f"{content} {i}", "status": st}
                                         for i, st in enumerate(statuses)], "revision": 1})
        self.assertEqual(ps._todo_phrase(todos("completed", "in_progress", "pending")), "is on step 2/3: Read Notion 1")
        self.assertEqual(ps._todo_phrase(todos("completed", "pending", "pending")), "is on step 2/3: Read Notion 1")
        self.assertIsNone(ps._todo_phrase(todos("completed", "completed")))
        self.assertEqual(ps._todo_phrase(todos("cancelled", "in_progress", "pending")), "is on step 1/2: Read Notion 1")
        long = ps._todo_phrase(todos("in_progress", content="Read every\nNotion page about the launch plan"))
        self.assertEqual((len(long), long[-1]), (49, "…"))
        self.assertNotIn("\n", long)
        for bad in ("not json", None, "{}", json.dumps({"todos": None})):
            self.assertIsNone(ps._todo_phrase(bad))

    def _keep_todo_state(self):
        for name in ("_chat_id", "_todo_status", "_adapters"):
            self.addCleanup(setattr, ps, name, getattr(ps, name))
        ps._adapters = set()

    class Adapter:
        def __init__(self):
            self.shown = {}

        def set_status_text(self, chat_id, text):  # like upstream: None clears the store
            if text is None:
                self.shown.pop(chat_id, None)
            else:
                self.shown[chat_id] = text

    def test_todo_step_wins_status_line(self):
        self._keep_todo_state()
        Adapter = type("Adapter", (self.Adapter,), {})
        ps._wrap_status_text(Adapter)
        ps._wrap_status_text(Adapter)  # idempotent
        a, ps._todo_status = Adapter(), {"C1": "is on step 1/2: x"}
        a.set_status_text("C1", None)
        self.assertEqual(a.shown["C1"], "is on step 1/2: x")
        a.set_status_text("C1", "is reading Notion…")
        self.assertEqual(a.shown["C1"], "is on step 1/2: x")
        a.set_status_text("C2", "is reading Notion…")
        self.assertEqual(a.shown["C2"], "is reading Notion…")

    def test_turn_end_clears_shown_step(self):
        self._keep_todo_state()
        Adapter = type("Adapter", (self.Adapter,), {})
        ps._wrap_status_text(Adapter)
        ps._chat_id, ps._todo_status = (lambda: "C1"), {"C1": "is on step 4/5: x"}
        a = Adapter()
        a.set_status_text("C1", None)  # tool-completion reset replaced by the step
        self.assertEqual(a.shown, {"C1": "is on step 4/5: x"})
        ps.on_turn_end(platform="slack")
        self.assertEqual((a.shown, ps._todo_status), ({}, {}))

    def test_on_tool_done_tracks_only_todo_list(self):
        self._keep_todo_state()
        ps._chat_id, ps._todo_status = (lambda: "C1"), {}
        result = json.dumps({"todos": [{"id": "1", "content": "Plan", "status": "in_progress"}]})
        ps.on_tool_done(tool_name="mcp__notion__fetch", result=result)
        self.assertEqual(ps._todo_status, {})
        ps.on_tool_done(tool_name="todo_list", args={"merge": True}, result=result)
        self.assertEqual(ps._todo_status, {"C1": "is on step 1/1: Plan"})
        ps.on_turn_end(platform="subagent")  # delegate_task child finishing inside the turn
        self.assertEqual(ps._todo_status, {"C1": "is on step 1/1: Plan"})
        ps.on_turn_end(platform="slack")
        self.assertEqual(ps._todo_status, {})
        ps.on_tool_done(tool_name="todo_list", result=result)
        ps.on_tool_done(tool_name="todo_list", result=json.dumps({"todos": []}))
        self.assertEqual(ps._todo_status, {})

    def test_subagent_todo_list_leaves_parent_step(self):
        self._keep_todo_state()
        ps._chat_id, ps._todo_status = (lambda: "C1"), {"C1": "is on step 1/3: Plan"}
        child = json.dumps({"todos": [{"id": "1", "content": "Child step", "status": "in_progress"}]})
        in_subagent, ps._in_subagent = ps._in_subagent, (lambda: True)
        try:
            ps.on_tool_done(tool_name="todo_list", result=child)
            ps.on_turn_end(platform="slack")
        finally:
            ps._in_subagent = in_subagent
        self.assertEqual(ps._todo_status, {"C1": "is on step 1/3: Plan"})

    def test_plan_card_shows_helper_progress_and_never_reposts(self):
        self._keep_todo_state()
        for name in ("_thread", "_in_subagent", "_call"):
            self.addCleanup(setattr, ps, name, getattr(ps, name))
        self.addCleanup(setattr, ps._slack_pool, "submit", ps._slack_pool.submit)
        for state in (ps._checklists, ps._plans, ps._research):
            self.addCleanup(state.clear)
        calls, child = [], [False]
        ps._call = lambda method, **p: calls.append((method, p["text"])) or {"ok": True, "ts": "9.9"}
        ps._slack_pool.submit = lambda fn, *a: fn(*a)  # run Slack calls inline
        ps._chat_id, ps._thread, ps._in_subagent = (lambda: "C1"), (lambda: "1.2"), (lambda: child[0])
        rows = lambda: [line for line in calls[-1][1].split("\n") if line.startswith(("✓ R", "✱ R"))]

        ps.on_helper_start()  # helpers before any plan: no card to show them on
        self.assertEqual(calls, [])
        ps.on_tool_done(tool_name="todo_list", result=json.dumps({"todos": [{"content": "A", "status": "in_progress"}]}))
        for _ in range(3):
            ps.on_helper_start()
        self.assertEqual(rows(), ["✱ Research: 0/3 helpers done"])
        ps._research[("C1", "1.2")]["at"] -= 60  # a minute later
        child[0] = True
        ps.on_tool_start(tool_name="tool_call", args={"name": "mcp__jira__jira_search"})
        self.assertEqual(rows(), ["✱ Research: 0/3 helpers done · Checking Jira"])
        edits = len(calls)
        ps.on_tool_start(tool_name="mcp__notion__notion_fetch", args={})  # within 15s: kept for the next edit
        self.assertEqual(len(calls), edits)
        ps.on_turn_end(platform="subagent")
        self.assertEqual(rows(), ["✱ Research: 1/3 helpers done · Reading Notion"])
        for _ in range(3):  # one too many: never past 3/3
            ps.on_turn_end(platform="subagent")
        self.assertEqual(rows(), ["✓ Research: 3/3 helpers done"])
        ps.on_helper_start()  # a second batch whose helper's turn end never fires
        self.assertEqual(rows(), ["✱ Research: 3/4 helpers done · Reading Notion"])
        child[0] = False
        ps.on_tool_done(tool_name="delegate_task", args={}, result="{}")
        self.assertEqual(rows(), ["✓ Research: 4/4 helpers done"])
        ps.on_turn_end(platform="slack")
        posts = [m for m, _ in calls].count("chat.postMessage")
        child[0] = True  # a helper event queued behind the turn's end
        ps.on_turn_end(platform="subagent")
        ps.on_helper_start()
        self.assertEqual(([m for m, _ in calls].count("chat.postMessage"), posts), (1, 1))
        self.assertEqual((ps._plans, ps._research), ({}, {}))

    def test_reasoning_only_reply_skips_promotion_once(self):
        import inspect
        import types

        class Agent:
            def _extract_reasoning(self, message):
                return "private notes"

        def finish_text_response(agent, *, assistant_message, finish_reason, messages):
            """Like upstream: the promotion call first, then later calls (stored row, continue prompt)."""
            seen = []
            content = assistant_message.content
            if finish_reason == "stop" and not assistant_message.tool_calls and not (content or "").strip():
                seen.append(agent._extract_reasoning(assistant_message))
            seen.append(agent._extract_reasoning(assistant_message))
            if messages == "boom":
                raise RuntimeError(messages)
            return seen

        loop = types.ModuleType("agent.conversation_loop")
        loop.finish_text_response = finish_text_response
        pkg = types.ModuleType("agent")
        pkg.conversation_loop = loop
        saved = {name: sys.modules.get(name) for name in ("agent", "agent.conversation_loop")}
        sys.modules.update({"agent": pkg, "agent.conversation_loop": loop})
        try:
            ps._no_reasoning_as_reply()
            patched = loop.finish_text_response
            ps._no_reasoning_as_reply()  # idempotent
            self.assertIs(loop.finish_text_response, patched)
            self.assertEqual(inspect.signature(patched), inspect.signature(finish_text_response))  # _run_phase reads it
            self.assertEqual(patched.__name__, "finish_text_response")

            agent, msg = Agent(), lambda content, tools=None: types.SimpleNamespace(content=content, tool_calls=tools)
            run = lambda m, reason="stop", messages=None: patched(agent, assistant_message=m, finish_reason=reason,
                                                                  messages=messages)
            self.assertEqual(run(msg(None)), [None, "private notes"])  # not promoted; later calls get the real text
            self.assertEqual(run(msg("  ")), [None, "private notes"])
            self.assertEqual(run(msg("hi")), ["private notes"])
            self.assertEqual(run(msg(None, [{"id": "1"}])), ["private notes"])
            self.assertEqual(run(msg(None), reason="length"), ["private notes"])
            with self.assertRaises(RuntimeError):
                run(msg(None), messages="boom")
            self.assertNotIn("_extract_reasoning", vars(agent))
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    def test_dispatch_clears_step_only_between_turns(self):
        class Gateway:
            running = True
            def _session_key_for_source(self, source): return "k"
            def _is_session_running(self, key): return self.running
        class Event:
            class source: chat_id = "C1"
        self._keep_todo_state()
        gateway, ps._todo_status = Gateway(), {"C1": "is on step 1/3: Plan"}
        asyncio.run(ps.on_dispatch(event=Event(), gateway=gateway))  # "still working?" mid-turn
        self.assertEqual(ps._todo_status, {"C1": "is on step 1/3: Plan"})
        gateway.running = False
        asyncio.run(ps.on_dispatch(event=Event(), gateway=gateway))
        self.assertEqual(ps._todo_status, {})


if __name__ == "__main__":
    unittest.main()
