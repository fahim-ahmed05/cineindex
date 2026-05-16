from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, List, Tuple, Callable
import time
import concurrent.futures

import requests
from colorama import Fore, Style, init

from ..db import get_conn
from .parser import parse_directory_page, ParsedPage

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
    # Max concurrent threads for crawling this root
    threads: int = 15


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
        threads = r.get("threads", 15)
        roots.append(
            RootConfig(
                url=url,
                enabled=bool(enabled),
                decode_percent=bool(decode_percent),
                dots_to_spaces=bool(dots_to_spaces),
                threads=int(threads),
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


def _fetch_and_parse(session: requests.Session, url: str, verbose: bool) -> Optional[ParsedPage]:
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        if verbose:
            print(Fore.RED + f"[CRAWL] Error fetching {url}: {e}")
        return None
    return parse_directory_page(html, url, verbose=verbose)


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
        
        live_dir_urls = set()
        live_media_urls = set()
        has_errors = False

        if verbose:
            print(Fore.MAGENTA + f"[CRAWL] Starting crawl for {root_cfg.url}")
        t0 = time.time()

        max_workers = root_cfg.threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_url = {}
            seen_urls = set()

            def submit_job(d_url, p_url):
                if d_url in seen_urls:
                    return
                seen_urls.add(d_url)
                
                rel = _path_from_root(root_cfg.url, d_url)
                if _is_blocked_dir(rel, cfg):
                    if verbose:
                        print(Fore.YELLOW + f"[SKIP] Blocked dir: {rel}")
                    return
                # Pass verbose=False to worker to avoid interleaved terminal output
                future = executor.submit(_fetch_and_parse, session, d_url, False)
                future_to_url[future] = (d_url, p_url, rel)

            while queue:
                submit_job(*queue.pop(0))

            while future_to_url:
                done, not_done = concurrent.futures.wait(
                    future_to_url.keys(), 
                    return_when=concurrent.futures.FIRST_COMPLETED
                )

                for future in done:
                    dir_url, parent_url, rel_path = future_to_url.pop(future)
                    try:
                        parsed = future.result()
                    except Exception as e:
                        if verbose:
                            print(Fore.RED + f"[CRAWL] Error processing {dir_url}: {e}")
                        has_errors = True
                        continue
                    if parsed is None:
                        has_errors = True
                        continue

                    live_dir_urls.add(dir_url)
                    for f in parsed.files:
                        if _should_keep_file(f.name, cfg):
                            live_media_urls.add(f.url)

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
                        submit_job(d.url, dir_url)

                    if processed_dirs % 20 == 0:
                        conn.commit()
                        if verbose:
                            print(
                                Style.DIM + f"  ...progress: {processed_dirs} dirs processed, "
                                f"{skipped_dirs} skipped as unchanged..."
                            )

        conn.commit()
        elapsed = time.time() - t0

        # Cleanup: Remove any rows from dirs and media that belong to this root
        # but were NOT encountered during this crawl. This handles files or
        # directories that were deleted from the upstream server.
        if incremental and not has_errors:
            try:
                # Delete stale directories
                cur.execute("SELECT url FROM dirs WHERE root = ?", (root_cfg.url,))
                dirs_to_del = [(r[0],) for r in cur.fetchall() if r[0] not in live_dir_urls]
                if dirs_to_del:
                    cur.executemany("DELETE FROM dirs WHERE url = ?", dirs_to_del)

                # Delete stale media files
                cur.execute("SELECT url FROM media WHERE root = ?", (root_cfg.url,))
                media_to_del = [(r[0],) for r in cur.fetchall() if r[0] not in live_media_urls]
                if media_to_del:
                    cur.executemany("DELETE FROM media WHERE url = ?", media_to_del)
            except Exception as e:
                if verbose:
                    print(Fore.RED + f"[CRAWL] Cleanup error: {e}")
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
