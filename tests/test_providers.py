"""The provider registry, Apple Music link parsing, and the generic link route."""

# Fixtures are reused from test_web_lists as parameters.
# ruff: noqa: F811, F401

from __future__ import annotations

import json

import pytest

from tests.test_web_lists import _csrf, client, list_files, web_app
from web import providers


def test_registry_declares_what_each_provider_can_do():
    by_id = {p.id: p for p in providers.REGISTRY}
    assert set(by_id) == {"spotify", "youtube", "apple-music", "apple-tv"}
    assert by_id["apple-music"].capabilities == {providers.LINK}  # links only for now
    assert by_id["apple-tv"].capabilities == {providers.LINK}
    assert by_id["spotify"].can(providers.WRITE) and by_id["youtube"].can(providers.BROWSE)
    assert [p.id for p in providers.for_page("watch")] == ["youtube", "apple-tv"]
    assert [p.id for p in providers.for_page("watch", providers.BROWSE)] == ["youtube"]
    assert [p.id for p in providers.for_page("listen")] == ["spotify", "youtube", "apple-music"]
    assert [p.id for p in providers.for_page("listen", providers.BROWSE)] == ["spotify", "youtube"]
    assert providers.get("nope") is None
    assert "apple_music_url" in providers.item_fields_for("listen")
    assert "apple_music_url" not in providers.item_fields_for("watch")


@pytest.mark.parametrize("key", ["watch", "listen"])
def test_every_provider_field_is_a_stored_item_field(key):
    extras = set(web_app._simple_list_config(key)["extras"])
    assert set(providers.item_fields_for(key)) <= extras


def test_a_link_only_provider_has_no_accounts_and_refuses_playlist_calls():
    apple = providers.get("apple-music")
    assert apple.accounts("listen") == []
    with pytest.raises(providers.ProviderError):
        apple.playlists(None, "listen")
    with pytest.raises(providers.ProviderError):
        apple.add(None, "listen", "x", [])


@pytest.mark.parametrize(
    ("value", "kind", "apple_id", "url"),
    [
        (
            "https://music.apple.com/us/album/ok-computer/1097861387",
            "album",
            "1097861387",
            "https://music.apple.com/us/album/ok-computer/1097861387",
        ),
        (  # a song inside an album link becomes the song
            "https://music.apple.com/gb/album/paranoid-android/1097861387?i=1097862870&uo=4",
            "song",
            "1097862870",
            "https://music.apple.com/gb/album/paranoid-android/1097861387?i=1097862870",
        ),
        (
            "music.apple.com/us/playlist/todays-hits/pl.f4d106fed2bd41149aaacabb233eb5eb",
            "playlist",
            "pl.f4d106fed2bd41149aaacabb233eb5eb",
            "https://music.apple.com/us/playlist/todays-hits/pl.f4d106fed2bd41149aaacabb233eb5eb",
        ),
        (
            "https://music.apple.com/us/podcast/some-show/id1234567890",
            "podcast",
            "id1234567890",
            "https://music.apple.com/us/podcast/some-show/id1234567890",
        ),
    ],
)
def test_apple_music_links_are_parsed_and_canonicalised(value, kind, apple_id, url):
    fields = providers.get("apple-music").parse_link(value)
    assert fields == {"apple_music_kind": kind, "apple_music_id": apple_id, "apple_music_url": url}


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://example.com/us/album/x/1097861387",
        "https://music.apple.com/us/unknown/x/1097861387",
        "https://music.apple.com/album/x/1097861387",  # no storefront
        "https://music.apple.com/us/album/x/abc",
        "https://music.apple.com.evil.example/us/album/x/1097861387",
    ],
)
def test_apple_music_rejects_everything_else(value):
    with pytest.raises(providers.ProviderError):
        providers.get("apple-music").parse_link(value)


def test_generic_link_route_attaches_an_apple_music_link(client, list_files):
    list_files["LISTEN_LIST_FILE"].write_text(
        json.dumps({"categories": [{"name": "Albums", "items": [{"title": "OK Computer"}]}]})
    )
    token = _csrf(client, "/api/listen")
    form = {"csrf_token": token, "category": "Albums", "item": "OK Computer"}
    bad = client.post(
        "/to-listen/link-provider/apple-music", data={**form, "link": "https://example.com/x"}
    )
    assert bad.status_code == 302
    assert (
        "apple_music_url"
        not in json.loads(list_files["LISTEN_LIST_FILE"].read_text())["categories"][0]["items"][0]
    )
    url = "https://music.apple.com/us/album/ok-computer/1097861387"
    client.post("/to-listen/link-provider/apple-music", data={**form, "link": url})
    item = json.loads(list_files["LISTEN_LIST_FILE"].read_text())["categories"][0]["items"][0]
    assert (item["apple_music_kind"], item["apple_music_url"]) == ("album", url)
    page = client.get("/to-listen").get_data(as_text=True)
    assert url in page and "Change Apple Music link" in page
    # Not offered where it does not apply, and unknown providers are 404.
    assert (
        client.post("/to-watch/link-provider/apple-music", data={**form, "link": url}).status_code
        == 404
    )
    assert (
        client.post("/to-listen/link-provider/nope", data={**form, "link": url}).status_code == 404
    )


SHOW = "umc.cmc.vtoh0mn0xn7t2c43o8i8ym4l"
EPISODE = "umc.cmc.25ocvpgvxe2ldgbd3ihq3krgy"


@pytest.mark.parametrize(
    ("value", "kind", "tv_id", "url"),
    [
        (
            f"https://tv.apple.com/us/show/ted-lasso/{SHOW}?l=en-US&ctx_brand=x",
            "show",
            SHOW,
            f"https://tv.apple.com/us/show/ted-lasso/{SHOW}",
        ),
        (
            f"tv.apple.com/gb/movie/dune/{EPISODE}",
            "movie",
            EPISODE,
            f"https://tv.apple.com/gb/movie/dune/{EPISODE}",
        ),
        (  # an episode keeps the show it belongs to
            f"https://tv.apple.com/us/episode/pilot/{EPISODE}?showId={SHOW}&x=1",
            "episode",
            EPISODE,
            f"https://tv.apple.com/us/episode/pilot/{EPISODE}?showId={SHOW}",
        ),
    ],
)
def test_apple_tv_links_are_parsed_and_canonicalised(value, kind, tv_id, url):
    fields = providers.get("apple-tv").parse_link(value)
    assert fields == {"apple_tv_kind": kind, "apple_tv_id": tv_id, "apple_tv_url": url}


@pytest.mark.parametrize(
    "value",
    [
        "",
        f"https://example.com/us/show/x/{SHOW}",
        f"https://tv.apple.com.evil.example/us/show/x/{SHOW}",
        f"https://tv.apple.com/show/x/{SHOW}",  # no storefront
        f"https://tv.apple.com/us/channel/x/{SHOW}",  # not a supported kind
        "https://tv.apple.com/us/show/x/not-an-id",
    ],
)
def test_apple_tv_rejects_everything_else(value):
    with pytest.raises(providers.ProviderError):
        providers.get("apple-tv").parse_link(value)


def test_apple_tv_links_attach_on_to_watch_only(client, list_files):
    list_files["WATCH_LIST_FILE"].write_text(
        json.dumps({"categories": [{"name": "TV Shows", "items": [{"title": "ted lasso"}]}]})
    )
    token = _csrf(client, "/api/watch")
    form = {"csrf_token": token, "category": "TV Shows", "item": "ted lasso"}
    url = f"https://tv.apple.com/us/show/ted-lasso/{SHOW}"
    assert (
        client.post("/to-watch/link-provider/apple-tv", data={**form, "link": url}).status_code
        == 302
    )
    item = json.loads(list_files["WATCH_LIST_FILE"].read_text())["categories"][0]["items"][0]
    assert (item["apple_tv_kind"], item["apple_tv_url"]) == ("show", url)
    page = client.get("/to-watch").get_data(as_text=True)
    assert url in page and "Change Apple TV link" in page
    assert "Apple Music" not in page  # Music links belong to To Listen
    assert (
        client.post("/to-listen/link-provider/apple-tv", data={**form, "link": url}).status_code
        == 404
    )
