"""Tests for the watch-library organizer and metadata catalog."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

import media_library as ml  # noqa: E402

HD = ml.StreamInfo(duration_s=5400.0, width=1920, height=800, video_codec="hevc", channels=6)


def _prober(_path):
    return HD


@pytest.mark.parametrize(
    ("name", "title", "year", "kind"),
    [
        ("Napoleon Dynamite (2004) [1080p]", "Napoleon Dynamite", 2004, "movies"),
        ("Dune.Part.Two.2024.1080p.WEBRip.x264.AAC-[YTS.MX].mp4", "Dune Part Two", 2024, "movies"),
        ("Interstellar.2014.2014.1080p.BluRay.x264.YIFY.mp4", "Interstellar", 2014, "movies"),
        ("Se7en ( 1995 )", "Se7en", 1995, "movies"),
        ("Schindlers List 1993 (1080p x265 10bit Joy).mkv", "Schindlers List", 1993, "movies"),
        (
            "www.UIndex.org    -    Hoppers 2026 1080p MA WEB-DL DDP5 1 Atmos H 264-BYNDR",
            "Hoppers",
            2026,
            "movies",
        ),
        ("1917 (2019) [1080p]", "1917", 2019, "movies"),
        ("The.Big.Short.2015.DVDScr.XVID.AC3.HQ.Hive-CM8", "The Big Short", 2015, "movies"),
        (
            "Breaking Bad (2008) Season 1-5 S01-S05 (1080p BluRay x265 HEVC 10bit AAC 5.1 Silence)",
            "Breaking Bad",
            2008,
            "shows",
        ),
        (
            "Harry.Potter.Complete.Collection.2001-2011.1080p.BluRay.x264.DTS-ETRG",
            "Harry Potter",
            2001,
            "collection",
        ),
    ],
)
def test_parse_release_name(name, title, year, kind):
    parsed = ml.parse_release_name(name)
    assert (parsed.title, parsed.year, parsed.kind) == (title, year, kind)


def test_parse_release_name_editions_and_episodes():
    assert ml.parse_release_name("The Lord of the Rings EXTENDED (2001)").edition == "Extended"
    assert ml.parse_release_name("Terminator 2 Judgment Day DC (1991)").edition == "Director's Cut"
    episode = ml.parse_release_name("Its.Always.Sunny.S07E03.720p.BluRay.x264-DEMAND.mkv")
    assert (episode.kind, episode.season, episode.episode) == ("shows", 7, 3)
    assert ml.parse_release_name("Show 3x07 Name.avi").season == 3


def test_quality_label_counts_widescreen_crops():
    assert ml.quality_label(1920, 800) == "1080p"
    assert ml.quality_label(1280, 536) == "720p"
    assert ml.quality_label(3840, 1600) == "2160p"
    assert ml.quality_label(None, None) is None


def test_safe_component_strips_smb_unsafe_characters():
    assert ml.safe_component("Star Wars: Episode V") == "Star Wars - Episode V"
    assert ml.safe_component('What? "Now"') == "What Now"


def test_subtitle_language():
    assert ml.subtitle_language("2_English.srt") == "en"
    assert ml.subtitle_language("Movie.spa.srt") == "es"
    assert ml.subtitle_language("Movie.srt") is None


def test_best_candidate_prefers_matching_year_and_rejects_weak_matches():
    candidates = [
        {"id": 1, "title": "The Dark Knight Rises", "year": 2012, "kind": "movies"},
        {"id": 2, "title": "The Dark Knight", "year": 2008, "kind": "movies"},
    ]
    assert ml.best_candidate("Batman The Dark Knight", 2008, "movies", candidates)["id"] == 2
    assert ml.best_candidate("Totally Different", 2008, "movies", candidates) is None


class FakeProvider:
    name = "fake"

    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, title, year, kind):
        self.calls.append((title, year, kind))
        return self.results.get(title)


def _touch(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_organize_movie_folder_renames_video_and_subtitles_and_can_undo(tmp_path):
    root = tmp_path / "watch"
    entry = root / "Batman The Dark Knight (2008) [1080p]"
    _touch(entry / "The.Dark.Knight.2008.1080p.BrRip.x264.YIFY.mp4", 1000)
    _touch(entry / "Subs" / "2_English.srt")
    _touch(entry / "WWW.YIFY-TORRENTS.COM.jpg")
    provider = FakeProvider(
        {"Batman The Dark Knight": ml.Metadata(title="The Dark Knight", year=2008, kind="movies", sources=["fake"])}
    )

    plans = ml.plan_library(root, providers=[provider], prober=_prober, tracked=set())
    journal = tmp_path / "journal.jsonl"
    batch, done = ml.apply_plans(plans, journal)

    folder = root / "movies" / "The Dark Knight (2008)"
    assert done == 3
    assert (folder / "The Dark Knight (2008) [1080p].mp4").stat().st_size == 1000
    assert (folder / "The Dark Knight (2008) [1080p].en.srt").exists()
    assert (folder / "WWW.YIFY-TORRENTS.COM.jpg").exists()
    assert not (folder / "Subs").exists()

    assert ml.undo_batch(batch, journal) == 3
    assert (entry / "The.Dark.Knight.2008.1080p.BrRip.x264.YIFY.mp4").exists()
    assert (entry / "Subs" / "2_English.srt").exists()


def test_organize_single_file_movie_keeps_edition(tmp_path):
    root = tmp_path / "watch"
    _touch(root / "Goodfellas 1990 Remastered 1080p BluRay HEVC x265 5.1 BONE.mkv")

    plans = ml.plan_library(root, prober=_prober, tracked=set())
    ml.apply_plans(plans, tmp_path / "j.jsonl")

    assert (
        root / "movies" / "Goodfellas (1990)" / "Goodfellas (1990) {edition-Remastered} [1080p].mkv"
    ).exists()


def test_organize_show_files_episodes_into_seasons(tmp_path):
    root = tmp_path / "watch"
    entry = root / "Its_Always_Sunny_in_Philadelphia"
    _touch(entry / "Season 01" / "Its.Always.Sunny.S01E01.mkv")
    _touch(entry / "Season 01" / "Its.Always.Sunny.S01E01.srt")
    _touch(entry / "Its.Always.Sunny.S07.720p" / "Its.Always.Sunny.S07E02.720p.mkv")
    _touch(entry / "Extras" / "Blooper reel.mkv")
    provider = FakeProvider(
        {
            "Its Always Sunny in Philadelphia": ml.Metadata(
                title="It's Always Sunny in Philadelphia", year=2005, kind="shows", sources=["fake"]
            )
        }
    )

    plans = ml.plan_library(root, providers=[provider], prober=_prober, tracked=set())
    ml.apply_plans(plans, tmp_path / "j.jsonl")

    show = root / "shows" / "It's Always Sunny in Philadelphia (2005)"
    assert (show / "Season 01" / "It's Always Sunny in Philadelphia S01E01 [1080p].mkv").exists()
    assert (show / "Season 01" / "It's Always Sunny in Philadelphia S01E01 [1080p].srt").exists()
    assert (show / "Season 07" / "It's Always Sunny in Philadelphia S07E02 [1080p].mkv").exists()
    assert (show / "Extras" / "Blooper reel.mkv").exists()
    assert "without an SxxEyy code" in plans[0].note


def test_organize_skips_seeding_and_multi_part(tmp_path):
    root = tmp_path / "watch"
    _touch(root / "Gran Torino (2008) [1080p]" / "Gran.Torino.mp4")
    _touch(root / "Some Movie (1999)" / "CD1.avi", 100)
    _touch(root / "Some Movie (1999)" / "CD2.avi", 90)

    plans = {
        p.entry.name: p
        for p in ml.plan_library(root, prober=_prober, tracked={"Gran Torino (2008) [1080p]"})
    }

    assert plans["Gran Torino (2008) [1080p]"].skipped == "still seeding"
    assert "several full-length" in plans["Some Movie (1999)"].skipped
    _batch, done = ml.apply_plans(plans.values(), tmp_path / "j.jsonl")
    assert done == 0


def test_organize_splits_movie_collections_into_one_folder_per_title(tmp_path):
    root = tmp_path / "watch"
    pack = root / "Harry.Potter.Complete.Collection.2001-2011.1080p.BluRay.x264.DTS-ETRG"
    _touch(pack / "Chamber.Of.Secrets.2002.Extended.1080p" / "Chamber.Of.Secrets.2002.mkv", 1000)
    _touch(pack / "Chamber.Of.Secrets.2002.Extended.1080p" / "English.srt")
    _touch(pack / "Goblet.Of.Fire.2005.1080p" / "Goblet.Of.Fire.2005.mkv", 900)
    _touch(pack / "Goblet.Of.Fire.2005.1080p" / "sample.mkv", 5)
    duo = root / "Jay and Silent Bob Strike Back,Reboot 2001,2019 1080p BluRay HEVC x265 5.1 BONE"
    _touch(duo / "Jay and Silent Bob Reboot 2019 1080p BluRay HEVC x265 5.1 BONE.mkv", 800)
    _touch(duo / "Jay and Silent Bob Strike Back 2001 1080p BluRay HEVC x265 5.1 BONE.mkv", 800)
    provider = FakeProvider(
        {
            "Chamber Of Secrets": ml.Metadata(
                title="Harry Potter and the Chamber of Secrets", year=2002, sources=["fake"]
            ),
            "Goblet Of Fire": ml.Metadata(
                title="Harry Potter and the Goblet of Fire", year=2005, sources=["fake"]
            ),
        }
    )

    plans = ml.plan_library(root, providers=[provider], prober=_prober, tracked=set())
    batch, _done = ml.apply_plans(plans, tmp_path / "j.jsonl")

    movies = root / "movies"
    chamber = movies / "Harry Potter and the Chamber of Secrets (2002)"
    assert (chamber / "Harry Potter and the Chamber of Secrets (2002) {edition-Extended} [1080p].mkv").exists()
    assert (chamber / "Harry Potter and the Chamber of Secrets (2002) {edition-Extended} [1080p].en.srt").exists()
    goblet = movies / "Harry Potter and the Goblet of Fire (2005)"
    assert (goblet / "Harry Potter and the Goblet of Fire (2005) [1080p].mkv").exists()
    assert (movies / "Jay and Silent Bob Reboot (2019)" / "Jay and Silent Bob Reboot (2019) [1080p].mkv").exists()
    assert (
        movies / "Jay and Silent Bob Strike Back (2001)" / "Jay and Silent Bob Strike Back (2001) [1080p].mkv"
    ).exists()
    assert not duo.exists()
    assert (pack / "Goblet.Of.Fire.2005.1080p" / "sample.mkv").exists()

    ml.undo_batch(batch, tmp_path / "j.jsonl")
    assert (duo / "Jay and Silent Bob Reboot 2019 1080p BluRay HEVC x265 5.1 BONE.mkv").exists()


def test_show_pack_files_feature_films_as_movies(tmp_path):
    root = tmp_path / "watch"
    pack = root / "Futurama S01-S07 (1999-) + 4 Movies (2007-2009)"
    _touch(pack / "Futurama S01 (360p re-dvdrip)" / "Futurama S01E01 Space Pilot 3000.mp4")
    _touch(pack / "Futurama S01 (360p re-dvdrip)" / "Futurama S01E02 The Series Has Landed.mp4")
    _touch(
        pack / "Futurama S05 (360p re-dvdrip)" / "Futurama S05M01 Bender's Big Score (2007 360p re-blurip).mp4"
    )
    provider = FakeProvider(
        {
            "Futurama": ml.Metadata(title="Futurama", year=1999, kind="shows", sources=["fake"]),
            "Futurama Bender's Big Score": ml.Metadata(
                title="Futurama - Bender's Big Score", year=2007, sources=["fake"]
            ),
        }
    )

    ml.apply_plans(
        ml.plan_library(root, providers=[provider], prober=_prober, tracked=set()), tmp_path / "j"
    )

    assert (root / "shows" / "Futurama (1999)" / "Season 01" / "Futurama S01E02 [1080p].mp4").exists()
    film = root / "movies" / "Futurama - Bender's Big Score (2007)"
    assert (film / "Futurama - Bender's Big Score (2007) [1080p].mp4").exists()
    assert not (root / "shows" / "Futurama (1999)" / "Futurama S05 (360p re-dvdrip)").exists()


def test_apply_never_overwrites_existing_target(tmp_path):
    root = tmp_path / "watch"
    _touch(root / "Hot Rod 2007 1080p BluRay.mkv")
    _touch(root / "movies" / "Hot Rod (2007)" / "Hot Rod (2007) [1080p].mkv", 99)

    _batch, done = ml.apply_plans(ml.plan_library(root, prober=_prober, tracked=set()), tmp_path / "j")

    assert done == 0
    assert (root / "Hot Rod 2007 1080p BluRay.mkv").exists()


def test_tracked_names_matches_torrents_in_library_aliases(tmp_path):
    payload = {
        "ok": True,
        "torrents": [
            {"name": "Gran Torino (2008) [1080p]", "downloadDir": "/srv/snowbridge/share/watch/"},
            {"name": "Elsewhere", "downloadDir": "/srv/snowbridge/share/torrents"},
        ],
    }
    names, error = ml.tracked_names(tmp_path, fetch=lambda _url: payload)
    assert error is None
    assert names == {"Gran Torino (2008) [1080p]"}

    def boom(_url):
        raise OSError("refused")

    assert ml.tracked_names(tmp_path, fetch=boom)[1].startswith("magneto unreachable")


def test_metadata_merge_fills_gaps_only():
    first = ml.Metadata(title="Seven", directors=["David Fincher"], sources=["wikidata"])
    first.merge(ml.Metadata(title="Se7en", plot="Two detectives.", rating=8.4, sources=["tmdb"]))
    assert first.title == "Seven"
    assert first.plot == "Two detectives."
    assert first.sources == ["wikidata", "tmdb"]


def test_wikidata_provider_parses_search_and_details():
    responses = iter(
        [
            {
                "results": {
                    "bindings": [
                        {
                            "item": {"value": "http://www.wikidata.org/entity/Q190908"},
                            "itemLabel": {"value": "Seven"},
                            "type": {"value": "http://www.wikidata.org/entity/Q11424"},
                            "year": {"value": "1995"},
                        }
                    ]
                }
            },
            {
                "results": {
                    "bindings": [
                        {"p": {"value": "director"}, "valueLabel": {"value": "David Fincher"}},
                        {"p": {"value": "cast"}, "valueLabel": {"value": "Brad Pitt"}},
                        {"p": {"value": "genre"}, "valueLabel": {"value": "thriller film"}},
                        {"p": {"value": "imdb"}, "value": {"value": "tt0114369"}},
                        {"p": {"value": "duration"}, "value": {"value": "127"}},
                    ]
                }
            },
        ]
    )
    provider = ml.WikidataProvider(fetch=lambda _url: next(responses))

    meta = provider.search("Se7en", 1995, "movies")

    assert meta.title == "Seven"
    assert meta.wikidata_id == "Q190908"
    assert meta.directors == ["David Fincher"]
    assert meta.genres == ["thriller"]
    assert meta.imdb_id == "tt0114369"
    assert meta.runtime_min == 127


def test_tmdb_provider_uses_credits():
    def fetch(url):
        if "/search/movie" in url:
            return {"results": [{"id": 807, "title": "Se7en", "release_date": "1995-09-22"}]}
        return {
            "overview": "Two detectives hunt a killer.",
            "vote_average": 8.4,
            "runtime": 127,
            "genres": [{"name": "Crime"}],
            "external_ids": {"imdb_id": "tt0114369"},
            "credits": {
                "crew": [{"job": "Director", "name": "David Fincher"}],
                "cast": [{"name": "Morgan Freeman"}],
            },
        }

    meta = ml.TmdbProvider("key", fetch=fetch).search("Se7en", 1995, "movies")

    assert meta.tmdb_id == "807"
    assert meta.directors == ["David Fincher"]
    assert meta.plot == "Two detectives hunt a killer."


def test_scan_and_search_catalog(tmp_path):
    root = tmp_path / "watch"
    _touch(root / "movies" / "Seven (1995)" / "Seven (1995) [1080p].mkv")
    _touch(root / "shows" / "Breaking Bad (2008)" / "Season 01" / "Breaking Bad S01E01 [720p].mkv")
    provider = FakeProvider(
        {
            "Seven": ml.Metadata(
                title="Seven",
                year=1995,
                directors=["David Fincher"],
                genres=["thriller"],
                sources=["fake"],
            ),
            "Breaking Bad": ml.Metadata(directors=["Vince Gilligan"], genres=["crime"], sources=["fake"]),
        }
    )
    conn = ml.connect(tmp_path / "lib.sqlite")

    stats = ml.scan_library(root, conn, providers=[provider], prober=_prober)
    assert stats == {"titles": 2, "probed": 2, "looked_up": 2, "removed": 0}

    again = ml.scan_library(root, conn, providers=[provider], prober=_prober)
    assert again["probed"] == 0 and again["looked_up"] == 0

    fincher = ml.search_catalog(conn, director="fincher")
    assert [r["title"] for r in fincher] == ["Seven"]
    assert fincher[0]["quality"] == "1080p"
    assert fincher[0]["directors"] == ["David Fincher"]
    assert [r["title"] for r in ml.search_catalog(conn, kind="shows")] == ["Breaking Bad"]
    assert [r["title"] for r in ml.search_catalog(conn, year_from=2000)] == ["Breaking Bad"]
    assert [r["title"] for r in ml.search_catalog(conn, q="crime")] == ["Breaking Bad"]
    assert ml.catalog_facets(conn)["directors"] == ["David Fincher", "Vince Gilligan"]

    (root / "movies" / "Seven (1995)" / "Seven (1995) [1080p].mkv").unlink()
    (root / "movies" / "Seven (1995)").rmdir()
    assert ml.scan_library(root, conn, prober=_prober)["removed"] == 1


def test_journal_lines_are_json(tmp_path):
    root = tmp_path / "watch"
    _touch(root / "Hot Rod 2007 1080p BluRay.mkv")
    journal = tmp_path / "j.jsonl"
    batch, _ = ml.apply_plans(ml.plan_library(root, prober=_prober, tracked=set()), journal)
    entry = json.loads(journal.read_text().splitlines()[0])
    assert entry["batch"] == batch
    assert entry["dst"].endswith("Hot Rod (2007) [1080p].mkv")


def test_lookup_cache_serves_parsed_and_canonical_names(tmp_path):
    conn = ml.connect(tmp_path / "lib.sqlite")
    provider = FakeProvider(
        {"Se7en": ml.Metadata(title="Seven", year=1995, directors=["David Fincher"], sources=["fake"])}
    )

    first = ml.lookup_metadata("Se7en", 1995, "movies", [provider], conn)
    again = ml.lookup_metadata("Se7en", 1995, "movies", [provider], conn)
    canonical = ml.lookup_metadata("Seven", 1995, "movies", [provider], conn)

    assert provider.calls == [("Se7en", 1995, "movies")]
    assert first.directors == again.directors == canonical.directors == ["David Fincher"]


def test_subtitle_language_codes_only_near_the_end():
    assert ml.subtitle_language("baq.srt") == "eu"
    assert ml.subtitle_language("Simplified.chi.srt") == "zh"
    assert ml.subtitle_language("Movie.en.srt") == "en"
    assert ml.subtitle_language("Cat.People.1942.srt") is None
    assert ml.subtitle_language("It.2017.srt") is None


def test_parse_long_episode_form_and_extras():
    double = ml.parse_release_name("Its Always Sunny Season 05 Episode 13 & 14 - A Very Sunny Christmas.avi")
    assert (double.season, double.episode, double.episode_end) == (5, 13, 14)
    extra = ml.parse_release_name("Its Always Sunny Season 01 Extra 01 - Charlie Has Cancer.mkv")
    assert extra.episode is None


def test_release_year_wins_over_metadata_year(tmp_path):
    root = tmp_path / "watch"
    _touch(root / "Primate 2026 1080p AMZN WEB-DL" / "Primate.mkv")
    provider = FakeProvider(
        {"Primate": ml.Metadata(title="Primate", year=2025, kind="movies", sources=["fake"])}
    )

    plan = ml.plan_library(root, providers=[provider], prober=_prober, tracked=set())[0]

    assert plan.moves[0].dst.name == "Primate (2026)"


def test_wikidata_label_falling_back_to_qid_is_ignored():
    rows = {
        "results": {
            "bindings": [
                {
                    "item": {"value": "http://www.wikidata.org/entity/Q134773"},
                    "itemLabel": {"value": "Q134773"},
                    "type": {"value": "http://www.wikidata.org/entity/Q11424"},
                    "year": {"value": "1994"},
                }
            ]
        }
    }
    assert ml.WikidataProvider(fetch=lambda _url: rows).search("Forrest Gump", 1994, "movies") is None


def test_apply_continues_after_a_failed_rename(tmp_path, monkeypatch, capsys):
    root = tmp_path / "watch"
    _touch(root / "Hot Rod 2007 1080p BluRay.mkv")
    _touch(root / "Hamnet.2025.1080p.WEB.h264-ETHEL.mkv")
    real_rename = ml.os.rename

    def flaky(src, dst):
        if "Hamnet" in str(src):
            raise PermissionError("denied")
        real_rename(src, dst)

    monkeypatch.setattr(ml.os, "rename", flaky)
    _batch, done = ml.apply_plans(ml.plan_library(root, prober=_prober, tracked=set()), tmp_path / "j")

    assert done == 1
    assert (root / "movies" / "Hot Rod (2007)" / "Hot Rod (2007) [1080p].mkv").exists()
    assert "rename failed" in capsys.readouterr().err
