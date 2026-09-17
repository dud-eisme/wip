"""
lprnet_ocr/reader.py

Wraps the LPRNet OCR model from sanchit2843/Indian_LPR
(https://github.com/sanchit2843/Indian_LPR) for use as
ANPR_OCR_BACKEND=lprnet in anpr_pipeline.py — an alternative to
EasyOCR/PaddleOCR for reading text out of a cropped plate. Unlike those
two (general scene-text OCR), LPRNet was trained specifically on
Indian plate crops, at the cost of needing Indian_LPR-compatible
weights rather than a generic pretrained checkpoint.

model.py in this package is LPRNet.py vendored verbatim from that repo
(the small_basic_block CNN + CTC-style output head, via build_lprnet()).
This module adapts it to anpr_pipeline.py's OCR-backend interface:

    is_available() -> (bool, Optional[str])
    read_plate_text(crop_bgr) -> Optional[(raw_text, confidence)]

CHARS below, the (94, 24) resize, and the preprocessing/decode logic are
copied from data/load_data.py's LPRDataLoader.transform and
test_LPRNet.py's Greedy_Decode_inference — NOT re-imported from those
files, so this backend doesn't pull in albumentations/imutils/nltk/
sklearn, which load_data.py and test_LPRNet.py need for
training/evaluation but inference doesn't.

--- Weights ---------------------------------------------------------
ANPR_LPRNET_WEIGHTS (env var, default "best_lprnet.pth") should point at
Indian_LPR's trained LPRNet checkpoint (weights/best_lprnet.pth in their
layout), or your own weights trained against the same CHARS alphabet
and build_lprnet() architecture vendored here.
"""
import os
from typing import Optional, Tuple

import numpy as np

# Same alphabet/order as data/load_data.py's CHARS — must match whatever
# alphabet ANPR_LPRNET_WEIGHTS was trained with, since class indices are
# positional.
CHARS = [
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N",
    "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
    "-",
]
_BLANK_INDEX = len(CHARS) - 1

ANPR_LPRNET_WEIGHTS = os.getenv("ANPR_LPRNET_WEIGHTS", "best_lprnet.pth")
ANPR_LPRNET_MAX_LEN = int(os.getenv("ANPR_LPRNET_MAX_LEN", "18"))
_IMG_SIZE = (94, 24)  # (w, h) — the repo trains/tests at this fixed size

_model = None
_device = None


def is_available() -> Tuple[bool, Optional[str]]:
    """Checks whether this backend can actually be used, without raising —
    mirrors anpr_pipeline.py's own is_available() pattern."""
    try:
        import torch  # noqa: F401
    except ImportError:
        return False, (
            "ANPR_OCR_BACKEND=lprnet requires 'torch'. Install with:\n"
            "  pip install torch"
        )
    if not os.path.exists(ANPR_LPRNET_WEIGHTS):
        return False, (
            f"ANPR_LPRNET_WEIGHTS='{ANPR_LPRNET_WEIGHTS}' was not found on "
            "disk. Point it at Indian_LPR's best_lprnet.pth (or your own "
            "LPRNet checkpoint)."
        )
    return True, None


def _get_model():
    global _model, _device
    if _model is None:
        import torch
        from .model import build_lprnet

        _device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        net = build_lprnet(
            lpr_max_len=ANPR_LPRNET_MAX_LEN,
            phase=False,
            class_num=len(CHARS),
            dropout_rate=0,
        )
        net.load_state_dict(torch.load(ANPR_LPRNET_WEIGHTS, map_location=_device))
        net.to(_device)
        net.eval()
        _model = net
    return _model


def _preprocess(crop_bgr: "np.ndarray") -> "np.ndarray":
    """Exactly mirrors LPRDataLoader.transform in data/load_data.py —
    accuracy depends on the crop being normalized the same way the
    weights were trained on."""
    import cv2

    img = cv2.resize(crop_bgr, _IMG_SIZE)
    img = img.astype("float32")
    img -= 127.5
    img *= 0.0078125
    return np.transpose(img, (2, 0, 1))


def _greedy_decode(class_probs: "np.ndarray") -> str:
    """Port of test_LPRNet.py's Greedy_Decode_inference for one sample.
    class_probs is [class_num, T] (softmax probabilities, not logits)."""
    raw_labels = [int(np.argmax(class_probs[:, t])) for t in range(class_probs.shape[1])]

    no_repeat_blank_labels = []
    pre_c = raw_labels[0]
    if pre_c != _BLANK_INDEX:
        no_repeat_blank_labels.append(pre_c)
    for c in raw_labels:
        if pre_c == c or c == _BLANK_INDEX:
            if c == _BLANK_INDEX:
                pre_c = c
            continue
        no_repeat_blank_labels.append(c)
        pre_c = c

    return "".join(CHARS[i] for i in no_repeat_blank_labels)


def read_plate_text(crop_bgr: "np.ndarray") -> Optional[Tuple[str, float]]:
    """Returns (raw_text, confidence) or None on an empty crop.

    NOTE ON CONFIDENCE: LPRNet's CTC-style greedy decode has no built-in
    per-read confidence the way EasyOCR/PaddleOCR return one. As a
    stand-in, this uses the mean of the top softmax probability at each
    decoded (non-blank, non-repeat) time-step — same 0-1 range, so
    anpr_pipeline.py's `combined_conf = (detector_conf + ocr_conf) / 2`
    still behaves sensibly regardless of which OCR backend is active.
    """
    import torch
    import torch.nn.functional as F

    if crop_bgr.size == 0:
        return None

    net = _get_model()
    img = _preprocess(crop_bgr)
    tensor = torch.from_numpy(img).unsqueeze(0).to(_device)

    with torch.no_grad():
        logits = net(tensor)  # [1, class_num, T]
        probs = F.softmax(logits, dim=1)[0].cpu().numpy()  # [class_num, T]

    text = _greedy_decode(probs)
    if not text:
        return None

    confidence = float(np.mean(np.max(probs, axis=0)))
    return text, confidence
