# CLAUDE.md — Gospel Worship Automation Pipeline

> This file is read automatically at the start of every Claude Code session.
> Do not delete it. Keep it under 200 lines.

---

## Project identity

**Name:** Gospel Worship Automation Pipeline  
**Purpose:** Fully automated pipeline that converts a gospel music MP3 + lyrics into a YouTube-ready music video with karaoke captions, thumbnail, and metadata.  
**Owner:** Antony Kinuthia  
**Stack:** Python 3.11+, FFmpeg, OpenAI Whisper API, DeepSeek API (via openai SDK), Mistral free API (fallback), Pexels API, Pixabay API, YouTube Data API v3  
**LLM backend:** DeepSeek v4 via `openai` SDK with custom `base_url`. NOT Anthropic Claude for runtime — you are the coding assistant only.

---

## Execution rules (non-negotiable)

1. **Do exactly what is asked. Nothing more.**  
   Do not add features, refactor surrounding code, rename variables, or "clean up" files that were not mentioned in the request.

2. **Do not suggest improvements unless asked.**  
   If you notice something unrelated to the current task, stay silent about it. Only flag it if it will directly cause a bug in the current task.

3. **Do not explain what you are about to do before doing it.**  
   Start with the action. Summarise briefly after if needed.

4. **Read before editing.**  
   Before modifying any file, read its current contents. Never assume what is in a file.

5. **Keep changes minimal.**  
   A bug fix does not need surrounding code cleaned up. A new function does not need the whole module restructured.

6. **No security vulnerabilities.**  
   Never hardcode API keys, tokens, or passwords. Always use `.env` via `python-dotenv`.

7. **Never commit secrets.**  
   `.env` is always in `.gitignore`. Never reference `.env` values inline in code — always via `os.getenv()`.

8. **Ask before destructive actions.**  
   Deleting files, overwriting cached assets, or resetting the SQLite database requires explicit user confirmation. State what will be destroyed and wait for approval.

9. **No unsolicited comments.**  
   Only add a code comment when the WHY is non-obvious: a hidden constraint, a workaround for a specific external API behaviour, or something that would genuinely surprise a reader. Never comment what the code obviously does.

10. **One task, one diff.**  
    Complete the requested task in the smallest possible set of file changes. Do not touch files unrelated to the current task.

---

## Project structure (enforced)

```
gospel_pipeline/
├── CLAUDE.md                  ← you are here (auto-loaded)
├── PYTHON_STANDARDS.md        ← coding rules reference
├── PIPELINE_CONTEXT.md        ← full pipeline architecture
├── TASKS.md                   ← build checklist by phase
├── README.md                  ← human-readable overview
│
├── main.py                    ← orchestrator: runs stages in order
├── .env                       ← secrets (never committed)
├── .env.example               ← safe template committed to git
├── requirements.txt
├── pyproject.toml
│
├── pipeline/                  ← one module per stage
│   ├── __init__.py
│   ├── stage01_ingest.py
│   ├── stage02_audio.py
│   ├── stage03_scene_plan.py
│   ├── stage04_acquire.py
│   ├── stage05_assemble.py
│   ├── stage06_captions.py
│   ├── stage07_render.py
│   ├── stage08_thumbnail.py
│   ├── stage09_dashboard.py
│   └── stage10_upload.py
│
├── core/                      ← shared utilities, no stage logic
│   ├── __init__.py
│   ├── config.py              ← loads .env, exposes typed settings
│   ├── logger.py              ← loguru setup
│   ├── checkpoint.py          ← pipeline_state.json read/write
│   ├── ffmpeg_utils.py        ← shared FFmpeg helper functions
│   └── db.py                  ← SQLAlchemy models + session factory
│
├── prompts/                   ← Jinja2 prompt templates
│   ├── scene_plan.j2
│   └── metadata.j2
│
├── assets/                    ← static project assets
│   ├── fonts/
│   ├── fallbacks/             ← fallback images when APIs return 0 results
│   └── luts/                  ← reserved (not used in current version)
│
├── cache/                     ← downloaded stock footage (gitignored)
│   ├── video/
│   └── images/
│
├── inputs/                    ← drop MP3 + optional lyrics.txt here
├── outputs/                   ← final rendered files per song
│   └── {song_slug}/
│       ├── master_16x9.mp4
│       ├── captioned_16x9.mp4
│       ├── final_16x9.mp4
│       ├── final_9x16.mp4
│       ├── thumbnail.jpg
│       ├── metadata.json
│       ├── scene_plan.json
│       ├── resolved_scenes.json
│       ├── alignment.json
│       ├── beats.json
│       └── segments.json
│
└── tests/
    ├── test_stage01.py
    ├── test_stage03.py
    └── test_stage06.py
```

---

## Key commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run full pipeline on a song
python main.py --song inputs/song.mp3

# Run a single stage (for debugging)
python main.py --song inputs/song.mp3 --stage 3

# Resume from last checkpoint
python main.py --song inputs/song.mp3 --resume

# Launch review dashboard
streamlit run pipeline/stage09_dashboard.py

# Run tests
pytest tests/ -v
```

---

## Imports to use

Reference these other files when needed:

- Full pipeline architecture → `@PIPELINE_CONTEXT.md`
- Python coding standards and module rules → `@PYTHON_STANDARDS.md`
- Current build checklist → `@TASKS.md`
