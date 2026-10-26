"""Experimental AI super-resolution of library videos, rendered ahead of time.

A long-running worker walks the catalog, lowest resolution first and newest
downloads first within that, and renders an upscaled copy of every video:
sources up to 576 lines become 1080p, 720p and 1080p sources become 4K. Each
frame goes through a Real-ESRGAN compact network on the GPU at 4x, then is
resized to the exact target. Animation uses realesr-animevideov3, everything
else realesr-general-x4v3.

Outputs are HEVC with the source audio as AAC, under a hidden
``<library>/.upscaled/`` tree that mirrors the library, so organizing and
naming are unaffected; the player streams them through web/streaming.py.

Rendering is chunked: one decoder streams the whole video, and the output is
encoded in chunks of whole 6-second streaming segments, sized for about a
minute of GPU work each, into ``<output>.chunks/`` with a manifest. Between
chunks the encoder is closed and the worker rests, so the GPU really idles
(an open NVENC session keeps it at ~120 W); a stop or restart loses at most
the chunk in progress, and the next run resumes from the manifest. The
finished chunks are joined and the audio encoded once from the source. The
worker stops starting new renders once free space on the library disk would
drop below CLOCKWORK_UPSCALE_MIN_FREE_GB, and checks again hourly.

The worker needs PyTorch, spandrel and the model weights, which live outside
the repo (see README). Everything except ``Renderer`` imports without them, so
the web app can read status and outputs.

    <venv>/bin/python web/upscale.py run      # render until done or disk floor
    python3 web/upscale.py queue               # show what would be rendered
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

try:
    from web import ecu
    from web import media_library as ml
except ModuleNotFoundError:
    import ecu
    import media_library as ml

UPSCALED_DIR = ".upscaled"
MODEL_DIR = Path(
    os.environ.get(
        "CLOCKWORK_UPSCALE_MODELS", Path.home() / ".cache" / "clockwork" / "upscale-models"
    )
)
STATUS_FILE = Path(
    os.environ.get(
        "CLOCKWORK_UPSCALE_STATUS",
        Path.home() / ".cache" / "clockwork" / "upscale-status.json",
    )
)
PAUSE_FILE = Path(
    os.environ.get(
        "CLOCKWORK_UPSCALE_PAUSE_FILE",
        Path.home() / ".cache" / "clockwork" / "upscale-pause",
    )
).expanduser()
PAUSED_FILE = Path(
    os.environ.get(
        "CLOCKWORK_UPSCALE_PAUSED_FILE",
        Path.home() / ".cache" / "clockwork" / "upscale-paused",
    )
).expanduser()
FAILED_FILE = Path(
    os.environ.get(
        "CLOCKWORK_UPSCALE_FAILED", ml.BASE_DIR / "config" / "media-library-upscale-failed.json"
    )
)
# Videos someone asked for from the player, rendered before the rest of the queue.
PRIORITY_FILE = Path(
    os.environ.get(
        "CLOCKWORK_UPSCALE_PRIORITY",
        ml.BASE_DIR / "config" / "media-library-upscale-priority.json",
    )
)
MIN_FREE_GB = float(os.environ.get("CLOCKWORK_UPSCALE_MIN_FREE_GB", "1000"))
SERVICE = "clockwork-upscale"

ANIMATION_MODEL = "realesr-animevideov3.pth"
LIVE_ACTION_MODEL = "realesr-general-x4v3.pth"
# Weak-denoise twin of the live-action model. Blending the two keeps film grain
# and skin texture that the full-strength model smooths into a waxy look.
LIVE_ACTION_WEAK_DENOISE = "realesr-general-wdn-x4v3.pth"
DENOISE = float(os.environ.get("CLOCKWORK_UPSCALE_DENOISE", "0.5"))
# Chunks are whole streaming segments (web/streaming.py SEGMENT_SECONDS), sized
# so one takes about CHUNK_WORK_SECONDS of GPU work, between these bounds.
SEGMENT_SECONDS = 6
CHUNK_WORK_SECONDS = float(os.environ.get("CLOCKWORK_UPSCALE_CHUNK_WORK_SECONDS", "60"))
MIN_CHUNK_SECONDS, MAX_CHUNK_SECONDS, FIRST_CHUNK_SECONDS = 6, 120, 12
MANIFEST = "manifest.json"
# Resuming: seek this far before the next frame, then find it by the content of
# the frames before it. Timestamp seeking alone lands a frame early or late
# depending on the container.
RESUME_LOOKBACK_SECONDS = 20
TAIL_FRAMES = 4
# Output bitrate assumptions for the disk check, in megabits per second.
ESTIMATED_MBPS = {1080: 6.0, 2160: 20.0}


@dataclass(frozen=True)
class Job:
    source: Path
    output: Path
    source_height: int
    target_height: int
    model: str
    duration: float
    title: str

    @property
    def estimated_bytes(self) -> int:
        return int(ESTIMATED_MBPS[self.target_height] * 1_000_000 / 8 * self.duration)


class YieldToRequest(Exception):
    """The background render saved its last chunk for an on-demand request."""


def target_height(source_height: int | None) -> int | None:
    """1080p for SD sources, 4K for 720p/1080p, nothing for sources already above."""
    if not source_height:
        return None
    if source_height <= 576:
        return 1080
    if source_height < 1200:
        return 2160
    return None


def output_path(root: Path, source: Path, height: int) -> Path:
    rel = source.relative_to(root)
    label = "4k" if height == 2160 else f"{height}p"
    return root / UPSCALED_DIR / rel.parent / f"{rel.stem}.ai-{label}.mkv"


def chunk_dir(output: Path) -> Path:
    return output.with_name(output.name + ".chunks")


def load_manifest(directory: Path, job: Job, rate: Fraction) -> dict[str, Any]:
    """The finished chunks of an interrupted render, or a fresh manifest.

    Chunks made with another model, target or frame rate are thrown away.
    """
    fresh = {
        "version": 1,
        "source": str(job.source),
        "target": job.target_height,
        "model": job.model,
        "rate": f"{rate.numerator}/{rate.denominator}",
        "chunks": [],
    }
    try:
        manifest = json.loads((directory / MANIFEST).read_text())
    except (OSError, ValueError):
        manifest = None
    if manifest and all(manifest.get(k) == v for k, v in fresh.items() if k != "chunks"):
        chunks = [c for c in manifest.get("chunks", []) if (directory / c["file"]).is_file()]
        if len(chunks) == len(manifest.get("chunks", [])):
            return manifest
    shutil.rmtree(directory, ignore_errors=True)
    return fresh


def save_manifest(directory: Path, manifest: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=1))
    tmp.replace(directory / MANIFEST)


def next_chunk_end(start_s: float, rate: Fraction, fps: float | None) -> float:
    """End (source seconds) of the next chunk: whole segments, ~CHUNK_WORK_SECONDS of work."""
    if fps:
        seconds = CHUNK_WORK_SECONDS * fps / float(rate)
        seconds = math.floor(seconds / SEGMENT_SECONDS) * SEGMENT_SECONDS
    else:
        seconds = FIRST_CHUNK_SECONDS
    return start_s + min(max(seconds, MIN_CHUNK_SECONDS), MAX_CHUNK_SECONDS)


def frame_index(seconds: float, rate: Fraction) -> int:
    return round(Fraction(seconds).limit_denominator(1000) * rate)


CONCAT_LIST = "chunks.ffconcat"


def concat_list(directory: Path, manifest: dict[str, Any], rate: Fraction) -> Path:
    """ffconcat list of the chunks with exact durations, so timestamps never drift.

    Rewritten after every chunk: the player streams a render in progress from
    it (web/streaming.py reads the clockwork-* comments for the audio source).
    """
    lines = [
        "ffconcat version 1.0",
        f"# clockwork-audio: {manifest['source']}",
        f"# clockwork-height: {manifest['target']}",
    ]
    if manifest["chunks"] and "end_s" in manifest["chunks"][-1]:
        # Chunks end on whole streaming segments; the summed frame durations can
        # be off by part of a frame, which would leave a tiny last segment.
        lines.append(f"# clockwork-seconds: {manifest['chunks'][-1]['end_s']:.6f}")
    for chunk in manifest["chunks"]:
        lines.append(f"file '{chunk['file']}'")
        lines.append(f"duration {chunk['frames'] / float(rate):.6f}")
    path = directory / CONCAT_LIST
    tmp = directory / (CONCAT_LIST + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)
    return path


def partial_render(root: Path, source: Path, height: int) -> tuple[Path, float] | None:
    """The concat list of a render in progress and how many seconds it covers."""
    try:
        directory = chunk_dir(output_path(root, source, height))
    except ValueError:
        return None
    listing = directory / CONCAT_LIST
    try:
        text = listing.read_text()
    except OSError:
        return None
    seconds = sum(
        float(line.split()[1]) for line in text.splitlines() if line.startswith("duration ")
    )
    for line in text.splitlines():
        if line.startswith("# clockwork-seconds: "):
            seconds = float(line.split(": ", 1)[1])
    return (listing, seconds) if seconds > 0 else None


def frame_hash(frame: bytes) -> str:
    return hashlib.blake2b(frame, digest_size=16).hexdigest()


def read_frames(stream, frame_bytes: int):
    """Raw frames from a decoder pipe, one bytes object each, until it ends."""
    while True:
        frame = stream.read(frame_bytes)
        if len(frame) < frame_bytes:
            return
        yield frame


def locate_resume(frames, tail: list[str], expected: int, slack: int) -> int | None:
    """How many frames to skip so the next one follows ``tail`` (hashes of the
    last frames already rendered), scanning up to ``expected + slack`` frames.

    None when the tail is not found exactly once (e.g. a still scene), so the
    caller falls back to counting frames from the start.
    """
    window: list[str] = []
    matches = []
    for index, frame in enumerate(frames):
        if index > expected + slack:
            break
        window = (window + [frame_hash(frame)])[-len(tail) :]
        if window == tail:
            matches.append(index + 1)
    return matches[0] if len(matches) == 1 else None


def upscaled_versions(root: Path, source: Path) -> list[tuple[str, Path]]:
    """Finished AI versions of a library video as (label, path), best first."""
    found = []
    for height in (2160, 1080):
        path = output_path(root, source, height)
        if path.is_file():
            found.append(("AI 4K" if height == 2160 else "AI 1080p", path))
    return found


def _is_animation(row: Any) -> bool:
    genres = " ".join(json.loads(row["genres"] or "[]")).lower()
    return row["category"] == "Animation" or "anim" in genres


def plan_jobs(
    root: Path, conn, failed: Iterable[str] = (), priority: Iterable[str] = ()
) -> list[Job]:
    """Every catalogued video still lacking its AI version, best value first.

    Videos in ``priority`` (requested on demand) come first, in request order.
    """
    failed = set(failed)
    requested = {path: index for index, path in enumerate(priority)}
    rows = conn.execute("""SELECT f.path, f.height, f.duration_s, t.title, t.genres, t.category,
                  t.downloaded_at
           FROM files f JOIN titles t ON t.folder = f.folder
           WHERE f.height IS NOT NULL AND f.duration_s IS NOT NULL""").fetchall()
    jobs = []
    for row in rows:
        source = Path(row["path"])
        target = target_height(row["height"])
        if target is None or str(source) in failed or not source.is_file():
            continue
        try:
            output = output_path(root, source, target)
        except ValueError:
            continue  # outside the library root
        if output.exists():
            continue
        jobs.append(
            (
                requested.get(str(source), len(requested)),
                # A render that was interrupted resumes before new ones start.
                0 if chunk_dir(output).is_dir() else 1,
                row["height"],
                -(row["downloaded_at"] or 0),
                str(source),
                Job(
                    source,
                    output,
                    row["height"],
                    target,
                    ANIMATION_MODEL if _is_animation(row) else LIVE_ACTION_MODEL,
                    row["duration_s"],
                    row["title"],
                ),
            )
        )
    return [job for *_key, job in sorted(jobs, key=lambda item: item[:5])]


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def has_room(root: Path, job: Job, min_free_gb: float = MIN_FREE_GB, free=free_bytes) -> bool:
    return free(root) - job.estimated_bytes > min_free_gb * 1024**3


def read_status(path: Path = STATUS_FILE) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_status(status: dict[str, Any], path: Path = STATUS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**status, "updated_at": time.time()}))
    tmp.replace(path)


def load_failed(path: Path | None = None) -> dict[str, str]:
    try:
        path = path or FAILED_FILE
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def record_failure(source: Path, reason: str, path: Path | None = None) -> None:
    path = path or FAILED_FILE
    failed = load_failed(path)
    failed[str(source)] = reason
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(failed, indent=2))


def load_priority(path: Path | None = None) -> list[str]:
    try:
        path = path or PRIORITY_FILE
        return [str(item) for item in json.loads(path.read_text())]
    except (OSError, ValueError, TypeError):
        return []


def _save_priority(items: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2))
    tmp.replace(path)


def request_render(source: Path, path: Path | None = None, failed_path: Path | None = None) -> int:
    """Move a video to the front of the render queue; returns its place (0 = next).

    A video that failed before is retried.
    """
    return request_renders([source], path, failed_path)[0]


def request_renders(
    sources: Iterable[Path], path: Path | None = None, failed_path: Path | None = None
) -> list[int]:
    """Promote requested videos ahead of older requests, preserving their input order."""
    path, failed_path = path or PRIORITY_FILE, failed_path or FAILED_FILE
    failed = load_failed(failed_path)
    requested = list(dict.fromkeys(map(str, sources)))
    failed_changed = False
    for source in requested:
        if failed.pop(source, None) is not None:
            failed_changed = True
    if failed_changed:
        failed_path.write_text(json.dumps(failed, indent=2))
    items = load_priority(path)
    promoted = requested + [item for item in items if item not in requested]
    if promoted != items:
        items = promoted
        _save_priority(items, path)
    return [items.index(source) for source in requested]


def drop_request(source: Path, path: Path | None = None) -> None:
    path = path or PRIORITY_FILE
    items = load_priority(path)
    if str(source) in items:
        items.remove(str(source))
        _save_priority(items, path)


def render_state(
    root: Path,
    source: Path,
    source_height: int | None,
    status: dict[str, Any] | None = None,
    priority: list[str] | None = None,
    failed: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Where a video's AI version stands, for the player and title pages.

    ``state`` is ready, rendering, queued (with ``position`` among on-demand
    requests), failed or none; None when the source is already 1440p or above.
    """
    target = target_height(source_height)
    if target is None:
        return None
    label = "AI 4K" if target == 2160 else f"AI {target}p"
    state: dict[str, Any] = {"target": target, "label": label, "state": "none"}
    try:
        rendered = output_path(root, source, target)
    except ValueError:
        return None  # outside the library root
    if rendered.is_file():
        return {**state, "state": "ready"}
    status = read_status() if status is None else status
    if status.get("source") == str(source) and status.get("state") in ("rendering", "cooling"):
        total = status.get("total_frames") or 0
        return {
            **state,
            "state": "rendering",
            "percent": round(100 * status.get("frames", 0) / total, 1) if total else None,
            "eta_seconds": status.get("eta_seconds"),
            "cooling": status.get("state") == "cooling",
        }
    priority = load_priority() if priority is None else priority
    if str(source) in priority:
        return {**state, "state": "queued", "position": priority.index(str(source))}
    failed = load_failed() if failed is None else failed
    if str(source) in failed:
        return {**state, "state": "failed", "reason": failed[str(source)]}
    return state


def probe_video(source: Path) -> tuple[int, int, Fraction, float]:
    """Width, height, frame rate and start time of the first video stream."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,avg_frame_rate,r_frame_rate,start_time",
            "-of",
            "json",
            str(source),
        ],  # fmt: skip
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    rate = stream.get("avg_frame_rate") or "0/1"
    if rate in ("0/0", "0/1"):
        rate = stream.get("r_frame_rate") or "24/1"
    try:
        start = float(stream.get("start_time") or 0)
    except ValueError:
        start = 0.0
    return int(stream["width"]), int(stream["height"]), Fraction(rate), start


def output_size(width: int, height: int, target: int) -> tuple[int, int]:
    out_w = round(width * target / height / 2) * 2
    return out_w, target


def decode_command(source: Path, seek: float = 0.0) -> list[str]:
    """Raw RGB frames; with ``seek``, from about that time (not frame-exact)."""
    return [
        # CPU decoding keeps up with the model and accepts streams NVDEC rejects.
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-threads", str(THREADS), *(["-ss", f"{seek:.6f}"] if seek else []), "-i", str(source),
        "-map", "0:v:0", "-fps_mode", "passthrough",
        # Keep the source time base: rounded to the frame rate, passthrough
        # timestamps collide and ffmpeg logs an error for every few frames.
        "-enc_time_base:v", "demux",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]  # fmt: skip


def chunk_command(output: Path, width: int, height: int, rate: Fraction) -> list[str]:
    """Encode one chunk of upscaled frames (video only) with its own NVENC session."""
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
        "-framerate", f"{rate.numerator}/{rate.denominator}", "-i", "-",
        # HD output: convert and tag as BT.709, which players assume for HD anyway.
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv",
        "-c:v", "hevc_nvenc", "-preset", "p5", "-cq", "22", "-tag:v", "hvc1",
        "-threads", str(THREADS), "-filter_threads", "1",
        "-f", "matroska", str(output),
    ]  # fmt: skip


def join_command(source: Path, chunks: Path, output: Path, start: float = 0.0) -> list[str]:
    """Join the chunks (copied) and encode the source audio once."""
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
        # Keep the source's video start so the audio stays in sync.
        "-itsoffset", f"{start:.6f}", "-f", "concat", "-safe", "0", "-i", str(chunks),
        "-i", str(source),
        "-map", "0:v:0", "-map", "1:a?", "-c:v", "copy",
        # Re-encoded rather than copied: copied packets keep edit-list offsets
        # (hidden pre-roll) that would shift the new video against the audio.
        # Encoded once for the whole film, not per chunk, so encoder padding
        # cannot add up to drift at chunk boundaries.
        "-c:a", "aac", "-b:a", "320k", "-map_metadata", "1",
        "-metadata:s:v:0", "comment=AI upscaled (Real-ESRGAN, clockwork experimental)",
        "-f", "matroska", str(output),
    ]  # fmt: skip


DUTY = float(os.environ.get("CLOCKWORK_UPSCALE_DUTY", "0.35"))
MAX_GPU_C = float(os.environ.get("CLOCKWORK_UPSCALE_MAX_GPU_C", "72"))
MAX_CPU_C = float(os.environ.get("CLOCKWORK_UPSCALE_MAX_CPU_C", "78"))
# Resume only once both have cooled this far below their limit. Idle here is
# about 60 C GPU / 65 C CPU, so a wider margin would rarely let it resume.
COOL_MARGIN_C = 6.0
THREADS = int(os.environ.get("CLOCKWORK_UPSCALE_THREADS", "2"))


def gpu_temperature() -> float | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return max(float(line) for line in result.stdout.split())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def cpu_temperature(hwmon: Path = Path("/sys/class/hwmon")) -> float | None:
    """CPU package temperature from the coretemp/k10temp hwmon driver."""
    for device in sorted(hwmon.glob("hwmon*")):
        try:
            if (device / "name").read_text().strip() in ("coretemp", "k10temp", "zenpower"):
                return int((device / "temp1_input").read_text()) / 1000
        except (OSError, ValueError):
            continue
    return None


def steady_cpu_temperature(
    samples: int = 6, interval: float = 0.5, read=cpu_temperature
) -> float | None:
    """Median over a few seconds: Intel package readings spike for a moment
    whenever a process wakes, including this check itself."""
    readings = []
    for index in range(samples):
        if index:
            time.sleep(interval)
        value = read()
        if value is not None:
            readings.append(value)
    if not readings:
        return None
    readings.sort()
    return readings[len(readings) // 2]


class Throttle:
    """Keeps the renderer cool and quiet: a duty cycle plus a temperature guard.

    After each batch it rests so that work takes only ``duty`` of wall time,
    and every ``check_every`` seconds it reads GPU and CPU temperatures; at or
    above a limit it pauses until both are COOL_MARGIN_C below their limits.
    """

    def __init__(
        self,
        duty: float = DUTY,
        max_gpu: float = MAX_GPU_C,
        max_cpu: float = MAX_CPU_C,
        *,
        gpu=gpu_temperature,
        cpu=steady_cpu_temperature,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        check_every: float = 10.0,
        stopped: Callable[[], bool] = lambda: False,
    ) -> None:
        self.duty = min(max(duty, 0.05), 1.0)
        self.max_gpu, self.max_cpu = max_gpu, max_cpu
        self.gpu, self.cpu = gpu, cpu
        self.sleep, self.clock = sleep, clock
        self.check_every = check_every
        self.stopped = stopped
        self.last_check = float("-inf")
        self.cooling = False
        self.temps: dict[str, float | None] = {}
        self.on_cooling: Callable[[dict[str, float | None]], None] = lambda _temps: None

    def _hot(self, margin: float) -> bool:
        gpu, cpu = self.gpu(), self.cpu()
        self.temps = {"gpu_c": gpu, "cpu_c": cpu}
        return (gpu is not None and gpu >= self.max_gpu - margin) or (
            cpu is not None and cpu >= self.max_cpu - margin
        )

    def rest(self, worked_seconds: float) -> None:
        """Rest so work takes only ``duty`` of wall time, then check temperatures."""
        if self.duty < 1:
            self.sleep(worked_seconds * (1 - self.duty) / self.duty)
        self.guard()

    def guard(self) -> float:
        """Pause while too hot (checked every few seconds); returns seconds spent cooling."""
        if self.clock() - self.last_check < self.check_every:
            return 0.0
        self.last_check = self.clock()
        if not self._hot(0):
            return 0.0
        began = self.clock()
        self.cooling = True
        while not self.stopped() and self._hot(COOL_MARGIN_C):
            self.on_cooling(self.temps)
            self.sleep(30)
        self.cooling = False
        self.last_check = self.clock()
        return self.clock() - began

    def after_batch(self, worked_seconds: float) -> None:
        self.rest(worked_seconds)


class Renderer:
    """Runs frames through a Real-ESRGAN model on the GPU. Needs torch and spandrel."""

    def __init__(self, model_dir: Path = MODEL_DIR) -> None:
        import spandrel
        import torch

        self.torch = torch
        self.loader = spandrel.ModelLoader()
        self.model_dir = model_dir
        self.models: dict[str, Any] = {}
        self.stop_event = threading.Event()
        self.yield_requested: Callable[[Job], bool] = lambda _job: False
        # Rests and cooling pauses end at once when the service is stopped.
        self.throttle = Throttle(sleep=self.stop_event.wait, stopped=self.stop_event.is_set)
        torch.set_num_threads(THREADS)

    def model(self, name: str):
        if name not in self.models:
            if name == LIVE_ACTION_MODEL and DENOISE < 1:
                loaded = self.loader.load_from_state_dict(self._blend(DENOISE))
            else:
                loaded = self.loader.load_from_file(str(self.model_dir / name))
            self.models[name] = loaded.model.cuda().half().eval()
        return self.models[name]

    def _blend(self, strength: float) -> dict[str, Any]:
        """Real-ESRGAN's denoise-strength interpolation of the two general models."""
        torch = self.torch

        def params(name: str) -> dict[str, Any]:
            state = torch.load(self.model_dir / name, map_location="cpu", weights_only=True)
            return state.get("params", state)

        strong, weak = params(LIVE_ACTION_MODEL), params(LIVE_ACTION_WEAK_DENOISE)
        return {k: strength * strong[k] + (1 - strength) * weak[k] for k in strong}

    @property
    def stopping(self) -> bool:
        return self.stop_event.is_set()

    def stop(self) -> None:
        self.stop_event.set()

    def render(self, job: Job, progress: Callable[[int, float], None]) -> None:
        width, height, rate, start = probe_video(job.source)
        out_w, out_h = output_size(width, height, job.target_height)
        directory = chunk_dir(job.output)
        manifest = load_manifest(directory, job, rate)
        save_manifest(directory, manifest)
        chunk_start = manifest["chunks"][-1]["end_s"] if manifest["chunks"] else 0.0
        done = sum(chunk["frames"] for chunk in manifest["chunks"])
        resumed, started = done, time.time()
        frame_bytes = width * height * 3
        tail = manifest["chunks"][-1].get("tail") if manifest["chunks"] else None
        decoder = self._open_decoder(job.source, done, rate, frame_bytes, tail)
        fps = None
        try:
            while True:
                chunk_end = next_chunk_end(chunk_start, rate, fps)
                want = frame_index(chunk_end, rate) - frame_index(chunk_start, rate)
                name = f"{len(manifest['chunks']):06d}.mkv"
                got, worked, tail = self._render_chunk(
                    decoder, directory / name, (width, height), (out_w, out_h), rate, job,
                    want, lambda count, before=done: progress(
                        before + count,
                        (before + count - resumed) / max(time.time() - started, 1e-6),
                    ),
                )  # fmt: skip
                if got:
                    done += got
                    end_s = chunk_end if got == want else chunk_start + got / float(rate)
                    manifest["chunks"].append(
                        {
                            "file": name,
                            "start_s": chunk_start,
                            "end_s": end_s,
                            "frames": got,
                            "tail": tail,
                        }
                    )
                    save_manifest(directory, manifest)
                    concat_list(directory, manifest, rate)
                    fps = got / max(worked, 1e-6)
                if got < want:
                    break  # end of the video
                chunk_start = chunk_end
                if self.yield_requested(job):
                    raise YieldToRequest
                # The encoder is closed: the GPU drops to idle while resting.
                self.throttle.rest(worked)
                if self.stopping:
                    raise InterruptedError("stopped")
            if decoder.wait() != 0:
                raise RuntimeError("ffmpeg decode failed")
            if not manifest["chunks"]:
                raise RuntimeError("no frames decoded")
            partial = job.output.with_name(job.output.name + ".part")
            joined = subprocess.run(
                join_command(job.source, concat_list(directory, manifest, rate), partial, start),
                check=False,
            )
            if joined.returncode != 0:
                partial.unlink(missing_ok=True)
                raise RuntimeError("ffmpeg join failed")
            partial.replace(job.output)
            shutil.rmtree(directory, ignore_errors=True)
        finally:
            if decoder.poll() is None:
                decoder.kill()

    def _open_decoder(
        self, source: Path, done: int, rate: Fraction, frame_bytes: int, tail: list[str] | None
    ) -> subprocess.Popen:
        """A decoder whose next frame is frame number ``done`` of the source, exactly."""

        def start(seek: float = 0.0) -> subprocess.Popen:
            return subprocess.Popen(decode_command(source, seek), stdout=subprocess.PIPE)

        def skipped(decoder: subprocess.Popen, count: int) -> subprocess.Popen:
            for _ in zip(range(count), read_frames(decoder.stdout, frame_bytes), strict=False):
                pass
            return decoder

        resume_at = done / float(rate)
        if done and tail and resume_at > RESUME_LOOKBACK_SECONDS:
            seek = resume_at - RESUME_LOOKBACK_SECONDS
            scan = start(seek)
            try:
                skip = locate_resume(
                    read_frames(scan.stdout, frame_bytes),
                    tail,
                    expected=round(RESUME_LOOKBACK_SECONDS * float(rate)),
                    slack=round(2 * float(rate)) + 8,
                )
            finally:
                scan.kill()
                scan.wait()
            if skip is not None:
                # The same seek decodes the same frames, so skipping is exact.
                return skipped(start(seek), skip)
            print(f"resume point not found by content in {source}; counting from the start")
        return skipped(start(), done)

    def _render_chunk(self, decoder, path, size, out_size, rate, job, want, progress):
        """Upscale up to ``want`` frames into one chunk file.

        Returns (frames, work seconds, hashes of the last source frames).
        """
        torch = self.torch
        (width, height), (out_w, out_h) = size, out_size
        frame_bytes = width * height * 3
        batch = 8 if height <= 576 else 1
        net = self.model(job.model)
        partial = path.with_name(path.name + ".part")
        encoder = subprocess.Popen(
            chunk_command(partial, out_w, out_h, rate), stdin=subprocess.PIPE
        )
        got, worked = 0, 0.0
        tail: list[str] = []
        try:
            with torch.inference_mode():
                while got < want and not self.stopping:
                    batch_started = time.time()
                    raw = decoder.stdout.read(frame_bytes * min(batch, want - got))
                    count = len(raw) // frame_bytes
                    if count == 0:
                        break
                    # Fingerprint the last frames of the chunk for an exact resume.
                    for index in range(count):
                        if got + index >= want - TAIL_FRAMES:
                            frame = raw[index * frame_bytes : (index + 1) * frame_bytes]
                            tail.append(frame_hash(frame))
                    frames = torch.frombuffer(
                        bytearray(raw[: count * frame_bytes]), dtype=torch.uint8
                    ).view(count, height, width, 3)
                    x = frames.cuda().permute(0, 3, 1, 2).half().div_(255)
                    y = net(x).clamp_(0, 1)
                    if y.shape[-2:] != (out_h, out_w):
                        y = torch.nn.functional.interpolate(
                            y.float(), size=(out_h, out_w), mode="bicubic", antialias=True
                        ).clamp_(0, 1)
                    out = y.mul(255).round_().to(torch.uint8).permute(0, 2, 3, 1).contiguous()
                    encoder.stdin.write(out.cpu().numpy().tobytes())
                    got += count
                    progress(got)
                    cooled = self.throttle.guard()
                    worked += time.time() - batch_started - cooled
            encoder.stdin.close()
            if self.stopping:
                raise InterruptedError("stopped")
            if encoder.wait() != 0:
                raise RuntimeError("ffmpeg encode failed")
            if got:
                partial.replace(path)
            return got, worked, tail[-TAIL_FRAMES:]
        finally:
            if encoder.poll() is None:
                encoder.kill()
            partial.unlink(missing_ok=True)


def progress_reporter(job: Job, base: dict[str, Any]) -> Callable[[int, float], None]:
    """Status-file writer for one render, at most every five seconds."""
    expected = job.duration * float(probe_video(job.source)[2])
    last = 0.0

    def progress(frames: int, fps: float) -> None:
        nonlocal last
        if time.time() - last < 5:
            return
        last = time.time()
        remaining = max(expected - frames, 0) / fps if fps else None
        write_status(
            {
                **base,
                "state": "rendering",
                "title": job.title,
                "file": job.source.name,
                "source": str(job.source),
                "target": "4K" if job.target_height == 2160 else f"{job.target_height}p",
                "model": job.model.removesuffix(".pth"),
                "frames": frames,
                "total_frames": round(expected),
                "fps": round(fps, 1),
                "eta_seconds": round(remaining) if remaining is not None else None,
            }
        )

    return progress


def wait_for_request(
    seconds: float, should_stop: Callable[[], bool], path: Path | None = None
) -> None:
    """Sleep up to ``seconds``, waking early when a render is requested."""
    path = path or PRIORITY_FILE

    def stamp() -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    before, deadline = stamp(), time.time() + seconds
    while time.time() < deadline and not should_stop():
        time.sleep(min(10.0, max(deadline - time.time(), 0.0)))
        if stamp() != before:
            return


def render_one(root: Path, db: Path, min_free_gb: float, source: Path) -> int:
    """Render one selected source under its ECU lease; 75 means replan the queue."""
    conn = ml.connect(db)
    jobs = plan_jobs(root, conn, load_failed(), load_priority())
    conn.close()
    job = next((candidate for candidate in jobs if candidate.source == source), None)
    if job is None:
        return 0
    if not has_room(root, job, min_free_gb):
        return ecu.REQUEUED

    renderer = Renderer()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: renderer.stop())
    signal.signal(signal.SIGINT, lambda _signum, _frame: renderer.stop())
    base = {"state": "idle", "queued": len(jobs), "finished": 0}
    progress = progress_reporter(job, {**base, "duty": renderer.throttle.duty})

    def cooling(
        temps: dict[str, float | None], job: Job = job, base: dict[str, Any] = base
    ) -> None:
        write_status(
            {
                **base,
                "state": "cooling",
                "title": job.title,
                "source": str(job.source),
                **temps,
            }
        )

    renderer.throttle.on_cooling = cooling
    renderer.yield_requested = lambda current: bool(
        PAUSE_FILE.exists()
        or ((requests := load_priority()) and requests[0] != str(current.source))
    )
    try:
        renderer.render(job, progress)
    except YieldToRequest:
        return ecu.REQUEUED
    except InterruptedError:
        return ecu.REQUEUED
    except Exception as exc:  # noqa: BLE001 - one bad file must not stop the queue
        print(f"upscale failed for {job.source}: {exc}", file=sys.stderr)
        record_failure(job.source, str(exc))
    return 0


def pause_after_requeue(base: dict[str, Any], job: Job, should_stop: Callable[[], bool]) -> None:
    """Sleep until the ECU will admit the next lease, waking for a render request."""
    answer = ecu.retry_after()
    delay = answer["retry_after_s"]
    if delay <= 0:
        return
    write_status(
        {
            **base,
            "state": "throttled",
            "next": job.title,
            "activity": answer.get("activity", ""),
            "retry_at": time.strftime("%H:%M", time.localtime(time.time() + delay)),
        }
    )
    wait_for_request(delay, should_stop)


def wait_while_paused(
    should_stop: Callable[[], bool],
    status: dict[str, Any],
    *,
    pause_file: Path = PAUSE_FILE,
    paused_file: Path = PAUSED_FILE,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Acknowledge a lease pause after rendering has yielded and wait for resume."""
    if not pause_file.exists():
        return False
    write_status({**status, "state": "paused"})
    paused_file.parent.mkdir(parents=True, exist_ok=True)
    paused_file.touch(mode=0o600, exist_ok=True)
    try:
        while pause_file.exists() and not should_stop():
            sleep(1.0)
    finally:
        paused_file.unlink(missing_ok=True)
    return not should_stop()


def run(root: Path, db: Path, min_free_gb: float, *, once: bool = False) -> int:
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stopping.set())
    signal.signal(signal.SIGINT, lambda _signum, _frame: stopping.set())
    finished = 0
    while not stopping.is_set():
        base = {"state": "idle", "finished": finished}
        if not wait_while_paused(stopping.is_set, base) and stopping.is_set():
            break
        conn = ml.connect(db)
        jobs = plan_jobs(root, conn, load_failed(), load_priority())
        conn.close()
        base = {"state": "idle", "queued": len(jobs), "finished": finished}
        base["free_gb"] = round(free_bytes(root) / 1024**3)
        base["min_free_gb"] = min_free_gb
        if not jobs:
            write_status({**base, "state": "done"})
            if once:
                return 0
            wait_for_request(3600, stopping.is_set)
            continue
        job = jobs[0]
        if not has_room(root, job, min_free_gb):
            write_status({**base, "state": "disk-floor", "next": job.title})
            if once:
                return 0
            wait_for_request(3600, stopping.is_set)
            continue
        priority = "interactive" if str(job.source) in load_priority() else "batch"
        try:
            result = ecu.run_render(priority, root, db, min_free_gb, job.source)
            if result == ecu.REQUEUED:
                if once:
                    break
                if wait_while_paused(stopping.is_set, base | {"next": job.title}):
                    continue
                pause_after_requeue(base, job, stopping.is_set)
                continue
            if result != 0:
                write_status({**base, "state": "ecu-error", "next": job.title})
                return result
            if str(job.source) not in load_failed():
                finished += 1
        except Exception as exc:  # noqa: BLE001 - a failed lease can be retried on restart
            print(f"ECU render lease failed for {job.source}: {exc}", file=sys.stderr)
            write_status({**base, "state": "ecu-error", "next": job.title})
            return 2
        drop_request(job.source)
        if once:
            break
    write_status({"state": "stopped", "finished": finished})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ml.LIBRARY_ROOT)
    parser.add_argument("--db", type=Path, default=ml.CATALOG_DB)
    parser.add_argument("--min-free-gb", type=float, default=MIN_FREE_GB)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="render until the queue is empty or the disk floor")
    run_parser.add_argument("--once", action="store_true", help="render one video and exit")
    render_parser = sub.add_parser("render-one", help=argparse.SUPPRESS)
    render_parser.add_argument("source", type=Path)
    sub.add_parser("queue", help="list the render queue")
    args = parser.parse_args(argv)
    if args.command == "queue":
        conn = ml.connect(args.db)
        jobs = plan_jobs(args.root, conn, load_failed(), load_priority())
        hours = sum(j.duration for j in jobs) / 3600
        size = sum(j.estimated_bytes for j in jobs) / 1024**4
        print(f"{len(jobs)} videos, {hours:.0f} h of video, about {size:.1f} TiB of output")
        for job in jobs[:25]:
            print(
                f"  {job.source_height}p -> {job.target_height}p  {job.model:28} {job.source.name}"
            )
        return 0
    if args.command == "render-one":
        return render_one(args.root, args.db, args.min_free_gb, args.source)
    return run(args.root, args.db, args.min_free_gb, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
