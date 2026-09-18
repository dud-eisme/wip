"""
anpr_lowres.py
The low-resolution toolkit: things that help when the plate is simply too
small or too skewed for the existing single-frame path in
anpr_pipeline.py to read.

Read plate_consensus.py first. Multi-frame voting is a bigger win than
anything in this file, and it is free of the failure modes below. This
module is the second lever, not the first.

Four independent tools here, in rough order of how much they help on
genuinely bad CCTV footage:

  1. VEHICLE-ROI-GUIDED DETECTION (detect_plates_in_vehicle_rois)
     The strongest one, and it only became available once vehicle
     detection existed. A detector resizes its input to a fixed size
     (usually 640x640) before it sees anything, so on a 1080p wide shot a
     40px plate is already down to ~13px by the time the network looks at
     it. anpr_pipeline's tiling addresses this with a FIXED 2x2 grid,
     which is a blind guess at where plates are. Vehicle boxes are not a
     guess: crop each detected vehicle, upscale that crop to the
     detector's native input size, and run plate detection inside it.
     Every plate then gets the detector's full resolution budget, and
     detector calls scale with the number of vehicles actually present
     rather than with a fixed grid.

  2. DESKEW / PERSPECTIVE RECTIFICATION (rectify_plate)
     CCTV cameras are mounted high and off to one side, so plates arrive
     as trapezoids with characters sheared. Every OCR engine here was
     trained on roughly axis-aligned text. Warping the crop back to a
     canonical rectangle before OCR is often worth more than any amount
     of contrast enhancement, and costs almost nothing.

  3. SHARPNESS-BASED FRAME SELECTION (sharpness, pick_sharpest)
     Motion blur varies a lot frame to frame. When you have several crops
     of the same plate (which you do, via a track), running OCR on the
     three sharpest instead of all of them is both faster and more
     accurate.

  4. SUPER-RESOLUTION (upscale)
     Learned upscaling (FSRCNN/ESPCN via cv2.dnn_superres) instead of
     bicubic. It does measurably help OCR on small text.

     BUT — and this needs saying plainly in a system that may be used
     evidentially — super-resolution does not recover information. It
     produces a plausible high-resolution image consistent with the
     low-resolution one, and on text that means it can render a clean,
     confident, WRONG character. Bicubic upscaling produces a blurry 8
     that OCR reports with low confidence; an SR model can produce a
     crisp B that OCR reports with high confidence. The second failure is
     far more dangerous because it looks like success.

     Two consequences, both enforced below: SR is OFF by default, and
     when it is on, LOWRES_SR_KEEP_BASELINE runs OCR on the bicubic
     version too and prefers the SR read only when both agree or the
     baseline read nothing. Never store an SR image as the evidential
     snapshot — save the original crop.
"""
import logging
import os
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("cctv_model2.anpr_lowres")

# --- Vehicle-ROI-guided detection ---
LOWRES_VEHICLE_ROI_ENABLED = os.getenv("LOWRES_VEHICLE_ROI_ENABLED", "true").strip().lower() in (
    "1", "true", "yes",
)
# Vehicle box is expanded by this fraction before the plate search — a
# vehicle detector's box can clip the front bumper, and that is exactly
# where the plate is.
LOWRES_ROI_PADDING_RATIO = float(os.getenv("LOWRES_ROI_PADDING_RATIO", "0.08"))
# ROI crops smaller than this (in either dimension) are upscaled to it
# before the plate detector runs, so the detector's internal resize does
# not shrink them further. 640 matches the common YOLO input size.
LOWRES_ROI_TARGET_SIZE = int(os.getenv("LOWRES_ROI_TARGET_SIZE", "640"))
# Don't bother with the ROI pass for vehicles that already fill a large
# part of the frame — the full-frame pass reads those fine.
LOWRES_ROI_MAX_BOX_AREA_RATIO = float(os.getenv("LOWRES_ROI_MAX_BOX_AREA_RATIO", "0.25"))

# --- Deskew ---
LOWRES_DESKEW_ENABLED = os.getenv("LOWRES_DESKEW_ENABLED", "true").strip().lower() in (
    "1", "true", "yes",
)
# Skew below this (degrees) is left alone — warping a near-straight crop
# only resamples it and loses a little detail for nothing.
LOWRES_DESKEW_MIN_ANGLE = float(os.getenv("LOWRES_DESKEW_MIN_ANGLE", "2.0"))
# Above this, the "plate quad" found is almost certainly not a plate.
LOWRES_DESKEW_MAX_ANGLE = float(os.getenv("LOWRES_DESKEW_MAX_ANGLE", "35.0"))
# Canonical output aspect for a rectified single-line Indian plate
# (500mm x 120mm ≈ 4.17). Two-line plates are ~2:1; the value only sets
# the output canvas shape, so a mismatch stretches rather than breaks.
LOWRES_RECTIFIED_ASPECT = float(os.getenv("LOWRES_RECTIFIED_ASPECT", "4.17"))

# --- Super-resolution ---
LOWRES_SR_ENABLED = os.getenv("LOWRES_SR_ENABLED", "false").strip().lower() in (
    "1", "true", "yes",
)
LOWRES_SR_MODEL_PATH = os.getenv("LOWRES_SR_MODEL_PATH", "")  # e.g. models/FSRCNN_x4.pb
LOWRES_SR_MODEL_NAME = os.getenv("LOWRES_SR_MODEL_NAME", "fsrcnn").strip().lower()
LOWRES_SR_SCALE = int(os.getenv("LOWRES_SR_SCALE", "4"))
# Only worth it on genuinely small crops; a 100px-tall plate does not
# need inventing detail added to it.
LOWRES_SR_MAX_INPUT_HEIGHT = int(os.getenv("LOWRES_SR_MAX_INPUT_HEIGHT", "48"))
LOWRES_SR_KEEP_BASELINE = os.getenv("LOWRES_SR_KEEP_BASELINE", "true").strip().lower() in (
    "1", "true", "yes",
)

_sr_model = None
_sr_unavailable_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# 1. Vehicle-ROI-guided plate detection
# ---------------------------------------------------------------------------

def _pad_box(box: tuple, frame_w: int, frame_h: int, ratio: float) -> tuple:
    x1, y1, x2, y2 = box
    pad_x, pad_y = int((x2 - x1) * ratio), int((y2 - y1) * ratio)
    return (
        max(x1 - pad_x, 0), max(y1 - pad_y, 0),
        min(x2 + pad_x, frame_w), min(y2 + pad_y, frame_h),
    )


def detect_plates_in_vehicle_rois(
    frame: "np.ndarray",
    vehicle_boxes: List[tuple],
    detect_fn: Callable[["np.ndarray", float], List[tuple]],
    min_confidence: float,
    include_full_frame: bool = True,
) -> List[tuple]:
    """
    Runs `detect_fn` (anpr_pipeline._detect_boxes_yolo or
    _detect_boxes_indian_lpr_fcos — same signature) inside each vehicle
    box instead of once across the whole frame, and returns boxes in
    ORIGINAL-frame coordinates as (x1, y1, x2, y2, conf) tuples, NMS-
    merged.

    `vehicle_boxes` are (x1, y1, x2, y2) from
    vehicle_pipeline.detect_vehicles_in_frame(). Passing an empty list
    degrades to a plain full-frame pass, so a frame where vehicle
    detection found nothing still gets searched normally.

    Cost note: this is one plate-detector call per vehicle plus one
    full-frame pass. At a quiet gate that is cheaper than the fixed 2x2
    tiling it replaces; at a busy junction with 15 vehicles in shot it is
    more expensive. If throughput becomes the constraint, cap it by
    sorting vehicle boxes by area and taking the largest N rather than
    turning it off — the biggest boxes are the nearest vehicles, whose
    plates are the ones actually readable.
    """
    frame_h, frame_w = frame.shape[:2]
    frame_area = float(frame_h * frame_w) or 1.0
    all_boxes: List[tuple] = []

    if LOWRES_VEHICLE_ROI_ENABLED:
        for box in vehicle_boxes:
            vx1, vy1, vx2, vy2 = _pad_box(tuple(box), frame_w, frame_h, LOWRES_ROI_PADDING_RATIO)
            if vx2 <= vx1 or vy2 <= vy1:
                continue
            if ((vx2 - vx1) * (vy2 - vy1)) / frame_area > LOWRES_ROI_MAX_BOX_AREA_RATIO:
                continue  # already large in frame; the full-frame pass handles it

            roi = frame[vy1:vy2, vx1:vx2]
            if roi.size == 0:
                continue

            roi_h, roi_w = roi.shape[:2]
            scale = max(1.0, LOWRES_ROI_TARGET_SIZE / max(roi_h, roi_w))
            if scale > 1.0:
                roi = cv2.resize(
                    roi, (int(roi_w * scale), int(roi_h * scale)), interpolation=cv2.INTER_CUBIC
                )

            for (bx1, by1, bx2, by2, conf) in detect_fn(roi, min_confidence):
                # Back to ROI coordinates, then to full-frame coordinates.
                all_boxes.append((
                    int(bx1 / scale) + vx1, int(by1 / scale) + vy1,
                    int(bx2 / scale) + vx1, int(by2 / scale) + vy1,
                    conf,
                ))

    if include_full_frame or not all_boxes:
        all_boxes.extend(detect_fn(frame, min_confidence))

    return _nms(all_boxes, iou_threshold=0.3)


def _nms(boxes: List[tuple], iou_threshold: float) -> List[tuple]:
    """Same job as anpr_pipeline._nms_merge_boxes — duplicated rather than
    imported to keep this module free of a circular import back into
    anpr_pipeline, which imports nothing from here."""
    if not boxes:
        return []
    rects = [[x1, y1, x2 - x1, y2 - y1] for x1, y1, x2, y2, _ in boxes]
    scores = [float(conf) for *_, conf in boxes]
    indices = cv2.dnn.NMSBoxes(rects, scores, score_threshold=0.0, nms_threshold=iou_threshold)
    if indices is None or len(indices) == 0:
        return []
    return [boxes[i] for i in np.array(indices).flatten()]


# ---------------------------------------------------------------------------
# 2. Deskew / perspective rectification
# ---------------------------------------------------------------------------

def _order_quad(points: "np.ndarray") -> "np.ndarray":
    """Orders 4 points as top-left, top-right, bottom-right, bottom-left,
    which is what getPerspectiveTransform expects. Uses coordinate sums
    and differences rather than angles — cheap and robust to the mild
    rotations a plate crop actually has."""
    points = points.reshape(4, 2).astype(np.float32)
    summed = points.sum(axis=1)
    diffed = np.diff(points, axis=1).reshape(-1)
    return np.array([
        points[np.argmin(summed)],   # top-left: smallest x+y
        points[np.argmin(diffed)],   # top-right: smallest y-x
        points[np.argmax(summed)],   # bottom-right
        points[np.argmax(diffed)],   # bottom-left
    ], dtype=np.float32)


def rectify_plate(crop: "np.ndarray") -> "np.ndarray":
    """
    Returns a deskewed copy of the plate crop, or the crop unchanged if
    no plausible plate quadrilateral was found.

    Tries perspective rectification first (find the plate's four corners
    and warp them to a rectangle), and falls back to pure rotation via
    minAreaRect when the contour isn't cleanly quadrilateral — which is
    common, since a low-contrast plate edge often merges into the bumper.
    Rotation-only still fixes the shear that hurts OCR most.

    Fails closed: any exception returns the original crop. A geometry bug
    must never be able to blank out a readable plate.
    """
    if not LOWRES_DESKEW_ENABLED or crop is None or crop.size == 0:
        return crop
    try:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        blurred = cv2.bilateralFilter(gray, 5, 60, 60)
        edges = cv2.Canny(blurred, 40, 130)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return crop

        largest = max(contours, key=cv2.contourArea)
        # Ignore a contour that's a small fraction of the crop — it's a
        # character or a bolt, not the plate outline.
        if cv2.contourArea(largest) < 0.25 * crop.shape[0] * crop.shape[1]:
            return _rotate_to_horizontal(crop, largest)

        perimeter = cv2.arcLength(largest, True)
        approx = cv2.approxPolyDP(largest, 0.02 * perimeter, True)
        if len(approx) != 4:
            return _rotate_to_horizontal(crop, largest)

        quad = _order_quad(approx)
        height = int(max(
            np.linalg.norm(quad[0] - quad[3]), np.linalg.norm(quad[1] - quad[2])
        ))
        if height <= 0:
            return crop
        width = int(height * LOWRES_RECTIFIED_ASPECT)
        target = np.array(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32
        )
        matrix = cv2.getPerspectiveTransform(quad, target)
        return cv2.warpPerspective(crop, matrix, (width, height), flags=cv2.INTER_CUBIC)
    except Exception:
        logger.debug("rectify_plate failed — using the original crop", exc_info=True)
        return crop


def _rotate_to_horizontal(crop: "np.ndarray", contour) -> "np.ndarray":
    """Rotation-only fallback. minAreaRect's angle is reported in
    [-90, 0) by OpenCV, so a plate tilted slightly clockwise and one
    tilted slightly anticlockwise can come back as -2 and -88; the
    normalisation below maps both to a small correction rather than an
    89-degree one."""
    try:
        angle = cv2.minAreaRect(contour)[-1]
        if angle < -45:
            angle += 90
        if abs(angle) < LOWRES_DESKEW_MIN_ANGLE or abs(angle) > LOWRES_DESKEW_MAX_ANGLE:
            return crop
        h, w = crop.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        return cv2.warpAffine(
            crop, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
        )
    except Exception:
        return crop


# ---------------------------------------------------------------------------
# 3. Sharpness
# ---------------------------------------------------------------------------

def sharpness(image: "np.ndarray") -> float:
    """Variance of the Laplacian: the standard cheap focus/blur metric.
    Higher is sharper. The absolute value is not comparable between
    differently-sized crops, so only ever compare crops of the SAME plate
    at similar scales — which is exactly the pick_sharpest use case."""
    if image is None or image.size == 0:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def pick_sharpest(crops: List["np.ndarray"], count: int = 3) -> List["np.ndarray"]:
    """Returns the `count` sharpest crops, sharpest first. Use it to
    spend the OCR budget on the frames most likely to read correctly
    rather than spreading it evenly across a track's motion-blurred
    ones."""
    scored = [(sharpness(c), c) for c in crops if c is not None and c.size]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [crop for _, crop in scored[:count]]


# ---------------------------------------------------------------------------
# 4. Super-resolution
# ---------------------------------------------------------------------------

def _get_sr_model():
    """Loads the dnn_superres model once. Needs opencv-contrib-python —
    the plain opencv-python wheel does not ship the dnn_superres module,
    which is a common and confusing install failure, hence the explicit
    message."""
    global _sr_model, _sr_unavailable_reason
    if _sr_model is not None or _sr_unavailable_reason is not None:
        return _sr_model
    try:
        sr = cv2.dnn_superres.DnnSuperResImpl_create()
    except AttributeError:
        _sr_unavailable_reason = (
            "cv2.dnn_superres is missing — install opencv-contrib-python "
            "(the plain opencv-python wheel does not include it)"
        )
        logger.warning("Super-resolution disabled: %s", _sr_unavailable_reason)
        return None
    if not LOWRES_SR_MODEL_PATH or not os.path.isfile(LOWRES_SR_MODEL_PATH):
        _sr_unavailable_reason = f"LOWRES_SR_MODEL_PATH is unset or missing: {LOWRES_SR_MODEL_PATH!r}"
        logger.warning("Super-resolution disabled: %s", _sr_unavailable_reason)
        return None
    try:
        sr.readModel(LOWRES_SR_MODEL_PATH)
        sr.setModel(LOWRES_SR_MODEL_NAME, LOWRES_SR_SCALE)
    except Exception as exc:  # noqa: BLE001
        _sr_unavailable_reason = str(exc)
        logger.warning("Super-resolution disabled: %s", exc)
        return None
    _sr_model = sr
    logger.info("Super-resolution ready: %s x%d", LOWRES_SR_MODEL_NAME, LOWRES_SR_SCALE)
    return _sr_model


def upscale(crop: "np.ndarray", target_height: int) -> Tuple["np.ndarray", "np.ndarray"]:
    """
    Returns (baseline, enhanced) — bicubic and super-resolved versions of
    the crop, both at roughly target_height. When SR is disabled or
    unavailable, both are the same bicubic image.

    Returning BOTH is the point, not an inefficiency: see the module
    docstring. Feed both to OCR and only trust the SR read when it agrees
    with the baseline or the baseline read nothing. Store the ORIGINAL
    crop as the snapshot, never either of these.
    """
    if crop is None or crop.size == 0:
        return crop, crop

    h, w = crop.shape[:2]
    scale = max(1.0, target_height / max(h, 1))
    baseline = crop if scale <= 1.0 else cv2.resize(
        crop, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC
    )

    if not LOWRES_SR_ENABLED or h > LOWRES_SR_MAX_INPUT_HEIGHT:
        return baseline, baseline

    model = _get_sr_model()
    if model is None:
        return baseline, baseline
    try:
        enhanced = model.upsample(crop)
    except Exception:
        logger.debug("Super-resolution upsample failed — using bicubic", exc_info=True)
        return baseline, baseline

    # Match heights so downstream OCR sees comparable inputs.
    eh, ew = enhanced.shape[:2]
    if eh != baseline.shape[0]:
        ratio = baseline.shape[0] / max(eh, 1)
        enhanced = cv2.resize(
            enhanced, (int(ew * ratio), baseline.shape[0]), interpolation=cv2.INTER_AREA
        )
    return baseline, enhanced


def reconcile_sr_read(
    baseline_read: Optional[tuple],
    sr_read: Optional[tuple],
) -> Optional[tuple]:
    """
    Decides which of two (text, confidence) OCR results to trust when SR
    is in play. The rule is intentionally conservative:

      - both read the same text -> that text, with the higher confidence
      - baseline read nothing   -> the SR read (nothing to contradict it)
      - they disagree           -> the BASELINE read

    The last line is the important one. A disagreement means SR changed
    what the characters look like, which is exactly the hallucination
    case, and an evidential system should fall back to the honest blurry
    read rather than the confident invented one. If you want the SR read
    to win disagreements, that should be a conscious change here with a
    comment explaining why, not a default.
    """
    if sr_read is None:
        return baseline_read
    if baseline_read is None:
        return sr_read
    base_text, base_conf = baseline_read
    sr_text, sr_conf = sr_read
    if base_text == sr_text:
        return (base_text, max(float(base_conf), float(sr_conf)))
    logger.debug("SR/baseline disagreement (%r vs %r) — keeping baseline", base_text, sr_text)
    return baseline_read


def status() -> dict:
    """For /health, so a missing contrib wheel or a bad model path is
    visible without reading logs."""
    return {
        "vehicle_roi_enabled": LOWRES_VEHICLE_ROI_ENABLED,
        "deskew_enabled": LOWRES_DESKEW_ENABLED,
        "super_resolution_enabled": LOWRES_SR_ENABLED,
        "super_resolution_available": LOWRES_SR_ENABLED and _get_sr_model() is not None,
        "super_resolution_error": _sr_unavailable_reason,
    }
