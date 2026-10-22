"""Roster — the live skill index read from disk. Stdlib only, no Hermes internals."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

MAX_CHOICES = 255  # the API refuses a Choice with more options
CHUNK_CHOICES = 240  # headroom under the cap
INDEX_DESC_CHARS = 120
MAX_NAME_CHARS = 64

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_SCALAR_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Minimal YAML-subset frontmatter reader: flat scalars, quotes, block scalars.

    Skill frontmatter only ever needs `name` and `description`, so this stays
    deliberately small instead of pulling a YAML dependency into a plugin.
    """
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    data: dict = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        mm = _SCALAR_RE.match(line)
        if not mm:
            continue
        key, value = mm.group(1), mm.group(2).strip()
        if value in (">-", ">", "|", "|-", "|+", ">-"):
            block = []
            while i < len(lines) and (
                not lines[i].strip() or lines[i][:1] in (" ", "\t")
            ):
                block.append(lines[i].strip())
                i += 1
            data[key] = " ".join(part for part in block if part)
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key] = value
    return data, text[m.end() :]


def iter_skill_files(root: Path):
    """Every SKILL.md under *root*, skipping dot-directories.

    Follows symlinked skill directories: a top-level entry often points
    outside *root* (e.g. a shared skills tree), and the session loader
    follows those links, so the roster must too. Each canonical directory
    (by ``os.path.realpath``) is visited once — already-seen subtrees are
    pruned before descending, so an ancestor-pointing symlink can neither
    loop forever nor yield a file twice.

    Invariant: every name suggested from this roster must be loadable in
    the session. This walk cannot enforce that alone (it also sees skills
    the session loader skips, e.g. nested helpers or names the user
    disabled in ``~/.hermes/config.yaml``); resolving the live set would
    mean coupling to Hermes config/profile state, which is deliberately
    left out — see the implementer report for the measured residual gap.
    """
    if not root.is_dir():
        return
    seen: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        real = os.path.realpath(dirpath)
        if real in seen:
            dirnames[:] = []  # second path to a covered tree: do not descend
            continue
        seen.add(real)
        dirnames[:] = [
            d
            for d in dirnames
            if os.path.realpath(os.path.join(dirpath, d)) not in seen
        ]
        if "SKILL.md" in filenames:
            yield Path(dirpath) / "SKILL.md"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str  # index-width description (what request 1 sees)
    path: Path

    def excerpt(self, chars: int = 700) -> str:
        """Opening of the skill's SKILL.md body — what request 2 sees."""
        try:
            _, body = parse_frontmatter(self.path.read_text(encoding="utf-8"))
        except OSError:
            return ""
        return " ".join(body.split())[:chars]


def load_roster(dirs) -> list[Skill]:
    """Read every skill once, de-duplicated by name, in stable name order.

    No cap here: rosters above the API choice limit are the router's job
    (it chunks them in request 1).
    """
    seen: set[str] = set()
    skills: list[Skill] = []
    for root in dirs:
        for skill_file in iter_skill_files(Path(root)):
            try:
                frontmatter, _ = parse_frontmatter(
                    skill_file.read_text(encoding="utf-8")
                )
            except OSError:
                continue
            name = str(frontmatter.get("name") or skill_file.parent.name).strip()
            if not name or len(name) > MAX_NAME_CHARS or name in seen:
                continue
            desc = " ".join(str(frontmatter.get("description") or name).split())
            seen.add(name)
            skills.append(
                Skill(
                    name=name,
                    description=desc[:INDEX_DESC_CHARS] or name,
                    path=skill_file,
                )
            )
    return sorted(skills, key=lambda s: s.name)
