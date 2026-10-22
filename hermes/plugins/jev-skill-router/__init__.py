"""jev-skill-router — name the skills that fit the turn, before the model call.

Typed routing via TypeSafe Jev (System One) on the Vercel AI Gateway: before the
model call, Jev picks the skills (up to `shortlist`) from the live roster that fit and the hook injects
a single ``<skill_relevance>`` line into the user-message context. Silence when
nothing fits. Opt-in (``mode: off`` by default), fail-open on every error path,
one JSON line logged per decision. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .jev_router import client as _client_mod
from .jev_router import roster as _roster_mod
from .jev_router import router as _router_mod

logger = logging.getLogger(__name__)

COMMAND = "jev-skill-router"
HOOK = "pre_llm_call"
ROSTER_TTL_S = 300  # installs change under us; re-scan at most this often

_CLI_HELP = "Typed skill routing via Jev (on|off|auto|status|suggest|check)"
_CLI_DESCRIPTION = (
    "Names the skills from the live roster that fit the current turn, "
    "via TypeSafe Jev on the Vercel AI Gateway. Says nothing when nothing fits."
)

# Bound by register(); the CLI handler runs without a ctx argument.
_CTX = None

# In-process roster cache: installs change under us, so entries expire quickly.
_roster_cache: dict = {"at": 0.0, "key": None, "skills": []}


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def _settings(ctx) -> dict:
    """Read plugin settings with the ticket defaults. Never raises."""

    def num(key, default, cast):
        try:
            return cast(ctx.get_config(key, default))
        except (TypeError, ValueError):
            return default

    return {
        "mode": str(ctx.get_config("mode", "off") or "off").strip().lower(),
        # pm-agent patch: shadow decides and logs off the turn's path and injects nothing
        "shadow": str(ctx.get_config("shadow", False)).strip().lower() in ("true", "1", "yes", "on"),
        "gate": num("gate", 0.30, float),
        "fits": num("fits", 0.40, float),
        "shortlist": num("shortlist", 3, int),
        "chunk": num("chunk", 240, int),
        "excerpt": num("excerpt", 700, int),
        "timeout_s": num("timeout_s", 4.0, float),
        "cache_seconds": num("cache_seconds", 300, int),
        "suggest_chars": num("suggest_chars", 4000, int),
        "backend": str(ctx.get_config("backend", "auto") or "auto"),
        "typesafe_model": str(ctx.get_config("typesafe_model", "jev-latest") or "jev-latest"),
        "typesafe_base_url": str(
            ctx.get_config("typesafe_base_url", "https://api.typesafe.ai")
            or "https://api.typesafe.ai"
        ),
        "retry_max_wait_s": num("retry_max_wait_s", 2.0, float),
        "breaker_threshold": num("breaker_threshold", 3, int),
        "breaker_cooldown_s": num("breaker_cooldown_s", 120, float),
        "min_interval_s": num("min_interval_s", 0.25, float),
        "jev_model": str(ctx.get_config("jev_model", "typesafe-ai/jev") or "typesafe-ai/jev"),
        "jev_base_url": str(
            ctx.get_config("jev_base_url", "https://ai-gateway.vercel.sh/v4/ai")
            or "https://ai-gateway.vercel.sh/v4/ai"
        ),
        "roster_dir": str(ctx.get_config("roster_dir", "") or ""),
        "log_path": str(ctx.get_config("log_path", "") or ""),
    }


def roster_dir(settings: dict) -> Path:
    if settings["roster_dir"]:
        return Path(settings["roster_dir"]).expanduser()
    return _hermes_home() / "skills"


def log_file(settings: dict) -> Path:
    if settings["log_path"]:
        return Path(settings["log_path"]).expanduser()
    return _hermes_home() / "logs" / "jev-skill-router.log"


def log_decision(settings: dict, entry: dict) -> None:
    """Append one JSON line; never raise (logging must never break a turn)."""
    try:
        path = log_file(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.debug("jev-skill-router: log write failed: %s", exc)


def get_roster(settings: dict) -> list:
    """Live roster, cached in-process with a short TTL. Never raises."""
    key = str(roster_dir(settings))
    now = time.monotonic()
    if _roster_cache["key"] == key and now - _roster_cache["at"] < ROSTER_TTL_S:
        return _roster_cache["skills"]
    try:
        skills = _roster_mod.load_roster([Path(key)])
    except Exception as exc:
        logger.debug("jev-skill-router: roster scan failed: %s", exc)
        skills = []
    _roster_cache.update(at=now, key=key, skills=skills)
    return skills


def _skip_reason(text: str, suggest_chars: int) -> str | None:
    return _router_mod.skip_reason(text, suggest_chars)


def decide(settings: dict, text: str, *, client=None, source: str = "hook"):
    """Run one routing decision. Returns (Result|None, info). Never raises."""
    base = {"source": source, "mode": settings.get("mode", "?")}
    try:
        reason = _skip_reason(text, int(settings.get("suggest_chars", 4000) or 0))
        if reason:
            log_decision(settings, {**base, "outcome": "silent", "reason": reason})
            return None, {"outcome": "silent", "reason": reason}
        skills = get_roster(settings)
        if len(skills) < 2:
            log_decision(
                settings, {**base, "outcome": "silent", "reason": "roster_small",
                           "roster": len(skills)}
            )
            return None, {"outcome": "silent", "reason": "roster_small"}
        live = client
        if live is None:
            live = _client_mod.JevClient(
                base_url=settings["jev_base_url"],
                model=settings["jev_model"],
                timeout=float(settings["timeout_s"]),
                cache_seconds=int(settings.get("cache_seconds", 300)),
                backend=str(settings.get("backend", "auto")),
                typesafe_model=str(settings.get("typesafe_model", "jev-latest")),
                typesafe_base_url=str(
                    settings.get("typesafe_base_url", "https://api.typesafe.ai")
                ),
                retry_max_wait_s=float(settings.get("retry_max_wait_s", 2.0)),
                breaker_threshold=int(settings.get("breaker_threshold", 3)),
                breaker_cooldown_s=float(settings.get("breaker_cooldown_s", 120)),
                min_interval_s=float(settings.get("min_interval_s", 0.25)),
            )
        result = _router_mod.suggest(
            live,
            text.strip(),
            skills,
            shortlist=int(settings["shortlist"]),
            excerpt=int(settings["excerpt"]),
            gate_threshold=float(settings["gate"]),
            fits_threshold=float(settings["fits"]),
            chunk=int(settings["chunk"]),
        )
        if result is None:
            log_decision(settings, {**base, "outcome": "silent", "reason": "no_fit"})
            return None, {"outcome": "silent", "reason": "no_fit"}
        log_decision(
            settings,
            {**base, "outcome": "suggested", "skill": result.skill, "skills": result.skills,
             "gate": result.gate,
             "probability": result.probability, "confidence": result.confidence,
             "latency_ms": result.latency_ms, "cost": result.cost, "calls": result.calls},
        )
        return result, {"outcome": "suggested"}
    except Exception as exc:
        logger.debug("jev-skill-router: decision failed (fail-open): %s", exc)
        try:
            log_decision(settings, {**base, "outcome": "error", "error": str(exc)})
        except Exception:
            pass
        return None, {"outcome": "error", "error": str(exc)}


def make_hook_handler(ctx):
    """Build the pre_llm_call handler. It reads user_message, never raises."""

    def on_pre_llm_call(**kwargs):
        try:
            settings = _settings(ctx)
            if settings["mode"] == "off":
                return None
            if settings["mode"] == "auto" and not _client_mod.resolve_backend(
                settings.get("backend", "auto")
            ):
                return None  # auto routes only when a key for the backend is present
            if settings["mode"] not in ("auto", "on"):
                return None
            text = kwargs.get("user_message")
            if not isinstance(text, str) or not text.strip():
                return None
            if settings["shadow"]:  # pm-agent patch
                threading.Thread(target=decide, args=(settings, text), kwargs={"source": "shadow"},
                                 daemon=True).start()
                return None
            result, _ = decide(settings, text, source="hook")
            if result is None:
                return None
            return {"context": _router_mod.block(*result.skills)}
        except Exception as exc:  # a broken router must never break a turn
            logger.debug("jev-skill-router: hook error (fail-open): %s", exc)
            return None

    return on_pre_llm_call


def setup_cli(subparser: argparse.ArgumentParser) -> None:
    subs = subparser.add_subparsers(dest="jev_skill_router_action")
    subs.add_parser("on", help="Turn routing on (every eligible turn asks Jev)")
    subs.add_parser("off", help="Turn routing off (default; nothing leaves the machine)")
    subs.add_parser("auto", help="Route only when AI_GATEWAY_API_KEY is present")
    subs.add_parser("status", help="Show mode, roster, thresholds, endpoint, log path")
    suggest = subs.add_parser("suggest", help="Run one live routing decision on <text>")
    suggest.add_argument("text", help="The request text to route")
    suggest.add_argument("--json", dest="as_json", action="store_true",
                         help="Print the decision as JSON (for scripting)")
    subs.add_parser("check", help="Verify key, roster and log writability (no inference spent)")


def _cmd_status(ctx, settings: dict) -> int:
    skills = get_roster(settings)
    backend = _client_mod.resolve_backend(settings.get("backend", "auto"))
    gw = "present" if _client_mod.api_key() else "missing"
    ts = "present" if _client_mod.typesafe_api_key() else "missing"
    print(f"mode:          {settings['mode']}")
    print(f"roster:        {len(skills)} skills from {roster_dir(settings)}")
    print(f"gate/fits:     {settings['gate']}/{settings['fits']}")
    print(f"shortlist:     {settings['shortlist']}  chunk: {settings['chunk']}  "
          f"excerpt: {settings['excerpt']}")
    print(f"timeout:       {settings['timeout_s']}s  suggest_chars: {settings['suggest_chars']}")
    print(f"backend:       {settings.get('backend', 'auto')} (resolved: {backend or 'silent'})")
    print(f"endpoint:      {settings['jev_model']} @ {settings['jev_base_url']}")
    print(f"typesafe:      {settings.get('typesafe_model', 'jev-latest')} @ {settings.get('typesafe_base_url', 'https://api.typesafe.ai')}")
    print(f"keys:          TYPESAFE_API_KEY={ts}  AI_GATEWAY_API_KEY={gw}")
    print(f"log:           {log_file(settings)}")
    return 0


def _cmd_suggest(ctx, settings: dict, args) -> int:
    as_json = bool(getattr(args, "as_json", False))
    result, info = decide(settings, args.text, source="cli-suggest")
    if result is None:
        if as_json:
            print(json.dumps({"skill": None, **info}, ensure_ascii=False))
        else:
            print(f"no suggestion ({info.get('reason', info.get('outcome', '?'))})")
        return 0
    if as_json:
        print(json.dumps({
            "skill": result.skill, "skills": result.skills, "gate": result.gate,
            "probability": result.probability, "confidence": result.confidence,
            "latency_ms": result.latency_ms, "cost": result.cost, "calls": result.calls,
        }, ensure_ascii=False))
    else:
        print(f"suggested: {', '.join(result.skills)} "
              f"(gate={result.gate:.2f} p={result.probability:.2f} "
              f"{result.latency_ms}ms, {result.calls} calls)")
    return 0


def _cmd_check(ctx, settings: dict) -> int:
    ok = True
    key = _client_mod.resolve_backend(settings.get("backend", "auto"))
    print(f"backend: {'OK (' + key + ')' if key else 'SILENT (no key for backend ' + str(settings.get('backend', 'auto')) + ')'}")
    ok = ok and bool(key)
    try:
        skills = get_roster(settings)
        print(f"roster: OK ({len(skills)} skills from {roster_dir(settings)})")
    except Exception as exc:
        print(f"roster: FAIL ({exc})")
        ok = False
    try:
        path = log_file(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(path.parent, os.W_OK)
        print(f"log:    {'OK (writable ' + str(path) + ')' if writable else 'FAIL (not writable ' + str(path) + ')'}")
        ok = ok and writable
    except Exception as exc:
        print(f"log:    FAIL ({exc})")
        ok = False
    return 0 if ok else 1


def run_cli(ctx, args: argparse.Namespace) -> int:
    action = getattr(args, "jev_skill_router_action", None)
    if action in ("on", "off", "auto"):
        try:
            ctx.set_config("mode", action)
        except Exception as exc:
            print(f"could not set mode: {exc}")
            return 1
        print(f"jev-skill-router: mode -> {action}")
        return 0
    try:
        settings = _settings(ctx)
    except Exception as exc:
        print(f"could not read settings: {exc}")
        return 1
    if action == "status":
        return _cmd_status(ctx, settings)
    if action == "suggest":
        return _cmd_suggest(ctx, settings, args)
    if action == "check":
        return _cmd_check(ctx, settings)
    print("Usage: hermes jev-skill-router {on|off|auto|status|suggest <text> [--json]|check}")
    return 2


def _cli_command(args: argparse.Namespace) -> int:
    return run_cli(_CTX, args)


def register(ctx) -> None:
    """Wire the hook and the CLI command (called once by the plugin loader)."""
    global _CTX
    _CTX = ctx
    ctx.register_hook(HOOK, make_hook_handler(ctx))
    ctx.register_cli_command(COMMAND, _CLI_HELP, setup_cli, _cli_command,
                             description=_CLI_DESCRIPTION)
