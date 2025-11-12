from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import os
import re
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

# Initialize colorama
init(autoreset=True)

CONFIG_JSON = ROOT / "config.json"
ROOTS_JSON = ROOT / "roots.json"

EPISODE_REGEX = re.compile(r"[sS](\d{1,2})[ ._-]*[eE](\d{1,3})")

# ---------- Banner & setup ----------

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


def ensure_config_files() -> None:
    created_any = False

    if not ROOTS_JSON.exists():
        demo_roots = [
            {
                "url": "http://example-server/ftps10/Movies/",
                "cookie": "",
                "tag": "FTPS10",
            }
        ]
        ROOTS_JSON.write_text(json.dumps(demo_roots, indent=2), encoding="utf-8")
        print(Fore.YELLOW + f"[SETUP] Created demo roots.json at {ROOTS_JSON}")
        print("        Edit this file and put your actual root URLs.\n")
        created_any = True

    if not CONFIG_JSON.exists():
        demo_cfg = {"video_extensions": [], "blocked_dirs": [], "download_dir": ""}
        CONFIG_JSON.write_text(json.dumps(demo_cfg, indent=2), encoding="utf-8")
        print(Fore.YELLOW + f"[SETUP] Created demo config.json at {CONFIG_JSON}")
        print("        Edit this file and set video_extensions, blocked_dirs, and optional download_dir.\n")
        created_any = True

    if created_any:
        print(Fore.GREEN + "Edit roots.json and config.json, then run Build index.\n")


def load_roots_config() -> list[dict]:
    if not ROOTS_JSON.exists():
        return []
    try:
        with ROOTS_JSON.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception as e:
        print(Fore.RED + f"[ERROR] Failed to load roots.json: {e}")
    return []


def load_config() -> dict:
    if not CONFIG_JSON.exists():
        return {}
    try:
        with CONFIG_JSON.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(Fore.RED + f"[ERROR] Failed to load config.json: {e}")
        return {}


def build_root_tag_map() -> dict[str, str]:
    from backend.app.media.crawler import normalize_root_url
    roots_raw = load_roots_config()
    mapping: dict[str, str] = {}

    for r in roots_raw:
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


# ---------- Index operations ----------

def build_index() -> None:
    print(Fore.CYAN + "[BUILD] Starting full index build...")
    init_db()
    roots_raw = load_roots_config()
    cfg_raw = load_config()
    if not roots_raw:
        print(Fore.RED + "[BUILD] No roots configured in roots.json.\n")
        return
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
    cfg_raw = load_config()
    if not roots_raw:
        print(Fore.RED + "[UPDATE] No roots configured in roots.json.\n")
        return
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


# ---------- Main loop ----------

def print_menu() -> None:
    print(Fore.MAGENTA + Style.BRIGHT + "\n=== CineIndex TUI ===")
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
        choice = input(Fore.CYAN + "Select an option: ").strip()

        if choice == "1":
            build_index()
        elif choice == "2":
            update_index()
        elif choice == "3":
            show_stats()
        elif choice == "4":
            from backend.main import search_index
            search_index()
        elif choice == "5":
            from backend.main import show_history
            show_history()
        elif choice == "6":
            from backend.main import download_index
            download_index()
        elif choice == "0" or choice == "":
            print(Fore.YELLOW + "👋 Bye!\n")
            break
        else:
            print(Fore.RED + "Invalid choice.\n")


if __name__ == "__main__":
    main()
