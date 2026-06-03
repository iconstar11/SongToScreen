import json, subprocess, shutil, re
from pathlib import Path
from core.checkpoint import PipelineState
from core.logger import log

OUTPUT_W, OUTPUT_H = 1920, 1080
FONT_SIZE = 52
ACCENT_COLOR = "#FFD700"
TEXT_COLOR = "white"
SHADOW_COLOR = "black"
MAX_CHARS_PER_LINE = 40
CURRENT_LINE_Y = OUTPUT_H - 200
NEXT_LINE_Y = OUTPUT_H - 130

def _escape_ffmpeg_text(word):
    """Escape characters that break ffmpeg drawtext."""
    w = word.replace("'", "")
    w = w.replace(":", " ")
    w = w.replace("\\", "")
    return w

def _word_x(word, line_words, font_size):
    line_text = " ".join(line_words)
    word_idx = line_text.find(word)
    prefix = line_text[:word_idx]
    char_width = font_size * 0.55
    total_width = len(line_text) * char_width
    x = (OUTPUT_W - total_width) / 2 + len(prefix) * char_width
    return max(0, int(x))

def _group_into_timed_lines(alignment):
    """Group words into lines with start/end times, respecting gaps and max chars."""
    lines = []
    current_words = []
    current_chars = 0
    last_end = None

    for w in alignment:
        word_text = _escape_ffmpeg_text(w["word"].strip())
        if not word_text:
            continue
        word_len = len(word_text)
        start = w["start_ms"]

        # New line on gap > 2s
        if last_end is not None and start - last_end > 2000:
            if current_words:
                lines.append({"words": current_words})
            current_words = []
            current_chars = 0

        # New line on overflow
        if current_chars + word_len + (1 if current_words else 0) > MAX_CHARS_PER_LINE:
            if current_words:
                lines.append({"words": current_words})
            current_words = [{"text": word_text, "start_ms": w["start_ms"], "end_ms": w["end_ms"]}]
            current_chars = word_len
        else:
            current_words.append({"text": word_text, "start_ms": w["start_ms"], "end_ms": w["end_ms"]})
            current_chars += word_len + (1 if current_words else 0)
        last_end = w["end_ms"]

    if current_words:
        lines.append({"words": current_words})

    # Annotate each line with its time window
    for line in lines:
        words = line["words"]
        line["start_sec"] = words[0]["start_ms"] / 1000.0
        line["end_sec"] = words[-1]["end_ms"] / 1000.0

    return lines

def _build_filters(lines):
    """Build drawtext filters: only current + next line visible at any time."""
    filters = []

    for i, line in enumerate(lines):
        words = line["words"]
        line_words = [w["text"] for w in words]
        is_last = (i == len(lines) - 1)
        next_start = lines[i + 1]["start_sec"] if not is_last else line["end_sec"] + 10

        # This line shows when it's "current" (its own window) or "next" (previous line's window)
        if i == 0:
            show_start = line["start_sec"]
        else:
            show_start = lines[i - 1]["start_sec"]
        show_end = line["end_sec"] if is_last else next_start

        # Determine Y: first line of the 2-line window uses CURRENT_LINE_Y
        # When this line is the "current" one it's on the current row
        # When it's the "next" one it's on the next row
        # Simplified: alternate Y based on position in 2-line window
        y_current = CURRENT_LINE_Y
        y_next = NEXT_LINE_Y

        for word in words:
            x = _word_x(word["text"], line_words, FONT_SIZE)
            t_start = word["start_ms"] / 1000.0
            t_end = word["end_ms"] / 1000.0

            # Current line position: word is gold during its window, white otherwise
            # Visible during current line + next line display window
            # Use gte/lte arithmetic instead of between() to avoid comma parsing issues
            active = (
                f"drawtext=text='{word['text']}':fontsize={FONT_SIZE}:fontcolor={ACCENT_COLOR}:"
                f"shadowcolor={SHADOW_COLOR}:shadowx=3:shadowy=3:"
                f"x={x}:y={y_current}:"
                f"enable='gte(t\\,{t_start})*lt(t\\,{t_end})'"
            )
            inactive = (
                f"drawtext=text='{word['text']}':fontsize={FONT_SIZE}:fontcolor={TEXT_COLOR}:"
                f"shadowcolor={SHADOW_COLOR}:shadowx=3:shadowy=3:"
                f"x={x}:y={y_current}:"
                f"enable='gte(t\\,{show_start})*lt(t\\,{show_end})*not(gte(t\\,{t_start})*lt(t\\,{t_end}))'"
            )
            filters.append(active)
            filters.append(inactive)

    return ",".join(filters)

def run(state):
    log.info(f"Stage 6: Adding karaoke captions for {state.song_slug}")

    alignment = json.loads(state.alignment_path.read_text(encoding="utf-8"))
    lines = _group_into_timed_lines(alignment)
    log.info(f"  {len(lines)} caption lines across {len(alignment)} words")

    captioned_path = state.output_dir / "captioned_16x9.mp4"
    drawtext_chain = _build_filters(lines)

    filter_script = f"[0:v]{drawtext_chain}[outv]"
    filter_file = state.output_dir / "_tmp_filter.txt"
    filter_file.write_text(filter_script, encoding="utf-8")

    log.info(f"  Rendering captioned video...")
    result = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(state.master_video_path),
        "-filter_complex_script", str(filter_file),
        "-map", "[outv]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(captioned_path)
    ], capture_output=True, text=True)

    filter_file.unlink(missing_ok=True)

    if result.returncode != 0:
        log.warning(f"  ffmpeg: {result.stderr[:300]}")
        shutil.copy(state.master_video_path, captioned_path)
        log.warning(f"  Captions failed, using master as fallback")

    state.captioned_video_path = captioned_path
    state.completed_stages[6] = True
    log.info(f"  Output: {state.output_dir}")
    return state
