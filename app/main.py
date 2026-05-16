from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse, unquote
from typing import Callable, TypeVar

from colorama import Fore, Style, init

from app.db import init_db, get_conn, CONFIG_DIR, DATA_DIR
from app.media.crawler import (
    load_root_configs,
    load_crawl_config,
    crawl_root,
)
from app.media.search import (
    load_media_entries,
    build_choice_list,
    search_media,
    MediaEntry,
)
from app.media.history import get_recent_history

# Path to this module's directory (the installed package location)
HERE = Path(__file__).resolve().parent

init(autoreset=True)

# Config files now live in an OS-appropriate config directory:
# - Linux:  ~/.config/CineIndex/
# - macOS:  ~/Library/Application Support/CineIndex/
# - Windows: %LOCALAPPDATA%\CineIndex\CineIndex\
CONFIG_JSON = CONFIG_DIR / "config.json"
ROOTS_JSON = CONFIG_DIR / "roots.json"

EPISODE_REGEX = re.compile(r"[sS](\d{1,2})[ ._-]*[eE](\d{1,3})")

# Patterns where dots should NOT be converted to spaces (e.g., audio formats, video bitrates)
DOT_BLOCKLIST_PATTERNS = [
    r"\d+\.\d+",  # e.g., "5.1" (audio), "2.0" (stereo), "1080.60" (framerate)
]

T = TypeVar("T")


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
    print(
        Fore.CYAN
        + "A fast terminal-based media indexer and player for directory-style servers\n"
    )


# ---------- Config setup ----------


def ensure_config_files() -> None:
    created_any = False

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if not ROOTS_JSON.exists():
        demo_roots = [
            {
                "url": "http://example-server/movies/",
                "tag": "Movies",
                "decode_percent": True,
                "dots_to_spaces": False,
            }
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
            "mpv_args": [
                "--save-position-on-quit",
                "--fullscreen",
                "--watch-later-options=start",
            ],
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
    from app.media.crawler import normalize_root_url

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


def build_root_presentation_map() -> dict[str, dict]:
    """Build a map of per-root presentation options (dots_to_spaces, etc.)."""
    from app.media.crawler import normalize_root_url

    mapping: dict[str, dict] = {}
    for r in load_roots_config():
        url = (r.get("url") or "").strip()
        if not url:
            continue
        norm = normalize_root_url(url)
        mapping[norm] = {
            "dots_to_spaces": r.get("dots_to_spaces", False),
        }
    return mapping


def _fzf_binary() -> str | None:
    return shutil.which("fzf") or shutil.which("fzf.exe")


def _tree_path_parts(path: str, decode_percent: bool = True) -> list[str]:
    if path == "/" or not path:
        return []
    parts = [part for part in path.strip("/").split("/") if part]
    if decode_percent:
        return [unquote(part) for part in parts]
    return parts


def _tree_init() -> dict:
    return {"children": {}, "files": []}


def _pretty_filename(fname: str, dots_to_spaces: bool = False) -> str:
    """
    Present filenames nicely: optionally replace dots used as word separators with spaces,
    but preserve the file extension (last dot) and any dots in blocklisted patterns.
    """
    if not fname:
        return fname
    if "." not in fname:
        return fname

    # Split on last dot to separate extension
    parts = fname.rsplit(".", 1)
    name, ext = parts[0], parts[1]

    if not dots_to_spaces:
        return fname

    # Find all blocklisted patterns and mark their positions
    blocked_ranges = set()
    for pattern in DOT_BLOCKLIST_PATTERNS:
        for match in re.finditer(pattern, name):
            for i in range(match.start(), match.end()):
                blocked_ranges.add(i)

    # Replace dots with spaces, except for dots in blocked ranges
    result = []
    for i, char in enumerate(name):
        if char == "." and i not in blocked_ranges:
            result.append(" ")
        else:
            result.append(char)

    display = "".join(result)
    # Clean up multiple spaces (from adjacent dots or replaced dots)
    display = " ".join([p for p in display.split() if p]) or name
    return f"{display}.{ext}"


def _tree_add_file(
    tree: dict,
    rel_path: str,
    filename: str,
    *,
    decode_percent: bool = True,
    dots_to_spaces: bool = False,
) -> None:
    node = tree
    for part in _tree_path_parts(rel_path, decode_percent=decode_percent):
        node = node["children"].setdefault(part, _tree_init())
    files: list[str] = node["files"]
    # Store the raw filename; prettification is applied at render time.
    if filename not in files:
        files.append(filename)


def _tree_render_node(
    node: dict,
    *,
    prefix: str = "",
    dots_to_spaces: bool = False,
) -> None:
    children = list(node["children"].items())
    files = list(node["files"])
    items: list[tuple[str, str, dict | None]] = [
        ("dir", name, child) for name, child in children
    ] + [("file", name, None) for name in files]

    for index, (kind, name, child) in enumerate(items):
        is_last = index == len(items) - 1
        connector = "└── " if is_last else "├── "
        if kind == "dir":
            print(Fore.CYAN + prefix + connector + name)
            next_prefix = prefix + ("    " if is_last else "│   ")
            _tree_render_node(
                child or _tree_init(), prefix=next_prefix, dots_to_spaces=dots_to_spaces
            )
        else:
            display_name = _pretty_filename(name, dots_to_spaces=dots_to_spaces)
            print(Fore.GREEN + prefix + connector + display_name)


def _tree_render_root(
    root_tag: str, tree: dict, *, dots_to_spaces: bool = False
) -> None:
    print(Fore.MAGENTA + root_tag)
    _tree_render_node(tree, prefix="", dots_to_spaces=dots_to_spaces)


def _crawl_root_with_tree(
    rc,
    *,
    crawl_cfg: dict,
    conn,
    root_tag_map: dict[str, str],
    incremental: bool,
    max_per_root: int,
) -> tuple[int, int, int, int, float, int]:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM dirs WHERE root = ?", (rc.url,))
    before_dirs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM media WHERE root = ?", (rc.url,))
    before_media = cur.fetchone()[0]

    live_state: dict = {
        "tree": _tree_init(),
        "printed_count": 0,
        "suppressed": 0,
    }

    def _on_new_file(_root_url: str, rel_path: str, fname: str) -> None:
        try:
            if max_per_root == 0 or live_state["printed_count"] < max_per_root:
                display_fname = (
                    unquote(fname) if getattr(rc, "decode_percent", True) else fname
                )
                _tree_add_file(
                    live_state["tree"],
                    rel_path,
                    display_fname,
                    decode_percent=getattr(rc, "decode_percent", True),
                    dots_to_spaces=getattr(rc, "dots_to_spaces", False),
                )
                live_state["printed_count"] += 1
            else:
                live_state["suppressed"] += 1
        except Exception:
            pass

    result = crawl_root(
        rc,
        crawl_cfg,
        conn=conn,
        incremental=incremental,
        summary_only=True,
        on_new_file=_on_new_file,
    )

    if live_state["printed_count"] > 0:
        print()
        _tree_render_root(
            root_tag_map.get(rc.url, rc.url),
            live_state["tree"],
            dots_to_spaces=getattr(rc, "dots_to_spaces", False),
        )

    cur.execute("SELECT COUNT(*) FROM dirs WHERE root = ?", (rc.url,))
    after_dirs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM media WHERE root = ?", (rc.url,))
    after_media = cur.fetchone()[0]

    return (
        before_dirs,
        after_dirs,
        before_media,
        after_media,
        result.elapsed_seconds,
        int(live_state["suppressed"]),
    )


def _change_text(before: int, after: int, noun: str) -> str:
    delta = after - before
    if delta < 0:
        return f"{noun}={before}→{after} (-{delta})"
    if delta > 0:
        return f"{noun}={before}→{after} (+{delta})"
    return f"{noun}={before}→{after}"


def _render_numbered_items(
    items: list[T],
    render_item: Callable[[int, T], list[str]],
) -> None:
    print()
    for i in reversed(range(len(items))):
        for line in render_item(i, items[i]):
            print(line)
        if i != 0:
            print(separator_line())
    print()


def _truncate_text(text: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return "." * max_len
    return text[: max_len - 3] + "..."


def _dir_label_from_path(path: str) -> str:
    if path == "/":
        return "/"
    return unquote(path.strip("/").split("/")[-1])


def _ansi_rgb(
    text: str, r: int, g: int, b: int, *, bold: bool = False, dim: bool = False
) -> str:
    # Truecolor ANSI style for fzf rows (--ansi). Uses a muted Nord-like palette.
    attrs: list[str] = []
    if bold:
        attrs.append("1")
    if dim:
        attrs.append("2")
    attrs.append(f"38;2;{r};{g};{b}")
    prefix = "\x1b[" + ";".join(attrs) + "m"
    return f"{prefix}{text}{Style.RESET_ALL}"


def _fzf_tag(
    label: str,
    *,
    width: int,
    color_rgb: tuple[int, int, int],
    bold: bool = False,
    dim: bool = False,
) -> str:
    clean = _truncate_text(label, width).ljust(width)
    return _ansi_rgb(
        clean, color_rgb[0], color_rgb[1], color_rgb[2], bold=bold, dim=dim
    )


def _fzf_media_text(
    entry: MediaEntry,
    root_tags: dict[str, str],
    root_presentation: dict[str, dict] | None = None,
    *,
    file_width: int = 80,
) -> str:
    if root_presentation is None:
        root_presentation = {}
    opts = root_presentation.get(entry.root, {})
    dots_to_spaces = opts.get("dots_to_spaces", False)

    file_text = _pretty_filename(entry.filename, dots_to_spaces=dots_to_spaces)
    return file_text


def _fzf_history_text(
    item: tuple[MediaEntry, str],
    root_tags: dict[str, str],
    root_presentation: dict[str, dict] | None = None,
) -> str:
    if root_presentation is None:
        root_presentation = {}
    entry, played_at = item
    opts = root_presentation.get(entry.root, {})
    dots_to_spaces = opts.get("dots_to_spaces", False)

    file_text = _pretty_filename(entry.filename) if dots_to_spaces else entry.filename
    dir_label = _dir_label_from_path(entry.path)
    dir_text = _ansi_rgb(dir_label, 136, 192, 208, bold=True)
    root_label = root_tags.get(entry.root, entry.root)
    root_text = _ansi_rgb(root_label, 94, 129, 172)
    played_text = _ansi_rgb(played_at, 76, 86, 106)
    return f"{file_text}    {dir_text}    {played_text}    {root_text}"


def _fzf_preview_text(entry: MediaEntry, all_entries: list[MediaEntry]) -> str:
    """
    Generate preview text for fzf selection.
    If entry is a TV show episode, show other episodes in the same season.
    Otherwise show file details.
    """
    filename = entry.filename
    m = EPISODE_REGEX.search(filename)

    if m:
        # It's an episode; show other episodes in the same season
        season = int(m.group(1))
        episode = int(m.group(2))

        # Find all episodes in the same directory and season
        same_dir = [
            e for e in all_entries if e.root == entry.root and e.path == entry.path
        ]
        same_season = [e for e in same_dir if EPISODE_REGEX.search(e.filename)]
        same_season.sort(key=lambda e: _episode_sort_key(e.filename))

        # Build output
        lines = [
            Fore.MAGENTA + f"Season {season} Episodes:",
            Fore.RESET,
        ]
        for ep in same_season:
            ep_m = EPISODE_REGEX.search(ep.filename)
            if ep_m and int(ep_m.group(1)) == season:
                ep_num = int(ep_m.group(2))
                marker = Fore.GREEN + "→ " if ep.filename == filename else "  "
                lines.append(f"{marker}E{ep_num:02d}: {ep.filename}")

        return "\n".join(lines)
    else:
        # Not an episode; show directory info
        return f"{Fore.CYAN}Path:{Fore.RESET} {entry.path}\n{Fore.CYAN}File:{Fore.RESET} {entry.filename}"


def _pick_with_fzf(
    items: list[T],
    item_to_text: Callable[[T], str],
    *,
    multi: bool = False,
    prompt: str = "Search: ",
    initial_query: str | None = None,
    preview_func: Callable[[T], str] | None = None,
    all_entries: list[T] | None = None,
    root_presentation: dict[str, dict] | None = None,
) -> tuple[list[T], str]:
    fzf_bin = _fzf_binary()
    if not fzf_bin or not items:
        return [], initial_query or ""

    index_lookup: dict[str, T] = {}
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}\t{item_to_text(item)}")
        index_lookup[str(index)] = item

    cmd = [
        fzf_bin,
        "--ansi",
        "--delimiter",
        "\t",
        "--with-nth",
        "2,3,4",
        "--prompt",
        prompt,
        "--height",
        "70%",
        "--border",
        "--layout=reverse",
    ]
    if multi:
        cmd.append("--multi")

    # Prepare preview if function provided
    preview_file = None
    preview_script = None
    if preview_func and all_entries:
        try:
            with tempfile.NamedTemporaryFile(
                delete=False, mode="w", encoding="utf-8", suffix=".json"
            ) as pf:
                # Serialize all entries for the preview script
                entries_data = []
                for entry in all_entries:
                    if isinstance(entry, MediaEntry):
                        data_item = {
                            "filename": entry.filename,
                            "root": entry.root,
                            "path": entry.path,
                            "url": entry.url,
                        }
                        # Include dots_to_spaces setting for this root
                        if root_presentation:
                            opts = root_presentation.get(entry.root, {})
                            data_item["dots_to_spaces"] = opts.get(
                                "dots_to_spaces", False
                            )
                        entries_data.append(data_item)
                    elif isinstance(entry, tuple) and len(entry) >= 1:
                        # Handle tuples like (MediaEntry, score) or (MediaEntry, timestamp)
                        if isinstance(entry[0], MediaEntry):
                            e = entry[0]
                            data_item = {
                                "filename": e.filename,
                                "root": e.root,
                                "path": e.path,
                                "url": e.url,
                            }
                            # Include dots_to_spaces setting for this root
                            if root_presentation:
                                opts = root_presentation.get(e.root, {})
                                data_item["dots_to_spaces"] = opts.get(
                                    "dots_to_spaces", False
                                )
                            # Include timestamp (e.g., played_at from history) if available
                            if len(entry) >= 2:
                                data_item["timestamp"] = str(entry[1])
                            entries_data.append(data_item)
                json.dump(entries_data, pf)
                preview_file = pf.name

            with tempfile.NamedTemporaryFile(
                delete=False, mode="w", encoding="utf-8", suffix=".py"
            ) as ps:
                # Convert path to use forward slashes for cross-platform compatibility
                preview_file_path = Path(preview_file).as_posix()
                ps.write(f"""
import json
import os
import sys
import re
from pathlib import Path
from urllib.parse import unquote
from datetime import datetime

# Reconstruct the preview function logic inline
EPISODE_REGEX = re.compile(r'[sS](\\d{{1,2}})[ ._-]*[eE](\\d{{1,3}})')
DOT_BLOCKLIST_PATTERNS = [
    r'\\d+\\.\\d+',
]

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

try:
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

def pretty_filename(fname, dots_to_spaces=False):
    if not fname or '.' not in fname:
        return fname
    if not dots_to_spaces:
        return fname
    
    parts = fname.rsplit('.', 1)
    name, ext = parts[0], parts[1]
    
    # Find blocklisted pattern positions
    blocked_ranges = set()
    for pattern in DOT_BLOCKLIST_PATTERNS:
        try:
            for match in re.finditer(pattern, name):
                for i in range(match.start(), match.end()):
                    blocked_ranges.add(i)
        except Exception:
            pass
    
    # Replace dots with spaces, except in blocked ranges
    result = []
    for i, char in enumerate(name):
        if char == '.' and i not in blocked_ranges:
            result.append(' ')
        else:
            result.append(char)
    
    display = ''.join(result)
    display = ' '.join([p for p in display.split() if p]) or name
    return f'{{display}}.{{ext}}'

def episode_sort_key(filename):
    m = EPISODE_REGEX.search(filename)
    if m:
        season = int(m.group(1))
        episode = int(m.group(2))
        return (season, episode, filename.lower())
    return (9999, 9999, filename.lower())

entries_data = json.load(open(r'{preview_file_path}'))

line_text = sys.argv[1] if len(sys.argv) > 1 else ''
line_index = line_text.split('\\t', 1)[0].strip()

if not line_index.isdigit():
    sys.exit(1)

entry_index = int(line_index)

if entry_index < 1 or entry_index > len(entries_data):
    sys.exit(1)

entry_data = entries_data[entry_index - 1]
filename = entry_data['filename']
display_path = unquote(entry_data['path'])
dots_to_spaces = entry_data.get('dots_to_spaces', False)
display_filename = pretty_filename(unquote(entry_data['filename']), dots_to_spaces=dots_to_spaces)
display_root = unquote(entry_data['root'])

# Check if it's an episode
m = EPISODE_REGEX.search(filename)
if m:
    season = int(m.group(1))
    # Find all episodes in same directory (just use the entries as proxy)
    same_season = [e for e in entries_data if e['path'] == entry_data['path']]
    same_season = [e for e in same_season if EPISODE_REGEX.search(e['filename'])]
    same_season.sort(key=lambda e: episode_sort_key(e['filename']))
    
    print(f"Root: {{display_root}}")
    print(f"Path: {{display_path}}")
    print(f"File: {{display_filename}}")
    print()
    print(f"Season {{season}} Episodes:")
    for ep in same_season:
        ep_m = EPISODE_REGEX.search(ep['filename'])
        if ep_m and int(ep_m.group(1)) == season:
            ep_num = int(ep_m.group(2))
            marker = ">" if ep['filename'] == filename else " "
            ep_dots_to_spaces = ep.get('dots_to_spaces', False)
            ep_display = pretty_filename(unquote(ep['filename']), dots_to_spaces=ep_dots_to_spaces)
            print(f"{{marker}} E{{ep_num:02d}}: {{ep_display}}")
else:
    print(f"Root: {{display_root}}")
    print(f"Path: {{display_path}}")
    print(f"File: {{display_filename}}")
    
    # Show timestamp if present (e.g., played_at for history)
    if "timestamp" in entry_data:
        try:
            ts_str = entry_data['timestamp']
            dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
            human_readable = dt.strftime('%a, %b %d %Y at %I:%M %p')
            print(f"Timestamp: {{human_readable}}")
        except Exception:
            try:
                for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S']:
                    try:
                        dt = datetime.strptime(ts_str.split('.')[0], fmt)
                        human_readable = dt.strftime('%a, %b %d %Y at %I:%M %p')
                        print(f"Timestamp: {{human_readable}}")
                        break
                    except Exception:
                        pass
                else:
                    print(f"Timestamp: {{ts_str}}")
            except Exception:
                print(f"Timestamp: {{ts_str}}")
""")
                preview_script = ps.name

            # Add preview command; the script reads the selected row from argv.
            cmd.extend(["--preview", f"python {preview_script} {{}}"])
        except Exception:
            pass  # Silently skip preview on error

    input_file = None
    output_file = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, mode="w", encoding="utf-8", suffix=".txt"
        ) as input_handle:
            input_handle.write("\n".join(lines) + "\n")
            input_file = input_handle.name

        with tempfile.NamedTemporaryFile(
            delete=False, mode="w", encoding="utf-8", suffix=".txt"
        ) as output_handle:
            output_file = output_handle.name

        # Use --print-query so fzf prints the current query as the first line of
        # output; this lets us restore it when re-opening the picker.
        query_part = f'--query "{initial_query}" ' if initial_query else ""
        print_query = "--print-query "
        multi_part = "--multi " if multi else ""
        preview_part = ""
        if preview_script:
            # Escape backslashes for Windows shell and add preview with toggle binding
            preview_script_escaped = preview_script.replace("\\", "\\\\")
            preview_part = f'--preview "python {preview_script_escaped} {{}}" --preview-window=hidden,wrap --bind "?:toggle-preview" '

        redirect_cmd = (
            f'"{fzf_bin}" --ansi --delimiter "\t" --with-nth "2,3,4" '
            f'--prompt "{prompt}" --height 70% --border --layout=reverse '
            + query_part
            + print_query
            + multi_part
            + preview_part
            + f'< "{input_file}" > "{output_file}"'
        )

        proc = subprocess.run(redirect_cmd, shell=True)
        if proc.returncode != 0:
            return [], initial_query or ""

        try:
            selected_text = Path(output_file).read_text(encoding="utf-8")
        except OSError:
            return [], initial_query or ""

        if not selected_text.strip():
            return [], initial_query or ""
    except Exception as e:
        print(
            Fore.YELLOW
            + f"[SEARCH] fzf unavailable, falling back to the built-in picker: {e}"
        )
        return [], initial_query or ""
    finally:
        for temp_path in (input_file, output_file, preview_file, preview_script):
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    # The first line is the printed query (from --print-query). Remaining lines
    # are the selected items. Parse and return both.
    lines_out = selected_text.splitlines()
    last_query = lines_out[0] if lines_out else ""
    selected: list[T] = []
    seen: set[str] = set()
    for line in lines_out[1:]:
        idx = line.split("\t", 1)[0].strip()
        if not idx or idx in seen:
            continue
        if idx not in index_lookup:
            continue
        seen.add(idx)
        selected.append(index_lookup[idx])

    return selected, last_query


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

    The Lua script, when loaded, writes JSONL history to a log file whose path is
    communicated via the CINEINDEX_HISTORY_PATH environment variable.
    """
    script_path = HERE / "cineindex-history.lua"
    script_arg = None
    if script_path.exists():
        script_arg = f"--script={script_path.as_posix()}"
    else:
        print(
            Fore.YELLOW
            + f"[PLAY] Warning: {script_path} not found; history Lua script will not run."
        )

    # History log path in the data directory (not hidden, since it's in app data)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    history_file = DATA_DIR / "cineindex-mpv-events.log"

    # Prepare environment for mpv, injecting history path
    env = os.environ.copy()
    env["CINEINDEX_HISTORY_PATH"] = str(history_file)

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
            subprocess.run(cmd, env=env)
        except FileNotFoundError:
            print(
                Fore.RED
                + "  !! mpv not found. Make sure it's in PATH or adjust the command."
            )
        except Exception as e:
            print(Fore.RED + f"  !! Error launching mpv: {e}")
        else:
            print(Fore.GREEN + "[PLAY] mpv exited.\n")
        return

    # Series playlist
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".m3u", mode="w", encoding="utf-8"
        ) as f:
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
            subprocess.run(cmd, env=env)
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
        subprocess.run(cmd, env=env)
    except FileNotFoundError:
        print(
            Fore.RED
            + "  !! mpv not found. Make sure it's in PATH or adjust the command."
        )
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
        print(
            Fore.RED + f"[DOWNLOAD] Failed to create download directory {dl_dir}: {e}"
        )
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

    print(
        Fore.CYAN
        + f"[DOWNLOAD] Running: "
        + Fore.YELLOW
        + " ".join(str(c) for c in cmd)
    )
    try:
        subprocess.run(cmd)
    except FileNotFoundError:
        print(Fore.RED + "  !! aria2c not found. Make sure it's in PATH.")
    except Exception as e:
        print(Fore.RED + f"  !! Error launching aria2c: {e}")
    else:
        print(Fore.GREEN + f"[DOWNLOAD] Finished: {dl_dir / entry.filename}\n")


# ---------- Root purge helper ----------


def purge_deleted_roots(conn, active_root_urls: set[str]) -> tuple[int, int]:
    """
    Remove all media/dirs rows whose 'root' is not present in roots.json anymore.
    """
    cur = conn.cursor()
    existing_roots: set[str] = set()

    # Collect distinct roots from dirs
    cur.execute("SELECT DISTINCT root FROM dirs WHERE root IS NOT NULL AND root <> ''")
    for (root_val,) in cur.fetchall():
        existing_roots.add(root_val)

    # Collect distinct roots from media
    cur.execute("SELECT DISTINCT root FROM media WHERE root IS NOT NULL AND root <> ''")
    for (root_val,) in cur.fetchall():
        existing_roots.add(root_val)

    to_remove = existing_roots - active_root_urls
    if not to_remove:
        return 0, 0

    print(Fore.YELLOW + "[CLEAN] Removing roots no longer present in roots.json:")
    removed_dirs = 0
    removed_media = 0
    for root in sorted(to_remove):
        print(Fore.YELLOW + f"  - {root}")
        cur.execute("SELECT COUNT(*) FROM dirs WHERE root = ?", (root,))
        removed_dirs += cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM media WHERE root = ?", (root,))
        removed_media += cur.fetchone()[0]
        cur.execute("DELETE FROM media WHERE root = ?", (root,))
        cur.execute("DELETE FROM dirs  WHERE root = ?", (root,))

    conn.commit()
    print()
    return removed_dirs, removed_media


# ---------- Index operations ----------


def build_index() -> None:
    print(Fore.MAGENTA + "\n=== CineIndex Build ===\n")
    print(Fore.CYAN + "[BUILD] Starting full index build...")
    init_db()
    roots_raw = load_roots_config()
    if not roots_raw:
        print(Fore.RED + "[BUILD] No roots configured in roots.json.\n")
        return
    cfg_raw = load_config()
    root_cfgs = load_root_configs(roots_raw)
    crawl_cfg = load_crawl_config(cfg_raw)
    # Determine max per-root live prints (0 = unlimited). Can be set in config.json
    try:
        max_per_root = int(cfg_raw.get("max_per_root", 0) or 0)
    except Exception:
        max_per_root = 0
    # Which roots to actually crawl (respect 'enabled' flag); keep all roots for purge
    crawl_targets = [rc for rc in root_cfgs if getattr(rc, "enabled", True)]
    conn = get_conn()
    try:
        total_roots = len(crawl_targets)
        active_roots = {rc.url for rc in root_cfgs}
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dirs")
        old_dirs_total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM media")
        old_media_total = cur.fetchone()[0]
        purge_deleted_roots(conn, active_roots)

        if not crawl_targets:
            print(
                Fore.YELLOW
                + "No enabled roots to crawl (check 'enabled' in roots.json).\n"
            )
            return

        root_tag_map = build_root_tag_map()
        for index, rc in enumerate(crawl_targets, start=1):
            (
                before_dirs,
                after_dirs,
                before_media,
                after_media,
                elapsed_seconds,
                suppressed,
            ) = _crawl_root_with_tree(
                rc,
                crawl_cfg=crawl_cfg,
                conn=conn,
                root_tag_map=root_tag_map,
                incremental=False,
                max_per_root=max_per_root,
            )

            print(
                Fore.GREEN
                + (
                    f"[BUILD] {index}/{total_roots} done | root={rc.url} | "
                    f"+dirs={after_dirs - before_dirs}, +files={after_media - before_media}, "
                    f"time={elapsed_seconds:.1f}s"
                )
            )

            if suppressed > 0:
                print(Fore.YELLOW + f"  ... +{suppressed} more omitted for this root")

        cur.execute("SELECT COUNT(*) FROM dirs")
        new_dirs_total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM media")
        new_media_total = cur.fetchone()[0]
        print(
            Fore.MAGENTA
            + (
                f"[BUILD] Summary: roots={total_roots}, "
                f"dirs={old_dirs_total}→{new_dirs_total} ({new_dirs_total - old_dirs_total:+}), "
                f"files={old_media_total}→{new_media_total} ({new_media_total - old_media_total:+})\n"
            )
        )
    finally:
        conn.close()


def update_index() -> None:
    print(Fore.MAGENTA + "\n=== CineIndex Update ===\n")
    print(Fore.CYAN + "[UPDATE] Checking modified roots...")
    init_db()
    roots_raw = load_roots_config()
    if not roots_raw:
        print(Fore.RED + "[UPDATE] No roots configured in roots.json.\n")
        return
    cfg_raw = load_config()
    root_cfgs = load_root_configs(roots_raw)
    crawl_cfg = load_crawl_config(cfg_raw)
    try:
        max_per_root = int(cfg_raw.get("max_per_root", 0) or 0)
    except Exception:
        max_per_root = 0
    crawl_targets = [rc for rc in root_cfgs if getattr(rc, "enabled", True)]
    conn = get_conn()
    try:
        total_roots = len(crawl_targets)
        active_roots = {rc.url for rc in root_cfgs}
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dirs")
        old_dirs_total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM media")
        old_media_total = cur.fetchone()[0]
        removed_dirs, removed_media = purge_deleted_roots(conn, active_roots)

        if not crawl_targets:
            print(
                Fore.YELLOW
                + "No enabled roots to crawl (check 'enabled' in roots.json).\n"
            )
            return

        root_tag_map = build_root_tag_map()
        for index, rc in enumerate(crawl_targets, start=1):
            (
                before_dirs,
                after_dirs,
                before_media,
                after_media,
                elapsed_seconds,
                suppressed,
            ) = _crawl_root_with_tree(
                rc,
                crawl_cfg=crawl_cfg,
                conn=conn,
                root_tag_map=root_tag_map,
                incremental=True,
                max_per_root=max_per_root,
            )

            print(
                Fore.GREEN
                + (
                    f"[UPDATE] {index}/{total_roots} done | root={rc.url} | "
                    f"{_change_text(before_dirs, after_dirs, 'dirs')}, "
                    f"{_change_text(before_media, after_media, 'files')}, "
                    f"time={elapsed_seconds:.1f}s"
                )
            )

            if suppressed > 0:
                print(Fore.YELLOW + f"  ... +{suppressed} more omitted for this root")

        cur.execute("SELECT COUNT(*) FROM dirs")
        new_dirs_total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM media")
        new_media_total = cur.fetchone()[0]
        print(
            Fore.MAGENTA
            + (
                f"[UPDATE] Summary: roots={total_roots}, "
                f"dirs={old_dirs_total}→{new_dirs_total} ({new_dirs_total - old_dirs_total:+}), "
                f"files={old_media_total}→{new_media_total} ({new_media_total - old_media_total:+})\n"
            )
        )
    finally:
        conn.close()


def show_stats() -> None:
    print(Fore.MAGENTA + f"\n=== CineIndex Stats ===")
    print(Fore.CYAN + "\n[STATS] Gathering database stats...\n")
    init_db()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dirs")
        dirs_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM media")
        media_count = cur.fetchone()[0]
        print(Fore.GREEN + f"Directories: {dirs_count}")
        print(Fore.GREEN + f"Media Files: {media_count}\n")
    finally:
        conn.close()


# ---------- Search ----------


def search_index() -> None:
    init_db()
    conn = get_conn()
    print(Fore.MAGENTA + "\n=== CineIndex Search ===")
    try:
        print(Fore.CYAN + "\n[SEARCH] Loading media entries...")
        entries = load_media_entries(conn)
        print(Fore.GREEN + f"[SEARCH] Loaded {len(entries)} entries.\n")
        if not entries:
            print(Fore.YELLOW + "Build the index first.\n")
            return

        root_tags = build_root_tag_map()
        root_presentation = build_root_presentation_map()
        last_query: str = ""

        if _fzf_binary():
            print(
                Fore.CYAN
                + "[SEARCH] Using fzf picker. Type to filter, Enter to select, Esc to exit.\n"
            )

            while True:
                picked, last_query = _pick_with_fzf(
                    entries,
                    lambda entry: _fzf_media_text(entry, root_tags, root_presentation),
                    multi=False,
                    prompt="Search: ",
                    initial_query=last_query,
                    preview_func=_fzf_preview_text,
                    all_entries=entries,
                    root_presentation=root_presentation,
                )
                if not picked:
                    print()
                    return
                play_entry(picked[0], conn)

        choices = build_choice_list(entries)

        def render_results(results: list[tuple[MediaEntry, float]]) -> None:
            def _render_row(index: int, row: tuple[MediaEntry, float]) -> list[str]:
                entry, score = row
                num = index + 1
                color = Fore.GREEN if index % 2 == 0 else Fore.CYAN
                display_root = root_tags.get(entry.root, entry.root)
                return [
                    color + f"{num:2d}. {entry.filename} (score {score:.1f})",
                    Fore.YELLOW + f"    [{display_root}] {entry.path}",
                ]

            _render_numbered_items(results, _render_row)

        last_results: list[tuple[MediaEntry, float]] | None = None

        while True:
            # If we have sticky results from a previous play, re-show them first.
            if last_results:
                if _fzf_binary():
                    picked, last_query = _pick_with_fzf(
                        [entry for entry, _score in last_results],
                        lambda entry: _fzf_media_text(
                            entry, root_tags, root_presentation
                        ),
                        multi=False,
                        prompt="Stream> ",
                        initial_query=last_query,
                        preview_func=_fzf_preview_text,
                        all_entries=entries,
                        root_presentation=root_presentation,
                    )
                    if not picked:
                        last_results = None
                        continue
                    play_entry(picked[0], conn)
                    continue

                render_results(last_results)
                # Same selection loop, but returns to query when ENTER is pressed.
                while True:
                    sel = input(
                        Fore.CYAN + "Select number to play (ENTER to search again): "
                    ).strip()
                    if not sel:
                        print()
                        last_results = None  # abandon sticky results; go to new search
                        break
                    if not sel.isdigit():
                        print(Fore.RED + "  Invalid selection.\n")
                        continue
                    num = int(sel)
                    if not (1 <= num <= len(last_results)):
                        print(Fore.RED + "  Out of range.\n")
                        continue
                    entry, _ = last_results[num - 1]
                    play_entry(entry, conn)
                    # After mpv exits, we simply loop and re-render the same list again.

            # New search
            pattern = input(
                Fore.YELLOW + "Type a search query (ENTER to return): "
            ).strip()
            if not pattern:
                print()
                return

            results = search_media(
                pattern,
                entries=entries,
                choices=choices,
                limit=50,
                score_cutoff=40,
            )
            if not results:
                print(Fore.RED + "  No matches.\n")
                last_results = None
                continue

            # Show and enter selection loop; after play, keep results sticky
            last_results = results

            if _fzf_binary():
                picked, last_query = _pick_with_fzf(
                    [entry for entry, _score in results],
                    lambda entry: _fzf_media_text(entry, root_tags, root_presentation),
                    multi=False,
                    prompt="Stream> ",
                    initial_query=last_query,
                    preview_func=_fzf_preview_text,
                    all_entries=entries,
                    root_presentation=root_presentation,
                )
                if not picked:
                    last_results = None
                    continue
                play_entry(picked[0], conn)
                continue

            render_results(results)

            while True:
                sel = input(
                    Fore.CYAN + "Select number to play (ENTER to search again): "
                ).strip()
                if not sel:
                    print()
                    last_results = None  # abandon sticky list, go back to query prompt
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
                # Do not break: we keep showing the same results for more selections

    finally:
        conn.close()


# ---------- History ----------


def show_history() -> None:
    init_db()
    conn = get_conn()
    try:
        history = get_recent_history(conn)
        if not history:
            print(Fore.YELLOW + "\nNo watch history yet. Watch something first.\n")
            return

        root_tags = build_root_tag_map()
        root_presentation = build_root_presentation_map()
        print(Fore.MAGENTA + "\n=== CineIndex Watch History ===\n")

        if _fzf_binary():
            print(
                Fore.CYAN
                + "[SEARCH] Using fzf picker. Type to filter, Enter to select, Esc to exit.\n"
            )
            picked, _ = _pick_with_fzf(
                history,
                lambda item: _fzf_media_text(item[0], root_tags, root_presentation),
                multi=False,
                prompt="Search: ",
                preview_func=lambda item: _fzf_preview_text(
                    item[0], [e[0] for e in history]
                ),
                all_entries=history,
                root_presentation=root_presentation,
            )
            if picked:
                play_entry(picked[0][0], conn)
            print()
            return

        def _render_history_row(index: int, row: tuple[MediaEntry, str]) -> list[str]:
            entry, played_at = row
            num = index + 1
            color = Fore.GREEN if index % 2 == 0 else Fore.CYAN
            display_root = root_tags.get(entry.root, entry.root)
            return [
                color + f"{num:2d}. {entry.filename}",
                Fore.YELLOW + f"    [{display_root}] {entry.path}",
                Fore.CYAN + f"    Played at: {played_at}",
            ]

        _render_numbered_items(history, _render_history_row)

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
    print(Fore.MAGENTA + "\n=== CineIndex Download ===")
    try:
        print(Fore.CYAN + "\n[SEARCH] Loading media entries...")
        entries = load_media_entries(conn)
        print(Fore.GREEN + f"[SEARCH] Loaded {len(entries)} entries.\n")
        if not entries:
            print(Fore.YELLOW + "No media indexed yet. Build the index first.\n")
            return

        root_tags = build_root_tag_map()
        root_presentation = build_root_presentation_map()
        last_query: str = ""

        if _fzf_binary():
            print(
                Fore.CYAN
                + "[SEARCH] Using fzf picker. Type to filter, Tab to mark, Enter to download, Esc to exit.\n"
            )

            while True:
                picked, last_query = _pick_with_fzf(
                    entries,
                    lambda entry: _fzf_media_text(entry, root_tags, root_presentation),
                    multi=True,
                    prompt="Search: ",
                    initial_query=last_query,
                    preview_func=_fzf_preview_text,
                    all_entries=entries,
                    root_presentation=root_presentation,
                )
                if not picked:
                    print()
                    return

                for entry in picked:
                    download_entry(entry)
                print()
                return

        choices = build_choice_list(entries)

        while True:
            pattern = input(
                Fore.YELLOW + "Type a search pattern (ENTER to return): "
            ).strip()
            if not pattern:
                print()
                return

            results = search_media(
                pattern, entries=entries, choices=choices, limit=50, score_cutoff=40
            )
            if not results:
                print(Fore.RED + "  No matches.\n")
                continue

            if _fzf_binary():
                picked, last_query = _pick_with_fzf(
                    [entry for entry, _score in results],
                    lambda entry: _fzf_media_text(entry, root_tags, root_presentation),
                    multi=True,
                    prompt="Download> ",
                    initial_query=last_query,
                    preview_func=_fzf_preview_text,
                    all_entries=entries,
                    root_presentation=root_presentation,
                )
                if not picked:
                    continue

                for entry in picked:
                    download_entry(entry)
                print()
                continue

            def _render_row(index: int, row: tuple[MediaEntry, float]) -> list[str]:
                entry, score = row
                num = index + 1
                color = Fore.GREEN if index % 2 == 0 else Fore.CYAN
                display_root = root_tags.get(entry.root, entry.root)
                return [
                    color + f"{num:2d}. {entry.filename} (score {score:.1f})",
                    Fore.YELLOW + f"    [{display_root}] {entry.path}",
                ]

            _render_numbered_items(results, _render_row)

            sel = input(
                Fore.CYAN
                + "Select numbers to download (comma or space separated, ENTER to new search): "
            ).strip()
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
    print(Fore.MAGENTA + Style.BRIGHT + "=== CineIndex TUI ===\n")
    print(Fore.YELLOW + "1." + Style.RESET_ALL + " Build index (full crawl)")
    print(Fore.YELLOW + "2." + Style.RESET_ALL + " Update index (incremental)")
    print(Fore.YELLOW + "3." + Style.RESET_ALL + " Show stats")
    print(Fore.YELLOW + "4." + Style.RESET_ALL + " Stream (mpv)")
    print(Fore.YELLOW + "5." + Style.RESET_ALL + " Watch history")
    print(Fore.YELLOW + "6." + Style.RESET_ALL + " Download (aria2)")


def main() -> None:
    print_banner()
    ensure_config_files()

    while True:
        print_menu()
        choice = input(Fore.CYAN + "\nSelect an option (ENTER to quit): ").strip()
        if choice == "":
            print(Fore.YELLOW + "\nBye!\n")
            break
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
        else:
            print(Fore.RED + "\nInvalid choice.\n")


if __name__ == "__main__":
    main()
