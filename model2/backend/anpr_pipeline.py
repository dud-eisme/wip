"""
anpr_pipeline.py
Plate detection (YOLO) + OCR (EasyOCR) against a recorded clip or a
registered source, per the spec: "Script/endpoint running plate detection
(YOLO-based) + OCR (EasyOCR) against a recorded clip; logs detections to
Postgres."

These are HEAVY dependencies (ultralytics pulls in PyTorch). They are
imported lazily inside functions, not at module load time, so:
  - the rest of the app (sources registry, feed relay, events API) works
    perfectly even if ultralytics/easyocr are not installed
  - only calling the ANPR endpoints requires them, and you get one clear
    error message telling you what to install, instead of the whole app
    failing to start.

This module intentionally does frame-level work only (detect -> crop ->
OCR -> return results). The job orchestration (looping a video, writing to
Postgres, updating job status) lives in routers/anpr.py so this file stays
easy to unit-test on its own.
"""
import os
from typing import List, NamedTuple, Optional

import numpy as np

ANPR_YOLO_WEIGHTS = os.getenv("ANPR_YOLO_WEIGHTS", "yolov8n.pt")

_yolo_model = None
_ocr_reader = None


class PlateDetection(NamedTuple):
    plate_text: str
    confidence: float
    bbox: tuple  # (x1, y1, x2, y2) in the source frame


def _get_yolo_model():
    """Lazily loads the YOLO plate-detection model (singleton per process)."""
    global _yolo_model
    if _yolo_model is None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "ANPR pipeline requires 'ultralytics' (and PyTorch). Install with:\n"
                "  pip install ultralytics==8.3.0\n"
                "See README 'ANPR pipeline setup' for details."
            ) from exc
        _yolo_model = YOLO(ANPR_YOLO_WEIGHTS)
    return _yolo_model


def _get_ocr_reader():
    """Lazily loads the EasyOCR reader (singleton per process)."""
    global _ocr_reader
    if _ocr_reader is None:
        try:
            import easyocr
        except ImportError as exc:
            raise RuntimeError(
                "ANPR pipeline requires 'easyocr'. Install with:\n"
                "  pip install easyocr==1.7.2\n"
                "See README 'ANPR pipeline setup' for details."
            ) from exc
        _ocr_reader = easyocr.Reader(["en"], gpu=False)
    return _ocr_reader


def is_available() -> tuple[bool, Optional[str]]:
    """Checks whether ANPR deps are installed without raising — used by the
    API to return a clean 503 instead of a stack trace when they're missing."""
    try:
        import ultralytics  # noqa: F401
        import easyocr  # noqa: F401
    except ImportError as exc:
        return False, str(exc)
    return True, None


def detect_plates_in_frame(frame: "np.ndarray", min_confidence: float = 0.4) -> List[PlateDetection]:
    """
    Runs plate detection + OCR on a single BGR frame (as read by OpenCV).

    NOTE: a stock `yolov8n.pt` (general-object weights) will NOT reliably
    detect license plates — it's a general COCO-classes model, used here
    only to prove the wiring works end-to-end. For real plate detection,
    point ANPR_YOLO_WEIGHTS at weights fine-tuned on a license-plate
    dataset (several open ones exist; swap the .pt file, no code change
    needed).
    """
    model = _get_yolo_model()
    reader = _get_ocr_reader()

    results = model(frame, verbose=False)
    detections: List[PlateDetection] = []

    for result in results:
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        for box in boxes:
            conf = float(box.conf[0]) if box.conf is not None else 0.0
            if conf < min_confidence:
                continue
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
            x1, y1 = max(x1, 0), max(y1, 0)
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue

            ocr_results = reader.readtext(crop)
            if not ocr_results:
                continue
            # Take the highest-confidence text region found inside the crop
            _, text, ocr_conf = max(ocr_results, key=lambda r: r[2])
            cleaned = "".join(ch for ch in text if ch.isalnum()).upper()
            if not cleaned:
                continue

            combined_conf = round((conf + ocr_conf) / 2, 4)
            detections.append(
                PlateDetection(plate_text=cleaned, confidence=combined_conf, bbox=(x1, y1, x2, y2))
            )

    return detections
