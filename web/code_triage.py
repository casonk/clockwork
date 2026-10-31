"""Sort a new To Code item into a repository (and so a group) with a System One decision model.

The To Code form used to require both a group and a repository. Now either can be left blank. In
this backlog a group is a family of repositories (every repository's items sit in one group), so the
group follows from the repository and only the repository is classified: ``classify`` asks Ollama's
``/v1/systemone`` endpoint one ``choice`` question over the known repositories (each with a
description) plus ``unknown``. When the group is given, only that group's repositories are offered.
The answer is ``unknown`` whenever the model picks it or is not confident enough, and then the group
is ``unknown`` too unless one was given. Nothing is guessed: an unsure item lands in the ``unknown``
group and repository, where a person or a stronger model can sort it later.

Measured on the real backlog (leave-one-out, 30 repositories): about 41% of items get a repository
and 72% of those are right at confidence 0.6; the rest are ``unknown``. Asking for the group as its
own question was tried and dropped: it was right less often (about 46%) than always answering the
most common group (73%). Stdlib only.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import tomllib

UNKNOWN = "unknown"
DEFAULT_URL = "http://localhost:11434/v1/systemone"
MODEL = "clef"
THRESHOLD = 0.6
# A model that is not resident loads first: 10 to 20 seconds on this machine.
TIMEOUT_SECONDS = 60
UNKNOWN_REPO = "Does not clearly belong to any listed repository"

Client = Callable[[str, str, dict], dict]


@dataclass(frozen=True)
class Triage:
    group: str
    repo: str
    repo_confidence: float | None = None
    error: str = ""

    def describe(self) -> str:
        if self.error:
            return f"auto-sort unavailable ({self.error}); filed as {self.group} / {self.repo}"
        confidence = "" if self.repo_confidence is None else f" ({self.repo_confidence:.0%})"
        return f"auto-sorted to {self.group} / {self.repo}{confidence}"


def enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return env.get("CLOCKWORK_TRIAGE", "1").strip().lower() not in {"0", "false", "no", "off"}


def repo_catalog(portfolio_root: Path, names: list[str]) -> dict[str, str]:
    """Repository name -> one-line description, from each checkout's ``.github/repository.toml``.

    A name with no readable description is described by the name alone."""
    found: dict[str, str] = {}
    try:
        candidates = [
            d for parent in portfolio_root.iterdir() if parent.is_dir() for d in parent.iterdir()
        ]
    except OSError:
        candidates = []
    by_name = {d.name.casefold(): d for d in candidates if d.is_dir()}
    for name in names:
        description = ""
        checkout = by_name.get(name.casefold())
        if checkout is not None:
            try:
                data = tomllib.loads(
                    (checkout / ".github" / "repository.toml").read_text(encoding="utf-8")
                )
                description = str(
                    data.get("description") or data.get("repository", {}).get("description") or ""
                )
            except (OSError, tomllib.TOMLDecodeError):
                pass
        found[name] = description.strip() or name
    return found


def systemone_client(url: str = DEFAULT_URL, timeout: int = TIMEOUT_SECONDS) -> Client:
    """A client for Ollama's decision-model endpoint (one call answers every question)."""

    def call(model: str, state: str, questions: dict) -> dict:
        body = json.dumps({"state": state, "model": model, "questions": questions}).encode()
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)

    return call


def canonical_repos(names: list[str]) -> dict[str, str]:
    """Case-folded name -> its most common spelling, so ``Clockwork`` and ``clockwork`` are one repository."""
    spellings: dict[str, dict[str, int]] = {}
    for name in names:
        if name.strip():
            counts = spellings.setdefault(name.casefold(), {})
            counts[name] = counts.get(name, 0) + 1
    return {
        key: max(counts, key=lambda n: (counts[n], n.islower(), n))
        for key, counts in spellings.items()
    }


def groups_by_repo(data: dict) -> dict[str, str]:
    """Case-folded repository -> the group most of its items sit in (finished work is not counted)."""
    tally: dict[str, dict[str, int]] = {}
    for category in data["categories"]:
        if category["name"].lower() == "archive":
            continue
        for item in category["items"]:
            repo = str(item.get("repo") or "").casefold()
            if repo:
                counts = tally.setdefault(repo, {})
                counts[category["name"]] = counts.get(category["name"], 0) + 1
    return {repo: max(counts, key=counts.get) for repo, counts in tally.items()}


def _ask(client: Client, model: str, text: str, instructions: str, criteria: dict[str, str]):
    """(choice, confidence) for one choice question, or raise ValueError on a malformed answer."""
    body = client(
        model, text, {"q": {"type": "choice", "instructions": instructions, "criteria": criteria}}
    )
    answer = body.get("answers", {}).get("q", {})
    probabilities = {str(k): float(v) for k, v in (answer.get("probabilities") or {}).items()}
    if not probabilities:
        raise ValueError("the model returned no probabilities")
    choice = max(probabilities, key=probabilities.get)
    return choice, probabilities[choice]


def classify(
    text: str,
    *,
    group: str = "",
    repo: str = "",
    group_of_repo: Mapping[str, str],
    repos: Mapping[str, str],
    client: Client,
    model: str = MODEL,
    threshold: float = THRESHOLD,
) -> Triage:
    """Fill in whichever of ``group`` and ``repo`` is blank; a given value is never changed.

    ``group_of_repo`` maps a case-folded repository to its group, ``repos`` maps each candidate
    repository to a description. With a group given, only that group's repositories are offered."""
    confidence = None
    error = ""
    if not repo:
        candidates = {
            name: desc
            for name, desc in repos.items()
            if name != UNKNOWN and (not group or group_of_repo.get(name.casefold()) == group)
        }
        if candidates:
            try:
                choice, confidence = _ask(
                    client,
                    model,
                    text,
                    "Which repository is this entry about? Answer unknown if it does not clearly belong to one.",
                    {**candidates, UNKNOWN: UNKNOWN_REPO},
                )
                repo = choice if choice in candidates and confidence >= threshold else UNKNOWN
            except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
                error = str(exc)[:80] or exc.__class__.__name__
    repo = repo or UNKNOWN
    if not group:
        group = group_of_repo.get(repo.casefold(), UNKNOWN)
    return Triage(group, repo, confidence, error)
