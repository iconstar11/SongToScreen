import json, sqlite3, datetime, uuid
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from core.checkpoint import PipelineState
from core.config import settings
from core.db import init_db, get_connection
from core.logger import log

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def _get_authenticated_service():
    secrets_path = settings.youtube_client_secrets_path
    if not secrets_path.exists():
        log.warning(f"  YouTube client secrets not found at {secrets_path}")
        return None
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    credentials = flow.run_local_server(port=0)
    return build("youtube", "v3", credentials=credentials)

def _upload_video(youtube, file_path, title, description, tags, category="22", is_shorts=False):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category,
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info(f"  Upload progress: {int(status.progress() * 100)}%")
    video_id = response["id"]
    label = "Shorts" if is_shorts else "16x9"
    log.info(f"  {label} uploaded: https://youtube.com/watch?v={video_id}")
    return video_id

def run(state):
    log.info(f"Stage 10: Uploading {state.song_slug} to YouTube")

    init_db()

    if state.review_status != "approved":
        # Check SQLite for approval
        conn = get_connection()
        rows = conn.execute(
            "SELECT status FROM pipeline_runs WHERE song_title = ? ORDER BY run_date DESC LIMIT 1",
            (state.song_slug,)
        ).fetchall()
        conn.close()
        if not rows or rows[0][0] != "approved":
            log.warning(f"  Skipping upload — not approved. Run Stage 9 dashboard first.")
            return state

    youtube = _get_authenticated_service()
    if youtube is None:
        log.warning(f"  Upload skipped — YouTube not configured")
        return state

    meta = json.loads(state.metadata_path.read_text(encoding="utf-8"))
    title = meta.get("title", state.song_slug)[:100]
    description = meta.get("description", "")
    tags = meta.get("tags", [])[:15]

    # Upload 16x9
    youtube_id_16x9 = _upload_video(youtube, state.final_16x9_path, title, description, tags)

    # Upload 9x16 as Shorts
    shorts_title = f"{title} #Shorts"[:100]
    youtube_id_9x16 = _upload_video(
        youtube, state.final_9x16_path, f"{shorts_title} #Shorts",
        description, tags, is_shorts=True
    )

    # Log to SQLite
    conn = get_connection()
    run_id = str(uuid.uuid4())[:8]
    conn.execute(
        "INSERT INTO pipeline_runs VALUES (?,?,?,?,?,?,?,?)",
        (run_id, state.song_slug, datetime.datetime.now().isoformat(),
         "uploaded", "", "", youtube_id_16x9, youtube_id_9x16)
    )
    conn.commit()
    conn.close()

    state.completed_stages[10] = True
    log.info(f"  Uploads complete")
    return state
