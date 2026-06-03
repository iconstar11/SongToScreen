# SongToScreen — Gospel Worship Automation Pipeline

Fully automated pipeline that converts a gospel music MP3 + lyrics into a YouTube-ready music video with karaoke captions, thumbnail, and SEO metadata.

## How It Works

```
inputs/song.mp3 + lyrics.txt
        │
        ▼
 [1] Ingest           →  Extract metadata, fetch lyrics via Genius API
 [2] Audio Analysis   →  Whisper word-level transcription + beat/segment detection
 [3] Scene Planning   →  LLM (DeepSeek + Mistral fallback) creates scene-by-scene plan
 [4] Acquire Assets   →  Search Pexels/Pixabay for stock video and photos
 [5] Assemble Video   →  FFmpeg concat clips, colour grade, loudness normalise
 [6] Karaoke Captions →  Word-level gold highlighting overlay
 [7] Render Formats   →  16:9 (YouTube) + 9:16 vertical (Shorts/TikTok)
 [8] Thumbnail        →  AI keyframe selection + branded overlay + LLM metadata
 [9] Review Dashboard →  Streamlit UI — approve, edit, or reject
[10] Upload           →  YouTube Data API v3 (16:9 + Shorts)
```

## Quick Start

### Prerequisites

- Python 3.11+
- FFmpeg installed and on PATH
- API keys for: DeepSeek, Mistral, OpenAI (Whisper), Pexels, Pixabay, Genius

### Setup

```bash
git clone https://github.com/iconstar11/SongToScreen.git
cd SongToScreen
python -m venv .venv
.venv\Scripts\activate   # Windows
source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
```

### Run

```bash
# Full pipeline
python main.py --song inputs/song.mp3

# Single stage (debugging)
python main.py --song inputs/song.mp3 --stage 3

# Resume from last checkpoint
python main.py --song inputs/song.mp3 --resume

# Review dashboard (stage 9)
streamlit run pipeline/stage09_dashboard.py
```

## Project Structure

```
SongToScreen/
├── main.py                  # CLI orchestrator
├── core/                    # Shared utilities (config, logging, db, checkpointing)
├── pipeline/                # 10 processing stages (stage01 — stage10)
├── prompts/                 # Jinja2 LLM prompt templates
├── inputs/                  # Drop MP3 + optional lyrics.txt here
├── outputs/                 # Final rendered videos per song
├── cache/                   # Downloaded stock footage (gitignored)
└── assets/fallbacks/        # Default images when APIs return no results
```

## Tech Stack

| Layer | Tools |
|---|---|
| Audio | OpenAI Whisper API, librosa, mutagen |
| AI/LLM | DeepSeek v4, Mistral (fallback), Jinja2 |
| Stock Media | Pexels API, Pixabay API |
| Video | FFmpeg (assemble, captions, render, thumbnail) |
| Dashboard | Streamlit |
| Upload | YouTube Data API v3 |
| Config | Pydantic Settings, python-dotenv |
| State | SQLite via sqlite3 |
