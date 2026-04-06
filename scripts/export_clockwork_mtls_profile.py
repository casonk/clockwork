#!/usr/bin/env python3
"""Generate a per-device iPhone-installable mTLS identity profile for Clockwork Web."""

from __future__ import annotations

import argparse
import grp
import hashlib
import os
import plistlib
import pwd
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn


DEFAULT_SHARE_TMP = Path("/srv/snowbridge/share/tmp")
DEFAULT_OWNER = "snowbridge"
DEFAULT_GROUP = "snowbridge"
DEFAULT_DEVICE_NAME = "iphone"
DEFAULT_PROFILE_IDENTIFIER_PREFIX = "local.clockwork.web-mtls"
DEFAULT_ORGANIZATION = "clockwork"
SLUG_PREFIX = "clockwork-web-mtls"


def _invoking_user_home() -> Path:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return Path.home()


def _default_ca_cert() -> Path:
    return _invoking_user_home() / ".config" / "clockwork" / "certs" / "ca.crt"


def _default_ca_key() -> Path:
    return _invoking_user_home() / ".config" / "clockwork" / "certs" / "ca.key"


def _default_issued_dir() -> Path:
    return _invoking_user_home() / ".config" / "clockwork" / "certs" / "issued"


class SetupError(RuntimeError):
    """Raised when mTLS profile generation cannot continue safely."""


@dataclass(frozen=True)
class Ownership:
    uid: int
    gid: int
    owner_name: str
    group_name: str


@dataclass(frozen=True)
class IdentityPaths:
    slug: str
    cert_path: Path
    key_path: Path
    p12_path: Path
    passphrase_path: Path
    serial_path: Path
    staged_profile_path: Path
    staged_p12_path: Path


def log(message: str) -> None:
    print(message)


def fail(message: str) -> NoReturn:
    raise SetupError(message)


def require_root() -> None:
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        fail("run as root so the issued identity and staged mobileconfig can be written safely")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a per-device iPhone-installable mTLS client identity profile for Clockwork Web.",
    )
    parser.add_argument(
        "--device-name",
        default=DEFAULT_DEVICE_NAME,
        help=f"Human-readable device label used in filenames and certificate subject. Default: {DEFAULT_DEVICE_NAME}",
    )
    parser.add_argument(
        "--ca-cert",
        default=None,
        help=f"Path to the Clockwork CA certificate (used for both server trust and client signing). Default: ~/.config/clockwork/certs/ca.crt",
    )
    parser.add_argument(
        "--ca-key",
        default=None,
        help="Path to the Clockwork CA private key. Default: ~/.config/clockwork/certs/ca.key",
    )
    parser.add_argument(
        "--issued-dir",
        default=None,
        help="Directory to store the issued client identity artifacts. Default: ~/.config/clockwork/certs/issued",
    )
    parser.add_argument(
        "--output",
        help=f"Output .mobileconfig path. Default: {DEFAULT_SHARE_TMP}/{SLUG_PREFIX}-<device>.mobileconfig",
    )
    parser.add_argument(
        "--p12-output",
        help=f"Optional staged .p12 copy path. Default: {DEFAULT_SHARE_TMP}/{SLUG_PREFIX}-<device>.p12",
    )
    parser.add_argument(
        "--owner",
        default=DEFAULT_OWNER,
        help=f"Owner for staged files and directories. Default: {DEFAULT_OWNER}",
    )
    parser.add_argument(
        "--group",
        default=DEFAULT_GROUP,
        help=f"Group for staged files and directories. Default: {DEFAULT_GROUP}",
    )
    parser.add_argument(
        "--organization",
        default=DEFAULT_ORGANIZATION,
        help=f"Organization string shown on iPhone. Default: {DEFAULT_ORGANIZATION}",
    )
    parser.add_argument(
        "--profile-identifier-prefix",
        default=DEFAULT_PROFILE_IDENTIFIER_PREFIX,
        help=f"Profile identifier prefix. Default: {DEFAULT_PROFILE_IDENTIFIER_PREFIX}",
    )
    parser.add_argument(
        "--profile-name",
        help="Human-readable profile name shown on iPhone. Default: Clockwork Web mTLS (<device>)",
    )
    parser.add_argument(
        "--identity-passphrase",
        help="Passphrase used to encrypt the PKCS#12 identity. Default: auto-generated and stored locally in the issued-dir metadata.",
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Replace any existing issued identity for this device with a freshly signed one.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the actions that would run without changing the host.",
    )
    return parser.parse_args()


def slugify_device_name(device_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", device_name.strip().lower()).strip("-")
    if not slug:
        fail("device name must contain at least one alphanumeric character")
    return slug


def resolve_ownership(owner_name: str, group_name: str) -> Ownership:
    try:
        owner = pwd.getpwnam(owner_name)
    except KeyError:
        fail(f"owner account not found: {owner_name}")
    try:
        group = grp.getgrnam(group_name)
    except KeyError:
        fail(f"group not found: {group_name}")
    return Ownership(uid=owner.pw_uid, gid=group.gr_gid, owner_name=owner_name, group_name=group_name)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def ensure_openssl() -> None:
    if command_exists("openssl"):
        return
    fail("openssl is not installed")


def ensure_directory(path: Path, mode: int, ownership: Ownership | None = None, dry_run: bool = False) -> None:
    if dry_run:
        owner_fragment = ""
        if ownership is not None:
            owner_fragment = f" owner={ownership.owner_name}:{ownership.group_name}"
        log(f"would ensure directory {path} mode={mode:o}{owner_fragment}")
        return
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, mode)
    if ownership is not None:
        os.chown(path, ownership.uid, ownership.gid)


def write_file(path: Path, content: bytes, mode: int, ownership: Ownership | None, dry_run: bool) -> None:
    ensure_directory(path.parent, 0o2770 if ownership else 0o750, ownership, dry_run)
    if dry_run:
        log(f"would write {path} mode={mode:o}")
        return
    path.write_bytes(content)
    os.chmod(path, mode)
    if ownership is not None:
        os.chown(path, ownership.uid, ownership.gid)


def copy_file(source: Path, target: Path, mode: int, ownership: Ownership, dry_run: bool) -> None:
    ensure_directory(target.parent, 0o2770, ownership, dry_run)
    if dry_run:
        log(f"would copy {source} -> {target} mode={mode:o}")
        return
    shutil.copy2(source, target)
    os.chmod(target, mode)
    os.chown(target, ownership.uid, ownership.gid)


def load_certificate_der(cert_path: Path) -> bytes:
    if not cert_path.is_file():
        fail(f"certificate not found: {cert_path}")
    raw = cert_path.read_bytes()
    if b"-----BEGIN CERTIFICATE-----" not in raw:
        return raw
    try:
        pem_text = raw.decode("ascii")
    except UnicodeDecodeError:
        fail(f"certificate looks PEM-encoded but is not ASCII-readable: {cert_path}")
    try:
        der_bytes = ssl.PEM_cert_to_DER_cert(pem_text)
    except ValueError as exc:
        fail(f"unable to parse PEM certificate at {cert_path}: {exc}")
    if isinstance(der_bytes, str):
        return der_bytes.encode("latin1")
    return der_bytes


def stable_uuid(label: str, digest: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{label}:{digest}")).upper()


def build_identity_paths(device_name: str, issued_dir: Path, output: str | None, p12_output: str | None) -> IdentityPaths:
    slug = slugify_device_name(device_name)
    staged_profile_path = (
        Path(output).expanduser().resolve()
        if output
        else (DEFAULT_SHARE_TMP / f"{SLUG_PREFIX}-{slug}.mobileconfig").resolve()
    )
    staged_p12_path = (
        Path(p12_output).expanduser().resolve()
        if p12_output
        else (DEFAULT_SHARE_TMP / f"{SLUG_PREFIX}-{slug}.p12").resolve()
    )
    prefix = issued_dir / f"{SLUG_PREFIX}-{slug}"
    return IdentityPaths(
        slug=slug,
        cert_path=prefix.with_suffix(".crt"),
        key_path=prefix.with_suffix(".key"),
        p12_path=prefix.with_suffix(".p12"),
        passphrase_path=prefix.with_suffix(".passphrase"),
        serial_path=issued_dir / "ca.srl",
        staged_profile_path=staged_profile_path,
        staged_p12_path=staged_p12_path,
    )


def run_command(command: list[str], *, env: dict[str, str] | None = None) -> None:
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        stdout = exc.stdout.strip()
        detail = stderr or stdout or str(exc)
        fail(f"command failed: {' '.join(command)}: {detail}")


def load_or_create_passphrase(passphrase_path: Path, explicit_passphrase: str | None, rotate: bool, dry_run: bool) -> str:
    if explicit_passphrase is not None:
        if not explicit_passphrase:
            fail("identity passphrase cannot be empty")
        if not dry_run:
            ensure_directory(passphrase_path.parent, 0o750)
            passphrase_path.write_text(explicit_passphrase + "\n", encoding="utf-8")
            os.chmod(passphrase_path, 0o600)
        return explicit_passphrase

    if passphrase_path.exists() and not rotate:
        passphrase = passphrase_path.read_text(encoding="utf-8").strip()
        if not passphrase:
            fail(f"stored passphrase file is empty: {passphrase_path}")
        return passphrase

    passphrase = secrets.token_urlsafe(24)
    if not dry_run:
        ensure_directory(passphrase_path.parent, 0o750)
        passphrase_path.write_text(passphrase + "\n", encoding="utf-8")
        os.chmod(passphrase_path, 0o600)
    return passphrase


def ensure_client_identity(
    *,
    ca_cert: Path,
    ca_key: Path,
    identity: IdentityPaths,
    device_name: str,
    passphrase: str,
    rotate: bool,
    dry_run: bool,
) -> None:
    has_cert = identity.cert_path.exists()
    has_key = identity.key_path.exists()
    has_p12 = identity.p12_path.exists()
    has_passphrase = identity.passphrase_path.exists()
    artifact_flags = [has_cert, has_key, has_p12]

    if any(artifact_flags) and (not all(artifact_flags) or not has_passphrase) and not rotate:
        fail(
            "existing client identity state is incomplete; expected cert, key, p12, and passphrase files together "
            f"under {identity.cert_path.parent}. Re-run with --rotate or repair the partial files manually."
        )
    if all(artifact_flags) and has_passphrase and not rotate:
        return

    ensure_openssl()
    ensure_directory(identity.cert_path.parent, 0o750, dry_run=dry_run)

    if dry_run:
        log(f"would issue a fresh mTLS client identity for {device_name} under {identity.cert_path.parent}")
        return

    for path in (identity.cert_path, identity.key_path, identity.p12_path):
        path.unlink(missing_ok=True)

    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".cnf") as handle:
        handle.write(
            "[v3_client]\n"
            "basicConstraints = critical,CA:FALSE\n"
            "keyUsage = critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage = clientAuth\n"
            "subjectKeyIdentifier = hash\n"
            "authorityKeyIdentifier = keyid,issuer\n"
        )
        ext_path = Path(handle.name)

    csr_path = identity.cert_path.with_suffix(".csr")
    env = os.environ.copy()
    env["CLOCKWORK_P12_PASS"] = passphrase

    try:
        run_command(
            [
                "openssl", "req", "-new",
                "-newkey", "rsa:2048",
                "-nodes",
                "-keyout", str(identity.key_path),
                "-out", str(csr_path),
                "-subj", f"/CN=Clockwork {device_name} Client/O=Portfolio/OU=Admin",
            ]
        )
        run_command(
            [
                "openssl", "x509", "-req",
                "-in", str(csr_path),
                "-CA", str(ca_cert),
                "-CAkey", str(ca_key),
                "-CAserial", str(identity.serial_path),
                "-CAcreateserial",
                "-out", str(identity.cert_path),
                "-days", "825",
                "-sha256",
                "-extfile", str(ext_path),
                "-extensions", "v3_client",
            ]
        )
        run_command(
            [
                "openssl", "pkcs12", "-export",
                "-name", f"Clockwork {device_name} Client",
                "-inkey", str(identity.key_path),
                "-in", str(identity.cert_path),
                "-certfile", str(ca_cert),
                "-out", str(identity.p12_path),
                "-passout", "env:CLOCKWORK_P12_PASS",
            ],
            env=env,
        )
        os.chmod(identity.key_path, 0o600)
        os.chmod(identity.cert_path, 0o644)
        os.chmod(identity.p12_path, 0o600)
    finally:
        csr_path.unlink(missing_ok=True)
        ext_path.unlink(missing_ok=True)

    log(f"issued mTLS client identity for {device_name}")


def build_mobileconfig(
    *,
    ca_cert_der: bytes,
    p12_bytes: bytes,
    profile_identifier: str,
    profile_name: str,
    organization: str,
    device_name: str,
    p12_file_name: str,
    ca_cert_file_name: str,
) -> bytes:
    digest = hashlib.sha256(ca_cert_der + p12_bytes).hexdigest()
    profile = {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": profile_identifier,
        "PayloadUUID": stable_uuid("clockwork-mtls-profile", digest),
        "PayloadDisplayName": profile_name,
        "PayloadDescription": (
            f"Trust profile and client identity for Clockwork Web mTLS access on {device_name}."
        ),
        "PayloadOrganization": organization,
        "PayloadRemovalDisallowed": False,
        "PayloadContent": [
            {
                "PayloadType": "com.apple.security.root",
                "PayloadVersion": 1,
                "PayloadIdentifier": f"{profile_identifier}.root",
                "PayloadUUID": stable_uuid("clockwork-mtls-root", digest),
                "PayloadDisplayName": "Clockwork CA",
                "PayloadDescription": (
                    "Installs the Clockwork CA so the device trusts "
                    "the private HTTPS endpoint over WireGuard."
                ),
                "PayloadCertificateFileName": ca_cert_file_name,
                "PayloadContent": ca_cert_der,
            },
            {
                "PayloadType": "com.apple.security.pkcs12",
                "PayloadVersion": 1,
                "PayloadIdentifier": f"{profile_identifier}.identity",
                "PayloadUUID": stable_uuid("clockwork-mtls-identity", digest),
                "PayloadDisplayName": f"Clockwork mTLS Client Identity ({device_name})",
                "PayloadDescription": (
                    "Installs the client certificate required for "
                    "Clockwork private mTLS access."
                ),
                "PayloadCertificateFileName": p12_file_name,
                "PayloadContent": p12_bytes,
            },
        ],
    }
    return plistlib.dumps(profile, fmt=plistlib.FMT_XML, sort_keys=False)


def summarize_install_steps(profile_path: Path, p12_path: Path, passphrase_path: Path, device_name: str) -> None:
    log("next iPhone steps:")
    log(f"  1. Open {profile_path.name} from the snowbridge SMB share in Files.")
    log("  2. Tap Allow if iPhone asks to download the profile.")
    log("  3. Open Settings, then tap Profile Downloaded.")
    log("  4. Install the profile. When iPhone asks for the identity password, enter the value printed below.")
    log("  5. Go to Settings > General > About > Certificate Trust Settings.")
    log("  6. Enable full trust for the Clockwork CA certificate.")
    log(f"fallback artifact: {p12_path}")
    log(f"stored local passphrase metadata: {passphrase_path}")
    log(f"device label: {device_name}")


def main() -> int:
    args = parse_args()
    device_name = args.device_name.strip()
    slug = slugify_device_name(device_name)
    profile_name = args.profile_name or f"Clockwork Web mTLS ({device_name})"
    profile_identifier = f"{args.profile_identifier_prefix}.{slug}"

    ca_cert = Path(args.ca_cert).expanduser().resolve() if args.ca_cert else _default_ca_cert()
    ca_key = Path(args.ca_key).expanduser().resolve() if args.ca_key else _default_ca_key()
    issued_dir = Path(args.issued_dir).expanduser().resolve() if args.issued_dir else _default_issued_dir()
    identity = build_identity_paths(device_name, issued_dir, args.output, args.p12_output)
    ownership = resolve_ownership(args.owner, args.group)

    try:
        if not args.dry_run:
            require_root()

        if not ca_cert.is_file():
            fail(
                f"CA certificate not found: {ca_cert}. "
                "Run scripts/setup-mtls.sh first to generate the Clockwork CA."
            )
        if not ca_key.is_file():
            fail(
                f"CA private key not found: {ca_key}. "
                "Run scripts/setup-mtls.sh first to generate the Clockwork CA."
            )

        passphrase = load_or_create_passphrase(identity.passphrase_path, args.identity_passphrase, args.rotate, args.dry_run)
        ensure_client_identity(
            ca_cert=ca_cert,
            ca_key=ca_key,
            identity=identity,
            device_name=device_name,
            passphrase=passphrase,
            rotate=args.rotate,
            dry_run=args.dry_run,
        )

        if args.dry_run:
            log(f"would stage {identity.staged_profile_path}")
            log(f"would stage {identity.staged_p12_path}")
            log("identity import password:")
            log(f"  {passphrase}")
            return 0

        ca_cert_der = load_certificate_der(ca_cert)
        p12_bytes = identity.p12_path.read_bytes()
        mobileconfig = build_mobileconfig(
            ca_cert_der=ca_cert_der,
            p12_bytes=p12_bytes,
            profile_identifier=profile_identifier,
            profile_name=profile_name,
            organization=args.organization,
            device_name=device_name,
            p12_file_name=identity.p12_path.name,
            ca_cert_file_name=ca_cert.name,
        )

        write_file(identity.staged_profile_path, mobileconfig, 0o644, ownership, args.dry_run)
        copy_file(identity.p12_path, identity.staged_p12_path, 0o640, ownership, args.dry_run)

        log(f"staged {identity.staged_profile_path}")
        log(f"staged {identity.staged_p12_path}")
        log("identity import password:")
        log(f"  {passphrase}")
        summarize_install_steps(identity.staged_profile_path, identity.staged_p12_path, identity.passphrase_path, device_name)
        return 0
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
