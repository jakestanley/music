"""
Interactive curses-based fuzzy track picker.

Usage:
    from scripts.core.picker import pick_track

    result = pick_track(tracks, prompt="Select track")
    if result:
        track_id, track = result
"""

import curses
import re
from difflib import SequenceMatcher


def _normalise(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def _score(query_norm: str, label_norm: str) -> float:
    if not query_norm:
        return 1.0
    q_tokens = query_norm.split()
    l_tokens = set(label_norm.split())
    token_hits = sum(1 for t in q_tokens if any(t in lt for lt in l_tokens))
    token_score = token_hits / len(q_tokens)
    seq_score = SequenceMatcher(None, query_norm, label_norm).ratio()
    return 0.7 * token_score + 0.3 * seq_score


def _search(
    query: str,
    tracks: list[tuple[str, dict]],
    sort_ascending: bool | None = None,
) -> list[tuple[str, dict, float]]:
    query_norm = _normalise(query)
    if not query_norm and sort_ascending is not None:
        # No query — sort by BPM in the direction of travel
        return sorted(
            [(tid, t, 1.0) for tid, t in tracks],
            key=lambda x: float(x[1].get("bpm") or 0),
            reverse=not sort_ascending,
        )
    scored = [
        (tid, t, _score(query_norm, _normalise(f"{t['artist']} {t['name']}")))
        for tid, t in tracks
    ]
    if query_norm:
        scored = [(tid, t, s) for tid, t, s in scored if s > 0.0]
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored


def _track_line(track: dict, width: int) -> str:
    key = track.get("camelot_key") or "?"
    bpm = track.get("bpm") or "?"
    label = f"{track['artist']} - {track['name']}"
    suffix = f"  [{key}  {bpm} BPM]"
    max_label = width - len(suffix) - 3
    if len(label) > max_label:
        label = label[: max_label - 1] + "…"
    return f"  {label}{suffix}"


def _run_picker(
    stdscr: "curses._CursesWindow",
    tracks: list[tuple[str, dict]],
    prompt: str,
    header: list[str],
    esc_label: str,
    sort_ascending: bool | None,
) -> tuple[str, dict] | None:
    curses.curs_set(1)
    try:
        curses.use_default_colors()
        curses.init_pair(1, -1, -1)
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
    except Exception:
        pass

    query = ""
    selected = 0
    # Rows consumed by header block (lines + separator), then search bar + separator
    header_rows = len(header) + (1 if header else 0)  # +1 for separator after header
    chrome_rows = header_rows + 1 + 1 + 1  # search bar + separator + status bar

    while True:
        results = _search(query, tracks, sort_ascending)
        height, width = stdscr.getmaxyx()
        list_height = max(1, height - chrome_rows)
        visible = results[:list_height]

        if selected >= len(visible):
            selected = max(0, len(visible) - 1)

        stdscr.erase()
        row = 0

        # Header lines
        for line in header:
            try:
                stdscr.addstr(row, 0, line[:width])
            except curses.error:
                pass
            row += 1

        # Separator after header (only if header present)
        if header:
            try:
                stdscr.addstr(row, 0, "─" * (width - 1))
            except curses.error:
                pass
            row += 1

        # Search bar
        search_line = f" {prompt}  {query}"
        try:
            stdscr.addstr(row, 0, search_line[:width])
        except curses.error:
            pass
        search_row = row
        row += 1

        # Separator after search bar
        try:
            stdscr.addstr(row, 0, "─" * (width - 1))
        except curses.error:
            pass
        row += 1

        # Results
        for i, (tid, track, _) in enumerate(visible):
            if row >= height - 1:
                break
            line = _track_line(track, width)
            try:
                if i == selected:
                    stdscr.attron(curses.A_REVERSE)
                    stdscr.addstr(row, 0, line[: width - 1].ljust(width - 1))
                    stdscr.attroff(curses.A_REVERSE)
                else:
                    stdscr.addstr(row, 0, line[: width - 1])
            except curses.error:
                pass
            row += 1

        # Status bar
        status = f" {len(results)} matches   ↑↓ navigate   Enter select   Esc {esc_label}"
        try:
            stdscr.addstr(height - 1, 0, status[: width - 1])
        except curses.error:
            pass

        # Cursor at end of query
        cursor_col = min(len(search_line), width - 1)
        try:
            stdscr.move(search_row, cursor_col)
        except curses.error:
            pass

        stdscr.refresh()

        ch = stdscr.getch()

        if ch == 27:  # Esc
            return None

        elif ch in (10, 13):  # Enter
            if visible:
                tid, track, _ = visible[selected]
                return (tid, track)
            return None

        elif ch == curses.KEY_UP:
            selected = max(0, selected - 1)

        elif ch == curses.KEY_DOWN:
            selected = min(len(visible) - 1, selected + 1)

        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            query = query[:-1]
            selected = 0

        elif 32 <= ch < 127:
            query += chr(ch)
            selected = 0


def pick_track(
    tracks: list[tuple[str, dict]],
    prompt: str = "Search:",
    header: list[str] | None = None,
    esc_label: str = "cancel",
    sort_ascending: bool | None = None,
) -> tuple[str, dict] | None:
    """
    Launch an interactive curses fuzzy picker over the given track list.

    Args:
        tracks:         list of (track_id, track_dict) pairs
        prompt:         label shown in the search bar
        header:         optional lines of static context shown above the search bar
        esc_label:      label for the Esc key in the status bar (default: "cancel")
        sort_ascending: when True/False, the default sort (no query) orders by BPM
                        ascending or descending respectively; entering a query switches
                        to fuzzy sort, clearing it restores BPM order

    Returns:
        (track_id, track_dict) of the selected track, or None if cancelled.
    """
    return curses.wrapper(_run_picker, tracks, prompt, header or [], esc_label, sort_ascending)
