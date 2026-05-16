from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, List, Tuple, Callable
import time

import requests
from colorama import Fore, Style, init

from ..db import get_conn
from .parser import parse_directory_page

init(autoreset=True)


@dataclass
class RootConfig:
    url: str
    enabled: bool = True
    # Presentation options (controlled via roots.json)
    # If True, decode percent-encoded paths (e.g. %20 -> space) when building display tree
    decode_percent: bool = True
    # If True, treat dots in filenames as word separators and display them as spaces
    dots_to_spaces: bool = False


@dataclass
class CrawlConfig:
    video_exts: List[str]
    blocked_dirs: List[str]


@dataclass
class CrawlResult:
    processed_dirs: int
    inserted_files: int
    skipped_dirs: int
    elapsed_seconds: float
    # List of added files as tuples: (path, filename, url)
    added_files: List[Tuple[str, str, str]] = field(default_factory=list)


def normalize_root_url(url: str) -> str:
    """Ensure root URL ends with a slash."""
    return url.rstrip("/") + "/"


def load_root_configs(raw_roots: Iterable[dict]) -> List[RootConfig]:
    """
    Build RootConfig objects from roots.json entries.

    Cookie support removed: we only care about the 'url' now.
    """
    roots: List[RootConfig] = []
    for r in raw_roots:
        url = r.get("url", "").strip()
        if not url:
            continue
        url = normalize_root_url(url)
        enabled = r.get("enabled", True)
        decode_percent = r.get("decode_percent", True)
        dots_to_spaces = r.get("dots_to_spaces", False)
        roots.append(
            RootConfig(
                url=url,
                enabled=bool(enabled),
                decode_percent=bool(decode_percent),
                dots_to_spaces=bool(dots_to_spaces),
            )
        )
    return roots


def load_crawl_config(raw_cfg: dict) -> CrawlConfig:
    exts = [e.lower().lstrip(".") for e in raw_cfg.get("video_extensions", [])]
    blocked = [b.strip().lower() for b in raw_cfg.get("blocked_dirs", [])]
    return CrawlConfig(video_exts=exts, blocked_dirs=blocked)


def _path_from_root(root_url: str, dir_url: str) -> str:
    r = normalize_root_url(root_url)
    if not dir_url.startswith(r):
        return "/"
    rel = dir_url[len(r) :].rstrip("/")
    return "/" + rel if rel else "/"


def _is_blocked_dir(path: str, cfg: CrawlConfig) -> bool:
    if not cfg.blocked_dirs:
        return False
    last = path.strip("/").split("/")[-1].lower()
    return last in cfg.blocked_dirs


def _should_keep_file(filename: str, cfg: CrawlConfig) -> bool:
    if not cfg.video_exts:
        return True
    lower = filename.lower()
    dot = lower.rfind(".")
    if dot == -1:
        return False
    ext = lower[dot + 1 :]
    return ext in cfg.video_exts


def _make_session(root_cfg: RootConfig) -> requests.Session:
    """
    Create a plain requests.Session.

    Cookie jar support removed — if you ever re-add auth, this is the place.
    """
    return requests.Session()


def _fetch_page(session: requests.Session, url: str) -> Optional[str]:
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(Fore.RED + f"[CRAWL] Error fetching {url}: {e}")
        return None


def crawl_root(
    root_cfg: RootConfig,
    cfg: CrawlConfig,
    conn=None,
    incremental: bool = False,
    summary_only: bool = False,
    on_new_file: Optional[Callable[[str, str, str], None]] = None,
) -> CrawlResult:
    """
    Crawl a single root directory and update the local database.

    Incremental logic (important bits):

    - We ALWAYS fetch & parse every directory page (so subdirs are discovered).
    - If incremental is True:
        * If dir_modified is not None and unchanged in DB:
              - we SKIP rewriting this dir's files in media/dirs
              - but we STILL descend into its subdirs.
        * If dir_modified is None:
              - we ALWAYS reindex this dir's files (no timestamp to compare).
    """

    own_conn = False
    if conn is None:
        conn = get_conn()
        own_conn = True

    session = _make_session(root_cfg)
    verbose = not summary_only

    try:
        cur = conn.cursor()

        if not incremental and verbose:
            print(Fore.CYAN + f"[BUILD] Clearing existing index for {root_cfg.url}")
            cur.execute("DELETE FROM media WHERE root = ?", (root_cfg.url,))
            cur.execute("DELETE FROM dirs WHERE root = ?", (root_cfg.url,))
            conn.commit()

        queue: List[Tuple[str, Optional[str]]] = [(root_cfg.url, None)]
        processed_dirs = 0
        inserted_files = 0
        added_files: List[Tuple[str, str, str]] = []
        skipped_dirs = 0

        if verbose:
            print(Fore.MAGENTA + f"[CRAWL] Starting crawl for {root_cfg.url}")
        t0 = time.time()

        while queue:
            dir_url, parent_url = queue.pop(0)
            rel_path = _path_from_root(root_cfg.url, dir_url)

            if _is_blocked_dir(rel_path, cfg):
                if verbose:
                    print(Fore.YELLOW + f"[SKIP] Blocked dir: {rel_path}")
                continue

            html = _fetch_page(session, dir_url)
            if html is None:
                continue

            parsed = parse_directory_page(html, dir_url, verbose=verbose)
            dir_modified = parsed.dir_modified

            unchanged = False
            if incremental:
                if dir_modified is not None:
                    # Only attempt skip if we have a timestamp
                    cur.execute("SELECT modified FROM dirs WHERE url = ?", (dir_url,))
                    row = cur.fetchone()
                    if row is not None and row["modified"] == dir_modified:
                        unchanged = True
                        skipped_dirs += 1

            batch_files = 0

            if not incremental or not unchanged:
                # Either full build, or directory changed (or no timestamp)
                cur.execute(
                    """
                    INSERT INTO dirs (url, root, parent, name, modified)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        root=excluded.root,
                        parent=excluded.parent,
                        name=excluded.name,
                        modified=excluded.modified
                    """,
                    (
                        dir_url,
                        root_cfg.url,
                        parent_url,
                        rel_path.rsplit("/", 1)[-1] if rel_path != "/" else "",
                        dir_modified,
                    ),
                )

                # Clear old files for this path (for this root)
                cur.execute(
                    "DELETE FROM media WHERE root = ? AND path = ?",
                    (root_cfg.url, rel_path),
                )

                media_inserts = []
                for f in parsed.files:
                    if not _should_keep_file(f.name, cfg):
                        continue

                    media_inserts.append((f.url, root_cfg.url, rel_path, f.name, f.modified, f.size))
                    batch_files += 1
                    # Record added file (path, filename, url) for reporting
                    added_files.append((rel_path, f.name, f.url))
                    # Notify live reporter if present
                    if on_new_file is not None:
                        try:
                            on_new_file(root_cfg.url, rel_path, f.name)
                        except Exception:
                            # Reporter errors shouldn't stop crawling
                            pass

                if media_inserts:
                    cur.executemany(
                        """
                        INSERT INTO media (url, root, path, filename, modified, size)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(url) DO UPDATE SET
                            root=excluded.root,
                            path=excluded.path,
                            filename=excluded.filename,
                            modified=excluded.modified,
                            size=excluded.size
                        """,
                        media_inserts,
                    )
                inserted_files += batch_files

            processed_dirs += 1

            # Status output (verbose only)
            if verbose:
                print(Fore.CYAN + f"[DIR] {dir_url}")
                if unchanged and incremental:
                    print(
                        Fore.YELLOW
                        + "  - unchanged (timestamp match), skipping files; descending into subdirs."
                    )
                else:
                    print(
                        Fore.GREEN
                        + f"  - indexed {batch_files} files"
                        + Fore.YELLOW
                        + f", {len(parsed.subdirs)} subdirs"
                    )

            # Always descend into subdirectories, even if this dir was unchanged
            for d in parsed.subdirs:
                queue.append((d.url, dir_url))

            if processed_dirs % 20 == 0:
                conn.commit()
                if verbose:
                    print(
                        Style.DIM + f"  ...progress: {processed_dirs} dirs processed, "
                        f"{skipped_dirs} skipped as unchanged..."
                    )

        conn.commit()
        elapsed = time.time() - t0

        # Cleanup: in incremental mode, remove directory rows that have no
        # media entries for this root. This keeps the DB from retaining
        # empty/obsolete dirs when files are removed from the site.
        if incremental:
            try:
                cur.execute("SELECT DISTINCT path FROM media WHERE root = ?", (root_cfg.url,))
                media_paths = {row[0] for row in cur.fetchall()}
                
                cur.execute("SELECT url FROM dirs WHERE root = ?", (root_cfg.url,))
                to_delete = []
                for row in cur.fetchall():
                    dir_url = row[0]
                    rel_path = _path_from_root(root_cfg.url, dir_url)
                    if rel_path not in media_paths:
                        to_delete.append((dir_url,))
                        
                removed_dirs = len(to_delete)
                if to_delete:
                    cur.executemany("DELETE FROM dirs WHERE url = ?", to_delete)
                
                if removed_dirs and verbose:
                    print(
                        Fore.YELLOW
                        + f"[CLEAN] Removed {removed_dirs} empty dirs for {root_cfg.url}"
                    )
                conn.commit()
            except Exception:
                # Don't let cleanup failures stop the crawl
                pass
        # Always print a concise summary. When in verbose mode we include skipped count.
        if verbose:
            print(
                Fore.MAGENTA + f"[DONE] {root_cfg.url} → dirs={processed_dirs}, "
                f"skipped={skipped_dirs}, files={inserted_files}, time={elapsed:.1f}s\n"
            )
        return CrawlResult(
            processed_dirs=processed_dirs,
            inserted_files=inserted_files,
            skipped_dirs=skipped_dirs,
            elapsed_seconds=elapsed,
            added_files=added_files,
        )
    finally:
        if own_conn:
            conn.close()
