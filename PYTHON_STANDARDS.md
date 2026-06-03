# PYTHON_STANDARDS.md — Coding Rules for Gospel Pipeline

> Reference this file when writing or editing any Python module in this project.
> These rules are enforced across all stages.

---

## Module structure rules

### One module per stage — no exceptions
Each pipeline stage lives in its own file: `pipeline/stage0N_name.py`.
No stage logic bleeds into another stage's file.

### Every stage module must export exactly one public function
The function is named `run(state: PipelineState) -> PipelineState`.
`main.py` calls `stage.run(state)` for each stage in sequence. Nothing else.

```python
# pipeline/stage03_scene_plan.py

from core.config import settings
from core.logger import log
from core.checkpoint import PipelineState

def run(state: PipelineState) -> PipelineState:
    """Generate scene_plan.json from alignment.json and song metadata."""
    # implementation here
    return state
```

### Shared utilities go in `core/` — never duplicated across stages
If two stages need the same helper function, it belongs in `core/`.
Never copy-paste a function from one stage file to another.

### `main.py` is the orchestrator only
`main.py` does not contain business logic. It:
1. Parses CLI args
2. Loads the pipeline state from checkpoint
3. Calls each stage's `run(state)` in sequence
4. Catches and logs exceptions per stage
5. Writes the final state to checkpoint

```python
# main.py skeleton
from pipeline import (
    stage01_ingest, stage02_audio, stage03_scene_plan,
    stage04_acquire, stage05_assemble, stage06_captions,
    stage07_render, stage08_thumbnail, stage10_upload,
)
from core.checkpoint import load_state, save_state

STAGES = [
    stage01_ingest,
    stage02_audio,
    stage03_scene_plan,
    stage04_acquire,
    stage05_assemble,
    stage06_captions,
    stage07_render,
    stage08_thumbnail,
    # stage09 is launched separately via streamlit
    stage10_upload,
]

def main(song_path: str, resume: bool = False, only_stage: int = None):
    state = load_state(song_path) if resume else init_state(song_path)
    for i, stage in enumerate(STAGES, start=1):
        if only_stage and i != only_stage:
            continue
        if state.completed_stages.get(i):
            log.info(f"Stage {i} already complete — skipping")
            continue
        state = stage.run(state)
        save_state(state)
```

---

## `PipelineState` — the single data contract

All stages communicate through one typed object. No global variables. No files passed as raw strings between stages.

```python
# core/checkpoint.py
from pydantic import BaseModel
from pathlib import Path
from typing import Optional

class PipelineState(BaseModel):
    song_path: Path
    song_slug: str
    output_dir: Path
    completed_stages: dict[int, bool] = {}

    # populated by stages as they complete
    alignment_path: Optional[Path] = None
    beats_path: Optional[Path] = None
    segments_path: Optional[Path] = None
    scene_plan_path: Optional[Path] = None
    resolved_scenes_path: Optional[Path] = None
    master_video_path: Optional[Path] = None
    captioned_video_path: Optional[Path] = None
    final_16x9_path: Optional[Path] = None
    final_9x16_path: Optional[Path] = None
    thumbnail_path: Optional[Path] = None
    metadata_path: Optional[Path] = None
    review_status: Optional[str] = None  # approved / rejected / edited

def load_state(song_path: Path) -> PipelineState:
    state_file = song_path.parent / f"{song_path.stem}_state.json"
    if state_file.exists():
        return PipelineState.model_validate_json(state_file.read_text())
    return init_state(song_path)

def save_state(state: PipelineState) -> None:
    state_file = state.output_dir / "pipeline_state.json"
    state_file.write_text(state.model_dump_json(indent=2))
```

---

## Configuration rules

### All settings come from `core/config.py` — never from raw `os.getenv()` in stage files

```python
# core/config.py
from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    # LLM
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"
    mistral_api_key: str = ""
    mistral_model: str = "mistral-small-latest"

    # Stock footage APIs
    pexels_api_key: str
    pixabay_api_key: str

    # YouTube
    youtube_client_secrets_path: Path = Path("secrets/client_secrets.json")

    # Pipeline behaviour
    auto_approve: bool = False
    cache_dir: Path = Path("cache")
    outputs_dir: Path = Path("outputs")

    class Config:
        env_file = ".env"

settings = Settings()
```

Stage files import like this:
```python
from core.config import settings

client = openai.OpenAI(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
)
```

---

## LLM client rules

### Always use the fallback chain
DeepSeek is primary. Mistral free is fallback. Wrap every LLM call in the shared client factory.

```python
# core/llm.py
import openai
from mistralai import Mistral
from core.config import settings
from core.logger import log

def get_llm_client():
    """Returns a DeepSeek openai-compatible client."""
    return openai.OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )

def chat_with_fallback(messages: list[dict], model: str = None) -> str:
    """
    Tries DeepSeek first. Falls back to Mistral on 429 or timeout.
    Returns the raw string content of the assistant message.
    """
    try:
        client = get_llm_client()
        response = client.chat.completions.create(
            model=model or settings.deepseek_model,
            messages=messages,
            timeout=20,
        )
        return response.choices[0].message.content
    except (openai.RateLimitError, openai.APITimeoutError) as e:
        log.warning(f"DeepSeek failed ({e}), switching to Mistral fallback")
        mistral = Mistral(api_key=settings.mistral_api_key)
        response = mistral.chat.complete(
            model=settings.mistral_model,
            messages=messages,
        )
        return response.choices[0].message.content
```

### Always batch LLM calls — never call per-scene in a loop
All scenes for a song are sent in one request. This avoids rate limits and is 20× cheaper.

---

## File naming rules

| Pattern | Rule |
|---|---|
| Stage modules | `stage0N_descriptive_name.py` — N is zero-padded two digits |
| Output files | Always namespaced under `outputs/{song_slug}/` |
| Temp files | Prefixed with `_tmp_`, cleaned up at end of stage |
| JSON files | `snake_case.json` |
| Cache files | Named by `sha256(search_term)[:12].ext` |

---

## Error handling rules

1. Each stage catches its own exceptions. Never let a stage exception propagate unhandled to `main.py`.
2. On failure, the stage logs the error with `log.error(...)` and raises a `StageError` with the stage number.
3. `main.py` catches `StageError`, marks the stage as failed in the checkpoint, and stops the pipeline.
4. The user reruns with `--resume` to pick up from the last successful stage.

```python
# core/exceptions.py
class StageError(Exception):
    def __init__(self, stage: int, message: str):
        self.stage = stage
        super().__init__(f"Stage {stage} failed: {message}")
```

---

## Comment rules

Only comment when the WHY is not obvious. Never comment what the code clearly does.

```python
# WRONG — states the obvious
# Loop through scenes and download each one
for scene in scenes:
    download(scene)

# CORRECT — explains a non-obvious constraint
# Pexels returns a max of 15 results per page even when per_page=80 is set.
# We cap at 3 results and pick the best-matching duration to avoid quota burn.
results = search_pexels(term, per_page=3)
```

---

## Testing rules

- Every stage that calls an external API must have a test that mocks the API call.
- Every Pydantic model must have a test that validates a known-good JSON fixture.
- Use `pytest` and `pytest-mock`. No other test framework.
- Test files live in `tests/`, named `test_stage0N.py`.
- Run before any commit: `pytest tests/ -v`

---

## What NOT to do

- Do not use `print()` for logging. Use `from core.logger import log` and `log.info()` / `log.error()`.
- Do not use `global` variables. Pass state through `PipelineState`.
- Do not import from a stage module into another stage module. All shared code goes in `core/`.
- Do not store API keys in any file other than `.env`.
- Do not use `moviepy`. FFmpeg only via `ffmpeg-python`.
- Do not use a local whisper installation (openai-whisper, whisperx). Use OpenAI Whisper API via the `openai` SDK only.
- Do not use `APScheduler` for anything other than the one-shot next-run trigger in Stage 10.
- Do not hardcode paths. All paths must come from `settings` or be derived from `state.output_dir`.
