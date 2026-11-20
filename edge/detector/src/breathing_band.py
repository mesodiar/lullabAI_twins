"""Fast Edge Subject Detector (Dog/Pet, Infant, Adult, Empty Crib) for LullabAI Twins.

Supports four subject classes on Edge without requiring any wearable band:
1. "PET" (🐶 น้องหมาในคอก): Detects a furry pet (such as a black-and-tan/sable Pomeranian)
   inside the playpen/crib using dark-core and warm-tan fur color and texture.
2. "BABY" (👶 เด็กทารก): Detects an infant using head-to-shoulder ratio (~1 : 1.10–1.72),
   compact body silhouette, and facial/skin validation.
3. "ADULT" (🧑 ผู้ใหญ่): Detects an adult in front of the camera (broad shoulders >= 1.85x head
   width or torso anchored to bottom frame border) and suppresses false baby/pet alerts.
4. "NONE" (🛏️ คอก/เตียงว่าง): Returns NONE when the playpen or room is empty.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Literal

import numpy as np

from cv_compat import _connected_region, _integral_box_mean, cv2

SubjectType = Literal["BABY", "PET", "ADULT", "NONE"]


@dataclass
class BandTrackingResult:
    band_detected: bool  # True when a monitored subject ("BABY" or "PET") is detected in the crib/pen
    band_confidence: float
    band_bbox: list[int] | None  # [x, y, w, h] of detected subject, or None if empty
    marker_count: int
    breathing_rpm: float | None  # Disabled (always None)
    rollover_suspected: bool
    breathing_anomaly: bool
    displacement_signal: float
    tracking_mode: str = "BABY_VISION"
    subject_type: SubjectType = "NONE"


class BreathingBandTracker:
    """Fast Edge detector that classifies PET (dog in playpen), BABY, ADULT, and NONE (empty)."""

    def __init__(
        self,
        min_markers: int = 8,
        rollover_missing_seconds: float = 1.5,
        **_kwargs: object,
    ) -> None:
        self.min_markers = min_markers
        self.rollover_missing_seconds = rollover_missing_seconds
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

        self._face_cascades: list[object] = []
        if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data"):
            for xml_name in (
                "haarcascade_frontalface_default.xml",
                "haarcascade_frontalface_alt2.xml",
                "haarcascade_profileface.xml",
            ):
                xml_path = os.path.join(cv2.data.haarcascades, xml_name)
                if os.path.exists(xml_path):
                    try:
                        c = cv2.CascadeClassifier(xml_path)
                        if not c.empty():
                            self._face_cascades.append(c)
                    except Exception:
                        pass

        self._last_result: BandTrackingResult | None = None
        self._last_frame_size: tuple[int, int] | None = None
        self._frame_count: int = 0

    @staticmethod
    def _detect_pet_in_playpen(
        frame_bgr: np.ndarray,
    ) -> tuple[bool, float, list[int] | None]:
        """Find a fur patch at a fixed working scale and use it to locate the body."""
        h_orig, w_orig = frame_bgr.shape[:2]
        if h_orig < 60 or w_orig < 60:
            return False, 0.0, None

        w_work = 380
        h_work = max(180, int(round(h_orig * (w_work / float(w_orig)))))
        y_idx = np.linspace(0, h_orig - 1, h_work).astype(np.int32)
        x_idx = np.linspace(0, w_orig - 1, w_work).astype(np.int32)
        small_bgr = frame_bgr[np.ix_(y_idx, x_idx)]
        sx = float(w_orig) / float(w_work)
        sy = float(h_orig) / float(h_work)

        bgr_f = small_bgr.astype(np.float32)
        b_ch, g_ch, r_ch = bgr_f[:, :, 0], bgr_f[:, :, 1], bgr_f[:, :, 2]
        y_lum = 0.299 * r_ch + 0.587 * g_ch + 0.114 * b_ch

        gy, gx = np.gradient(y_lum)
        grad_mag = np.hypot(gx, gy)

        very_dark = (
            (y_lum < 44.0)
            & ((r_ch - b_ch) >= -3.0)
            & ((r_ch - b_ch) <= 24.0)
            & ((b_ch - g_ch) <= 5.0)
        )
        dark_60 = (
            (y_lum < 60.0)
            & ((r_ch - b_ch) >= -3.0)
            & ((r_ch - b_ch) <= 24.0)
            & ((b_ch - g_ch) <= 5.0)
        )
        pom_tan = (
            (y_lum >= 22.0)
            & (y_lum < 140.0)
            & ((r_ch - b_ch) >= 6.0)
            & ((r_ch - b_ch) <= 44.0)
            & ((r_ch - g_ch) >= 2.0)
            & ((r_ch - g_ch) <= 24.0)
            & ((b_ch - g_ch) <= 6.0)
            & ((g_ch - b_ch) <= 22.0)
        )
        fluffy_taupe = (
            (y_lum >= 45.0)
            & (y_lum < 145.0)
            & (np.abs(r_ch - b_ch) <= 20.0)
            & (np.abs(r_ch - g_ch) <= 15.0)
            & (np.abs(b_ch - g_ch) <= 7.0)
            & (grad_mag > 4.5)
        )

        r_box = max(12, int(min(h_work, w_work) * 0.09))
        vd_den = _integral_box_mean(very_dark.astype(np.float32), r_box)
        d60_den = _integral_box_mean(dark_60.astype(np.float32), r_box)
        tan_den = _integral_box_mean(pom_tan.astype(np.float32), r_box)
        taupe_den = _integral_box_mean(fluffy_taupe.astype(np.float32), r_box)
        white_den = _integral_box_mean((y_lum > 160.0).astype(np.float32), r_box)
        tex_den = _integral_box_mean((grad_mag > 5.0).astype(np.float32), r_box)
        mean_gx = _integral_box_mean(np.abs(gx), r_box)
        mean_gy = _integral_box_mean(np.abs(gy), r_box)
        iso_tex = (mean_gy >= mean_gx * 0.62) & (tex_den >= 0.25)

        roi = np.zeros((h_work, w_work), dtype=bool)
        roi[int(h_work * 0.08) : int(h_work * 0.88), int(w_work * 0.10) : int(w_work * 0.90)] = True

        valid = (
            (vd_den >= 0.19)
            & (d60_den >= 0.36)
            & (tan_den >= 0.32)
            & ((tan_den + taupe_den) >= 0.42)
            & (white_den < 0.06)
            & iso_tex
            & roi
        )

        score = valid.astype(np.float32) * d60_den * (tan_den + taupe_den) * tex_den
        confidence_floor = 0.76
        if float(np.max(score)) < 0.080:
            # Blur suppresses fine gradients. Require a substantial dark and tan patch
            # and gradients in both directions so fence slats remain a poor match.
            blur_valid = (
                (vd_den >= 0.18)
                & (d60_den >= 0.33)
                & (tan_den >= 0.27)
                & ((tan_den + taupe_den) >= 0.34)
                & (white_den < 0.06)
                & (tex_den >= 0.14)
                & (mean_gy >= mean_gx * 0.55)
                & (mean_gx >= mean_gy * 0.55)
                & roi
            )
            score = blur_valid * d60_den * (tan_den + taupe_den) * tex_den
            confidence_floor = 0.68
            if float(np.max(score)) < 0.045:
                return False, 0.0, None

        py, px = divmod(int(np.argmax(score)), w_work)
        coat_color = (
            ((r_ch - b_ch) >= -4.0)
            & ((r_ch - b_ch) <= 52.0)
            & ((b_ch - g_ch) <= 8.0)
            & ((r_ch - g_ch) <= 27.0)
        )
        dark_coat = (y_lum < 86.0) & coat_color
        core_mask = _integral_box_mean(dark_coat.astype(np.float32), 3) >= 0.54
        y0, x0 = max(0, py - r_box), max(0, px - r_box)
        ys, xs = np.where(core_mask[y0 : py + r_box + 1, x0 : px + r_box + 1])
        if not xs.size:
            return False, 0.0, None

        nearest = int(np.argmin((ys + y0 - py) ** 2 + (xs + x0 - px) ** 2))
        seed_x, seed_y = int(xs[nearest] + x0), int(ys[nearest] + y0)
        core = _connected_region(core_mask, seed_x, seed_y)
        # Let the box include nearby tan fur and lighter fringe with limited reach
        # so similar colors in a hand, floor shadow, or fence cannot grow freely.
        light_coat = coat_color & (
            (y_lum < 100.0)
            | ((y_lum < 132.0) & (((r_ch - b_ch) >= 8.0) | (grad_mag > 4.0)))
        )
        near_core = _integral_box_mean(core.astype(np.float32), r_box) >= 0.025
        coat_support = _integral_box_mean(light_coat.astype(np.float32), 3) >= 0.48
        body = _connected_region(core | (near_core & coat_support), seed_x, seed_y)
        ys, xs = np.where(body)
        if xs.size < h_work * w_work * 0.014:
            return False, 0.0, None

        # Trim thin outlying strands and clamp the result to the original frame.
        x1, x2 = np.percentile(xs, (0.5, 99.5))
        y1, y2 = np.percentile(ys, (0.5, 99.5))
        bx1 = max(0, int((x1 - 2) * sx))
        by1 = max(0, int((y1 - 2) * sy))
        bx2 = min(w_orig, int(np.ceil((x2 + 3) * sx)))
        by2 = min(h_orig, int(np.ceil((y2 + 3) * sy)))
        bw, bh = bx2 - bx1, by2 - by1

        if x2 - x1 < 26 or y2 - y1 < 26 or not (0.38 <= bw / bh <= 2.65):
            return False, 0.0, None

        confidence = min(0.97, round(confidence_floor + float(score[py, px]) * 0.65, 2))
        return True, confidence, [bx1, by1, bw, bh]

    @staticmethod
    def _compute_skin_and_face_validity(
        frame_bgr: np.ndarray,
        gray: np.ndarray,
        box: tuple[int, int, int, int],
    ) -> tuple[bool, float]:
        """Validates whether a candidate human head/face box (x, y, w, h) has real facial structure & skin cues."""
        h_img, w_img = gray.shape[:2]
        fx, fy, fw, fh = box
        x1 = max(0, fx)
        y1 = max(0, fy)
        x2 = min(w_img, fx + fw)
        y2 = min(h_img, fy + fh)
        if x2 - x1 < 24 or y2 - y1 < 24:
            return False, 0.0

        patch_gray = gray[y1:y2, x1:x2].astype(np.float32)
        patch_bgr = frame_bgr[y1:y2, x1:x2].astype(np.float32)

        gray_std = float(np.std(patch_gray))
        if gray_std < 12.0:
            return False, 0.0

        gy, gx = np.gradient(patch_gray)
        mean_gy = float(np.mean(np.abs(gy)))
        mean_gx = float(np.mean(np.abs(gx)))
        if mean_gy < 0.8 or (mean_gy / (mean_gx + 1e-4)) < 0.50:
            return False, 0.0

        ph, pw = patch_gray.shape[:2]
        half_w = pw // 2
        if half_w >= 8:
            left_half = patch_gray[:, :half_w]
            right_half_flipped = patch_gray[:, pw - half_w :][:, ::-1]
            l_zm = left_half - float(np.mean(left_half))
            r_zm = right_half_flipped - float(np.mean(right_half_flipped))
            denom = float(np.linalg.norm(l_zm) * np.linalg.norm(r_zm))
            symmetry = float(np.sum(l_zm * r_zm) / denom) if denom > 1e-4 else 0.0
        else:
            symmetry = 0.0

        if symmetry < 0.18:
            return False, 0.0

        b_ch, g_ch, r_ch = patch_bgr[:, :, 0], patch_bgr[:, :, 1], patch_bgr[:, :, 2]
        color_diff_rg = float(np.mean(np.abs(r_ch - g_ch)))
        is_infrared_grayscale = color_diff_rg < 3.5

        if not is_infrared_grayscale:
            y_val = 0.299 * r_ch + 0.587 * g_ch + 0.114 * b_ch
            cr = (r_ch - y_val) * 0.713 + 128.0
            cb = (b_ch - y_val) * 0.564 + 128.0
            skin_mask = (cr >= 133.0) & (cr <= 182.0) & (cb >= 75.0) & (cb <= 132.0) & ((cr - cb) >= 10.0)
            skin_ratio = float(np.mean(skin_mask))
            if skin_ratio < 0.22:
                return False, 0.0
            score = min(1.0, 0.45 * skin_ratio + 0.35 * max(0.0, symmetry) + 0.20 * min(1.0, gray_std / 40.0))
            return True, score

        if symmetry < 0.35 or gray_std < 18.0:
            return False, 0.0
        score = min(1.0, 0.60 * symmetry + 0.40 * min(1.0, gray_std / 45.0))
        return True, score

    def _detect_face_box(self, frame_bgr: np.ndarray, gray: np.ndarray) -> tuple[int, int, int, int] | None:
        """Detects a validated human face bounding box (x, y, w, h) using cascades + skin-cluster fallback."""
        h_img, w_img = gray.shape[:2]
        candidates: list[tuple[float, tuple[int, int, int, int]]] = []

        if self._face_cascades:
            scale = min(1.0, 480.0 / float(max(w_img, 1)))
            small_w = max(64, int(round(w_img * scale)))
            small_h = max(64, int(round(h_img * scale)))
            small_gray = cv2.resize(gray, (small_w, small_h), interpolation=cv2.INTER_AREA)
            inv = 1.0 / scale

            for cascade in self._face_cascades:
                faces = cascade.detectMultiScale(
                    small_gray,
                    scaleFactor=1.12,
                    minNeighbors=5,
                    minSize=(28, 28),
                )
                if faces is None or len(faces) == 0:
                    continue
                for b in faces:
                    fx = int(round(b[0] * inv))
                    fy = int(round(b[1] * inv))
                    fw = int(round(b[2] * inv))
                    fh = int(round(b[3] * inv))
                    valid, score = self._compute_skin_and_face_validity(frame_bgr, gray, (fx, fy, fw, fh))
                    if valid:
                        area_weight = score * float(fw * fh)
                        candidates.append((area_weight, (fx, fy, fw, fh)))
                if candidates:
                    break

        if not candidates:
            bgr_f = frame_bgr.astype(np.float32)
            b_ch, g_ch, r_ch = bgr_f[:, :, 0], bgr_f[:, :, 1], bgr_f[:, :, 2]
            y_lum = 0.299 * r_ch + 0.587 * g_ch + 0.114 * b_ch
            cr = (r_ch - y_lum) * 0.713 + 128.0
            cb = (b_ch - y_lum) * 0.564 + 128.0
            skin_face = (
                (cr >= 138.0)
                & (cr <= 178.0)
                & (cb >= 77.0)
                & (cb <= 130.0)
                & ((r_ch - g_ch) >= 16.0)
                & ((g_ch - b_ch) >= -4.0)
                & ((g_ch - b_ch) <= 13.0)
                & (y_lum >= 75.0)
                & (y_lum <= 210.0)
            )
            sy1, sy2 = int(h_img * 0.08), int(h_img * 0.55)
            sx1, sx2 = int(w_img * 0.10), int(w_img * 0.80)
            ys, xs = np.where(skin_face[sy1:sy2, sx1:sx2])
            if xs.size >= max(40, int(h_img * w_img * 0.012)):
                fx1 = sx1 + int(np.percentile(xs, 6))
                fx2 = sx1 + int(np.percentile(xs, 94))
                fy1 = sy1 + int(np.percentile(ys, 5))
                fy2 = sy1 + int(np.percentile(ys, 95))
                pad_x = int((fx2 - fx1) * 0.18)
                pad_y = int((fy2 - fy1) * 0.15)
                fx1 = max(0, fx1 - pad_x)
                fx2 = min(w_img, fx2 + pad_x)
                fy1 = max(0, fy1 - pad_y)
                fy2 = min(h_img, fy2 + pad_y)
                fw = max(28, fx2 - fx1)
                fh = max(28, fy2 - fy1)
                aspect = float(fw) / float(max(fh, 1))
                if 0.55 <= aspect <= 1.65 and fw <= int(w_img * 0.48):
                    valid, score = self._compute_skin_and_face_validity(frame_bgr, gray, (fx1, fy1, fw, fh))
                    if valid:
                        candidates.append((score * float(fw * fh), (fx1, fy1, fw, fh)))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _verify_human_head_and_torso(
        self,
        frame_bgr: np.ndarray,
        gray: np.ndarray,
        fg_mask: np.ndarray,
        roi_offset: tuple[int, int],
        bbox: list[int],
        face_box: tuple[int, int, int, int] | None,
    ) -> bool:
        """Verifies that the foreground region contains a human head + connected torso."""
        if face_box is not None:
            return True

        h_img, w_img = gray.shape[:2]
        rx1, ry1 = roi_offset
        bx, by, bw, bh = bbox

        local_x1 = max(0, bx - rx1)
        local_x2 = min(fg_mask.shape[1], bx + bw - rx1)
        local_y1 = max(0, by - ry1)
        local_y2 = min(fg_mask.shape[0], by + bh - ry1)
        sub_mask = fg_mask[local_y1:local_y2, local_x1:local_x2]
        sh_h, sh_w = sub_mask.shape[:2]
        if sh_h < 60 or sh_w < 50:
            return False

        head_y1 = int(sh_h * 0.04)
        head_y2 = max(head_y1 + 16, int(sh_h * 0.36))
        head_slice = sub_mask[head_y1:head_y2, :]
        head_ys, head_xs = np.where(head_slice)
        if head_xs.size < 60:
            return False

        hx1 = bx + int(np.percentile(head_xs, 10))
        hx2 = bx + int(np.percentile(head_xs, 90))
        hy1 = by + head_y1 + int(np.percentile(head_ys, 10))
        hy2 = by + head_y1 + int(np.percentile(head_ys, 95))
        hw = max(24, hx2 - hx1)
        hh = max(24, hy2 - hy1)

        head_aspect = float(hw) / float(max(hh, 1))
        if not (0.55 <= head_aspect <= 1.65):
            return False

        if hw > int(w_img * 0.48):
            return False

        head_valid, _ = self._compute_skin_and_face_validity(frame_bgr, gray, (hx1, hy1, hw, hh))
        if not head_valid:
            return False

        torso_y1 = int(sh_h * 0.38)
        torso_y2 = int(sh_h * 0.85)
        torso_slice = sub_mask[torso_y1:torso_y2, :]
        torso_fill = float(np.mean(torso_slice)) if torso_slice.size else 0.0
        if torso_fill < 0.30:
            return False

        return True

    def _classify_infant_vs_adult(
        self,
        frame_bgr: np.ndarray,
        gray: np.ndarray,
        fg_mask: np.ndarray,
        roi_offset: tuple[int, int],
        bbox: list[int],
        face_box: tuple[int, int, int, int] | None,
    ) -> tuple[SubjectType, float]:
        """Classifies a verified human subject into 'BABY' vs 'ADULT'."""
        h_img, w_img = gray.shape[:2]
        rx1, ry1 = roi_offset
        bx, by, bw, bh = bbox

        adult_score = 0.0
        baby_score = 0.0

        local_x1 = max(0, bx - rx1)
        local_x2 = min(fg_mask.shape[1], bx + bw - rx1)
        local_y1 = max(0, by - ry1)
        local_y2 = min(fg_mask.shape[0], by + bh - ry1)
        sub_mask = fg_mask[local_y1:local_y2, local_x1:local_x2]

        sh_h, sh_w = sub_mask.shape[:2]
        if sh_h < 30 or sh_w < 30:
            return "NONE", 0.0

        def _slice_width(y_start_frac: float, y_end_frac: float) -> float:
            s1 = max(0, int(sh_h * y_start_frac))
            s2 = min(sh_h, max(s1 + 1, int(sh_h * y_end_frac)))
            row_spans: list[float] = []
            for r_idx in range(s1, s2):
                xs = np.where(sub_mask[r_idx])[0]
                if xs.size >= 4:
                    span = float(np.percentile(xs, 92) - np.percentile(xs, 8))
                    row_spans.append(span)
            return float(np.median(row_spans)) if row_spans else 0.0

        w_head = _slice_width(0.08, 0.32)
        w_shoulder = _slice_width(0.38, 0.68)
        w_lower = _slice_width(0.72, 0.95)

        if w_head > 10.0 and w_shoulder > 10.0:
            shoulder_to_head = w_shoulder / w_head
            if shoulder_to_head >= 1.85:
                adult_score += 2.2
            elif 1.05 <= shoulder_to_head <= 1.68:
                baby_score += 2.0
            elif shoulder_to_head < 0.85:
                adult_score += 0.8

        if w_shoulder > 10.0 and w_lower > 10.0:
            lower_to_head = w_lower / max(w_head, 10.0)
            if lower_to_head >= 1.75 and (by + bh) >= int(h_img * 0.90):
                adult_score += 1.8
            elif w_lower <= w_shoulder * 0.95 and w_head >= w_shoulder * 0.58:
                baby_score += 1.2

        if face_box is not None:
            fx, fy, fw, fh = face_box
            sh_y1 = min(h_img - 2, fy + int(fh * 1.05))
            sh_y2 = min(h_img - 1, fy + int(fh * 2.20))
            if sh_y2 > sh_y1 + 8:
                below_y1 = max(0, sh_y1 - ry1)
                below_y2 = min(fg_mask.shape[0], sh_y2 - ry1)
                if below_y2 > below_y1:
                    xs_below = np.where(np.any(fg_mask[below_y1:below_y2, :], axis=0))[0]
                    if xs_below.size >= 8:
                        measured_shoulder_w = float(np.percentile(xs_below, 92) - np.percentile(xs_below, 8))
                        face_shoulder_ratio = measured_shoulder_w / float(max(fw, 1))
                        if face_shoulder_ratio >= 2.05:
                            adult_score += 2.5
                        elif 1.10 <= face_shoulder_ratio <= 1.75:
                            baby_score += 1.8

            face_patch = gray[max(0, fy) : min(h_img, fy + fh), max(0, fx) : min(w_img, fx + fw)].astype(np.float32)
            if face_patch.shape[0] >= 24 and face_patch.shape[1] >= 24:
                fh_p, fw_p = face_patch.shape[:2]
                gy_f, gx_f = np.gradient(face_patch)
                nose_zone = np.abs(gx_f[int(fh_p * 0.28) : int(fh_p * 0.72), int(fw_p * 0.35) : int(fw_p * 0.65)])
                cheek_zone = np.hypot(
                    gx_f[int(fh_p * 0.50) : int(fh_p * 0.82), int(fw_p * 0.15) : int(fw_p * 0.85)],
                    gy_f[int(fh_p * 0.50) : int(fh_p * 0.82), int(fw_p * 0.15) : int(fw_p * 0.85)],
                )
                nose_edge = float(np.mean(nose_zone)) if nose_zone.size else 0.0
                cheek_edge = float(np.mean(cheek_zone)) if cheek_zone.size else 1.0
                if nose_edge / (cheek_edge + 1e-4) > 1.28 or cheek_edge > 14.5:
                    adult_score += 1.4
                else:
                    baby_score += 1.0

                if fy < int(h_img * 0.32) and (by + bh) >= int(h_img * 0.90) and fw > int(w_img * 0.18):
                    adult_score += 1.6

        if bw >= int(w_img * 0.58) and (by + bh) >= int(h_img * 0.90):
            adult_score += 1.5

        if adult_score > baby_score:
            conf = min(0.96, round(0.65 + 0.08 * (adult_score - baby_score), 2))
            return "ADULT", conf

        conf = min(0.96, round(0.70 + 0.07 * max(0.5, baby_score - adult_score), 2))
        return "BABY", conf

    def detect_subject(
        self,
        frame_bgr: np.ndarray,
        gray: np.ndarray | None = None,
        *,
        check_human: bool = True,
    ) -> tuple[SubjectType, float, list[int] | None, int]:
        """Detects PET (dog in playpen), BABY (infant), ADULT, or NONE (empty view)."""
        pet_ok, pet_conf, pet_bbox = self._detect_pet_in_playpen(frame_bgr)
        if pet_ok and pet_bbox is not None:
            return "PET", pet_conf, pet_bbox, 12
        if not check_human:
            return "NONE", 0.0, None, 0

        if gray is None:
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        h_img, w_img = gray.shape[:2]
        gray_f = gray.astype(np.float32)

        # Check for Human (Infant vs Adult) inside crib/camera ROI
        x1, x2 = int(w_img * 0.08), int(w_img * 0.92)
        y1, y2 = int(h_img * 0.10), int(h_img * 0.95)
        if x2 - x1 < 40 or y2 - y1 < 40:
            return "NONE", 0.0, None, 0

        gy, gx = np.gradient(gray_f)
        grad_mag = np.hypot(gx, gy)
        roi_grad = grad_mag[y1:y2, x1:x2]
        roi_std = float(np.std(gray_f[y1:y2, x1:x2]))

        if roi_std < 8.0 and float(np.percentile(roi_grad, 95)) < 12.0:
            return "NONE", 0.0, None, 0

        bgr_f = frame_bgr.astype(np.float32)
        border_top = bgr_f[y1 : y1 + 10, x1:x2].reshape(-1, 3)
        border_left = bgr_f[y1 : int(y2 * 0.75), x1 : x1 + 10].reshape(-1, 3)
        border_right = bgr_f[y1 : int(y2 * 0.75), max(0, x2 - 10) : x2].reshape(-1, 3)
        border_samples = np.concatenate([border_top, border_left, border_right], axis=0)
        bg_color = np.median(border_samples, axis=0)

        color_diff = np.linalg.norm(bgr_f - bg_color, axis=2)
        color_smooth = _integral_box_mean(color_diff, max(8, int(min(h_img, w_img) * 0.03)))
        grad_smooth = _integral_box_mean(grad_mag, max(6, int(min(h_img, w_img) * 0.025)))

        roi_color = color_smooth[y1:y2, x1:x2]
        roi_tex = grad_smooth[y1:y2, x1:x2]

        p95_color = float(np.percentile(roi_color, 95))
        p95_tex = float(np.percentile(roi_tex, 95))
        if p95_color < 22.0 and p95_tex < 8.0:
            return "NONE", 0.0, None, 0

        c_norm = np.clip(roi_color / 85.0, 0.0, 1.5)
        t_norm = np.clip(roi_tex / max(p95_tex, 10.0), 0.0, 1.5)
        saliency = 0.65 * c_norm + 0.35 * t_norm

        fg_mask = (saliency >= 0.48) & ((roi_color >= 32.0) | (roi_tex >= 9.0))
        valid_rows = np.where(np.sum(fg_mask, axis=1) >= 18)[0]
        valid_cols = np.where(np.sum(fg_mask, axis=0) >= 18)[0]
        if valid_rows.size < 30 or valid_cols.size < 30:
            return "NONE", 0.0, None, 0

        bx1 = x1 + int(np.percentile(valid_cols, 2))
        bx2 = x1 + int(np.percentile(valid_cols, 98))
        by1 = y1 + int(np.percentile(valid_rows, 2))
        by2 = y1 + int(np.percentile(valid_rows, 98))

        bw = max(60, bx2 - bx1)
        bh = max(60, by2 - by1)
        bbox = [bx1, by1, bw, bh]

        face_box = self._detect_face_box(frame_bgr, gray)
        is_human = self._verify_human_head_and_torso(
            frame_bgr=frame_bgr,
            gray=gray,
            fg_mask=fg_mask,
            roi_offset=(x1, y1),
            bbox=bbox,
            face_box=face_box,
        )
        if not is_human:
            return "NONE", 0.0, None, 0

        subject_type, confidence = self._classify_infant_vs_adult(
            frame_bgr=frame_bgr,
            gray=gray,
            fg_mask=fg_mask,
            roi_offset=(x1, y1),
            bbox=bbox,
            face_box=face_box,
        )
        return subject_type, confidence, bbox, 12

    def process_frame(self, frame_bgr: np.ndarray, timestamp: float | None = None) -> BandTrackingResult:
        """Processes a single BGR frame and classifies PET, BABY, ADULT, or NONE."""
        self._frame_count += 1
        frame_size = frame_bgr.shape[:2]
        previous = self._last_result
        check_human = (
            previous is None
            or previous.subject_type == "PET"
            or frame_size != self._last_frame_size
            or self._frame_count % 3 == 1
        )
        self._last_frame_size = frame_size
        subject_type, conf, bbox, kp_count = self.detect_subject(frame_bgr, check_human=check_human)
        if previous is not None and not check_human and subject_type == "NONE":
            return previous

        is_monitored_subject = subject_type in ("BABY", "PET")
        self._last_result = BandTrackingResult(
            band_detected=is_monitored_subject,
            band_confidence=conf,
            band_bbox=bbox,
            marker_count=kp_count if is_monitored_subject else 0,
            breathing_rpm=None,
            rollover_suspected=False,
            breathing_anomaly=False,
            displacement_signal=0.0,
            tracking_mode=f"VISION_{subject_type}",
            subject_type=subject_type,
        )
        return self._last_result
