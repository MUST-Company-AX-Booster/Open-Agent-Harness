"""Two-stage skill router — the TypeSafe "skill suggestion" cookbook, over the gateway.

Request 1 ranks the whole roster with one Choice and scores the request with three
booleans (a gate: does this turn want a skill at all?). Request 2 re-reads the top
three with each candidate's own SKILL.md excerpt plus one absolute `fits` judgment
per candidate. Two requests, two thresholds. The winner plus every other shortlisted
candidate that also passes `fits` come back (pm-agent patch: upstream returns the winner only).

Question text, state shape and thresholds follow the published cookbook
(docs.typesafe.ai/cookbooks/skill_suggestion) so upstream changes can be diffed.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import roster as roster_mod
from .roster import Skill

SHORTLIST = 3  # candidates carried from request 1 into request 2
EXCERPT_CHARS = 700  # SKILL.md characters each candidate brings
GATE_THRESHOLD = 0.30  # mean of the three request judgments
FITS_THRESHOLD = 0.40  # winner's own "does this fit" judgment
NONE_OPTION = "none_of_these"
NONE_THRESHOLD = 0.50  # a chunk with P(none) at least this nominates nobody

CHOICE_INSTRUCTIONS = (
    "Which of these skills, if any, is the right one to load to help with the "
    "user's latest request?"
)
RERANK_INSTRUCTIONS = (  # pm-agent patch: was "Exactly one of these skills is the right one..."
    "Which one of these skills is the best one to load for the user's latest "
    "request? Read what each actually does, not just its name."
)

#: Gate booleans: two point toward needing a skill, one points away.
GATE_QUESTIONS = {
    "acts_on_user_system": (
        "Is the assistant being asked to act on the user's files, accounts, devices, "
        "or online services, rather than only to explain or advise?"
    ),
    "would_follow_documented_procedure": (
        "Would a careful expert answering this consult a specific documented procedure "
        "or set of commands, rather than answering from general understanding?"
    ),
    "prose_suffices": (
        "Could a knowledgeable generalist fully satisfy this request in prose, with "
        "no tools, no documentation, and no access to the user's files or accounts?"
    ),
}
INVERTED = {"prose_suffices"}


@dataclass
class Result:
    skills: list[str]  # winner first; pm-agent patch: upstream had a single `skill`
    gate: float
    probability: float
    confidence: float
    latency_ms: int
    cost: str | None = None
    calls: int = 1

    @property
    def skill(self) -> str:
        return self.skills[0]


def document(request: str, recent_context: str = "") -> dict:
    """The state every question in this recipe is asked over."""
    return {"request": request, "recent_context": recent_context}


def skip_reason(text: str, suggest_chars: int = 4000) -> str | None:
    """Why this turn is left alone (None = eligible). Single source of truth."""
    stripped = (text or "").strip()
    if not stripped:
        return "empty"
    if stripped.startswith("/"):
        return "slash"  # slash commands pick their own flow
    if suggest_chars and len(stripped) > suggest_chars:
        return "too_long"  # long pastes are not routing questions
    if "<skill_relevance>" in stripped:
        return "already_routed"  # already routed this turn
    return None


def should_skip_request(text: str, *, suggest_chars: int = 4000) -> bool:
    return skip_reason(text, suggest_chars) is not None


def block(*names: str) -> str:
    """The single line injected into the user-message context."""
    safe = ", ".join(str(n).replace("<", "").replace(">", "")[:64] for n in names)
    return (
        "<skill_relevance>\n"
        f"Relevant to the current request: {safe}. Ignore any that do not fit "
        "what the user actually asked for.\n"
        "</skill_relevance>"
    )


def chunk_roster(
    skills: list[Skill], size: int = roster_mod.CHUNK_CHOICES
) -> list[list[Skill]]:
    if size > roster_mod.MAX_CHOICES:
        raise ValueError(
            f"chunk size {size} exceeds the API cap of {roster_mod.MAX_CHOICES}"
        )
    items = list(skills)
    if not items:
        return [[]]
    return [items[i : i + size] for i in range(0, len(items), size)]


def gate_mean(answers: dict) -> float:
    """Mean of the three request judgments, with `prose_suffices` inverted.

    A missing gate key means no evidence: silence (0.0, below the default
    threshold) instead of a partial mean that could pass it.
    """
    answers = answers or {}
    if any(f"gate::{key}" not in answers for key in GATE_QUESTIONS):
        return 0.0
    values = []
    for key in GATE_QUESTIONS:
        row = answers.get(f"gate::{key}") or {}
        value = float(row.get("probability") or 0.0)
        values.append(1.0 - value if key in INVERTED else value)
    return sum(values) / len(values) if values else 0.0


def rank_wide(
    client,
    request: str,
    skills: list[Skill],
    *,
    chunk: int = roster_mod.CHUNK_CHOICES,
    shortlist: int = SHORTLIST,
) -> dict | None:
    """Request 1: rank the whole roster, gate the turn, nominate a shortlist."""
    groups = chunk_roster(skills, chunk)
    chunked = len(groups) > 1
    state = document(request)
    per_chunk: list[list[tuple[str, float]]] = []
    none_pressure: list[float] = []
    answers_first: dict | None = None
    first_result: dict | None = None
    calls = 0
    valid = {s.name for s in skills}  # unknown names are discarded, never nominated

    for index, group in enumerate(groups):
        criteria = {s.name: s.description for s in group}
        if chunked:
            criteria[NONE_OPTION] = "None of these skills fit the request."
        questions: dict = {
            "which": {
                "type": "choice",
                "instructions": CHOICE_INSTRUCTIONS,
                "criteria": criteria,
            }
        }
        if index == 0:
            for key, text in GATE_QUESTIONS.items():
                questions[f"gate::{key}"] = {"type": "boolean", "instructions": text}
        result = client.evaluate(state, questions)
        calls += 1
        if result is None:
            return None  # fail-open: no ranking, no suggestion
        if index == 0:
            answers_first = result["answers"]
            first_result = result
        probabilities = (result["answers"].get("which") or {}).get(
            "probabilities"
        ) or {}
        ranked = sorted(
            (
                (n, float(p))
                for n, p in probabilities.items()
                if n != NONE_OPTION and n in valid
            ),
            key=lambda kv: (-kv[1], kv[0]),
        )
        per_chunk.append(ranked)
        none_pressure.append(float(probabilities.get(NONE_OPTION, 0.0)))

    best_chunk = max(
        range(len(per_chunk)),
        key=lambda i: per_chunk[i][0][1] if per_chunk[i] else -1.0,
    )
    shortlisted: list[str] = []
    # Best chunk nominates first: with a small shortlist the later chunk's
    # winner would otherwise be cut by chunk order.
    order = [best_chunk] + [i for i in range(len(per_chunk)) if i != best_chunk]
    for index in order:
        ranked = per_chunk[index]
        if index != best_chunk and none_pressure[index] >= NONE_THRESHOLD:
            continue  # that chunk says nothing fits: noise
        for name, _ in ranked[:shortlist]:
            if name not in shortlisted:
                shortlisted.append(name)

    return {
        "shortlist": shortlisted[:shortlist],
        "gate": gate_mean(answers_first or {}),
        "calls": calls,
        "latency_ms": (first_result or {}).get("latency_ms", 0),
        "cost": (first_result or {}).get("cost"),
    }


def rerank_questions(
    names: list[str], by_name: dict[str, Skill], excerpt: int = EXCERPT_CHARS
) -> dict:
    criteria = {}
    for name in names:
        skill = by_name[name]
        text = skill.description
        body = skill.excerpt(excerpt)
        criteria[name] = f"{text}\n\n{body}" if body else text
    criteria[NONE_OPTION] = "None of these skills fit the request."
    questions: dict = {
        "which": {
            "type": "choice",
            "instructions": RERANK_INSTRUCTIONS,
            "criteria": criteria,
        }
    }
    for name in names:
        questions[f"fits::{name}"] = {
            "type": "boolean",
            "instructions": (
                f"Does the skill '{name}' do the specific thing the user's request asks "
                f"for? It is described as: {by_name[name].description}"
            ),
        }
    return questions


def suggest(
    client,
    request: str,
    skills: list[Skill],
    *,
    shortlist: int = SHORTLIST,
    excerpt: int = EXCERPT_CHARS,
    gate_threshold: float = GATE_THRESHOLD,
    fits_threshold: float = FITS_THRESHOLD,
    chunk: int = roster_mod.CHUNK_CHOICES,
) -> Result | None:
    """Full decision: rank (request 1) then re-read the top three (request 2)."""
    if len(skills) < 2:
        return None
    ranked = rank_wide(client, request, skills, chunk=chunk, shortlist=shortlist)
    if ranked is None:
        return None
    if ranked["gate"] < gate_threshold or not ranked["shortlist"]:
        return None

    names = ranked["shortlist"]
    by_name = {s.name: s for s in skills}
    result = client.evaluate(
        document(request), rerank_questions(names, by_name, excerpt)
    )
    calls = ranked["calls"] + 1
    if result is None:
        return None
    answers = result["answers"]
    chosen = str((answers.get("which") or {}).get("choice") or "")
    confidence = float((result.get("confidence") or {}).get("which") or 0.0)
    if not chosen or chosen == NONE_OPTION or chosen not in by_name:
        return None
    def fits(name: str) -> float:
        return float((answers.get(f"fits::{name}") or {}).get("probability") or 0.0)

    if fits(chosen) < fits_threshold:
        return None
    # pm-agent patch: a turn can need several skills, so the other candidates that pass
    # their own `fits` judgment ride along, best fit first. The winner still gates the turn.
    others = sorted((n for n in names if n != chosen and fits(n) >= fits_threshold),
                    key=lambda n: -fits(n))

    probabilities = (answers.get("which") or {}).get("probabilities") or {}
    return Result(
        skills=[chosen, *others],
        gate=ranked["gate"],
        probability=float(probabilities.get(chosen) or 0.0),
        confidence=confidence,
        latency_ms=int(ranked["latency_ms"]) + int(result.get("latency_ms") or 0),
        cost=result.get("cost") or ranked.get("cost"),
        calls=calls,
    )
