"""Jev client — TypeSafe System One, direct or via the Vercel AI Gateway.

Pure stdlib. Backend selection (`backend: auto|typesafe|gateway`, default
`auto`): TypeSafe direct when TYPESAFE_API_KEY is present, else the gateway
when AI_GATEWAY_API_KEY is present, else silent. Every failure path returns
None (fail-open).
"""

from __future__ import annotations

import email.utils
import hashlib
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request
from datetime import timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://ai-gateway.vercel.sh/v4/ai"
DEFAULT_MODEL = "typesafe-ai/jev"
DEFAULT_TYPESAFE_BASE_URL = "https://api.typesafe.ai"
DEFAULT_TYPESAFE_MODEL = "jev-latest"
PROTOCOL_VERSION = "0.0.1"
SPEC_VERSION = "4"

RATE_LIMIT_CODES = (429, 529)

# Cap for the per-process response cache: distinct turns each insert one
# entry, so size must be bounded even though entries also expire by TTL.
CACHE_MAX_ENTRIES = 256

# Per-process state, keyed by endpoint URL: clients are rebuilt per decision,
# but pacing and breaker must survive that.
_PACE_LAST: dict[str, float] = {}  # endpoint -> monotonic time of last attempt
_BREAKER: dict[str, list] = {}  # endpoint -> [consecutive 429/529s, open-until]


def _env_key(name: str) -> str:
    key = os.environ.get(name, "").strip()
    if key:
        return key
    env_file = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / ".env"
    try:
        m = re.search(
            rf'^{name}=["\']?([^"\'\n]+)', env_file.read_text(), re.MULTILINE
        )
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


def api_key() -> str:
    """The gateway key from the environment, falling back to <HERMES_HOME>/.env."""
    return _env_key("AI_GATEWAY_API_KEY")


def typesafe_api_key() -> str:
    """The TypeSafe direct key, same lookup order as the gateway key."""
    return _env_key("TYPESAFE_API_KEY")


def resolve_backend(backend: str = "auto") -> str | None:
    """Pick 'typesafe' | 'gateway' | None (no key for the wanted backend)."""
    want = (backend or "auto").strip().lower()
    if want not in ("auto", "typesafe", "gateway"):
        want = "auto"
    has_ts, has_gw = bool(typesafe_api_key()), bool(api_key())
    if want == "typesafe":
        return "typesafe" if has_ts else None
    if want == "gateway":
        return "gateway" if has_gw else None
    if has_ts:
        return "typesafe"
    return "gateway" if has_gw else None


def to_typesafe_questions(questions: dict) -> dict:
    """Internal boolean -> TypeSafe noul; choice/score pass through."""
    out = {}
    for qid, question in (questions or {}).items():
        question = dict(question or {})
        if question.get("type") == "boolean":
            out[qid] = {
                "type": "noul",
                "instructions": question.get("instructions", ""),
            }
            if "criteria" in question:
                out[qid]["criteria"] = question["criteria"]
        else:
            out[qid] = question
    return out


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_typesafe_answers(answers: dict) -> dict:
    """TypeSafe noul -> internal {probability}; choice/score already internal."""
    out = {}
    for qid, answer in (answers or {}).items():
        if not isinstance(answer, dict):
            continue
        if answer.get("type") == "noul":
            out[qid] = {"type": "boolean", "probability": _num(answer.get("noul"))}
        else:
            out[qid] = answer
    return out


def typesafe_confidence(answers: dict) -> dict:
    """Per-answer inline confidence (noul answers carry none)."""
    confidence = {}
    for qid, answer in (answers or {}).items():
        if isinstance(answer, dict) and "confidence" in answer:
            confidence[qid] = _num(answer["confidence"])
    return confidence


def retry_after_s(value, now_wall: float) -> float | None:
    """Parse a Retry-After header: seconds or an HTTP date. Garbage -> None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        wait = float(text)
    except ValueError:
        pass
    else:
        return wait if math.isfinite(wait) else None
    try:
        moment = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    delta = moment.timestamp() - now_wall
    return delta if math.isfinite(delta) else None


class JevClient:
    """One call = one request = typed answers (boolean / choice / score)."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 4.0,
        cache_seconds: int = 300,
        backend: str = "auto",
        typesafe_model: str = DEFAULT_TYPESAFE_MODEL,
        typesafe_base_url: str = DEFAULT_TYPESAFE_BASE_URL,
        retry_max_wait_s: float = 2.0,
        breaker_threshold: int = 3,
        breaker_cooldown_s: float = 120,
        min_interval_s: float = 0.25,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = float(timeout)
        self.cache_seconds = int(cache_seconds)
        self.backend = backend
        self.typesafe_model = typesafe_model
        self.typesafe_base_url = typesafe_base_url.rstrip("/")
        self.retry_max_wait_s = float(retry_max_wait_s)
        self.breaker_threshold = int(breaker_threshold)
        self.breaker_cooldown_s = float(breaker_cooldown_s)
        self.min_interval_s = float(min_interval_s)
        self._cache: dict[str, tuple[float, dict]] = {}
        # Seams for offline tests (transport stub, clock mock).
        self._urlopen = urllib.request.urlopen
        self._clock = time.monotonic
        self._sleep = time.sleep

    def _resolve(self) -> tuple[str | None, str | None]:
        backend = resolve_backend(self.backend)
        if backend == "typesafe":
            return backend, typesafe_api_key()
        if backend == "gateway":
            return backend, api_key()
        return None, None

    def _breaker_open(self, endpoint: str) -> bool:
        state = _BREAKER.get(endpoint)
        return (
            state is not None
            and state[0] >= self.breaker_threshold
            and self._clock() < state[1]
        )

    def _note_ratelimit(self, endpoint: str) -> None:
        state = _BREAKER.get(endpoint) or [0, 0.0]
        state[0] += 1
        if state[0] >= self.breaker_threshold:
            state[1] = self._clock() + self.breaker_cooldown_s
        _BREAKER[endpoint] = state

    def _note_success(self, endpoint: str) -> None:
        _BREAKER.pop(endpoint, None)

    def _pace(self, endpoint: str) -> None:
        """Space outgoing calls by min_interval_s (per process, per endpoint)."""
        if self.min_interval_s <= 0:
            _PACE_LAST[endpoint] = self._clock()
            return
        now = self._clock()
        last = _PACE_LAST.get(endpoint)
        if last is not None and now - last < self.min_interval_s:
            self._sleep(self.min_interval_s - (now - last))
            now = self._clock()
        _PACE_LAST[endpoint] = now

    def _send(self, url: str, data: bytes, headers: dict):
        """One paced POST, returning the parsed JSON body. Raises."""
        self._pace(url)
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        with self._urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def _post_json(self, url: str, data: bytes, headers: dict):
        """POST with a single rate-limit retry. Returns the body or None."""
        try:
            body = self._send(url, data, headers)
        except urllib.error.HTTPError as exc:
            if exc.code not in RATE_LIMIT_CODES:
                logger.debug(
                    "jev-skill-router: evaluate failed (%s): %s",
                    type(exc).__name__,
                    exc,
                )
                return None
            self._note_ratelimit(url)
            wait = retry_after_s(
                exc.headers.get("retry-after") if exc.headers else None,
                time.time(),
            )
            if wait is None or wait > self.retry_max_wait_s:
                return None  # fail-open: no (or too long a) wait instructed
            if wait > 0:
                self._sleep(wait)
            try:
                body = self._send(url, data, headers)
            except urllib.error.HTTPError as exc2:
                # Counted once per evaluate (first 429/529 above); no double note.
                logger.debug(
                    "jev-skill-router: retry failed (%s): %s",
                    type(exc2).__name__,
                    exc2,
                )
                return None
            except Exception as exc2:  # timeout, network, parse — fail-open
                logger.debug(
                    "jev-skill-router: retry failed (%s): %s",
                    type(exc2).__name__,
                    exc2,
                )
                return None
        except Exception as exc:  # timeout, 5xx, network, parse — fail-open
            logger.debug(
                "jev-skill-router: evaluate failed (%s): %s", type(exc).__name__, exc
            )
            return None
        self._note_success(url)
        return body

    def evaluate(self, state, questions: dict) -> dict | None:
        """Return {"answers", "confidence", "cost", "latency_ms"} or None (fail-open)."""
        backend, key = self._resolve()
        if not backend or not key:
            return None

        cache_key = hashlib.sha256(
            json.dumps(
                [backend, self.model, self.typesafe_model, state, questions],
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).encode()
        ).hexdigest()
        now = time.time()
        hit = self._cache.get(cache_key)
        if hit and hit[0] > now:
            return hit[1]

        if backend == "typesafe":
            endpoint = f"{self.typesafe_base_url}/v1/systemone"
            payload = json.dumps(
                {
                    "state": state,
                    "model": self.typesafe_model,
                    "questions": to_typesafe_questions(questions),
                },
                ensure_ascii=False,
                default=str,
            ).encode()
            headers = {
                "Authorization": f"Bearer {key}",
                "content-type": "application/json",
            }
        else:
            endpoint = f"{self.base_url}/evaluation-model"
            payload = json.dumps(
                {"state": state, "questions": questions},
                ensure_ascii=False,
                default=str,
            ).encode()
            headers = {
                "Authorization": f"Bearer {key}",
                "ai-gateway-protocol-version": PROTOCOL_VERSION,
                "ai-evaluation-model-specification-version": SPEC_VERSION,
                "ai-model-id": self.model,
                "content-type": "application/json",
            }
        if self._breaker_open(endpoint):
            logger.debug("jev-skill-router: breaker open for %s; skipping", endpoint)
            return None
        started = self._clock()
        body = self._post_json(endpoint, payload, headers)
        if body is None:
            return None

        if backend == "typesafe":
            raw_answers = body.get("answers", {})
            result = {
                "answers": normalize_typesafe_answers(raw_answers),
                "confidence": typesafe_confidence(raw_answers),
                "cost": None,  # TypeSafe direct reports usage in tokens, no $ cost
                "usage": body.get("usage"),
                "latency_ms": round((self._clock() - started) * 1000),
            }
        else:
            result = {
                "answers": body.get("answers", {}),
                "confidence": (
                    body.get("providerMetadata", {}).get("typesafe", {}) or {}
                ).get("confidence", {}),
                "cost": (body.get("providerMetadata", {}).get("gateway", {}) or {}).get(
                    "cost"
                ),
                "usage": body.get("usage"),
                "latency_ms": round((self._clock() - started) * 1000),
            }
        self._cache[cache_key] = (now + self.cache_seconds, result)
        if len(self._cache) > CACHE_MAX_ENTRIES:
            self._cache.pop(next(iter(self._cache)))  # oldest-inserted first
        return result
