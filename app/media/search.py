from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple
import re

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
        # This is what the user sees in the TUI
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
    """
    Build list of strings for fuzzy matching.
    Each choice corresponds by index to entries[i].
    """
    return [entry.display_text for entry in entries]


# ---------- helpers ----------

_normalize_sep_re = re.compile(r"[._\-]+")


def _normalize_for_exact(text: str) -> str:
    """
    Normalize text for 'exact-ish' substring matching:

    - lower-case
    - replace '.', '_', '-' with space
    - collapse multiple spaces

    e.g. "Penny.Dreadful-S02E03.1080p" -> "penny dreadful s02e03 1080p"
    """
    text = text.lower()
    text = _normalize_sep_re.sub(" ", text)
    # collapse whitespace
    parts = text.split()
    return " ".join(parts)


# ---------- main search ----------

def search_media(
    pattern: str,
    entries: List[MediaEntry],
    choices: List[str],
    limit: int = 50,
    score_cutoff: int = 40,
) -> List[Tuple[MediaEntry, float]]:
    """
    Two-stage fuzzy search:

    1. "Exact-ish" matches first:
       - Normalize both pattern and candidates (lowercase, replace ._- with spaces)
       - If normalized pattern is a substring of a candidate, that candidate
         is considered an 'exact' match and ranked at the top.

    2. Remaining candidates ranked by RapidFuzz WRatio.

    This keeps search case-insensitive, but strongly biases towards the
    intuitive 'this string is actually in the filename/path' results.
    """

    pattern = pattern.strip()
    if not pattern or not entries:
        return []

    norm_pattern = _normalize_for_exact(pattern)
    if not norm_pattern:
        return []

    # --- Stage 1: collect "exact-ish" matches (substring in normalized text) ---

    exact_indices: List[int] = []
    exact_results: List[Tuple[MediaEntry, float]] = []

    for idx, label in enumerate(choices):
        norm_label = _normalize_for_exact(label)
        if norm_pattern in norm_label:
            # This feels like what fzf would treat as a very strong match.
            score = float(fuzz.WRatio(pattern, label))
            if score >= score_cutoff:
                exact_indices.append(idx)
                exact_results.append((entries[idx], score))

    # Sort exact matches by score descending, stable by original order
    exact_results.sort(key=lambda es: -es[1])

    # If we've already got enough exact matches, return them
    if len(exact_results) >= limit:
        return exact_results[:limit]

    # --- Stage 2: standard fuzzy search for the rest ---

    remaining_choices = []
    remaining_map = []  # map local index -> original index

    exact_set = set(exact_indices)

    for idx, label in enumerate(choices):
        if idx in exact_set:
            continue
        remaining_choices.append(label)
        remaining_map.append(idx)

    fuzzy_limit = max(0, limit - len(exact_results))
    fuzzy_results: List[Tuple[MediaEntry, float]] = []

    if remaining_choices and fuzzy_limit > 0:
        # Use RapidFuzz over the remaining candidates
        rf_results = process.extract(
            pattern,
            remaining_choices,
            scorer=fuzz.WRatio,
            score_cutoff=score_cutoff,
            limit=fuzzy_limit,
        )

        for _choice_text, score, local_idx in rf_results:
            original_idx = remaining_map[local_idx]
            fuzzy_results.append((entries[original_idx], float(score)))

    # Final list: exact-ish matches first, then other fuzzy matches
    return exact_results + fuzzy_results
