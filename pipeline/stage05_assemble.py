import json, subprocess, tempfile
from pathlib import Path
import numpy as np
from mutagen.mp3 import MP3
from core.checkpoint import PipelineState
from core.config import settings
from core.logger import log

BEAT_SNAP_MS = 80
OUTPUT_W, OUTPUT_H = 1920, 1080
FPS = 30

def _snap_to_beat(time_ms, beats_ms):
    arr = np.array(beats_ms)
    idx = np.argmin(np.abs(arr - time_ms))
    if abs(arr[idx] - time_ms) <= BEAT_SNAP_MS:
        return int(arr[idx])
    return time_ms

def _run_ffmpeg(args, description=""):
    """Run ffmpeg with logging."""
    log.debug(f"  ffmpeg {' '.join(str(a) for a in args[:8])}...")
    result = subprocess.run(["ffmpeg", "-y", "-loglevel", "error"] + args,
                          capture_output=True, text=True)
    if result.returncode != 0 and result.stderr:
        log.warning(f"  ffmpeg: {result.stderr[:200]}")
    return result.returncode == 0

def _render_clip(scene, asset_path, scene_type, duration, tmp_dir, index):
    """Render a single scene clip to a temp file with filters applied."""
    out_path = tmp_dir / f"clip_{index:04d}.mp4"

    if scene_type == "text_motion" or not asset_path:
        _run_ffmpeg([
            "-f", "lavfi", "-i", f"color=c=black:s={OUTPUT_W}x{OUTPUT_H}:d={duration}:r=30",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an",
            str(out_path)
        ])
    elif scene_type == "still_motion":
        zoom_frames = max(int(duration * FPS), 30)
        _run_ffmpeg([
            "-loop", "1", "-framerate", str(FPS), "-i", asset_path,
            "-vf", (f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=decrease,"
                    f"pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2,"
                    f"zoompan=z='min(zoom+0.0015,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={zoom_frames}:s={OUTPUT_W}x{OUTPUT_H}"),
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an",
            str(out_path)
        ])
    else:
        # stock_video — trim to duration with scale+pad
        _run_ffmpeg([
            "-i", asset_path,
            "-vf", (f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=decrease,"
                    f"pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2,"
                    f"setpts=PTS-STARTPTS"),
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an",
            str(out_path)
        ])

    return out_path

def _render_gap(duration, tmp_dir, index):
    """Render a silent black clip for a gap between scenes."""
    out_path = tmp_dir / f"gap_{index:04d}.mp4"
    _run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=black:s={OUTPUT_W}x{OUTPUT_H}:d={duration}:r={FPS}",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-an",
        str(out_path)
    ])
    return out_path


def run(state):
    log.info(f"Stage 5: Assembling video for {state.song_slug}")

    scenes = json.loads(state.scene_plan_path.read_text(encoding="utf-8"))
    resolved = json.loads(state.resolved_scenes_path.read_text(encoding="utf-8"))
    beats_data = json.loads(state.beats_path.read_text(encoding="utf-8"))
    beats_ms = beats_data["beats_ms"]

    # Get full song duration from the audio file
    song_duration = MP3(state.song_path).info.length

    tmp_dir = Path(tempfile.mkdtemp(prefix="stage05_"))
    clip_list_path = tmp_dir / "clips.txt"
    clip_files = []
    gap_total = 0.0

    log.info(f"  Rendering {min(len(scenes), len(resolved))} clips...")
    prev_end_ms = 0
    for i, scene in enumerate(scenes):
        if i >= len(resolved):
            break

        start_ms = scene.get("start_ms", prev_end_ms)
        end_ms = scene.get("end_ms", start_ms + int(scene.get("duration", 5.0) * 1000))

        # Insert black gap if there's instrumental space between scenes
        gap_ms = start_ms - prev_end_ms
        if gap_ms > 100:
            gap_duration = gap_ms / 1000.0
            gap_path = _render_gap(gap_duration, tmp_dir, len(clip_files))
            clip_files.append(gap_path)
            gap_total += gap_duration

        asset = resolved[i]
        duration = (end_ms - start_ms) / 1000.0
        clip_path = _render_clip(
            scene, asset["local_path"], asset["resolved_type"], duration, tmp_dir, i
        )
        clip_files.append(clip_path)
        prev_end_ms = end_ms

    # Final gap after last lyric until song ends
    final_gap = song_duration - (prev_end_ms / 1000.0)
    if final_gap > 0.1:
        gap_path = _render_gap(final_gap, tmp_dir, len(clip_files))
        clip_files.append(gap_path)
        gap_total += final_gap

    if gap_total > 0:
        log.info(f"  Inserted {gap_total:.1f}s of gap padding across instrumental sections")

    # Write concat file list
    with open(clip_list_path, "w") as f:
        for cf in clip_files:
            f.write(f"file '{cf.resolve()}'\n")

    master_path = state.output_dir / "master_16x9.mp4"

    # Concat all clips, add audio, apply global filters
    log.info(f"  Concatenating and applying colour grade + loudnorm...")
    _run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(clip_list_path),
        "-i", str(state.song_path),
        "-vf", (f"eq=contrast=1.08:saturation=1.15:brightness=-0.02,"
                f"format=yuv420p"),
        "-af", "loudnorm=I=-16:LRA=11:TP=-1.5",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        str(master_path)
    ])

    # Cleanup
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

    state.master_video_path = master_path
    state.completed_stages[5] = True
    log.info(f"  Output: {state.output_dir}")
    return state
