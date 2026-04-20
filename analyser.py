import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv

from scripts.core import state as st
from scripts.core.paths import ensure_dir, resolve_dir

load_dotenv()

def main() -> int:
    parser = argparse.ArgumentParser(description="Analyse MP3s in a folder for key and BPM")
    parser.add_argument("root", help="Playlist root directory containing state.json")
    args = parser.parse_args()

    root = Path(args.root).expanduser()

# TODO restore later
    # playlist_state = st.load(root)
    # if not playlist_state:
    #     print(f"ERROR: no state.json found in {root}", file=sys.stderr)
    #     return 1

    root_mp3s: Dict[str, List[str]] = {}

    base_dir = os.path.join(root, "unprocessed")
    if not os.path.isdir(base_dir):
        raise SystemExit(f"Unprocessed directory not found: {base_dir}")
    
    mp3_list = [
        os.path.join(base_dir, f)
        for f in os.listdir(base_dir)
        if f.lower().endswith(".mp3") and not f.startswith("._")
    ]

    mp3_list.sort()

    for mp3_file in mp3_list:
        print(mp3_file)

    # remaining_files = len(mp3_list)

    # if remaining_files is not None:
    #     if remaining_files <= 0:
    #         mp3_list = []
    #     else:
    #         mp3_list = mp3_list[:remaining_files]
    #         remaining_files -= len(mp3_list)



#  for f in *.mp3; do                                                                                   │
#  bpm=$(aubio tempo "$f" | tail -1 | awk '{print $1}')                                                                          │
#  key=$(keyfinder-cli -n camelot "$f")                                                                                          │
#  echo "$bpm $key $f" >> info.txt                                                                                               │
# done | sort -n                     

if __name__ == "__main__":
    raise SystemExit(main())
