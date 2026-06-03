import json, sqlite3, datetime, uuid
from pathlib import Path
import streamlit as st
from core.config import settings

DB_PATH = Path("pipeline_runs.db")

def _init_db():
    conn = sqlite3.connect(str(DB_PATH))
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
    return conn

def main():
    st.set_page_config(page_title="Gospel Pipeline — Review", layout="wide")
    st.title("Gospel Worship Pipeline — Review Dashboard")

    _init_db()

    # Scan output directories
    outputs_dir = settings.outputs_dir
    songs = []
    for d in sorted(outputs_dir.iterdir()):
        if d.is_dir():
            state_file = d / "pipeline_state.json"
            if state_file.exists():
                state = json.loads(state_file.read_text())
                if state.get("completed_stages", {}).get("7"):
                    songs.append({"dir": d, "state": state})

    if not songs:
        st.info("No completed videos found. Run the pipeline first.")
        return

    song_names = [s["dir"].name for s in songs]
    selected = st.sidebar.selectbox("Select song", song_names)
    song = next(s for s in songs if s["dir"].name == selected)
    d = song["dir"]

    col1, col2, col3 = st.columns([2, 1, 1])

    with col1:
        st.subheader("Video Preview")
        final_16x9 = d / "final_16x9.mp4"
        if final_16x9.exists():
            st.video(str(final_16x9))

    with col2:
        st.subheader("Thumbnail")
        thumb = d / "thumbnail.jpg"
        if thumb.exists():
            st.image(str(thumb), use_container_width=True)

    with col3:
        st.subheader("Metadata")
        meta_file = d / "metadata.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text())
            new_title = st.text_input("Title", meta.get("title", ""))
            new_desc = st.text_area("Description", meta.get("description", ""), height=200)
            new_tags = st.text_input("Tags", ", ".join(meta.get("tags", [])))
            if st.button("Save Metadata"):
                meta["title"] = new_title
                meta["description"] = new_desc
                meta["tags"] = [t.strip() for t in new_tags.split(",")]
                meta_file.write_text(json.dumps(meta, indent=2))
                st.success("Saved!")

    st.divider()
    st.subheader("Decision")

    c1, c2, c3, c4 = st.columns(4)
    quality_flag = st.selectbox(
        "Quality flag (if rejecting/editing)",
        ["", "wrong_clip", "bad_caption", "colour_grade", "metadata"],
    )

    conn = sqlite3.connect(str(DB_PATH))
    run_id = str(uuid.uuid4())[:8]
    run_date = datetime.datetime.now().isoformat()

    if c1.button("Approve", type="primary"):
        conn.execute(
            "INSERT INTO pipeline_runs VALUES (?,?,?,?,?,?,?,?)",
            (run_id, selected, run_date, "approved", quality_flag or "", "", "", "")
        )
        conn.commit()
        st.success(f"Approved! Run ID: {run_id}")
        st.balloons()

    if c2.button("Edit & Hold"):
        conn.execute(
            "INSERT INTO pipeline_runs VALUES (?,?,?,?,?,?,?,?)",
            (run_id, selected, run_date, "edited", quality_flag or "", "", "", "")
        )
        conn.commit()
        st.warning(f"Held for editing. Run ID: {run_id}")

    if c3.button("Reject"):
        conn.execute(
            "INSERT INTO pipeline_runs VALUES (?,?,?,?,?,?,?,?)",
            (run_id, selected, run_date, "rejected", quality_flag or "", "", "", "")
        )
        conn.commit()
        st.error(f"Rejected. Run ID: {run_id}")

    # Show recent runs
    if c4.button("Show History"):
        rows = conn.execute("SELECT * FROM pipeline_runs ORDER BY run_date DESC LIMIT 10").fetchall()
        for row in rows:
            st.text(f"{row[2][:19]} | {row[3]:10s} | {row[4] or '—'} | {row[0]}")

    conn.close()

if __name__ == "__main__":
    main()
