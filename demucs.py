"""
Run Demucs stem separation on a playlist's unprocessed/ directory.

Usage:
    python demucs.py [--api] <root_dir> [4|2|both]

    4     4-stem output only  (vocals, drums, bass, other)
    2     2-stem output only  (vocals isolate)
    both  both outputs (default)

Example:
    python demucs.py "/home/jake/Music/Playlists/BATW Rejects" both
    python demucs.py --api "/home/jake/Music/Playlists/BATW Rejects"
"""

import sys
from scripts.cli.demucs import main

if __name__ == "__main__":
    raise SystemExit(main())
