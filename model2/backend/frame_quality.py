"""
frame_quality.py
Detects a garbled/corrupted decoded frame WITHOUT any reference to what
the frame "should" look like — necessary because ffmpeg's own decode-
error log lines (the "error while decoding MB ...", "mmco: unref short
failure" console spam) aren't exposed through OpenCV's Python API at all;
they go straight to C-level stderr, with no per-frame, per-source hook to
catch. This module works from the decoded pixels instead.

WHY BLOCKINESS: H.264 has no error concealment in ffmpeg's default decode
path. When a needed reference is missing (a late/dropped packet under
network jitter, or a bandwidth shortfall the source can't sustain), the
decoder fills in whatever it has — smeared/frozen/garbage macroblocks —
and it does so on the CODEC'S OWN 16x16 macroblock grid. That produces
sharp, perfectly grid-aligned discontinuities that healthy video content
essentially never produces at that regularity (real image edges don't
line up with an arbitrary 16px grid). Comparing pixel discontinuity AT
that grid to discontinuity elsewhere in the frame catches this without
needing to know anything about the actual scene.

THIS IS A HEURISTIC, NOT A CERTAINTY. Two known failure modes, both
expected:
  - False positives: heavily-compressed low-bitrate footage (dark/night
    scenes especially) has some genuine macroblock-grid visibility from
    ordinary quantization, not corruption. A too-low threshold will
    reject some legitimately fine frames.
  - False negatives: not every corruption pattern is blocky (e.g. a
    single skipped B-frame can just look like motion judder). This will
    miss those.
The default FRAME_BLOCKINESS_THRESHOLD below is a starting point, not a
calibrated value — it has not been tuned against any real footage from
this deployment. Tune it by watching `last_blockiness_score` on
GET /api/v2/sources/status/workers: let it run against both a known-good
stretch and a known-corrupted stretch of the actual feed, and set the
threshold between the two observed score ranges. Different cameras /
bitrates may warrant different thresholds; this is currently one global
setting, not per-source.

NOTE: this whole module is a detect-and-reject heuristic for RTSP's
push-delivery corruption specifically (see camera_worker.py's module
docstring). It is not applied to HLS sources at all (only the `is_rtsp`
branch in CameraWorker.run() calls into this module) because HLS's
HTTP/TCP segment delivery avoids the corruption mechanism this module
detects at the transport level, rather than needing it caught after
decode. If a source keeps triggering false positives here after
threshold tuning, switching that source to HLS (source_type='hls') is
worth trying before spending more time calibrating this heuristic.
"""
import os
from typing import Optional

import cv2
import numpy as np

# Matches H.264's macroblock size — this is what makes the metric
# meaningful; it is NOT a generic "chunkiness" tuning knob.
FRAME_QUALITY_BLOCK_SIZE = int(os.getenv("FRAME_QUALITY_BLOCK_SIZE", "16"))

# Ratio of boundary-vs-interior pixel discontinuity above which a frame
# is treated as corrupted. See the module docstring — this NEEDS
# real-world calibration, this default is only a starting point.
FRAME_BLOCKINESS_THRESHOLD = float(os.getenv("FRAME_BLOCKINESS_THRESHOLD", "3.0"))

# Master switch — sources known to be clean (local FILE clips) skip this
# entirely rather than pay the per-frame CPU cost for nothing.
FRAME_QUALITY_CHECK_ENABLED = os.getenv("FRAME_QUALITY_CHECK_ENABLED", "true").strip().lower() == "true"


def _to_grayscale_float(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame
    return gray.astype(np.float32)


def blockiness_score(frame: np.ndarray, block_size: int = FRAME_QUALITY_BLOCK_SIZE) -> float:
    """Returns the ratio of average pixel discontinuity AT the codec's
    block-grid boundaries vs discontinuity INSIDE blocks, averaged across
    the horizontal and vertical directions. ~1.0 means no special
    boundary effect (healthy). Higher means stronger grid-aligned seams
    (more likely corrupted). No hard ceiling — a badly garbled frame can
    score well into double digits.
    """
    gray = _to_grayscale_float(frame)

    # Horizontal: difference between each column and the next.
    col_diff = np.abs(np.diff(gray, axis=1))  # shape (h, w-1)
    col_idx = np.arange(col_diff.shape[1])
    is_h_boundary = (col_idx + 1) % block_size == 0
    if is_h_boundary.any() and (~is_h_boundary).any():
        h_boundary = float(col_diff[:, is_h_boundary].mean())
        h_interior = float(col_diff[:, ~is_h_boundary].mean())
    else:
        h_boundary = h_interior = 0.0

    # Vertical: difference between each row and the next.
    row_diff = np.abs(np.diff(gray, axis=0))  # shape (h-1, w)
    row_idx = np.arange(row_diff.shape[0])
    is_v_boundary = (row_idx + 1) % block_size == 0
    if is_v_boundary.any() and (~is_v_boundary).any():
        v_boundary = float(row_diff[is_v_boundary, :].mean())
        v_interior = float(row_diff[~is_v_boundary, :].mean())
    else:
        v_boundary = v_interior = 0.0

    eps = 1e-6
    h_score = h_boundary / (h_interior + eps)
    v_score = v_boundary / (v_interior + eps)
    return (h_score + v_score) / 2.0


def is_frame_corrupted(
    frame: Optional[np.ndarray],
    threshold: float = FRAME_BLOCKINESS_THRESHOLD,
    block_size: int = FRAME_QUALITY_BLOCK_SIZE,
) -> tuple:
    """Returns (corrupted: bool, score: float). A frame that can't be
    scored at all (None, empty, too small for one block) is treated as
    corrupted for None/empty (nothing to publish), but a scoring
    exception fails OPEN (treated as fine, score=0.0) — a bug in this
    heuristic should never be able to blank out an otherwise-working
    feed."""
    if frame is None or frame.size == 0:
        return True, 0.0
    try:
        score = blockiness_score(frame, block_size=block_size)
    except Exception:
        return False, 0.0
    return score > threshold, score
