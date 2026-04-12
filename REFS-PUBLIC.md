# REFS-PUBLIC.md - Public References

> Record external public repositories, datasets, documentation, APIs, or other
> public resources that this repository utilizes or depends on.
> This file is tracked and intentionally kept free of private or local-only details.

## Public Repositories

- No fixed external code repository is the main upstream; the repo renders local scheduler manifests into systemd and cron text.

## Public Datasets and APIs

- No standing public data APIs are required; all manifests and scheduler targets are local.

## Documentation and Specifications

- https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html - systemd service-unit reference
- https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html - systemd timer reference
- https://man7.org/linux/man-pages/man5/crontab.5.html - cron syntax reference used by the cron renderer
- https://flask.palletsprojects.com/ - Flask documentation for the local web UI

## Notes

- clockwork is a local render/install tool. The durable external references are the scheduler specifications it targets, not any upstream service API.
