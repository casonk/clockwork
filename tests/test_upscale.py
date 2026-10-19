"""Tests for the experimental AI super-resolution queue (no GPU or torch needed)."""

from __future__ import annotations

import json
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

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


def test_encode_command_tags_bt709_and_reencodes_audio(tmp_path):
    command = upscale.encode_command(
        Path("/in.mp4"), tmp_path / "out.mkv", 1440, 1080, Fraction(30000, 1001), 1.5
    )
    assert command[command.index("-framerate") + 1] == "30000/1001"
    assert command[command.index("-itsoffset") + 1] == "1.500000"
    assert command[command.index("-colorspace") + 1] == "bt709"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-c:v") + 1] == "hevc_nvenc"
    assert "-hwaccel" not in upscale.decode_command(Path("/in.mp4"))


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


def test_requested_videos_jump_the_queue_in_request_order(tmp_path):
    root, conn = _catalog(tmp_path)
    priority = tmp_path / "priority.json"
    failed = tmp_path / "failed.json"
    hot_rod = root / "movies/Comedy/Hot Rod (2007)/Hot_Rod.mkv"
    network = root / "movies/Drama/Network (1976)/Network.mkv"
    upscale.record_failure(network, "ffmpeg failed", failed)

    assert upscale.request_render(hot_rod, priority, failed) == 0
    assert upscale.request_render(network, priority, failed) == 1
    assert upscale.request_render(hot_rod, priority, failed) == 0  # no duplicate
    assert upscale.load_failed(failed) == {}  # a requested failure is retried

    jobs = upscale.plan_jobs(root, conn, priority=upscale.load_priority(priority))
    assert [j.title for j in jobs] == ["Hot Rod", "Network", "Futurama", "Old"]

    upscale.drop_request(hot_rod, priority)
    assert upscale.load_priority(priority) == [str(network)]


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
