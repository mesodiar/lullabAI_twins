"""Audio Cry & Pet Vocalization Detector (System 1 Edge Reflex).

Supports:
1. YAMNet TFLite (`yamnet.tflite`) when present on the Edge Node.
2. Built-in Spectral Harmonic Cry/Whimper Detector fallback so the system works out-of-the-box
   even before downloading TFLite weights.
3. Both `BABY` mode (`Baby cry, infant cry`, `Crying, sobbing`) and `PET_TEST` mode
   (`Dog`, `Whimper (dog)`, `Bark`, `Yip`) for testing with a dog wearing the breathing band.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

SubjectMode = Literal["BABY", "PET_TEST"]

BABY_CRY_CLASSES = {
    "Baby cry, infant cry",
    "Crying, sobbing",
    "Screaming",
    "Whimper",
    "Wail, moan",
}

PET_TEST_CLASSES = BABY_CRY_CLASSES | {
    "Dog",
    "Bark",
    "Yip",
    "Howl",
    "Bow-wow",
    "Growling",
    "Whimper (dog)",
    "Canidae, dogs, wolves",
}


@dataclass
class AudioDetectionResult:
    cry_prob: float
    top_label: str
    rms_level: float
    cry_triggered: bool


class AudioCryDetector:
    """Detects baby cries (and dog vocalizations when in PET_TEST mode) from 16kHz mono audio."""

    def __init__(
        self,
        subject_mode: SubjectMode = "BABY",
        cry_threshold: float = 0.55,
        tflite_model_path: str | Path | None = None,
        class_map_csv_path: str | Path | None = None,
    ) -> None:
        self.subject_mode: SubjectMode = subject_mode
        self.cry_threshold = cry_threshold
        self._interpreter = None
        self._class_names: list[str] = []

        if tflite_model_path and Path(tflite_model_path).exists():
            self._load_tflite(Path(tflite_model_path), Path(class_map_csv_path) if class_map_csv_path else None)

    def _load_tflite(self, model_path: Path, csv_path: Path | None) -> None:
        try:
            try:
                from ai_edge_litert.interpreter import Interpreter  # type: ignore[import-not-found]
            except ImportError:
                from tflite_runtime.interpreter import Interpreter  # type: ignore[import-not-found]

            self._interpreter = Interpreter(model_path=str(model_path))
            self._interpreter.allocate_tensors()
            if csv_path and csv_path.exists():
                with csv_path.open("r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    self._class_names = [row["display_name"] for row in reader]
        except Exception as exc:
            print(f"[AudioCryDetector] TFLite load skipped ({exc}); using spectral harmonic detector.")
            self._interpreter = None

    def analyze_waveform(self, waveform_16k: np.ndarray, sample_rate: int = 16000) -> AudioDetectionResult:
        """Analyzes a 1D float32 mono waveform in [-1.0, 1.0]."""
        if waveform_16k.size == 0:
            return AudioDetectionResult(0.0, "Silence", 0.0, False)

        samples = waveform_16k.astype(np.float32).flatten()
        rms = float(np.sqrt(np.mean(np.square(samples))))

        if rms < 0.015:
            return AudioDetectionResult(
                cry_prob=round(rms * 2.0, 3),
                top_label="Silence",
                rms_level=round(rms, 4),
                cry_triggered=False,
            )

        if self._interpreter is not None and self._class_names:
            return self._analyze_with_yamnet(samples, rms)

        return self._analyze_with_spectral_harmonics(samples, sample_rate, rms)

    def _analyze_with_yamnet(self, samples: np.ndarray, rms: float) -> AudioDetectionResult:
        assert self._interpreter is not None
        input_details = self._interpreter.get_input_details()
        output_details = self._interpreter.get_output_details()

        self._interpreter.resize_tensor_input(input_details[0]["index"], [len(samples)])
        self._interpreter.allocate_tensors()
        self._interpreter.set_tensor(input_details[0]["index"], samples)
        self._interpreter.invoke()

        scores = self._interpreter.get_tensor(output_details[0]["index"])
        mean_scores = np.mean(scores, axis=0)
        top_idx = int(np.argmax(mean_scores))
        top_label = self._class_names[top_idx] if top_idx < len(self._class_names) else f"class_{top_idx}"

        target_set = PET_TEST_CLASSES if self.subject_mode == "PET_TEST" else BABY_CRY_CLASSES
        target_indices = [i for i, name in enumerate(self._class_names) if name in target_set]
        cry_prob = float(np.max(mean_scores[target_indices])) if target_indices else float(mean_scores[top_idx])

        return AudioDetectionResult(
            cry_prob=round(cry_prob, 3),
            top_label=top_label,
            rms_level=round(rms, 4),
            cry_triggered=cry_prob >= self.cry_threshold,
        )

    def _analyze_with_spectral_harmonics(
        self, samples: np.ndarray, sample_rate: int, rms: float
    ) -> AudioDetectionResult:
        """Spectral harmonic detector tuned for infant cries (350-2200 Hz) & dog whimpers/barks."""
        windowed = samples * np.hanning(len(samples))
        spectrum = np.abs(np.fft.rfft(windowed))
        freqs = np.fft.rfftfreq(len(samples), d=1.0 / sample_rate)

        total_energy = float(np.sum(spectrum**2)) + 1e-9

        # Fundamental + primary harmonics band for infant cry / canine vocalization (300 Hz - 2400 Hz)
        cry_band_mask = (freqs >= 300.0) & (freqs <= 2400.0)
        cry_band_energy = float(np.sum(spectrum[cry_band_mask] ** 2))
        band_ratio = cry_band_energy / total_energy

        # Spectral crest factor (tonal harmonic peaks vs flat white/pink fan noise)
        cry_band_spec = spectrum[cry_band_mask]
        mean_spec = float(np.mean(cry_band_spec)) + 1e-9
        max_spec = float(np.max(cry_band_spec)) if cry_band_spec.size > 0 else 0.0
        tonality = min(1.0, (max_spec / mean_spec) / 14.0)

        # Combine RMS loudness, harmonic band concentration, and tonality
        loudness_factor = min(1.0, rms / 0.18)
        cry_prob = float(np.clip(band_ratio * 0.45 + tonality * 0.35 + loudness_factor * 0.25, 0.0, 0.99))

        if cry_prob >= self.cry_threshold:
            label = "Baby cry, infant cry" if self.subject_mode == "BABY" else "Whimper (dog) / Baby cry"
        elif rms > 0.05:
            label = "Inside, small room"
        else:
            label = "Silence"

        return AudioDetectionResult(
            cry_prob=round(cry_prob, 3),
            top_label=label,
            rms_level=round(rms, 4),
            cry_triggered=cry_prob >= self.cry_threshold,
        )

