"""Detect sustained overlap between the tracked pet and the yellow test blanket."""

from __future__ import annotations

import numpy as np

from cv_compat import _integral_box_mean, cv2


class PetCoverDetector:
    """Keep a recent pet location available when its body is hidden by the cloth."""

    def __init__(self) -> None:
        self._pet_box: tuple[int, int, int, int] | None = None
        self._last_pet_at = 0.0
        self._last_frame_at: float | None = None
        self._frame_shape: tuple[int, int] | None = None
        self._cover_since: float | None = None
        self._cover_frames = 0
        self._clear_since: float | None = None
        self.active = False

    def process_frame(
        self, frame: np.ndarray, pet_bbox: list[int] | None, timestamp: float
    ) -> bool:
        height, width = frame.shape[:2]
        elapsed = 0.0 if self._last_frame_at is None else timestamp - self._last_frame_at
        if (height, width) != self._frame_shape or elapsed < 0 or elapsed > 4.0:
            self._pet_box = None
            self._clear_since = None
            self.active = False
            self._frame_shape = (height, width)
        if self._pet_box is None or elapsed > 1.25:
            self._cover_since = None
            self._cover_frames = 0
            self._clear_since = None
        self._last_frame_at = timestamp

        if not self.active and self._cover_since is None and timestamp - self._last_pet_at > 4.0:
            self._pet_box = None
        if self._pet_box is None and pet_bbox is None:
            return False

        work_width = 384
        work_height = max(16, round(height * work_width / width))
        if pet_bbox is not None:
            x, y, w, h = pet_bbox
            live_box = (
                max(0, round(x * work_width / width)),
                max(0, round(y * work_height / height)),
                min(work_width, round((x + w) * work_width / width)),
                min(work_height, round((y + h) * work_height / height)),
            )
            self._last_pet_at = timestamp
        else:
            live_box = None

        small = cv2.resize(frame, (work_width, work_height), interpolation=cv2.INTER_AREA)
        blue, green, red = small.astype(np.float32).transpose(2, 0, 1)
        luminance = 0.114 * blue + 0.587 * green + 0.299 * red
        yellow = (
            (red - blue >= 33)
            & (green - blue >= 24)
            & (red - green <= 36)
            & (luminance >= 83)
        )
        # Require broad colored patches so muzzle markings and small toys do not
        # qualify as a blanket.
        cloth = _integral_box_mean(yellow.astype(np.float32), 4) >= 0.42

        anchor = self._pet_box if self._cover_since is not None or self.active else live_box
        if anchor is None:
            anchor = self._pet_box
        covered = anchor is not None and self._cloth_overlaps(cloth, anchor)
        if covered and anchor is not None and live_box is not None and self.active:
            left, top, right, bottom = anchor
            live_x = (live_box[0] + live_box[2]) / 2
            live_y = (live_box[1] + live_box[3]) / 2
            if not (left <= live_x <= right and top <= live_y <= bottom):
                # A visible pet elsewhere can mean the blanket was left behind.
                covered = self._cloth_overlaps(cloth, live_box)

        if covered:
            self._pet_box = anchor
            self._clear_since = None
            if self._cover_since is None:
                self._cover_since = timestamp
            self._cover_frames += 1
            if self._cover_frames >= 3 and timestamp - self._cover_since >= 1.0:
                self.active = True
        else:
            self._cover_since = None
            self._cover_frames = 0
            if self._clear_since is None:
                self._clear_since = timestamp
            if timestamp - self._clear_since >= 0.5:
                self.active = False
            if not self.active and live_box is not None:
                self._pet_box = live_box
        return self.active

    @staticmethod
    def _cloth_overlaps(mask: np.ndarray, box: tuple[int, int, int, int]) -> bool:
        left, top, right, bottom = box
        width, height = right - left, bottom - top
        if width < 12 or height < 12:
            return False

        overlap = float(mask[top:bottom, left:right].mean())
        pad_x, pad_y = round(width * 0.15), round(height * 0.12)
        nearby = mask[max(0, top - pad_y) : bottom + pad_y, max(0, left - pad_x) : right + pad_x]
        return overlap >= 0.13 and np.count_nonzero(nearby) >= mask.size * 0.024
