import json
import re
from pathlib import Path

import jinja2
from mistralai import Mistral
from openai import RateLimitError, APITimeoutError
from pydantic import BaseModel, Field, ValidationError

from core.checkpoint import PipelineState
from core.config import settings
from core.llm import get_deepseek_client
from core.logger import log


class SceneEntry(BaseModel):
    scene_id: str
    lyric: str
    start_ms: int
    end_ms: int
    duration: float
    segment_type: str
    scene_type: str = Field(default="stock_video", pattern="^(stock_video|still_motion|text_motion)$")
    search_terms: list[str] = Field(default_factory=list)
    visual_mood: str = ""
    camera_motion: str = Field(default="slow_push_in")
    transition_in: str = Field(default="fade", pattern="^(fade|dissolve|cut)$")
    transition_out: str = Field(default="fade", pattern="^(fade|dissolve|cut)$")
    caption_style: str = Field(default="karaoke_2line")
    fallback_strategy: str = Field(default="still_motion", pattern="^(still_motion|text_motion)$")


class ScenePlan(BaseModel):
    scenes: list[SceneEntry]


def _group_words_by_segments(alignment: list[dict], segments: list[dict]) -> list[dict]:
    """Assign each word to its containing segment."""
    for seg in segments:
        seg["words"] = [
            w for w in alignment
            if w["start_ms"] >= seg["start_ms"] and w["end_ms"] <= seg["end_ms"]
        ]
    return segments


def _build_lyric_line(segment: dict) -> str:
    """Reconstruct a lyric line from words in a segment."""
    line = ""
    last_end = None
    for w in segment.get("words", []):
        start = w["start_ms"]
        # Insert line break if gap > 2 seconds between words
        if last_end is not None and start - last_end > 2000:
            line += "\n"
        line += w["word"] + " "
        last_end = w["end_ms"]
    return line.strip()


def _extract_json_array(raw: str) -> list[dict]:
    """Extract JSON array from LLM response, handling markdown code blocks and thinking tags."""
    text = raw.strip()
    # Remove DeepSeek thinking tags if present
    text = re.sub(r"<\|thinker\|>.*?<\|/thinker\|>", "", text, flags=re.DOTALL)
    # Extract from markdown code block if present
    if "```" in text:
        lines = text.split("\n")
        json_lines = []
        in_block = False
        for line in lines:
            if line.startswith("```"):
                in_block = not in_block
                continue
            if in_block:
                json_lines.append(line)
        text = "\n".join(json_lines)
    # Find JSON array in remaining text
    match = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
    if match:
        text = match.group()
    return json.loads(text)


def _normalize_scene(scene: dict, index: int) -> dict:
    """Normalize LLM output to match SceneEntry schema, handling common field name variations."""
    # Map common alternative field names
    if "start_time" in scene and "start_ms" not in scene:
        scene["start_ms"] = round(scene.pop("start_time") * 1000)
    if "end_time" in scene and "end_ms" not in scene:
        scene["end_ms"] = round(scene.pop("end_time") * 1000)
    # Ensure required fields exist
    scene.setdefault("scene_id", f"sc_{index + 1:03d}")
    scene.setdefault("lyric", "")
    scene.setdefault("segment_type", "verse")
    scene.setdefault("duration", round((scene.get("end_ms", 0) - scene.get("start_ms", 0)) / 1000, 1))
    # search_terms may come as a single string — wrap in list
    if isinstance(scene.get("search_terms"), str):
        scene["search_terms"] = [scene["search_terms"]]
    scene.setdefault("search_terms", ["gospel worship"])
    scene.setdefault("visual_mood", "contemplative")
    scene.setdefault("camera_motion", "slow_push_in")
    scene.setdefault("transition_in", "fade")
    scene.setdefault("transition_out", "fade")
    scene.setdefault("caption_style", "karaoke_2line")
    scene.setdefault("fallback_strategy", "still_motion")
    return scene


def _llm_call(messages: list[dict]) -> str:
    """Call LLM with DeepSeek primary, Mistral fallback. Returns raw content."""
    client = get_deepseek_client()
    try:
        response = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=messages,
            timeout=15,
        )
        return response.choices[0].message.content
    except (RateLimitError, APITimeoutError) as e:
        log.warning(f"  DeepSeek failed ({e}), switching to Mistral")
        mistral = Mistral(api_key=settings.mistral_api_key)
        response = mistral.chat.complete(
            model=settings.mistral_model,
            messages=messages,
        )
        return response.choices[0].message.content


def _build_prompt(state: PipelineState) -> str:
    """Render the Jinja2 prompt template with song data."""
    alignment = json.loads(state.alignment_path.read_text(encoding="utf-8"))
    segments = json.loads(state.segments_path.read_text(encoding="utf-8"))

    segments = _group_words_by_segments(alignment, segments)

    env = jinja2.Environment(loader=jinja2.FileSystemLoader("prompts"))
    template = env.get_template("scene_plan.j2")

    # Build lyric display strings per segment
    for seg in segments:
        seg["lyric_line"] = _build_lyric_line(seg)

    # Calculate total duration from alignment
    total_duration = 0.0
    if alignment:
        total_duration = alignment[-1]["end_ms"] / 1000.0

    return template.render(
        artist="Unknown Artist",
        title=state.song_slug.replace("_", " ").title(),
        total_duration_sec=round(total_duration, 1),
        segments=segments,
    )


def _extract_scene_metadata(alignment: list[dict], segments: list[dict]) -> list[dict]:
    """Build minimal scene entries aligned to segments when LLM is unavailable."""
    scenes = []
    for i, seg in enumerate(segments):
        words_in_seg = seg.get("words", [])
        if not words_in_seg:
            continue
        lyric = " ".join(w["word"] for w in words_in_seg)
        duration = (seg["end_ms"] - seg["start_ms"]) / 1000.0
        scenes.append({
            "scene_id": f"sc_{i + 1:03d}",
            "lyric": lyric,
            "start_ms": seg["start_ms"],
            "end_ms": seg["end_ms"],
            "duration": round(duration, 1),
            "segment_type": seg.get("segment_type", "verse"),
            "scene_type": "stock_video" if seg.get("mood") in ("energetic", "triumphant") else "still_motion",
            "search_terms": ["gospel worship"],
            "visual_mood": seg.get("mood", "contemplative"),
            "camera_motion": "slow_push_in",
            "transition_in": "fade",
            "transition_out": "fade",
            "caption_style": "karaoke_2line",
            "fallback_strategy": "still_motion",
        })
    return scenes


def run(state: PipelineState) -> PipelineState:
    log.info(f"Stage 3: Planning scenes for {state.song_slug}")

    prompt = _build_prompt(state)
    messages = [
        {"role": "system", "content": "You are a music video director. Return ONLY a JSON array of scene objects. No markdown, no explanations."},
        {"role": "user", "content": prompt},
    ]

    scenes = None
    try:
        raw = _llm_call(messages)
        scenes_data = _extract_json_array(raw)
        scenes_data = [_normalize_scene(s, i) for i, s in enumerate(scenes_data)]
        plan = ScenePlan.model_validate({"scenes": scenes_data})
        scenes = [s.model_dump() for s in plan.scenes]
        log.info(f"  LLM returned {len(scenes)} scenes")
    except (json.JSONDecodeError, ValidationError) as e:
        log.warning(f"  JSON validation failed, retrying...")
        messages.append({"role": "user", "content": "Return ONLY a JSON array. Each object must have: scene_id, lyric, start_ms, end_ms, duration, segment_type, scene_type, search_terms (array), visual_mood, camera_motion, transition_in, transition_out, caption_style, fallback_strategy."})
        try:
            raw = _llm_call(messages)
            scenes_data = _extract_json_array(raw)
            scenes_data = [_normalize_scene(s, i) for i, s in enumerate(scenes_data)]
            plan = ScenePlan.model_validate({"scenes": scenes_data})
            scenes = [s.model_dump() for s in plan.scenes]
            log.info(f"  LLM returned {len(scenes)} scenes on retry")
        except Exception as e2:
            log.warning(f"  Retry failed, using rule-based plan")
    except Exception as e:
        log.warning(f"  LLM failed ({e}), using rule-based plan")

    if scenes is None:
        alignment = json.loads(state.alignment_path.read_text(encoding="utf-8"))
        segments = json.loads(state.segments_path.read_text(encoding="utf-8"))
        segments = _group_words_by_segments(alignment, segments)
        scenes = _extract_scene_metadata(alignment, segments)
        log.info(f"  Rule-based plan: {len(scenes)} scenes")

    scene_plan_path = state.output_dir / "scene_plan.json"
    scene_plan_path.write_text(json.dumps(scenes, indent=2), encoding="utf-8")
    state.scene_plan_path = scene_plan_path
    state.completed_stages[3] = True

    log.info(f"  Output: {state.output_dir}")
    return state
