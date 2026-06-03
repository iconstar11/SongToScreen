import hashlib, json, time
from pathlib import Path
import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from core.checkpoint import PipelineState
from core.config import settings
from core.logger import log

PEXELS_VIDEO_URL = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_URL = "https://api.pexels.com/v1/search"
PIXABAY_VIDEO_URL = "https://pixabay.com/api/videos/"
PIXABAY_PHOTO_URL = "https://pixabay.com/api/"

def _cache_path(search_term, duration, ext):
    bucket = round(duration / 0.5) * 0.5
    key = f"{search_term}_{bucket}"
    hashed = hashlib.sha256(key.encode()).hexdigest()[:12]
    subdir = "video" if ext == ".mp4" else "images"
    return settings.cache_dir / subdir / f"{hashed}{ext}"

@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
def _search_pexels_video(query, per_page=5):
    resp = requests.get(PEXELS_VIDEO_URL, headers={"Authorization": settings.pexels_api_key}, params={"query": query, "per_page": per_page}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("videos", [])

@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
def _search_pixabay_video(query, per_page=5):
    resp = requests.get(PIXABAY_VIDEO_URL, params={"key": settings.pixabay_api_key, "q": query, "per_page": per_page}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("hits", [])

@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
def _search_pexels_photo(query, per_page=5):
    resp = requests.get(PEXELS_PHOTO_URL, headers={"Authorization": settings.pexels_api_key}, params={"query": query, "per_page": per_page}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("photos", [])

@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=2, max=8))
def _search_pixabay_photo(query, per_page=5):
    resp = requests.get(PIXABAY_PHOTO_URL, params={"key": settings.pixabay_api_key, "q": query, "per_page": per_page}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("hits", [])

def _download_file(url, dest):
    try:
        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        log.debug(f"Download failed: {e}")
        return False

def _pick_best_video(videos, target_duration):
    if not videos: return None
    best, best_diff = None, float("inf")
    for v in videos:
        diff = abs(v.get("duration", 0) - target_duration)
        if diff < best_diff: best_diff, best = diff, v
    return best

def _get_video_download_url(video):
    files = video.get("video_files", [])
    if not files:
        videos_dict = video.get("videos", {})
        for quality in ["large", "medium", "small"]:
            if quality in videos_dict: return videos_dict[quality].get("url")
        return None
    for f in sorted(files, key=lambda x: x.get("width", 0) or 0, reverse=True):
        if f.get("width", 0) >= 1280: return f["link"]
    return files[0]["link"] if files else None

def _resolve_still_motion(scene, scene_index=-1, total=-1):
    mood = scene.get("visual_mood", "worship")
    terms = scene.get("search_terms", [f"{mood} gospel worship"])
    duration = scene.get("duration", 5.0)
    if scene_index >= 0: log.info(f"  [{scene_index + 1}/{total}] still_motion: {mood} ({duration:.1f}s)")
    for term in terms:
        cache_file = _cache_path(term, duration, ".jpg")
        if cache_file.exists(): return {"local_path": str(cache_file.resolve()), "resolved_type": "still_motion"}
        try:
            photos = _search_pexels_photo(term)
            if photos:
                src = photos[0].get("src", {})
                url = src.get("large") or src.get("original")
                if url and _download_file(url, cache_file): return {"local_path": str(cache_file.resolve()), "resolved_type": "still_motion"}
        except Exception as e: log.debug(f"  Pexels photo failed: {e}")
        try:
            photos = _search_pixabay_photo(term)
            if photos:
                url = photos[0].get("largeImageURL") or photos[0].get("webformatURL")
                if url and _download_file(url, cache_file): return {"local_path": str(cache_file.resolve()), "resolved_type": "still_motion"}
        except Exception as e: log.debug(f"  Pixabay photo failed: {e}")
    fallback = Path("assets/fallbacks/worship_default.jpg")
    return {"local_path": str(fallback.resolve()) if fallback.exists() else "", "resolved_type": "still_motion"}

def _resolve_stock_video(scene, scene_index, total):
    terms = scene.get("search_terms", ["gospel worship"])
    duration = scene.get("duration", 5.0)
    log.info(f"  [{scene_index + 1}/{total}] stock_video: {terms[0][:50]} ({duration:.1f}s)")
    for term in terms:
        cache_file = _cache_path(term, duration, ".mp4")
        if cache_file.exists(): return {"local_path": str(cache_file.resolve()), "resolved_type": "stock_video"}
        try:
            videos = _search_pexels_video(term)
            best = _pick_best_video(videos, duration)
            if best:
                url = _get_video_download_url(best)
                if url and _download_file(url, cache_file): return {"local_path": str(cache_file.resolve()), "resolved_type": "stock_video"}
        except Exception as e: log.debug(f"  Pexels video failed: {e}")
        try:
            videos = _search_pixabay_video(term)
            best = _pick_best_video(videos, duration)
            if best:
                url = _get_video_download_url(best)
                if url and _download_file(url, cache_file): return {"local_path": str(cache_file.resolve()), "resolved_type": "stock_video"}
        except Exception as e: log.debug(f"  Pixabay video failed: {e}")
    log.info(f"    No video found, downgrading to still_motion")
    return _resolve_still_motion(scene, -1, -1)

def _resolve_scene(scene, index, total):
    scene_type = scene.get("scene_type", "still_motion")
    if scene_type == "text_motion": return {"local_path": "", "resolved_type": "text_motion"}
    if scene_type == "stock_video":
        result = _resolve_stock_video(scene, index, total)
        if result["resolved_type"] == "stock_video": return result
    return _resolve_still_motion(scene, index, total)

def run(state):
    log.info(f"Stage 4: Acquiring assets for {state.song_slug}")
    scenes = json.loads(state.scene_plan_path.read_text(encoding="utf-8"))
    log.info(f"  {len(scenes)} scenes to resolve")
    resolved = []
    for i, scene in enumerate(scenes):
        result = _resolve_scene(scene, i, len(scenes))
        resolved.append({"scene_id": scene["scene_id"], **result})
        time.sleep(0.3)
    resolved_path = state.output_dir / "resolved_scenes.json"
    resolved_path.write_text(json.dumps(resolved, indent=2), encoding="utf-8")
    state.resolved_scenes_path = resolved_path
    state.completed_stages[4] = True
    video_count = sum(1 for r in resolved if r["resolved_type"] == "stock_video")
    still_count = sum(1 for r in resolved if r["resolved_type"] == "still_motion")
    text_count = sum(1 for r in resolved if r["resolved_type"] == "text_motion")
    log.info(f"  Resolved: {video_count} video, {still_count} still, {text_count} text")
    log.info(f"  Output: {state.output_dir}")
    return state
