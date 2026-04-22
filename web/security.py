"""Security helpers for the clockwork web UI."""

from __future__ import annotations

import ipaddress
import os
import ssl
from urllib.parse import urlparse


def env_truthy(name: str) -> bool:
    """Return True when *name* is set to a common truthy value."""
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def is_loopback_host(host: str) -> bool:
    """Return True when *host* is a loopback literal or localhost."""
    candidate = host.strip()
    if not candidate:
        return False
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    if candidate.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def same_origin_host(origin_or_referrer: str, host: str) -> bool:
    """Return True when the supplied URL targets the current request host."""
    parsed = urlparse(origin_or_referrer)
    return bool(parsed.scheme and parsed.netloc and parsed.netloc == host)


def has_client_authenticated_tls(ssl_context: ssl.SSLContext | None) -> bool:
    """Return True when the TLS config requires a trusted client certificate."""
    return ssl_context is not None and ssl_context.verify_mode == ssl.CERT_REQUIRED


def validate_remote_bind(host: str, ssl_context: ssl.SSLContext | None) -> None:
    """Reject unsafe remote exposure unless the operator explicitly opts in."""
    if is_loopback_host(host):
        return
    if not env_truthy("CLOCKWORK_WEB_ALLOW_REMOTE"):
        raise ValueError(
            "Refusing to bind clockwork-web to a non-loopback host without "
            "CLOCKWORK_WEB_ALLOW_REMOTE=1."
        )
    if has_client_authenticated_tls(ssl_context):
        return
    if env_truthy("CLOCKWORK_WEB_ALLOW_REMOTE_WITHOUT_MTLS"):
        return
    raise ValueError(
        "Refusing to expose clockwork-web remotely without client-authenticated TLS. "
        "Configure CLOCKWORK_WEB_CERT/CLOCKWORK_WEB_KEY/CLOCKWORK_WEB_CA or set "
        "CLOCKWORK_WEB_ALLOW_REMOTE_WITHOUT_MTLS=1 to override."
    )
