"""Sort a new To Code item into a group and a repository with a System One decision model.

The To Code form used to require both. Now either can be left blank: ``classify`` asks Ollama's
``/v1/systemone`` endpoint a ``choice`` question for each missing field, lists the allowed values
(with a description each) and an ``unknown`` option, and answers ``unknown`` whenever the model
picks it or is not confident enough. Nothing is guessed: an unsure item lands in the ``unknown``
group or repository, where a person or a stronger model can sort it later.

The defaults come from crew-chief's ``benchmarks/decisions/backlog-triage.json`` (synthetic
entries, held out): the repository is answered well by ``clef-flash`` in about 190 ms (82% right
overall; at confidence 0.6 it answers 74% of items and is wrong on 8%), the group is much harder
and only ``clef`` (about 0.9 s) is useful (at 0.6 it answers 59% and is wrong on 8%). Stdlib only.
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
REPO_MODEL = "clef-flash"
GROUP_MODEL = "clef"
THRESHOLD = 0.6
TIMEOUT_SECONDS = 20
# What each standard group means; a group not listed here is described by its name.
GROUP_DESCRIPTIONS = {
    "util": "Improvements, bugs and features in the tools and services that run the portfolio",
    "doc": "Documentation, diagrams, write-ups and records",
    "sec": "Security: credentials, access, exposure and hardening",
    "research": "Investigation or evaluation before anything is built",
    "health": "Machine, drive or service health, monitoring and maintenance",
}
UNKNOWN_GROUP = "Does not clearly belong to any listed group"
UNKNOWN_REPO = "Does not clearly belong to any listed repository"

Client = Callable[[str, str, dict], dict]


@dataclass(frozen=True)
class Triage:
    group: str
    repo: str
    group_confidence: float | None = None
    repo_confidence: float | None = None
    error: str = ""

    def describe(self) -> str:
        if self.error:
            return f"auto-sort unavailable ({self.error}); filed as {self.group} / {self.repo}"

        def part(value: str, confidence: float | None) -> str:
            return value if confidence is None else f"{value} ({confidence:.0%})"

        return f"auto-sorted to {part(self.group, self.group_confidence)} / {part(self.repo, self.repo_confidence)}"


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


def _decide(client, model, text, instructions, criteria, threshold) -> tuple[str, float]:
    choice, confidence = _ask(client, model, text, instructions, criteria)
    if choice == UNKNOWN or choice not in criteria or confidence < threshold:
        return UNKNOWN, confidence
    return choice, confidence


def classify(
    text: str,
    *,
    group: str = "",
    repo: str = "",
    groups: list[str],
    repos: Mapping[str, str],
    client: Client,
    group_model: str = GROUP_MODEL,
    repo_model: str = REPO_MODEL,
    threshold: float = THRESHOLD,
) -> Triage:
    """Fill in whichever of ``group`` and ``repo`` is blank; a given value is never changed."""
    group_confidence = repo_confidence = None
    error = ""
    try:
        if not group and groups:
            criteria = {g: GROUP_DESCRIPTIONS.get(g, g) for g in groups if g != UNKNOWN}
            criteria[UNKNOWN] = UNKNOWN_GROUP
            group, group_confidence = _decide(
                client,
                group_model,
                text,
                "Which backlog group does this entry belong to? Answer unknown if it does not clearly fit.",
                criteria,
                threshold,
            )
        if not repo and repos:
            criteria = {
                **{name: desc for name, desc in repos.items() if name != UNKNOWN},
                UNKNOWN: UNKNOWN_REPO,
            }
            repo, repo_confidence = _decide(
                client,
                repo_model,
                text,
                "Which repository is this entry about? Answer unknown if it does not clearly belong to one.",
                criteria,
                threshold,
            )
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        error = str(exc)[:80] or exc.__class__.__name__
    return Triage(group or UNKNOWN, repo or UNKNOWN, group_confidence, repo_confidence, error)
