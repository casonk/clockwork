"""Read and listen library browsing, safe file serving, and title matching."""

from __future__ import annotations

import os
import re
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request, send_file, url_for

personal_library = Blueprint("personal_library", __name__)

ROOTS = {
    "read": (
        Path(os.environ["CLOCKWORK_READ_LIBRARY_DIR"])
        if os.environ.get("CLOCKWORK_READ_LIBRARY_DIR")
        else None
    ),
    "listen": (
        Path(os.environ["CLOCKWORK_LISTEN_LIBRARY_DIR"])
        if os.environ.get("CLOCKWORK_LISTEN_LIBRARY_DIR")
        else None
    ),
}
EXTENSIONS = {
    "read": frozenset({".pdf", ".epub", ".txt", ".md"}),
    "listen": frozenset({".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav"}),
}


def root_for(kind: str) -> Path | None:
    if kind not in ROOTS:
        abort(404)
    root = ROOTS[kind]
    return root if root and root.is_dir() else None


def _resolve(kind: str, rel: str) -> Path:
    root = root_for(kind)
    if root is None:
        abort(404)
    parts = Path(rel).parts
    if not parts or any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        abort(404)
    root = root.resolve()
    path = (root / rel).resolve()
    if root not in path.parents or not path.exists():
        abort(404)
    return path


def _relative(kind: str, path: Path) -> str:
    root = root_for(kind)
    assert root is not None
    return str(path.resolve().relative_to(root.resolve()))


def _files(kind: str, folder: Path) -> list[Path]:
    extensions = EXTENSIONS[kind]
    found = []
    for base, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = [d for d in dirs if not d.startswith(".") and not (Path(base) / d).is_symlink()]
        for name in files:
            path = Path(base) / name
            if (
                not name.startswith(".")
                and not path.is_symlink()
                and path.suffix.lower() in extensions
            ):
                found.append(path)
    return sorted(found, key=lambda path: str(path).casefold())


def _norm(value: str) -> str:
    value = re.sub(r"\((?:19|20)\d{2}\)$", "", value)
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def match_title(kind: str, title: str, year: str = "", *, files: list[Path] | None = None) -> dict:
    """Find a playable local file or album folder for one list item."""
    root = root_for(kind)
    if root is None:
        return {"found": False, "match": None, "url": None}
    wanted = _norm(title)
    if not wanted:
        return {"found": False, "match": None, "url": None}
    words = set(wanted.split())
    best: tuple[int, Path] | None = None
    for path in files if files is not None else _files(kind, root):
        candidates = [(path.stem, path)]
        if kind == "listen":
            for parent in path.parents:
                if parent == root:
                    break
                candidates.append((parent.name, parent))
        for candidate, target in candidates:
            candidate_year = re.search(r"\b(?:19|20)\d{2}\b", candidate)
            if year and candidate_year and candidate_year.group() != year:
                continue
            normalized = _norm(candidate)
            if normalized == wanted:
                score = 3
            elif len(words) >= 2 and words <= set(normalized.split()):
                score = 1
            else:
                continue
            if year and year in candidate:
                score += 1
            if target != path:
                score += 1
            if best is None or score > best[0]:
                best = (score, target)
    if best is None:
        return {"found": False, "match": None, "url": None}
    target = best[1]
    rel = _relative(kind, target)
    endpoint = "personal_library.play" if kind == "listen" else "personal_library.view"
    return {"found": True, "match": target.name, "url": url_for(endpoint, rel=rel)}


@personal_library.get("/media/<kind>/")
@personal_library.get("/media/<kind>/<path:rel>")
def browse(kind: str, rel: str = ""):
    root = root_for(kind)
    if root is None:
        return render_template(
            "personal-browse.html",
            kind=kind,
            available=False,
            rel="",
            dirs=[],
            files=[],
            playable=False,
        )
    folder = _resolve(kind, rel) if rel else root
    if not folder.is_dir():
        abort(404)
    dirs, files = [], []
    for child in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
        if child.name.startswith(".") or child.is_symlink():
            continue
        child_rel = _relative(kind, child)
        if child.is_dir():
            dirs.append({"name": child.name, "rel": child_rel})
        elif child.is_file() and child.suffix.lower() in EXTENSIONS[kind]:
            endpoint = "personal_library.play" if kind == "listen" else "personal_library.view"
            files.append({"name": child.name, "url": url_for(endpoint, rel=child_rel)})
    return render_template(
        "personal-browse.html",
        kind=kind,
        available=True,
        rel=rel,
        dirs=dirs,
        files=files,
        playable=kind == "listen" and bool(_files("listen", folder)),
    )


@personal_library.post("/api/media/<kind>/check")
def check(kind: str):
    if kind not in ROOTS:
        abort(404)
    titles = (request.get_json(silent=True) or {}).get("titles", [])
    if (
        not isinstance(titles, list)
        or len(titles) > 500
        or any(not isinstance(item, dict) for item in titles)
    ):
        return jsonify({"error": "Expected up to 500 titles."}), 400
    root = root_for(kind)
    files = _files(kind, root) if root else []
    results = [
        match_title(kind, str(item.get("title") or ""), str(item.get("year") or ""), files=files)
        for item in titles
    ]
    return jsonify({"available": root is not None, "results": results})


@personal_library.get("/media/<kind>/file/<path:rel>")
def media_file(kind: str, rel: str):
    path = _resolve(kind, rel)
    if not path.is_file() or path.suffix.lower() not in EXTENSIONS[kind]:
        abort(404)
    download = request.args.get("download") == "1"
    return send_file(path, conditional=True, as_attachment=download, download_name=path.name)


@personal_library.get("/media/read/view/<path:rel>")
def view(rel: str):
    path = _resolve("read", rel)
    if not path.is_file() or path.suffix.lower() not in EXTENSIONS["read"]:
        abort(404)
    return render_template(
        "personal-view.html",
        kind="read",
        title=path.stem,
        media_type=path.suffix.lower(),
        file_url=url_for("personal_library.media_file", kind="read", rel=rel),
        download_url=url_for("personal_library.media_file", kind="read", rel=rel, download=1),
        playlist=[],
    )


@personal_library.get("/media/listen/play/<path:rel>")
def play(rel: str):
    path = _resolve("listen", rel)
    tracks = _files("listen", path) if path.is_dir() else [path]
    if not tracks or any(track.suffix.lower() not in EXTENSIONS["listen"] for track in tracks):
        abort(404)
    playlist = [
        {
            "name": track.stem,
            "url": url_for(
                "personal_library.media_file", kind="listen", rel=_relative("listen", track)
            ),
        }
        for track in tracks
    ]
    return render_template(
        "personal-view.html",
        kind="listen",
        title=path.name if path.is_dir() else path.stem,
        media_type=path.suffix.lower() if path.is_file() else "album",
        file_url=playlist[0]["url"],
        download_url=url_for(
            "personal_library.media_file",
            kind="listen",
            rel=_relative("listen", tracks[0]),
            download=1,
        ),
        playlist=playlist,
    )
