"""Tests for the experimental AI super-resolution queue (no GPU or torch needed)."""

from __future__ import annotations

import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

import ecu  # noqa: E402
import media_library as ml  # noqa: E402
import upscale  # noqa: E402


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_targets_by_source_height():
    assert upscale.target_height(360) == 1080
    assert upscale.target_height(576) == 1080
    assert upscale.target_height(720) == 2160
    assert upscale.target_height(1080) == 2160
    assert upscale.target_height(2160) is None
    assert upscale.target_height(None) is None


def test_output_paths_mirror_the_library_under_a_hidden_folder(tmp_path):
    source = (
        tmp_path / "movies" / "Comedy" / "Hot Rod (2007)" / "Hot_Rod_2007_1080p_Akiva_Schaffer.mkv"
    )
    assert upscale.output_path(tmp_path, source, 2160) == (
        tmp_path / ".upscaled" / "movies" / "Comedy" / "Hot Rod (2007)"
        / "Hot_Rod_2007_1080p_Akiva_Schaffer.ai-4k.mkv"
    )  # fmt: skip
    assert upscale.output_path(tmp_path, source, 1080).name.endswith(".ai-1080p.mkv")
    assert upscale.upscaled_versions(tmp_path, source) == []
    _touch(upscale.output_path(tmp_path, source, 2160))
    assert upscale.upscaled_versions(tmp_path, source) == [
        ("AI 4K", upscale.output_path(tmp_path, source, 2160))
    ]


def _catalog(tmp_path):
    root = tmp_path / "watch"
    conn = ml.connect(tmp_path / "lib.sqlite")
    entries = [
        ("movies/Comedy/Hot Rod (2007)", "Hot Rod", "Comedy", '["comedy"]', 1080, 5280, 100),
        ("shows/Futurama (1999)", "Futurama", None, '["adult animated television series"]', 360, 1300, 50),
        ("movies/Drama/Network (1976)", "Network", "Drama", '["drama"]', 720, 7260, 300),
        ("movies/Drama/Old (1950)", "Old", "Drama", '["drama"]', 360, 6000, 10),
        ("movies/Sci-Fi/Big (2025)", "Big", "Sci-Fi", '["science fiction"]', 2160, 7000, 400),
    ]  # fmt: skip
    for rel, title, category, genres, height, duration, downloaded in entries:
        folder = root / rel
        video = _touch(folder / f"{title.replace(' ', '_')}.mkv")
        conn.execute(
            "INSERT INTO titles (folder, kind, title, genres, category, downloaded_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (str(folder), rel.split("/")[0], title, genres, category, downloaded),
        )
        conn.execute(
            "INSERT INTO files (path, folder, height, duration_s) VALUES (?, ?, ?, ?)",
            (str(video), str(folder), height, duration),
        )
    conn.commit()
    return root, conn


def test_queue_orders_lowest_resolution_then_newest_and_picks_models(tmp_path):
    root, conn = _catalog(tmp_path)

    jobs = upscale.plan_jobs(root, conn)

    assert [j.title for j in jobs] == ["Futurama", "Old", "Network", "Hot Rod"]
    assert [j.target_height for j in jobs] == [1080, 1080, 2160, 2160]
    assert jobs[0].model == upscale.ANIMATION_MODEL
    assert {j.model for j in jobs[1:]} == {upscale.LIVE_ACTION_MODEL}


def test_queue_skips_rendered_and_failed_videos(tmp_path):
    root, conn = _catalog(tmp_path)
    first, second, *_ = upscale.plan_jobs(root, conn)
    _touch(first.output)

    remaining = upscale.plan_jobs(root, conn, failed=[str(second.source)])

    assert [j.title for j in remaining] == ["Network", "Hot Rod"]


def test_disk_floor_accounts_for_the_estimated_output(tmp_path):
    root, conn = _catalog(tmp_path)
    job = upscale.plan_jobs(root, conn)[-1]  # Hot Rod, 88 min to 4K
    gib = 1024**3
    estimate = job.estimated_bytes
    assert 10 * gib < estimate < 15 * gib
    assert upscale.has_room(root, job, 1000, free=lambda _p: 1100 * gib)
    assert not upscale.has_room(root, job, 1000, free=lambda _p: 1005 * gib)


def test_output_sizes_keep_aspect_and_even_width():
    assert upscale.output_size(480, 360, 1080) == (1440, 1080)
    assert upscale.output_size(1920, 1040, 2160) == (3988, 2160)
    assert upscale.output_size(1920, 800, 2160) == (5184, 2160)


def test_chunks_are_video_only_and_the_join_encodes_audio_once(tmp_path):
    chunk = upscale.chunk_command(tmp_path / "000000.mkv.part", 1440, 1080, Fraction(30000, 1001))
    assert chunk[chunk.index("-framerate") + 1] == "30000/1001"
    assert chunk[chunk.index("-colorspace") + 1] == "bt709"
    assert chunk[chunk.index("-c:v") + 1] == "hevc_nvenc"
    assert "-c:a" not in chunk and "-map" not in chunk

    join = upscale.join_command(Path("/in.mp4"), tmp_path / "chunks.ffconcat", tmp_path / "o", 1.5)
    assert join[join.index("-itsoffset") + 1] == "1.500000"
    assert join[join.index("-f") + 1] == "concat"
    assert join[join.index("-c:v") + 1] == "copy" and join[join.index("-c:a") + 1] == "aac"
    assert "-hwaccel" not in upscale.decode_command(Path("/in.mp4"))


def test_decoder_seeks_roughly_and_keeps_source_timestamps():
    command = upscale.decode_command(Path("/in.mkv"), 42.5)
    assert command[command.index("-ss") + 1] == "42.500000"
    assert command.index("-ss") < command.index("-i")
    assert command[command.index("-enc_time_base:v") + 1] == "demux"
    assert "-ss" not in upscale.decode_command(Path("/in.mkv"))


def test_resume_point_is_found_by_frame_content():
    frames = [bytes([n]) * 4 for n in range(100)]
    tail = [upscale.frame_hash(f) for f in frames[46:50]]  # frames 46-49 already rendered

    assert upscale.locate_resume(iter(frames), tail, expected=48, slack=10) == 50
    # Not within the scanned window, or not unique (a still scene): give up.
    assert upscale.locate_resume(iter(frames), tail, expected=20, slack=10) is None
    still = [b"\0" * 4] * 100
    still_tail = [upscale.frame_hash(still[0])] * 4
    assert upscale.locate_resume(iter(still), still_tail, expected=50, slack=10) is None

    stream = __import__("io").BytesIO(b"".join(frames[:3]) + b"xx")
    assert list(upscale.read_frames(stream, 4)) == frames[:3]  # partial frame dropped


def test_chunks_are_whole_segments_sized_for_about_a_minute_of_work():
    rate = Fraction(24000, 1001)
    assert upscale.next_chunk_end(0, rate, None) == 12  # first chunk: speed unknown
    assert upscale.next_chunk_end(12, rate, 18.0) == 12 + 42  # 45 s of video, whole segments
    assert upscale.next_chunk_end(0, rate, 1.5) == 6  # slow 4K renders: one segment
    assert upscale.next_chunk_end(0, rate, 500.0) == 120  # capped
    # Boundaries come from absolute times, so chunk frame counts never drift.
    assert upscale.frame_index(12, rate) == 288 and upscale.frame_index(54, rate) == 1295


def _job(tmp_path, model=upscale.LIVE_ACTION_MODEL):
    return upscale.Job(
        tmp_path / "in.mkv", tmp_path / "out.ai-4k.mkv", 1080, 2160, model, 60.0, "T"
    )


def test_manifest_resumes_matching_chunks_and_discards_others(tmp_path):
    rate = Fraction(24, 1)
    job = _job(tmp_path)
    directory = upscale.chunk_dir(job.output)
    assert directory.name == "out.ai-4k.mkv.chunks"

    manifest = upscale.load_manifest(directory, job, rate)
    assert manifest["chunks"] == [] and manifest["rate"] == "24/1"
    manifest["chunks"].append({"file": "000000.mkv", "start_s": 0, "end_s": 12, "frames": 288})
    upscale.save_manifest(directory, manifest)
    (directory / "000000.mkv").write_bytes(b"x")

    assert upscale.load_manifest(directory, job, rate)["chunks"][0]["frames"] == 288
    listing = upscale.concat_list(directory, manifest, rate).read_text()
    assert "file '000000.mkv'\nduration 12.000000" in listing

    other = _job(tmp_path, model=upscale.ANIMATION_MODEL)
    assert upscale.load_manifest(directory, other, rate)["chunks"] == []
    assert not directory.exists()  # stale chunks removed

    upscale.save_manifest(directory, manifest)  # chunk file missing: start over
    assert upscale.load_manifest(directory, job, rate)["chunks"] == []


def test_interrupted_renders_resume_before_new_ones(tmp_path):
    root, conn = _catalog(tmp_path)
    jobs = upscale.plan_jobs(root, conn)
    hot_rod = next(j for j in jobs if j.title == "Hot Rod")
    upscale.chunk_dir(hot_rod.output).mkdir(parents=True)

    assert [j.title for j in upscale.plan_jobs(root, conn)][0] == "Hot Rod"
    requested = [str(jobs[1].source)]
    assert upscale.plan_jobs(root, conn, priority=requested)[0].title == jobs[1].title


def test_status_and_failures_round_trip(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    upscale.write_status({"state": "rendering", "frames": 10}, status)
    assert upscale.read_status(status)["frames"] == 10
    assert upscale.read_status(tmp_path / "missing.json") == {}

    failed = tmp_path / "failed.json"
    upscale.record_failure(Path("/a.mkv"), "ffmpeg failed", failed)
    assert upscale.load_failed(failed) == {"/a.mkv": "ffmpeg failed"}


def test_progress_reporter_writes_eta(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    monkeypatch.setattr(upscale, "STATUS_FILE", status)
    monkeypatch.setattr(
        upscale, "write_status", lambda data, path=status: path.write_text(json.dumps(data))
    )
    monkeypatch.setattr(upscale, "probe_video", lambda _p: (480, 360, Fraction(25), 0.0))
    job = upscale.Job(
        Path("/v.mkv"), Path("/o.mkv"), 360, 1080, upscale.ANIMATION_MODEL, 100.0, "V"
    )

    upscale.progress_reporter(job, {"queued": 3})(500, 50.0)

    data = json.loads(status.read_text())
    assert data["state"] == "rendering" and data["total_frames"] == 2500
    assert data["eta_seconds"] == 40 and data["target"] == "1080p" and data["queued"] == 3


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


def test_throttle_rests_to_keep_the_duty_cycle():
    fake = FakeClock()
    throttle = upscale.Throttle(
        0.25, gpu=lambda: 50.0, cpu=lambda: 50.0, sleep=fake.sleep, clock=fake.clock
    )

    throttle.after_batch(1.0)

    assert fake.slept == [3.0]  # 1 s of work, 3 s of rest: 25% duty


def test_throttle_pauses_when_hot_until_both_cool_below_the_margin():
    fake = FakeClock()
    gpu = iter([73.0, 70.0, 66.0, 65.0])
    events = []
    throttle = upscale.Throttle(
        1.0, 72, 78, gpu=lambda: next(gpu), cpu=lambda: 60.0, sleep=fake.sleep, clock=fake.clock
    )
    throttle.on_cooling = events.append

    throttle.after_batch(0.1)

    # 73 trips it; 70 and 66 are not yet 6 C below 72; 65 resumes.
    assert fake.slept == [30, 30]
    assert [e["gpu_c"] for e in events] == [70.0, 66.0]
    assert not throttle.cooling


def test_throttle_only_checks_temperatures_every_few_seconds():
    fake = FakeClock()
    reads = []
    throttle = upscale.Throttle(
        1.0,
        gpu=lambda: reads.append("gpu") or 40.0,
        cpu=lambda: 40.0,
        sleep=fake.sleep,
        clock=fake.clock,
        check_every=10,
    )
    for _ in range(5):
        throttle.after_batch(0.5)
        fake.now += 1
    assert reads == ["gpu"]


def test_steady_cpu_temperature_ignores_spikes(monkeypatch):
    monkeypatch.setattr(upscale.time, "sleep", lambda _s: None)
    readings = iter([62.0, 79.0, 63.0, 61.0, 80.0, 62.0])
    assert upscale.steady_cpu_temperature(read=lambda: next(readings)) == 63.0
    assert upscale.steady_cpu_temperature(read=lambda: None) is None


def test_cpu_temperature_reads_the_package_sensor(tmp_path):
    (tmp_path / "hwmon0").mkdir()
    (tmp_path / "hwmon0" / "name").write_text("nvme\n")
    (tmp_path / "hwmon5").mkdir()
    (tmp_path / "hwmon5" / "name").write_text("coretemp\n")
    (tmp_path / "hwmon5" / "temp1_input").write_text("64000\n")
    assert upscale.cpu_temperature(tmp_path) == 64.0
    assert upscale.cpu_temperature(tmp_path / "missing") is None


def test_new_requests_move_ahead_of_older_requests_and_background_jobs(tmp_path):
    root, conn = _catalog(tmp_path)
    priority = tmp_path / "priority.json"
    failed = tmp_path / "failed.json"
    hot_rod = root / "movies/Comedy/Hot Rod (2007)/Hot_Rod.mkv"
    network = root / "movies/Drama/Network (1976)/Network.mkv"
    upscale.record_failure(network, "ffmpeg failed", failed)

    assert upscale.request_render(hot_rod, priority, failed) == 0
    assert upscale.request_render(network, priority, failed) == 0
    assert upscale.load_priority(priority) == [str(network), str(hot_rod)]
    assert upscale.request_render(hot_rod, priority, failed) == 0  # no duplicate
    assert upscale.load_priority(priority) == [str(hot_rod), str(network)]
    assert upscale.load_failed(failed) == {}  # a requested failure is retried

    jobs = upscale.plan_jobs(root, conn, priority=upscale.load_priority(priority))
    assert [j.title for j in jobs] == ["Hot Rod", "Network", "Futurama", "Old"]

    upscale.drop_request(hot_rod, priority)
    assert upscale.load_priority(priority) == [str(network)]


def test_batch_requests_preempt_old_requests_and_keep_input_order(tmp_path):
    priority = tmp_path / "priority.json"
    failed = tmp_path / "failed.json"
    older = tmp_path / "older.mkv"
    first_episode = tmp_path / "S01E01.mkv"
    second_episode = tmp_path / "S01E02.mkv"
    priority.write_text(json.dumps([str(older)]))

    positions = upscale.request_renders([first_episode, second_episode], priority, failed)

    assert positions == [0, 1]
    assert upscale.load_priority(priority) == [
        str(first_episode),
        str(second_episode),
        str(older),
    ]


def test_requested_render_uses_interactive_preemptible_ecu_lease(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ecu, "_command", lambda: (["ecu"], {"TEST": "1"}))
    monkeypatch.setattr(
        ecu,
        "subprocess",
        type(
            "FakeSubprocess",
            (),
            {
                "run": staticmethod(
                    lambda command, **kwargs: calls.append((command, kwargs))
                    or subprocess.CompletedProcess(command, 0)
                )
            },
        ),
    )
    source = tmp_path / "episode.mkv"

    result = ecu.run_render("interactive", tmp_path, tmp_path / "catalog.db", 100, source)

    assert result == 0
    command, options = calls[0]
    assert command[:8] == [
        "ecu",
        "run",
        "--gpu",
        "quiet",
        "--priority",
        "interactive",
        "--preemptible",
        "--max-wait",
    ]
    assert command[8:11] == ["24h", "--wait", "--"]
    assert command[-2] == "render-one"
    assert command[-1] == str(source)
    assert options["env"] == {"TEST": "1"}


def test_render_state_reports_each_stage(tmp_path):
    root = tmp_path / "watch"
    source = _touch(root / "shows" / "Futurama (1999)" / "Futurama_S01E01_SD.mkv")

    def state(**kwargs):
        snapshot = {"status": {}, "priority": [], "failed": {}, **kwargs}
        return upscale.render_state(root, source, 480, **snapshot)

    assert upscale.render_state(root, source, 2160, {}, [], {}) is None
    assert state() == {"target": 1080, "label": "AI 1080p", "state": "none"}
    assert state(priority=["other", str(source)])["position"] == 1
    assert state(failed={str(source): "boom"})["reason"] == "boom"
    rendering = state(
        status={
            "state": "rendering",
            "source": str(source),
            "frames": 250,
            "total_frames": 1000,
            "eta_seconds": 4000,
        }
    )
    assert (rendering["state"], rendering["percent"]) == ("rendering", 25.0)
    _touch(upscale.output_path(root, source, 1080))
    assert state(priority=[str(source)])["state"] == "ready"


def test_idle_worker_wakes_when_a_render_is_requested(tmp_path, monkeypatch):
    priority = tmp_path / "priority.json"
    clock = [0.0]
    monkeypatch.setattr(upscale.time, "time", lambda: clock[0])

    def sleep(seconds):
        clock[0] += seconds
        if clock[0] >= 20:
            priority.write_text("[]")

    monkeypatch.setattr(upscale.time, "sleep", sleep)
    upscale.wait_for_request(3600, lambda: False, priority)
    assert clock[0] < 60


def test_cooling_ends_at_once_when_the_service_stops():
    fake = FakeClock()
    stop = []
    throttle = upscale.Throttle(
        1.0, 72, 78, gpu=lambda: 80.0, cpu=lambda: 60.0, sleep=lambda s: stop.append(s),
        clock=fake.clock, stopped=lambda: bool(stop),
    )  # fmt: skip
    throttle.guard()
    assert stop == [30]  # one pause, then it notices the stop instead of waiting to cool


def _fake_ecu_subprocess(monkeypatch, run):
    monkeypatch.setattr(ecu, "_command", lambda: (["ecu"], {}))
    monkeypatch.setattr(
        ecu,
        "subprocess",
        type("FakeSubprocess", (), {**vars(subprocess), "run": staticmethod(run)}),
    )


def test_retry_after_asks_central_ecu(monkeypatch):
    calls = []
    answer = {"retry_after_s": 95.0, "rate_per_hour": 30.0, "activity": "clockwork-stream"}

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(answer), "")

    _fake_ecu_subprocess(monkeypatch, run)
    assert ecu.retry_after() == answer
    assert calls == [["ecu", "retry-after", "--json"]]


def test_retry_after_waits_rather_than_spins_when_ecu_cannot_answer(monkeypatch):
    _fake_ecu_subprocess(
        monkeypatch, lambda command, **kwargs: subprocess.CompletedProcess(command, 2, "", "boom")
    )
    assert ecu.retry_after() == {"retry_after_s": ecu.FALLBACK_RETRY_S}


def test_retry_after_without_ecu_goes_on_at_once(monkeypatch):
    monkeypatch.setattr(ecu, "_command", lambda: None)
    assert ecu.retry_after() == {"retry_after_s": 0.0}


def test_requeued_render_sleeps_on_the_ecu_answer(tmp_path, monkeypatch):
    statuses, waits = [], []
    monkeypatch.setattr(
        upscale.ecu, "retry_after", lambda: {"retry_after_s": 120.0, "activity": "clockwork-stream"}
    )
    monkeypatch.setattr(upscale, "write_status", statuses.append)
    monkeypatch.setattr(upscale, "wait_for_request", lambda s, stop: waits.append(s))
    job = upscale.Job(tmp_path / "a.mkv", tmp_path / "out.mkv", 360, 1080, "m", 60.0, "A Show")

    upscale.pause_after_requeue({"queued": 3}, job, lambda: False)

    assert waits == [120.0]
    assert statuses[0]["state"] == "throttled"
    assert statuses[0]["next"] == "A Show"
    assert statuses[0]["activity"] == "clockwork-stream"
    assert statuses[0]["queued"] == 3


def test_requeued_render_goes_on_when_ecu_admits_now(tmp_path, monkeypatch):
    statuses, waits = [], []
    monkeypatch.setattr(upscale.ecu, "retry_after", lambda: {"retry_after_s": 0.0})
    monkeypatch.setattr(upscale, "write_status", statuses.append)
    monkeypatch.setattr(upscale, "wait_for_request", lambda s, stop: waits.append(s))
    job = upscale.Job(tmp_path / "a.mkv", tmp_path / "out.mkv", 360, 1080, "m", 60.0, "A Show")

    upscale.pause_after_requeue({}, job, lambda: False)

    assert (statuses, waits) == ([], [])
