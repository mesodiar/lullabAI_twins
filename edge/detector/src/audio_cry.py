"""On-device YAMNet classification of infant cries and dog vocalizations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from zipfile import BadZipFile, ZipFile

import numpy as np

AUDIO_MODEL_PATH = Path("/app/models/yamnet.tflite")
BABY_CRY = "Baby cry, infant cry"
DOG_SOUNDS = ("Bark", "Yip", "Howl", "Bow-wow", "Growling", "Whimper (dog)")
BACKGROUND_NOISE = ("Wind", "Wind noise (microphone)", "Mechanical fan", "Air conditioning", "Noise", "White noise")
DOG_SOUND_EVIDENCE = 0.25
AudioKind = Literal["BABY_CRY", "DOG_VOCALIZATION"]


@dataclass
class AudioDetectionResult:
    cry_prob: float
    top_label: str
    rms_level: float
    cry_triggered: bool
    audio_kind: AudioKind | None = None


def vocalization_score(scores: dict[str, float]) -> tuple[float, str, AudioKind]:
    baby_score = scores.get(BABY_CRY, 0.0)
    dog_label = max(DOG_SOUNDS, key=lambda label: scores.get(label, 0.0))
    dog_score = scores.get(dog_label, 0.0)

    # The general Dog class can support a bark or whimper, but cannot alert by itself.
    general_dog = scores.get("Dog", 0.0)
    if dog_score >= DOG_SOUND_EVIDENCE and general_dog > dog_score:
        dog_score, dog_label = general_dog, "Dog"
    if dog_score > baby_score:
        return dog_score, dog_label, "DOG_VOCALIZATION"
    return baby_score, BABY_CRY, "BABY_CRY"


class AudioCryDetector:
    """Classify 16 kHz mono PCM with the local model and alert on vocalizations only."""

    def __init__(self, cry_threshold: float = 0.55, tflite_model_path: str | Path = AUDIO_MODEL_PATH) -> None:
        self.cry_threshold = cry_threshold
        self._interpreter = None
        model = Path(tflite_model_path)
        try:
            with ZipFile(model) as archive:
                self._labels = archive.read("yamnet_label_list.txt").decode("utf-8").splitlines()

            from ai_edge_litert.interpreter import Interpreter  # type: ignore[import-not-found]

            interpreter = Interpreter(model_path=str(model), num_threads=1)
            interpreter.allocate_tensors()
            input_info = interpreter.get_input_details()[0]
            output_info = interpreter.get_output_details()[0]
            self._input_shape = tuple(int(size) for size in input_info["shape"])
            self._window_size = int(np.prod(self._input_shape))
            if input_info["dtype"] != np.float32 or self._window_size != 15600 or output_info["shape"][-1] != len(self._labels):
                raise ValueError("Unexpected YAMNet model input or output")
            self._input_index = input_info["index"]
            self._output_index = output_info["index"]
            self._interpreter = interpreter
            print("[AudioCryDetector] YAMNet vocalization classifier ready.")
        except (BadZipFile, ImportError, KeyError, OSError, RuntimeError, ValueError) as exc:
            print(f"[AudioCryDetector] Audio alerts unavailable ({type(exc).__name__}); check the Edge model installation.")

    @property
    def ready(self) -> bool:
        return self._interpreter is not None

    def analyze_waveform(self, waveform_16k: np.ndarray) -> AudioDetectionResult:
        samples = np.asarray(waveform_16k, dtype=np.float32).reshape(-1)
        rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        if rms < 0.001:
            return AudioDetectionResult(0.0, "Silence", round(rms, 4), False)
        if not self.ready:
            return AudioDetectionResult(0.0, "Audio classifier unavailable", round(rms, 4), False)

        try:
            return self._analyze_with_yamnet(samples, rms)
        except (RuntimeError, ValueError) as exc:
            self._interpreter = None
            print(f"[AudioCryDetector] Audio alerts unavailable after inference error ({type(exc).__name__}).")
            return AudioDetectionResult(0.0, "Audio classifier unavailable", round(rms, 4), False)

    def _analyze_with_yamnet(self, samples: np.ndarray, rms: float) -> AudioDetectionResult:
        interpreter = self._interpreter
        assert interpreter is not None
        if samples.size < self._window_size:
            samples = np.pad(samples, (0, self._window_size - samples.size))
        last_offset = samples.size - self._window_size
        offsets = [*range(0, last_offset, self._window_size // 2), last_offset]
        highest_target = 0.0
        top_score, top_label = -1.0, "Unknown"
        alert_score = 0.0
        alert_kind: AudioKind | None = None
        alert_label = ""

        for offset in offsets:
            window = samples[offset:offset + self._window_size]
            interpreter.set_tensor(self._input_index, window.reshape(self._input_shape))
            interpreter.invoke()
            output = interpreter.get_tensor(self._output_index).reshape(-1)
            scores = dict(zip(self._labels, output))
            top_index = int(np.argmax(output))
            if output[top_index] > top_score:
                top_score, top_label = float(output[top_index]), self._labels[top_index]
            score, label, kind = vocalization_score(scores)
            highest_target = max(highest_target, score)
            noise_score = max(scores.get(noise, 0.0) for noise in BACKGROUND_NOISE)
            if score >= self.cry_threshold and score > max(noise_score, alert_score):
                alert_score, alert_label, alert_kind = score, label, kind

        if alert_kind is not None:
            return AudioDetectionResult(round(float(alert_score), 3), alert_label, round(rms, 4), True, alert_kind)
        return AudioDetectionResult(round(float(highest_target), 3), top_label, round(rms, 4), False)
