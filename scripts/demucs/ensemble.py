"""Blend multiple demucs models' stem outputs into one file via sample averaging."""

import shutil
import subprocess
from pathlib import Path
from typing import List


def blend_wavs(inputs: List[Path], output: Path) -> None:
    """Average N wav files sample-by-sample (arithmetic mean) into output."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if len(inputs) == 1:
        shutil.copy2(inputs[0], output)
        return

    cmd = ["ffmpeg", "-y"]
    for path in inputs:
        cmd += ["-i", str(path)]
    n = len(inputs)
    streams = "".join(f"[{i}:a]" for i in range(n))
    # normalize=0 sums samples directly; volume=1/n turns the sum back into a mean.
    filter_complex = f"{streams}amix=inputs={n}:normalize=0,volume={1.0 / n}"
    cmd += ["-filter_complex", filter_complex, str(output)]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"ffmpeg blend failed for {output.name}: {exc.stderr}") from exc


def parse_model_list(raw: str | None, fallback: str) -> List[str]:
    if not raw:
        return [fallback]
    models = [m.strip() for m in raw.split(",") if m.strip()]
    return models or [fallback]
