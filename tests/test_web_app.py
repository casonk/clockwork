import os
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import tomlkit

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
sys.path.insert(0, str(_WEB_DIR))
_APP_PATH = _WEB_DIR / "app.py"
_SPEC = spec_from_file_location("clockwork_web_app", _APP_PATH)
assert _SPEC is not None and _SPEC.loader is not None
os.environ["CLOCKWORK_WEB_AUTOGENERATE_CRON"] = "0"
web_app = module_from_spec(_SPEC)
_SPEC.loader.exec_module(web_app)


def _write_bug_sweep_manifest(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """[[jobs]]
name = "bug-sweep-agentic"
description = "Daily agentic review of clean code repos for potential bugs and regressions"
scope = "user"
service_type = "oneshot"
working_directory = "%h/git/util-repos/traction-control"
exec_start = "%h/git/util-repos/traction-control/scripts/bug_sweep_agentic.sh"
environment_files = ["-%h/.config/traction-control/bug-sweep-agentic.env"]

[jobs.environment]
PORTFOLIO_ROOT = "%h/git"
BUG_SWEEP_AGENTIC_PROVIDER = "auto"
BUG_SWEEP_AGENTIC_MODEL = ""
""",
        encoding="utf-8",
    )


def test_scan_repos_includes_agent_provider_model_and_env_file(tmp_path, monkeypatch):
    manifest_path = tmp_path / "traction-control" / "bug-sweep-agentic.toml"
    _write_bug_sweep_manifest(manifest_path)
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", tmp_path)

    repos = web_app.scan_repos()

    job = repos["traction-control"]["manifests"][0]["jobs"][0]
    assert job["provider_env_key"] == "BUG_SWEEP_AGENTIC_PROVIDER"
    assert job["provider_value"] == "auto"
    assert job["model_env_key"] == "BUG_SWEEP_AGENTIC_MODEL"
    assert job["model_value"] == ""
    assert job["environment_files"] == ["-%h/.config/traction-control/bug-sweep-agentic.env"]


def test_edit_job_updates_agent_provider_and_model_defaults(tmp_path, monkeypatch):
    manifest_path = tmp_path / "traction-control" / "bug-sweep-agentic.toml"
    _write_bug_sweep_manifest(manifest_path)
    monkeypatch.setattr(web_app, "EXAMPLES_DIR", tmp_path)

    client = web_app.app.test_client()
    response = client.post(
        "/edit/job",
        data={
            "manifest_path": "traction-control/bug-sweep-agentic.toml",
            "job_name": "bug-sweep-agentic",
            "description": "Daily bug review",
            "exec_start": "%h/git/util-repos/traction-control/scripts/bug_sweep_agentic.sh",
            "working_directory": "%h/git/util-repos/traction-control",
            "provider_env_key": "BUG_SWEEP_AGENTIC_PROVIDER",
            "provider_value": "claude",
            "model_env_key": "BUG_SWEEP_AGENTIC_MODEL",
            "model_value": "claude-sonnet-4-6",
        },
        headers={"Origin": "http://localhost"},
    )

    assert response.status_code == 302

    doc = tomlkit.loads(manifest_path.read_text(encoding="utf-8"))
    job = doc["jobs"][0]
    assert job["description"] == "Daily bug review"
    assert job["environment"]["BUG_SWEEP_AGENTIC_PROVIDER"] == "claude"
    assert job["environment"]["BUG_SWEEP_AGENTIC_MODEL"] == "claude-sonnet-4-6"
