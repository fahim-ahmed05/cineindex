from __future__ import annotations

import sqlite3
from pathlib import Path

# backend/app/db.py → ROOT is backend/
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "media_index.db"


def get_conn() -> sqlite3.Connection:
    """
    Open a SQLite connection with row access by column name.
    """
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
