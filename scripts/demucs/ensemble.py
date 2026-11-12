"""Blend multiple demucs models' stem outputs into one file via sample averaging."""

import shutil
import subprocess
from pathlib import Path
from typing import List


def blend_stems(inputs: List[Path], output: Path, mp3_bitrate: int | None = None) -> None:
    """Average N stem files sample-by-sample (arithmetic mean) into output.

    The output format follows output's extension. A single input in the same
    format is copied as-is; otherwise ffmpeg mixes and/or transcodes it.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    if len(inputs) == 1 and inputs[0].suffix.lower() == output.suffix.lower():
        shutil.copy2(inputs[0], output)
        return

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for path in inputs:
        cmd += ["-i", str(path)]
    n = len(inputs)
    if n > 1:
        streams = "".join(f"[{i}:a]" for i in range(n))
        # normalize=0 sums samples directly; volume=1/n turns the sum back into a mean.
        cmd += ["-filter_complex", f"{streams}amix=inputs={n}:normalize=0,volume={1.0 / n}"]
    if output.suffix.lower() == ".mp3":
        cmd += ["-codec:a", "libmp3lame"]
        if mp3_bitrate:
            cmd += ["-b:a", f"{mp3_bitrate}k"]
    cmd.append(str(output))

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"ffmpeg blend failed for {output.name}: {exc.stderr}") from exc


def parse_model_list(raw: str | None, fallback: str) -> List[str]:
    if not raw:
        return [fallback]
    models = [m.strip() for m in raw.split(",") if m.strip()]
    return models or [fallback]
