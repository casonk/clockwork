"""Auto-sorting a To Code item: the classifier, and the form and API that use it."""

# The route tests reuse the list fixtures from test_web_lists as parameters.
# ruff: noqa: F811, F401

from __future__ import annotations

import json

import pytest

from tests.test_web_lists import _csrf, client, list_files, web_app
from web import code_triage

REPOS = {
    "scheduler-service": "Schedules and runs recurring jobs",
    "media-library": "Plays the local video library",
    "docs-site": "A personal website",
}
GROUP_OF = {"scheduler-service": "util", "media-library": "util", "docs-site": "doc"}


class FakeModel:
    """Answers the one repository question with fixed probabilities; records the calls."""

    def __init__(self, repo=None, fail=None):
        self.repo = repo or {"scheduler-service": 0.85, "media-library": 0.1, "unknown": 0.05}
        self.fail = fail
        self.calls = []

    def __call__(self, model, state, questions):
        if self.fail:
            raise self.fail
        spec = questions["q"]
        self.calls.append((model, state, sorted(spec["criteria"])))
        return {"answers": {"q": {"probabilities": self.repo}}}


def classify(text="Show the next run of each job", **kwargs):
    kwargs.setdefault("client", FakeModel())
    return code_triage.classify(text, group_of_repo=GROUP_OF, repos=REPOS, **kwargs)


def test_picks_the_repo_and_takes_the_group_from_it_with_one_call():
    model = FakeModel()
    result = classify(client=model)
    assert (result.group, result.repo, result.repo_confidence) == (
        "util",
        "scheduler-service",
        0.85,
    )
    (call,) = model.calls
    assert call[0] == code_triage.MODEL
    assert call[2] == ["docs-site", "media-library", "scheduler-service", "unknown"]
    assert result.describe() == "auto-sorted to util / scheduler-service (85%)"


def test_a_given_group_limits_the_repositories_offered_and_is_kept():
    model = FakeModel(repo={"docs-site": 0.9, "unknown": 0.1})
    result = classify(group="doc", client=model)
    assert (result.group, result.repo) == ("doc", "docs-site")
    assert model.calls[0][2] == ["docs-site", "unknown"]


def test_a_given_repo_is_never_asked_about_and_gives_its_group():
    model = FakeModel()
    result = classify(repo="media-library", client=model)
    assert (result.group, result.repo) == ("util", "media-library")
    assert model.calls == []
    unlisted = classify(repo="brand-new-repo", client=model)
    assert (unlisted.group, unlisted.repo) == ("unknown", "brand-new-repo")
    both = classify(group="doc", repo="media-library", client=model)
    assert (both.group, both.repo) == ("doc", "media-library") and model.calls == []


@pytest.mark.parametrize(
    "probabilities",
    [
        {"scheduler-service": 0.45, "media-library": 0.4, "unknown": 0.15},  # not confident enough
        {"scheduler-service": 0.2, "media-library": 0.1, "unknown": 0.7},  # the model says unknown
        {"made-up-repo": 0.95, "unknown": 0.05},  # not one of the options
    ],
)
def test_an_unsure_model_gives_unknown_for_both(probabilities):
    result = classify(client=FakeModel(repo=probabilities))
    assert (result.group, result.repo) == ("unknown", "unknown")


def test_an_unsure_model_still_keeps_a_group_that_was_given():
    result = classify(group="util", client=FakeModel(repo={"unknown": 1.0}))
    assert (result.group, result.repo) == ("util", "unknown")


@pytest.mark.parametrize(
    "failure", [OSError("connection refused"), ValueError("bad"), json.JSONDecodeError("x", "y", 0)]
)
def test_an_unreachable_or_malformed_model_gives_unknown_with_a_note(failure):
    result = classify(client=FakeModel(fail=failure))
    assert (result.group, result.repo) == ("unknown", "unknown")
    assert "auto-sort unavailable" in result.describe()


def test_the_threshold_and_model_are_configurable():
    probabilities = {"scheduler-service": 0.45, "media-library": 0.4, "unknown": 0.15}
    assert classify(client=FakeModel(repo=probabilities), threshold=0.4).repo == "scheduler-service"
    model = FakeModel()
    classify(client=model, model="other")
    assert model.calls[0][0] == "other"


def test_canonical_repos_merges_case_variants_and_keeps_the_commonest_spelling():
    got = code_triage.canonical_repos(
        ["Clockwork", "clockwork", "clockwork", "tachometer", "", " "]
    )
    assert got == {"clockwork": "clockwork", "tachometer": "tachometer"}
    assert code_triage.canonical_repos(["Clockwork", "clockwork"])["clockwork"] == "clockwork"


def test_groups_by_repo_uses_the_biggest_group_and_ignores_archive():
    data = {
        "categories": [
            {"name": "archive", "items": [{"title": "a", "repo": "old-only"}]},
            {
                "name": "util",
                "items": [{"title": "b", "repo": "Clockwork"}, {"title": "c", "repo": "x"}],
            },
            {
                "name": "doc",
                "items": [{"title": "d", "repo": "clockwork"}, {"title": "e", "repo": "clockwork"}],
            },
        ]
    }
    assert code_triage.groups_by_repo(data) == {"clockwork": "doc", "x": "util"}


def test_repo_catalog_reads_descriptions_from_repository_toml_and_falls_back_to_the_name(tmp_path):
    repo = tmp_path / "util-repos" / "scheduler-service" / ".github"
    repo.mkdir(parents=True)
    (repo / "repository.toml").write_text('description = "Schedules things"\n')
    (tmp_path / "util-repos" / "plain").mkdir()
    catalog = code_triage.repo_catalog(tmp_path, ["Scheduler-Service", "plain", "absent"])
    assert catalog == {
        "Scheduler-Service": "Schedules things",
        "plain": "plain",
        "absent": "absent",
    }
    assert code_triage.repo_catalog(tmp_path / "missing", ["x"]) == {"x": "x"}


def test_enabled_reads_the_environment():
    assert code_triage.enabled({}) is True
    assert code_triage.enabled({"CLOCKWORK_TRIAGE": "off"}) is False


@pytest.fixture()
def fake(monkeypatch, list_files):
    model = FakeModel()
    list_files["CODE_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "util",
                        "items": [{"title": "t", "done": False, "repo": "scheduler-service"}],
                    },
                    {"name": "doc", "items": [{"title": "u", "done": False, "repo": "docs-site"}]},
                ]
            }
        )
    )
    monkeypatch.setattr(web_app.code_triage, "enabled", lambda env=None: True)
    monkeypatch.setattr(web_app.code_triage, "systemone_client", lambda *a, **k: model)
    monkeypatch.setattr(
        web_app.code_triage, "repo_catalog", lambda root, names: {n: n for n in names}
    )
    return model


def _saved(list_files):
    return json.loads(list_files["CODE_LIST_FILE"].read_text())["categories"]


def _items(list_files, group):
    return next(c for c in _saved(list_files) if c["name"] == group)["items"]


def test_the_form_sorts_an_item_with_no_group_or_repo(client, list_files, fake):
    response = client.post(
        "/to-code/add-item",
        data={
            "csrf_token": _csrf(client, "/api/code"),
            "category": "",
            "repo": "",
            "item": "Show next run",
        },
        follow_redirects=True,
    )
    assert "auto-sorted to util / scheduler-service (85%)" in response.get_data(as_text=True)
    assert {"title": "Show next run", "done": False, "repo": "scheduler-service"} in _items(
        list_files, "util"
    )
    assert fake.calls[0][2] == [
        "docs-site",
        "scheduler-service",
        "unknown",
    ]  # archive repos are not offered


def test_an_unsure_form_item_lands_in_unknown_and_is_still_saved(client, list_files, fake):
    fake.repo = {"unknown": 0.9, "scheduler-service": 0.1}
    client.post(
        "/to-code/add-item",
        data={
            "csrf_token": _csrf(client, "/api/code"),
            "category": "",
            "repo": "",
            "item": "Think about stuff",
        },
    )
    assert _items(list_files, "unknown") == [
        {"title": "Think about stuff", "done": False, "repo": "unknown"}
    ]


def test_the_api_reports_how_an_item_was_sorted_and_keeps_a_given_group(client, list_files, fake):
    fake.repo = {"docs-site": 0.9, "unknown": 0.1}
    response = client.post(
        "/api/code/add-item",
        json={"category": "doc", "item": "Refresh the landing page"},
        headers={"X-CSRF-Token": _csrf(client, "/api/code")},
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["auto_sorted"] == "auto-sorted to doc / docs-site (90%)"
    assert _items(list_files, "doc")[-1]["repo"] == "docs-site"
    assert fake.calls[0][2] == ["docs-site", "unknown"]


def test_a_given_repo_in_another_case_is_normalised_and_gives_its_group(client, list_files, fake):
    response = client.post(
        "/api/code/add-item",
        json={"repo": "Scheduler-Service", "item": "Another one"},
        headers={"X-CSRF-Token": _csrf(client, "/api/code")},
    )
    assert response.status_code == 200
    assert _items(list_files, "util")[-1] == {
        "title": "Another one",
        "done": False,
        "repo": "scheduler-service",
    }
    assert fake.calls == []


def test_a_given_group_and_repo_skip_the_model(client, list_files, fake):
    response = client.post(
        "/api/code/add-item",
        json={"category": "doc", "repo": "clockwork", "item": "Write it down"},
        headers={"X-CSRF-Token": _csrf(client, "/api/code")},
    )
    assert response.status_code == 200 and "auto_sorted" not in response.get_json()
    assert fake.calls == []


def test_with_auto_sorting_off_a_missing_repo_is_still_refused(client, list_files, monkeypatch):
    monkeypatch.setattr(web_app.code_triage, "enabled", lambda env=None: False)
    response = client.post(
        "/api/code/add-item",
        json={"category": "util", "item": "No repo"},
        headers={"X-CSRF-Token": _csrf(client, "/api/code")},
    )
    assert response.status_code == 400 and response.get_json()["error"] == "repo required"
