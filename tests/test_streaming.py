"""Tests for on-demand HLS transcoding of library videos."""

from __future__ import annotations

import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

import streaming  # noqa: E402

HEVC_MKV = streaming.MediaInfo(60.0, ".mkv", "hevc", "yuv420p10le", "aac")


def test_plays_directly_only_for_browser_friendly_files():
    assert streaming.plays_directly(streaming.MediaInfo(60, ".mp4", "h264", "yuv420p", "aac"))
    assert not streaming.plays_directly(HEVC_MKV)
    assert not streaming.plays_directly(streaming.MediaInfo(60, ".mkv", "h264", "yuv420p", "aac"))
    assert not streaming.plays_directly(streaming.MediaInfo(60, ".mp4", "h264", "yuv420p", "ac3"))
    assert not streaming.plays_directly(
        streaming.MediaInfo(60, ".mp4", "h264", "yuv420p10le", "aac")
    )
    assert not streaming.plays_directly(streaming.MediaInfo(60, ".avi", "mpeg4", "yuv420p", "mp3"))


def test_vod_playlist_covers_the_whole_file():
    text = streaming.vod_playlist(14.5, lambda i: f"seg/{i}.ts")
    lines = text.splitlines()
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in lines
    assert [line for line in lines if line.startswith("seg/")] == [
        "seg/0.ts",
        "seg/1.ts",
        "seg/2.ts",
    ]
    assert "#EXTINF:2.500," in lines
    assert lines[-1] == "#EXT-X-ENDLIST"


def test_transcode_command_aligns_segments_across_restarts(tmp_path):
    nvenc = streaming.transcode_command(Path("/v.mkv"), tmp_path, 50, "h264_nvenc")
    assert nvenc[nvenc.index("-ss") + 1] == "300"
    assert nvenc[nvenc.index("-output_ts_offset") + 1] == "300"
    assert nvenc[nvenc.index("-start_number") + 1] == "50"
    assert "-forced-idr" in nvenc and "cuda" in nvenc
    assert nvenc[nvenc.index("-force_key_frames") + 1] == "expr:gte(t,n_forced*6)"
    cpu = streaming.transcode_command(Path("/v.mkv"), tmp_path, 0, "libx264")
    assert "libx264" in cpu and "cuda" not in cpu


class FakeProcess:
    """Stands in for ffmpeg: 'writes' segments as the test advances it."""

    def __init__(self, command):
        self.command = command
        self.directory = Path(command[command.index("-hls_segment_filename") + 1]).parent
        self.start = int(command[command.index("-start_number") + 1])
        self.produced = self.start - 1
        self.signals = []
        self.alive = True

    def produce(self, upto):
        for index in range(self.produced + 1, upto + 1):
            (self.directory / f"seg_{index}.ts").write_bytes(b"ts")
        self.produced = upto
        playlist = "".join(f"seg_{i}.ts\n" for i in range(self.start, upto + 1))
        (self.directory / f"run_{self.start}.m3u8").write_text(playlist)

    def poll(self):
        return None if self.alive else 0

    def send_signal(self, sig):
        self.signals.append(sig)

    def terminate(self):
        self.alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.alive = False


@pytest.fixture()
def harness(tmp_path):
    source = tmp_path / "show.mkv"
    source.write_bytes(b"video")
    processes = []
    clock = {"now": 1000.0}

    def popen(command, **_kwargs):
        process = FakeProcess(command)
        processes.append(process)
        return process

    def sleep(seconds):
        clock["now"] += seconds
        # ffmpeg keeps going while the request waits.
        if processes and processes[-1].alive:
            processes[-1].produce(processes[-1].produced + 1)

    manager = streaming.StreamManager(
        tmp_path / "cache",
        encoder=lambda: "h264_nvenc",
        probe=lambda _p: streaming.MediaInfo(600.0, ".mkv", "hevc", "yuv420p10le", "aac"),
        popen=popen,
        clock=lambda: clock["now"],
        sleep=sleep,
    )
    return manager, source, processes, clock


def test_first_request_starts_transcoding_and_waits_for_the_segment(harness):
    manager, source, processes, _clock = harness

    segment = manager.segment(source, 0)

    assert segment is not None and segment.name == "seg_0.ts"
    assert len(processes) == 1 and processes[0].start == 0


def test_sequential_requests_reuse_the_running_transcoder(harness):
    manager, source, processes, _clock = harness
    manager.segment(source, 0)
    processes[0].produce(3)

    assert manager.segment(source, 1).name == "seg_1.ts"
    assert manager.segment(source, 4).name == "seg_4.ts"
    assert len(processes) == 1


def test_seeking_far_ahead_restarts_there_and_keeps_finished_segments(harness):
    manager, source, processes, _clock = harness
    manager.segment(source, 0)
    processes[0].produce(2)

    assert manager.segment(source, 50).name == "seg_50.ts"
    assert len(processes) == 2 and processes[1].start == 50
    assert not processes[0].alive

    # Segments the first run finished stay usable without restarting again.
    assert manager.segment(source, 2).name == "seg_2.ts"
    assert len(processes) == 2


def test_out_of_range_segments_are_refused(harness):
    manager, source, _processes, _clock = harness
    assert manager.segment(source, 100) is None
    assert manager.segment(source, -1) is None


def test_transcoder_pauses_far_ahead_and_resumes_when_playback_catches_up(harness):
    manager, source, processes, _clock = harness
    manager.segment(source, 0)
    processes[0].produce(streaming.PAUSE_AHEAD + 1)

    manager.housekeeping()
    assert processes[0].signals == [signal.SIGSTOP]

    manager.segment(source, streaming.PAUSE_AHEAD - streaming.RESUME_AHEAD + 5)
    assert processes[0].signals[-1] == signal.SIGCONT


def test_idle_sessions_are_dropped_with_their_segments(harness):
    manager, source, processes, clock = harness
    manager.segment(source, 0)
    directory = next(iter(manager.sessions.values())).directory

    clock["now"] += streaming.IDLE_SECONDS + 1
    manager.housekeeping()

    assert manager.sessions == {}
    assert not directory.exists()
    assert not processes[0].alive


def test_failed_transcode_returns_none(harness):
    manager, source, processes, _clock = harness

    def dead_popen(command, **_kwargs):
        process = FakeProcess(command)
        process.alive = False
        processes.append(process)
        return process

    manager.popen = dead_popen
    assert manager.segment(source, 0) is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_ffmpeg_segments_line_up(tmp_path):
    source = tmp_path / "clip.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=24:duration=20",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=20",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],  # fmt: skip
        check=True,
    )
    manager = streaming.StreamManager(tmp_path / "cache", encoder=lambda: "libx264")

    late = manager.segment(source, 2)
    early = manager.segment(source, 0)

    assert late is not None and early is not None
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=start_time,duration",
            "-of",
            "default=nw=1:nk=1",
            str(late),
        ],  # fmt: skip
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert 12 <= float(probe[0]) < 14
    assert 5 < float(probe[1]) < 7
    for session in manager.sessions.values():
        session.stop()


def test_segments_from_an_earlier_seek_do_not_count_as_progress(harness):
    manager, source, processes, _clock = harness
    # Watched from 50 first, leaving 50..80 finished.
    manager.segment(source, 50)
    processes[-1].produce(80)
    # Then jumped back to the start and on to the middle.
    manager.segment(source, 9)
    assert processes[-1].start == 9 and processes[-1].signals == []

    assert manager.segment(source, 30).name == "seg_30.ts"
    assert processes[-1].start == 30  # restarted rather than waiting on run 9


def test_new_run_is_not_paused_by_segments_far_ahead(harness):
    manager, source, processes, _clock = harness
    manager.segment(source, 60)
    processes[-1].produce(99)
    manager.segment(source, 0)

    manager.housekeeping()

    assert signal.SIGSTOP not in processes[-1].signals


def test_quality_profiles_follow_the_source_resolution():
    hd = streaming.MediaInfo(60, ".mkv", "hevc", "yuv420p10le", "aac", height=1080)
    uhd = streaming.MediaInfo(60, ".mkv", "hevc", "yuv420p10le", "aac", height=2160)
    sd = streaming.MediaInfo(60, ".avi", "mpeg4", "yuv420p", "mp3", height=360)

    assert [p.name for p in streaming.available_profiles(hd)] == ["original", "720p", "480p"]
    assert [p.name for p in streaming.available_profiles(uhd)] == [
        "original",
        "1440p",
        "1080p",
        "720p",
        "480p",
    ]
    assert [p.name for p in streaming.available_profiles(sd)] == ["original"]
    original = streaming.PROFILES["original"]
    assert streaming.for_source(original, uhd).maxrate_kbps == 30000
    assert streaming.for_source(original, hd).maxrate_kbps == 12000
    assert streaming.for_source(streaming.PROFILES["720p"], uhd).maxrate_kbps == 4000
    assert streaming.profile("bogus").name == "original"


def test_transcode_command_scales_and_sets_bitrate_per_profile(tmp_path):
    command = streaming.transcode_command(
        Path("/v.mkv"), tmp_path, 0, "h264_nvenc", streaming.PROFILES["720p"]
    )
    assert command[command.index("-vf") + 1] == "format=yuv420p,scale=-2:'min(720,ih)'"
    assert command[command.index("-maxrate") + 1] == "4000k"
    assert command[command.index("-b:a") + 1] == "160k"
    original = streaming.transcode_command(Path("/v.mkv"), tmp_path, 0, "libx264")
    assert original[original.index("-vf") + 1] == "format=yuv420p"


def test_each_quality_gets_its_own_session_and_switching_stops_the_old_one(harness):
    manager, source, processes, _clock = harness
    manager.segment(source, 0, "original")
    manager.segment(source, 0, "480p")

    assert len(manager.sessions) == 2
    assert not processes[0].alive and processes[1].alive
    assert "scale=-2:'min(480,ih)'" in processes[1].command[processes[1].command.index("-vf") + 1]
