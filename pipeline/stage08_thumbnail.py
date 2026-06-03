import json, subprocess, re, textwrap
from pathlib import Path
from PIL import Image

from mistralai import Mistral
from openai import RateLimitError, APITimeoutError

from core.checkpoint import PipelineState
from core.config import settings
from core.llm import get_deepseek_client
from core.logger import log

THUMB_COUNT = 5

def _extract_keyframes(video_path, output_dir):
    """Extract top 5 keyframes using scene-change detection."""
    log.info(f"  Extracting keyframes...")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video_path),
        "-vf", r"select=gt(scene\,0.4),scale=1920:1080",
        "-vsync", "vfr", "-frames:v", str(THUMB_COUNT),
        str(output_dir / "thumb_%d.jpg")
    ], capture_output=True, text=True)

    frames = sorted(output_dir.glob("thumb_*.jpg"))
    return frames

def _score_frame(frame_path):
    """Score a frame by brightness variance. Higher = better. Rejects near-black/white."""
    img = Image.open(frame_path).convert("L")
    pixels = list(img.getdata())  # type: ignore[arg-type]
    mean = sum(pixels) / len(pixels)
    # Reject near-black (< 30) or near-white (> 225)
    if mean < 30 or mean > 225:
        return -1
    variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)
    return variance

def _apply_thumbnail_template(frame_path, output_path, title, artist):
    """Apply branded overlay to thumbnail."""
    img = Image.open(frame_path).convert("RGB")
    # Dark gradient overlay at bottom
    from PIL import ImageDraw, ImageFont
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    h = img.height
    for i in range(300):
        alpha = int(180 * (1 - i / 300))
        draw.rectangle([(0, h - 300 + i), (img.width, h)], fill=(0, 0, 0, alpha))

    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    # Draw title text
    draw = ImageDraw.Draw(img)
    try:
        font_large = ImageFont.truetype("arial.ttf", 72)
        font_small = ImageFont.truetype("arial.ttf", 36)
    except Exception:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    short_title = textwrap.shorten(title, width=30, placeholder="...")
    draw.text((60, h - 220), short_title, fill=(255, 255, 255), font=font_large)
    draw.text((60, h - 140), artist, fill=(200, 200, 200), font=font_small)
    draw.text((60, h - 90), "Official Lyric Video", fill=(255, 215, 0), font=font_small)

    img.save(output_path, "JPEG", quality=90)

def _llm_chat(messages: list[dict[str, str]]) -> str:
    """Call DeepSeek primary, Mistral fallback."""
    client = get_deepseek_client()
    try:
        response = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=messages,  # type: ignore[arg-type]
            timeout=15,
        )
        return response.choices[0].message.content or ""
    except (RateLimitError, APITimeoutError) as e:
        log.warning(f"  DeepSeek failed ({e}), switching to Mistral")
        mistral = Mistral(api_key=settings.mistral_api_key)
        response = mistral.chat.complete(
            model=settings.mistral_model,
            messages=messages,  # type: ignore[arg-type]
        )
        if response.choices:
            content = response.choices[0].message.content
            return content if isinstance(content, str) else str(content)
        return ""

def _extract_json(raw):
    """Extract JSON object from LLM response."""
    text = raw.strip()
    text = re.sub(r"<\|thinker\|>.*?<\|/thinker\|>", "", text, flags=re.DOTALL)
    if "```" in text:
        text = re.sub(r"```\w*\n?", "", text)
        text = text.replace("```", "")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text)

def _generate_metadata(state, thumbnail_frame):
    """Generate YouTube metadata via LLM."""
    lyrics = state.output_dir / "lyrics.txt"
    lyrics_text = lyrics.read_text(encoding="utf-8")[:500] if lyrics.exists() else ""

    prompt = f"""Generate YouTube metadata for a gospel worship lyric video.

Song file: {state.song_path.name}
First 500 chars of lyrics: {lyrics_text}

Return ONLY a JSON object with these fields:
- title: "Song Title | Artist | Official Lyric Video" format
- description: SEO-optimized description (200-300 words), include the artist name, genre tags, and a call to subscribe
- tags: array of 10-15 relevant YouTube tags
- category: "Music"
- thumbnail_frame: "{thumbnail_frame.name}"
"""

    try:
        raw = _llm_chat([
            {"role": "system", "content": "You are a YouTube SEO expert. Return ONLY valid JSON."},
            {"role": "user", "content": prompt},
        ])
        return _extract_json(raw)
    except Exception as e:
        log.warning(f"  LLM metadata failed ({e}), using defaults")
        stem = state.song_path.stem[:80]
        return {
            "title": f"{stem} | Official Lyric Video",
            "description": f"Gospel worship lyric video for {stem}. Subscribe for more gospel music videos.",
            "tags": ["gospel music", "worship song", "christian music", "lyric video"],
            "category": "Music",
            "thumbnail_frame": thumbnail_frame.name,
        }

def run(state):
    log.info(f"Stage 8: Thumbnail & metadata for {state.song_slug}")

    # Extract keyframes from master (uncaptioned) so thumbnail text
    # doesn't overlap with karaoke lyrics visible in the frame
    video_path = state.master_video_path
    frames = _extract_keyframes(video_path, state.output_dir)
    if not frames:
        log.warning("  No keyframes extracted, using fallback")
        return state

    # Score and pick best frame
    scored = [(f, _score_frame(f)) for f in frames]
    scored.sort(key=lambda x: x[1], reverse=True)
    best_frame, best_score = scored[0]
    log.info(f"  Best frame: {best_frame.name} (score: {best_score:.0f})")

    # Generate metadata
    metadata = _generate_metadata(state, best_frame)
    metadata_path = state.output_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    state.metadata_path = metadata_path
    log.info(f"  Title: {metadata['title'][:80]}")

    # Create thumbnail with overlay
    thumbnail_path = state.output_dir / "thumbnail.jpg"
    title = metadata.get("title", state.song_slug).split("|")[0].strip()
    artist_name = state.song_path.stem[:50]
    _apply_thumbnail_template(best_frame, thumbnail_path, title, artist_name)
    state.thumbnail_path = thumbnail_path

    state.completed_stages[8] = True
    log.info(f"  Output: {state.output_dir}")
    return state
