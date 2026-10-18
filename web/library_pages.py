"""Web pages for the watch library: title details, playback, folder browsing, management.

The search page (/library) and its JSON API live in app.py; these pages build
on the same catalog (media_library.CATALOG_DB) and library root.
"""

from __future__ import annotations

import datetime
import json
import mimetypes
import re
import subprocess
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

try:
    from web import media_library as ml
except ModuleNotFoundError:
    import media_library as ml

library_pages = Blueprint("library_pages", __name__)

SYNC_UNIT = "clockwork-media-library"
# Containers and codecs most browsers play without transcoding.
_PLAYABLE_CONTAINERS = {".mp4", ".m4v", ".webm", ".mov"}
_PLAYABLE_CODECS = {"h264", "vp8", "vp9", "av1", None}
mimetypes.add_type("video/x-matroska", ".mkv")
mimetypes.add_type("text/vtt", ".vtt")


def _root() -> Path:
    return ml.LIBRARY_ROOT


def _resolve(rel: str) -> Path:
    """Map a library-relative path to disk, refusing anything outside or hidden."""
    rel = rel.strip("/")
    parts = Path(rel).parts
    if any(part in ("..", "") or part.startswith(".") for part in parts):
        abort(404)
    root = _root().resolve()
    path = (root / rel).resolve()
    if path != root and root not in path.parents:
        abort(404)
    if not path.exists():
        abort(404)
    return path


def _rel(path: Path) -> str:
    return str(path.resolve().relative_to(_root().resolve()))


def _catalog():
    if not ml.CATALOG_DB.exists():
        return None
    return ml.connect(ml.CATALOG_DB)


def _title_row(conn, folder: Path):
    if conn is None:
        return None
    for key in {str(_root() / _rel(folder)), str(folder)}:
        row = conn.execute("SELECT * FROM titles WHERE folder = ?", (key,)).fetchone()
        if row:
            return row
    return None


def _format_size(size: int | None) -> str:
    if not size:
        return ""
    gb = size / 1024**3
    return f"{gb:.1f} GB" if gb >= 1 else f"{size / 1024**2:.0f} MB"


def _companions(video: Path) -> list[dict[str, str]]:
    """Subtitles and audio tracks sharing the video's stem, with their language tag."""
    prefix = f"{video.stem}."
    found = []
    for sibling in sorted(video.parent.iterdir()):
        if (
            sibling.is_file()
            and sibling.name.startswith(prefix)
            and sibling.suffix.lower() in ml.COMPANION_EXTS
        ):
            tags = sibling.name[len(prefix) : -len(sibling.suffix)].split(".")
            label = ".".join(t for t in tags if t) or (
                "audio" if sibling.suffix.lower() in ml.AUDIO_EXTS else "subtitles"
            )
            found.append(
                {
                    "name": sibling.name,
                    "rel": _rel(sibling),
                    "label": label,
                    "kind": "audio" if sibling.suffix.lower() in ml.AUDIO_EXTS else "subtitle",
                }
            )
    return found


def _file_info(conn, video: Path) -> dict:
    row = None
    if conn is not None:
        for key in {str(_root() / _rel(video)), str(video)}:
            row = conn.execute("SELECT * FROM files WHERE path = ?", (key,)).fetchone()
            if row:
                break
    info = dict(row) if row else {}
    codec = info.get("video_codec")
    playable = video.suffix.lower() in _PLAYABLE_CONTAINERS and codec in _PLAYABLE_CODECS
    return {
        "name": video.name,
        "rel": _rel(video),
        "size": _format_size(video.stat().st_size),
        "quality": info.get("quality"),
        "video_codec": codec,
        "audio_codec": info.get("audio_codec"),
        "channels": info.get("channels"),
        "duration_min": round(info["duration_s"] / 60) if info.get("duration_s") else None,
        "season": info.get("season"),
        "episode": info.get("episode"),
        "playable": playable,
        "companions": _companions(video),
    }


def _breadcrumbs(rel: str) -> list[dict[str, str]]:
    crumbs = [{"name": "watch", "rel": ""}]
    parts = Path(rel).parts if rel else ()
    for index, part in enumerate(parts):
        crumbs.append({"name": part, "rel": str(Path(*parts[: index + 1]))})
    return crumbs


# ---------------------------------------------------------------------------
# Title details
# ---------------------------------------------------------------------------


@library_pages.get("/library/title/<path:rel>")
def title_page(rel: str):
    folder = _resolve(rel)
    if not folder.is_dir():
        abort(404)
    conn = _catalog()
    try:
        row = _title_row(conn, folder)
        meta = {}
        if row:
            meta = dict(row)
            for column in ("directors", "cast_members", "genres", "countries", "languages"):
                meta[column] = json.loads(meta.get(column) or "[]")
        files = [_file_info(conn, v) for v in ml._video_files(folder)]
    finally:
        if conn is not None:
            conn.close()
    match = re.match(r"^(?P<title>.+?)(?: \((?P<year>\d{4})\))?$", folder.name)
    kind = ml.SHOWS if _rel(folder).startswith(ml.SHOWS + "/") else ml.MOVIES
    seasons: dict[int, list[dict]] = {}
    if kind == ml.SHOWS:
        for item in files:
            seasons.setdefault(item["season"] or 0, []).append(item)
        for items in seasons.values():
            items.sort(key=lambda f: (f["episode"] or 0, f["name"]))
    extras = [
        {"name": v.name, "rel": _rel(v), "size": _format_size(v.stat().st_size)}
        for v in ml._video_files(folder, include_extras=True)
        if ml._in_extras(v, folder)
    ]
    genre = folder.parent.name if folder.parent.name in ml.GENRE_FOLDERS else None
    return render_template(
        "library-title.html",
        rel=_rel(folder),
        title=meta.get("title") or (match.group("title") if match else folder.name),
        year=meta.get("year") or (match.group("year") if match else None),
        kind=kind,
        meta=meta,
        files=files,
        seasons=sorted(seasons.items()),
        extras=extras,
        genre=genre,
        genres=ml.GENRE_FOLDERS,
        downloaded=(
            datetime.datetime.fromtimestamp(meta["downloaded_at"]).strftime("%Y-%m-%d")
            if meta.get("downloaded_at")
            else None
        ),
        breadcrumbs=_breadcrumbs(_rel(folder)),
    )


@library_pages.post("/library/title/<path:rel>/genre")
def change_genre(rel: str):
    folder = _resolve(rel)
    genre = request.form.get("genre", "")
    conn = _catalog()
    try:
        target = ml.move_to_genre(_root(), _root() / _rel(folder), genre, conn)
    except (ValueError, FileExistsError, OSError) as exc:
        flash(f"Could not move: {exc}", "error")
        return redirect(url_for("library_pages.title_page", rel=rel))
    finally:
        if conn is not None:
            conn.close()
    flash(f"Moved to {genre}.", "success")
    return redirect(url_for("library_pages.title_page", rel=_rel(target)))


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------


@library_pages.get("/library/play/<path:rel>")
def play_page(rel: str):
    video = _resolve(rel)
    if not video.is_file() or video.suffix.lower() not in ml.VIDEO_EXTS:
        abort(404)
    conn = _catalog()
    try:
        info = _file_info(conn, video)
    finally:
        if conn is not None:
            conn.close()
    tracks = [
        c
        for c in info["companions"]
        if c["kind"] == "subtitle" and c["name"].lower().endswith((".srt", ".vtt"))
    ]
    title_dir = video.parent
    if re.fullmatch(r"Season \d{2}", title_dir.name):
        title_dir = title_dir.parent
    return render_template(
        "library-play.html",
        info=info,
        tracks=tracks,
        title_rel=_rel(title_dir),
        title_name=title_dir.name,
    )


@library_pages.get("/library/media/<path:rel>")
def media_file(rel: str):
    path = _resolve(rel)
    if not path.is_file() or path.suffix.lower() not in ml.VIDEO_EXTS | ml.COMPANION_EXTS:
        abort(404)
    download = request.args.get("download") == "1"
    # conditional=True answers Range requests, which seeking in <video> needs.
    return send_file(path, conditional=True, as_attachment=download, download_name=path.name)


def srt_to_vtt(text: str) -> str:
    body = text.lstrip("﻿").replace("\r\n", "\n")
    body = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", body)
    return "WEBVTT\n\n" + body


@library_pages.get("/library/subtitle/<path:rel>")
def subtitle_vtt(rel: str):
    path = _resolve(rel)
    if not path.is_file() or path.suffix.lower() not in (".srt", ".vtt"):
        abort(404)
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    if path.suffix.lower() == ".srt":
        text = srt_to_vtt(text)
    return Response(text, mimetype="text/vtt")


# ---------------------------------------------------------------------------
# Folder browser
# ---------------------------------------------------------------------------


@library_pages.get("/library/browse/")
@library_pages.get("/library/browse/<path:rel>")
def browse(rel: str = ""):
    path = _resolve(rel) if rel else _root()
    if not path.is_dir():
        abort(404)
    conn = _catalog()
    try:
        title_folders = (
            {row["folder"] for row in conn.execute("SELECT folder FROM titles")} if conn else set()
        )
    finally:
        if conn is not None:
            conn.close()
    dirs, files = [], []
    for child in sorted(path.iterdir(), key=lambda p: p.name.lower()):
        if child.name.startswith("."):
            continue
        child_rel = _rel(child)
        if child.is_dir():
            dirs.append(
                {
                    "name": child.name,
                    "rel": child_rel,
                    "is_title": str(_root() / child_rel) in title_folders,
                    "count": sum(1 for c in child.iterdir() if not c.name.startswith(".")),
                }
            )
        elif child.is_file():
            files.append(
                {
                    "name": child.name,
                    "rel": child_rel,
                    "size": _format_size(child.stat().st_size),
                    "video": child.suffix.lower() in ml.VIDEO_EXTS,
                }
            )
    is_title = str(_root() / rel) in title_folders if rel else False
    return render_template(
        "library-browse.html",
        rel=rel,
        dirs=dirs,
        files=files,
        is_title=is_title,
        breadcrumbs=_breadcrumbs(rel),
    )


# ---------------------------------------------------------------------------
# Management
# ---------------------------------------------------------------------------


def _systemctl_show(unit: str, *props: str) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "show", unit, *[f"-p{p}" for p in props]],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def _recent_log(lines: int = 8) -> list[str]:
    try:
        result = subprocess.run(
            [
                "journalctl",
                "--user",
                "-u",
                f"{SYNC_UNIT}.service",
                "-n",
                str(lines),
                "--no-pager",
                "-o",
                "short-iso",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in result.stdout.splitlines() if line and not line.startswith("--")]


@library_pages.get("/library/manage")
def manage_page():
    tracked, _error = ml.tracked_names(_root())
    conn = _catalog()
    try:
        attention = ml.needs_attention(_root(), conn, tracked)
        totals = (
            dict(
                conn.execute(
                    "SELECT COUNT(*) AS titles, COALESCE(SUM(file_count), 0) AS files,"
                    " COALESCE(SUM(size_bytes), 0) AS size FROM titles"
                ).fetchone()
            )
            if conn
            else {"titles": 0, "files": 0, "size": 0}
        )
    finally:
        if conn is not None:
            conn.close()
    service = _systemctl_show(
        f"{SYNC_UNIT}.service", "ActiveState", "Result", "ExecMainExitTimestamp"
    )
    timer = _systemctl_show(f"{SYNC_UNIT}.timer", "ActiveState", "NextElapseUSecRealtime")
    return render_template(
        "library-manage.html",
        attention=attention,
        totals={**totals, "size": _format_size(totals["size"])},
        service=service,
        timer=timer,
        log=_recent_log(),
        batches=ml.recent_batches(ml.RENAME_JOURNAL),
        journal=str(ml.RENAME_JOURNAL),
    )


@library_pages.post("/library/manage/sync")
def start_sync():
    try:
        result = subprocess.run(
            ["systemctl", "--user", "start", "--no-block", f"{SYNC_UNIT}.service"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        flash(f"Could not start the sync: {exc}", "error")
        return redirect(url_for("library_pages.manage_page"))
    if result.returncode:
        flash(f"Could not start the sync: {result.stderr.strip()}", "error")
    else:
        flash("Sync started; refresh in a minute to see the result.", "success")
    return redirect(url_for("library_pages.manage_page"))
