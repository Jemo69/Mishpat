"""Video helper: stitch Selenium screenshots into an MP4 with local ffmpeg. No paid service."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def stitch_frames(shot_paths: list[str], out: str | Path, fps: int = 2) -> dict:
    """Compile ordered PNGs into an MP4. Returns {ok, path} or {ok: False, error}."""
    out = Path(out)
    if not shot_paths:
        return {"ok": False, "error": "no frames captured"}
    if shutil.which("ffmpeg") is None:
        return {"ok": False, "error": "ffmpeg not found on PATH"}
    frames = out.parent / (out.stem + "-frames")
    frames.mkdir(parents=True, exist_ok=True)
    for i, src in enumerate(shot_paths, 1):
        shutil.copy(src, frames / f"frame-{i:03d}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", str(frames / "frame-%03d.png"),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out.exists():
        return {"ok": False, "error": proc.stderr[-2000:]}
    return {"ok": True, "path": str(out), "frames": len(shot_paths)}
