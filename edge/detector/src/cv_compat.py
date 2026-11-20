"""OpenCV (`cv2`) compatibility layer with a pure-NumPy fallback.

- In Docker / Ubuntu Mini PC where `opencv-python-headless` is installed, this module
  re-exports the native `cv2` module directly.
- In lightweight local environments where `cv2` is not installed, it provides a vectorized
  NumPy implementation so all Edge Detector algorithms and unit tests run out-of-the-box.
"""

from __future__ import annotations

import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

try:
    import cv2 as _real_cv2  # type: ignore[import-not-found]

    HAS_NATIVE_CV2 = True
except ImportError:
    _real_cv2 = None
    HAS_NATIVE_CV2 = False


def _integral_box_mean(img_f32: np.ndarray, radius: int) -> np.ndarray:
    """Fast O(1)-per-pixel local box mean using 2D summed-area table (integral image)."""
    h, w = img_f32.shape[:2]
    padded = np.pad(img_f32, ((radius + 1, radius), (radius + 1, radius)), mode="reflect")
    padded[0, :] = 0.0
    padded[:, 0] = 0.0
    sat = np.cumsum(np.cumsum(padded, axis=0), axis=1)
    k = 2 * radius + 1
    area = float(k * k)
    total = (
        sat[k : k + h, k : k + w]
        - sat[0:h, k : k + w]
        - sat[k : k + h, 0:w]
        + sat[0:h, 0:w]
    )
    return (total / area).astype(np.float32)


def _connected_region(mask: np.ndarray, seed_x: int, seed_y: int) -> np.ndarray:
    """Return the four-connected region of a binary mask at the seed."""
    remaining = mask.astype(np.uint8)
    if _real_cv2 is not None:
        _real_cv2.floodFill(remaining, None, (seed_x, seed_y), 2)
        return remaining == 2

    region = np.zeros_like(mask)
    height, width = mask.shape
    spans = [(seed_x, seed_y)]
    while spans:
        x, y = spans.pop()
        row = remaining[y]
        if not row[x]:
            continue
        left, right = x, x + 1
        while left > 0 and row[left - 1]:
            left -= 1
        while right < width and row[right]:
            right += 1
        row[left:right] = 0
        region[y, left:right] = True
        for neighbor in (y - 1, y + 1):
            if 0 <= neighbor < height:
                segment = remaining[neighbor, left:right]
                starts = np.flatnonzero(segment & ~np.r_[0, segment[:-1]])
                spans.extend((left + int(start), neighbor) for start in starts)
    return region


def _decode_bmp_bytes(raw: bytes) -> np.ndarray | None:
    if len(raw) < 54 or raw[:2] != b"BM":
        return None
    pixel_offset = struct.unpack_from("<I", raw, 10)[0]
    width = struct.unpack_from("<i", raw, 18)[0]
    height = struct.unpack_from("<i", raw, 22)[0]
    bpp = struct.unpack_from("<H", raw, 28)[0]
    top_down = height < 0
    abs_h = abs(height)
    channels = bpp // 8
    if channels not in (3, 4):
        return None
    row_stride = ((width * channels + 3) // 4) * 4
    expected_end = pixel_offset + row_stride * abs_h
    if len(raw) < expected_end:
        return None
    buf = np.frombuffer(raw, dtype=np.uint8, offset=pixel_offset, count=row_stride * abs_h)
    rows = buf.reshape((abs_h, row_stride))[:, : width * channels].reshape((abs_h, width, channels))
    if not top_down:
        rows = rows[::-1]
    return rows[:, :, :3].copy()


def _encode_bmp_bytes(img_bgr: np.ndarray) -> bytes:
    h, w = img_bgr.shape[:2]
    row_stride = ((w * 3 + 3) // 4) * 4
    pad = row_stride - w * 3
    flipped = img_bgr[::-1, :, :3]
    if pad > 0:
        flipped = np.pad(flipped, ((0, 0), (0, pad), (0, 0)), mode="constant")
    pixel_bytes = flipped.tobytes()
    file_size = 54 + len(pixel_bytes)
    header = struct.pack(
        "<2sIHHIiiiHHIIIIII",
        b"BM",
        file_size,
        0,
        0,
        54,
        40,
        w,
        h,
        1,
        24,
        0,
        len(pixel_bytes),
        2835,
        2835,
        0,
        0,
    )
    return header + pixel_bytes


class _CLAHEFallback:
    def apply(self, gray: np.ndarray) -> np.ndarray:
        p2, p98 = np.percentile(gray, (2, 98))
        if p98 - p2 < 1.0:
            return gray.copy()
        scaled = np.clip((gray.astype(np.float32) - p2) * (255.0 / (p98 - p2)), 0.0, 255.0)
        return scaled.astype(np.uint8)


class _NumpyCV2Fallback:
    """Vectorized NumPy implementation of the OpenCV subset used by LullabAI Edge."""

    INTER_AREA = 3
    INTER_LINEAR = 1
    BORDER_REFLECT_101 = 4
    COLOR_BGR2GRAY = 6
    IMWRITE_JPEG_QUALITY = 1
    IMREAD_COLOR = 1
    THRESH_BINARY = 0
    THRESH_BINARY_INV = 1
    ADAPTIVE_THRESH_GAUSSIAN_C = 1
    RETR_LIST = 0
    CHAIN_APPROX_SIMPLE = 2
    FONT_HERSHEY_SIMPLEX = 0
    LINE_AA = 16
    TERM_CRITERIA_EPS = 2
    TERM_CRITERIA_COUNT = 1

    @staticmethod
    def imread(path: str, flags: int = 1) -> np.ndarray | None:
        p = Path(path)
        if not p.exists():
            return None
        raw = p.read_bytes()
        bmp = _decode_bmp_bytes(raw)
        if bmp is not None:
            return bmp
        with tempfile.NamedTemporaryFile(suffix=".bmp", delete=True) as tmp:
            subprocess.run(
                ["sips", "-s", "format", "bmp", str(p), "--out", tmp.name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return _decode_bmp_bytes(Path(tmp.name).read_bytes())

    @staticmethod
    def imwrite(path: str, img: np.ndarray, params: list[int] | None = None) -> bool:
        Path(path).write_bytes(_encode_bmp_bytes(img))
        return True

    @staticmethod
    def imencode(ext: str, img: np.ndarray, params: list[int] | None = None) -> tuple[bool, np.ndarray]:
        data = _encode_bmp_bytes(img)
        return True, np.frombuffer(data, dtype=np.uint8)

    @staticmethod
    def imdecode(buf: np.ndarray, flags: int = 1) -> np.ndarray | None:
        return _decode_bmp_bytes(buf.tobytes())

    @staticmethod
    def resize(img: np.ndarray, dsize: tuple[int, int], interpolation: int = 1) -> np.ndarray:
        new_w, new_h = dsize
        h, w = img.shape[:2]
        if (w, h) == (new_w, new_h):
            return img.copy()
        y_idx = np.linspace(0, h - 1, new_h).astype(np.int32)
        x_idx = np.linspace(0, w - 1, new_w).astype(np.int32)
        return img[np.ix_(y_idx, x_idx)]

    @staticmethod
    def cvtColor(img: np.ndarray, code: int) -> np.ndarray:
        if img.ndim == 2:
            return img.copy()
        # Standard BGR to Grayscale luminance formula
        b = img[:, :, 0].astype(np.float32)
        g = img[:, :, 1].astype(np.float32)
        r = img[:, :, 2].astype(np.float32)
        return (0.114 * b + 0.587 * g + 0.299 * r).astype(np.uint8)

    @staticmethod
    def createCLAHE(clipLimit: float = 2.0, tileGridSize: tuple[int, int] = (8, 8)) -> _CLAHEFallback:
        return _CLAHEFallback()

    @staticmethod
    def GaussianBlur(img: np.ndarray, ksize: tuple[int, int], sigmaX: float) -> np.ndarray:
        radius = max(1, ksize[0] // 2)
        return _integral_box_mean(img.astype(np.float32), radius).astype(np.uint8)

    @staticmethod
    def absdiff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return np.abs(a.astype(np.int16) - b.astype(np.int16)).astype(np.uint8)

    @staticmethod
    def addWeighted(
        src1: np.ndarray, alpha: float, src2: np.ndarray, beta: float, gamma: float
    ) -> np.ndarray:
        res = src1.astype(np.float32) * alpha + src2.astype(np.float32) * beta + gamma
        return np.clip(res, 0, 255).astype(np.uint8)

    @staticmethod
    def threshold(src: np.ndarray, thresh: float, maxval: float, type: int) -> tuple[float, np.ndarray]:
        if type == _NumpyCV2Fallback.THRESH_BINARY_INV:
            out = np.where(src <= thresh, int(maxval), 0).astype(np.uint8)
        else:
            out = np.where(src > thresh, int(maxval), 0).astype(np.uint8)
        return thresh, out

    @staticmethod
    def adaptiveThreshold(
        src: np.ndarray,
        maxValue: float,
        adaptiveMethod: int,
        thresholdType: int,
        blockSize: int,
        C: float,
    ) -> np.ndarray:
        radius = max(3, blockSize // 2)
        local_mean = _integral_box_mean(src.astype(np.float32), radius)
        if thresholdType == _NumpyCV2Fallback.THRESH_BINARY_INV:
            return np.where(src.astype(np.float32) < (local_mean - C), int(maxValue), 0).astype(np.uint8)
        return np.where(src.astype(np.float32) > (local_mean - C), int(maxValue), 0).astype(np.uint8)

    @staticmethod
    def count_nonzero(arr: np.ndarray) -> int:
        return int(np.count_nonzero(arr))

    @staticmethod
    def remap(
        src: np.ndarray,
        map1: np.ndarray,
        map2: np.ndarray,
        interpolation: int = 1,
        borderMode: int = 4,
    ) -> np.ndarray:
        """Bilinear image remap for sub-pixel breathing simulation."""
        h, w = src.shape[:2]
        x = np.clip(map1, 0.0, w - 1.001)
        y = np.clip(map2, 0.0, h - 1.001)

        x0 = np.floor(x).astype(np.int32)
        y0 = np.floor(y).astype(np.int32)
        x1 = np.minimum(x0 + 1, w - 1)
        y1 = np.minimum(y0 + 1, h - 1)

        wx = (x - x0)[..., np.newaxis]
        wy = (y - y0)[..., np.newaxis]

        Ia = src[y0, x0].astype(np.float32)
        Ib = src[y1, x0].astype(np.float32)
        Ic = src[y0, x1].astype(np.float32)
        Id = src[y1, x1].astype(np.float32)

        top = Ia * (1.0 - wx) + Ic * wx
        bottom = Ib * (1.0 - wx) + Id * wx
        out = top * (1.0 - wy) + bottom * wy
        return np.clip(out, 0, 255).astype(np.uint8)

    @staticmethod
    def rectangle(
        img: np.ndarray,
        pt1: tuple[int, int],
        pt2: tuple[int, int],
        color: tuple[int, int, int],
        thickness: int = 1,
    ) -> np.ndarray:
        x1, y1 = max(0, pt1[0]), max(0, pt1[1])
        x2, y2 = min(img.shape[1], pt2[0]), min(img.shape[0], pt2[1])
        if thickness < 0:
            img[y1:y2, x1:x2] = color
        else:
            img[y1 : y1 + thickness, x1:x2] = color
            img[max(0, y2 - thickness) : y2, x1:x2] = color
            img[y1:y2, x1 : x1 + thickness] = color
            img[y1:y2, max(0, x2 - thickness) : x2] = color
        return img

    @staticmethod
    def putText(
        img: np.ndarray,
        text: str,
        org: tuple[int, int],
        fontFace: int,
        fontScale: float,
        color: tuple[int, int, int],
        thickness: int = 1,
        lineType: int = 16,
    ) -> np.ndarray:
        return img

    @staticmethod
    def goodFeaturesToTrack(
        gray: np.ndarray,
        maxCorners: int = 80,
        qualityLevel: float = 0.05,
        minDistance: int = 6,
        blockSize: int = 7,
        mask: np.ndarray | None = None,
    ) -> np.ndarray | None:
        f = gray.astype(np.float32)
        gx = np.zeros_like(f)
        gy = np.zeros_like(f)
        gx[:, 1:-1] = (f[:, 2:] - f[:, :-2]) * 0.5
        gy[1:-1, :] = (f[2:, :] - f[:-2, :]) * 0.5
        grad_mag = gx * gx + gy * gy
        if mask is not None:
            grad_mag = np.where(mask > 0, grad_mag, 0.0)

        # Downsample onto a grid of `minDistance` cells to pick well-separated strong corners
        step = max(4, int(minDistance))
        h, w = gray.shape[:2]
        candidates: list[tuple[float, float, float]] = []
        for y in range(step, h - step, step):
            for x in range(step, w - step, step):
                patch = grad_mag[y : y + step, x : x + step]
                max_val = float(np.max(patch))
                if max_val > 150.0:
                    idx = int(np.argmax(patch))
                    dy, dx = divmod(idx, patch.shape[1])
                    candidates.append((max_val, float(x + dx), float(y + dy)))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        selected = candidates[:maxCorners]
        pts = np.array([[[c[1], c[2]]] for c in selected], dtype=np.float32)
        return pts

    @staticmethod
    def calcOpticalFlowPyrLK(
        prevImg: np.ndarray,
        nextImg: np.ndarray,
        prevPts: np.ndarray,
        nextPts: Any = None,
        winSize: tuple[int, int] = (15, 15),
        maxLevel: int = 2,
        criteria: Any = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Standard iterative Lucas-Kanade sub-pixel optical flow solver in vectorized NumPy."""
        I0 = prevImg.astype(np.float32)
        I1 = nextImg.astype(np.float32)
        h, w = I0.shape[:2]
        r = max(4, winSize[0] // 2)

        gx = np.zeros_like(I0)
        gy = np.zeros_like(I0)
        gx[:, 1:-1] = (I0[:, 2:] - I0[:, :-2]) * 0.5
        gy[1:-1, :] = (I0[2:, :] - I0[:-2, :]) * 0.5
        dt_img = I1 - I0

        n_pts = len(prevPts)
        out_pts = prevPts.copy()
        status = np.zeros((n_pts, 1), dtype=np.uint8)
        err = np.zeros((n_pts, 1), dtype=np.float32)

        for i in range(n_pts):
            px, py = float(prevPts[i, 0, 0]), float(prevPts[i, 0, 1])
            ix, iy = int(round(px)), int(round(py))
            if ix - r < 1 or ix + r + 1 >= w or iy - r < 1 or iy + r + 1 >= h:
                continue

            ix_patch = gx[iy - r : iy + r + 1, ix - r : ix + r + 1]
            iy_patch = gy[iy - r : iy + r + 1, ix - r : ix + r + 1]
            it_patch = dt_img[iy - r : iy + r + 1, ix - r : ix + r + 1]

            sxx = float(np.sum(ix_patch * ix_patch))
            syy = float(np.sum(iy_patch * iy_patch))
            sxy = float(np.sum(ix_patch * iy_patch))
            det = sxx * syy - sxy * sxy
            if det < 1e-3:
                continue

            b_x = -float(np.sum(ix_patch * it_patch))
            b_y = -float(np.sum(iy_patch * it_patch))

            u = (syy * b_x - sxy * b_y) / det
            v = (-sxy * b_x + sxx * b_y) / det

            if abs(u) < r and abs(v) < r:
                out_pts[i, 0, 0] = px + u
                out_pts[i, 0, 1] = py + v
                status[i, 0] = 1

        return out_pts, status, err


cv2: Any = _real_cv2 if HAS_NATIVE_CV2 else _NumpyCV2Fallback

