"""OpenCV ROI Motion Detector for LullabAI Twins (System 1 Edge Reflex)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cv_compat import cv2


@dataclass
class MotionResult:
    motion_score: float
    motion_triggered: bool
    roi_bbox_px: tuple[int, int, int, int]  # (x1, y1, x2, y2)


class MotionDetector:
    """Computes motion intensity inside a normalized Crib Region-of-Interest (ROI)."""

    def __init__(
        self,
        roi_normalized: tuple[float, float, float, float] = (0.10, 0.05, 0.90, 0.95),
        motion_threshold: float = 4.5,
         sustain_frames: int = 3,
    ) -> None:
        self.roi_normalized = roi_normalized
        self.motion_threshold = motion_threshold
        self.sustain_frames = sustain_frames
        self._prev_roi_gray: np.ndarray | None = None
        self._consecutive_above: int = 0

    def process_frame(self, frame_bgr: np.ndarray) -> MotionResult:
        h, w = frame_bgr.shape[:2]
        rx1, ry1, rx2, ry2 = self.roi_normalized
        x1, y1 = int(w * rx1), int(h * ry1)
        x2, y2 = int(w * rx2), int(h * ry2)

        roi = frame_bgr[y1:y2, x1:x2]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (15, 15), 0)

        if self._prev_roi_gray is None or self._prev_roi_gray.shape != blurred.shape:
            self._prev_roi_gray = blurred
            return MotionResult(
                motion_score=0.0,
                motion_triggered=False,
                roi_bbox_px=(x1, y1, x2, y2),
            )

        diff = cv2.absdiff(self._prev_roi_gray, blurred)
        # Blend previous background slightly to avoid lingering ghosting
        self._prev_roi_gray = cv2.addWeighted(self._prev_roi_gray, 0.5, blurred, 0.5, 0)

        # Ignore subtle sensor noise (< 8 intensity levels)
        _, thresh = cv2.threshold(diff, 8, 255, cv2.THRESH_BINARY)
        changed_ratio = float(np.count_nonzero(thresh)) / float(thresh.size)
        mean_diff = float(np.mean(diff))
        motion_score = round((changed_ratio * 100.0 * 0.7) + (mean_diff * 0.8), 2)

        if motion_score >= self.motion_threshold:
            self._consecutive_above += 1
        else:
            self._consecutive_above = max(0, self._consecutive_above - 1)

        triggered = self._consecutive_above >= self.sustain_frames
        return MotionResult(
            motion_score=motion_score,
            motion_triggered=triggered,
            roi_bbox_px=(x1, y1, x2, y2),
        )
