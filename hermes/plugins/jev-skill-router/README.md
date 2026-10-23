# jev-skill-router

[TypeSafe Jev](https://docs.typesafe.ai) (System One) as a skill router for
Hermes Agent: before the model call, Jev names the skills (up to `shortlist`) from the
live roster for the current turn, and the plugin injects a single
`<skill_relevance>` line into the user-message context. It says nothing when
nothing fits. Two routes, picked by the `backend` setting (`auto` by
default: `TYPESAFE_API_KEY` wins, else `AI_GATEWAY_API_KEY`) — pure stdlib,
no Node, no SDK.

| Route             | Endpoint                                                          | Key                  | Questions                      | Confidence                             | Cost                            |
| ----------------- | ----------------------------------------------------------------- | -------------------- | ------------------------------ | -------------------------------------- | ------------------------------- |
| TypeSafe direto   | `POST https://api.typesafe.ai/v1/systemone` (`model: jev-latest`) | `TYPESAFE_API_KEY`   | `noul` / `choice` / `score`    | inline per answer                      | none (`usage` in tokens)        |
| Vercel AI Gateway | `POST {jev_base_url}/evaluation-model` (`typesafe-ai/jev`)        | `AI_GATEWAY_API_KEY` | `boolean` / `choice` / `score` | `providerMetadata.typesafe.confidence` | `providerMetadata.gateway.cost` |

Yes/no questions are `boolean` internally and mapped to `noul` on the
TypeSafe wire; answers come back normalized.

Exercitada ao vivo nos dois backends (2026-09-21, mesmo state/perguntas):
TypeSafe direto — `suggest` → `xlsx`, gate 0.57, p 1.00, 1431 ms, `cost: null`,
usage em tokens; gateway — 732 ms, `cost: 0`. Respostas normalizadas iguais
(`noul`→`{probability}`, confiança inline 1.0/0.83 vs 1/0.81).

## What it does

**Hook** (`pre_llm_call`, opt-in): two Jev requests per eligible turn.
Request 1 ranks the whole roster with one Choice plus three gate booleans
(does this turn want a skill at all — the third one inverted). Request 2
re-reads the top three with each candidate's SKILL.md excerpt plus one
absolute `fits` judgment per candidate. Two thresholds: the winner must pass
`fits`, and every other candidate that also passes it comes back too, best fit
first (open-agent patch; upstream returns the winner only). The model stays in charge: the line says to ignore it when it
does not fit.

Rosters above the API's 255-choice cap are chunked (240 per chunk, each with
a `none_of_these` option). Slash commands, empty messages, long pastes and
already-routed turns are left alone.

## Install

```bash
hermes plugins install DoGMaTiiC/hermes-jev/plugins/jev-skill-router
hermes jev-skill-router auto   # or: on
```

Requires `TYPESAFE_API_KEY` (direct) and/or `AI_GATEWAY_API_KEY` (Vercel AI
Gateway key) and Hermes ≥ 0.21. No key at all: the plugin loads and stays
silent.

## Settings

`plugins.entries.jev-skill-router.settings` in `config.yaml`:

| Key                  | Default                                   | Meaning                                                                                                        |
| -------------------- | ----------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `mode`               | `off`                                     | `off` = never · `auto` = only with key · `on` = always                                                         |
| `gate`               | `0.30`                                    | Mean of the 3 request judgments; below it, silence                                                             |
| `fits`               | `0.40`                                    | Each candidate's "does it fit" judgment; winner below it = silence, others below it are dropped               |
| `shortlist`          | `3`                                       | Candidates carried from request 1 into request 2                                                               |
| `chunk`              | `240`                                     | Skills per Choice question (API caps one at 255)                                                               |
| `excerpt`            | `700`                                     | SKILL.md characters each candidate brings                                                                      |
| `timeout_s`          | `4.0`                                     | Per-attempt timeout (worst case per call: 2×`timeout_s` + `retry_max_wait_s`)                                  |
| `cache_seconds`      | `300`                                     | Identical calls answered from cache per window                                                                 |
| `backend`            | `auto`                                    | `auto` = TypeSafe key wins, else gateway · `typesafe`/`gateway` forces one                                     |
| `typesafe_model`     | `jev-latest`                              | TypeSafe direto model                                                                                          |
| `typesafe_base_url`  | `https://api.typesafe.ai`                 | TypeSafe direto endpoint override                                                                              |
| `retry_max_wait_s`   | `2.0`                                     | Retry once on 429/529 only if Retry-After waits at most this                                                   |
| `breaker_threshold`  | `3`                                       | Consecutive 429/529s before going silent                                                                       |
| `breaker_cooldown_s` | `120`                                     | Silence window after the breaker opens                                                                         |
| `min_interval_s`     | `0.25`                                    | Minimum gap between outgoing Jev calls, per process                                                            |
| `suggest_chars`      | `4000`                                    | Longer user messages are left alone                                                                            |
| `jev_model`          | `typesafe-ai/jev`                         | Gateway override (prefixed: the loader rejects bare `model`)                                                   |
| `jev_base_url`       | `https://ai-gateway.vercel.sh/v4/ai`      | Endpoint override (bare `base_url` is inert — reserved roots are only `model`/`plugins`/`security`/`settings`) |
| `roster_dir`         | `<HERMES_HOME>/skills`                    | Where SKILL.md files are scanned                                                                               |
| `log_path`           | `<HERMES_HOME>/logs/jev-skill-router.log` | JSONL decision log                                                                                             |

## What leaves your machine

Per eligible turn: the request text plus skill names and one-line
descriptions; for the 3 shortlisted candidates, the description plus the
first 700 characters of SKILL.md. Never: conversation history, files,
memory, or tool output.

## Cost / latency

About 2 Jev calls per eligible turn — roughly $0.00002 on the gateway
(TypeSafe direto bills in tokens, no per-call $) and ~1 s. Ineligible
turns (slash, empty, long, already routed) and `mode: off` cost nothing.

## Calibration (measured, not guessed)

Thresholds were calibrated on this machine's live roster — **76 labelled requests**
(66 covered by exactly one skill, 10 covered by none), shipped pipeline, TypeSafe
direto: **top-1 95%**, **needless 0/10**, and the second door picked the right skill
**66/66** — every loss came from the gate. Sweep over gate 0.20–0.40 × fits 0.30–0.50
puts the shipped pair at the knee (gate 0.25–0.30, any fits in 0.30–0.45 flat).
Full tables, misses and repro commands: [`docs/calibration/router-roster-2026-09-21.md`](../../docs/calibration/router-roster-2026-09-21.md).

## Fail-open, always

Missing key for the selected backend, timeout, HTTP error, malformed body —
the hook returns nothing and the turn proceeds exactly as today.

## Under rate limit

The gateway free tier limits per model; TypeSafe direto has no gateway
limiter. On 429/529 the client reads `Retry-After` (seconds or HTTP date;
garbage and non-finite values like `nan`/`inf` are ignored) and retries
**once** if the wait fits in `retry_max_wait_s` (2.0s) — never in a loop.
After `breaker_threshold` (3) consecutive 429/529s the endpoint goes silent
for `breaker_cooldown_s` (120s); any success resets the count. The breaker
counts one rate-limit event per call, even when the retry is limited too.
Outgoing calls are spaced `min_interval_s` (0.25s) apart per process, and
identical calls share one cached answer for `cache_seconds` (300s).

Wall-clock budget: `timeout_s` bounds each attempt, so one call with a
retry can take up to ~2×`timeout_s` + `retry_max_wait_s` (~10s at defaults).

## Off switch

```bash
hermes jev-skill-router off
```

## Commands

```bash
hermes jev-skill-router on|off|auto
hermes jev-skill-router status
hermes jev-skill-router suggest "deploy the site" [--json]
hermes jev-skill-router check
```

## Prior art

Skill routing is an established slot in the catalog, and it came first: **`typesafe-skill-router`**
(DECRUX9812, listed 2026-09-16) and **`skill-router`** (xXLODXx, listed 2026-09-15). This plugin
follows the same two-stage design — gate booleans over the roster, then a re-read of the shortlist,
one `<skill_relevance>` line, silence when nothing fits — and the same defaults (`gate 0.30`,
`fits 0.40`, `shortlist 3`, `chunk 240`, `excerpt 700`, `suggest_chars 4000`). It is a rebuild
with three differences: a **dual backend** (TypeSafe direct or Vercel AI Gateway, `backend: auto`),
**rate-limit hardening** (Retry-After retry, breaker, per-process pacing, TTL cache) and a
**published calibration** on a labelled request set (`docs/calibration/`). MIT, like the rest of
the family.

The wider Jev family in the catalog — Nerve (`hermes-jev`) by keeltrace, `jev-approvals` and
`jev-curator` by anpicasso, Pinutss' four `jev-*` routers, `jev` by ourines, `jev-typesafe` by
ajensenwaud — is credited where its ideas were borrowed.

## Verify

```bash
python3 tests/test_offline.py          # offline logic + fail-open, no network
hermes plugins validate .              # catalog admission gate
hermes plugins doctor . --ci           # Hermes loader contract
```

Decision log (one JSON line per decision):

```bash
tail -f "${HERMES_HOME:-$HOME/.hermes}/logs/jev-skill-router.log"
```
