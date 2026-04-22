import ssl
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_SECURITY_PATH = Path(__file__).resolve().parent.parent / "web" / "security.py"
_SPEC = spec_from_file_location("clockwork_web_security", _SECURITY_PATH)
assert _SPEC is not None and _SPEC.loader is not None
security = module_from_spec(_SPEC)
_SPEC.loader.exec_module(security)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "[::1]", "localhost"])
def test_validate_remote_bind_allows_loopback(host, monkeypatch):
    monkeypatch.delenv("CLOCKWORK_WEB_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("CLOCKWORK_WEB_ALLOW_REMOTE_WITHOUT_MTLS", raising=False)

    security.validate_remote_bind(host, None)


def test_validate_remote_bind_rejects_non_loopback_without_opt_in(monkeypatch):
    monkeypatch.delenv("CLOCKWORK_WEB_ALLOW_REMOTE", raising=False)
    monkeypatch.delenv("CLOCKWORK_WEB_ALLOW_REMOTE_WITHOUT_MTLS", raising=False)

    with pytest.raises(ValueError, match="CLOCKWORK_WEB_ALLOW_REMOTE=1"):
        security.validate_remote_bind("0.0.0.0", None)


def test_validate_remote_bind_rejects_remote_without_client_tls(monkeypatch):
    monkeypatch.setenv("CLOCKWORK_WEB_ALLOW_REMOTE", "1")
    monkeypatch.delenv("CLOCKWORK_WEB_ALLOW_REMOTE_WITHOUT_MTLS", raising=False)

    with pytest.raises(ValueError, match="client-authenticated TLS"):
        security.validate_remote_bind("0.0.0.0", None)


def test_validate_remote_bind_allows_remote_with_client_tls(monkeypatch):
    monkeypatch.setenv("CLOCKWORK_WEB_ALLOW_REMOTE", "1")
    monkeypatch.delenv("CLOCKWORK_WEB_ALLOW_REMOTE_WITHOUT_MTLS", raising=False)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.verify_mode = ssl.CERT_REQUIRED

    security.validate_remote_bind("0.0.0.0", context)


def test_same_origin_host_requires_matching_host_and_port():
    assert (
        security.same_origin_host("https://clockwork.example:5000/", "clockwork.example:5000")
        is True
    )
    assert security.same_origin_host("https://clockwork.example/", "127.0.0.1:5000") is False
