from pathlib import Path

import mutagen
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from pydantic import BaseModel, field_validator

from core.checkpoint import PipelineState
from core.config import settings
from core.exceptions import StageError
from core.logger import log


class AssetBundle(BaseModel):
    audio_path: Path
    lyrics: str
    title: str
    artist: str
    duration_sec: float
    bpm_hint: float | None = None

    @field_validator("audio_path")
    @classmethod
    def audio_must_exist(cls, v: Path) -> Path:
        if not v.exists():
            raise ValueError(f"Audio file not found: {v}")
        return v

    @field_validator("duration_sec")
    @classmethod
    def duration_minimum(cls, v: float) -> float:
        if v < 30:
            raise ValueError(f"Duration {v:.0f}s is below 30s minimum")
        return v

    @field_validator("lyrics")
    @classmethod
    def lyrics_not_empty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped or len(stripped) < 20:
            raise ValueError("Lyrics too short or empty")
        return stripped


def _extract_metadata(audio_path: Path) -> dict:
    """Extract title, artist, duration, and BPM hint from MP3 tags."""
    audio = MP3(audio_path)
    duration = audio.info.length

    title = str(audio_path.stem)
    artist = "Unknown Artist"
    bpm_hint = None

    try:
        tags = ID3(audio_path)
    except Exception:
        tags = None

    if tags:
        if "TIT2" in tags:
            title = str(tags["TIT2"])
        if "TPE1" in tags:
            artist = str(tags["TPE1"])
        if "TBPM" in tags:
            try:
                bpm_hint = float(str(tags["TBPM"]))
            except (ValueError, TypeError):
                pass

    return {"title": title, "artist": artist, "duration_sec": duration, "bpm_hint": bpm_hint}


def _fetch_lyrics(title: str, artist: str, audio_path: Path) -> str:
    """Fetch lyrics via Genius API, falling back to local .txt file."""
    import lyricsgenius

    genius = lyricsgenius.Genius(settings.genius_access_token, remove_section_headers=True)

    # Try Genius search
    queries = [f"{title} {artist}", title]
    for query in queries:
        try:
            song = genius.search_song(query, artist=artist if artist != "Unknown Artist" else "")
            if song and song.lyrics and len(song.lyrics.strip()) > 20:
                return song.lyrics.strip()
        except Exception as e:
            log.debug(f"Genius search failed for '{query}': {e}")
            continue

    # Fallback to local .txt
    txt_path = audio_path.with_suffix(".txt")
    if txt_path.exists():
        lyrics = txt_path.read_text(encoding="utf-8").strip()
        if len(lyrics) > 20:
            log.info(f"Using local lyrics from {txt_path.name}")
            return lyrics

    raise StageError(1, "No lyrics available — Genius API failed and no local .txt file found")


def _create_slug(title: str, artist: str) -> str:
    """Create a clean directory-safe slug from the song title."""
    import re

    clean = title
    # Remove "Artist - " prefix if present
    clean = re.sub(r"^.*?\s-\s", "", clean)
    # Remove (feat. ...) blocks
    clean = re.sub(r"\(feat\..*?\)", "", clean, flags=re.IGNORECASE)
    # Remove trailing parentheticals like (Bless the Lord) or (Live)
    clean = re.sub(r"\([^)]*\)", "", clean)
    # Remove everything after | (channel/album info)
    clean = re.sub(r"\|.*$", "", clean)
    clean = clean.strip()

    slug = re.sub(r"[^a-zA-Z0-9]+", "_", clean).strip("_").lower()
    return slug[:40] or "unknown_song"


def run(state: PipelineState) -> PipelineState:
    audio_path = state.song_path
    log.info(f"Stage 1: Ingesting {audio_path.name}")

    meta = _extract_metadata(audio_path)
    log.info(f"  Title: {meta['title']} | Artist: {meta['artist']} | Duration: {meta['duration_sec']:.1f}s")

    lyrics = _fetch_lyrics(meta["title"], meta["artist"], audio_path)
    log.info(f"  Lyrics: {len(lyrics)} characters")

    bundle = AssetBundle(
        audio_path=audio_path,
        lyrics=lyrics,
        title=meta["title"],
        artist=meta["artist"],
        duration_sec=meta["duration_sec"],
        bpm_hint=meta["bpm_hint"],
    )

    slug = _create_slug(bundle.title, bundle.artist)
    output_dir = settings.outputs_dir / slug
    output_dir.mkdir(parents=True, exist_ok=True)

    lyrics_path = output_dir / "lyrics.txt"
    lyrics_path.write_text(bundle.lyrics, encoding="utf-8")

    state.song_slug = slug
    state.output_dir = output_dir
    state.completed_stages[1] = True

    log.info(f"  Output: {output_dir}")
    return state
