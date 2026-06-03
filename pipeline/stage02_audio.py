import json
from io import BytesIO
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from openai import OpenAI

from core.checkpoint import PipelineState
from core.config import settings
from core.exceptions import StageError
from core.logger import log

# OpenAI accepts max 25 MB per request
MAX_CHUNK_BYTES = 25 * 1024 * 1024


def _chunk_audio(audio_path: Path) -> list[bytes]:
    """Read audio and split into chunks under 25 MB if needed."""
    data, sr = sf.read(str(audio_path), dtype="float32")

    # Convert to mono 16-bit PCM in memory, estimate WAV size
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = (data * 32767).astype(np.int16)

    buffer = BytesIO()
    sf.write(buffer, data, sr, format="WAV")
    wav_bytes = buffer.getvalue()

    if len(wav_bytes) <= MAX_CHUNK_BYTES:
        return [wav_bytes]

    # Split into roughly equal chunks at silence boundaries
    chunk_count = (len(wav_bytes) // MAX_CHUNK_BYTES) + 2
    chunk_size = len(data) // chunk_count
    chunks = []
    for i in range(chunk_count):
        start = i * chunk_size
        end = min((i + 1) * chunk_size, len(data))
        chunk = data[start:end]
        buf = BytesIO()
        sf.write(buf, chunk, sr, format="WAV")
        chunks.append(buf.getvalue())
    return chunks


def _transcribe(audio_path: Path) -> list[dict]:
    """Transcribe via OpenAI Whisper API, return word-level alignment."""
    client = OpenAI(api_key=settings.openai_api_key)
    chunks = _chunk_audio(audio_path)
    words = []
    time_offset = 0.0
    chunk_duration = 0.0

    for i, chunk in enumerate(chunks):
        log.info(f"  Transcribing chunk {i + 1}/{len(chunks)} via Whisper API...")
        chunk_file = BytesIO(chunk)
        chunk_file.name = f"chunk_{i}.wav"

        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=chunk_file,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

        for w in response.words:
            # OpenAI SDK v2 returns TranscriptionWord objects, not dicts
            if hasattr(w, "word"):
                word_text, w_start, w_end = w.word, w.start, w.end
            else:
                word_text, w_start, w_end = w["word"], w["start"], w["end"]
            words.append({
                "word": word_text,
                "start_ms": round((w_start + time_offset) * 1000),
                "end_ms": round((w_end + time_offset) * 1000),
            })

        # Track chunk duration for offset calculation
        data, sr = sf.read(BytesIO(chunk), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        chunk_duration = len(data) / sr
        time_offset += chunk_duration

    return words


def _detect_beats(audio_path: Path) -> tuple[float, list[int]]:
    """Detect tempo and beat timestamps in milliseconds."""
    y, sr = librosa.load(str(audio_path), sr=None)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    tempo = float(np.atleast_1d(tempo).flatten()[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    beats_ms = [round(t * 1000) for t in beat_times.tolist()]
    return tempo, beats_ms


def _detect_segments(audio_path: Path) -> list[dict]:
    """Detect structural segments (verse/chorus/bridge) with mood hints."""
    y, sr = librosa.load(str(audio_path), sr=None)

    # Compute spectral features for segmentation
    S = np.abs(librosa.stft(y))
    rms = librosa.feature.rms(S=S).flatten()
    spectral_centroid = librosa.feature.spectral_centroid(S=S).flatten()
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr)

    # Use RMS energy to find boundaries
    rms_smooth = np.convolve(rms, np.ones(20) / 20, mode="same")
    threshold = np.median(rms_smooth) * 1.3
    above = rms_smooth > threshold
    boundaries = np.where(np.diff(above.astype(int)))[0]

    if len(boundaries) < 2:
        duration_ms = round(len(y) / sr * 1000)
        return [{
            "segment_type": "verse",
            "start_ms": 0,
            "end_ms": duration_ms,
            "mood": _classify_mood(rms, spectral_centroid, 0, len(rms)),
        }]

    segments = []
    seg_starts = [0] + boundaries.tolist() + [len(rms)]
    for i in range(len(seg_starts) - 1):
        a, b = seg_starts[i], seg_starts[i + 1]
        if b <= a:
            continue
        start_ms = round(float(times[a]) * 1000)
        end_ms = round(float(times[min(b, len(times) - 1)]) * 1000)
        if end_ms - start_ms < 2000:
            continue

        mood = _classify_mood(rms, spectral_centroid, a, b)
        seg_type = _classify_segment_type(i, len(seg_starts) - 1)
        segments.append({
            "segment_type": seg_type,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "mood": mood,
        })

    return segments


def _classify_mood(rms: np.ndarray, centroid: np.ndarray, a: int, b: int) -> str:
    """Classify segment mood from spectral features."""
    rms_slice = rms[a:b] if b > a else rms[a:]
    cent_slice = centroid[a:b] if b > a else centroid[a:]

    energy = float(np.mean(rms_slice))
    brightness = float(np.mean(cent_slice))

    if energy > np.median(rms) * 1.2 and brightness > np.median(centroid) * 1.1:
        return "triumphant"
    elif energy > np.median(rms) * 1.0:
        return "energetic"
    else:
        return "contemplative"


def _classify_segment_type(index: int, total: int) -> str:
    """Heuristic segment type classification by position."""
    if total <= 3:
        return "verse"
    if index == 0:
        return "verse"
    if index == total - 1:
        return "verse"
    if index % 2 == 1:
        return "chorus"
    return "bridge"


def run(state: PipelineState) -> PipelineState:
    audio_path = state.song_path
    log.info(f"Stage 2: Analysing {audio_path.name}")

    # 1. Whisper API transcription
    words = _transcribe(audio_path)
    log.info(f"  Transcription: {len(words)} words")

    alignment_path = state.output_dir / "alignment.json"
    alignment_path.write_text(json.dumps(words, indent=2), encoding="utf-8")
    state.alignment_path = alignment_path

    # 2. Beat detection
    tempo, beats_ms = _detect_beats(audio_path)
    log.info(f"  Tempo: {tempo:.1f} BPM | {len(beats_ms)} beats")

    beats_path = state.output_dir / "beats.json"
    beats_path.write_text(json.dumps({"tempo_bpm": tempo, "beats_ms": beats_ms}, indent=2), encoding="utf-8")
    state.beats_path = beats_path

    # 3. Segment detection
    segments = _detect_segments(audio_path)
    log.info(f"  Segments: {len(segments)} detected")

    segments_path = state.output_dir / "segments.json"
    segments_path.write_text(json.dumps(segments, indent=2), encoding="utf-8")
    state.segments_path = segments_path

    state.completed_stages[2] = True
    log.info(f"  Output: {state.output_dir}")
    return state
