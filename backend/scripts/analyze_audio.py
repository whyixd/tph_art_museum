"""Write a beat map (backend/tracks/<id>.json) from a WAV file. Development machine only.

librosa 不是後端的相依套件，執行時臨時帶入：
librosa is not a backend dependency; bring it in for the run:

    uv run --with librosa python backend/scripts/analyze_audio.py audio/光林_聖誕5.wav \
        --id christmas-5 --title 光林_聖誕5 --grid

--grid：速度穩定的曲子，把偵測到的拍點擬合成等距格線（去掉逐拍的偵測抖動）。
速度不穩的曲子（例如環境曲）不要加，之後在前端手動修正。
--grid fits detected beats to an evenly spaced grid (removing per-beat detection jitter) for steady-tempo tracks.
Leave it off for tracks with a drifting tempo (e.g. the ambient track) and correct them by hand later.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import librosa
import numpy as np

TRACKS = Path(__file__).resolve().parents[1] / "tracks"
SAMPLE_RATE = 22050
HOP = 128  # 5.8 ms per onset frame


def analyze(path: Path, grid: bool) -> tuple[float, float | None, list[float], str]:
    """(duration, tempo or None, beat times, description of the method)."""
    y, sr = librosa.load(path, sr=SAMPLE_RATE, mono=True)
    duration = len(y) / sr
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    tempo, frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=HOP)
    beats = librosa.frames_to_time(frames, sr=sr, hop_length=HOP)
    tempo_value = float(np.atleast_1d(tempo)[0])
    method = f"librosa {librosa.__version__} beat_track (hop {HOP / sr * 1000:.1f} ms)"
    if not grid or len(beats) < 8:
        return duration, round(tempo_value, 2) if tempo_value > 0 else None, [round(float(t), 4) for t in beats], method
    # 擬合 t = start + index × period：先以偵測速度編號，反覆以擬合結果重新編號、排除偏離超過 40 ms 的拍
    # （偵測偶爾整段錯相位），最後往前後延伸到整首。
    # Fit t = start + index * period: number beats by the detected tempo, then repeatedly renumber from the fit and
    # drop beats more than 40 ms off (the tracker occasionally slips phase for a stretch); extend over the track.
    slope, intercept = 60 / tempo_value, float(beats[0])
    keep = np.ones(len(beats), dtype=bool)
    for _ in range(6):
        index = np.round((beats - intercept) / slope)
        slope, intercept = np.polyfit(index[keep], beats[keep], 1)
        residual = beats - (intercept + slope * np.round((beats - intercept) / slope))
        keep = np.abs(residual) <= 0.04
    first = intercept - np.floor(intercept / slope) * slope
    grid_beats = np.arange(first, duration, slope)
    rms = np.sqrt(np.mean(residual[keep] ** 2)) * 1000
    method += (f", grid fit {60 / slope:.3f} BPM ({keep.sum()}/{len(beats)} beats within 40 ms, "
               f"residual RMS {rms:.1f} ms)")
    return duration, round(60 / slope, 3), [round(float(t), 4) for t in grid_beats], method


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wav", type=Path)
    parser.add_argument("--id", required=True, help="track id: a-z, 0-9 and -")
    parser.add_argument("--title", required=True)
    parser.add_argument("--grid", action="store_true", help="fit an evenly spaced grid (steady tempo only)")
    parser.add_argument("--beats-per-bar", type=int, default=4)
    parser.add_argument("--first-downbeat", type=int, default=0, help="index of the first beat that starts a bar")
    parser.add_argument("--out", type=Path, default=None, help="default: backend/tracks/<id>.json")
    args = parser.parse_args(argv)
    duration, tempo, beats, method = analyze(args.wav, args.grid)
    document = {
        "schemaVersion": 1, "id": args.id, "title": args.title, "file": args.wav.name,
        "durationSeconds": round(duration, 4), "tempo": tempo,
        "beatsPerBar": args.beats_per_bar, "firstDownbeat": args.first_downbeat % args.beats_per_bar,
        "beats": beats, "source": method,
    }
    out = args.out or TRACKS / f"{args.id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{out}: {len(beats)} beats, tempo {tempo}, {method}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
