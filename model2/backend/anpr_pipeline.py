"""
anpr_pipeline.py
Plate detection (YOLO) + OCR against a recorded clip or a registered
source, per the spec: "Script/endpoint running plate detection
(YOLO-based) + OCR against a recorded clip; logs detections to Postgres."

These are HEAVY dependencies (ultralytics/easyocr/paddleocr all pull in a
deep-learning runtime). They are imported lazily inside functions, not at
module load time, so:
  - the rest of the app (sources registry, feed relay, events API) works
    perfectly even if none of them are installed
  - only calling the ANPR endpoints requires them, and you get one clear
    error message telling you what to install, instead of the whole app
    failing to start.

This module intentionally does frame-level work only (detect -> crop ->
OCR -> validate -> return results). The job orchestration (looping a
video, writing to Postgres, updating job status) lives in routers/anpr.py
so this file stays easy to unit-test on its own.

--- Detector backend ----------------------------------------------------
ANPR_DETECTOR_BACKEND (env var, default "yolo") selects which detector
finds plate bounding boxes. Supported: "yolo", "indian_lpr_fcos".

  "yolo" (default): ultralytics.YOLO(ANPR_YOLO_WEIGHTS) — see below.

  "indian_lpr_fcos": the FCOS+HRNet detector vendored from
  sanchit2843/Indian_LPR (package indian_lpr_fcos/ alongside this file).
  Point ANPR_FCOS_WEIGHTS at that repo's best_od.pth. This is a
  DIFFERENT architecture from YOLO (anchor-free, HRNet backbone) — it is
  not a drop-in .pt swap, which is why it's a separate backend rather
  than another ANPR_YOLO_WEIGHTS value. Both backends feed into the same
  OCR + Indian-format-validation step below, so switching backends only
  changes how boxes are found, not how plate text is read/validated.

--- Detector weights (ANPR_DETECTOR_BACKEND=yolo) ------------------------
ANPR_YOLO_WEIGHTS (env var, default "yolov8n.pt") is passed straight to
ultralytics.YOLO(...) — swap the .pt file, no code change needed. The
default is stock COCO weights (proves the pipeline wiring end-to-end but
does NOT know what a license plate looks like). For real plate
detection, point this at an actual plate-detector checkpoint, e.g.
"keremberke/yolov8n-license-plate" downloaded from Hugging Face, or your
own weights fine-tuned on an Indian-plate dataset.

--- OCR backend ---------------------------------------------------------
ANPR_OCR_BACKEND (env var, default "easyocr") selects which OCR engine
reads text out of the cropped plate. Supported: "easyocr", "paddleocr",
"lprnet". PaddleOCR tends to read small, high-contrast alphanumeric
crops (like plates) more reliably than EasyOCR, which is tuned for
general scene text — worth trying if EasyOCR's plate reads are noisy.
Both are fully open-source (Apache-2.0), no paid API involved.

"lprnet" is the OCR model from sanchit2843/Indian_LPR (package
lprnet_ocr/ alongside this file) — trained specifically on Indian plate
crops, unlike EasyOCR/PaddleOCR's general scene-text training. Point
ANPR_LPRNET_WEIGHTS at that repo's best_lprnet.pth (or your own weights
trained the same way). It plugs into the same crop -> OCR ->
Indian-format-validation flow below as the other two backends; only
_read_plate_text()'s dispatch changes.

--- Plate-format validation ---------------------------------------------
ANPR_VALIDATE_INDIAN_FORMAT (env var, default "true") runs OCR output
through a regex for the standard Indian plate format
(e.g. "MH12AB1234") before accepting it as a detection. This is cheap and
catches a lot of OCR garbage regardless of which detector/OCR backend is
in use. Set to "false" to disable if you need to log everything OCR
returns (e.g. while debugging a new detector).

--- CCTV accuracy tweaks (new) -------------------------------------------
Real CCTV footage is a much harder case than the close-up, well-lit plate
photos most detectors/OCR engines are implicitly tuned on: plates are
tiny, low-contrast, motion-blurred, and often clipped too tightly by the
detector's own bounding box. The knobs below exist to claw back accuracy
on that kind of footage without touching the detector/OCR weights
themselves:

  ANPR_CROP_PADDING_RATIO (default "0.15") — expands each detected box by
  this fraction of its width/height before cropping. A box that's even a
  few pixels too tight can clip the first/last character clean off,
  which no amount of OCR quality can then recover.

  ANPR_OCR_UPSCALE_TARGET_HEIGHT (default "64") — crops shorter than this
  (in px) are upscaled up to it before OCR. A 20px-tall CCTV plate crop
  simply doesn't contain enough pixels-per-character for any OCR model to
  read reliably; upscaling doesn't invent detail, but it does put the
  crop back in the size range these OCR engines were actually trained on.

  ANPR_PLATE_ASPECT_MIN / ANPR_PLATE_ASPECT_MAX (default "0.8" / "6.0") —
  sanity-check on the detected box's width/height ratio before ever
  paying for an OCR call on it. A real plate (single or two-line) falls
  in a fairly predictable aspect-ratio band; a box far outside it is
  almost always a false-positive from a general-purpose/under-trained
  detector (e.g. a chunk of bumper or a whole vehicle), not OCR noise.

  ANPR_OCR_ALLOWLIST (default "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") —
  restricts EasyOCR's character set to exactly what a plate can contain,
  which measurably cuts misreads (EasyOCR's general scene-text model
  otherwise happily "reads" punctuation/lowercase/etc. out of noise).

  ANPR_CORRECT_OCR_CONFUSIONS (default "true") — if a raw OCR read fails
  the Indian-format regex, tries swapping classic OCR lookalikes
  (0/O, 1/I, 8/B, 5/S, ...) one character at a time and re-checks the
  format after each swap, keeping the first swap that validates. Same
  "one glyph is probably wrong, not the whole read" assumption
  routers/anpr.py's dedup logic (_plates_match) already relies on.

  ANPR_OCR_GPU (default "false") — the crop-ensemble below runs OCR
  twice per detection (see _ocr_candidates), which roughly doubles OCR
  cost per frame; set this true if a CUDA GPU is available to absorb
  that instead of it slowing down frame throughput.

  ANPR_DEBUG_OCR (default "false") — logs every raw OCR read (text +
  confidence + crop size + bbox) BEFORE the format filter drops it, at
  DEBUG level. Turn this on any time detections feel wrong — it tells
  you immediately whether OCR is reading close (one bad character,
  silently discarded) or reading complete garbage (a crop/detector
  problem, not an OCR-engine problem), which need very different fixes.

--- Wide-angle / mixed-resolution CCTV tweaks (new) ----------------------
Real deployments mix camera types: some are close, well-lit boom-gate
cams; many are wide, elevated junction/yard cams (uneven exposure, a
blown-out light source on one side of frame and deep shadow on the
other, plates that are a small fraction of the frame, a burned-in OSD
timestamp strip). The knobs below target that second case specifically
and only cost anything when the footage actually needs them.

  ANPR_OSD_MASK_REGIONS (default "0.0,0.0,1.0,0.045") — semicolon-
  separated list of "x1,y1,x2,y2" rectangles, each given as a FRACTION
  of frame width/height (not raw pixels), blacked out before detection
  only. Fractional so the same config masks the same physical strip of
  the image regardless of whether a given camera streams at 480p, 720p,
  or 4K — see "most footage would not be 1080" in the design notes. The
  default masks a thin strip across the top of the frame, where a
  burned-in timestamp/camera-name overlay commonly sits; that kind of
  high-contrast alphanumeric text is exactly the shape a general-purpose
  detector can mistake for a plate. Set to "" to disable, or add more
  rectangles (e.g. a bottom-right corner label) per camera.

  ANPR_FRAME_CLAHE_ENABLED (default "true") — runs CLAHE (contrast-
  limited adaptive histogram equalization) on the whole frame's
  luminance channel before detection, so a harsh backlight/dusk mix
  (bright gate light on one side, dark shadowed yard on the other)
  doesn't leave the detector confident in the lit half of the frame and
  blind in the shadowed half. This is separate from, and runs before,
  the per-crop CLAHE pass in _preprocess_crop_for_ocr, which still
  handles the OCR-specific enhancement of the final plate crop.
  ANPR_FRAME_CLAHE_CLIP_LIMIT / ANPR_FRAME_CLAHE_TILE_GRID (defaults
  "2.0" / "8") tune its strength/locality.

  ANPR_TILING_ENABLED (default "false") — splits the frame into an
  overlapping grid and runs the detector on each tile separately instead
  of once on the whole frame, merging results back with NMS. Why this
  matters: a detector resizes its input to a fixed size internally
  (often 640x640) regardless of source resolution, so a plate that's a
  few dozen pixels wide in a wide-angle shot gets crushed to a handful
  of pixels before the detector ever sees it — no amount of post-crop
  upscaling recovers detail that was never handed to the detector in the
  first place. Tiling gives each tile the detector's full internal
  resolution budget instead of splitting it across the whole scene. Off
  by default because it multiplies detector calls per frame (cols*rows,
  plus one full-frame pass) — real cost, worth paying only on footage
  where small/distant plates are actually the problem.
  ANPR_TILE_GRID (default "2x2") — cols x rows.
  ANPR_TILE_OVERLAP_RATIO (default "0.2") — each tile is expanded by
  this fraction of its own size so a plate sitting near a tile boundary
  still lands whole inside at least one tile.
  ANPR_TILE_MIN_FRAME_WIDTH (default "1600") — tiling only engages above
  this frame width; tiling a small/already-close-up frame just adds cost
  for no benefit.
  ANPR_TILE_NMS_IOU (default "0.3") — IoU threshold used to de-duplicate
  the same plate detected in more than one tile's overlap region.
  ANPR_TILE_INCLUDE_FULL_FRAME_PASS (default "true") — also runs one
  full-frame detection pass alongside the tiles, catching large/near
  plates that a tile boundary might otherwise clip awkwardly even with
  overlap.
"""
import logging
import os
import re
from typing import List, NamedTuple, Optional

import cv2
import numpy as np

# Same fix, same reasoning as camera_worker.py's identical line: OpenCV's
# ffmpeg backend only reads RTSP transport config from the process
# environment, at the moment a VideoCapture is opened — there's no
# per-call API for it. routers/anpr.py opens its OWN VideoCapture,
# independent of camera_worker.py's, so THAT module having set this
# env var does nothing here unless camera_worker.py happened to be
# imported first in this process. Repeating the (idempotent) setdefault
# here guarantees it's set before anpr.py's capture opens too, regardless
# of import order. Without this, OpenCV defaults to UDP, and dropped
# packets show up as exactly the ffmpeg errors this pipeline exists to
# avoid: "mmco: unref short failure", "co located POCs unavailable",
# "error while decoding MB ..." — missing reference pictures cascading
# into corrupted decodes.
os.environ.setdefault(
    "OPENCV_FFMPEG_CAPTURE_OPTIONS",
    "rtsp_transport;tcp|stimeout;20000000|max_delay;500000|buffer_size;1048576",
)

ANPR_DETECTOR_BACKEND = os.getenv("ANPR_DETECTOR_BACKEND", "yolo").strip().lower()
ANPR_YOLO_WEIGHTS = os.getenv("ANPR_YOLO_WEIGHTS", "yolov8n.pt")
ANPR_OCR_BACKEND = os.getenv("ANPR_OCR_BACKEND", "easyocr").strip().lower()
ANPR_VALIDATE_INDIAN_FORMAT = os.getenv("ANPR_VALIDATE_INDIAN_FORMAT", "true").strip().lower() in (
    "1", "true", "yes",
)
ANPR_MIN_DETECTION_CONFIDENCE = float(os.getenv("ANPR_MIN_DETECTION_CONFIDENCE", "0.4"))

# --- CCTV accuracy tweaks (see module docstring for the "why" on each) ---
ANPR_CROP_PADDING_RATIO = float(os.getenv("ANPR_CROP_PADDING_RATIO", "0.15"))
ANPR_OCR_UPSCALE_TARGET_HEIGHT = int(os.getenv("ANPR_OCR_UPSCALE_TARGET_HEIGHT", "64"))
ANPR_PLATE_ASPECT_MIN = float(os.getenv("ANPR_PLATE_ASPECT_MIN", "0.8"))
ANPR_PLATE_ASPECT_MAX = float(os.getenv("ANPR_PLATE_ASPECT_MAX", "6.0"))
ANPR_OCR_ALLOWLIST = os.getenv("ANPR_OCR_ALLOWLIST", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
ANPR_CORRECT_OCR_CONFUSIONS = os.getenv("ANPR_CORRECT_OCR_CONFUSIONS", "true").strip().lower() in (
    "1", "true", "yes",
)
ANPR_OCR_GPU = os.getenv("ANPR_OCR_GPU", "false").strip().lower() in ("1", "true", "yes")
ANPR_DEBUG_OCR = os.getenv("ANPR_DEBUG_OCR", "false").strip().lower() in ("1", "true", "yes")

# --- Wide-angle / mixed-resolution CCTV tweaks (see module docstring) ---
ANPR_OSD_MASK_REGIONS = os.getenv("ANPR_OSD_MASK_REGIONS", "0.0,0.0,1.0,0.045")

ANPR_FRAME_CLAHE_ENABLED = os.getenv("ANPR_FRAME_CLAHE_ENABLED", "true").strip().lower() in (
    "1", "true", "yes",
)
ANPR_FRAME_CLAHE_CLIP_LIMIT = float(os.getenv("ANPR_FRAME_CLAHE_CLIP_LIMIT", "2.0"))
ANPR_FRAME_CLAHE_TILE_GRID = int(os.getenv("ANPR_FRAME_CLAHE_TILE_GRID", "8"))

ANPR_TILING_ENABLED = os.getenv("ANPR_TILING_ENABLED", "false").strip().lower() in (
    "1", "true", "yes",
)
ANPR_TILE_GRID = os.getenv("ANPR_TILE_GRID", "2x2")
ANPR_TILE_OVERLAP_RATIO = float(os.getenv("ANPR_TILE_OVERLAP_RATIO", "0.2"))
ANPR_TILE_MIN_FRAME_WIDTH = int(os.getenv("ANPR_TILE_MIN_FRAME_WIDTH", "1600"))
ANPR_TILE_NMS_IOU = float(os.getenv("ANPR_TILE_NMS_IOU", "0.3"))
ANPR_TILE_INCLUDE_FULL_FRAME_PASS = os.getenv(
    "ANPR_TILE_INCLUDE_FULL_FRAME_PASS", "true"
).strip().lower() in ("1", "true", "yes")

_SUPPORTED_DETECTOR_BACKENDS = ("yolo", "indian_lpr_fcos")
_SUPPORTED_OCR_BACKENDS = ("easyocr", "paddleocr", "lprnet")

logger = logging.getLogger("cctv_model2.anpr_pipeline")

_yolo_model = None
_ocr_reader = None  # EasyOCR reader instance
_paddle_ocr = None  # PaddleOCR instance
# lprnet_ocr.reader keeps its own model singleton (mirrors _yolo_model /
# _ocr_reader / _paddle_ocr above), since its loader also needs a device
# handle alongside the model — see lprnet_ocr/reader.py's _get_model().

# Standard Indian plate formats this regex accepts, after OCR text has
# been upper-cased and stripped of everything but letters/digits:
#   MH12AB1234   - standard private/commercial: 2 letters, 1-2 digits,
#                  1-2 letters, 4 digits
#   MH12A1234    - older single-letter series
#   DL1CAB1234   - Delhi's leading-digit format (e.g. "DL1C...")
# This is deliberately permissive (it will accept some strings that
# aren't real plates) rather than strict, since OCR crops are noisy and
# a false-negative here silently throws away a real detection with no
# way to recover it later - a false positive just gets a wrong-looking
# plate_text row, which a human reviewing flagged events can still spot.
_INDIAN_PLATE_RE = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$")

# OCR-ambiguous character pairs, keyed by the direction of the swap.
# Used only to retry a FAILED format check (see _try_correct_plate_format)
# — never applied to a read that already validates.
_DIGIT_LOOKALIKE = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}
_LETTER_LOOKALIKE = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G"}


class PlateDetection(NamedTuple):
    plate_text: str
    confidence: float
    bbox: tuple  # (x1, y1, x2, y2) in the source frame


def looks_like_indian_plate(cleaned_text: str) -> bool:
    """Cheap sanity check on OCR output against standard Indian plate
    formats. Exposed as a standalone function so routers/tests can reuse
    the same rule without re-running OCR."""
    return bool(_INDIAN_PLATE_RE.match(cleaned_text))


def _try_correct_plate_format(cleaned: str) -> Optional[str]:
    """If `cleaned` doesn't match the Indian plate regex outright, tries
    swapping OCR-ambiguous characters (0/O, 1/I, 8/B, ...) one position
    at a time and returns the first corrected string that validates.
    Returns `cleaned` unchanged if it already validates, or None if
    nothing (unmodified or one swap away) matches.

    Deliberately only tries ONE substitution per candidate — same
    "one glyph is probably wrong, not the whole read" assumption
    routers/anpr.py's dedup logic (_plates_match) already makes for
    repeat reads of a dwelling vehicle. Trying every combination of
    swaps would start accepting reads that are barely related to what
    OCR actually saw.
    """
    if looks_like_indian_plate(cleaned):
        return cleaned
    if not ANPR_CORRECT_OCR_CONFUSIONS:
        return None
    # Standard format is 9-10 characters (2 letters + 1-2 digits + 1-3
    # letters + 4 digits). A read far outside that range is missing/extra
    # characters, not a single misread glyph — no single swap fixes that,
    # so don't waste time trying.
    if not (8 <= len(cleaned) <= 10):
        return None

    for i, ch in enumerate(cleaned):
        for lookup in (_DIGIT_LOOKALIKE, _LETTER_LOOKALIKE):
            replacement = lookup.get(ch)
            if replacement is None:
                continue
            candidate = cleaned[:i] + replacement + cleaned[i + 1:]
            if looks_like_indian_plate(candidate):
                return candidate
    return None


def _pad_bbox(x1: int, y1: int, x2: int, y2: int, frame_w: int, frame_h: int, ratio: float) -> tuple:
    """Expands a detected box by `ratio` of its own width/height, clipped
    to the frame. A tightly-fit box from a general-purpose/under-trained
    detector very often clips a character right at the edge; a real plate
    detector trained with box-augmentation is less prone to this, but the
    padding costs nothing when it isn't needed and recovers a lot when it
    is."""
    w, h = x2 - x1, y2 - y1
    pad_x = int(w * ratio)
    pad_y = int(h * ratio)
    return (
        max(x1 - pad_x, 0),
        max(y1 - pad_y, 0),
        min(x2 + pad_x, frame_w),
        min(y2 + pad_y, frame_h),
    )


def _parse_mask_regions(spec: str, frame_w: int, frame_h: int) -> List[tuple]:
    """Parses ANPR_OSD_MASK_REGIONS into pixel rects sized for THIS
    frame. Regions are configured as fractions of width/height, not raw
    pixels, specifically because source cameras don't all deliver the
    same resolution — a fractional "top 4.5% of the frame" masks the
    same physical strip whether the source is 480p or 4K; a fixed pixel
    offset would not."""
    regions: List[tuple] = []
    if not spec:
        return regions
    for chunk in spec.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            fx1, fy1, fx2, fy2 = (float(v) for v in chunk.split(","))
        except ValueError:
            logger.warning("Skipping malformed ANPR_OSD_MASK_REGIONS entry: %r", chunk)
            continue
        x1 = max(int(fx1 * frame_w), 0)
        y1 = max(int(fy1 * frame_h), 0)
        x2 = min(int(fx2 * frame_w), frame_w)
        y2 = min(int(fy2 * frame_h), frame_h)
        if x2 > x1 and y2 > y1:
            regions.append((x1, y1, x2, y2))
    return regions


def _apply_osd_mask(frame: "np.ndarray") -> "np.ndarray":
    """Blacks out configured OSD regions (burned-in timestamp/camera-name
    overlays) before detection only. That kind of high-contrast
    alphanumeric strip is exactly the shape a general-purpose detector
    can mistake for a plate. Returns a copy — the caller's frame is left
    untouched, since cropping/OCR downstream still read from the
    original, unmasked frame."""
    h, w = frame.shape[:2]
    regions = _parse_mask_regions(ANPR_OSD_MASK_REGIONS, w, h)
    if not regions:
        return frame
    masked = frame.copy()
    for x1, y1, x2, y2 in regions:
        masked[y1:y2, x1:x2] = 0
    return masked


def _apply_frame_clahe(frame: "np.ndarray") -> "np.ndarray":
    """Full-frame exposure normalization, for detection only (see
    _prepare_detection_frame). CLAHE on the L channel of LAB keeps colour
    intact while pulling shadow/highlight detail back into a usable
    range — a wide, elevated CCTV shot with a blown-out light on one
    side and a dark shadowed area on the other can otherwise leave a
    detector confident in the lit half of the frame and blind in the
    shadowed half. This is a coarser, frame-scale pass; the plate crop
    itself still gets its own separately-tuned CLAHE pass in
    _preprocess_crop_for_ocr for OCR specifically."""
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=ANPR_FRAME_CLAHE_CLIP_LIMIT,
        tileGridSize=(ANPR_FRAME_CLAHE_TILE_GRID, ANPR_FRAME_CLAHE_TILE_GRID),
    )
    l_enhanced = clahe.apply(l_channel)
    enhanced = cv2.merge((l_enhanced, a_channel, b_channel))
    return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)


def _prepare_detection_frame(frame: "np.ndarray") -> "np.ndarray":
    """Returns a frame prepared for the DETECTOR only — exposure-
    normalized, then OSD-masked. Neither step changes frame geometry or
    resolution, so bounding boxes found against this prepared copy still
    map directly onto the original `frame` unchanged. The original frame
    is what cropping/OCR use downstream; this function's output never
    reaches OCR.

    Order matters here: CLAHE must run BEFORE masking, not after.
    CLAHE's local contrast stretch operates per grid-tile across the
    whole image — if the OSD region were already blacked out first,
    CLAHE could still partially brighten it back up by redistributing
    the tile's histogram (the masked pixels are zero, but the tile
    around them isn't). Masking last guarantees the region stays blank
    regardless of what CLAHE did to it."""
    prepped = frame
    if ANPR_FRAME_CLAHE_ENABLED:
        prepped = _apply_frame_clahe(prepped)
    if ANPR_OSD_MASK_REGIONS:
        prepped = _apply_osd_mask(prepped)
    return prepped


def _parse_tile_grid(spec: str) -> tuple:
    try:
        cols_s, rows_s = spec.lower().split("x")
        cols, rows = int(cols_s), int(rows_s)
        if cols < 1 or rows < 1:
            raise ValueError
        return cols, rows
    except (ValueError, AttributeError):
        logger.warning("Invalid ANPR_TILE_GRID=%r, falling back to 2x2", spec)
        return 2, 2


def _make_tiles(frame_w: int, frame_h: int, cols: int, rows: int, overlap_ratio: float) -> List[tuple]:
    """Returns (x1, y1, x2, y2) pixel rects covering the frame in a
    cols x rows grid, each expanded by overlap_ratio of its own size so
    a plate sitting near a tile boundary still lands whole inside at
    least one tile — a plate cut exactly at a seam is unreadable in
    either half."""
    tile_w = frame_w / cols
    tile_h = frame_h / rows
    ov_x = tile_w * overlap_ratio
    ov_y = tile_h * overlap_ratio

    tiles = []
    for r in range(rows):
        for c in range(cols):
            x1 = max(int(c * tile_w - ov_x), 0)
            y1 = max(int(r * tile_h - ov_y), 0)
            x2 = min(int((c + 1) * tile_w + ov_x), frame_w)
            y2 = min(int((r + 1) * tile_h + ov_y), frame_h)
            tiles.append((x1, y1, x2, y2))
    return tiles


def _nms_merge_boxes(boxes: List[tuple], iou_threshold: float) -> List[tuple]:
    """De-duplicates (x1, y1, x2, y2, conf) boxes coming back from
    multiple overlapping tiles (and optionally a full-frame pass) via
    standard NMS. Without this, a plate sitting in a tile's overlap
    region gets detected — and OCR'd, and logged — twice."""
    if not boxes:
        return []
    rects = [[x1, y1, x2 - x1, y2 - y1] for x1, y1, x2, y2, _ in boxes]
    scores = [conf for *_, conf in boxes]
    indices = cv2.dnn.NMSBoxes(rects, scores, score_threshold=0.0, nms_threshold=iou_threshold)
    if indices is None or len(indices) == 0:
        return []
    flat_indices = np.array(indices).flatten()
    return [boxes[i] for i in flat_indices]


def _detect_boxes_with_tiling(frame: "np.ndarray", min_confidence: float, detect_fn) -> List[tuple]:
    """Runs `detect_fn` (either _detect_boxes_yolo or
    _detect_boxes_indian_lpr_fcos) tiled across the frame instead of once
    on the whole thing, then merges results back to full-frame
    coordinates with NMS.

    Why this matters for wide/elevated CCTV shots specifically: a
    detector resizes its input to a fixed size internally (commonly
    640x640) regardless of source resolution. A plate that's a few dozen
    pixels wide in a wide-angle frame gets crushed to a handful of
    pixels by that internal resize before the detector ever sees it — no
    amount of post-crop upscaling recovers detail that was never handed
    to the detector in the first place. Tiling gives each tile the
    detector's full internal resolution budget instead of splitting it
    across the whole scene.

    Only engages when ANPR_TILING_ENABLED and above
    ANPR_TILE_MIN_FRAME_WIDTH — it multiplies detector calls per frame
    (cols*rows, plus one optional full-frame pass), real cost worth
    paying only where the resolution/distance actually warrants it.
    """
    frame_h, frame_w = frame.shape[:2]
    if not ANPR_TILING_ENABLED or frame_w < ANPR_TILE_MIN_FRAME_WIDTH:
        return detect_fn(frame, min_confidence)

    cols, rows = _parse_tile_grid(ANPR_TILE_GRID)
    tiles = _make_tiles(frame_w, frame_h, cols, rows, ANPR_TILE_OVERLAP_RATIO)

    all_boxes: List[tuple] = []
    for (tx1, ty1, tx2, ty2) in tiles:
        tile_img = frame[ty1:ty2, tx1:tx2]
        if tile_img.size == 0:
            continue
        for (bx1, by1, bx2, by2, conf) in detect_fn(tile_img, min_confidence):
            all_boxes.append((bx1 + tx1, by1 + ty1, bx2 + tx1, by2 + ty1, conf))

    if ANPR_TILE_INCLUDE_FULL_FRAME_PASS:
        # Catches large/near plates that a tile boundary might otherwise
        # clip awkwardly even with overlap, at the cost of one more call.
        all_boxes.extend(detect_fn(frame, min_confidence))

    return _nms_merge_boxes(all_boxes, ANPR_TILE_NMS_IOU)


def _preprocess_crop_for_ocr(crop: "np.ndarray") -> "np.ndarray":
    """Upscales + contrast-enhances a plate crop before OCR. Returns a
    single-channel (grayscale) image; every OCR backend used here accepts
    grayscale input directly.

    CCTV plate crops are frequently tiny (30-60px wide), low-contrast,
    and compressed/motion-blurred — no OCR model can read detail that
    isn't in the source pixels, so this buys back what it practically
    can before the crop ever reaches an OCR engine.
    """
    h, w = crop.shape[:2]
    if h == 0 or w == 0:
        return crop

    # Upscale toward the target height (never shrinks an already-large
    # crop — that would throw away real detail for no benefit).
    scale = max(1.0, ANPR_OCR_UPSCALE_TARGET_HEIGHT / h)
    if scale > 1.0:
        crop = cv2.resize(crop, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop

    # CLAHE (local, not global, histogram equalization): pulls out
    # character/background contrast in under/over-exposed regions
    # without blowing out parts of the crop that were already fine —
    # very common on CCTV plates lit by headlights or angled sun.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Denoise before sharpening, not after — sharpening a noisy/
    # compressed CCTV frame amplifies compression artifacts into fake
    # "edges" just as readily as it sharpens real character edges.
    denoised = cv2.bilateralFilter(enhanced, d=5, sigmaColor=50, sigmaSpace=50)

    # Unsharp mask: cheap, standard sharpen that tends to help thin
    # plate-font strokes read cleaner after the blur denoising above.
    blurred = cv2.GaussianBlur(denoised, (0, 0), sigmaX=3)
    sharpened = cv2.addWeighted(denoised, 1.5, blurred, -0.5, 0)

    return sharpened


def _binarize_for_ocr(gray: "np.ndarray") -> "np.ndarray":
    """Otsu binarization on top of the already-enhanced grayscale crop.
    Not always better than the plain enhanced version, which is exactly
    why it's tried as a second candidate rather than a replacement — see
    _ocr_candidates()."""
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh


def _ocr_candidates(crop: "np.ndarray") -> List["np.ndarray"]:
    """Builds the ordered list of preprocessed crop variants to try OCR
    against. No single preprocessing wins on every plate: the enhanced
    grayscale crop is usually enough, but a hard Otsu binarization
    sometimes reads better on very low-contrast or glare-heavy CCTV
    crops where CLAHE alone doesn't fully separate characters from
    background. Both EasyOCR/PaddleOCR callers below run OCR against
    every candidate and keep whichever gave the highest confidence."""
    enhanced = _preprocess_crop_for_ocr(crop)
    binarized = _binarize_for_ocr(enhanced)
    return [enhanced, binarized]


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

        if ANPR_YOLO_WEIGHTS == "yolov8n.pt":
            logging.getLogger("cctv_model2.anpr").warning(
                "ANPR_YOLO_WEIGHTS is still the stock 'yolov8n.pt' (general "
                "COCO object detector) — it does NOT know what a license "
                "plate looks like. Detections will draw boxes around whole "
                "vehicles/people/objects and OCR whatever text happens to "
                "fall inside that box, which will NOT match real plates. "
                "Point ANPR_YOLO_WEIGHTS at an actual plate-detector "
                "checkpoint before trusting any results. See README "
                "'ANPR pipeline setup'."
            )

        _yolo_model = YOLO(ANPR_YOLO_WEIGHTS)
    return _yolo_model


def _get_easyocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        try:
            import easyocr
        except ImportError as exc:
            raise RuntimeError(
                "ANPR_OCR_BACKEND=easyocr requires 'easyocr'. Install with:\n"
                "  pip install easyocr==1.7.2\n"
                "See README 'ANPR pipeline setup' for details."
            ) from exc
        _ocr_reader = easyocr.Reader(["en"], gpu=ANPR_OCR_GPU)
    return _ocr_reader


def _get_paddle_ocr():
    global _paddle_ocr
    if _paddle_ocr is None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError(
                "ANPR_OCR_BACKEND=paddleocr requires 'paddleocr' (and paddlepaddle). Install with:\n"
                "  pip install paddlepaddle==2.6.1 paddleocr==2.7.3\n"
                "See README 'ANPR pipeline setup' for details."
            ) from exc
        # use_angle_cls=False: plate crops are already roughly upright once
        # cropped from a YOLO box, so skip the rotation classifier for speed.
        _paddle_ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)
    return _paddle_ocr


def _read_text_easyocr(crop: "np.ndarray") -> Optional[tuple]:
    """Returns (raw_text, ocr_confidence) or None if nothing was read.

    Tries OCR against every preprocessed variant from _ocr_candidates()
    and keeps whichever read came back with the highest confidence —
    CCTV crops vary enough (glare, blur, exposure) that no single
    preprocessing wins every time. allowlist restricts EasyOCR's output
    to exactly the plate character set, which on its own noticeably cuts
    down on stray punctuation/lowercase misreads from a model trained on
    general scene text.
    """
    reader = _get_easyocr_reader()
    best: Optional[tuple] = None
    for candidate in _ocr_candidates(crop):
        ocr_results = reader.readtext(candidate, allowlist=ANPR_OCR_ALLOWLIST)
        if not ocr_results:
            continue
        # Take the highest-confidence text region found inside this candidate.
        _, text, ocr_conf = max(ocr_results, key=lambda r: r[2])
        ocr_conf = float(ocr_conf)
        if best is None or ocr_conf > best[1]:
            best = (text, ocr_conf)
    return best


def _read_text_paddleocr(crop: "np.ndarray") -> Optional[tuple]:
    """Returns (raw_text, ocr_confidence) or None if nothing was read.
    Same multi-candidate approach as _read_text_easyocr — see there for
    why. PaddleOCR has no direct allowlist equivalent, so filtering to
    the plate character set still happens where it already did, in
    detect_plates_in_frame's cleaned = "".join(... isalnum() ...) step."""
    ocr = _get_paddle_ocr()
    best: Optional[tuple] = None
    for candidate in _ocr_candidates(crop):
        result = ocr.ocr(candidate, cls=False)
        # PaddleOCR returns a list-per-image of [ [box, (text, conf)], ... ] —
        # or [None] when nothing was found on that image.
        lines = result[0] if result else None
        if not lines:
            continue
        _, (text, ocr_conf) = max(lines, key=lambda line: line[1][1])
        ocr_conf = float(ocr_conf)
        if best is None or ocr_conf > best[1]:
            best = (text, ocr_conf)
    return best


def _read_text_lprnet(crop: "np.ndarray") -> Optional[tuple]:
    """Returns (raw_text, ocr_confidence) or None if nothing was read.

    Deliberately does NOT run this crop through _preprocess_crop_for_ocr
    first — lprnet_ocr/reader.py already applies its own fixed
    preprocessing (94x24 resize + the exact normalization it was trained
    with), and feeding it a differently-enhanced image risks moving it
    off the distribution its weights actually learned. The padding
    applied to the bbox before cropping (see detect_plates_in_frame)
    still helps here the same as any other backend.
    """
    from lprnet_ocr import reader as lprnet_reader

    return lprnet_reader.read_plate_text(crop)


def _read_plate_text(crop: "np.ndarray") -> Optional[tuple]:
    if ANPR_OCR_BACKEND == "paddleocr":
        return _read_text_paddleocr(crop)
    if ANPR_OCR_BACKEND == "lprnet":
        return _read_text_lprnet(crop)
    return _read_text_easyocr(crop)


def is_available() -> tuple[bool, Optional[str]]:
    """Checks whether ANPR deps are installed without raising — used by the
    API to return a clean 503 instead of a stack trace when they're missing."""
    if ANPR_DETECTOR_BACKEND not in _SUPPORTED_DETECTOR_BACKENDS:
        return False, (
            f"ANPR_DETECTOR_BACKEND='{ANPR_DETECTOR_BACKEND}' is not supported "
            f"(expected one of {_SUPPORTED_DETECTOR_BACKENDS})"
        )
    if ANPR_OCR_BACKEND not in _SUPPORTED_OCR_BACKENDS:
        return False, (
            f"ANPR_OCR_BACKEND='{ANPR_OCR_BACKEND}' is not supported "
            f"(expected one of {_SUPPORTED_OCR_BACKENDS})"
        )

    if ANPR_DETECTOR_BACKEND == "indian_lpr_fcos":
        from indian_lpr_fcos import detector as fcos_detector
        available, reason = fcos_detector.is_available()
        if not available:
            return False, reason
    else:
        try:
            import ultralytics  # noqa: F401
        except ImportError as exc:
            return False, str(exc)

    if ANPR_OCR_BACKEND == "lprnet":
        from lprnet_ocr import reader as lprnet_reader

        available, reason = lprnet_reader.is_available()
        if not available:
            return False, reason
    else:
        try:
            if ANPR_OCR_BACKEND == "paddleocr":
                import paddleocr  # noqa: F401
            else:
                import easyocr  # noqa: F401
        except ImportError as exc:
            return False, str(exc)
    return True, None


def _detect_boxes_yolo(frame: "np.ndarray", min_confidence: float) -> List[tuple]:
    """Returns a list of (x1, y1, x2, y2, detector_confidence) tuples.

    NOTE: a stock `yolov8n.pt` (general-object weights) will NOT reliably
    detect license plates — it's a general COCO-classes model, used here
    only to prove the wiring works end-to-end. For real plate detection,
    point ANPR_YOLO_WEIGHTS at weights fine-tuned on a license-plate
    dataset (several open ones exist; swap the .pt file, no code change
    needed).
    """
    model = _get_yolo_model()
    results = model(frame, verbose=False)

    boxes_out = []
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
            boxes_out.append((x1, y1, x2, y2, conf))
    return boxes_out


def _detect_boxes_indian_lpr_fcos(frame: "np.ndarray", min_confidence: float) -> List[tuple]:
    """Returns a list of (x1, y1, x2, y2, detector_confidence) tuples,
    using the vendored FCOS+HRNet detector. See
    indian_lpr_fcos/detector.py for the preprocessing assumptions this
    relies on (BGR-vs-RGB, no resize) — they matter for accuracy and
    can't be verified without the actual best_od.pth weights in hand."""
    from indian_lpr_fcos import detector as fcos_detector

    raw_detections = fcos_detector.detect_plate_boxes(frame, min_confidence=min_confidence)
    return [(x1, y1, x2, y2, score) for (x1, y1, x2, y2), score in raw_detections]


def detect_plates_in_frame(
    frame: "np.ndarray", min_confidence: float = ANPR_MIN_DETECTION_CONFIDENCE
) -> List[PlateDetection]:
    """
    Runs plate detection + OCR on a single BGR frame (as read by OpenCV).
    Which detector finds the boxes is controlled by
    ANPR_DETECTOR_BACKEND ("yolo" or "indian_lpr_fcos") — both feed into
    the same crop -> OCR -> Indian-format-validation step below.

    Detection itself runs against a PREPARED copy of the frame — OSD
    regions masked out and full-frame exposure normalized (see
    _prepare_detection_frame) — and, when ANPR_TILING_ENABLED, tiled
    across the frame for small/distant plates (see
    _detect_boxes_with_tiling). Neither step changes frame geometry, so
    the returned bbox coordinates still map directly onto the ORIGINAL
    `frame` passed in: cropping and OCR below deliberately keep reading
    from `frame`, not the prepared copy, since the crop-level OCR
    preprocessing in _preprocess_crop_for_ocr is already separately
    tuned and shouldn't have frame-level CLAHE stacked underneath it.
    """
    detection_frame = _prepare_detection_frame(frame)

    if ANPR_DETECTOR_BACKEND == "indian_lpr_fcos":
        detect_fn = _detect_boxes_indian_lpr_fcos
    else:
        detect_fn = _detect_boxes_yolo

    raw_boxes = _detect_boxes_with_tiling(detection_frame, min_confidence, detect_fn)

    frame_h, frame_w = frame.shape[:2]

    detections: List[PlateDetection] = []
    for x1, y1, x2, y2, conf in raw_boxes:
        box_w, box_h = x2 - x1, y2 - y1
        if box_w <= 0 or box_h <= 0:
            continue

        # Sanity-check the box shape before ever paying for an OCR call
        # on it — a box far outside a plausible plate aspect ratio is
        # almost always a false-positive (e.g. a chunk of bumper or a
        # whole vehicle from an under-trained/general detector), not
        # something OCR could salvage.
        aspect = box_w / box_h
        if not (ANPR_PLATE_ASPECT_MIN <= aspect <= ANPR_PLATE_ASPECT_MAX):
            if ANPR_DEBUG_OCR:
                logger.debug(
                    "Skipping box with aspect ratio %.2f outside [%s, %s]: bbox=%s conf=%.3f",
                    aspect, ANPR_PLATE_ASPECT_MIN, ANPR_PLATE_ASPECT_MAX, (x1, y1, x2, y2), conf,
                )
            continue

        px1, py1, px2, py2 = _pad_bbox(x1, y1, x2, y2, frame_w, frame_h, ANPR_CROP_PADDING_RATIO)
        crop = frame[py1:py2, px1:px2]
        if crop.size == 0:
            continue

        ocr_result = _read_plate_text(crop)
        if ocr_result is None:
            continue
        text, ocr_conf = ocr_result
        cleaned = "".join(ch for ch in text if ch.isalnum()).upper()
        if not cleaned:
            continue

        if ANPR_DEBUG_OCR:
            logger.debug(
                "Raw OCR read %r (ocr_conf=%.3f) from %dx%d crop at bbox=%s (padded=%s)",
                cleaned, ocr_conf, crop.shape[1], crop.shape[0], (x1, y1, x2, y2), (px1, py1, px2, py2),
            )

        if ANPR_VALIDATE_INDIAN_FORMAT:
            corrected = _try_correct_plate_format(cleaned)
            if corrected is None:
                if ANPR_DEBUG_OCR:
                    logger.debug("Read %r did not match Indian plate format even after correction attempt", cleaned)
                continue
            cleaned = corrected

        # OCR backends can return numpy.float32/float64 for confidence.
        # Cast both operands to plain Python float before combining, so
        # combined_conf is never a numpy scalar — psycopg2's adaptation
        # of numpy floats has been unreliable across numpy versions and
        # can end up embedding the value's repr() as literal SQL text
        # instead of a bound parameter (seen as errors like
        # 'schema "np" does not exist').
        combined_conf = float(round((float(conf) + float(ocr_conf)) / 2, 4))
        detections.append(
            PlateDetection(plate_text=cleaned, confidence=combined_conf, bbox=(x1, y1, x2, y2))
        )

    return detections
