from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

from rapidfuzz import process, fuzz

from ..db import get_conn


@dataclass
class MediaEntry:
    url: str
    root: str
    path: str
    filename: str
    size: str | None
    modified: str | None

    @property
    def display_text(self) -> str:
        return f"{self.filename}  [{self.root}]  {self.path}"


def load_media_entries(conn=None) -> List[MediaEntry]:
    own_conn = False
    if conn is None:
        conn = get_conn()
        own_conn = True

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT url, root, path, filename, size, modified FROM media"
        )
        rows = cur.fetchall()
        entries: List[MediaEntry] = []
        for r in rows:
            entries.append(
                MediaEntry(
                    url=r["url"],
                    root=r["root"],
                    path=r["path"],
                    filename=r["filename"],
                    size=r["size"],
                    modified=r["modified"],
                )
            )
        return entries
    finally:
        if own_conn:
            conn.close()


def build_choice_list(entries: Iterable[MediaEntry]) -> List[str]:
    return [entry.display_text for entry in entries]


def search_media(
    pattern: str,
    entries: List[MediaEntry],
    choices: List[str],
    limit: int = 50,
    score_cutoff: int = 40,
) -> List[Tuple[MediaEntry, float]]:
    pattern = pattern.strip()
    if not pattern:
        return []

    results = process.extract(
        pattern,
        choices,
        scorer=fuzz.WRatio,
        score_cutoff=score_cutoff,
        limit=limit,
    )

    hits: List[Tuple[MediaEntry, float]] = []
    for _choice_text, score, idx in results:
        entry = entries[idx]
        hits.append((entry, float(score)))

    return hits
