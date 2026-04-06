#!/usr/bin/env python3
"""Generate and optionally install a combined Caddyfile that serves both
files.snowbridge.internal and config.clockwork.internal from the system Caddy.

Both sites share the same server TLS certificate (issued by the clockwork CA).
Before running this script, regenerate the server cert so it covers both
hostnames:

    CLOCKWORK_WG_IP=10.99.0.1 \\
    CLOCKWORK_EXTRA_HOSTNAME=files.snowbridge.internal \\
        bash scripts/setup-mtls.sh

Typical usage (generates the Caddyfile and installs it system-wide):

    sudo python3 scripts/setup_caddy.py --system-install

This will:
  1. Copy TLS certs to /etc/caddy/certs/clockwork/  (readable by system caddy)
  2. Write /etc/caddy/Caddyfile
  3. Run caddy validate
  4. Run systemctl reload caddy

Usage:
    python3 scripts/setup_caddy.py [options]

Options:
    --system-install         Copy certs to /etc/caddy/certs/, write
                             /etc/caddy/Caddyfile, reload caddy (needs root)
    --snowbridge-env PATH    Path to snowbridge filebrowser.env.local
                             (auto-detected from sibling repo by default)
    --certs-dir DIR          User clockwork certs directory
                             (default: ~/.config/clockwork/certs)
    --sb-client-ca PATH      Snowbridge mTLS client CA cert
                             (default: <CADDY_DATA_DIR>/mtls/client-ca.crt)
    --flask-port PORT        Clockwork Flask port (default: 5000)
    --filebrowser-port PORT  Snowbridge filebrowser host port
                             (default: from env file, fallback 8080)
    --output PATH            Where to also write a reference copy of the
                             Caddyfile (default: config/web/caddy/Caddyfile.combined.local)
    --validate               Run caddy validate without installing
"""

from __future__ import annotations

import argparse
import contextlib
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DEFAULT_OUTPUT = REPO_ROOT / "config" / "web" / "caddy" / "Caddyfile.combined.local"
DEFAULT_USER_CERTS_DIR = Path.home() / ".config" / "clockwork" / "certs"
DEFAULT_SYSTEM_CERTS_DIR = Path("/etc/caddy/certs/clockwork")
SYSTEM_CADDYFILE = Path("/etc/caddy/Caddyfile")
DEFAULT_CADDY_DATA_DIR = Path("/var/lib/snowbridge/caddy/data")
DEFAULT_CLOCKWORK_HOST = "config.clockwork.internal"
DEFAULT_SNOWBRIDGE_HOST = "files.snowbridge.internal"
DEFAULT_FLASK_PORT = 5000
DEFAULT_FB_PORT = 8080


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().split("#")[0].strip().strip('"').strip("'")
        if key.strip():
            env[key.strip()] = value
    return env


def find_snowbridge_repo() -> Path | None:
    for repo in [REPO_ROOT.parent / "snowbridge", Path.home() / ".local" / "share" / "snowbridge"]:
        if (repo / "config" / "web" / "filebrowser").is_dir():
            return repo
    return None


def read_env_file(path: Path) -> dict[str, str]:
    """Read an env file, falling back to sudo cat if permission is denied."""
    try:
        return parse_env_file(path)
    except PermissionError:
        rc, out = _run(["sudo", "cat", str(path)])
        if rc != 0:
            return {}
        env: dict[str, str] = {}
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().split("#")[0].strip().strip('"').strip("'")
            if key.strip():
                env[key.strip()] = value
        return env


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def _caddy_user() -> tuple[int, int] | None:
    """Return (uid, gid) of the system caddy user, or None if not found."""
    try:
        pw = pwd.getpwnam("caddy")
        return pw.pw_uid, pw.pw_gid
    except KeyError:
        return None


def _invoking_user_home() -> Path:
    """When running under sudo, return the invoking user's home, not root's."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return Path.home()


# ---------------------------------------------------------------------------
# Caddyfile generation
# ---------------------------------------------------------------------------


def generate_caddyfile(
    *,
    snowbridge_host: str,
    clockwork_host: str,
    certs_dir: Path,
    sb_client_ca: Path,
    fb_port: int,
    flask_port: int,
) -> str:
    """Return the combined Caddyfile as a string using absolute paths."""
    cert = str(certs_dir / "server.crt")
    key  = str(certs_dir / "server.key")
    cw_ca = str(certs_dir / "ca.crt")

    def site_block(host: str, client_ca: str, proxy_target: str, comment: str,
                   proxy_headers: dict[str, str] | None = None) -> str:
        proxy_extra = ""
        if proxy_headers:
            lines = "\n".join(f"\t\theader_up {k} {v}" for k, v in proxy_headers.items())
            proxy_extra = f" {{\n{lines}\n\t}}"
        return (
            f"# {comment}\n"
            f"https://{host} {{\n"
            f"\ttls {cert} {key} {{\n"
            f"\t\tclient_auth {{\n"
            f"\t\t\tmode require_and_verify\n"
            f"\t\t\ttrust_pool file {client_ca}\n"
            f"\t\t}}\n"
            f"\t}}\n"
            f"\n"
            f"\tencode zstd gzip\n"
            f"\treverse_proxy {proxy_target}{proxy_extra}\n"
            f"\n"
            f"\theader {{\n"
            f'\t\tStrict-Transport-Security "max-age=31536000"\n'
            f'\t\tX-Content-Type-Options "nosniff"\n'
            f'\t\tX-Frame-Options "SAMEORIGIN"\n'
            f'\t\tReferrer-Policy "no-referrer"\n'
            f"\t}}\n"
            f"}}"
        )

    sb_block = site_block(
        snowbridge_host, str(sb_client_ca), f"127.0.0.1:{fb_port}",
        f"{snowbridge_host} — File Browser (snowbridge) via WireGuard + mTLS",
        # Caddy is the auth boundary in mTLS mode; inject the trusted username so
        # filebrowser can use proxy-auth and skip its own login page.
        proxy_headers={"X-Snowbridge-Auth-User": "snowbridge"},
    )
    cw_block = site_block(
        clockwork_host, cw_ca, f"127.0.0.1:{flask_port}",
        f"{clockwork_host} — Clockwork Web UI via WireGuard + mTLS",
    )

    return (
        "{\n"
        "\temail admin@example.com\n"
        "}\n\n"
        "# Combined Caddyfile — generated by scripts/setup_caddy.py\n"
        "# Server TLS cert (covers both hostnames via SAN):\n"
        f"#   {cert}\n\n"
        f"{sb_block}\n\n"
        f"{cw_block}\n"
    )


# ---------------------------------------------------------------------------
# System install
# ---------------------------------------------------------------------------


def system_install(
    *,
    user_certs_dir: Path,
    sb_client_ca: Path,
    fb_port: int,
    flask_port: int,
) -> int:
    """Copy certs to /etc/caddy/certs/clockwork/, write /etc/caddy/Caddyfile, reload."""
    if os.geteuid() != 0:
        print("error: --system-install must be run with sudo", file=sys.stderr)
        return 1

    caddy_owner = _caddy_user()

    # ── 1. Create system certs directory ─────────────────────────────────────
    DEFAULT_SYSTEM_CERTS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DEFAULT_SYSTEM_CERTS_DIR, 0o750)
    if caddy_owner:
        os.chown(DEFAULT_SYSTEM_CERTS_DIR, 0, caddy_owner[1])  # root:caddy

    # ── 2. Copy clockwork certs ───────────────────────────────────────────────
    for name, mode in [("server.crt", 0o644), ("server.key", 0o640), ("ca.crt", 0o644)]:
        src = user_certs_dir / name
        if not src.exists():
            print(f"error: cert not found: {src}", file=sys.stderr)
            print("  Run: bash scripts/setup-mtls.sh", file=sys.stderr)
            return 1
        dst = DEFAULT_SYSTEM_CERTS_DIR / name
        shutil.copy2(src, dst)
        os.chmod(dst, mode)
        if caddy_owner:
            os.chown(dst, 0, caddy_owner[1])  # root:caddy
        print(f"  copied {name} → {dst}")

    # ── 3. Copy snowbridge client CA ──────────────────────────────────────────
    sb_ca_dst = DEFAULT_SYSTEM_CERTS_DIR / "snowbridge-client-ca.crt"
    if sb_client_ca.exists():
        shutil.copy2(sb_client_ca, sb_ca_dst)
        os.chmod(sb_ca_dst, 0o644)
        if caddy_owner:
            os.chown(sb_ca_dst, 0, caddy_owner[1])
        print(f"  copied snowbridge-client-ca.crt → {sb_ca_dst}")
    else:
        print(f"warning: snowbridge client CA not found at {sb_client_ca}", file=sys.stderr)
        print("  Snowbridge mTLS client auth will not work until the CA is present.", file=sys.stderr)
        print("  Start the snowbridge Docker stack once to generate it, then re-run.", file=sys.stderr)
        # Use placeholder so Caddyfile is syntactically valid; operator must fix manually
        sb_ca_dst = sb_client_ca

    # ── 4. Generate and write /etc/caddy/Caddyfile ────────────────────────────
    content = generate_caddyfile(
        snowbridge_host=DEFAULT_SNOWBRIDGE_HOST,
        clockwork_host=DEFAULT_CLOCKWORK_HOST,
        certs_dir=DEFAULT_SYSTEM_CERTS_DIR,
        sb_client_ca=sb_ca_dst,
        fb_port=fb_port,
        flask_port=flask_port,
    )
    SYSTEM_CADDYFILE.write_text(content)
    print(f"  wrote {SYSTEM_CADDYFILE}")

    # Also keep a reference copy in the repo
    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.write_text(content)
    print(f"  wrote reference copy: {DEFAULT_OUTPUT}")

    # ── 5. Restore SELinux contexts (Fedora/RHEL — silently skipped elsewhere) ─
    rc_se, out_se = _run(["restorecon", "-Rv", str(DEFAULT_SYSTEM_CERTS_DIR)])
    if rc_se == 0:
        print(f"  restorecon: ok{(' — ' + out_se) if out_se else ''}")
        # Files copied from container-managed paths (e.g. snowbridge Docker data)
        # retain container_file_t even after restorecon because restorecon respects
        # admin-set labels.  Force all certs in our dir to httpd_config_t so the
        # caddy service (which runs confined) can actually read them.
        for cert_file in DEFAULT_SYSTEM_CERTS_DIR.iterdir():
            _run(["chcon", "-t", "httpd_config_t", str(cert_file)])
    elif rc_se == 127:
        pass  # restorecon not present (non-SELinux system)
    else:
        print(f"warning: restorecon failed: {out_se}", file=sys.stderr)

    # ── 7. Validate ───────────────────────────────────────────────────────────
    rc, out = _run(["caddy", "validate", "--config", str(SYSTEM_CADDYFILE)])
    if rc != 0:
        print(f"error: caddy validate failed:\n{out}", file=sys.stderr)
        return 1
    print("  caddy validate: ok")

    # ── 8. Reload ─────────────────────────────────────────────────────────────
    rc, out = _run(["systemctl", "restart", "caddy"], timeout=120)
    if rc != 0:
        print(f"error: systemctl reload-or-restart caddy failed:\n{out}", file=sys.stderr)
        print("  Try manually: sudo systemctl restart caddy", file=sys.stderr)
        print("  Then check:  sudo journalctl -u caddy -n 50 --no-pager", file=sys.stderr)
        return 1
    print("  systemctl reload-or-restart caddy: ok")

    # ── 9. Enable user lingering ──────────────────────────────────────────────
    sudo_user = os.environ.get("SUDO_USER")
    invoking_uid: int | None = None
    if sudo_user:
        with contextlib.suppress(KeyError):
            invoking_uid = pwd.getpwnam(sudo_user).pw_uid
        rc_l, out_l = _run(["loginctl", "enable-linger", sudo_user])
        if rc_l == 0:
            print(f"  loginctl enable-linger {sudo_user}: ok")
        else:
            print(f"warning: loginctl enable-linger failed: {out_l}", file=sys.stderr)
    else:
        print("info: SUDO_USER not set — skipping linger (run: loginctl enable-linger $USER)")

    # ── 10. Enable clockwork-web user service ─────────────────────────────────
    if sudo_user and invoking_uid is not None:
        env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{invoking_uid}"}
        try:
            r = subprocess.run(
                ["runuser", "-u", sudo_user, "--",
                 "systemctl", "--user", "enable", "--now", "clockwork-web.service"],
                capture_output=True, text=True, timeout=30, env=env,
            )
            out_cw = (r.stdout + r.stderr).strip()
            if r.returncode == 0:
                print("  systemctl --user enable --now clockwork-web.service: ok")
            else:
                print(f"warning: clockwork-web enable failed: {out_cw}", file=sys.stderr)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            print(f"warning: could not enable clockwork-web: {exc}", file=sys.stderr)
    else:
        print("info: skipping clockwork-web enable (run: systemctl --user enable --now clockwork-web.service)")

    print()
    print(f"  https://{DEFAULT_SNOWBRIDGE_HOST}  → 127.0.0.1:{fb_port}")
    print(f"  https://{DEFAULT_CLOCKWORK_HOST}  → 127.0.0.1:{flask_port}")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Generate and optionally install combined Caddyfile.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--system-install", action="store_true",
                   help="Copy certs to /etc/caddy/, write Caddyfile, reload (needs root)")
    p.add_argument("--snowbridge-env", metavar="PATH",
                   help="Path to snowbridge filebrowser.env.local")
    default_certs_dir = _invoking_user_home() / ".config" / "clockwork" / "certs"
    p.add_argument("--certs-dir", metavar="DIR", default=str(default_certs_dir),
                   help="User clockwork certs dir (default: <invoking-user-home>/.config/clockwork/certs)")
    p.add_argument("--sb-client-ca", metavar="PATH",
                   help="Snowbridge mTLS client CA cert path")
    p.add_argument("--flask-port", type=int, default=DEFAULT_FLASK_PORT)
    p.add_argument("--filebrowser-port", type=int)
    p.add_argument("--output", metavar="PATH", default=str(DEFAULT_OUTPUT),
                   help=f"Reference Caddyfile output (default: {DEFAULT_OUTPUT})")
    p.add_argument("--validate", action="store_true",
                   help="Run caddy validate on the generated file")
    args = p.parse_args(argv)

    certs_dir = Path(args.certs_dir).expanduser()

    # ── Snowbridge env ────────────────────────────────────────────────────────
    sb_env: dict[str, str] = {}
    sb_repo: Path | None = None

    if args.snowbridge_env:
        sb_env_path = Path(args.snowbridge_env).expanduser()
        if not sb_env_path.exists():
            print(f"error: --snowbridge-env not found: {sb_env_path}", file=sys.stderr)
            return 1
        sb_env = read_env_file(sb_env_path)
        sb_repo = sb_env_path.parent.parent.parent.parent
    else:
        sb_repo = find_snowbridge_repo()
        if sb_repo:
            env_path = sb_repo / "config" / "web" / "filebrowser" / "filebrowser.env.local"
            if env_path.exists():
                sb_env = read_env_file(env_path)
                print(f"info: snowbridge env: {env_path}")
        else:
            print("warning: snowbridge repo not found — using default ports", file=sys.stderr)

    # ── Resolve ports ─────────────────────────────────────────────────────────
    fb_port: int
    if args.filebrowser_port:
        fb_port = args.filebrowser_port
    else:
        try:
            fb_port = int(sb_env.get("FILEBROWSER_HTTP_PORT", str(DEFAULT_FB_PORT)))
        except ValueError:
            fb_port = DEFAULT_FB_PORT

    # ── Resolve snowbridge mTLS client CA ─────────────────────────────────────
    if args.sb_client_ca:
        sb_client_ca = Path(args.sb_client_ca).expanduser()
    else:
        caddy_data_dir = Path(sb_env.get("CADDY_DATA_DIR", str(DEFAULT_CADDY_DATA_DIR)))
        sb_client_ca = caddy_data_dir / "mtls" / "client-ca.crt"

    # ── System install ────────────────────────────────────────────────────────
    if args.system_install:
        return system_install(
            user_certs_dir=certs_dir,
            sb_client_ca=sb_client_ca,
            fb_port=fb_port,
            flask_port=args.flask_port,
        )

    # ── Reference Caddyfile only ──────────────────────────────────────────────
    # For the reference copy, use system cert paths if they exist, else user paths.
    if DEFAULT_SYSTEM_CERTS_DIR.exists():
        caddyfile_certs_dir = DEFAULT_SYSTEM_CERTS_DIR
        caddyfile_sb_ca = DEFAULT_SYSTEM_CERTS_DIR / "snowbridge-client-ca.crt"
    else:
        caddyfile_certs_dir = certs_dir
        caddyfile_sb_ca = sb_client_ca

    content = generate_caddyfile(
        snowbridge_host=DEFAULT_SNOWBRIDGE_HOST,
        clockwork_host=DEFAULT_CLOCKWORK_HOST,
        certs_dir=caddyfile_certs_dir,
        sb_client_ca=caddyfile_sb_ca,
        fb_port=fb_port,
        flask_port=args.flask_port,
    )

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)
    print(f"wrote: {output_path}")

    if args.validate:
        rc, out = _run(["caddy", "validate", "--config", str(output_path)])
        if rc != 0:
            print(f"caddy validate failed:\n{out}", file=sys.stderr)
            return 1
        print("caddy validate: ok")

    sb_compose = (sb_repo or Path("/path/to/snowbridge")) / "config" / "web" / "filebrowser" / "docker-compose.local.yml"
    print()
    print("To install system-wide (run once; needs root):")
    print("  sudo python3 scripts/setup_caddy.py --system-install")
    print()
    print("Or manually:")
    print(f"  sudo podman-compose -f {sb_compose} stop caddy")
    print("  sudo python3 scripts/setup_caddy.py --system-install")

    return 0


if __name__ == "__main__":
    sys.exit(main())
