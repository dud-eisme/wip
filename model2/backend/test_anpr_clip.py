"""
test_anpr_clip.py
Standalone accuracy-debugging tool: runs anpr_pipeline.py's
detect_plates_in_frame() against a single video file/URL directly — no
FastAPI, no Postgres, no job/worker threading — and prints every result
straight to the console.

Use this to iterate on ANPR_* env vars (crop padding, upscale target,
OCR backend, aspect-ratio filter, ...) fast: change one, rerun, see the
difference in a few seconds — instead of going through
POST /api/v2/anpr/jobs -> poll job status -> GET /api/v2/anpr/events
every time you want to check whether a tweak actually helped.

Run from the project root (same directory as anpr_pipeline.py), so the
bare `import anpr_pipeline` below resolves:

    venv\\Scripts\\activate
    python test_anpr_clip.py path\\to\\clip.mp4
    python test_anpr_clip.py path\\to\\clip.mp4 --frame-skip 3 --max-frames 200
    python test_anpr_clip.py path\\to\\clip.mp4 --show-all --save-crops out\\crops
    python test_anpr_clip.py path\\to\\clip.mp4 --ocr-backend paddleocr

Any ANPR_* env var already set in your shell/.env (ANPR_DETECTOR_BACKEND,
ANPR_YOLO_WEIGHTS, ANPR_CROP_PADDING_RATIO, etc.) is picked up exactly as
the real app would use it — this script doesn't change how the pipeline
behaves, only how visible its output is and where the input comes from.

--show-all bypasses ANPR_VALIDATE_INDIAN_FORMAT for THIS RUN ONLY (the
override is applied via os.environ before anpr_pipeline is imported and
never touches your actual .env) so every OCR read is counted and printed
— including ones that fail the plate-format regex. Use this to tell
"OCR read close but got filtered by the regex" apart from "OCR read
complete garbage" — very different problems, very different fixes.

--save-crops <dir> saves the exact (unpadded-vs-original) crop region
for every logged read, named by frame number + plate text + bbox — so
you can SEE whether the crop is even legible before doubting the OCR
engine itself. Open a few of these before changing anything else.

Debug logging (ANPR_DEBUG_OCR) is force-enabled for every run of this
script, regardless of your .env — the whole point of this tool is
maximum visibility. It logs every raw OCR read (text, confidence, crop
size, bbox) BEFORE the format filter, so even reads that fail
--show-all-less validation still show up in the log lines below the
summary table.
"""
import argparse
import logging
import os
import sys
import time
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Debug the ANPR pipeline against a single clip, outside the full app."
    )
    parser.add_argument(
        "source",
        help="Path to a video file, or an RTSP/HTTP URL — same as a registered source's source_url.",
    )
    parser.add_argument(
        "--frame-skip", type=int, default=int(os.getenv("ANPR_FRAME_SKIP", "5")),
        help="Process every Nth frame (default: matches ANPR_FRAME_SKIP, else 5).",
    )
    parser.add_argument(
        "--max-frames", type=int, default=300,
        help="Stop after processing (not reading) this many frames (default: 300).",
    )
    parser.add_argument(
        "--min-confidence", type=float, default=None,
        help="Override the detector confidence threshold for this run only.",
    )
    parser.add_argument(
        "--show-all", action="store_true",
        help="Bypass ANPR_VALIDATE_INDIAN_FORMAT for this run so every OCR read is "
             "counted/printed, not just ones matching the plate-format regex.",
    )
    parser.add_argument(
        "--save-crops", metavar="DIR", default=None,
        help="Save the crop region for every logged read into this directory.",
    )
    parser.add_argument(
        "--detector-backend", default=None,
        help="Override ANPR_DETECTOR_BACKEND for this run only (yolo | indian_lpr_fcos).",
    )
    parser.add_argument(
        "--ocr-backend", default=None,
        help="Override ANPR_OCR_BACKEND for this run only (easyocr | paddleocr | lprnet).",
    )
    parser.add_argument(
        "--filter-corrupted", action="store_true",
        help="Skip frames that score as corrupted by the blockiness heuristic in "
             "frame_quality.py (see that file — this is for RTSP sources with "
             "network/bandwidth-induced decode corruption) instead of running "
             "detection on them. Off by default: this tool's whole point is maximum "
             "visibility, and corrupted frames are themselves often exactly what "
             "you're here to look at. Skipped frames and their scores are still "
             "reported in the summary either way.",
    )
    return parser.parse_args()


def _apply_env_overrides(args):
    """ANPR_* env vars are read once, at anpr_pipeline import time — so
    every override here has to happen BEFORE that import, further down
    in main(), or it's silently ignored."""
    os.environ["ANPR_DEBUG_OCR"] = "true"
    if args.show_all:
        os.environ["ANPR_VALIDATE_INDIAN_FORMAT"] = "false"
    if args.detector_backend:
        os.environ["ANPR_DETECTOR_BACKEND"] = args.detector_backend
    if args.ocr_backend:
        os.environ["ANPR_OCR_BACKEND"] = args.ocr_backend


def main():
    args = parse_args()
    _apply_env_overrides(args)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet down noisy third-party loggers so ANPR's own debug lines
    # aren't buried under model-loading/framework chatter.
    for noisy_logger in ("PIL", "easyocr", "urllib3", "matplotlib", "filelock"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Imported only now, after the env overrides above have been applied —
    # anpr_pipeline reads ANPR_* env vars into module-level constants at
    # import time.
    import cv2
    import anpr_pipeline
    import frame_quality

    save_dir = None
    if args.save_crops:
        save_dir = Path(args.save_crops)
        save_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(args.source)
    if not cap.isOpened():
        print(f"Could not open source: {args.source}", file=sys.stderr)
        sys.exit(1)

    min_confidence = (
        args.min_confidence if args.min_confidence is not None else anpr_pipeline.ANPR_MIN_DETECTION_CONFIDENCE
    )

    print(f"--- Running ANPR pipeline against: {args.source} ---")
    print(
        f"detector={anpr_pipeline.ANPR_DETECTOR_BACKEND}  ocr={anpr_pipeline.ANPR_OCR_BACKEND}  "
        f"validate_format={anpr_pipeline.ANPR_VALIDATE_INDIAN_FORMAT}  min_confidence={min_confidence}"
    )
    print(
        f"padding={anpr_pipeline.ANPR_CROP_PADDING_RATIO}  "
        f"upscale_target_h={anpr_pipeline.ANPR_OCR_UPSCALE_TARGET_HEIGHT}  "
        f"aspect=[{anpr_pipeline.ANPR_PLATE_ASPECT_MIN}, {anpr_pipeline.ANPR_PLATE_ASPECT_MAX}]  "
        f"correct_confusions={anpr_pipeline.ANPR_CORRECT_OCR_CONFUSIONS}"
    )
    print("-" * 78)

    frame_number = 0
    processed = 0
    total_detections = 0
    corrupted_skipped = 0
    start = time.monotonic()

    while processed < args.max_frames:
        ok, frame = cap.read()
        if not ok:
            print("(end of clip)")
            break
        frame_number += 1
        if frame_number % max(args.frame_skip, 1) != 0:
            continue

        corrupted, blockiness = frame_quality.is_frame_corrupted(frame)
        if corrupted:
            print(f"frame={frame_number:<6} SKIPPED (corrupted, blockiness={blockiness:.2f})"
                  f"{' — filtered out' if args.filter_corrupted else ' — filter off, processing anyway'}")
            corrupted_skipped += 1
            if args.filter_corrupted:
                continue

        detections = anpr_pipeline.detect_plates_in_frame(frame, min_confidence=min_confidence)
        processed += 1

        for det in detections:
            total_detections += 1
            print(f"frame={frame_number:<6} plate={det.plate_text:<12} confidence={det.confidence:.3f} bbox={det.bbox}")

            if save_dir is not None:
                x1, y1, x2, y2 = det.bbox
                crop = frame[y1:y2, x1:x2]
                if crop.size:
                    out_path = save_dir / f"frame{frame_number}_{det.plate_text}_{x1}-{y1}-{x2}-{y2}.jpg"
                    cv2.imwrite(str(out_path), crop)

    elapsed = time.monotonic() - start
    cap.release()

    print("-" * 78)
    print(f"Processed {processed} frames ({frame_number} read total) in {elapsed:.1f}s — {total_detections} detection(s) logged.")
    if corrupted_skipped:
        print(f"Frames flagged as corrupted by blockiness heuristic: {corrupted_skipped}"
              f"{' (filtered out of detection)' if args.filter_corrupted else ' (still processed — pass --filter-corrupted to skip them)'}")
    if save_dir is not None:
        print(f"Crops saved to: {save_dir.resolve()}")
    if total_detections == 0:
        print(
            "No detections at all — check the DEBUG log lines above for "
            "'Skipping box with aspect ratio ...' (detector IS firing, just "
            "filtered) versus no such lines at all (detector isn't firing on "
            "this footage — try --min-confidence 0.1 to see raw detector "
            "output before OCR/validation even run)."
        )


if __name__ == "__main__":
    main()
