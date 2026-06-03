import subprocess, shutil
from pathlib import Path
from core.checkpoint import PipelineState
from core.logger import log

def run(state):
    log.info(f"Stage 7: Rendering formats for {state.song_slug}")

    src = state.captioned_video_path

    # Pass 1: 16x9 (just copy if already correct)
    final_16x9 = state.output_dir / "final_16x9.mp4"
    shutil.copy(src, final_16x9)
    state.final_16x9_path = final_16x9

    # Pass 2: 9x16 vertical crop for Shorts/TikTok
    final_9x16 = state.output_dir / "final_9x16.mp4"
    log.info(f"  Rendering 9x16 vertical...")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-vf", "crop=608:1080:(iw-608)/2:0,scale=1080:1920",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(final_9x16)
    ], capture_output=True, text=True)
    state.final_9x16_path = final_9x16

    state.completed_stages[7] = True
    log.info(f"  Output: {state.output_dir}")
    return state
