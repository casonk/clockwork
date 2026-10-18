"""Organize the watch library into Jellyfin/Plex layout and catalog its metadata.

Layout produced by ``organize``::

    <root>/movies/Title (Year)/Title (Year) [1080p].mkv
    <root>/movies/Title (Year)/Title (Year) [1080p].en.srt
    <root>/shows/Title (Year)/Season 01/Title S01E01 [1080p].mkv

Every rename is appended to a JSONL journal so a batch can be undone. Entries
Transmission still tracks are never touched, because renaming them would break
seeding. ``scan`` reads stream details with ffprobe, looks titles up in
Wikidata (and TMDB when an API key is configured), and stores everything in a
SQLite catalog that the web UI searches.

Run ``python3 web/media_library.py --help`` for the CLI.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import pwd
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

LIBRARY_ROOT = Path(os.environ.get("CLOCKWORK_WATCH_DIR", "/mnt/16tb-sata/watch"))
CATALOG_DB = Path(
    os.environ.get("CLOCKWORK_MEDIA_DB", BASE_DIR / "config" / "media-library.sqlite")
)
RENAME_JOURNAL = Path(
    os.environ.get("CLOCKWORK_MEDIA_JOURNAL", BASE_DIR / "config" / "media-library-renames.jsonl")
)
MAGNETO_URL = os.environ.get("CLOCKWORK_MAGNETO_URL", "http://127.0.0.1:5400")
# Paths Transmission may report for the library root (it can see the share bind mount).
TRANSMISSION_ROOT_ALIASES = tuple(
    p
    for p in os.environ.get(
        "CLOCKWORK_MEDIA_TRANSMISSION_ROOTS", "/mnt/16tb-sata/watch:/srv/snowbridge/share/watch"
    ).split(":")
    if p
)
TRANSMISSION_USER = os.environ.get("CLOCKWORK_MEDIA_TRANSMISSION_USER", "transmission")
TMDB_API_KEY = os.environ.get("CLOCKWORK_TMDB_API_KEY", "").strip()
OLLAMA_URL = os.environ.get("CREW_CHIEF_URL", "http://localhost:11434")
GENRE_MODEL = os.environ.get("CLOCKWORK_GENRE_MODEL", "qwen3:14b")
USER_AGENT = "clockwork-media-library/0.1 (https://github.com/casonk/clockwork)"

MOVIES = "movies"
SHOWS = "shows"

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".mpg", ".mpeg", ".ts", ".webm"}
SUBTITLE_EXTS = {".srt", ".sub", ".idx", ".ass", ".ssa", ".vtt"}
AUDIO_EXTS = {".ac3", ".eac3", ".dts", ".dtshd", ".aac", ".mka", ".flac"}
COMPANION_EXTS = SUBTITLE_EXTS | AUDIO_EXTS

# ---------------------------------------------------------------------------
# Release-name parsing
# ---------------------------------------------------------------------------

_SITE_PREFIX_RE = re.compile(r"^\s*(?:www\.)?[\w-]+\.(?:org|com|net|to|cc|me)\s*-\s*", re.I)
_YEAR_RE = re.compile(r"(?<![\d])[\(\[]?\s*((?:19|20)\d\d)\s*[\)\]]?(?![\d])")
_YEAR_SPAN_RE = re.compile(r"\b(?:19|20)\d\d\s*[-,]\s*(?:(?:19|20)\d\d)?")
_EPISODE_RE = re.compile(
    r"\bS(\d{1,2})[ ._-]?E(\d{1,3})(?:[-E]+(\d{1,3}))?\b|\b(\d{1,2})x(\d{2,3})\b", re.I
)
_LONG_EPISODE_RE = re.compile(
    r"\bSeason[ ._]*(\d{1,2})[ ._-]*Episode[ ._]*(\d{1,3})(?:[ ._]*(?:&|-|and)[ ._]*(\d{1,3}))?\b",
    re.I,
)
_SEASON_ONLY_RE = re.compile(
    r"\bS(\d{1,2})(?:\s*-\s*S?\d{1,2})?\b(?!E)|\bSeason[ ._]*(\d{1,2})\b", re.I
)
_COLLECTION_RE = re.compile(r"\b(collection|trilogy|quadrilogy|anthology|\d+\s+movies)\b", re.I)
_TAG_RE = re.compile(
    r"\b(2160p|1080p|720p|576p|480p|4k|uhd|hdr|bluray|blu-ray|brrip|bdrip|dvdrip|dvdscr|webrip|"
    r"web-?dl|web|hdtv|telesync|ts|cam|x264|x265|h ?264|h ?265|hevc|xvid|aac\d?|ac3|dts|ddp?\d|"
    r"10bit|remux|repack|proper|internal|multi|complete|amzn|nf|ma)\b",
    re.I,
)
_EDITION_PATTERNS = (
    (re.compile(r"\bextended(?: (?:edition|cut))?\b", re.I), "Extended"),
    (re.compile(r"\bdirector'?s cut\b|\bDC\b"), "Director's Cut"),
    (re.compile(r"\bunrated\b", re.I), "Unrated"),
    (re.compile(r"\bremastered\b", re.I), "Remastered"),
    (re.compile(r"\bcolori[sz]ed\b", re.I), "Colorized"),
    (re.compile(r"\bcriterion\b", re.I), "Criterion"),
    (re.compile(r"\btheatrical(?: cut)?\b", re.I), "Theatrical"),
)
# code -> (English name, other names, ISO 639-2 codes)
_LANGUAGES = {
    "en": ("english", (), ("eng",)),
    "es": ("spanish", ("español", "castilian", "latino"), ("spa",)),
    "fr": ("french", ("français",), ("fre", "fra")),
    "de": ("german", ("deutsch",), ("ger", "deu")),
    "it": ("italian", (), ("ita",)),
    "pt": ("portuguese", ("brazilian",), ("por", "pob")),
    "ja": ("japanese", (), ("jpn",)),
    "ko": ("korean", (), ("kor",)),
    "zh": ("chinese", ("mandarin", "cantonese"), ("chi", "zho", "chs", "cht")),
    "ru": ("russian", (), ("rus",)),
    "nl": ("dutch", (), ("dut", "nld")),
    "sv": ("swedish", (), ("swe",)),
    "da": ("danish", (), ("dan",)),
    "no": ("norwegian", (), ("nor", "nob")),
    "fi": ("finnish", (), ("fin",)),
    "pl": ("polish", (), ("pol",)),
    "ar": ("arabic", (), ("ara",)),
    "he": ("hebrew", (), ("heb",)),
    "tr": ("turkish", (), ("tur",)),
    "el": ("greek", (), ("gre", "ell")),
    "eu": ("basque", (), ("baq", "eus")),
    "bg": ("bulgarian", (), ("bul",)),
    "ca": ("catalan", (), ("cat",)),
    "cs": ("czech", (), ("cze", "ces")),
    "hr": ("croatian", (), ("hrv", "scr")),
    "et": ("estonian", (), ("est",)),
    "fa": ("persian", ("farsi",), ("per", "fas")),
    "gl": ("galician", (), ("glg",)),
    "hi": ("hindi", (), ("hin",)),
    "hu": ("hungarian", (), ("hun",)),
    "is": ("icelandic", (), ("ice", "isl")),
    "id": ("indonesian", (), ("ind",)),
    "lv": ("latvian", (), ("lav",)),
    "lt": ("lithuanian", (), ("lit",)),
    "ms": ("malay", (), ("may", "msa")),
    "ro": ("romanian", (), ("rum", "ron")),
    "sk": ("slovak", (), ("slo", "slk")),
    "sl": ("slovenian", ("slovene",), ("slv",)),
    "sr": ("serbian", (), ("srp", "scc")),
    "th": ("thai", (), ("tha",)),
    "uk": ("ukrainian", (), ("ukr",)),
    "vi": ("vietnamese", (), ("vie",)),
    "bn": ("bengali", (), ("ben",)),
    "ta": ("tamil", (), ("tam",)),
    "te": ("telugu", (), ("tel",)),
    "ml": ("malayalam", (), ("mal",)),
    "fil": ("filipino", ("tagalog",), ("fil", "tgl")),
}
_FORBIDDEN_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class ParsedName:
    title: str
    year: int | None = None
    kind: str = MOVIES  # movies | shows | collection
    edition: str | None = None
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None


def _spaced(name: str) -> str:
    name = _SITE_PREFIX_RE.sub("", name)
    # Dots and underscores separate words in release names; keep decimals like 5.1.
    name = re.sub(r"(?<!\d)[._](?!\d)|(?<=\d)[._](?!\d)|(?<!\d)[._](?=\d)", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _clean_title(raw: str) -> str:
    raw = re.sub(r"[\[\(\{][^\]\)\}]*[\]\)\}]?", " ", raw)
    raw = re.sub(r"[\[\]\(\)\{\}]", " ", raw)
    raw = re.sub(r"\s+-\s*$|^\s*-\s+", " ", raw)
    for pattern, _label in _EDITION_PATTERNS:
        raw = pattern.sub(" ", raw)
    return re.sub(r"\s+", " ", raw).strip(" -,")


def _edition(name: str) -> str | None:
    for pattern, label in _EDITION_PATTERNS:
        if pattern.search(name):
            return label
    return None


def parse_release_name(name: str) -> ParsedName:
    """Pull title, year, kind and episode numbers out of a release name."""
    stem = name
    if Path(name).suffix.lower() in VIDEO_EXTS | SUBTITLE_EXTS:
        stem = Path(name).stem
    spaced = _spaced(stem)
    edition = _edition(spaced)

    episode = _EPISODE_RE.search(spaced)
    long_episode = None if episode else _LONG_EPISODE_RE.search(spaced)
    season_only = None if episode or long_episode else _SEASON_ONLY_RE.search(spaced)
    collection = bool(_COLLECTION_RE.search(spaced)) or bool(
        re.search(r"\b(?:19|20)\d\d\s*[-,]\s*(?:19|20)\d\d\b", spaced)
    )

    cut = len(spaced)
    year: int | None = None
    for match in _YEAR_RE.finditer(spaced):
        if match.start() == 0:
            continue  # a title that is only a year, e.g. "1917", keeps it
        year = int(match.group(1))
        cut = match.start()
        break
    for marker in (episode, long_episode, season_only, _TAG_RE.search(spaced)):
        if marker and 0 < marker.start() < cut:
            cut = marker.start()
    title = _clean_title(spaced[:cut]) or _clean_title(spaced)

    if episode:
        if episode.group(1):
            season, ep, ep_end = int(episode.group(1)), int(episode.group(2)), episode.group(3)
        else:
            season, ep, ep_end = int(episode.group(4)), int(episode.group(5)), None
        return ParsedName(title, year, SHOWS, edition, season, ep, int(ep_end) if ep_end else None)
    if long_episode:
        end = long_episode.group(3)
        return ParsedName(
            title,
            year,
            SHOWS,
            edition,
            int(long_episode.group(1)),
            int(long_episode.group(2)),
            int(end) if end else None,
        )
    if collection:
        return ParsedName(title, year, "collection", edition)
    if season_only:
        season = int(season_only.group(1) or season_only.group(2))
        return ParsedName(title, year, SHOWS, edition, season)
    return ParsedName(title, year, MOVIES, edition)


def safe_component(text: str) -> str:
    """Make a title safe as one path component on Linux and SMB clients."""
    text = text.replace(":", " -").replace("/", "-")
    text = _FORBIDDEN_CHARS_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text or "Untitled"


def title_folder(title: str, year: int | None) -> str:
    return safe_component(f"{title} ({year})" if year else title)


def subtitle_language(name: str) -> str | None:
    """Language code from a subtitle name like ``2_English.srt`` or ``SDH.eng.HI.srt``.

    Full language names count anywhere; short codes only among the last two
    tokens (two-letter codes only as the last), so "Cat People" or "It (2017)"
    are not read as languages.
    """
    tokens = [t for t in re.split(r"[\s._\-\[\]()]+", Path(name).stem.lower()) if t]
    tail = tokens[-2:]
    for code, (english, others, iso3) in _LANGUAGES.items():
        if english in tokens or any(word in tokens for word in others):
            return code
        if code == tokens[-1] or any(word in tail for word in iso3):
            return code
    return None


# ---------------------------------------------------------------------------
# ffprobe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamInfo:
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    channels: int | None = None
    audio_languages: tuple[str, ...] = ()
    subtitle_languages: tuple[str, ...] = ()

    @property
    def quality(self) -> str | None:
        return quality_label(self.width, self.height)


def quality_label(width: int | None, height: int | None) -> str | None:
    """Resolution class; widescreen crops (1920x800) still count as 1080p."""
    if not width and not height:
        return None
    w, h = width or 0, height or 0
    if w >= 3200 or h >= 1600:
        return "2160p"
    if w >= 1800 or h >= 1000:
        return "1080p"
    if w >= 1200 or h >= 700:
        return "720p"
    if h >= 470 or w >= 700:
        return "480p"
    return "SD"


def probe(path: Path, runner: Callable[..., Any] = subprocess.run) -> StreamInfo:
    try:
        result = runner(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type,codec_name,width,height,channels:stream_tags=language"
                ":format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        data = json.loads(result.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return StreamInfo()
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("width")), {})
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]

    def langs(items: list[dict[str, Any]]) -> tuple[str, ...]:
        found = [(s.get("tags") or {}).get("language", "") for s in items]
        return tuple(dict.fromkeys(lang for lang in found if lang and lang != "und"))

    try:
        duration = float((data.get("format") or {}).get("duration") or 0) or None
    except ValueError:
        duration = None
    return StreamInfo(
        duration_s=duration,
        width=video.get("width"),
        height=video.get("height"),
        video_codec=video.get("codec_name"),
        audio_codec=audio[0].get("codec_name") if audio else None,
        channels=audio[0].get("channels") if audio else None,
        audio_languages=langs(audio),
        subtitle_languages=langs(subs),
    )


# ---------------------------------------------------------------------------
# Metadata providers
# ---------------------------------------------------------------------------


@dataclass
class Metadata:
    title: str | None = None
    year: int | None = None
    kind: str | None = None
    wikidata_id: str | None = None
    imdb_id: str | None = None
    tmdb_id: str | None = None
    directors: list[str] = field(default_factory=list)
    cast: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    runtime_min: int | None = None
    plot: str | None = None
    rating: float | None = None
    sources: list[str] = field(default_factory=list)

    def merge(self, other: Metadata) -> None:
        """Fill gaps from another provider without overriding what is known."""
        for name in (
            "title",
            "year",
            "kind",
            "wikidata_id",
            "imdb_id",
            "tmdb_id",
            "runtime_min",
            "plot",
            "rating",
        ):
            if getattr(self, name) in (None, "") and getattr(other, name) not in (None, ""):
                setattr(self, name, getattr(other, name))
        for name in ("directors", "cast", "genres", "countries", "languages"):
            if not getattr(self, name) and getattr(other, name):
                setattr(self, name, list(getattr(other, name)))
        self.sources.extend(s for s in other.sources if s not in self.sources)


def _http_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _norm(text: str) -> str:
    text = text.lower().replace("&", "and")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\b(the|a|an)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_similarity(left: str, right: str) -> float:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    tokens_a, tokens_b = set(a.split()), set(b.split())
    overlap = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
    contained = 0.9 if tokens_b <= tokens_a or tokens_a <= tokens_b else 0.0
    return max(ratio, overlap, contained)


def best_candidate(
    title: str, year: int | None, kind: str, candidates: Iterable[dict[str, Any]]
) -> dict[str, Any] | None:
    """Choose the candidate whose label and year best fit the parsed name."""
    best: tuple[float, dict[str, Any]] | None = None
    for candidate in candidates:
        score = title_similarity(title, candidate.get("title") or "")
        cand_year = candidate.get("year")
        if year and cand_year:
            gap = abs(int(cand_year) - year)
            score += 0.5 if gap == 0 else 0.25 if gap == 1 else -0.5
        elif year and not cand_year:
            score -= 0.2
        if candidate.get("kind") == kind:
            score += 0.15
        if best is None or score > best[0]:
            best = (score, candidate)
    if best is None or best[0] < (1.1 if year else 0.85):
        return None
    return best[1]


_WD_TYPES = {
    "Q11424": MOVIES,  # film
    "Q506240": MOVIES,  # television film
    "Q202866": MOVIES,  # animated film
    "Q24869": MOVIES,  # feature film
    "Q5398426": SHOWS,  # television series
    "Q581714": SHOWS,  # animated television series
    "Q526877": SHOWS,  # web series
    "Q1259759": SHOWS,  # miniseries
    "Q117467246": SHOWS,  # animated television series
    "Q63952888": SHOWS,  # anime television series
    "Q20650540": MOVIES,  # anime film
}


class WikidataProvider:
    name = "wikidata"
    endpoint = "https://query.wikidata.org/sparql"

    def __init__(self, fetch: Callable[[str], Any] | None = None) -> None:
        self._fetch = fetch or (
            lambda url: _http_json(url, headers={"Accept": "application/sparql-results+json"})
        )

    def _query(self, sparql: str) -> list[dict[str, str]]:
        url = f"{self.endpoint}?{urllib.parse.urlencode({'query': sparql, 'format': 'json'})}"
        try:
            data = self._fetch(url)
        except OSError:  # the public endpoint times out now and then; retry once
            time.sleep(2)
            data = self._fetch(url)
        bindings = data["results"]["bindings"]
        return [{k: v["value"] for k, v in row.items()} for row in bindings]

    def search(self, title: str, year: int | None, kind: str) -> Metadata | None:
        types = "|".join(f"P31={qid}" for qid in _WD_TYPES)
        search = json.dumps(f"{title} haswbstatement:{types}")
        rows = self._query(f"""SELECT ?item ?itemLabel ?type (MIN(YEAR(?date)) AS ?year) WHERE {{
  SERVICE wikibase:mwapi {{
    bd:serviceParam wikibase:api "Search"; wikibase:endpoint "www.wikidata.org";
      mwapi:srsearch {search}; mwapi:srlimit "10".
    ?item wikibase:apiOutputItem mwapi:title.
  }}
  ?item wdt:P31 ?type. VALUES ?type {{ {" ".join(f"wd:{q}" for q in _WD_TYPES)} }}
  OPTIONAL {{ ?item wdt:P577|wdt:P580 ?date }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul". }}
}} GROUP BY ?item ?itemLabel ?type""")
        candidates = [
            {
                "id": row["item"].rsplit("/", 1)[-1],
                # An item without an English or multilingual label comes back as its QID.
                "title": (
                    ""
                    if re.fullmatch(r"Q\d+", row.get("itemLabel", ""))
                    else row.get("itemLabel", "")
                ),
                "year": int(row["year"]) if row.get("year") else None,
                "kind": _WD_TYPES.get(row.get("type", "").rsplit("/", 1)[-1]),
            }
            for row in rows
        ]
        chosen = best_candidate(title, year, kind, candidates)
        if not chosen:
            return None
        return self.details(chosen["id"], chosen)

    def details(self, qid: str, chosen: dict[str, Any]) -> Metadata:
        rows = self._query(f"""SELECT ?p ?valueLabel ?value WHERE {{
  VALUES ?p {{ "director" "creator" "cast" "genre" "country" "language" "imdb" "tmdb" "tmdbtv" "duration" }}
  {{ wd:{qid} wdt:P57 ?value. BIND("director" AS ?p) }}
  UNION {{ wd:{qid} wdt:P170 ?value. BIND("creator" AS ?p) }}
  UNION {{ wd:{qid} wdt:P161 ?value. BIND("cast" AS ?p) }}
  UNION {{ wd:{qid} wdt:P136 ?value. BIND("genre" AS ?p) }}
  UNION {{ wd:{qid} wdt:P495 ?value. BIND("country" AS ?p) }}
  UNION {{ wd:{qid} wdt:P364 ?value. BIND("language" AS ?p) }}
  UNION {{ wd:{qid} wdt:P345 ?value. BIND("imdb" AS ?p) }}
  UNION {{ wd:{qid} wdt:P4947 ?value. BIND("tmdb" AS ?p) }}
  UNION {{ wd:{qid} wdt:P4983 ?value. BIND("tmdbtv" AS ?p) }}
  UNION {{ wd:{qid} wdt:P2047 ?value. BIND("duration" AS ?p) }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul". }}
}}""")
        values: dict[str, list[str]] = {}
        for row in rows:
            label = row.get("valueLabel") or row.get("value", "")
            bucket = values.setdefault(row["p"], [])
            if label and label not in bucket:
                bucket.append(label)
        duration = values.get("duration", [])
        runtime = None
        if duration:
            try:
                runtime = round(float(duration[0]))
            except ValueError:
                runtime = None
        tmdb = (values.get("tmdb") or values.get("tmdbtv") or [None])[0]
        return Metadata(
            title=chosen.get("title"),
            year=chosen.get("year"),
            kind=chosen.get("kind"),
            wikidata_id=qid,
            imdb_id=(values.get("imdb") or [None])[0],
            tmdb_id=tmdb,
            # Shows rarely carry a director; their creators fill the same role.
            directors=values.get("director") or values.get("creator", []),
            cast=values.get("cast", [])[:12],
            genres=[g.removesuffix(" film") for g in values.get("genre", [])],
            countries=values.get("country", []),
            languages=values.get("language", []),
            runtime_min=runtime,
            sources=[self.name],
        )


class TmdbProvider:
    """TMDB lookups; only active when CLOCKWORK_TMDB_API_KEY is set."""

    name = "tmdb"
    base = "https://api.themoviedb.org/3"

    def __init__(self, api_key: str, fetch: Callable[[str], Any] | None = None) -> None:
        self.api_key = api_key
        self._fetch = fetch or _http_json

    def _get(self, path: str, **params: Any) -> Any:
        query = urllib.parse.urlencode({"api_key": self.api_key, **params})
        return self._fetch(f"{self.base}{path}?{query}")

    def search(self, title: str, year: int | None, kind: str) -> Metadata | None:
        media = "tv" if kind == SHOWS else "movie"
        params: dict[str, Any] = {"query": title}
        if year:
            params["first_air_date_year" if media == "tv" else "year"] = year
        results = self._get(f"/search/{media}", **params).get("results") or []
        candidates = []
        for item in results[:10]:
            date = item.get("release_date") or item.get("first_air_date") or ""
            candidates.append(
                {
                    "id": item["id"],
                    "title": item.get("title") or item.get("name") or "",
                    "year": int(date[:4]) if date[:4].isdigit() else None,
                    "kind": SHOWS if media == "tv" else MOVIES,
                }
            )
        chosen = best_candidate(title, year, kind, candidates)
        if not chosen:
            return None
        info = self._get(f"/{media}/{chosen['id']}", append_to_response="credits,external_ids")
        credits = info.get("credits") or {}
        directors = [c["name"] for c in credits.get("crew") or [] if c.get("job") == "Director"]
        if media == "tv":
            directors = directors or [c["name"] for c in info.get("created_by") or []]
        runtime = info.get("runtime") or (info.get("episode_run_time") or [None])[0]
        return Metadata(
            title=chosen["title"],
            year=chosen["year"],
            kind=chosen["kind"],
            tmdb_id=str(chosen["id"]),
            imdb_id=(info.get("external_ids") or {}).get("imdb_id") or info.get("imdb_id"),
            directors=directors,
            cast=[c["name"] for c in (credits.get("cast") or [])[:12]],
            genres=[g["name"] for g in info.get("genres") or []],
            countries=[c["name"] for c in info.get("production_countries") or []],
            languages=[lang["english_name"] for lang in info.get("spoken_languages") or []],
            runtime_min=runtime,
            plot=info.get("overview") or None,
            rating=info.get("vote_average") or None,
            sources=[self.name],
        )


def default_providers() -> list[Any]:
    providers: list[Any] = [WikidataProvider()]
    if TMDB_API_KEY:
        providers.append(TmdbProvider(TMDB_API_KEY))
    return providers


def lookup_metadata(
    title: str,
    year: int | None,
    kind: str,
    providers: Iterable[Any],
    cache: sqlite3.Connection | None = None,
) -> Metadata:
    """Ask every provider and merge; the first match sets the canonical title.

    With a catalog connection, matches are cached under both the queried and
    the canonical name, so organizing and the following scan look up once.
    """
    providers = list(providers)
    if not providers:
        return Metadata()
    names = ",".join(p.name for p in providers)
    key = json.dumps([_norm(title), year, kind, names])
    if cache is not None:
        row = cache.execute("SELECT data FROM lookups WHERE key = ?", (key,)).fetchone()
        if row:
            return Metadata(**json.loads(row["data"]))
    merged = Metadata()
    for provider in providers:
        try:
            found = provider.search(title, year, kind)
        except Exception as exc:  # noqa: BLE001 - one provider failing must not stop the rest
            print(f"{provider.name} lookup failed for {title!r}: {exc}", file=sys.stderr)
            continue
        if found:
            merged.merge(found)
    if cache is not None and merged.sources:
        data = json.dumps(merged.__dict__)
        keys = {key}
        if merged.title:
            keys.add(json.dumps([_norm(merged.title), merged.year, kind, names]))
        cache.executemany(
            "INSERT OR REPLACE INTO lookups (key, data, at) VALUES (?, ?, ?)",
            [(k, data, time.time()) for k in keys],
        )
        cache.commit()
    return merged


# ---------------------------------------------------------------------------
# Genre folders for movies
# ---------------------------------------------------------------------------

GENRE_FOLDERS = (
    "Action",
    "Adventure",
    "Animation",
    "Comedy",
    "Crime",
    "Documentary",
    "Drama",
    "Family",
    "Fantasy",
    "Horror",
    "Musical",
    "Mystery",
    "Romance",
    "Sci-Fi",
    "Thriller",
    "War",
    "Western",
    "Other",
)
# Keyword fallback when no model is reachable, most specific genre first.
_GENRE_KEYWORDS = (
    ("Documentary", ("documentary",)),
    ("Animation", ("animated", "animation", "anime")),
    ("Horror", ("horror", "slasher", "zombie")),
    ("Sci-Fi", ("science fiction", "sci-fi", "cyberpunk", "dystopian", "space opera")),
    ("Western", ("western",)),
    ("War", ("war film", "war")),
    ("Musical", ("musical",)),
    ("Fantasy", ("fantasy",)),
    ("Crime", ("crime", "gangster", "heist", "mafia", "noir")),
    ("Thriller", ("thriller", "suspense")),
    ("Mystery", ("mystery", "detective")),
    ("Action", ("action", "martial arts", "superhero", "disaster")),
    ("Adventure", ("adventure",)),
    ("Comedy", ("comedy", "satire", "parody", "sitcom")),
    ("Romance", ("romance", "romantic")),
    ("Family", ("family", "children")),
    ("Drama", ("drama", "biographical", "historical")),
)


def heuristic_genre(tags: Iterable[str]) -> str:
    lowered = [t.lower() for t in tags]
    for genre, words in _GENRE_KEYWORDS:
        if any(re.search(rf"\b{re.escape(w)}\b", tag) for tag in lowered for w in words):
            return genre
    return "Other"


def _ask_ollama(prompt: str) -> str:
    body = json.dumps(
        {
            "model": GENRE_MODEL,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {"temperature": 0},
        }
    ).encode()
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", body, {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8")).get("response", "")


class GenreClassifier:
    """Choose one genre folder per movie.

    Wikidata genre tags are unordered and noisy (Memento is tagged horror), so
    a local Ollama model picks the primary genre with the tags as hints. Only
    the title, year, director and tags go to the local model. Documentary and
    animation tags decide directly; without a model the keyword fallback
    applies. Answers are cached in the catalog's lookup table.
    """

    def __init__(
        self,
        cache: sqlite3.Connection | None = None,
        ask: Callable[[str], str] | None = _ask_ollama,
    ) -> None:
        self.cache = cache
        self.ask = ask

    def __call__(self, title: str, year: int | None, meta: Metadata) -> str:
        tags = list(meta.genres)
        fixed = heuristic_genre(tags)
        if fixed in ("Documentary", "Animation") or self.ask is None:
            return fixed
        key = json.dumps(["genre", _norm(title), year])
        if self.cache is not None:
            row = self.cache.execute("SELECT data FROM lookups WHERE key = ?", (key,)).fetchone()
            if row:
                return json.loads(row["data"])["genre"]
        choices = [g for g in GENRE_FOLDERS if g != "Other"]
        film = f'the movie "{title}"' + (f" ({year})" if year else "")
        if meta.directors:
            film += f", directed by {meta.directors[0]}"
        if meta.cast:
            film += ", starring " + ", ".join(meta.cast[:3])
        prompt = (
            f"Pick the single primary genre of {film}, for filing it in a home media library. "
            "Wikidata genre tags (unordered, noisy): " + (", ".join(tags) or "none") + ". "
            "Documentary means non-fiction only; a dramatized true story is not one. "
            "Answer with exactly one of: " + ", ".join(choices) + ". Reply with the genre only."
        )
        try:
            answer = self.ask(prompt).strip().strip(".").lower()
        except Exception as exc:  # noqa: BLE001 - fall back when the model is unavailable
            print(f"genre model unavailable for {title!r}: {exc}", file=sys.stderr)
            return fixed
        genre = next((g for g in choices if g.lower() == answer), None)
        if genre is None:
            return fixed
        if self.cache is not None:
            self.cache.execute(
                "INSERT OR REPLACE INTO lookups (key, data, at) VALUES (?, ?, ?)",
                (key, json.dumps({"genre": genre}), time.time()),
            )
            self.cache.commit()
        return genre


def _keyword_genre(_title: str, _year: int | None, meta: Metadata) -> str:
    return heuristic_genre(meta.genres)


def movie_dir(root: Path, genre: str, title: str, year: int | None) -> Path:
    return root / MOVIES / genre / title_folder(title, year)


# ---------------------------------------------------------------------------
# Organize: plan and apply renames
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Move:
    src: Path
    dst: Path


@dataclass
class EntryPlan:
    entry: Path
    kind: str
    title: str
    year: int | None
    moves: list[Move] = field(default_factory=list)
    note: str | None = None
    skipped: str | None = None
    # Directory to clear of emptied subfolders (and itself, if left empty) after applying.
    prune: Path | None = None


# Folders media servers treat as bonus material; the organizer leaves them as they are.
EXTRAS_DIRS = {
    "extras",
    "featurettes",
    "behind the scenes",
    "deleted scenes",
    "interviews",
    "scenes",
    "shorts",
    "trailers",
    "specials",
    "other",
}
# Hidden per-title folder for release clutter (ads, samples, duplicate copies).
RELEASE_DIR = ".release"
_EXTRA_CODE_RE = re.compile(r"\bS\d{1,2}X\d{1,3}\b", re.I)


def _is_hidden(path: Path, base: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(base).parts)


def _in_extras(path: Path, base: Path) -> bool:
    parts = path.relative_to(base).parts[:-1]
    return any(p.lower() in EXTRAS_DIRS or p.lower().endswith(" extras") for p in parts)


def _files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(f for f in path.rglob("*") if f.is_file() and not _is_hidden(f, path))


def _video_files(path: Path, *, include_extras: bool = False) -> list[Path]:
    """Feature videos under ``path``: no samples, hidden folders or (by default) extras."""
    return [
        f
        for f in _files(path)
        if f.suffix.lower() in VIDEO_EXTS
        and "sample" not in f.name.lower()
        and (include_extras or path.is_file() or not _in_extras(f, path))
    ]


def _companion_files(path: Path) -> list[Path]:
    """Subtitles and external audio tracks that belong next to a video."""
    if path.is_file():
        return []
    return [
        f for f in _files(path) if f.suffix.lower() in COMPANION_EXTS and not _in_extras(f, path)
    ]


UNKNOWN = "Unknown"


def _field(text: str) -> str:
    # File names carry no spaces at all: every space, inside or between fields, is "_".
    return re.sub(r"_+", "_", safe_component(text.replace("_", " ")).replace(" ", "_"))


def director_field(directors: Iterable[str]) -> str:
    names = [d for d in directors if d][:2]
    return " & ".join(names) if names else UNKNOWN


def movie_stem(
    title: str,
    year: int | None,
    quality: str | None,
    directors: Iterable[str] = (),
    edition: str | None = None,
) -> str:
    """NAME_DATE_QUALITY_DIRECTOR[_EDITION], e.g. ``Napoleon_Dynamite_2004_1080p_Jared_Hess``."""
    fields = [title, str(year) if year else UNKNOWN, quality or UNKNOWN, director_field(directors)]
    if edition:
        fields.append(edition)
    return "_".join(_field(f) for f in fields)


EDITIONS = tuple(label for _pattern, label in _EDITION_PATTERNS)


def _stem_edition(stem: str) -> str | None:
    """Edition from a file stem; it is the trailing field when present."""
    normalized = stem.replace(" ", "_")
    for label in EDITIONS:
        if normalized.endswith("_" + label.replace(" ", "_")):
            return label
    legacy = re.search(r"\{edition-([^}]+)\}", stem)
    return legacy.group(1) if legacy else None


def episode_stem(show: str, code: str, quality: str | None) -> str:
    """NAME_EPISODE_QUALITY, e.g. ``Breaking_Bad_S01E01_1080p``."""
    return "_".join(_field(f) for f in (show, code, quality or UNKNOWN))


def _companion_moves(companions: list[Path], base: Path, video_stem: str) -> list[Move]:
    moves: list[Move] = []
    used: set[str] = set()
    for companion in companions:
        parts = [video_stem]
        lang = subtitle_language(companion.name)
        if lang:
            parts.append(lang)
        lowered = companion.name.lower()
        if "forced" in lowered:
            parts.append("forced")
        elif "sdh" in lowered:
            parts.append("sdh")
        name = ".".join(parts)
        suffix = companion.suffix.lower()
        candidate, n = name, 2
        while f"{candidate}{suffix}" in used:
            candidate = f"{name}.{n}"
            n += 1
        used.add(f"{candidate}{suffix}")
        moves.append(Move(companion, base / f"{candidate}{suffix}"))
    return moves


def _leftover_moves(
    entry: Path, current: Path, moved: set[Path], release: Path, *, keep_extras: bool = True
) -> list[Move]:
    """Park files the plan does not place (ads, samples, duplicates) in a hidden folder.

    ``entry`` is the original location, ``current`` where those files sit once
    the plan's earlier moves ran, and ``release`` the hidden destination.
    """
    moves = []
    for f in _files(entry):
        if f in moved or (keep_extras and _in_extras(f, entry)):
            continue
        rel = f.relative_to(entry)
        moves.append(Move(current / rel, release / rel))
    return moves


def plan_movie(
    entry: Path,
    root: Path,
    title: str,
    year: int | None,
    edition: str | None,
    prober,
    genre: str = "Other",
    directors: Iterable[str] = (),
) -> EntryPlan:
    plan = EntryPlan(entry, MOVIES, title, year)
    videos = _video_files(entry)
    if not videos:
        plan.skipped = "no video files"
        return plan
    sizes = {v: v.stat().st_size for v in videos}
    main = max(videos, key=sizes.__getitem__)
    rivals = [v for v in videos if v != main and sizes[v] > sizes[main] * 0.4]
    if rivals:
        plan.skipped = "several full-length videos (multi-part or collection); review by hand"
        return plan
    folder = movie_dir(root, genre, title, year)
    stem = movie_stem(title, year, prober(main).quality, directors, edition)
    if entry.is_file():
        plan.moves.append(Move(entry, folder / f"{stem}{entry.suffix.lower()}"))
        return plan
    plan.moves.append(Move(entry, folder))
    plan.prune = folder
    plan.moves.append(
        Move(folder / main.relative_to(entry), folder / f"{stem}{main.suffix.lower()}")
    )
    companions = _companion_files(entry)
    plan.moves.extend(
        Move(folder / c.relative_to(entry), m.dst)
        for c, m in zip(companions, _companion_moves(companions, folder, stem), strict=True)
    )
    plan.moves.extend(_leftover_moves(entry, folder, {main, *companions}, folder / RELEASE_DIR))
    return plan


def _episode_of(path: Path, entry: Path) -> tuple[int, int, int | None] | None:
    if _EXTRA_CODE_RE.search(path.name):
        return None  # "S07X01 Simpsorama (The Simpsons S26E06)" is a bonus, not S26E06
    parsed = parse_release_name(path.name)
    if parsed.episode is not None and parsed.season is not None:
        return parsed.season, parsed.episode, parsed.episode_end
    return None


_SHOW_MOVIE_RE = re.compile(r"\bS\d{1,2}M\d{1,2}\b\s*(?P<rest>.+)$", re.I)


def _no_lookup(_title: str, _year: int | None, _kind: str) -> Metadata:
    return Metadata()


def plan_show(
    entry: Path,
    root: Path,
    title: str,
    year: int | None,
    prober,
    lookup: Callable[[str, int | None, str], Metadata] = _no_lookup,
    genre_of: Callable[[str, int | None, Metadata], str] = _keyword_genre,
) -> EntryPlan:
    plan = EntryPlan(entry, SHOWS, title, year)
    if entry.is_file():
        plan.skipped = "single episode file; file it under its show by hand"
        return plan
    folder = root / SHOWS / title_folder(title, year)
    plan.moves.append(Move(entry, folder))
    plan.prune = folder

    def rebase(p: Path) -> Path:
        return folder / p.relative_to(entry)

    companions_by_code: dict[tuple[int, int], list[Path]] = {}
    for companion in _companion_files(entry):
        code = _episode_of(companion, entry)
        if code:
            companions_by_code.setdefault(code[:2], []).append(companion)

    placed: set[Path] = set()
    targets: set[Path] = set()
    extras = 0
    for video in _video_files(entry):
        found = _episode_of(video, entry)
        feature = None if found else _SHOW_MOVIE_RE.search(_spaced(video.stem))
        if feature:
            # Feature films shipped inside a show pack (e.g. "S05M01 Bender's Big Score").
            parsed = parse_release_name(feature.group("rest"))
            meta = lookup(f"{title} {parsed.title}", parsed.year, MOVIES)
            film_title = meta.title or f"{title} - {parsed.title}"
            film_year = parsed.year or meta.year
            companions = [
                c for c in _companion_files(video.parent) if c.stem.startswith(video.stem)
            ]
            plan.moves.extend(
                _movie_moves(
                    video,
                    rebase(video),
                    root,
                    film_title,
                    film_year,
                    parsed.edition,
                    prober,
                    companions=companions,
                    genre=genre_of(film_title, film_year, meta),
                    directors=meta.directors,
                    rebase=rebase,
                )
            )
            placed.update([video, *companions])
            continue
        if not found:
            # Bonus videos without an episode code become extras of the show.
            plan.moves.append(Move(rebase(video), folder / "Extras" / video.name))
            placed.add(video)
            extras += 1
            continue
        season, ep, ep_end = found
        code = f"S{season:02d}E{ep:02d}" + (f"-E{ep_end:02d}" if ep_end else "")
        season_dir = folder / f"Season {season:02d}"
        stem = episode_stem(title, code, prober(video).quality)
        dst = season_dir / f"{stem}{video.suffix.lower()}"
        if dst in targets:
            continue  # duplicate copy of an episode; parked with the release clutter
        targets.add(dst)
        plan.moves.append(Move(rebase(video), dst))
        placed.add(video)
        companions = [c for c in companions_by_code.pop((season, ep), []) if c not in placed]
        plan.moves.extend(
            Move(rebase(c), m.dst)
            for c, m in zip(companions, _companion_moves(companions, season_dir, stem), strict=True)
        )
        placed.update(companions)
    plan.moves.extend(_leftover_moves(entry, folder, placed, folder / RELEASE_DIR))
    if extras:
        plan.note = f"{extras} video(s) without an episode code filed under Extras"
    return plan


def _movie_moves(
    video: Path,
    current: Path,
    root: Path,
    title: str,
    year: int | None,
    edition: str | None,
    prober,
    *,
    companions: list[Path],
    genre: str = "Other",
    directors: Iterable[str] = (),
    rebase: Callable[[Path], Path] = lambda p: p,
) -> list[Move]:
    """Moves that file one feature video (and its companions) as its own movie folder."""
    folder = movie_dir(root, genre, title, year)
    stem = movie_stem(title, year, prober(video).quality, directors, edition)
    moves = [Move(current, folder / f"{stem}{video.suffix.lower()}")]
    moves.extend(
        Move(rebase(c), m.dst)
        for c, m in zip(companions, _companion_moves(companions, folder, stem), strict=True)
    )
    return moves


def plan_collection(
    entry: Path,
    root: Path,
    prober,
    lookup: Callable[[str, int | None, str], Metadata] = _no_lookup,
    genre_of: Callable[[str, int | None, Metadata], str] = _keyword_genre,
) -> list[EntryPlan]:
    """Split a multi-movie pack so every film gets its own movies/ folder."""
    videos = _video_files(entry)
    if not videos:
        return [EntryPlan(entry, MOVIES, entry.name, None, skipped="no video files")]
    sizes = {v: v.stat().st_size for v in videos}
    largest = max(sizes.values())
    features = [v for v in videos if sizes[v] >= largest * 0.25]
    plans: list[EntryPlan] = []
    placed: set[Path] = set()
    for video in features:
        own_dir = video.parent != entry and sum(f.parent == video.parent for f in features) == 1
        source_name = video.parent.name if own_dir else video.name
        parsed = parse_release_name(source_name)
        if not parsed.year:
            parsed = parse_release_name(video.name)
        meta = lookup(parsed.title, parsed.year, MOVIES)
        title = meta.title or parsed.title
        year = parsed.year or meta.year
        if own_dir:
            companions = _companion_files(video.parent)
        else:
            companions = [c for c in _companion_files(entry) if c.stem.startswith(video.stem)]
        plan = EntryPlan(video, MOVIES, title, year, prune=entry)
        plan.moves = _movie_moves(
            video,
            video,
            root,
            title,
            year,
            parsed.edition,
            prober,
            companions=companions,
            genre=genre_of(title, year, meta),
            directors=meta.directors,
        )
        placed.update([video, *companions])
        if not meta.sources:
            plan.note = "no metadata match; parsed name used"
        plans.append(plan)
    leftovers = _leftover_moves(
        entry, entry, placed, root / RELEASE_DIR / entry.name, keep_extras=False
    )
    if leftovers:
        plans.append(EntryPlan(entry, "release", entry.name, None, leftovers, prune=entry))
    return plans


def tracked_names(
    root: Path, fetch: Callable[[str], Any] = _http_json
) -> tuple[set[str], str | None]:
    """Names of torrents Transmission is still tracking inside the library root."""
    try:
        data = fetch(f"{MAGNETO_URL}/api/torrents")
    except Exception as exc:  # noqa: BLE001
        return set(), f"magneto unreachable: {exc}"
    if not data.get("ok"):
        return set(), f"magneto error: {data.get('error')}"
    roots = {os.path.normpath(p) for p in (*TRANSMISSION_ROOT_ALIASES, str(root))}
    return {
        t["name"]
        for t in data.get("torrents") or []
        if os.path.normpath(str(t.get("downloadDir") or "")) in roots
    }, None


def _owned_by_transmission(path: Path) -> bool:
    try:
        return path.stat().st_uid == pwd.getpwnam(TRANSMISSION_USER).pw_uid
    except (KeyError, OSError):
        return False


def plan_library(
    root: Path,
    *,
    providers: Iterable[Any] = (),
    prober: Callable[[Path], StreamInfo] = probe,
    tracked: set[str] | None = None,
    tracked_error: str | None = None,
    cache: sqlite3.Connection | None = None,
    genre_of: Callable[[str, int | None, Metadata], str] = _keyword_genre,
) -> list[EntryPlan]:
    """Plan renames for every unsorted top-level entry of the library root.

    Movies already filed directly under movies/ (before genre folders) are
    moved into their genre folder. Titles inside a genre folder are never
    moved again, so moving one by hand overrides the chosen genre.
    """
    providers = list(providers)

    def lookup(title: str, year: int | None, kind: str) -> Metadata:
        return lookup_metadata(title, year, kind, providers, cache)

    plans: list[EntryPlan] = _plan_genre_folders(root, lookup, genre_of)
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith(".") or entry.name in (MOVIES, SHOWS):
            continue
        parsed = parse_release_name(entry.name)
        if tracked is not None and entry.name in tracked:
            plans.append(
                EntryPlan(entry, parsed.kind, parsed.title, parsed.year, skipped="still seeding")
            )
            continue
        if tracked is None and _owned_by_transmission(entry):
            plans.append(
                EntryPlan(
                    entry,
                    parsed.kind,
                    parsed.title,
                    parsed.year,
                    skipped=f"may still be seeding ({tracked_error})",
                )
            )
            continue
        if entry.is_file() and entry.suffix.lower() not in VIDEO_EXTS:
            continue
        if parsed.kind == "collection" and entry.is_dir():
            episodes = sum(1 for v in _video_files(entry) if _episode_of(v, entry))
            if episodes < 2:
                plans.extend(plan_collection(entry, root, prober, lookup, genre_of))
                continue
        kind = SHOWS if parsed.kind == "collection" else parsed.kind
        if kind == MOVIES and entry.is_dir():
            episodes = sum(1 for v in _video_files(entry) if _episode_of(v, entry))
            if episodes >= 2:
                kind = SHOWS
        meta = lookup(parsed.title, parsed.year, kind)
        title = meta.title or parsed.title
        # Wikidata dates are often festival premieres a year early; trust the release name.
        year = parsed.year or meta.year
        if meta.kind in (MOVIES, SHOWS) and meta.kind != kind and not parsed.season:
            kind = meta.kind
        if kind == SHOWS:
            plan = plan_show(entry, root, title, year, prober, lookup, genre_of)
        else:
            genre = genre_of(title, year, meta)
            plan = plan_movie(
                entry, root, title, year, parsed.edition, prober, genre, meta.directors
            )
        if not meta.sources:
            plan.note = "; ".join(filter(None, [plan.note, "no metadata match; parsed name used"]))
        plans.append(plan)
    return plans


_FOLDER_RE = re.compile(r"^(?P<title>.+?)(?: \((?P<year>\d{4})\))?$")


def _plan_genre_folders(
    root: Path,
    lookup: Callable[[str, int | None, str], Metadata],
    genre_of: Callable[[str, int | None, Metadata], str],
) -> list[EntryPlan]:
    """Move titles sitting directly in movies/ into their genre folder."""
    base = root / MOVIES
    if not base.is_dir():
        return []
    plans = []
    for folder in sorted(base.iterdir()):
        if not folder.is_dir() or folder.name.startswith(".") or folder.name in GENRE_FOLDERS:
            continue
        match = _FOLDER_RE.match(folder.name)
        title = match.group("title") if match else folder.name
        year = int(match.group("year")) if match and match.group("year") else None
        genre = genre_of(title, year, lookup(title, year, MOVIES))
        plans.append(
            EntryPlan(folder, MOVIES, title, year, [Move(folder, base / genre / folder.name)])
        )
    return plans


def _catalog_quality(conn: sqlite3.Connection | None, prober) -> Callable[[Path], str | None]:
    """Quality from the catalog when it has probed this exact file, else ffprobe."""

    def quality(path: Path) -> str | None:
        if conn is not None:
            row = conn.execute(
                "SELECT quality, size_bytes, mtime FROM files WHERE path = ?", (str(path),)
            ).fetchone()
            stat = path.stat()
            if row and row["size_bytes"] == stat.st_size and row["mtime"] == stat.st_mtime:
                return row["quality"]
        return prober(path).quality

    return quality


def _rename_with_companions(video: Path, stem: str) -> list[Move]:
    """Rename a video and the subtitles/audio next to it that share its stem."""
    if video.stem == stem:
        return []
    moves = [Move(video, video.with_name(f"{stem}{video.suffix}"))]
    prefix = f"{video.stem}."
    for sibling in sorted(video.parent.iterdir()):
        if (
            sibling.is_file()
            and sibling.name.startswith(prefix)
            and sibling.suffix.lower() in COMPANION_EXTS
        ):
            moves.append(Move(sibling, sibling.with_name(stem + "." + sibling.name[len(prefix) :])))
    return moves


def plan_file_names(root: Path, conn: sqlite3.Connection | None, prober=probe) -> list[EntryPlan]:
    """Bring files already in the library to the current naming convention.

    Directors come from the catalog, so a title gets them on the sync after
    its first scan. Folder names are left as they are.
    """
    quality = _catalog_quality(conn, prober)
    plans: list[EntryPlan] = []
    for kind, folder, _category in _title_dirs(root):
        match = _FOLDER_RE.match(folder.name)
        title = match.group("title") if match else folder.name
        year = int(match.group("year")) if match and match.group("year") else None
        moves: list[Move] = []
        if kind == MOVIES:
            videos = [v for v in _video_files(folder) if v.parent == folder]
            if not videos:
                continue
            sizes = {v: v.stat().st_size for v in videos}
            main = max(videos, key=sizes.__getitem__)
            if any(v != main and sizes[v] > sizes[main] * 0.4 for v in videos):
                continue  # multi-part movie; leave its names alone
            directors: list[str] = []
            if conn is not None:
                row = conn.execute(
                    "SELECT directors FROM titles WHERE folder = ?", (str(folder),)
                ).fetchone()
                directors = json.loads(row["directors"]) if row else []
            edition = _stem_edition(main.stem) or _edition(_spaced(main.stem))
            stem = movie_stem(title, year, quality(main), directors, edition)
            moves = _rename_with_companions(main, stem)
        else:
            for video in _video_files(folder):
                if not re.fullmatch(r"Season \d{2}", video.parent.name):
                    continue
                found = _episode_of(video, folder)
                if not found:
                    continue
                season, ep, ep_end = found
                code = f"S{season:02d}E{ep:02d}" + (f"-E{ep_end:02d}" if ep_end else "")
                moves.extend(
                    _rename_with_companions(video, episode_stem(title, code, quality(video)))
                )
        if moves:
            plans.append(EntryPlan(folder, kind, title, year, moves))
    return plans


def apply_plans(plans: Iterable[EntryPlan], journal: Path = RENAME_JOURNAL) -> tuple[str, int]:
    """Perform planned renames in order, journaling each so the batch can be undone."""
    batch = datetime.datetime.now().strftime("%Y%m%dT%H%M%S-") + uuid.uuid4().hex[:6]
    journal.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    with journal.open("a", encoding="utf-8") as log:
        for plan in plans:
            if plan.skipped:
                continue
            for move in plan.moves:
                if move.src == move.dst or not os.path.lexists(move.src):
                    continue
                if os.path.lexists(move.dst):
                    print(f"exists, not overwriting: {move.dst}", file=sys.stderr)
                    continue
                try:
                    move.dst.parent.mkdir(parents=True, exist_ok=True)
                    os.rename(move.src, move.dst)
                except OSError as exc:
                    print(f"rename failed, left in place: {move.src}: {exc}", file=sys.stderr)
                    continue
                log.write(
                    json.dumps(
                        {
                            "batch": batch,
                            "src": str(move.src),
                            "dst": str(move.dst),
                            "at": time.time(),
                        }
                    )
                    + "\n"
                )
                log.flush()
                done += 1
            _prune_empty_dirs(plan)
    return batch, done


def _prune_empty_dirs(plan: EntryPlan) -> None:
    top = plan.prune
    if not top or not top.is_dir():
        return
    for directory in [*sorted((d for d in top.rglob("*") if d.is_dir()), reverse=True), top]:
        with contextlib.suppress(OSError):
            directory.rmdir()  # only succeeds when empty


def undo_batch(batch: str, journal: Path = RENAME_JOURNAL) -> int:
    """Reverse one batch's renames, newest first."""
    entries = [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("batch") == batch
    ]
    undone = 0
    vacated: set[Path] = set()
    for item in reversed(entries):
        src, dst = Path(item["src"]), Path(item["dst"])
        if os.path.lexists(dst) and not os.path.lexists(src):
            src.parent.mkdir(parents=True, exist_ok=True)
            os.rename(dst, src)
            vacated.update(list(dst.parents)[:3])
            undone += 1
    # Remove folders the batch created and left empty, deepest first.
    for directory in sorted(vacated, key=lambda d: len(d.parts), reverse=True):
        with contextlib.suppress(OSError):
            directory.rmdir()
    return undone


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS titles (
    folder TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    edition TEXT,
    wikidata_id TEXT,
    imdb_id TEXT,
    tmdb_id TEXT,
    directors TEXT NOT NULL DEFAULT '[]',
    cast_members TEXT NOT NULL DEFAULT '[]',
    genres TEXT NOT NULL DEFAULT '[]',
    countries TEXT NOT NULL DEFAULT '[]',
    languages TEXT NOT NULL DEFAULT '[]',
    runtime_min INTEGER,
    plot TEXT,
    rating REAL,
    sources TEXT NOT NULL DEFAULT '[]',
    quality TEXT,
    video_codec TEXT,
    audio_codec TEXT,
    channels INTEGER,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    metadata_checked_at REAL,
    scanned_at REAL,
    category TEXT
);
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    folder TEXT NOT NULL REFERENCES titles(folder) ON DELETE CASCADE,
    size_bytes INTEGER,
    mtime REAL,
    season INTEGER,
    episode INTEGER,
    duration_s REAL,
    width INTEGER,
    height INTEGER,
    quality TEXT,
    video_codec TEXT,
    audio_codec TEXT,
    channels INTEGER,
    audio_languages TEXT NOT NULL DEFAULT '[]',
    subtitle_languages TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS files_folder ON files(folder);
CREATE TABLE IF NOT EXISTS lookups (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    at REAL NOT NULL
);
"""

_QUALITY_RANK = {"2160p": 5, "1080p": 4, "720p": 3, "480p": 2, "SD": 1}


def connect(db_path: Path = CATALOG_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(titles)")}
    if "category" not in columns:  # catalogs built before genre folders
        conn.execute("ALTER TABLE titles ADD COLUMN category TEXT")
    return conn


METADATA_RETRY_SECONDS = 7 * 24 * 3600


def _title_dirs(root: Path) -> Iterable[tuple[str, Path, str | None]]:
    """Yield (kind, title folder, genre folder) for movies/<Genre>/<Title> and shows/<Title>."""
    for kind in (MOVIES, SHOWS):
        base = root / kind
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if kind == MOVIES and entry.name in GENRE_FOLDERS:
                for title_dir in sorted(entry.iterdir()):
                    if title_dir.is_dir() and not title_dir.name.startswith("."):
                        yield kind, title_dir, entry.name
            else:
                yield kind, entry, None


def scan_library(
    root: Path,
    conn: sqlite3.Connection,
    *,
    providers: Iterable[Any] = (),
    prober: Callable[[Path], StreamInfo] = probe,
    refresh_metadata: bool = False,
    now: Callable[[], float] = time.time,
) -> dict[str, int]:
    """Bring the catalog in line with the organized library folders."""
    providers = list(providers)
    stats = {"titles": 0, "probed": 0, "looked_up": 0, "removed": 0}
    seen: set[str] = set()
    for kind, folder, category in _title_dirs(root):
        key = str(folder)
        seen.add(key)
        stats["titles"] += 1
        match = _FOLDER_RE.match(folder.name)
        title = match.group("title") if match else folder.name
        year = int(match.group("year")) if match and match.group("year") else None
        row = conn.execute("SELECT * FROM titles WHERE folder = ?", (key,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO titles (folder, kind, title, year) VALUES (?, ?, ?, ?)",
                (key, kind, title, year),
            )
            row = conn.execute("SELECT * FROM titles WHERE folder = ?", (key,)).fetchone()
        conn.execute("UPDATE titles SET category = ? WHERE folder = ?", (category, key))
        stats["probed"] += _scan_files(conn, key, folder, kind, prober)
        checked = row["metadata_checked_at"]
        stale = checked is None or (
            not json.loads(row["sources"]) and now() - checked > METADATA_RETRY_SECONDS
        )
        if providers and (refresh_metadata or stale):
            meta = lookup_metadata(title, year, kind, providers, None if refresh_metadata else conn)
            conn.execute(
                """UPDATE titles SET wikidata_id = ?, imdb_id = ?, tmdb_id = ?, directors = ?,
                cast_members = ?, genres = ?, countries = ?, languages = ?, runtime_min = ?,
                plot = ?, rating = ?, sources = ?, metadata_checked_at = ? WHERE folder = ?""",
                (
                    meta.wikidata_id,
                    meta.imdb_id,
                    meta.tmdb_id,
                    json.dumps(meta.directors),
                    json.dumps(meta.cast),
                    json.dumps(meta.genres),
                    json.dumps(meta.countries),
                    json.dumps(meta.languages),
                    meta.runtime_min,
                    meta.plot,
                    meta.rating,
                    json.dumps(meta.sources),
                    now(),
                    key,
                ),
            )
            stats["looked_up"] += 1
        _summarize_title(conn, key, folder.name, now())
        conn.commit()
    for (folder,) in conn.execute("SELECT folder FROM titles").fetchall():
        if folder not in seen:
            conn.execute("DELETE FROM titles WHERE folder = ?", (folder,))
            stats["removed"] += 1
    conn.commit()
    return stats


def _scan_files(conn, key: str, folder: Path, kind: str, prober) -> int:
    probed = 0
    present: set[str] = set()
    for video in _video_files(folder):
        stat = video.stat()
        present.add(str(video))
        existing = conn.execute(
            "SELECT size_bytes, mtime FROM files WHERE path = ?", (str(video),)
        ).fetchone()
        if (
            existing
            and existing["size_bytes"] == stat.st_size
            and existing["mtime"] == stat.st_mtime
        ):
            continue
        info = prober(video)
        probed += 1
        parsed = parse_release_name(video.name) if kind == SHOWS else None
        conn.execute(
            """INSERT OR REPLACE INTO files (path, folder, size_bytes, mtime, season, episode,
            duration_s, width, height, quality, video_codec, audio_codec, channels,
            audio_languages, subtitle_languages) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(video),
                key,
                stat.st_size,
                stat.st_mtime,
                parsed.season if parsed else None,
                parsed.episode if parsed else None,
                info.duration_s,
                info.width,
                info.height,
                info.quality,
                info.video_codec,
                info.audio_codec,
                info.channels,
                json.dumps(list(info.audio_languages)),
                json.dumps(list(info.subtitle_languages)),
            ),
        )
    for (path,) in conn.execute("SELECT path FROM files WHERE folder = ?", (key,)).fetchall():
        if path not in present:
            conn.execute("DELETE FROM files WHERE path = ?", (path,))
    return probed


def _summarize_title(conn, key: str, folder_name: str, scanned_at: float) -> None:
    files = conn.execute("SELECT * FROM files WHERE folder = ?", (key,)).fetchall()
    best = max(files, key=lambda f: _QUALITY_RANK.get(f["quality"] or "", 0), default=None)
    largest = max(files, key=lambda f: f["size_bytes"] or 0, default=None)
    edition = _stem_edition(Path(largest["path"]).stem) if largest else None
    conn.execute(
        """UPDATE titles SET quality = ?, video_codec = ?, audio_codec = ?, channels = ?,
        size_bytes = ?, file_count = ?, edition = ?, scanned_at = ? WHERE folder = ?""",
        (
            best["quality"] if best else None,
            best["video_codec"] if best else None,
            best["audio_codec"] if best else None,
            best["channels"] if best else None,
            sum(f["size_bytes"] or 0 for f in files),
            len(files),
            edition,
            scanned_at,
            key,
        ),
    )


_JSON_COLUMNS = ("directors", "cast_members", "genres", "countries", "languages", "sources")


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for column in _JSON_COLUMNS:
        item[column] = json.loads(item.get(column) or "[]")
    item["cast"] = item.pop("cast_members")
    return item


def search_catalog(
    conn: sqlite3.Connection,
    *,
    q: str = "",
    director: str = "",
    actor: str = "",
    genre: str = "",
    category: str = "",
    kind: str = "",
    quality: str = "",
    codec: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    sort: str = "title",
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    def like(column: str, value: str) -> None:
        clauses.append(f"{column} LIKE ? COLLATE NOCASE")
        params.append(f"%{value}%")

    if q:
        clauses.append(
            "(title LIKE ? COLLATE NOCASE OR directors LIKE ? COLLATE NOCASE"
            " OR cast_members LIKE ? COLLATE NOCASE OR genres LIKE ? COLLATE NOCASE"
            " OR plot LIKE ? COLLATE NOCASE)"
        )
        params.extend([f"%{q}%"] * 5)
    if director:
        like("directors", director)
    if actor:
        like("cast_members", actor)
    if genre:
        like("genres", genre)
    if codec:
        like("video_codec", codec)
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if quality:
        clauses.append("quality = ?")
        params.append(quality)
    if year_from:
        clauses.append("year >= ?")
        params.append(year_from)
    if year_to:
        clauses.append("year <= ?")
        params.append(year_to)
    order = {
        "title": "title COLLATE NOCASE",
        "year": "year DESC, title COLLATE NOCASE",
        "added": "scanned_at DESC",
        "size": "size_bytes DESC",
        "rating": "rating DESC",
    }.get(sort, "title COLLATE NOCASE")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT * FROM titles {where} ORDER BY {order}", params).fetchall()
    return [_row_dict(row) for row in rows]


def catalog_facets(conn: sqlite3.Connection) -> dict[str, list[str]]:
    rows = conn.execute(
        "SELECT directors, genres, quality, video_codec, category FROM titles"
    ).fetchall()
    categories = {row["category"] for row in rows if row["category"]}
    directors: set[str] = set()
    genres: set[str] = set()
    qualities: set[str] = set()
    codecs: set[str] = set()
    for row in rows:
        directors.update(json.loads(row["directors"]))
        genres.update(json.loads(row["genres"]))
        if row["quality"]:
            qualities.add(row["quality"])
        if row["video_codec"]:
            codecs.add(row["video_codec"])
    return {
        "directors": sorted(directors),
        "genres": sorted(genres),
        "qualities": sorted(qualities, key=lambda q: -_QUALITY_RANK.get(q, 0)),
        "codecs": sorted(codecs),
        "categories": [g for g in GENRE_FOLDERS if g in categories],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _apply_file_names(root: Path, conn: sqlite3.Connection, journal: Path) -> None:
    plans = plan_file_names(root, conn)
    if not plans:
        return
    batch, done = apply_plans(plans, journal)
    # Carry probe results over to the new paths; renaming keeps size and mtime.
    for plan in plans:
        for move in plan.moves:
            conn.execute("UPDATE files SET path = ? WHERE path = ?", (str(move.dst), str(move.src)))
    conn.commit()
    print(f"file-name batch {batch}: {done} rename(s)")


def _print_plans(plans: list[EntryPlan], root: Path) -> None:
    for plan in plans:
        label = f"{plan.entry.name}"
        if plan.skipped:
            print(f"SKIP  {label}\n      {plan.skipped}")
            continue
        top = plan.moves[0].dst if plan.moves else None
        print(f"{plan.kind[:-1].upper():5} {label}\n   -> {top.relative_to(root) if top else '?'}")
        for move in plan.moves[1:]:
            base = top if top and move.dst.is_relative_to(top) else root
            print(f"      {move.src.name}  =>  {move.dst.relative_to(base)}")
        if plan.note:
            print(f"      note: {plan.note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=LIBRARY_ROOT)
    parser.add_argument("--db", type=Path, default=CATALOG_DB)
    parser.add_argument("--journal", type=Path, default=RENAME_JOURNAL)
    sub = parser.add_subparsers(dest="command", required=True)
    organize = sub.add_parser("organize", help="rename unsorted entries into movies/ and shows/")
    organize.add_argument("--apply", action="store_true", help="perform the renames")
    organize.add_argument("--offline", action="store_true", help="skip metadata lookups")
    organize.add_argument("--only", action="append", default=[], help="limit to these entry names")
    scan = sub.add_parser("scan", help="refresh the metadata catalog")
    scan.add_argument("--refresh-metadata", action="store_true")
    scan.add_argument("--offline", action="store_true")
    sub.add_parser("sync", help="organize --apply, then scan")
    undo = sub.add_parser("undo", help="reverse one organize batch")
    undo.add_argument("batch")
    search = sub.add_parser("search", help="query the catalog")
    search.add_argument("q", nargs="?", default="")
    search.add_argument("--director", default="")
    search.add_argument("--genre", default="")
    search.add_argument("--folder", default="", help="genre folder, e.g. Sci-Fi")
    search.add_argument("--quality", default="")
    search.add_argument("--year-from", type=int)
    search.add_argument("--year-to", type=int)
    args = parser.parse_args(argv)

    if args.command in ("organize", "sync"):
        offline = getattr(args, "offline", False)
        tracked, error = tracked_names(args.root)
        cache = connect(args.db)
        plans = plan_library(
            args.root,
            providers=[] if offline else default_providers(),
            tracked=None if error else tracked,
            tracked_error=error,
            cache=cache,
            genre_of=GenreClassifier(cache, ask=None if offline else _ask_ollama),
        )
        only = set(getattr(args, "only", []) or [])
        if only:
            plans = [p for p in plans if p.entry.name in only]
        if args.command == "organize" and not args.apply:
            _print_plans(plans + plan_file_names(args.root, cache), args.root)
            print("\ndry run; add --apply to rename")
            return 0
        batch, done = apply_plans(plans, args.journal)
        print(f"organize batch {batch}: {done} rename(s)")
        if args.command == "organize":
            _apply_file_names(args.root, cache, args.journal)
            return 0
    if args.command in ("scan", "sync"):
        conn = connect(args.db)
        stats = scan_library(
            args.root,
            conn,
            providers=[] if getattr(args, "offline", False) else default_providers(),
            refresh_metadata=getattr(args, "refresh_metadata", False),
        )
        print("scan " + " ".join(f"{k}={v}" for k, v in stats.items()))
        if args.command == "sync":
            # After the scan, so titles filed this run already have their directors.
            _apply_file_names(args.root, conn, args.journal)
        return 0
    if args.command == "undo":
        print(f"undid {undo_batch(args.batch, args.journal)} rename(s)")
        return 0
    if args.command == "search":
        conn = connect(args.db)
        for item in search_catalog(
            conn,
            q=args.q,
            director=args.director,
            genre=args.genre,
            category=args.folder,
            quality=args.quality,
            year_from=args.year_from,
            year_to=args.year_to,
        ):
            directors = ", ".join(item["directors"]) or "?"
            print(f"{item['title']} ({item['year']}) [{item['quality']}] - {directors}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
