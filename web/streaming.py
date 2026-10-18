"""Stream library videos the browser cannot play directly, as HLS transcoded on demand.

Browsers play MP4 with H.264 and AAC, not MKV, HEVC, 10-bit video, AC3/DTS
audio or AVI. For those, the player loads a VOD playlist covering the whole
file in fixed SEGMENT_SECONDS segments, and segments are transcoded when
asked for: ffmpeg starts at the requested segment's time and keeps going, so
playback that runs on finds its next segments ready. Seeking beyond what is
ready restarts ffmpeg there. Forced IDR frames every SEGMENT_SECONDS and an
output timestamp offset keep segments from different runs aligned.

The encoder is NVENC when the GPU offers it, otherwise x264. A transcoder
pauses (SIGSTOP) once it is far ahead of playback and sessions idle for
IDLE_SECONDS are killed and their segments deleted.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

SEGMENT_SECONDS = 6
IDLE_SECONDS = 600
MAX_SESSIONS = 3
# Pause a transcoder this many segments ahead of the newest request, resume nearer.
PAUSE_AHEAD = 40
RESUME_AHEAD = 20
# A request within this many segments of the transcoder's progress waits for it.
NEAR_SEGMENTS = 4
WAIT_SECONDS = 45

CACHE_DIR = Path(
    os.environ.get("CLOCKWORK_STREAM_CACHE", Path.home() / ".cache" / "clockwork" / "library-hls")
)

DIRECT_CONTAINERS = {".mp4", ".m4v", ".webm"}
DIRECT_VIDEO = {"h264", "vp8", "vp9", "av1"}
DIRECT_AUDIO = {"aac", "mp3", "opus", "vorbis", None}
DIRECT_PIXEL_FORMATS = {"yuv420p", "yuvj420p", None}
TEXT_SUBTITLE_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "text"}


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    container: str
    video_codec: str | None
    pixel_format: str | None
    audio_codec: str | None
    text_subtitles: tuple[tuple[int, str], ...] = ()  # (subtitle stream index, language)


def probe_media(
    path: Path, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> MediaInfo:
    result = runner(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,pix_fmt:stream_tags=language",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    subtitles = [s for s in streams if s.get("codec_type") == "subtitle"]
    text_subtitles = tuple(
        (index, (s.get("tags") or {}).get("language") or "und")
        for index, s in enumerate(subtitles)
        if s.get("codec_name") in TEXT_SUBTITLE_CODECS
    )
    return MediaInfo(
        duration=float((data.get("format") or {}).get("duration") or 0),
        container=path.suffix.lower(),
        video_codec=video.get("codec_name"),
        pixel_format=video.get("pix_fmt"),
        audio_codec=audio.get("codec_name"),
        text_subtitles=text_subtitles,
    )


def plays_directly(info: MediaInfo) -> bool:
    """True when common browsers can play the file as is."""
    return (
        info.container in DIRECT_CONTAINERS
        and info.video_codec in DIRECT_VIDEO
        and info.pixel_format in DIRECT_PIXEL_FORMATS
        and info.audio_codec in DIRECT_AUDIO
    )


def segment_count(duration: float) -> int:
    return max(1, math.ceil(duration / SEGMENT_SECONDS))


def vod_playlist(duration: float, segment_url: Callable[[int], str]) -> str:
    count = segment_count(duration)
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        # Segments run a little past SEGMENT_SECONDS to reach the next forced keyframe.
        f"#EXT-X-TARGETDURATION:{SEGMENT_SECONDS + 1}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    for index in range(count):
        length = min(SEGMENT_SECONDS, duration - index * SEGMENT_SECONDS) if duration else 0
        lines.append(f"#EXTINF:{max(length, 0.1):.3f},")
        lines.append(segment_url(index))
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


_ENCODER: str | None = None
_ENCODER_LOCK = threading.Lock()


def pick_encoder(runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str:
    """h264_nvenc when a usable NVIDIA GPU is present, else libx264. Checked once."""
    global _ENCODER
    with _ENCODER_LOCK:
        if _ENCODER is None:
            try:
                result = runner(
                    [
                        "ffmpeg",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "color=size=256x144:duration=0.2",
                        "-c:v",
                        "h264_nvenc",
                        "-f",
                        "null",
                        "-",
                    ],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                _ENCODER = "h264_nvenc" if result.returncode == 0 else "libx264"
            except (OSError, subprocess.SubprocessError):
                _ENCODER = "libx264"
        return _ENCODER


def transcode_command(source: Path, out_dir: Path, start_index: int, encoder: str) -> list[str]:
    start = start_index * SEGMENT_SECONDS
    keyframes = f"expr:gte(t,n_forced*{SEGMENT_SECONDS})"
    if encoder == "h264_nvenc":
        decode = ["-hwaccel", "cuda"]
        video = [
            "-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23",
            "-maxrate", "12M", "-bufsize", "24M",
            "-forced-idr", "1", "-no-scenecut", "1",
        ]  # fmt: skip
    else:
        decode = []
        video = [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-maxrate", "12M", "-bufsize", "24M", "-sc_threshold", "0",
        ]  # fmt: skip
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        *decode,
        "-ss", str(start), "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?",
        # 10-bit and other formats down to what H.264 decoders in browsers accept.
        "-vf", "format=yuv420p",
        *video,
        "-force_key_frames", keyframes,
        "-c:a", "aac", "-ac", "2", "-b:a", "192k",
        "-output_ts_offset", str(start),
        "-f", "hls", "-hls_time", str(SEGMENT_SECONDS),
        "-start_number", str(start_index),
        "-hls_segment_filename", str(out_dir / "seg_%d.ts"),
        "-hls_playlist_type", "event", "-hls_flags", "temp_file",
        str(out_dir / f"run_{start_index}.m3u8"),
    ]  # fmt: skip


@dataclass
class Session:
    key: str
    source: Path
    directory: Path
    info: MediaInfo
    process: subprocess.Popen | None = None
    run_start: int = 0
    last_index: int = 0
    paused: bool = False
    last_access: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def _listed(playlist: Path) -> set[int]:
        try:
            text = playlist.read_text()
        except OSError:
            return set()
        return {
            int(line[4:-3])
            for line in text.splitlines()
            if line.startswith("seg_") and line.endswith(".ts")
        }

    def complete_segments(self) -> set[int]:
        """Segments some run's playlist lists; partial files from killed runs are not."""
        done: set[int] = set()
        for playlist in self.directory.glob("run_*.m3u8"):
            done |= self._listed(playlist)
        return done

    def run_progress(self) -> int:
        """Last segment the current run has finished (run_start - 1 before its first)."""
        listed = self._listed(self.directory / f"run_{self.run_start}.m3u8")
        return max(listed, default=self.run_start - 1)

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            if self.paused:
                self.process.send_signal(signal.SIGCONT)
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        self.paused = False


class StreamManager:
    def __init__(
        self,
        cache_dir: Path = CACHE_DIR,
        *,
        encoder: Callable[[], str] = pick_encoder,
        probe: Callable[[Path], MediaInfo] = probe_media,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache_dir = cache_dir
        self.encoder = encoder
        self.probe = probe
        self.popen = popen
        self.clock = clock
        self.sleep = sleep
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()

    def session(self, source: Path) -> Session:
        stat = source.stat()
        key = hashlib.sha256(f"{source}|{stat.st_size}|{stat.st_mtime}".encode()).hexdigest()[:24]
        with self.lock:
            self._reap()
            session = self.sessions.get(key)
            if session is None:
                directory = self.cache_dir / key
                shutil.rmtree(directory, ignore_errors=True)
                directory.mkdir(parents=True, exist_ok=True)
                session = Session(key, source, directory, self.probe(source))
                self.sessions[key] = session
                self._enforce_limit(keep=key)
            session.last_access = self.clock()
            return session

    def segment(self, source: Path, index: int) -> Path | None:
        """Path of a finished segment, transcoding it first if needed; None on failure."""
        session = self.session(source)
        if index < 0 or index >= segment_count(session.info.duration):
            return None
        target = session.directory / f"seg_{index}.ts"
        deadline = self.clock() + WAIT_SECONDS
        session.last_index = index
        with session.lock:
            done = session.complete_segments()
            if index not in done:
                # Only the current run's own output says how close it is; segments
                # left by earlier runs (other seek positions) must not count.
                reachable = (
                    session.running()
                    and session.run_start <= index <= session.run_progress() + NEAR_SEGMENTS
                )
                if not reachable:
                    self._start(session, index)
            self._throttle(session, index, done)
        while self.clock() < deadline:
            if index in session.complete_segments():
                with session.lock:
                    self._throttle(session, index, session.complete_segments())
                return target
            if not session.running():
                # The run ended (end of file or error) without producing this segment.
                return target if index in session.complete_segments() else None
            self.sleep(0.2)
        return None

    def _start(self, session: Session, index: int) -> None:
        session.stop()
        session.run_start = index
        command = transcode_command(session.source, session.directory, index, self.encoder())
        session.process = self.popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )

    def _throttle(self, session: Session, index: int, done: set[int]) -> None:
        if not session.running():
            return
        # Finished segments in an unbroken run after the one being watched.
        end = index - 1
        while end + 1 in done:
            end += 1
        ahead = end - index
        if ahead >= PAUSE_AHEAD and not session.paused:
            session.process.send_signal(signal.SIGSTOP)
            session.paused = True
        elif ahead <= RESUME_AHEAD and session.paused:
            session.process.send_signal(signal.SIGCONT)
            session.paused = False

    def _reap(self) -> None:
        now = self.clock()
        for key, session in list(self.sessions.items()):
            if now - session.last_access > IDLE_SECONDS:
                self._drop(key)

    def _enforce_limit(self, keep: str) -> None:
        others = sorted(
            (s for k, s in self.sessions.items() if k != keep), key=lambda s: s.last_access
        )
        while len(others) >= MAX_SESSIONS:
            self._drop(others.pop(0).key)

    def _drop(self, key: str) -> None:
        session = self.sessions.pop(key, None)
        if session is None:
            return
        session.stop()
        shutil.rmtree(session.directory, ignore_errors=True)

    def housekeeping(self) -> None:
        """Drop idle sessions and pause transcoders far ahead of their viewer."""
        with self.lock:
            self._reap()
            sessions = list(self.sessions.values())
        for session in sessions:
            with session.lock:
                self._throttle(session, session.last_index, session.complete_segments())

    def reap_forever(self, interval: float = 5.0) -> None:
        # Without this a closed tab leaves ffmpeg converting to the end of the file.
        while True:
            self.sleep(interval)
            self.housekeeping()


def subtitle_vtt(source: Path, stream_index: int, runner=subprocess.run) -> str:
    """Extract one embedded text subtitle stream as WebVTT."""
    result = runner(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(source),
            "-map",
            f"0:s:{stream_index}",
            "-f",
            "webvtt",
            "-",
        ],  # fmt: skip
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return result.stdout if result.returncode == 0 else "WEBVTT\n\n"


_MANAGER: StreamManager | None = None
_MANAGER_LOCK = threading.Lock()


def manager() -> StreamManager:
    """Process-wide manager with a background reaper for idle sessions."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = StreamManager()
            threading.Thread(target=_MANAGER.reap_forever, daemon=True).start()
        return _MANAGER
