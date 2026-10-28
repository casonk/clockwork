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
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

try:
    from web import episode_guide, streaming, upscale
    from web import media_library as ml
except ModuleNotFoundError:
    import episode_guide
    import media_library as ml
    import streaming
    import upscale

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
        # The path the catalog (and so the AI render queue) knows this file by.
        "source": info.get("path") or str(_root() / _rel(video)),
        "height": info.get("height"),
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


def _title_dir(video: Path) -> Path:
    folder = video.parent
    while folder != _root().resolve() and (
        re.fullmatch(r"Season \d{2}", folder.name)
        or folder.name.lower() in ml.EXTRAS_DIRS
        or folder.name.lower().endswith(" extras")
    ):
        folder = folder.parent
    return folder


def _neighbours(video: Path) -> tuple[dict | None, dict | None]:
    """The previous and next video of the same title (episodes in order), if any."""
    title_dir = _title_dir(video)
    videos = ml._video_files(title_dir)
    if video not in videos:  # an extra: step through the other extras
        videos = [v for v in ml._video_files(title_dir, include_extras=True) if v not in videos]
    if video not in videos:
        return None, None
    index = videos.index(video)

    def entry(path: Path) -> dict:
        match = re.search(r"S(\d{2})E(\d{2,3})", path.name, re.IGNORECASE)
        label = f"S{match.group(1)}E{match.group(2)}" if match else path.stem
        return {"rel": _rel(path), "label": label, "name": path.name}

    previous = entry(videos[index - 1]) if index > 0 else None
    following = entry(videos[index + 1]) if index + 1 < len(videos) else None
    return previous, following


def _ai_snapshot() -> dict:
    """Render-queue state read once per page: worker status, requests, failures."""
    return {
        "status": upscale.read_status(),
        "priority": upscale.load_priority(),
        "failed": upscale.load_failed(),
    }


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return (
        f"{minutes // 60}:{minutes % 60:02d}:{secs:02d}"
        if minutes >= 60
        else f"{minutes}:{secs:02d}"
    )


def _ai_state(info: dict, height: int | None, snapshot: dict) -> dict | None:
    state = upscale.render_state(_root(), Path(info["source"]), height, **snapshot)
    if state and state["state"] != "ready":
        partial = upscale.partial_render(_root(), Path(info["source"]), state["target"])
        if partial:
            # Chunks rendered so far can be watched while the rest renders.
            state["partial_seconds"] = partial[1]
            state["partial_clock"] = _clock(partial[1])
    if state and state.get("eta_seconds"):
        minutes = round(state["eta_seconds"] / 60)
        state["eta"] = (
            f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"
        )
    return state


def _breadcrumbs(rel: str) -> list[dict[str, str]]:
    crumbs = [{"name": "watch", "rel": ""}]
    parts = Path(rel).parts if rel else ()
    for index, part in enumerate(parts):
        crumbs.append({"name": part, "rel": str(Path(*parts[: index + 1]))})
    return crumbs


# ---------------------------------------------------------------------------
# Title details
# ---------------------------------------------------------------------------


@library_pages.get("/api/library/episodes/<path:rel>")
def show_episodes(rel: str):
    """Compare the aired episode guide with videos already filed for one show."""
    parts = Path(rel).parts
    if len(parts) != 2 or parts[0] != ml.SHOWS:
        abort(404)
    folder = _resolve(rel)
    if not folder.is_dir():
        abort(404)
    match = re.match(r"^(?P<title>.+?)(?: \((?P<year>\d{4})\))?$", folder.name)
    assert match is not None
    show_id = request.args.get("show_id", "").strip()
    if show_id and not show_id.isdigit():
        return jsonify({"error": "Invalid show ID."}), 400
    try:
        result = episode_guide.episode_guide(
            folder,
            match.group("title"),
            int(match.group("year")) if match.group("year") else None,
            show_id=int(show_id) if show_id else None,
        )
    except episode_guide.GuideError as exc:
        return jsonify({"error": str(exc)}), 503
    response = jsonify(result)
    response.cache_control.max_age = 300
    return response


@library_pages.get("/api/library/show-summary/<path:rel>")
def show_summary(rel: str):
    """Lightweight show description for folder browsing when the catalog lacks one."""
    parts = Path(rel).parts
    if len(parts) != 2 or parts[0] != ml.SHOWS or not _resolve(rel).is_dir():
        abort(404)
    match = re.match(r"^(?P<title>.+?)(?: \((?P<year>\d{4})\))?$", parts[1])
    assert match is not None
    try:
        show = episode_guide.find_show(
            match.group("title"),
            int(match.group("year")) if match.group("year") else None,
        )
    except episode_guide.GuideError as exc:
        return jsonify({"error": str(exc)}), 503
    if show.get("ambiguous"):
        return jsonify({"ambiguous": True})
    rating = show.get("rating") or {}
    response = jsonify(
        {
            "rating": rating.get("average") if isinstance(rating, dict) else None,
            "description": episode_guide.plain_text(show.get("summary")),
            "source_url": show.get("url") or "",
        }
    )
    response.cache_control.max_age = 3600
    return response


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
    snapshot = _ai_snapshot()
    for item in files:
        item["ai"] = _ai_state(item, item["height"], snapshot)
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
        ai_pending=sum(1 for f in files if f["ai"] and f["ai"]["state"] in ("none", "failed")),
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


def _start_upscale_service() -> str:
    state = _systemctl_show(f"{upscale.SERVICE}.service", "ActiveState").get("ActiveState")
    if state == "active":
        return " AI upscaling is active."
    try:
        result = subprocess.run(
            ["systemctl", "--user", "start", "--no-block", f"{upscale.SERVICE}.service"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f" Could not start AI upscaling: {exc}"
    if result.returncode:
        return f" Could not start AI upscaling: {result.stderr.strip()}"
    return " AI upscaling started."


@library_pages.post("/library/title/<path:rel>/upscale")
def request_title_upscale(rel: str):
    folder = _resolve(rel)
    if not folder.is_dir():
        abort(404)
    conn = _catalog()
    try:
        files = [_file_info(conn, v) for v in ml._video_files(folder)]
    finally:
        if conn is not None:
            conn.close()
    snapshot = _ai_snapshot()
    files.sort(key=lambda f: (f["season"] or 0, f["episode"] or 0, f["name"]))
    sources = []
    for item in files:
        state = _ai_state(item, item["height"], snapshot)
        if state and state["state"] in ("none", "failed"):
            sources.append(Path(item["source"]))
    queued = len(sources)
    if queued:
        upscale.request_renders(sources)
        start_note = _start_upscale_service()
        flash(
            f"Queued {queued} AI render{'s' if queued != 1 else ''} ahead of other requests and"
            " background library renders, in episode order." + start_note,
            "success",
        )
    else:
        flash("Nothing to render: every file is done, queued or already high resolution.")
    return redirect(url_for("library_pages.title_page", rel=_rel(folder)))


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
    media = streaming.probe_media(video)
    direct = streaming.plays_directly(media)
    embedded = [
        {"index": index, "label": f"{lang} (embedded)", "lang": lang}
        for index, lang in media.text_subtitles
    ]
    title_dir = _title_dir(video)
    qualities = [
        {
            "name": p.name,
            "label": (
                f"Original ({media.height}p{', direct' if direct else ''})"
                if p.height is None and media.height
                else p.label
            ),
            "kbps": p.maxrate_kbps + p.audio_kbps,
        }
        for p in (streaming.for_source(q, media) for q in streaming.available_profiles(media))
    ]
    # Experimental AI super-resolution renders, when the background job has made them.
    for label, _path in upscale.upscaled_versions(_root(), _root() / _rel(video)):
        variant = "ai-4k" if label == "AI 4K" else "ai-1080p"
        qualities.insert(0, {"name": variant, "label": f"✨ {label} (experimental)", "kbps": None})
    previous, following = _neighbours(video)
    ai = _ai_state(info, media.height or info["height"], _ai_snapshot())
    if ai and ai.get("partial_seconds"):
        variant = "ai-4k" if ai["target"] == 2160 else "ai-1080p"
        ai["variant"] = variant
        qualities.insert(
            0,
            {
                "name": variant,
                "label": f"✨ {ai['label']} (rendering: first {ai['partial_clock']})",
                "kbps": None,
            },
        )
    return render_template(
        "library-play.html",
        info=info,
        tracks=tracks,
        embedded=embedded,
        direct=direct,
        media=media,
        qualities=qualities,
        title_rel=_rel(title_dir),
        title_name=title_dir.name,
        previous=previous,
        following=following,
        ai=ai,
        live=[q for q in qualities if not q["name"].startswith("ai-") and q["name"] != "original"],
    )


@library_pages.post("/library/play/<path:rel>/upscale")
def request_upscale(rel: str):
    video = _resolve(rel)
    if not video.is_file() or video.suffix.lower() not in ml.VIDEO_EXTS:
        abort(404)
    conn = _catalog()
    try:
        info = _file_info(conn, video)
    finally:
        if conn is not None:
            conn.close()
    height = info["height"] or streaming.probe_media(video).height
    state = _ai_state(info, height, _ai_snapshot())
    if state is None:
        flash("This video is already high resolution; there is no AI version to make.")
    elif state["state"] == "ready":
        flash(f"{state['label']} is already available in the quality menu.", "success")
    elif state["state"] == "rendering":
        flash(f"{state['label']} is rendering now.", "success")
    else:
        position = upscale.request_render(Path(info["source"]))
        ahead = "next" if position == 0 else f"after {position} other request(s)"
        start_note = _start_upscale_service()
        flash(
            f"{state['label']} queued {ahead} of other requests and background renders; it starts"
            " once the current render yields." + start_note,
            "success",
        )
    return redirect(url_for("library_pages.play_page", rel=_rel(video)))


AI_VARIANTS = {"ai-4k": 2160, "ai-1080p": 1080}


def _variant_arg() -> str:
    variant = request.args.get("v", "")
    if variant and variant not in AI_VARIANTS:
        abort(404)
    return variant


def _stream_source(rel: str) -> Path:
    """The library file, or its AI-upscaled render when ?v=ai-4k|ai-1080p asks for it."""
    video = _resolve(rel)
    if not video.is_file() or video.suffix.lower() not in ml.VIDEO_EXTS:
        abort(404)
    variant = _variant_arg()
    if variant:
        rendered = upscale.output_path(_root(), _root() / _rel(video), AI_VARIANTS[variant])
        if rendered.is_file():
            return rendered
        # Still rendering: stream the chunks finished so far.
        partial = upscale.partial_render(_root(), _root() / _rel(video), AI_VARIANTS[variant])
        if partial is None:
            abort(404)
        return partial[0]
    return video


def _quality_arg() -> str:
    name = request.args.get("q", streaming.DEFAULT_PROFILE)
    if name not in streaming.PROFILES:
        abort(404)
    return name


@library_pages.get("/library/stream/<path:rel>/index.m3u8")
def stream_playlist(rel: str):
    quality = _quality_arg()
    variant = _variant_arg()
    session = streaming.manager().session(_stream_source(rel), quality)
    suffix = f"?q={quality}" + (f"&v={variant}" if variant else "")
    playlist = streaming.vod_playlist(session.info.duration, lambda i: f"seg/{i}.ts{suffix}")
    return Response(
        playlist,
        mimetype="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )


@library_pages.get("/library/stream/<path:rel>/seg/<int:index>.ts")
def stream_segment(rel: str, index: int):
    segment = streaming.manager().segment(_stream_source(rel), index, _quality_arg())
    if segment is None:
        abort(503, description="The segment could not be transcoded in time.")
    return send_file(segment, mimetype="video/mp2t", max_age=3600)


@library_pages.get("/library/stream/<path:rel>/sub/<int:index>.vtt")
def stream_subtitle(rel: str, index: int):
    return Response(streaming.subtitle_vtt(_stream_source(rel), index), mimetype="text/vtt")


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
        title_rows = (
            {
                row["folder"]: {
                    "rating": row["rating"],
                    "description": row["plot"] or ", ".join(json.loads(row["genres"] or "[]")[:4]),
                    "has_plot": bool(row["plot"]),
                    "kind": row["kind"],
                }
                for row in conn.execute("SELECT folder, kind, rating, plot, genres FROM titles")
            }
            if conn
            else {}
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
                    "is_title": str(_root() / child_rel) in title_rows,
                    "meta": title_rows.get(str(_root() / child_rel)),
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
    current_meta = title_rows.get(str(_root() / rel)) if rel else None
    is_title = current_meta is not None
    return render_template(
        "library-browse.html",
        rel=rel,
        dirs=dirs,
        files=files,
        is_title=is_title,
        current_meta=current_meta,
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
    upscale_root = _root() / upscale.UPSCALED_DIR
    # Finished renders only: a render in progress keeps its chunks in *.chunks/.
    rendered = (
        [p for p in upscale_root.rglob("*.mkv") if not p.parent.name.endswith(".chunks")]
        if upscale_root.is_dir()
        else []
    )
    ai = {
        "status": upscale.read_status(),
        "service": _systemctl_show(f"{upscale.SERVICE}.service", "ActiveState", "LoadState"),
        "rendered": len(rendered),
        "rendered_size": _format_size(sum(p.stat().st_size for p in rendered)),
        "failed": len(upscale.load_failed()),
    }
    return render_template(
        "library-manage.html",
        attention=attention,
        totals={**totals, "size": _format_size(totals["size"])},
        service=service,
        timer=timer,
        log=_recent_log(),
        batches=ml.recent_batches(ml.RENAME_JOURNAL),
        ai=ai,
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


@library_pages.post("/library/manage/upscale/<action>")
def control_upscale(action: str):
    if action not in ("start", "stop"):
        abort(404)
    try:
        result = subprocess.run(
            ["systemctl", "--user", action, "--no-block", f"{upscale.SERVICE}.service"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        flash(f"Could not {action} AI upscaling: {exc}", "error")
        return redirect(url_for("library_pages.manage_page"))
    if result.returncode:
        flash(f"Could not {action} AI upscaling: {result.stderr.strip()}", "error")
    else:
        flash(f"AI upscaling {'started' if action == 'start' else 'stopping'}.", "success")
    return redirect(url_for("library_pages.manage_page"))
