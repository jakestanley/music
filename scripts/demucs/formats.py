"""Stem output format selection (WAV or MP3) shared by the local and API paths."""

import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List

STEM_FORMATS = ("mp3", "wav")
DEFAULT_STEM_FORMAT = "mp3"
MP3_BITRATES = (128, 160, 192, 224, 256, 320)
MAX_MP3_BITRATE = MP3_BITRATES[-1]
AUTO_BITRATE = "auto"

FOUR_STEMS = ("vocals", "drums", "bass", "other")
TWO_STEMS = ("vocals", "no_vocals")


def probe_bitrate_kbps(path: str | Path) -> float | None:
    """Return the first audio stream's bitrate in kbps (container bitrate as fallback)."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=bit_rate:format=bit_rate",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        return None
    for line in result.stdout.split():
        try:
            return int(line) / 1000.0
        except ValueError:
            continue  # "N/A" for VBR streams without a stream-level bitrate
    return None


def bitrate_for_source(source_kbps: float | None) -> int:
    """Smallest allowed MP3 bitrate at or above the source's; max when unknown or higher."""
    if source_kbps is None:
        return MAX_MP3_BITRATE
    for bitrate in MP3_BITRATES:
        if bitrate >= source_kbps:
            return bitrate
    return MAX_MP3_BITRATE


@dataclass(frozen=True)
class StemFormat:
    ext: str
    mp3_bitrate: int | None = None
    # When set, mp3_bitrate is chosen per input file from the source bitrate.
    auto_bitrate: bool = False

    def files(self, stems: tuple[str, ...]) -> List[str]:
        return [f"{stem}.{self.ext}" for stem in stems]

    def for_source(self, path: str | Path) -> "StemFormat":
        """Resolve auto bitrate against a specific input file; no-op otherwise."""
        if not self.auto_bitrate:
            return self
        return replace(self, mp3_bitrate=bitrate_for_source(probe_bitrate_kbps(path)), auto_bitrate=False)

    def describe(self) -> str:
        if self.ext != "mp3":
            return self.ext
        return f"mp3 @ {AUTO_BITRATE if self.auto_bitrate else f'{self.mp3_bitrate}k'}"


WAV = StemFormat("wav")


def resolve_stem_format(cli_value: str | None = None) -> StemFormat:
    """Pick the stem format from the CLI flag, then DEMUCS_STEM_FORMAT, then the default."""
    ext = (cli_value or os.environ.get("DEMUCS_STEM_FORMAT") or DEFAULT_STEM_FORMAT).strip().lower()
    if ext not in STEM_FORMATS:
        raise SystemExit(f"Invalid stem format {ext!r}: expected one of {', '.join(STEM_FORMATS)}")
    if ext == "wav":
        return WAV

    raw_bitrate = (os.environ.get("DEMUCS_MP3_BITRATE") or AUTO_BITRATE).strip().lower()
    if raw_bitrate == AUTO_BITRATE:
        return StemFormat("mp3", auto_bitrate=True)
    try:
        bitrate = int(raw_bitrate)
    except ValueError:
        raise SystemExit(f"Invalid DEMUCS_MP3_BITRATE: expected {AUTO_BITRATE!r} or an integer")
    if bitrate not in MP3_BITRATES:
        raise SystemExit(
            f"Invalid DEMUCS_MP3_BITRATE {bitrate}: expected {AUTO_BITRATE!r} or one of "
            f"{', '.join(map(str, MP3_BITRATES))}"
        )
    return StemFormat("mp3", bitrate)


def find_stem_file(directory: str | Path, stem: str) -> Path | None:
    """Return the stem file in directory in any supported format, or None."""
    for ext in STEM_FORMATS:
        candidate = Path(directory) / f"{stem}.{ext}"
        if candidate.is_file():
            return candidate
    return None


def has_stem(directory: str | Path, stem: str = "vocals") -> bool:
    return find_stem_file(directory, stem) is not None
