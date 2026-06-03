import sqlite3
from pathlib import Path
from core.config import settings

DB_PATH = Path("pipeline_runs.db")

def get_connection():
    return sqlite3.connect(str(DB_PATH))

def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id TEXT PRIMARY KEY,
            song_title TEXT,
            run_date TEXT,
            status TEXT,
            quality_flag TEXT,
            notes TEXT,
            youtube_id_16x9 TEXT,
            youtube_id_9x16 TEXT
        )
    """)
    conn.commit()
    conn.close()
