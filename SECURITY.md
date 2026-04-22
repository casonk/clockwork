# Security Policy

## Scope

`clockwork` renders scheduler artifacts. It must never become a place to store
live secrets, crontab exports with credentials, or host-specific private data.
Its web UI can trigger scheduler state changes, so deployment boundaries matter:
the standalone Flask app is intended to stay on `localhost` by default, with
Caddy and mTLS providing the remote trust boundary when the UI is exposed over
WireGuard.

## Reporting

Report security issues privately to the repository owner instead of opening a
public issue with exploit details.

## Handling Rules

- Keep secrets in external env files or secret stores, not in manifests.
- Use generic paths and placeholder usernames in tracked examples unless an
  exact path is required to explain the workflow.
- Treat generated unit files and crontab snippets as reviewable text artifacts;
  do not add hidden shell expansion or remote download behavior to install
  flows.
- Do not expose `clockwork-web` on a public network interface without
  client-authenticated TLS or another explicit authentication layer.
- Treat scheduler state, unit names, logs, and local service topology as
  potentially sensitive operational context when writing issues or screenshots.
