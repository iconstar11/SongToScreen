# PIPELINE_CONTEXT.md — Full Architecture Reference

> Reference this file when working on any stage.
> This is the single source of truth for what each stage does, what it reads, and what it produces.

---

## Data flow overview

```
inputs/song.mp3 + lyrics.txt
        │
        ▼
[1] Asset Ingestion        watchdog · mutagen · lyricsgenius · pydantic
        │  state.song_path, state.song_slug, state.output_dir
        ▼
[2] Audio Analysis         openai (Whisper API) · librosa · numpy · soundfile
        │  alignment.json · beats.json · segments.json
        ▼
[3] AI Scene Planning      openai (DeepSeek) · mistralai (fallback)
        │  · instructor · jinja2 · pydantic
        │  scene_plan.json
        ▼
[4] Scene Acquisition      requests · tenacity · tqdm · Pexels API · Pixabay API
        │  resolved_scenes.json + cached assets in cache/
        ▼
[5] Video Assembly         ffmpeg-python · numpy
        │  master_16x9.mp4
        ▼
[6] Lyric Caption Overlay  ffmpeg-python · Pillow (preview only)
        │  captioned_16x9.mp4
        ▼
[7] Format Rendering       ffmpeg-python
        │  final_16x9.mp4 · final_9x16.mp4
        ▼
[8] Thumbnail + Metadata   Pillow · openai (DeepSeek) · instructor
        │  thumbnail.jpg · metadata.json
        ▼
[9] Review Dashboard       streamlit · SQLAlchemy · loguru    ← human gate
        │  state.review_status = "approved"
        ▼
[10] Upload & Schedule     google-api-python-client · google-auth-oauthlib · APScheduler
        │  YouTube video IDs logged to SQLite
```

---

## Stage specifications

---

### Stage 1 — Asset Ingestion
**File:** `pipeline/stage01_ingest.py`  
**Type:** Input

`watchdog` monitors the `inputs/` folder. `mutagen` extracts track metadata (title, artist, BPM hint, duration). `lyricsgenius` fetches lyrics from Genius API; falls back to a local `.txt` file in the same folder as the audio. `pydantic` validates the full asset bundle before anything enters the pipeline. A `pipeline_state.json` checkpoint file is written — if the pipeline crashes at any later stage, Stage 1 is skipped on re-run.

**Reads:**
- `inputs/` folder (watched by `watchdog`)
- MP3 or WAV audio file
- Optional `{song_name}.txt` lyrics file in same folder

**Does:**
1. `mutagen` extracts: title, artist, duration, BPM hint from ID3 tags
2. `lyricsgenius` fetches lyrics if no local `.txt` — caches result to `outputs/{slug}/lyrics.txt`
3. `pydantic` validates asset bundle (audio file exists, duration > 30s, lyrics not empty)
4. Creates `outputs/{song_slug}/` directory
5. Writes initial `pipeline_state.json`

**Produces:**
- `state.song_path`, `state.song_slug`, `state.output_dir`
- `outputs/{slug}/lyrics.txt`

**Failure behaviour:** If Genius API fails and no local lyrics file exists, raises `StageError(1, "No lyrics available")`. User must provide a `.txt` file.

**Tools:** watchdog · mutagen · lyricsgenius · pydantic

---

### Stage 2 — Audio Analysis
**File:** `pipeline/stage02_audio.py`  
**Type:** Processing

OpenAI Whisper API transcribes the audio and returns word-level timestamps. `librosa` detects tempo (BPM), beat grid, and song segment boundaries (verse / chorus / bridge). Outputs three JSON files.

**Reads:**
- `state.song_path` (the MP3/WAV)

**Does:**
1. Splits audio into chunks < 25 MB (OpenAI API limit) and sends to Whisper API with `timestamp_granularities=["word"]` for word-level timing
2. `librosa` detects: tempo (BPM), beat grid, segment boundaries (verse/chorus/bridge)
3. Segment boundaries annotated with mood hint: `"energetic"`, `"contemplative"`, `"triumphant"`
4. All outputs saved as JSON to `state.output_dir`

**⚠️ Whisper API vs whisperx:**
OpenAI Whisper API returns word-level timestamps but does **not** provide phoneme-level forced alignment. This means:
- Word boundaries are slightly coarser than whisperx's phoneme alignment
- The karaoke caption renderer in Stage 6 will highlight whole words rather than tracking syllable-by-syllable
- This is an acceptable trade-off for simplicity and eliminating the local whisperx/mps/cuda dependency
- If caption precision becomes an issue later, swap back to whisperx without changing any other stage (alignment.json schema remains identical)

**Performance:** API call completes in 30–60s for a 4-minute song (vs 3–7 minutes for local whisperx). No subprocess needed — async HTTP call is non-blocking. **API cost:** ~$0.024 per 4-minute song (Whisper API at $0.006/min).

**Produces:**
- `outputs/{slug}/alignment.json` — per-word `{word, start_ms, end_ms}`
- `outputs/{slug}/beats.json` — array of beat timestamps in milliseconds
- `outputs/{slug}/segments.json` — `{segment_type, start_ms, end_ms, mood}`
- `state.alignment_path`, `state.beats_path`, `state.segments_path`

**Tools:** openai · librosa · numpy · soundfile

---

### Stage 3 — AI Scene Planning
**File:** `pipeline/stage03_scene_plan.py`  
**Type:** AI

A single batched LLM call — all lyric segments sent in one request, never one call per scene. This eliminates rate-limit risk and is far cheaper. The Jinja2 prompt packages the full lyric alignment, segment boundaries, artist name, and a gospel tone descriptor.

**Reads:**
- `state.alignment_path` (alignment.json)
- `state.segments_path` (segments.json)
- `prompts/scene_plan.j2` (Jinja2 template)

**LLM routing:**
```
Primary:  DeepSeek (openai SDK, custom base_url from settings.deepseek_base_url)
Fallback: Mistral free tier (triggered on RateLimitError or timeout > 15s)
```

**Does:**
1. Groups lyric words into scene segments using `segments.json` boundaries
2. Renders the Jinja2 prompt with all segments in one batch (single LLM call)
3. Sends to DeepSeek → falls back to Mistral if 429 or timeout
4. `instructor` enforces the `ScenePlan` Pydantic schema, auto-retries up to 3 times

**Produces:**
- `outputs/{slug}/scene_plan.json`
- `state.scene_plan_path`

**scene_plan.json schema (per scene entry):**
```json
{
  "scene_id": "sc_004",
  "lyric": "Every breath a song of praise",
  "start_ms": 14200,
  "end_ms": 19200,
  "duration": 5.0,
  "segment_type": "chorus",
  "scene_type": "stock_video",
  "search_terms": ["gospel choir singing", "worship crowd hands raised"],
  "visual_mood": "joyful energetic vibrant",
  "camera_motion": "slow_push_in",
  "transition_in": "fade",
  "transition_out": "fade",
  "caption_style": "karaoke_2line",
  "fallback_strategy": "still_motion"
}
```
**New in v3:** `start_ms` and `end_ms` are pulled directly from `alignment.json` so Stage 4 knows the exact duration each asset must fill.

**Tools:** openai · mistralai · instructor · jinja2 · pydantic

---

### Stage 4 — Scene Acquisition
**File:** `pipeline/stage04_acquire.py`  
**Type:** Asset Resolution

Scene router — resolves each entry in `scene_plan.json` to a local file. Cost: $0.

```
stock_video  →  Pexels API search (search_terms[0])
               hit → download → cache by search-term hash
               miss → Pixabay API search (search_terms[0], then search_terms[1])
                 hit → download → cache
                 miss → downgrade scene_type to "still_motion"

still_motion →  Pexels photo search (visual_mood + "worship")
               hit → download → cache
               miss → Pixabay photo search
                 hit → download → cache
                 miss → use bundled fallback image from assets/fallbacks/worship_default.jpg

text_motion  →  No asset needed. FFmpeg generates this in Stage 5.
               Proceeds immediately.
```

**Cache key:** `sha256(f"{search_term}_{duration_bucket}")[:12]` where `duration_bucket = round(duration / 0.5) * 0.5` — prevents re-fetching the same clip for a 5.0s vs 5.2s scene.

All video assets trimmed to exact scene duration at download time.

**Reads:**
- `state.scene_plan_path`

**Produces:**
- `outputs/{slug}/resolved_scenes.json` — maps `scene_id` to `{local_path, resolved_type}`
- `state.resolved_scenes_path`

**Tools:** requests · tenacity · tqdm · pathlib

---

### Stage 5 — Video Assembly
**File:** `pipeline/stage05_assemble.py`  
**Type:** Processing

Pure FFmpeg. `beats.json` drives cut timing — each scene's `start_ms` snaps to the nearest beat boundary within ±80ms to keep cuts musical.

**Reads:**
- `state.resolved_scenes_path`
- `state.beats_path`
- `state.song_path`

**Filter chain:**
```
[per clip]    scale=1920:1080, setpts, zoompan (if still_motion — Ken Burns effect)
[global]      concat with xfade transitions (fade / dissolve / wipeleft —
              driven by transition_in / transition_out from scene plan)
              eq=contrast=1.08:saturation=1.15:brightness=-0.02
              curves=r='0/0 0.5/0.48 1/0.95'
              loudnorm=I=-16:LRA=11:TP=-1.5
[output]      master_16x9.mp4  (1920×1080, H.264, AAC, no captions)
```

**Does:**
1. For each scene, selects FFmpeg input and builds the filter chain entry
2. Beat-syncs cuts: each scene's `start_ms` snaps to nearest beat within ±80ms
3. Applies `zoompan` to `still_motion` scenes for Ken Burns effect
4. Concatenates all clips with `xfade` transitions
5. Applies cinematic colour grade inline
6. Mixes in the original audio track
7. Applies loudness normalisation for YouTube spec

**Produces:**
- `outputs/{slug}/master_16x9.mp4` (1920×1080, H.264, AAC, no captions)
- `state.master_video_path`

**Tools:** ffmpeg-python · numpy

---

### Stage 6 — Lyric Caption Overlay
**File:** `pipeline/stage06_captions.py`  
**Type:** Processing

Karaoke 2-line window renderer. At any moment the video shows the current line + next line. The word currently being sung is highlighted in accent colour `#FFD700` (gold). All other visible words are white with drop shadow.

**Reads:**
- `state.master_video_path`
- `state.alignment_path`

**Implementation:**
One `drawtext` filter entry per word, toggled by `enable='between(t,start,end)'`. Timing comes directly from `alignment.json` — no subtitle file conversion.

```
# Active word (gold highlight)
drawtext=text='grace':fontsize=56:fontcolor=#FFD700:
         shadowcolor=black:shadowx=3:shadowy=3:
         x=<word_x>:y=h-140:
         enable='between(t,14.2,14.8)'

# Rest of line (white)
drawtext=text='Your':fontsize=56:fontcolor=white:
         shadowcolor=black:shadowx=3:shadowy=3:
         x=<word_x>:y=h-140:
         enable='between(t,13.5,16.0)'
```

**Line wrap rule:** Max 40 characters per line. If a lyric line exceeds 40 chars, split at the nearest word boundary and push overflow to line 2 of the window.

**⚠️ Alignment note:** With OpenAI Whisper API (Stage 2), word boundaries are approximate rather than phoneme-precise. The gold highlight will cover whole words rather than tracking syllable-by-syllable. This is visually acceptable for karaoke but slightly less precise than whisperx's phoneme alignment.

**Produces:**
- `outputs/{slug}/captioned_16x9.mp4`
- `state.captioned_video_path`

**Tools:** ffmpeg-python · Pillow (caption layout preview/debug only)

---

### Stage 7 — Format Rendering
**File:** `pipeline/stage07_render.py`  
**Type:** Processing

Two FFmpeg passes from `captioned_16x9.mp4`:

```
Pass 1: 1920×1080  →  final_16x9.mp4   (YouTube long-form, copy if no changes needed)
Pass 2: 1080×1920  →  final_9x16.mp4   (Shorts / Reels / TikTok)
        crop=608:1080:(iw-608)/2:0, scale=1080:1920
        (centre-crops the 16:9 frame to a 9:16 portrait vertical)
```

**Reads:**
- `state.captioned_video_path`

**Produces:**
- `outputs/{slug}/final_16x9.mp4`
- `outputs/{slug}/final_9x16.mp4`
- `state.final_16x9_path`, `state.final_9x16_path`

**Tools:** ffmpeg-python

---

### Stage 8 — Thumbnail & Metadata
**File:** `pipeline/stage08_thumbnail.py`  
**Type:** AI

**Reads:**
- `state.final_16x9_path`
- `state.scene_plan_path`
- `state.alignment_path`
- `prompts/metadata.j2`

**Does:**
1. FFmpeg scene-change detection extracts top 5 candidate keyframes:
   ```
   -vf "select=gt(scene\,0.4),scale=1920:1080" -vsync vfr -frames:v 5 thumb_%d.jpg
   ```
2. `Pillow` scores frames by brightness variance — rejects near-black / near-white
3. Highest-contrast frame selected; branded template overlay applied
4. Same DeepSeek → Mistral routing as Stage 3
5. Single LLM call: keyframe description + top 3 lyrics + artist + SEO instruction
6. `instructor` enforces `Metadata` Pydantic schema

**Metadata schema:**
```json
{
  "title": "Your Grace Still Carries Me | Artist | Official Video",
  "description": "...(SEO optimised, 300 words max)...",
  "tags": ["gospel music", "worship song", "christian music 2026"],
  "category": "Music",
  "thumbnail_frame": "thumb_3.jpg"
}
```

**Produces:**
- `outputs/{slug}/thumbnail.jpg`
- `outputs/{slug}/metadata.json`
- `state.thumbnail_path`, `state.metadata_path`

**Tools:** Pillow · openai · mistralai · instructor

---

### Stage 9 — Review Dashboard
**File:** `pipeline/stage09_dashboard.py`  
**Type:** Human Gate  
**Launch:** `streamlit run pipeline/stage09_dashboard.py`

**Reads:**
- All output files for the current song
- SQLite database via `core/db.py`

**Does:**
- Three-column layout: video preview | thumbnail | metadata (editable fields)
- Approve / Edit / Reject buttons
- Logs decision to `pipeline_runs` table with `quality_flag` column
- `quality_flag` is the calibration instrument — after 10 videos you will have a ranked list of which stage fails most often and can target prompt or filter improvements precisely

**SQLite schema:**
```sql
CREATE TABLE pipeline_runs (
  run_id          TEXT PRIMARY KEY,
  song_title      TEXT,
  run_date        DATETIME,
  status          TEXT,        -- approved / rejected / edited
  quality_flag    TEXT,        -- wrong_clip / bad_caption / colour_grade / metadata
  notes           TEXT,
  youtube_id_16x9 TEXT,        -- populated by Stage 10
  youtube_id_9x16 TEXT         -- populated by Stage 10
);
```

**Auto-bypass:** Once you have approved 10 consecutive videos without any edits, set `AUTO_APPROVE=true` in `.env` to skip the dashboard and upload directly. The counter resets on any rejection.

**Produces:**
- `state.review_status` = `"approved"` / `"rejected"` / `"edited"`

**Tools:** streamlit · SQLAlchemy · loguru · python-dotenv

---

### Stage 10 — Upload & Schedule
**File:** `pipeline/stage10_upload.py`  
**Type:** Output  
**Trigger:** Only runs if `state.review_status == "approved"`

**Reads:**
- `state.final_16x9_path`
- `state.final_9x16_path`
- `state.thumbnail_path`
- `state.metadata_path`

**Does:**
1. YouTube Data API v3: uploads `final_16x9.mp4` as primary video
2. YouTube Data API v3: uploads `final_9x16.mp4` as Shorts
3. Sets scheduled publish time from `metadata.json`
4. Logs YouTube video IDs to SQLite `pipeline_runs` table
5. `APScheduler` one-shot job: queues next pipeline run for the next song in `inputs/`

**Produces:**
- YouTube video IDs written to SQLite
- Log entry: `"Upload complete: {youtube_url}"`

**Tools:** google-api-python-client · google-auth-oauthlib · APScheduler

---

## Environment variables (.env)

```env
# LLM
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-v4-flash
MISTRAL_API_KEY=your_key_here
MISTRAL_MODEL=mistral-small-2603

# OpenAI (Whisper API for Stage 2 + LLM fallback)
OPENAI_API_KEY=your_key_here

# Stock footage
PEXELS_API_KEY=your_key_here
PIXABAY_API_KEY=your_key_here

# Lyrics
GENIUS_ACCESS_TOKEN=your_key_here
GENIUS_BASE_URL=https://api.genius.com

# YouTube (path to OAuth client secrets file)
YOUTUBE_CLIENT_SECRETS_PATH=secrets/client_secrets.json

# Pipeline behaviour
AUTO_APPROVE=false
CACHE_DIR=cache
OUTPUTS_DIR=outputs
```
