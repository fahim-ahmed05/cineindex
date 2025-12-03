from __future__ import annotations

import json
from typing import List, Tuple
from pathlib import Path

from ..db import get_conn, DATA_DIR
from .search import MediaEntry

# History log written by cineindex-history.lua.
LOGFILE: Path = DATA_DIR / "cineindex-mpv-events.log"


def get_recent_history(
    conn=None,
    limit: int = 50,
) -> List[Tuple[MediaEntry, str]]:
    """
    Read recent watch history from the JSONL log written by cineindex-history.lua.

    - Deduplicate by URL (keep latest Time)
    - Sort by Time descending (most recent first)
    - Join with media table when possible for metadata
    - Return up to `limit` items as (MediaEntry, played_at)
    """
    if not LOGFILE.exists():
        return []

    try:
        with LOGFILE.open("r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []

    # Limit processing to the last N lines for performance on large logs
    lines = lines[-2000:]

    # Map: url -> latest time string
    latest: dict[str, str] = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        url = data.get("Url") or data.get("url") or ""
        t = data.get("Time") or data.get("time") or ""
        if not url or not t:
            continue

        latest[url] = t

    if not latest:
        return []

    # Sort by time descending (string compare works because of YYYY-MM-DD HH:MM:SS format)
    sorted_items = sorted(latest.items(), key=lambda kv: kv[1], reverse=True)

    own_conn = False
    if conn is None:
        conn = get_conn()
        own_conn = True

    try:
        urls = [u for u, _t in sorted_items]
        if not urls:
            return []

        placeholders = ",".join("?" for _ in urls)
        media_map: dict[str, MediaEntry] = {}

        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT url, root, path, filename, size, modified
            FROM media
            WHERE url IN ({placeholders})
            """,
            urls,
        )
        for r in cur.fetchall():
            media_map[r["url"]] = MediaEntry(
                url=r["url"],
                root=r["root"] or "",
                path=r["path"] or "",
                filename=r["filename"],
                size=r["size"],
                modified=r["modified"],
            )

        history: List[Tuple[MediaEntry, str]] = []

        for url, t in sorted_items:
            if url in media_map:
                entry = media_map[url]
            else:
                # Fallback entry when URL isn't in the media table
                entry = MediaEntry(
                    url=url,
                    root="",
                    path="",
                    filename=url,
                    size=None,
                    modified=None,
                )
            history.append((entry, t))
            if len(history) >= limit:
                break

        return history
    finally:
        if own_conn:
            conn.close()
