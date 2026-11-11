"""Spotify link parsing, the To Listen playlist sync, and the owner-only OAuth adapter."""

# Fixtures are reused from test_web_lists as parameters.
# ruff: noqa: F811, F401

from __future__ import annotations

import json
import urllib.parse

import pytest

from tests.test_web_lists import _csrf, client, list_files, web_app
from web import spotify_api

TRACK = "4uLU6hMCjMI75M1A2tKUQC"
EPISODE = "512ojhOuo1ktJprKbVcKyQ"
ALBUM = "1DFixLWuPkv3KT3TnV35m3"


@pytest.mark.parametrize(
    ("value", "kind", "sid"),
    [
        (f"https://open.spotify.com/track/{TRACK}?si=abc", "track", TRACK),
        (f"open.spotify.com/intl-de/album/{ALBUM}", "album", ALBUM),
        (f"spotify:episode:{EPISODE}", "episode", EPISODE),
    ],
)
def test_parse_link_accepts_urls_and_uris(value, kind, sid):
    link = spotify_api.parse_link(value)
    assert (link["spotify_kind"], link["spotify_id"]) == (kind, sid)
    assert link["spotify_uri"] == f"spotify:{kind}:{sid}"
    assert link["spotify_url"] == f"https://open.spotify.com/{kind}/{sid}"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://evil.example/track/" + TRACK,
        "https://open.spotify.com/track/short",
        f"https://open.spotify.com/user/{TRACK}",
        "spotify:track",
    ],
)
def test_parse_link_rejects_everything_else(value):
    with pytest.raises(spotify_api.SpotifyError):
        spotify_api.parse_link(value)


def _grant(path, scopes=("playlist-read-private", "playlist-modify-private"), mode=0o600):
    path.write_text(
        json.dumps(
            {
                "client_id": "id",
                "client_secret": "secret",
                "refresh_token": "r",
                "scopes": list(scopes),
            }
        )
    )
    path.chmod(mode)
    return path


def test_credentials_require_an_owner_only_file_and_the_read_scope(tmp_path):
    with pytest.raises(spotify_api.SpotifyError, match="0600"):
        spotify_api.load_credentials(_grant(tmp_path / "g.json", mode=0o644))
    with pytest.raises(spotify_api.SpotifyError, match="playlist-read-private"):
        spotify_api.load_credentials(_grant(tmp_path / "h.json", scopes=()))
    assert spotify_api.load_credentials(_grant(tmp_path / "i.json"))["refresh_token"] == "r"


def test_settings_find_the_stored_playlist_id_and_never_leak_paths(tmp_path):
    path = _grant(tmp_path / "g.json")
    payload = json.loads(path.read_text())
    payload["listen_playlist_id"] = TRACK
    path.write_text(json.dumps(payload))
    settings = spotify_api.settings_from_env({"CLOCKWORK_SPOTIFY_OAUTH_FILE": str(path)})
    assert settings.playlist_id == TRACK and settings.ready()
    status = spotify_api.public_status(settings)
    assert status == {
        "credential_file_present": True,
        "playlist_configured": True,
        "configured": True,
    }
    assert spotify_api.playlist_link(settings) == f"https://open.spotify.com/playlist/{TRACK}"


def test_playlist_items_reads_the_2026_item_shape_and_skips_local_and_unsupported(
    tmp_path, monkeypatch
):
    settings = spotify_api.Settings(oauth_file=_grant(tmp_path / "g.json"), playlist_id=ALBUM)
    monkeypatch.setattr(spotify_api, "access_token", lambda _s: "tok")
    page = {
        "items": [
            {
                "item": {
                    "id": TRACK,
                    "type": "track",
                    "name": "Song",
                    "artists": [{"name": "A"}, {"name": "B"}],
                }
            },
            {"track": {"id": EPISODE, "type": "episode", "name": "Ep", "show": {"name": "Pod"}}},
            {"item": {"id": ALBUM, "type": "track", "name": "Local", "is_local": True}},
            {"item": None},
        ],
        "next": None,
    }
    seen = []
    monkeypatch.setattr(spotify_api, "_get", lambda path, token, **kw: seen.append(path) or page)
    rows = spotify_api.playlist_items(settings)
    assert seen == [f"playlists/{ALBUM}/items"]
    assert [r["title"] for r in rows] == ["A, B – Song", "Pod – Ep"]
    assert rows[1]["spotify_uri"] == f"spotify:episode:{EPISODE}"


def test_sync_imports_pushes_only_active_tracks_and_is_idempotent(
    client, list_files, tmp_path, monkeypatch
):
    settings = spotify_api.Settings(oauth_file=_grant(tmp_path / "g.json"), playlist_id=ALBUM)
    remote = [
        {
            "spotify_kind": "track",
            "spotify_id": TRACK,
            "spotify_uri": f"spotify:track:{TRACK}",
            "spotify_url": f"https://open.spotify.com/track/{TRACK}",
            "spotify_artist": "A",
            "title": "A – Song",
        }
    ]
    pushed: list[list[str]] = []
    monkeypatch.setattr(web_app.spotify_api, "settings_from_env", lambda: settings)
    monkeypatch.setattr(web_app.spotify_api, "playlist_items", lambda _s: remote)
    monkeypatch.setattr(
        web_app.spotify_api, "add_items", lambda _s, uris: pushed.append(uris) or len(uris)
    )
    local_track = f"spotify:episode:{EPISODE}"
    list_files["LISTEN_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Podcasts",
                        "items": [
                            {
                                "title": "Pod ep",
                                "watched": False,
                                "spotify_kind": "episode",
                                "spotify_uri": local_track,
                            },
                            {
                                "title": "An album",
                                "watched": False,
                                "spotify_kind": "album",
                                "spotify_uri": f"spotify:album:{ALBUM}",
                            },
                            {
                                "title": "Done ep",
                                "watched": True,
                                "spotify_kind": "track",
                                "spotify_uri": "spotify:track:" + "x" * 22,
                            },
                        ],
                    }
                ]
            }
        )
    )
    token = _csrf(client, "/api/listen")
    for _ in range(2):
        assert client.post("/to-listen/sync-spotify", data={"csrf_token": token}).status_code == 302

    saved = json.loads(list_files["LISTEN_LIST_FILE"].read_text())
    imported = saved["categories"][-1]
    assert imported["name"] == "Spotify"
    assert [i["title"] for i in imported["items"]] == ["A – Song"]  # imported once, not twice
    assert imported["items"][0]["watched"] is False
    # Only the unlistened episode is pushed: not the album, not the finished track.
    assert pushed == [[local_track], [local_track]]
    history = list_files["LISTEN_LIST_HISTORY_FILE"].read_text()
    assert '"action": "sync-spotify"' in history


def test_link_route_attaches_a_spotify_link_and_rejects_bad_ones(client, list_files):
    list_files["LISTEN_LIST_FILE"].write_text(
        json.dumps({"categories": [{"name": "Albums", "items": [{"title": "OK Computer"}]}]})
    )
    token = _csrf(client, "/api/listen")
    bad = client.post(
        "/to-listen/link-spotify",
        data={
            "csrf_token": token,
            "category": "Albums",
            "item": "OK Computer",
            "spotify_url": "https://example.com/x",
        },
    )
    assert bad.status_code == 302
    assert (
        "spotify_url"
        not in json.loads(list_files["LISTEN_LIST_FILE"].read_text())["categories"][0]["items"][0]
    )
    client.post(
        "/to-listen/link-spotify",
        data={
            "csrf_token": token,
            "category": "Albums",
            "item": "OK Computer",
            "spotify_url": f"https://open.spotify.com/album/{ALBUM}",
        },
    )
    item = json.loads(list_files["LISTEN_LIST_FILE"].read_text())["categories"][0]["items"][0]
    assert (item["spotify_kind"], item["spotify_id"]) == ("album", ALBUM)
    page = client.get("/to-listen").get_data(as_text=True)
    assert f"https://open.spotify.com/album/{ALBUM}" in page
    assert "Sync Spotify" in page


def _two_playlists(monkeypatch, tmp_path, items_for):
    settings = spotify_api.Settings(oauth_file=_grant(tmp_path / "g.json"), playlist_id=ALBUM)
    monkeypatch.setattr(web_app.spotify_api, "settings_from_env", lambda: settings)
    monkeypatch.setattr(
        web_app.spotify_api,
        "user_playlists",
        lambda _s: [
            {
                "id": ALBUM,
                "title": "To Listen",
                "count": "2",
                "url": "u1",
                "owner": "",
                "private": "private",
            },
            {
                "id": TRACK,
                "title": "Road trip",
                "count": "1",
                "url": "u2",
                "owner": "",
                "private": "",
            },
        ],
    )
    monkeypatch.setattr(
        web_app.spotify_api,
        "playlist_items",
        lambda s: items_for[s.playlist_id],
    )
    monkeypatch.setattr(web_app.youtube_api, "settings_list_from_env", lambda: [])
    web_app._PLAYLIST_CACHE.clear()


def _entry(kind, sid, title):
    return {
        "spotify_kind": kind,
        "spotify_id": sid,
        "spotify_uri": f"spotify:{kind}:{sid}",
        "spotify_url": f"https://open.spotify.com/{kind}/{sid}",
        "spotify_artist": "",
        "title": title,
    }


def test_playlist_view_lists_every_playlist_and_opens_any_of_them(
    client, list_files, tmp_path, monkeypatch
):
    items = {
        ALBUM: [_entry("track", TRACK, "A – Song"), _entry("track", ALBUM, "B – New")],
        TRACK: [_entry("track", EPISODE, "Road song")],
    }
    _two_playlists(monkeypatch, tmp_path, items)
    list_files["LISTEN_LIST_FILE"].write_text(
        json.dumps(
            {
                "categories": [
                    {
                        "name": "Albums",
                        "items": [
                            {
                                "title": "A – Song",
                                "watched": True,
                                "spotify_kind": "track",
                                "spotify_uri": f"spotify:track:{TRACK}",
                            },
                            {
                                "title": "Queued ep",
                                "watched": False,
                                "spotify_kind": "episode",
                                "spotify_uri": f"spotify:episode:{EPISODE}",
                            },
                            {
                                "title": "An album",
                                "watched": False,
                                "spotify_kind": "album",
                                "spotify_uri": f"spotify:album:{ALBUM}",
                            },
                        ],
                    }
                ]
            }
        )
    )
    # Default: the account's playlists in the sidebar, the To Listen queue opened.
    page = client.get("/to-listen/playlist").get_data(as_text=True)
    assert "Road trip" in page and "Your playlists · 2" in page
    assert "A – Song" in page and "B – New" in page
    assert "listened · Albums" in page and "not in list" in page
    assert "1 linked item not yet pushed" in page  # the episode; the album is link-only
    # Any other playlist opens too, and the queue-only push hint is not shown for it.
    other = client.get(f"/to-listen/playlist?source=spotify&playlist={TRACK}").get_data(
        as_text=True
    )
    assert "Road song" in other and "B – New" not in other
    assert "not yet pushed" not in other
    # An id the account does not list is ignored, never fetched.
    bogus = client.get("/to-listen/playlist?source=spotify&playlist=" + "z" * 22)
    assert bogus.status_code == 200 and "A – Song" in bogus.get_data(as_text=True)
    assert client.get("/to-read/playlist").status_code == 404


def test_playlist_view_reports_a_provider_error_instead_of_failing(
    client, list_files, tmp_path, monkeypatch
):
    _two_playlists(monkeypatch, tmp_path, {})

    def boom(_s):
        raise spotify_api.SpotifyError("Spotify me failed with HTTP 403")

    monkeypatch.setattr(web_app.spotify_api, "user_playlists", boom)
    response = client.get("/to-listen/playlist")
    assert response.status_code == 200
    assert "could not be read: Spotify me failed with HTTP 403" in response.get_data(as_text=True)


def test_copy_selected_items_into_an_existing_playlist_skips_duplicates(
    client, list_files, tmp_path, monkeypatch
):
    items = {
        ALBUM: [_entry("track", TRACK, "A – Song"), _entry("track", ALBUM, "B – New")],
        TRACK: [_entry("track", TRACK, "A – Song")],  # destination already has A
    }
    _two_playlists(monkeypatch, tmp_path, items)
    added: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        web_app.spotify_api,
        "add_items",
        lambda _s, uris, playlist_id="": added.append((playlist_id, uris)) or len(uris),
    )
    token = _csrf(client, "/api/listen")
    response = client.post(
        "/to-listen/playlist/copy",
        data={
            "csrf_token": token,
            "source": "spotify",
            "playlist": ALBUM,
            "mode": "selected",
            "item": [
                f"spotify:track:{TRACK}",
                f"spotify:track:{ALBUM}",
                "spotify:track:" + "z" * 22,
            ],
            "dest": f"spotify|{TRACK}",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    # A already there, the made-up id is not on the source playlist: only B is copied.
    assert added == [(TRACK, [f"spotify:track:{ALBUM}"])]
    assert "1 item copied" in response.get_data(as_text=True)
    assert '"action": "copy-playlist"' in list_files["LISTEN_LIST_HISTORY_FILE"].read_text()


def test_duplicate_a_whole_playlist_into_a_new_one(client, list_files, tmp_path, monkeypatch):
    items = {ALBUM: [_entry("track", TRACK, "A"), _entry("episode", EPISODE, "E")], TRACK: []}
    _two_playlists(monkeypatch, tmp_path, items)
    created: list[str] = []
    added: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        web_app.spotify_api,
        "create_playlist",
        lambda _s, title, description="": created.append(title) or EPISODE,
    )
    monkeypatch.setattr(
        web_app.spotify_api,
        "add_items",
        lambda _s, uris, playlist_id="": added.append((playlist_id, uris)) or len(uris),
    )
    token = _csrf(client, "/api/listen")
    client.post(
        "/to-listen/playlist/copy",
        data={
            "csrf_token": token,
            "source": "spotify",
            "playlist": ALBUM,
            "mode": "all",
            "dest": "spotify|new",
            "new_name": "",
        },
    )
    assert created == ["To Listen (copy)"]
    assert added == [(EPISODE, [f"spotify:track:{TRACK}", f"spotify:episode:{EPISODE}"])]


def test_copy_refuses_other_services_unknown_sources_and_selecting_nothing(
    client, list_files, tmp_path, monkeypatch
):
    _two_playlists(monkeypatch, tmp_path, {ALBUM: [_entry("track", TRACK, "A")], TRACK: []})
    called = []
    monkeypatch.setattr(web_app.spotify_api, "add_items", lambda *a, **k: called.append(a) or 1)
    token = _csrf(client, "/api/listen")
    base = {"csrf_token": token, "source": "spotify", "playlist": ALBUM, "mode": "all"}
    for data in (
        {**base, "dest": "youtube|new"},  # a service that is not connected / not the same
        {**base, "playlist": "z" * 22, "dest": f"spotify|{TRACK}"},  # source not on the account
        {**base, "dest": f"spotify|{ALBUM}"},  # copy onto itself
        {**base, "mode": "selected", "dest": f"spotify|{TRACK}"},  # nothing ticked
        {**base, "dest": "spotify|" + "z" * 22},  # destination not on the account
    ):
        assert client.post("/to-listen/playlist/copy", data=data).status_code == 302
    assert called == []
    assert client.post("/to-read/playlist/copy", data=base).status_code in (403, 404)


def _youtube_account(tmp_path, name):
    from web import youtube_api

    path = tmp_path / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "client_id": "i",
                "client_secret": "s",
                "refresh_token": "r",
                "scopes": [youtube_api.YOUTUBE_WRITE_SCOPE],
            }
        )
    )
    path.chmod(0o600)
    return youtube_api.Settings(oauth_file=path, name=name, watch_playlist_id="WATCHQUEUE1")


def test_copy_between_two_youtube_accounts_creates_and_fills_a_private_playlist(
    client, list_files, tmp_path, monkeypatch
):
    me, partner = _youtube_account(tmp_path, "Me"), _youtube_account(tmp_path, "Partner")
    monkeypatch.setattr(web_app.youtube_api, "settings_list_from_env", lambda: [me, partner])
    monkeypatch.setattr(
        web_app.youtube_api,
        "owned_playlists",
        lambda s: [
            {"id": "PL" + s.name.ljust(8, "x"), "title": f"{s.name} mix", "privacy": "private"}
        ],
    )
    monkeypatch.setattr(
        web_app.youtube_api,
        "playlist_videos",
        lambda s, key: (
            [
                {
                    "video_id": f"vid{n}abcdef",
                    "playlist_item_id": "i",
                    "title": f"Video {n}",
                    "channel": "C",
                    "account": s.name,
                    "url": f"https://www.youtube.com/watch?v=vid{n}abcdef",
                }
                for n in (1, 2)
            ]
            if s.playlist_id("watch") == "PL" + "Me".ljust(8, "x")
            else []
        ),
    )
    made, added = [], []
    monkeypatch.setattr(
        web_app.youtube_api,
        "create_private_playlist",
        lambda s, title, description="": made.append((s.name, title)) or "PLNEWNEWNEW",
    )
    monkeypatch.setattr(
        web_app.youtube_api,
        "add_video_to_playlist_id",
        lambda s, pid, vid: added.append((s.name, pid, vid)) or "item",
    )
    web_app._PLAYLIST_CACHE.clear()
    token = _csrf(client, "/api/watch")
    source = "PL" + "Me".ljust(8, "x")
    client.post(
        "/to-watch/playlist/copy",
        data={
            "csrf_token": token,
            "source": "youtube-me",
            "playlist": source,
            "mode": "selected",
            "item": ["vid2abcdef"],
            "dest": "youtube-partner|new",
            "new_name": "From Me",
        },
    )
    assert made == [("Partner", "From Me")]
    assert added == [("Partner", "PLNEWNEWNEW", "vid2abcdef")]
