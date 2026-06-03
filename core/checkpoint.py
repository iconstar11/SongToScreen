from pydantic import BaseModel
from pathlib import Path
from typing import Optional


class PipelineState(BaseModel):
    song_path: Path
    song_slug: str
    output_dir: Path
    completed_stages: dict[int, bool] = {}

    # Populated by stages as they complete
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


def init_state(song_path: Path, output_dir: Path) -> PipelineState:
    slug = song_path.stem[:50].replace(" ", "_").lower()
    return PipelineState(
        song_path=song_path.resolve(),
        song_slug=slug,
        output_dir=output_dir.resolve(),
    )


def load_state(song_path: Path, output_dir: Path) -> PipelineState:
    import json
    state_file = output_dir / "pipeline_state.json"
    if state_file.exists():
        data = json.loads(state_file.read_text())
        # JSON keys are always strings; convert completed_stages keys to int
        if "completed_stages" in data:
            data["completed_stages"] = {int(k): v for k, v in data["completed_stages"].items()}
        return PipelineState.model_validate(data)
    return init_state(song_path, output_dir)


def save_state(state: PipelineState) -> None:
    state_file = state.output_dir / "pipeline_state.json"
    state_file.write_text(state.model_dump_json(indent=2))
