"""Unit & Integration Tests for LullabAI Twins System 1 Edge Detector (Infant vs. Adult AI)."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "edge" / "detector" / "src"))
sys.path.insert(0, str(ROOT / "edge" / "mock-camera"))

from audio_cry import AudioCryDetector, AudioDetectionResult, vocalization_score  # noqa: E402
from breathing_band import BreathingBandTracker  # noqa: E402
from cloud_sync import CloudSyncClient  # noqa: E402
from cv_compat import cv2  # noqa: E402
from local_events import LocalEventStore  # noqa: E402
from main import BabyMonitorPipeline  # noqa: E402
from mock_streamer import MockCameraState  # noqa: E402
from motion import MotionDetector  # noqa: E402
from pet_cover import PetCoverDetector  # noqa: E402
from stream_reader import AUDIO_SAMPLE_RATE, RingBufferStreamReader  # noqa: E402


SAMPLE_IMAGE = ROOT / "edge" / "assets" / "sample_baby_band.jpg"
SAMPLE_DOG_PEN = ROOT / "edge" / "assets" / "sample_dog_pen.jpg"
SAMPLE_DOG_HELD_TP260 = ROOT / "edge" / "assets" / "sample_dog_held_tp260.jpg"
SAMPLE_TP260_PLAYPEN_DOG1 = ROOT / "edge" / "assets" / "sample_tp260_playpen_dog1.jpg"
SAMPLE_TP260_PLAYPEN_DOG2 = ROOT / "edge" / "assets" / "sample_tp260_playpen_dog2.jpg"
SAMPLE_TP260_PLAYPEN_EMPTY = ROOT / "edge" / "assets" / "sample_tp260_playpen_empty.jpg"


def test_cloud_review_mode_persists_and_skips_events_from_earlier_modes(tmp_path: Path) -> None:
    def event(event_id: str) -> dict:
        return {
            "event_id": event_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "trigger_type": "CRY",
            "media": {"local_video_path": f"/media/{event_id}.mp4"},
        }

    store = LocalEventStore(tmp_path)
    cloud = CloudSyncClient(store, firestore=None, bucket=None)
    assert not cloud.enabled
    local_event = event("evt_123_abcd01")
    cloud.set_enabled(True)
    assert CloudSyncClient(store, firestore=None, bucket=None).enabled
    cloud.enqueue(local_event)
    assert "cloud_review" not in local_event

    pending = event("evt_124_abcd02")
    cloud.enqueue(pending)
    assert pending["cloud_review"] == "PENDING"
    marker = cloud.queue / pending["event_id"]
    cloud.set_enabled(False)
    assert not CloudSyncClient(store, firestore=None, bucket=None).enabled
    assert "cloud_review" not in cloud.with_review(pending)
    cloud.set_enabled(True)
    cloud._sync(marker)
    assert not marker.exists()
    stored = store.load(pending["event_id"])
    assert stored is not None and "cloud_review" not in stored
    assert stored["media"] == pending["media"]


def test_detect_pomeranian_dog_in_playpen_and_reject_empty_playpen() -> None:
    """Verifies that the Pomeranian dog inside the playpen is detected as PET, and empty playpen returns NONE."""
    assert SAMPLE_DOG_PEN.exists(), f"Missing dog sample image at {SAMPLE_DOG_PEN}"
    dog_img = cv2.imread(str(SAMPLE_DOG_PEN))
    assert dog_img is not None

    tracker = BreathingBandTracker()
    res = tracker.process_frame(dog_img, timestamp=500.0)
    assert res.subject_type == "PET"
    assert res.band_detected is True
    assert res.band_bbox is not None
    bx, by, bw, bh = res.band_bbox
    assert bw >= 45 and bh >= 45

    # Now erase the dog from the playpen floor (leaving fence slats, curtain, bed, water bowl, and toys)
    empty_pen = dog_img.copy()
    h, w = empty_pen.shape[:2]
    empty_pen[int(h * 0.28) : int(h * 0.65), int(w * 0.22) : int(w * 0.62)] = (195, 205, 212)

    tracker_empty = BreathingBandTracker()
    res_empty = tracker_empty.process_frame(empty_pen, timestamp=505.0)
    assert res_empty.subject_type == "NONE"
    assert res_empty.band_detected is False
    assert res_empty.band_bbox is None


def test_detect_pomeranian_held_in_front_of_tp260_camera() -> None:
    """Verifies that holding the Pomeranian in front of the live TP260 camera detects PET at both native and 1280x720."""
    assert SAMPLE_DOG_HELD_TP260.exists(), f"Missing TP260 held dog sample at {SAMPLE_DOG_HELD_TP260}"
    tp_img = cv2.imread(str(SAMPLE_DOG_HELD_TP260))
    assert tp_img is not None

    for frame in (tp_img, cv2.resize(tp_img, (1280, 720))):
        tracker = BreathingBandTracker()
        res = tracker.process_frame(frame, timestamp=600.0)
        assert res.subject_type == "PET"
        assert res.band_detected is True
        assert res.band_bbox is not None

    # Erase the dog with the wearer's shirt color so only the adult + room/window remain
    tp_adult_only = tp_img.copy()
    tp_adult_only[80:188, 45:162] = (101, 85, 150)

    for frame in (tp_adult_only, cv2.resize(tp_adult_only, (1280, 720))):
        tracker_adult = BreathingBandTracker()
        res_adult = tracker_adult.process_frame(frame, timestamp=605.0)
        assert res_adult.subject_type == "ADULT"
        assert res_adult.band_detected is False


def test_tp260_live_playpen_views_with_and_without_dog_at_hd() -> None:
    """Verifies the 3 live TP260 playpen views (2 with dog in top-right/right, 1 empty playpen) at 1280x720."""
    for dog_path in (SAMPLE_TP260_PLAYPEN_DOG1, SAMPLE_TP260_PLAYPEN_DOG2):
        img = cv2.imread(str(dog_path))
        assert img is not None
        hd = cv2.resize(img, (1280, 720))
        res = BreathingBandTracker().process_frame(hd, timestamp=700.0)
        assert res.subject_type == "PET"
        assert res.band_detected is True
        assert res.band_bbox is not None

    empty_img = cv2.imread(str(SAMPLE_TP260_PLAYPEN_EMPTY))
    assert empty_img is not None
    empty_hd = cv2.resize(empty_img, (1280, 720))
    res_empty = BreathingBandTracker().process_frame(empty_hd, timestamp=710.0)
    assert res_empty.subject_type == "NONE"
    assert res_empty.band_detected is False
    assert res_empty.band_bbox is None


def test_pet_box_follows_consecutive_frames_and_clears_when_dog_leaves() -> None:
    tracker = BreathingBandTracker()
    dog_top, dog_right, empty = (
        cv2.imread(str(path))
        for path in (SAMPLE_TP260_PLAYPEN_DOG1, SAMPLE_TP260_PLAYPEN_DOG2, SAMPLE_TP260_PLAYPEN_EMPTY)
    )
    frames = [cv2.resize(frame, (1280, 720)) for frame in (empty, dog_top, dog_right, empty)]
    empty_first, first, second, empty_last = [tracker.process_frame(frame) for frame in frames]

    assert empty_first.subject_type == empty_last.subject_type == "NONE"
    assert empty_last.band_bbox is None
    assert first.subject_type == second.subject_type == "PET"
    assert first.band_bbox is not None and second.band_bbox is not None
    assert first.band_bbox[0] + first.band_bbox[2] >= 1280 * 0.90
    assert second.band_bbox[1] > first.band_bbox[1] + 100


def test_idle_edge_does_not_emit_periodic_events_with_legacy_config(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("edge_main", ROOT / "edge/detector/src/main.py")
    assert spec is not None and spec.loader is not None
    edge_main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(edge_main)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(edge_main, "time", SimpleNamespace(time=lambda: clock.now))
    pipeline = edge_main.BabyMonitorPipeline(
        {"baby_id": "test", "camera_id": "test", "video_source_url": ""},
        {"subject_mode": "BABY", "periodic_snapshot_interval_sec": 300},
        SimpleNamespace(),
    )
    empty = cv2.resize(cv2.imread(str(SAMPLE_TP260_PLAYPEN_EMPTY)), (640, 360))
    assert pipeline.step_once(empty)["triggered"] is None

    clock.now += 600
    assert pipeline.step_once(empty)["triggered"] is None

    pipeline._latest_audio = edge_main.AudioDetectionResult(0.9, "Baby cry", 0.1, True)
    assert pipeline.step_once(empty)["triggered"] == "CRY"


def test_local_trigger_persists_history_and_snapshot_without_cloud(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("edge_main", ROOT / "edge/detector/src/main.py")
    assert spec is not None and spec.loader is not None
    edge_main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(edge_main)
    store = LocalEventStore(tmp_path)
    pipeline = edge_main.BabyMonitorPipeline(
        {"baby_id": "baby_1", "camera_id": "test", "video_source_url": ""},
        {"post_roll_seconds": 0},
        store,
    )
    monkeypatch.setattr(pipeline.reader, "get_buffered_frames", lambda **kwargs: [(0.0, np.zeros((32, 32, 3), dtype=np.uint8))])

    def export_snapshot(directory, event_id, frames, *, include_audio):
        assert include_audio
        snapshot = directory / f"{event_id}_thumb.jpg"
        snapshot.write_bytes(b"test snapshot")
        return directory / f"{event_id}.mp4", snapshot, None

    monkeypatch.setattr(pipeline.reader, "export_clip_and_snapshot", export_snapshot)
    event = pipeline.handle_triggered_event("CRY", {"yamnet_cry_prob": 0.7})

    assert event["status"] == "EDGE_DETECTED"
    assert event["ai_analysis"] is None
    assert LocalEventStore(tmp_path).recent("baby_1") == [event]
    assert LocalEventStore(tmp_path).recent("baby_2") == []
    snapshot = event["media"]["local_snapshot_path"].removeprefix("/media/")
    assert store.media_path(snapshot).read_bytes() == b"test snapshot"
    assert store.media_path("../" + snapshot) is None

    legacy = {**event, "event_id": "evt_9999999998_000001", "status": "PENDING_AI"}
    periodic = {**legacy, "event_id": "evt_9999999999_000002", "trigger_type": "PERIODIC_CHECK"}
    store.save(legacy)
    store.save(periodic)
    reopened = store.recent("baby_1")
    assert [item["event_id"] for item in reopened] == [legacy["event_id"], event["event_id"]]
    assert reopened[0]["status"] == "EDGE_DETECTED"
    assert json.loads((tmp_path / f"{legacy['event_id']}.json").read_text())["status"] == "PENDING_AI"


def test_buffered_microphone_audio_keeps_gaps_and_clips_to_requested_window() -> None:
    reader = RingBufferStreamReader("")
    half_second = AUDIO_SAMPLE_RATE // 2
    quarter_second = AUDIO_SAMPLE_RATE // 4
    reader._buffer_audio(100.0, np.full(half_second, 1200, dtype="<i2"))
    reader._buffer_audio(100.75, np.full(half_second, -800, dtype="<i2"))

    audio = reader.get_buffered_audio(100.25, 101.0)
    assert audio is not None
    np.testing.assert_array_equal(audio[:quarter_second], 1200)
    np.testing.assert_array_equal(audio[quarter_second:half_second], 0)
    np.testing.assert_array_equal(audio[half_second:], -800)
    assert audio.size == 3 * quarter_second
    assert reader.get_buffered_audio(101.25, 102.0) is None


def test_cover_alert_requires_a_tracked_pet_and_fires_once_per_episode(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("edge_main", ROOT / "edge/detector/src/main.py")
    assert spec is not None and spec.loader is not None
    edge_main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(edge_main)
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(edge_main, "time", SimpleNamespace(time=lambda: clock.now, monotonic=lambda: clock.now))
    monkeypatch.delenv("SUBJECT_MODE", raising=False)

    pipeline = edge_main.BabyMonitorPipeline(
        {"baby_id": "test", "camera_id": "test", "video_source_url": "", "pet_cover_detection": True},
        {"subject_mode": "BABY", "cooldowns": {"BAND_ROLLOVER": 25}},
        SimpleNamespace(),
    )
    pet = cv2.resize(cv2.imread(str(SAMPLE_TP260_PLAYPEN_DOG2)), (640, 360))
    empty = cv2.resize(cv2.imread(str(SAMPLE_TP260_PLAYPEN_EMPTY)), (640, 360))
    bbox = BreathingBandTracker().process_frame(pet).band_bbox
    assert bbox is not None
    x, y, width, height = bbox
    covered = empty.copy()
    covered[y : y + height, x : x + width] = (110, 175, 208)
    silence = np.zeros(16000, dtype=np.float32)

    def step(frame, elapsed):
        clock.now = 100.0 + elapsed
        return pipeline.step_once(frame, audio_waveform=silence)

    for elapsed in (0, 0.5, 1.1):
        assert not step(covered, elapsed)["edge_metrics"]["face_cover_suspected"]

    assert step(pet, 1.2)["edge_metrics"]["subject_type"] == "PET"
    for elapsed in (1.3, 1.6, 2.0):
        assert step(covered, elapsed)["triggered"] != "BAND_ROLLOVER"
    status = step(covered, 2.4)
    assert status["triggered"] == "BAND_ROLLOVER"
    assert status["edge_metrics"]["face_cover_suspected"]
    assert not status["edge_metrics"]["band_detected"]

    for elapsed in range(3, 30):
        status = step(covered, elapsed)
        assert status["triggered"] != "BAND_ROLLOVER"
        assert status["edge_metrics"]["face_cover_suspected"]

    step(empty, 30)
    assert not step(empty, 30.6)["edge_metrics"]["face_cover_suspected"]
    step(pet, 31)
    step(covered, 31.1)
    step(covered, 31.5)
    assert step(covered, 32.2)["triggered"] == "BAND_ROLLOVER"


def test_cover_detector_rejects_a_blanket_beside_the_pet() -> None:
    detector = PetCoverDetector()
    frame = np.full((360, 640, 3), 212, dtype=np.uint8)
    frame[110:270, 330:490] = (110, 175, 208)
    for timestamp in (0.0, 0.5, 1.2):
        assert not detector.process_frame(frame, [160, 110, 160, 160], timestamp)


def test_detect_infant_on_uploaded_sample_image() -> None:
    """Verifies that BreathingBandTracker classifies the infant photo as BABY without RPM."""
    assert SAMPLE_IMAGE.exists(), f"Missing sample image at {SAMPLE_IMAGE}"
    img = cv2.imread(str(SAMPLE_IMAGE))
    assert img is not None

    tracker = BreathingBandTracker()
    res = tracker.process_frame(img, timestamp=1000.0)

    assert res.subject_type == "BABY"
    assert res.band_detected is True
    assert res.breathing_rpm is None
    assert res.band_confidence >= 0.70
    assert res.band_bbox is not None


def test_markerless_infant_detection_without_band() -> None:
    """Verifies that an infant wearing plain clothes (no band) is classified as BABY."""
    cam = MockCameraState(SAMPLE_IMAGE)
    h, w = cam.base_frame.shape[:2]
    x1, x2 = int(w * 0.33), int(w * 0.61)
    y1, y2 = int(h * 0.54), int(h * 0.83)
    yy, xx = np.indices((y2 - y1, x2 - x1), dtype=np.float32)
    fabric = 230.0 + 14.0 * np.sin(yy / 6.0) + 8.0 * np.cos(xx / 9.0)
    cam.base_frame[y1:y2, x1:x2, 0] = np.clip(fabric, 180, 250).astype(np.uint8)
    cam.base_frame[y1:y2, x1:x2, 1] = np.clip(fabric + 4.0, 180, 250).astype(np.uint8)
    cam.base_frame[y1:y2, x1:x2, 2] = np.clip(fabric + 8.0, 180, 250).astype(np.uint8)

    tracker = BreathingBandTracker()
    res = tracker.process_frame(cam.render_frame(now=0.0), timestamp=0.0)

    assert res.subject_type == "BABY"
    assert res.band_detected is True
    assert res.breathing_rpm is None


def test_adult_in_front_of_camera_is_classified_as_adult_not_baby() -> None:
    """Verifies that an adult (head + wide shoulders anchored to bottom frame) is classified as ADULT."""
    h, w = 720, 960
    # Neutral room wall background
    frame = np.full((h, w, 3), (210, 215, 220), dtype=np.uint8)

    # Draw realistic adult silhouette in front of camera:
    # Adult head with realistic warm skin tone (BGR: B=110, G=145, R=195)
    frame[150:330, 405:555] = (110, 145, 195)
    # Add symmetric adult eyes, eyebrows, nasal bridge, and mouth horizontal edges
    frame[195:210, 425:465] = (40, 45, 60)   # Left eye/brow
    frame[195:210, 495:535] = (40, 45, 60)   # Right eye/brow
    frame[210:270, 472:488] = (75, 105, 150) # Nasal bridge
    frame[285:300, 450:510] = (65, 80, 130)  # Mouth/jaw
    # Adult broad shoulders & upper torso extending to bottom of frame (width ~460px, y: 335..710, x: 250..710)
    frame[335:710, 250:710] = (50, 65, 90)

    tracker = BreathingBandTracker()
    res = tracker.process_frame(frame, timestamp=10.0)

    assert res.subject_type == "ADULT"
    assert res.band_detected is False
    assert res.breathing_rpm is None
    assert res.band_bbox is not None


def test_empty_crib_and_furnished_room_are_classified_as_none() -> None:
    """Verifies that both a blank crib and an empty room with furniture/curtains/pillows return NONE."""
    tracker = BreathingBandTracker()

    # 1. Uniform empty crib
    blank_frame = np.full((720, 960, 3), (200, 205, 210), dtype=np.uint8)
    res_blank = tracker.process_frame(blank_frame, timestamp=20.0)
    assert res_blank.subject_type == "NONE"
    assert res_blank.band_detected is False
    assert res_blank.band_bbox is None

    # 2. Empty room with textured walls, window curtain, wooden cabinet, and bed pillow (no person)
    room = np.full((720, 960, 3), (185, 190, 195), dtype=np.uint8)
    # Curtain stripes on left
    for x in range(100, 320, 24):
        room[80:550, x : x + 12] = (120, 130, 140)
    # Dark cabinet on right
    room[220:680, 620:860] = (55, 65, 75)
    # White pillow in center
    room[420:580, 340:580] = (235, 240, 242)

    tracker2 = BreathingBandTracker()
    res_room = tracker2.process_frame(room, timestamp=25.0)
    assert res_room.subject_type == "NONE"
    assert res_room.band_detected is False
    assert res_room.band_bbox is None


def test_audio_detector_does_not_use_a_tone_as_fallback(tmp_path: Path) -> None:
    cam = MockCameraState(SAMPLE_IMAGE)
    detector = AudioCryDetector(tflite_model_path=tmp_path / "missing.tflite")

    cam.set_mode("CRY")
    result = detector.analyze_waveform(cam.generate_audio_samples(duration_sec=1.5))
    assert not detector.ready
    assert not result.cry_triggered
    assert result.audio_kind is None
    assert result.top_label == "Audio classifier unavailable"


def test_vocalization_score_requires_specific_baby_or_dog_labels() -> None:
    assert vocalization_score({"Dog": 0.95, "Pant": 0.95, "Crying, sobbing": 0.95})[0] == 0.0
    assert vocalization_score({"Bark": 0.50, "Dog": 0.80}) == (0.80, "Dog", "DOG_VOCALIZATION")
    assert vocalization_score({"Baby cry, infant cry": 0.70, "Bark": 0.10}) == (
        0.70, "Baby cry, infant cry", "BABY_CRY",
    )


def test_dog_vocalization_does_not_cool_down_baby_cry_alerts(tmp_path: Path) -> None:
    pipeline = BabyMonitorPipeline(
        {"baby_id": "test", "camera_id": "test", "video_source_url": ""},
        {"cooldowns": {"CRY": 30.0}},
        LocalEventStore(tmp_path),
    )
    subject = SimpleNamespace(subject_type="NONE", rollover_suspected=False)
    motion = SimpleNamespace(motion_triggered=False)
    dog = AudioDetectionResult(0.8, "Bark", 0.1, True, "DOG_VOCALIZATION")
    baby = AudioDetectionResult(0.8, "Baby cry, infant cry", 0.1, True, "BABY_CRY")

    assert pipeline.evaluate_triggers(subject, motion, dog, 100.0) == "CRY"
    assert pipeline.evaluate_triggers(subject, motion, dog, 101.0) is None
    assert pipeline.evaluate_triggers(subject, motion, baby, 102.0) == "CRY"
    assert pipeline.evaluate_triggers(subject, motion, baby, 103.0) is None


def test_motion_detector_roi() -> None:
    """Verifies that calm breathing does not trigger full-body motion alert, while MOTION mode does."""
    cam = MockCameraState(SAMPLE_IMAGE)
    motion_det = MotionDetector(motion_threshold=4.0, sustain_frames=2)

    cam.set_mode("NORMAL", rpm=32.0)
    for i in range(5):
        cam.start_time = -float(i) * 0.1
        res = motion_det.process_frame(cam.render_frame())
    assert res.motion_triggered is False

    cam.set_mode("MOTION")
    triggered_any = False
    for i in range(8):
        cam.start_time = -float(i) * 0.15
        res = motion_det.process_frame(cam.render_frame())
        if res.motion_triggered:
            triggered_any = True
    assert triggered_any is True
