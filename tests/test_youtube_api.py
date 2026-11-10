"""Unit tests for the owner-only YouTube adapter."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import pytest

from web import youtube_api


def _credential_file(path: Path, *, mode: int = 0o600, scopes: list[str] | None = None) -> Path:
    path.write_text(
        json.dumps(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "scopes": scopes or [youtube_api.YOUTUBE_SCOPE],
            }
        )
    )
    path.chmod(mode)
    return path


def test_load_credentials_requires_owner_only_regular_file(tmp_path):
    path = _credential_file(tmp_path / "youtube.json", mode=0o644)

    with pytest.raises(youtube_api.YouTubeError, match="0600"):
        youtube_api.load_credentials(path)

    path.chmod(0o600)
    assert youtube_api.load_credentials(path)["refresh_token"] == "refresh-token"

    link = tmp_path / "youtube-link.json"
    link.symlink_to(path)
    with pytest.raises(youtube_api.YouTubeError, match="symbolic link"):
        youtube_api.load_credentials(link)


def test_load_credentials_requires_youtube_scope(tmp_path):
    path = tmp_path / "youtube.json"
    path.write_text(
        json.dumps(
            {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "refresh_token": "refresh-token",
                "scopes": ["openid"],
            }
        )
    )
    path.chmod(0o600)

    with pytest.raises(youtube_api.YouTubeError, match="required YouTube scope"):
        youtube_api.load_credentials(path)


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://www.youtube.com/watch?v=abc_123-XYZ", "abc_123-XYZ"),
        ("https://youtu.be/abc_123-XYZ?t=30", "abc_123-XYZ"),
        ("https://youtube.com/shorts/abc_123-XYZ", "abc_123-XYZ"),
        ("music.youtube.com/watch?v=abc_123-XYZ", "abc_123-XYZ"),
    ],
)
def test_video_id_from_url_accepts_supported_youtube_urls(url, video_id):
    assert youtube_api.video_id_from_url(url) == video_id
    assert youtube_api.video_url(video_id) == f"https://www.youtube.com/watch?v={video_id}"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=abc_123-XYZ",
        "https://youtube.example/watch?v=abc_123-XYZ",
        "https://youtube.com/channel/abc_123-XYZ",
        "not a URL",
    ],
)
def test_video_id_from_url_rejects_non_video_urls(url):
    with pytest.raises(youtube_api.YouTubeError, match="valid youtube"):
        youtube_api.video_id_from_url(url)


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_playlist_videos_refreshes_token_and_paginates(tmp_path, monkeypatch):
    oauth = _credential_file(tmp_path / "youtube.json")
    requests = []
    pages = [
        {
            "items": [
                {
                    "id": "playlist-item-1",
                    "snippet": {
                        "title": "First video",
                        "videoOwnerChannelTitle": "Creator",
                        "resourceId": {"videoId": "video-1"},
                    },
                    "contentDetails": {"videoId": "video-1"},
                },
                {
                    "id": "private",
                    "snippet": {"title": "Private video"},
                    "contentDetails": {"videoId": "private-video"},
                },
            ],
            "nextPageToken": "next",
        },
        {
            "items": [
                {
                    "id": "playlist-item-2",
                    "snippet": {"title": "Second video", "resourceId": {"videoId": "video-2"}},
                    "contentDetails": {"videoId": "video-2"},
                }
            ]
        },
    ]

    def fake_urlopen(request, timeout):
        assert timeout == 30
        requests.append(request)
        if request.full_url == youtube_api.TOKEN_URL:
            assert b"refresh_token=refresh-token" in request.data
            return _Response({"access_token": "access-token"})
        assert request.headers["Authorization"] == "Bearer access-token"
        return _Response(pages.pop(0))

    monkeypatch.setattr(youtube_api.urllib.request, "urlopen", fake_urlopen)
    settings = youtube_api.Settings(oauth_file=oauth, name="Personal", watch_playlist_id="playlist")

    videos = youtube_api.playlist_videos(settings, "watch")

    assert [video["video_id"] for video in videos] == ["video-1", "video-2"]
    assert videos[0]["url"] == "https://www.youtube.com/watch?v=video-1"
    assert videos[0]["account"] == "Personal"
    first_query = urllib.parse.parse_qs(urllib.parse.urlsplit(requests[1].full_url).query)
    second_query = urllib.parse.parse_qs(urllib.parse.urlsplit(requests[2].full_url).query)
    assert first_query["playlistId"] == ["playlist"]
    assert "pageToken" not in first_query
    assert second_query["pageToken"] == ["next"]


def test_public_status_does_not_open_credentials(tmp_path, monkeypatch):
    oauth = _credential_file(tmp_path / "youtube.json")
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: pytest.fail("opened secret"))

    status = youtube_api.public_status(
        youtube_api.Settings(oauth_file=oauth, listen_playlist_id="listen-playlist")
    )

    assert status["credential_file_present"] is True
    assert status["account_count"] == 1
    assert status["watch"]["configured"] is False
    assert status["listen"]["configured"] is True


def test_multi_account_registry_is_owner_only_and_resolves_relative_oauth_paths(tmp_path):
    registry = tmp_path / "youtube-accounts.json"
    registry.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "name": "Main",
                        "oauth_file": "youtube-main.json",
                        "watch_playlist_id": "watch-main",
                    },
                    {
                        "name": "Music",
                        "oauth_file": "/credentials/youtube-music.json",
                        "listen_playlist_id": "listen-music",
                        "listen_category": "Music account",
                    },
                ]
            }
        )
    )
    registry.chmod(0o600)

    accounts = youtube_api.settings_list_from_env(
        {"CLOCKWORK_YOUTUBE_ACCOUNTS_FILE": str(registry)}
    )

    assert [account.name for account in accounts] == ["Main", "Music"]
    assert accounts[0].oauth_file == tmp_path / "youtube-main.json"
    assert accounts[1].oauth_file == Path("/credentials/youtube-music.json")
    assert accounts[1].category("listen") == "Music account"

    registry.chmod(0o644)
    with pytest.raises(youtube_api.YouTubeError, match="0600"):
        youtube_api.settings_list_from_env({"CLOCKWORK_YOUTUBE_ACCOUNTS_FILE": str(registry)})


def test_multi_account_status_reports_aggregate_without_paths(tmp_path):
    present = _credential_file(tmp_path / "present.json")
    status = youtube_api.public_status(
        [
            youtube_api.Settings(oauth_file=present, name="Main", watch_playlist_id="watch"),
            youtube_api.Settings(
                oauth_file=tmp_path / "missing.json",
                name="Music",
                listen_playlist_id="listen",
            ),
        ]
    )

    assert status["account_count"] == 2
    assert status["watch"]["configured_accounts"] == 1
    assert status["listen"]["configured_accounts"] == 0
    assert [row["name"] for row in status["accounts"]] == ["Main", "Music"]
    assert "oauth_file" not in json.dumps(status)


def test_ensure_private_playlist_requires_write_scope(tmp_path):
    settings = youtube_api.Settings(oauth_file=_credential_file(tmp_path / "readonly.json"))

    with pytest.raises(youtube_api.YouTubeError, match="reauthorized"):
        youtube_api.ensure_private_playlist(settings, "To Watch")


def test_ensure_private_playlist_reuses_existing_private_playlist(tmp_path, monkeypatch):
    oauth = _credential_file(
        tmp_path / "write.json",
        scopes=[youtube_api.YOUTUBE_READ_SCOPE, youtube_api.YOUTUBE_WRITE_SCOPE],
    )
    settings = youtube_api.Settings(oauth_file=oauth, name="Main")
    monkeypatch.setattr(youtube_api, "access_token", lambda _credentials: "token")

    def api_get(resource, _token, **_params):
        if resource == "channels":
            return {"items": [{"id": "channel"}]}
        return {
            "items": [
                {
                    "id": "playlist",
                    "snippet": {"title": "To Watch"},
                    "status": {"privacyStatus": "private"},
                }
            ]
        }

    monkeypatch.setattr(youtube_api, "_api_get", api_get)
    monkeypatch.setattr(
        youtube_api,
        "_api_post",
        lambda *_args, **_kwargs: pytest.fail("should not create a duplicate"),
    )

    assert youtube_api.ensure_private_playlist(settings, "To Watch") == (
        "playlist",
        False,
    )


def test_ensure_private_playlist_creates_private_playlist(tmp_path, monkeypatch):
    oauth = _credential_file(
        tmp_path / "write.json",
        scopes=[youtube_api.YOUTUBE_READ_SCOPE, youtube_api.YOUTUBE_WRITE_SCOPE],
    )
    settings = youtube_api.Settings(oauth_file=oauth, name="Main")
    monkeypatch.setattr(youtube_api, "access_token", lambda _credentials: "token")
    monkeypatch.setattr(
        youtube_api,
        "_api_get",
        lambda resource, _token, **_params: (
            {"items": [{"id": "channel"}]} if resource == "channels" else {"items": []}
        ),
    )
    requests = []

    def api_post(resource, token, payload, **params):
        requests.append((resource, token, payload, params))
        return {"id": "created-playlist"}

    monkeypatch.setattr(youtube_api, "_api_post", api_post)

    assert youtube_api.ensure_private_playlist(settings, "To Listen") == (
        "created-playlist",
        True,
    )
    assert requests[0][0:2] == ("playlists", "token")
    assert requests[0][2]["status"] == {"privacyStatus": "private"}
    assert requests[0][2]["snippet"]["title"] == "To Listen"


def test_add_video_to_playlist_posts_explicit_video(tmp_path, monkeypatch):
    oauth = _credential_file(
        tmp_path / "write.json",
        scopes=[youtube_api.YOUTUBE_READ_SCOPE, youtube_api.YOUTUBE_WRITE_SCOPE],
    )
    settings = youtube_api.Settings(
        oauth_file=oauth,
        name="Main",
        watch_playlist_id="watch-playlist",
    )
    monkeypatch.setattr(youtube_api, "access_token", lambda _credentials: "token")
    requests = []

    def api_post(resource, token, payload, **params):
        requests.append((resource, token, payload, params))
        return {"id": "playlist-item"}

    monkeypatch.setattr(youtube_api, "_api_post", api_post)

    assert youtube_api.add_video_to_playlist(settings, "watch", "abc_123-XYZ") == ("playlist-item")
    assert requests == [
        (
            "playlistItems",
            "token",
            {
                "snippet": {
                    "playlistId": "watch-playlist",
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": "abc_123-XYZ",
                    },
                }
            },
            {"part": "snippet"},
        )
    ]


def test_set_playlist_id_preserves_registry_and_owner_only_mode(tmp_path, monkeypatch):
    registry = tmp_path / "youtube-accounts.json"
    registry.write_text(
        json.dumps(
            {
                "note": "preserve",
                "accounts": [
                    {"name": "Main", "oauth_file": "main.json"},
                    {"name": "Other", "oauth_file": "other.json"},
                ],
            }
        )
    )
    registry.chmod(0o600)
    monkeypatch.setenv("CLOCKWORK_YOUTUBE_ACCOUNTS_FILE", str(registry))

    youtube_api.set_playlist_id("Main", "watch", "playlist-id")

    saved = json.loads(registry.read_text())
    assert saved["note"] == "preserve"
    assert saved["accounts"][0]["watch_playlist_id"] == "playlist-id"
    assert "watch_playlist_id" not in saved["accounts"][1]
    assert registry.stat().st_mode & 0o777 == 0o600
