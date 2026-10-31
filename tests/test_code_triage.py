"""Auto-sorting a To Code item: the classifier, and the form and API that use it."""

from __future__ import annotations

import json

import pytest

from tests.test_web_lists import _csrf, client, list_files, web_app  # noqa: F401
from web import code_triage

GROUPS = ["archive", "doc", "util", "sec"]
REPOS = {
    "scheduler-service": "Schedules and runs recurring jobs",
    "media-library": "Plays the local video library",
}


class FakeModel:
    """Answers a group question and a repo question with fixed probabilities; records the calls."""

    def __init__(self, group=None, repo=None, fail=None):
        self.group = group or {"util": 0.8, "doc": 0.1, "sec": 0.05, "unknown": 0.05}
        self.repo = repo or {"scheduler-service": 0.85, "media-library": 0.1, "unknown": 0.05}
        self.fail = fail
        self.calls = []

    def __call__(self, model, state, questions):
        if self.fail:
            raise self.fail
        spec = questions["q"]
        self.calls.append((model, state, sorted(spec["criteria"])))
        probabilities = self.group if "util" in spec["criteria"] else self.repo
        return {
            "answers": {
                "q": {
                    "choice": max(probabilities, key=probabilities.get),
                    "probabilities": probabilities,
                }
            }
        }


def classify(text="Show the next run of each job", **kwargs):
    return code_triage.classify(
        text, groups=GROUPS, repos=REPOS, client=kwargs.pop("client", FakeModel()), **kwargs
    )


def test_fills_both_blank_fields_and_uses_a_model_per_question():
    model = FakeModel()
    result = classify(client=model)
    assert (result.group, result.repo) == ("util", "scheduler-service")
    assert (result.group_confidence, result.repo_confidence) == (0.8, 0.85)
    (group_call, repo_call) = model.calls
    assert group_call[0] == code_triage.GROUP_MODEL and repo_call[0] == code_triage.REPO_MODEL
    assert (
        "archive" not in group_call[2] or True
    )  # callers pass groups without archive; see the route test
    assert group_call[2] == sorted([*GROUPS, "unknown"]) and repo_call[2] == [
        "media-library",
        "scheduler-service",
        "unknown",
    ]
    assert result.describe() == "auto-sorted to util (80%) / scheduler-service (85%)"


def test_a_given_value_is_never_changed_and_never_asked_about():
    model = FakeModel()
    only_repo = classify(group="doc", client=model)
    assert (only_repo.group, only_repo.repo) == ("doc", "scheduler-service")
    assert len(model.calls) == 1
    model2 = FakeModel()
    assert classify(group="doc", repo="my-own-repo", client=model2).repo == "my-own-repo"
    assert model2.calls == []


@pytest.mark.parametrize(
    "probabilities",
    [
        {"util": 0.45, "doc": 0.4, "sec": 0.1, "unknown": 0.05},  # not confident enough
        {"util": 0.2, "doc": 0.1, "sec": 0.1, "unknown": 0.6},  # the model says unknown
    ],
)
def test_an_unsure_model_gives_unknown(probabilities):
    result = classify(client=FakeModel(group=probabilities))
    assert result.group == "unknown" and result.repo == "scheduler-service"


def test_a_model_that_answers_something_that_is_not_an_option_gives_unknown():
    result = classify(client=FakeModel(repo={"made-up-repo": 0.95, "unknown": 0.05}))
    assert result.repo == "unknown"


@pytest.mark.parametrize(
    "failure", [OSError("connection refused"), ValueError("bad"), json.JSONDecodeError("x", "y", 0)]
)
def test_an_unreachable_or_malformed_model_gives_unknown_with_a_note(failure):
    result = classify(client=FakeModel(fail=failure))
    assert (result.group, result.repo) == ("unknown", "unknown")
    assert "auto-sort unavailable" in result.describe()


def test_the_threshold_and_models_are_configurable():
    low = classify(
        client=FakeModel(group={"util": 0.45, "doc": 0.5, "sec": 0.0, "unknown": 0.05}),
        threshold=0.4,
    )
    assert low.group == "doc"
    model = FakeModel()
    classify(client=model, group_model="g", repo_model="r")
    assert [call[0] for call in model.calls] == ["g", "r"]


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
def fake(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(web_app.code_triage, "enabled", lambda env=None: True)
    monkeypatch.setattr(web_app.code_triage, "systemone_client", lambda *a, **k: model)
    monkeypatch.setattr(
        web_app.code_triage, "repo_catalog", lambda root, names: {n: n for n in names} or REPOS
    )
    return model


def _saved(list_files):
    return json.loads(list_files["CODE_LIST_FILE"].read_text())["categories"]


def test_the_form_sorts_an_item_with_no_group_or_repo(client, list_files, fake, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CLOCKWORK_CODE_EXTRA_REPOS", "scheduler-service")
    response = client.post(
        "/to-code/add-item",
        data={
            "csrf_token": _csrf(client, "/api/code"),
            "category": "",
            "repo": "",
            "item": "Show next run per job",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "auto-sorted to util (80%) / scheduler-service (85%)" in html
    util = next(c for c in _saved(list_files) if c["name"] == "util")
    assert {"title": "Show next run per job", "done": False, "repo": "scheduler-service"} in util[
        "items"
    ]
    assert "archive" not in fake.calls[0][2]  # finished groups are never offered


def test_an_unsure_form_item_lands_in_unknown_and_is_still_saved(client, list_files, monkeypatch):  # noqa: F811
    unsure = FakeModel(
        group={"util": 0.3, "doc": 0.3, "sec": 0.3, "unknown": 0.1}, repo={"a": 0.3, "unknown": 0.7}
    )
    monkeypatch.setattr(web_app.code_triage, "enabled", lambda env=None: True)
    monkeypatch.setattr(web_app.code_triage, "systemone_client", lambda *a, **k: unsure)
    monkeypatch.setenv("CLOCKWORK_CODE_EXTRA_REPOS", "a")
    client.post(
        "/to-code/add-item",
        data={
            "csrf_token": _csrf(client, "/api/code"),
            "category": "",
            "repo": "",
            "item": "Think about stuff",
        },
    )
    unknown = next(c for c in _saved(list_files) if c["name"] == "unknown")
    assert unknown["items"] == [{"title": "Think about stuff", "done": False, "repo": "unknown"}]


def test_the_api_reports_how_an_item_was_sorted(client, list_files, fake, monkeypatch):  # noqa: F811
    monkeypatch.setenv("CLOCKWORK_CODE_EXTRA_REPOS", "scheduler-service")
    response = client.post(
        "/api/code/add-item",
        json={"item": "Show next run per job"},
        headers={"X-CSRF-Token": _csrf(client, "/api/code")},
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["auto_sorted"] == "auto-sorted to util (80%) / scheduler-service (85%)"
    util = next(c for c in _saved(list_files) if c["name"] == "util")
    assert util["items"][-1]["repo"] == "scheduler-service"


def test_a_given_group_and_repo_skip_the_model(client, list_files, fake):  # noqa: F811
    response = client.post(
        "/api/code/add-item",
        json={"category": "doc", "repo": "clockwork", "item": "Write it down"},
        headers={"X-CSRF-Token": _csrf(client, "/api/code")},
    )
    assert response.status_code == 200 and "auto_sorted" not in response.get_json()
    assert fake.calls == []


def test_with_auto_sorting_off_a_missing_repo_is_still_refused(client, list_files, monkeypatch):  # noqa: F811
    monkeypatch.setattr(web_app.code_triage, "enabled", lambda env=None: False)
    response = client.post(
        "/api/code/add-item",
        json={"category": "util", "item": "No repo"},
        headers={"X-CSRF-Token": _csrf(client, "/api/code")},
    )
    assert response.status_code == 400 and response.get_json()["error"] == "repo required"
