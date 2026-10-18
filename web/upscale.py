"""Experimental AI super-resolution of library videos, rendered ahead of time.

A long-running worker walks the catalog, lowest resolution first and newest
downloads first within that, and renders an upscaled copy of every video:
sources up to 576 lines become 1080p, 720p and 1080p sources become 4K. Each
frame goes through a Real-ESRGAN compact network on the GPU at 4x, then is
resized to the exact target. Animation uses realesr-animevideov3, everything
else realesr-general-x4v3.

Outputs are HEVC with the source audio as AAC, under a hidden
``<library>/.upscaled/`` tree that mirrors the library, so organizing and
naming are unaffected; the player streams them through web/streaming.py. The
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
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

try:
    from web import media_library as ml
except ModuleNotFoundError:
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
FAILED_FILE = Path(
    os.environ.get(
        "CLOCKWORK_UPSCALE_FAILED", ml.BASE_DIR / "config" / "media-library-upscale-failed.json"
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


def plan_jobs(root: Path, conn, failed: Iterable[str] = ()) -> list[Job]:
    """Every catalogued video still lacking its AI version, best value first."""
    failed = set(failed)
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
    return [job for *_key, job in sorted(jobs, key=lambda item: item[:3])]


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


def load_failed(path: Path = FAILED_FILE) -> dict[str, str]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def record_failure(source: Path, reason: str, path: Path = FAILED_FILE) -> None:
    failed = load_failed(path)
    failed[str(source)] = reason
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(failed, indent=2))


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


def decode_command(source: Path) -> list[str]:
    return [
        # CPU decoding keeps up with the model and accepts streams NVDEC rejects.
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(source),
        "-map", "0:v:0", "-fps_mode", "passthrough",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]  # fmt: skip


def encode_command(
    source: Path, output: Path, width: int, height: int, rate: Fraction, start: float = 0.0
) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
        "-framerate", f"{rate.numerator}/{rate.denominator}",
        # Keep the source's video start so the copied audio stays in sync.
        "-itsoffset", f"{start:.6f}", "-i", "-",
        "-i", str(source),
        "-map", "0:v:0", "-map", "1:a?",
        # HD output: convert and tag as BT.709, which players assume for HD anyway.
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv",
        "-c:v", "hevc_nvenc", "-preset", "p5", "-cq", "22", "-tag:v", "hvc1",
        # Re-encoded rather than copied: copied packets keep edit-list offsets
        # (hidden pre-roll) that would shift the new video against the audio.
        "-c:a", "aac", "-b:a", "320k", "-map_metadata", "1",
        "-metadata:s:v:0", "comment=AI upscaled (Real-ESRGAN, clockwork experimental)",
        "-f", "matroska", str(output),
    ]  # fmt: skip


class Renderer:
    """Runs frames through a Real-ESRGAN model on the GPU. Needs torch and spandrel."""

    def __init__(self, model_dir: Path = MODEL_DIR) -> None:
        import spandrel
        import torch

        self.torch = torch
        self.loader = spandrel.ModelLoader()
        self.model_dir = model_dir
        self.models: dict[str, Any] = {}
        self.stopping = False

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

    def render(self, job: Job, progress: Callable[[int, float], None]) -> None:
        torch = self.torch
        width, height, rate, start = probe_video(job.source)
        out_w, out_h = output_size(width, height, job.target_height)
        frame_bytes = width * height * 3
        batch = 8 if height <= 576 else 1
        net = self.model(job.model)
        job.output.parent.mkdir(parents=True, exist_ok=True)
        partial = job.output.with_name(job.output.name + ".part")
        decoder = subprocess.Popen(decode_command(job.source), stdout=subprocess.PIPE)
        encoder = subprocess.Popen(
            encode_command(job.source, partial, out_w, out_h, rate, start), stdin=subprocess.PIPE
        )
        done = 0
        started = time.time()
        try:
            with torch.inference_mode():
                while not self.stopping:
                    raw = decoder.stdout.read(frame_bytes * batch)
                    count = len(raw) // frame_bytes
                    if count == 0:
                        break
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
                    done += count
                    progress(done, done / max(time.time() - started, 1e-6))
            encoder.stdin.close()
            if self.stopping:
                raise InterruptedError("stopped")
            if encoder.wait() != 0 or decoder.wait() != 0:
                raise RuntimeError("ffmpeg failed")
            partial.replace(job.output)
        finally:
            for process in (decoder, encoder):
                if process.poll() is None:
                    process.kill()
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
                "target": "4K" if job.target_height == 2160 else f"{job.target_height}p",
                "model": job.model.removesuffix(".pth"),
                "frames": frames,
                "total_frames": round(expected),
                "fps": round(fps, 1),
                "eta_seconds": round(remaining) if remaining is not None else None,
            }
        )

    return progress


def run(root: Path, db: Path, min_free_gb: float, *, once: bool = False) -> int:
    renderer = Renderer()

    def stop(_signum, _frame):
        renderer.stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    finished = 0
    while not renderer.stopping:
        conn = ml.connect(db)
        jobs = plan_jobs(root, conn, load_failed())
        conn.close()
        base = {"state": "idle", "queued": len(jobs), "finished": finished}
        base["free_gb"] = round(free_bytes(root) / 1024**3)
        base["min_free_gb"] = min_free_gb
        if not jobs:
            write_status({**base, "state": "done"})
            if once:
                return 0
            time.sleep(3600)
            continue
        job = jobs[0]
        if not has_room(root, job, min_free_gb):
            write_status({**base, "state": "disk-floor", "next": job.title})
            if once:
                return 0
            time.sleep(3600)
            continue
        progress = progress_reporter(job, base)
        try:
            renderer.render(job, progress)
            finished += 1
        except InterruptedError:
            break
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the queue
            print(f"upscale failed for {job.source}: {exc}", file=sys.stderr)
            record_failure(job.source, str(exc))
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
    sub.add_parser("queue", help="list the render queue")
    args = parser.parse_args(argv)
    if args.command == "queue":
        conn = ml.connect(args.db)
        jobs = plan_jobs(args.root, conn, load_failed())
        hours = sum(j.duration for j in jobs) / 3600
        size = sum(j.estimated_bytes for j in jobs) / 1024**4
        print(f"{len(jobs)} videos, {hours:.0f} h of video, about {size:.1f} TiB of output")
        for job in jobs[:25]:
            print(
                f"  {job.source_height}p -> {job.target_height}p  {job.model:28} {job.source.name}"
            )
        return 0
    return run(args.root, args.db, args.min_free_gb, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
