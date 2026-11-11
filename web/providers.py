"""A small provider framework for the To Watch / To Listen integrations.

A *provider* is an outside service a list item can be linked to (Spotify, YouTube, Apple Music and
Apple TV). Each one is a ``Provider`` subclass registered in ``REGISTRY``; the list pages, the
playlist browser and the copy flow talk only to this interface, so a new service is one subclass.

What a provider can do is declared in ``capabilities``:

``link``    parse a pasted URL into item fields (``parse_link``), shown as a link on the item
``browse``  list the account's playlists and their entries (``accounts``, ``playlists``, ``entries``)
``write``   create playlists and add entries (``create_playlist``, ``add``)

A provider that only links (Apple Music today) needs no accounts or credentials. Item fields are
namespaced by provider (``spotify_*``, ``youtube_*``, ``apple_music_*``); ``field`` names the one
that holds the provider's stable id, which is also how playlist entries are matched to list items.

Everything here is a thin adapter: the HTTP and credential handling stays in ``spotify_api`` and
``youtube_api``.
"""

from __future__ import annotations

import dataclasses
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

try:
    from web import spotify_api, youtube_api
except ModuleNotFoundError:  # web/app.py run as a script
    import spotify_api  # type: ignore[no-redef]
    import youtube_api  # type: ignore[no-redef]

LINK = "link"
BROWSE = "browse"
WRITE = "write"

YOUTUBE_COPY_LIMIT = 100  # playlistItems.insert costs 50 quota units of the 10,000 a day


class ProviderError(RuntimeError):
    """A safe-to-display provider failure (the service errors subclass RuntimeError too)."""


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@dataclass(frozen=True)
class Account:
    """One connected account of a provider, and its queue playlist for a list page."""

    id: str
    label: str
    provider: Provider
    settings: Any
    queue: str = ""
    queue_name: str = ""

    @property
    def field(self) -> str:
        return self.provider.field


class Provider:
    id = ""
    label = ""
    field = ""  # the item field holding the provider's stable id
    pages: tuple[str, ...] = ()  # list keys this provider serves ("watch", "listen")
    capabilities: frozenset[str] = frozenset()
    item_fields: tuple[str, ...] = ()  # every item field this provider stores
    url_field = ""  # the item field holding the link shown on the item
    link_placeholder = ""
    icon = "🔗"  # shown on an item that has a link

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    def accounts(self, key: str) -> list[Account]:
        return []

    def parse_link(self, value: str) -> dict[str, str]:
        raise ProviderError(f"{self.label} links are not supported")

    def playlists(self, account: Account, key: str) -> list[dict[str, str]]:
        raise ProviderError(f"{self.label} playlists cannot be browsed")

    def entries(self, account: Account, key: str, playlist_id: str) -> list[dict[str, Any]]:
        raise ProviderError(f"{self.label} playlists cannot be browsed")

    def create_playlist(self, account: Account, key: str, title: str) -> str:
        raise ProviderError(f"{self.label} playlists cannot be created")

    def add(
        self, account: Account, key: str, playlist_id: str, entries: list[dict[str, Any]]
    ) -> tuple[int, list[str]]:
        """Add entries to a playlist: ``(added, problems)``; stops at the first refusal."""
        raise ProviderError(f"{self.label} playlists cannot be edited")


# --- Spotify -----------------------------------------------------------------------------------


class SpotifyProvider(Provider):
    id = "spotify"
    label = "Spotify"
    field = "spotify_uri"
    pages = ("listen",)
    capabilities = frozenset({LINK, BROWSE, WRITE})
    item_fields = (
        "spotify_kind",
        "spotify_id",
        "spotify_uri",
        "spotify_url",
        "spotify_artist",
    )
    url_field = "spotify_url"
    link_placeholder = "https://open.spotify.com/track/..."

    def accounts(self, key: str) -> list[Account]:
        settings = spotify_api.settings_from_env()
        if key not in self.pages or not settings.oauth_file.is_file():
            return []
        return [Account(self.id, self.label, self, settings, settings.playlist_id, "To Listen")]

    def parse_link(self, value: str) -> dict[str, str]:
        return spotify_api.parse_link(value)

    def playlists(self, account: Account, key: str) -> list[dict[str, str]]:
        return spotify_api.user_playlists(account.settings)

    def entries(self, account: Account, key: str, playlist_id: str) -> list[dict[str, Any]]:
        items = spotify_api.playlist_items(
            dataclasses.replace(account.settings, playlist_id=playlist_id)
        )
        return [
            {
                "title": i["title"],
                "sub": i["spotify_kind"],
                "url": i["spotify_url"],
                "match": (self.field, i["spotify_uri"]),
            }
            for i in items
        ]

    def create_playlist(self, account: Account, key: str, title: str) -> str:
        return spotify_api.create_playlist(account.settings, title, "Copied by Clockwork.")

    def add(self, account, key, playlist_id, entries):
        try:
            uris = [e["match"][1] for e in entries]
            return spotify_api.add_items(account.settings, uris, playlist_id), []
        except (OSError, RuntimeError) as exc:
            return 0, [str(exc)]


# --- YouTube -----------------------------------------------------------------------------------


class YouTubeProvider(Provider):
    id = "youtube"
    label = "YouTube"
    field = "youtube_video_id"
    pages = ("watch", "listen")
    capabilities = frozenset({LINK, BROWSE, WRITE})
    item_fields = (
        "youtube_video_id",
        "youtube_playlist_item_id",
        "youtube_url",
        "youtube_channel",
        "youtube_account",
    )
    url_field = "youtube_url"
    link_placeholder = "https://youtu.be/..."

    def accounts(self, key: str) -> list[Account]:
        if key not in self.pages:
            return []
        try:
            rows = youtube_api.settings_list_from_env()
        except RuntimeError:
            return []
        return [
            Account(
                f"youtube-{slug(row.name)}",
                f"YouTube · {row.name}" if len(rows) > 1 else "YouTube",
                self,
                row,
                row.playlist_id(key),
                f"To {key.title()}",
            )
            for row in rows
            if row.oauth_file.is_file()
        ]

    def parse_link(self, value: str) -> dict[str, str]:
        video_id = youtube_api.video_id_from_url(value)
        return {"youtube_video_id": video_id, "youtube_url": youtube_api.video_url(video_id)}

    def playlists(self, account: Account, key: str) -> list[dict[str, str]]:
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "count": "",
                "url": "https://www.youtube.com/playlist?"
                + urllib.parse.urlencode({"list": row["id"]}),
                "owner": "",
                "private": "private" if row["privacy"] == "private" else "",
            }
            for row in youtube_api.owned_playlists(account.settings)
        ]

    def entries(self, account: Account, key: str, playlist_id: str) -> list[dict[str, Any]]:
        one = dataclasses.replace(account.settings, **{f"{key}_playlist_id": playlist_id})
        return [
            {
                "title": v["title"],
                "sub": v.get("channel", ""),
                "url": v["url"],
                "match": (self.field, v["video_id"]),
            }
            for v in youtube_api.playlist_videos(one, key)
        ]

    def create_playlist(self, account: Account, key: str, title: str) -> str:
        return youtube_api.create_private_playlist(account.settings, title, "Copied by Clockwork.")

    def add(self, account, key, playlist_id, entries):
        added = 0
        try:
            for entry in entries[:YOUTUBE_COPY_LIMIT]:
                youtube_api.add_video_to_playlist_id(
                    account.settings, playlist_id, entry["match"][1]
                )
                added += 1
            return added, []
        except (OSError, RuntimeError) as exc:
            return added, [str(exc)]


# --- Apple Music (links only until a MusicKit adapter exists) ----------------------------------

_APPLE_KINDS = ("album", "song", "playlist", "artist", "music-video", "station", "podcast")
_APPLE_ID = re.compile(r"(?:pl\.[A-Za-z0-9._-]{4,64}|ra\.[A-Za-z0-9._-]{4,64}|(?:id)?\d{4,20})")


class AppleMusicProvider(Provider):
    """Links to Apple Music. Playlist sync would need a MusicKit developer token and a user token.

    To add it: implement ``accounts``/``playlists``/``entries`` (read) and ``create_playlist``/``add``
    (write) against ``api.music.apple.com/v1/me/library`` and add ``BROWSE``/``WRITE`` here.
    """

    id = "apple-music"
    label = "Apple Music"
    field = "apple_music_id"
    pages = ("listen",)
    capabilities = frozenset({LINK})
    item_fields = ("apple_music_kind", "apple_music_id", "apple_music_url")
    url_field = "apple_music_url"
    link_placeholder = "https://music.apple.com/us/album/..."
    icon = "🍎"

    def parse_link(self, value: str) -> dict[str, str]:
        text = str(value or "").strip()
        if not text:
            raise ProviderError("Apple Music link cannot be empty")
        parsed = urllib.parse.urlsplit(text if "://" in text else f"https://{text}")
        host = (parsed.hostname or "").casefold().rstrip(".")
        parts = [p for p in parsed.path.split("/") if p]
        if host not in {"music.apple.com", "embed.music.apple.com", "geo.music.apple.com"}:
            parts = []
        storefront = ""
        if parts and re.fullmatch(r"[a-z]{2}", parts[0]):
            storefront, parts = parts[0], parts[1:]
        if not storefront or len(parts) < 2 or parts[0] not in _APPLE_KINDS:
            raise ProviderError(
                "Enter a valid music.apple.com link "
                "(album, song, playlist, artist, music video, station or podcast)"
            )
        kind, apple_id = parts[0], parts[-1]
        if not _APPLE_ID.fullmatch(apple_id):
            raise ProviderError("That music.apple.com link has no recognizable ID")
        query = urllib.parse.parse_qs(parsed.query)
        song = query.get("i", [""])[0]
        if kind == "album" and re.fullmatch(r"\d{4,20}", song):  # a song inside an album
            kind, apple_id = "song", song
        slug_part = parts[1] if len(parts) >= 3 else "-"
        path = f"{storefront}/{parts[0]}/{slug_part}/{parts[-1]}"
        url = f"https://music.apple.com/{path}" + (f"?i={song}" if kind == "song" and song else "")
        return {"apple_music_kind": kind, "apple_music_id": apple_id, "apple_music_url": url}


# --- Apple TV (links only: Apple publishes no watchlist / Up Next API) -------------------------

_TV_KINDS = ("movie", "show", "episode", "season")
_TV_ID = re.compile(r"umc\.[a-z]{3}\.[a-z0-9]{10,40}")


class AppleTVProvider(Provider):
    """Links to the Apple TV app/site. There is no public watchlist API, so nothing syncs.

    ``tv.apple.com/<storefront>/<movie|show|episode|season>/<slug>/umc.<xxx>.<id>`` links are
    parsed and canonicalised; an episode keeps its ``showId`` so the link opens in context.
    """

    id = "apple-tv"
    label = "Apple TV"
    field = "apple_tv_id"
    pages = ("watch",)
    capabilities = frozenset({LINK})
    item_fields = ("apple_tv_kind", "apple_tv_id", "apple_tv_url")
    url_field = "apple_tv_url"
    link_placeholder = "https://tv.apple.com/us/movie/..."
    icon = "🍎"

    def parse_link(self, value: str) -> dict[str, str]:
        text = str(value or "").strip()
        if not text:
            raise ProviderError("Apple TV link cannot be empty")
        parsed = urllib.parse.urlsplit(text if "://" in text else f"https://{text}")
        host = (parsed.hostname or "").casefold().rstrip(".")
        parts = [p for p in parsed.path.split("/") if p] if host == "tv.apple.com" else []
        storefront = ""
        if parts and re.fullmatch(r"[a-z]{2}", parts[0]):
            storefront, parts = parts[0], parts[1:]
        if not storefront or len(parts) < 2 or parts[0] not in _TV_KINDS:
            raise ProviderError("Enter a valid tv.apple.com link (movie, show, season or episode)")
        kind, tv_id = parts[0], parts[-1]
        if not _TV_ID.fullmatch(tv_id):
            raise ProviderError("That tv.apple.com link has no recognizable ID")
        slug_part = parts[1] if len(parts) >= 3 else "-"
        url = f"https://tv.apple.com/{storefront}/{kind}/{slug_part}/{tv_id}"
        show = urllib.parse.parse_qs(parsed.query).get("showId", [""])[0]
        if kind == "episode" and _TV_ID.fullmatch(show):
            url += f"?showId={show}"
        return {"apple_tv_kind": kind, "apple_tv_id": tv_id, "apple_tv_url": url}


REGISTRY: tuple[Provider, ...] = (
    SpotifyProvider(),
    YouTubeProvider(),
    AppleMusicProvider(),
    AppleTVProvider(),
)


def get(provider_id: str) -> Provider | None:
    return next((p for p in REGISTRY if p.id == provider_id), None)


def for_page(key: str, capability: str = "") -> list[Provider]:
    """Providers serving a list page, optionally only those with a capability."""
    return [p for p in REGISTRY if key in p.pages and (not capability or p.can(capability))]


def accounts_for(key: str, capability: str = "") -> list[Account]:
    """Connected accounts for a list page that support ``capability`` (default: browsing)."""
    capability = capability or BROWSE
    return [a for p in for_page(key, capability) for a in p.accounts(key)]


def item_fields_for(key: str) -> list[str]:
    return [f for p in for_page(key) for f in p.item_fields]
