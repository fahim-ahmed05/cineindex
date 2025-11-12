from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import os
import re
import shutil
from urllib.parse import urlparse
from colorama import Fore, Style, init

from backend.app.db import init_db, get_conn, ROOT
from backend.app.media.crawler import (
    load_root_configs,
    load_crawl_config,
    crawl_root,
)
from backend.app.media.search import (
    load_media_entries,
    build_choice_list,
    search_media,
    MediaEntry,
)
from backend.app.media.history import get_recent_history

init(autoreset=True)

CONFIG_JSON = ROOT / "config.json"
ROOTS_JSON = ROOT / "roots.json"
EPISODE_REGEX = re.compile(r"[sS](\d{1,2})[ ._-]*[eE](\d{1,3})")


# ---------- Utility ----------

def separator_line() -> str:
    """Return a dim magenta line matching terminal width (capped for readability)."""
    width = shutil.get_terminal_size((80, 20)).columns
    return Fore.MAGENTA + Style.DIM + "─" * min(width, 120)


# ---------- Banner ----------

def print_banner() -> None:
    banner = Fore.MAGENTA + Style.BRIGHT + r"""
_________ .__              .___            .___             
\_   ___ \|__| ____   ____ |   | ____    __| _/____ ___  ___
/    \  \/|  |/    \_/ __ \|   |/    \  / __ |/ __ \\  \/  /
\     \___|  |   |  \  ___/|   |   |  \/ /_/ \  ___/ >    < 
 \______  /__|___|  /\___  >___|___|  /\____ |\___  >__/\_ \
        \/        \/     \/         \/      \/    \/      \/
""" + Style.RESET_ALL
    print(banner)
    print(Fore.CYAN + "A fast terminal-based media indexer and player for directory-style servers\n")


# ---------- Config setup ----------

def ensure_config_files() -> None:
    created_any = False

    if not ROOTS_JSON.exists():
        demo_roots = [
            {"url": "http://example-server/ftps10/Movies/", "cookie": "", "tag": "FTPS10"}
        ]
        ROOTS_JSON.write_text(json.dumps(demo_roots, indent=2), encoding="utf-8")
        print(Fore.YELLOW + f"[SETUP] Created demo roots.json at {ROOTS_JSON}")
        created_any = True

    if not CONFIG_JSON.exists():
        demo_cfg = {
            "video_extensions": [],
            "blocked_dirs": [],
            "download_dir": "",
            # example mpv args users can fill in:
            "mpv_args": ["--save-position-on-quit", "--watch-later-options=start,volume,mute"],
        }
        CONFIG_JSON.write_text(json.dumps(demo_cfg, indent=2), encoding="utf-8")
        print(Fore.YELLOW + f"[SETUP] Created demo config.json at {CONFIG_JSON}")
        created_any = True

    if created_any:
        print(Fore.GREEN + "Edit roots.json and config.json, then run Build index.\n")


def load_roots_config() -> list[dict]:
    if not ROOTS_JSON.exists():
        return []
    try:
        return json.load(ROOTS_JSON.open("r", encoding="utf-8"))
    except Exception as e:
        print(Fore.RED + f"[ERROR] Failed to load roots.json: {e}")
        return []


def load_config() -> dict:
    if not CONFIG_JSON.exists():
        return {}
    try:
        return json.load(CONFIG_JSON.open("r", encoding="utf-8"))
    except Exception as e:
        print(Fore.RED + f"[ERROR] Failed to load config.json: {e}")
        return {}


def build_root_tag_map() -> dict[str, str]:
    from backend.app.media.crawler import normalize_root_url
    mapping: dict[str, str] = {}
    for r in load_roots_config():
        url = (r.get("url") or "").strip()
        if not url:
            continue
        norm = normalize_root_url(url)
        tag = (r.get("tag") or "").strip()
        if not tag:
            parsed = urlparse(norm)
            path_str = parsed.path.strip("/")
            tag = path_str.split("/")[-1] if path_str else parsed.netloc
        mapping[norm] = tag
    return mapping


# ---------- Playlist helpers (series handling) ----------

def _episode_sort_key(filename: str) -> tuple[int, int, str]:
    m = EPISODE_REGEX.search(filename)
    if m:
        season = int(m.group(1))
        episode = int(m.group(2))
        return (season, episode, filename.lower())
    return (9999, 9999, filename.lower())


def build_dir_playlist(entry: MediaEntry, conn) -> tuple[list[MediaEntry], int]:
    """
    Build a playlist for a directory if it looks like a series (SxxEyy).
    Returns (playlist_entries, start_index). Falls back to single entry.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT url, root, path, filename, size, modified
        FROM media
        WHERE path = ?
        """,
        (entry.path,),
    )
    rows = cur.fetchall()

    playlist: list[MediaEntry] = []
    for r in rows:
        playlist.append(
            MediaEntry(
                url=r["url"],
                root=r["root"],
                path=r["path"],
                filename=r["filename"],
                size=r["size"],
                modified=r["modified"],
            )
        )

    if not playlist:
        return [entry], 0

    ep_like = [e for e in playlist if EPISODE_REGEX.search(e.filename)]
    if len(ep_like) < 2:
        return [entry], 0

    playlist.sort(key=lambda e: _episode_sort_key(e.filename))

    start_index = 0
    for i, e in enumerate(playlist):
        if e.url == entry.url:
            start_index = i
            break

    return playlist, start_index


# ---------- mpv player ----------

def play_entry(entry: MediaEntry, conn) -> None:
    """
    Play a single entry or a series playlist with mpv.
    Honors mpv_args from config.json and loads cineindex-history.lua if present.
    """
    script_path = ROOT / "cineindex-history.lua"
    script_arg = None
    if script_path.exists():
        script_arg = f"--script={script_path.as_posix()}"
    else:
        print(Fore.YELLOW + f"[PLAY] Warning: {script_path} not found; history Lua script will not run.")

    cfg = load_config()
    mpv_args = cfg.get("mpv_args", [])
    if not isinstance(mpv_args, list):
        mpv_args = []

    playlist, start_index = build_dir_playlist(entry, conn)

    # Single item
    if len(playlist) == 1:
        cmd = ["mpv", *mpv_args]
        if script_arg:
            cmd.append(script_arg)
        cmd.append(playlist[0].url)

        print(Fore.CYAN + f"\n[PLAY] Running: " + Fore.YELLOW + " ".join(cmd))
        try:
            subprocess.run(cmd)
        except FileNotFoundError:
            print(Fore.RED + "  !! mpv not found. Make sure it's in PATH or adjust the command.")
        except Exception as e:
            print(Fore.RED + f"  !! Error launching mpv: {e}")
        else:
            print(Fore.GREEN + "[PLAY] mpv exited.\n")
        return

    # Series playlist
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".m3u", mode="w", encoding="utf-8") as f:
            playlist_path = f.name
            for e in playlist:
                f.write(e.url + "\n")
    except Exception as e:
        print(Fore.RED + f"  !! Failed to create playlist file: {e}")
        cmd = ["mpv", *mpv_args]
        if script_arg:
            cmd.append(script_arg)
        cmd.append(entry.url)
        print(Fore.CYAN + f"\n[PLAY] Fallback: " + Fore.YELLOW + " ".join(cmd))
        try:
            subprocess.run(cmd)
        except Exception as e2:
            print(Fore.RED + f"  !! Error launching mpv fallback: {e2}")
        else:
            print(Fore.GREEN + "[PLAY] mpv exited.\n")
        return

    cmd = ["mpv", *mpv_args]
    if script_arg:
        cmd.append(script_arg)
    cmd.append(f"--playlist={playlist_path}")
    cmd.append(f"--playlist-start={start_index}")

    print(Fore.CYAN + f"\n[PLAY] Running: " + Fore.YELLOW + " ".join(cmd))
    try:
        subprocess.run(cmd)
    except FileNotFoundError:
        print(Fore.RED + "  !! mpv not found. Make sure it's in PATH or adjust the command.")
    except Exception as e:
        print(Fore.RED + f"  !! Error launching mpv: {e}")
    finally:
        try:
            os.remove(playlist_path)
        except OSError:
            pass

    print(Fore.GREEN + "[PLAY] mpv exited.\n")


# ---------- aria2c downloader ----------

def download_entry(entry: MediaEntry) -> None:
    """
    Download a media entry using aria2c.
    - Saves as entry.filename under download_dir (config.json). If empty/missing, use ./downloads.
    """
    cfg = load_config()
    dl_dir_val = (cfg.get("download_dir") or "").strip()

    if dl_dir_val:
        dl_dir = Path(dl_dir_val).expanduser()
        if not dl_dir.is_absolute():
            dl_dir = Path.cwd() / dl_dir
    else:
        dl_dir = Path.cwd() / "downloads"

    try:
        dl_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(Fore.RED + f"[DL] Failed to create download directory {dl_dir}: {e}")
        print(Fore.YELLOW + "     Falling back to current working directory.")
        dl_dir = Path.cwd()

    cmd = [
        "aria2c",
        "--continue=true",
        "--max-connection-per-server=4",
        "--split=4",
        "--min-split-size=10M",
        "--dir",
        str(dl_dir),
        "--out",
        entry.filename,
        entry.url,
    ]

    print(Fore.CYAN + f"[DL] Running: " + Fore.YELLOW + " ".join(str(c) for c in cmd))
    try:
        subprocess.run(cmd)
    except FileNotFoundError:
        print(Fore.RED + "  !! aria2c not found. Make sure it's in PATH.")
    except Exception as e:
        print(Fore.RED + f"  !! Error launching aria2c: {e}")
    else:
        print(Fore.GREEN + f"[DL] Finished: {dl_dir / entry.filename}\n")


# ---------- Index operations ----------

def build_index() -> None:
    print(Fore.CYAN + "[BUILD] Starting full index build...")
    init_db()
    roots_raw = load_roots_config()
    if not roots_raw:
        print(Fore.RED + "[BUILD] No roots configured in roots.json.\n")
        return
    cfg_raw = load_config()
    root_cfgs = load_root_configs(roots_raw, ROOT)
    crawl_cfg = load_crawl_config(cfg_raw)
    conn = get_conn()
    try:
        for rc in root_cfgs:
            crawl_root(rc, crawl_cfg, conn=conn, incremental=False)
    finally:
        conn.close()
    print(Fore.GREEN + "[BUILD] Done.\n")


def update_index() -> None:
    print(Fore.CYAN + "[UPDATE] Checking modified roots...")
    init_db()
    roots_raw = load_roots_config()
    if not roots_raw:
        print(Fore.RED + "[UPDATE] No roots configured in roots.json.\n")
        return
    cfg_raw = load_config()
    root_cfgs = load_root_configs(roots_raw, ROOT)
    crawl_cfg = load_crawl_config(cfg_raw)
    conn = get_conn()
    try:
        for rc in root_cfgs:
            crawl_root(rc, crawl_cfg, conn=conn, incremental=True)
    finally:
        conn.close()
    print(Fore.GREEN + "[UPDATE] Done.\n")


def show_stats() -> None:
    print(Fore.CYAN + "[STATS] Gathering database stats...")
    init_db()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dirs")
        dirs_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM media")
        media_count = cur.fetchone()[0]
        print(Fore.MAGENTA + f"\n=== CineIndex Stats ===")
        print(Fore.GREEN + f"Directories: {dirs_count}")
        print(Fore.GREEN + f"Media Files: {media_count}\n")
    finally:
        conn.close()


# ---------- Search ----------

def search_index() -> None:
    init_db()
    conn = get_conn()
    try:
        print(Fore.CYAN + "\n[SEARCH] Loading media entries...")
        entries = load_media_entries(conn)
        print(Fore.GREEN + f"[SEARCH] Loaded {len(entries)} entries.\n")
        if not entries:
            print(Fore.YELLOW + "Build the index first.\n")
            return

        choices = build_choice_list(entries)
        root_tags = build_root_tag_map()
        print(Fore.CYAN + "Type a search query (ENTER to return):\n")

        while True:
            pattern = input(Fore.YELLOW + "CineIndex search> ").strip()
            if not pattern:
                print()
                return

            results = search_media(pattern, entries=entries, choices=choices, limit=50, score_cutoff=40)
            if not results:
                print(Fore.RED + "  No matches.\n")
                continue

            print()
            for i in reversed(range(len(results))):
                entry, score = results[i]
                num = i + 1
                color = Fore.GREEN if i % 2 == 0 else Fore.CYAN
                display_root = root_tags.get(entry.root, entry.root)
                print(color + f"{num:2d}. {entry.filename} (score {score:.1f})")
                print(Fore.YELLOW + f"    [{display_root}] {entry.path}")
                if i != 0:
                    print(separator_line())

            print()
            while True:
                sel = input(Fore.CYAN + "Select number to play (ENTER to search again): ").strip()
                if not sel:
                    print()
                    break
                if not sel.isdigit():
                    print(Fore.RED + "  Invalid selection.\n")
                    continue
                num = int(sel)
                if not (1 <= num <= len(results)):
                    print(Fore.RED + "  Out of range.\n")
                    continue
                entry, _ = results[num - 1]
                play_entry(entry, conn)
                break
    finally:
        conn.close()


# ---------- History ----------

def show_history() -> None:
    init_db()
    conn = get_conn()
    try:
        history = get_recent_history(conn)
        if not history:
            print(Fore.YELLOW + "\n[HISTORY] No watch history yet.\n")
            return

        root_tags = build_root_tag_map()
        print(Fore.MAGENTA + "\n=== CineIndex Watch History (last 50) ===\n")

        for i in reversed(range(len(history))):
            entry, played_at = history[i]
            num = i + 1
            color = Fore.GREEN if i % 2 == 0 else Fore.CYAN
            display_root = root_tags.get(entry.root, entry.root)
            print(color + f"{num:2d}. {entry.filename}")
            print(Fore.YELLOW + f"    [{display_root}] {entry.path}")
            print(Fore.CYAN + f"    Played at: {played_at}")
            if i != 0:
                print(separator_line())

        print()
        while True:
            sel = input(Fore.CYAN + "Select number to play (ENTER to return): ").strip()
            if not sel:
                print()
                break
            if not sel.isdigit():
                print(Fore.RED + "  Invalid selection.\n")
                continue
            num = int(sel)
            if not (1 <= num <= len(history)):
                print(Fore.RED + "  Out of range.\n")
                continue
            entry, _ = history[num - 1]
            play_entry(entry, conn)
            break
    finally:
        conn.close()


# ---------- Download (aria2) ----------

def download_index() -> None:
    init_db()
    conn = get_conn()
    try:
        print(Fore.CYAN + "\n[DL] Loading media entries...")
        entries = load_media_entries(conn)
        print(Fore.GREEN + f"[DL] Loaded {len(entries)} entries.\n")
        if not entries:
            print(Fore.YELLOW + "No media indexed yet. Build the index first.\n")
            return

        choices = build_choice_list(entries)
        root_tags = build_root_tag_map()
        print(Fore.CYAN + "Type a search pattern (ENTER to return):\n")

        while True:
            pattern = input(Fore.YELLOW + "CineIndex download> ").strip()
            if not pattern:
                print()
                return

            results = search_media(pattern, entries=entries, choices=choices, limit=50, score_cutoff=40)
            if not results:
                print(Fore.RED + "  No matches.\n")
                continue

            print()
            for i in reversed(range(len(results))):
                entry, score = results[i]
                num = i + 1
                color = Fore.GREEN if i % 2 == 0 else Fore.CYAN
                display_root = root_tags.get(entry.root, entry.root)
                print(color + f"{num:2d}. {entry.filename} (score {score:.1f})")
                print(Fore.YELLOW + f"    [{display_root}] {entry.path}")
                if i != 0:
                    print(separator_line())
            print()

            sel = input(Fore.CYAN + "Select numbers to download (comma or space separated, ENTER to new search): ").strip()
            if not sel:
                print()
                continue

            # parse comma/space separated numbers, dedupe
            nums = {int(x) for x in re.findall(r"\d+", sel)}
            if not nums:
                print(Fore.RED + "  No valid numbers.\n")
                continue

            for num in sorted(nums):
                if 1 <= num <= len(results):
                    entry, _ = results[num - 1]
                    download_entry(entry)
                else:
                    print(Fore.RED + f"  Out of range: {num}")
            print()
    finally:
        conn.close()


# ---------- Menu ----------

def print_menu() -> None:
    print(Fore.MAGENTA + Style.BRIGHT + "\n=== CineIndex TUI ===\n")
    print(Fore.YELLOW + "1." + Style.RESET_ALL + " Build index (full crawl)")
    print(Fore.YELLOW + "2." + Style.RESET_ALL + " Update index (incremental)")
    print(Fore.YELLOW + "3." + Style.RESET_ALL + " Show stats")
    print(Fore.YELLOW + "4." + Style.RESET_ALL + " Stream (mpv)")
    print(Fore.YELLOW + "5." + Style.RESET_ALL + " Watch history")
    print(Fore.YELLOW + "6." + Style.RESET_ALL + " Download (aria2)")
    print(Fore.YELLOW + "0." + Style.RESET_ALL + " Quit")


def main() -> None:
    print_banner()
    ensure_config_files()

    while True:
        print_menu()
        choice = input(Fore.CYAN + "\nSelect an option: ").strip()
        if choice == "1":
            build_index()
        elif choice == "2":
            update_index()
        elif choice == "3":
            show_stats()
        elif choice == "4":
            search_index()
        elif choice == "5":
            show_history()
        elif choice == "6":
            download_index()
        elif choice in ("0", ""):
            print(Fore.YELLOW + "\n👋 Bye!\n")
            break
        else:
            print(Fore.RED + "\nInvalid choice.\n")


if __name__ == "__main__":
    main()
