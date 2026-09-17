"""
indian_lpr_fcos/detector.py

Wraps the FCOS+HRNet plate detector from sanchit2843/Indian_LPR
(https://github.com/sanchit2843/Indian_LPR) for use as
ANPR_DETECTOR_BACKEND=indian_lpr_fcos in anpr_pipeline.py.

fcos.py / fpn.py / head.py / loss.py / config.py / backbone/hrnet.py in
this package are vendored from that repo (backbone/hrnet.py has one
compatibility fix — an `np.int` call that numpy>=1.24 removed — see the
comment at that line). This module is the piece that adapts them to
anpr_pipeline.py's expected interface:

    is_available() -> (bool, Optional[str])
    detect_plate_boxes(frame_bgr, min_confidence) -> [((x1,y1,x2,y2), score), ...]

hrnetv2()'s forward pass returns 4 feature maps at 16/32/64/128
channels, matching fpn.py's prj_2..prj_5 exactly, so no shape-adapter
code is needed between the two — is_available() below just confirms
torch/torchvision are installed and the weights file exists.

--- Weights ---------------------------------------------------------
ANPR_FCOS_WEIGHTS (env var, default "best_od.pth") should point at the
Indian_LPR repo's trained FCOS+HRNet checkpoint (weights/best_od.pth in
their layout), or your own weights trained with the same
fcos.py/fpn.py/head.py architecture vendored here.
"""
import os
from typing import List, Optional, Tuple

ANPR_FCOS_WEIGHTS = os.getenv("ANPR_FCOS_WEIGHTS", "best_od.pth")
ANPR_FCOS_SCORE_THRESHOLD = float(os.getenv("ANPR_FCOS_SCORE_THRESHOLD", "0.3"))
ANPR_FCOS_NMS_IOU = float(os.getenv("ANPR_FCOS_NMS_IOU", "0.2"))

_model = None
_device = None


def is_available() -> Tuple[bool, Optional[str]]:
    """Checks whether this backend can actually be used, without raising —
    mirrors anpr_pipeline.py's own is_available() pattern so the API can
    return a clean 503 instead of a stack trace."""
    try:
        import torch  # noqa: F401
    except ImportError:
        return False, (
            "ANPR_DETECTOR_BACKEND=indian_lpr_fcos requires 'torch' and "
            "'torchvision'. Install with:\n  pip install torch torchvision"
        )
    try:
        from .backbone.hrnet import hrnetv2  # noqa: F401
    except ImportError as exc:
        return False, (
            "indian_lpr_fcos backend's HRNet backbone "
            "(indian_lpr_fcos/backbone/hrnet.py, exporting hrnetv2()) "
            f"failed to import: {exc}"
        )
    if not os.path.exists(ANPR_FCOS_WEIGHTS):
        return False, (
            f"ANPR_FCOS_WEIGHTS='{ANPR_FCOS_WEIGHTS}' was not found on disk. "
            "Point it at Indian_LPR's best_od.pth (or your own FCOS+HRNet "
            "checkpoint)."
        )
    return True, None


def _get_model():
    global _model, _device
    if _model is None:
        import torch
        from .fcos import FCOSDetector
        from .config import DefaultConfig

        class _InferenceConfig(DefaultConfig):
            score_threshold = ANPR_FCOS_SCORE_THRESHOLD
            nms_iou_threshold = ANPR_FCOS_NMS_IOU

        _device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        model = FCOSDetector(mode="inference", config=_InferenceConfig)
        state_dict = torch.load(ANPR_FCOS_WEIGHTS, map_location=_device)
        model.load_state_dict(state_dict)
        model = model.eval().to(_device)
        _model = model
    return _model


def _preprocess(frame_bgr):
    """Mirrors demo_video.py's preprocess_image: BGR->RGB, ToTensor,
    ImageNet mean/std normalize, add batch dim. Detection accuracy
    depends on matching this exactly."""
    import cv2
    from torchvision import transforms

    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    tensor = transforms.ToTensor()(rgb)
    tensor = transforms.Normalize(
        mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
    )(tensor)
    return tensor.unsqueeze(0)


def detect_plate_boxes(
    frame_bgr, min_confidence: float = 0.4
) -> List[Tuple[Tuple[int, int, int, int], float]]:
    """Runs the FCOS detector on one BGR frame (as read by OpenCV).

    Returns [((x1, y1, x2, y2), score), ...] in the source frame's pixel
    coordinates — the exact shape anpr_pipeline.py's
    _detect_boxes_indian_lpr_fcos() already expects and unpacks.
    """
    import torch

    model = _get_model()
    image = _preprocess(frame_bgr).to(_device)

    with torch.no_grad():
        scores, _classes, boxes = model(image)

    scores = scores[0].cpu().numpy().tolist()
    boxes = boxes[0].cpu().numpy().tolist()

    results: List[Tuple[Tuple[int, int, int, int], float]] = []
    for score, box in zip(scores, boxes):
        if score < min_confidence:
            continue
        x1, y1, x2, y2 = (int(v) for v in box)
        x1, y1 = max(x1, 0), max(y1, 0)
        if x2 <= x1 or y2 <= y1:
            continue
        results.append(((x1, y1, x2, y2), float(score)))
    return results
