from __future__ import annotations

import sqlite3
from pathlib import Path

from platformdirs import PlatformDirs

APP_NAME = "CineIndex"

_dirs = PlatformDirs(APP_NAME, None, ensure_exists=True)

# OS-appropriate user config and data dirs:
# - Linux:  ~/.config/CineIndex/   (config), ~/.local/share/CineIndex/ (data)
# - macOS:  ~/Library/Application Support/CineIndex/ (both)
# - Win:    %LOCALAPPDATA%\CineIndex\CineIndex\ (both, by default)
CONFIG_DIR = Path(_dirs.user_config_dir)
DATA_DIR = Path(_dirs.user_data_dir)

DB_PATH = DATA_DIR / "media_index.db"
FZF_INPUT_CACHE = DATA_DIR / "fzf_input.txt"
FZF_JSON_CACHE = DATA_DIR / "fzf_preview.json"
FZF_SCRIPT_CACHE = DATA_DIR / "fzf_preview.py"
FZF_EP_INDEX_CACHE = DATA_DIR / "fzf_ep_index.json"


def get_conn() -> sqlite3.Connection:
    """
    Open a SQLite connection with row access by column name.
    """
    # parent dir is ensured by PlatformDirs(ensure_exists=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """
    Create tables and indexes if they do not exist.
    """
    conn = get_conn()
    try:
        cur = conn.cursor()

        # Directories table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dirs (
                url      TEXT PRIMARY KEY,
                root     TEXT NOT NULL,
                parent   TEXT,
                name     TEXT,
                modified TEXT
            )
            """
        )

        # Media files table
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS media (
                url      TEXT PRIMARY KEY,
                root     TEXT NOT NULL,
                path     TEXT NOT NULL,
                filename TEXT NOT NULL,
                modified TEXT,
                size     TEXT
            )
            """
        )

        # Helpful indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_dirs_root   ON dirs(root)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_dirs_parent ON dirs(parent)")

        cur.execute("CREATE INDEX IF NOT EXISTS idx_media_root     ON media(root)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_media_path     ON media(path)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_media_filename ON media(filename)")

        conn.commit()
    finally:
        conn.close()
