import argparse
from pathlib import Path
from core.checkpoint import load_state, save_state
from core.config import settings
from core.logger import log
from pipeline import (
    stage01_ingest, stage02_audio, stage03_scene_plan,
    stage04_acquire, stage05_assemble, stage06_captions,
    stage07_render, stage08_thumbnail, stage10_upload,
)

STAGES = [
    (1, stage01_ingest),
    (2, stage02_audio),
    (3, stage03_scene_plan),
    (4, stage04_acquire),
    (5, stage05_assemble),
    (6, stage06_captions),
    (7, stage07_render),
    (8, stage08_thumbnail),
    # Stage 9 is launched separately: streamlit run pipeline/stage09_dashboard.py
    (10, stage10_upload),
]

def _find_existing_output(song_path):
    """Scan outputs/ for an existing state file matching this song."""
    song = Path(song_path).resolve()
    for d in settings.outputs_dir.iterdir():
        if d.is_dir():
            state_file = d / "pipeline_state.json"
            if state_file.exists():
                import json
                data = json.loads(state_file.read_text())
                if Path(data.get("song_path", "")) == song:
                    return d
    return None

def main(song_path, resume=False, only_stage=None):
    song = Path(song_path)
    if not song.exists():
        log.error(f"Song not found: {song_path}")
        return

    # Check for existing output from a previous run
    output_dir = _find_existing_output(song_path) if (resume or only_stage) else None

    if output_dir:
        log.info(f"Found existing output: {output_dir}")
    else:
        temp_slug = song.stem[:30].replace(" ", "_").lower()
        output_dir = settings.outputs_dir / temp_slug

    state = load_state(song, output_dir)
    if resume:
        log.info(f"Resuming from {state.output_dir}")

    for stage_num, stage_fn in STAGES:
        if only_stage and stage_num != only_stage:
            continue
        if state.completed_stages.get(stage_num):
            log.info(f"Stage {stage_num} already complete — skipping")
            continue
        try:
            state = stage_fn.run(state)
            state.completed_stages[stage_num] = True
            save_state(state)
        except Exception as e:
            log.error(f"Stage {stage_num} failed: {e}")
            save_state(state)
            break

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gospel Worship Automation Pipeline")
    parser.add_argument("--song", required=True, help="Path to MP3/WAV file")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    parser.add_argument("--stage", type=int, help="Run only a specific stage")
    args = parser.parse_args()
    main(args.song, args.resume, args.stage)
