"""
plate_consensus.py
Multi-frame plate tracking + per-character consensus voting.

THIS IS THE SINGLE BIGGEST ACCURACY WIN AVAILABLE ON LOW-RESOLUTION
FOOTAGE, and it is not a preprocessing trick — it is a change in what
counts as "a detection".

Why: anpr_pipeline.py's single-frame path is already close to the ceiling
of what can be squeezed out of one bad frame (padding, frame CLAHE, crop
CLAHE, denoise, unsharp, Otsu ensemble, upscaling, tiling, allowlist,
confusion correction). Stacking more filters on one 28px-tall crop has
sharply diminishing returns, because the information simply is not in
those pixels.

But a vehicle does not appear in one frame. At 25fps it is in shot for
20-60 frames, and each frame is a slightly different sample: different
sub-pixel alignment, different motion blur direction, different
compression noise, different glare angle. The OCR errors across those
frames are largely INDEPENDENT. Independent errors average out. A
character read correctly in 7 of 11 frames wins its position even though
no single frame read the whole plate correctly.

Concretely, this turns:

    frame 112  MH12AB1Z34   (conf 0.51)
    frame 117  NH12AB1234   (conf 0.44)
    frame 122  MH12A81234   (conf 0.62)
    frame 127  MH12AB1234   (conf 0.58)
    frame 132  MH12AB123A   (conf 0.47)

into one event: MH12AB1234, with a per-character agreement score that is
far more informative than any individual read's confidence.

--- What this changes downstream ---------------------------------------
routers/anpr.py currently writes one AnprEvent per accepted read and
de-duplicates afterwards with _plates_match. With this module, the unit
becomes the TRACK, not the read: events are emitted when a track ENDS
(the vehicle leaves frame, or the track goes stale). That is strictly
better than post-hoc dedup — dedup picks one read and discards the rest,
whereas voting uses all of them as evidence — but it does mean an event
appears a second or two after the vehicle first enters, not instantly.
For a live "of interest" alert that latency matters, so emit_partial()
exists to read out the current best guess of a still-open track without
closing it.

--- Tracking approach ---------------------------------------------------
Deliberately IoU-based, not a Kalman/DeepSORT tracker. At the frame rates
ANPR actually processes (every ANPR_FRAME_SKIP-th frame, so effectively
5-8fps) a plate moves a long way between processed frames, so the
motion-model advantage of a proper tracker is small, while its cost and
dependency weight are not. Two things carry most of the load instead:
a generous IoU threshold, and text similarity — two reads that are one
character apart are very likely the same plate even if the boxes barely
overlap, which handles the fast-mover case a pure IoU tracker drops.
"""
import logging
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, NamedTuple, Optional, Tuple

logger = logging.getLogger("cctv_model2.plate_consensus")

# IoU between a new plate box and a track's last box above which they are
# treated as the same plate. Lower than a typical tracker's 0.5 because
# consecutive PROCESSED frames are several real frames apart.
CONSENSUS_IOU_THRESHOLD = float(os.getenv("CONSENSUS_IOU_THRESHOLD", "0.25"))

# Fallback match: if IoU is too low but the texts differ by at most this
# many characters (and lengths match), treat as the same plate anyway.
CONSENSUS_MAX_TEXT_DISTANCE = int(os.getenv("CONSENSUS_MAX_TEXT_DISTANCE", "2"))

# A track with no new reads for this many PROCESSED frames is closed and
# emitted. Too low fragments one vehicle into several events; too high
# merges two different vehicles passing the same spot.
CONSENSUS_TRACK_TIMEOUT_FRAMES = int(os.getenv("CONSENSUS_TRACK_TIMEOUT_FRAMES", "12"))

# Minimum reads before a track is allowed to produce an event at all. A
# plate seen exactly once is precisely the case where OCR is most likely
# to be hallucinating on noise, and it is also the case consensus can do
# nothing for. Raising this trades recall for precision directly.
CONSENSUS_MIN_READS = int(os.getenv("CONSENSUS_MIN_READS", "3"))

# Per-character agreement below which the whole result is rejected. 0.5
# means the winning character must have more than half the weighted vote
# at every position.
CONSENSUS_MIN_AGREEMENT = float(os.getenv("CONSENSUS_MIN_AGREEMENT", "0.5"))

# Cap on stored reads per track — a vehicle stopped at a red light can
# otherwise accumulate thousands, and the first ~40 already carry
# essentially all the information the vote will use.
CONSENSUS_MAX_READS_PER_TRACK = int(os.getenv("CONSENSUS_MAX_READS_PER_TRACK", "40"))


class ConsensusPlate(NamedTuple):
    """The output unit: one plate, one vehicle pass, many reads."""
    plate_text: str
    confidence: float             # mean confidence of the reads that voted
    agreement: float              # weakest per-character agreement, 0-1
    read_count: int
    first_frame: int
    last_frame: int
    best_bbox: tuple              # bbox of the highest-confidence single read
    best_frame: int               # frame number of that read — use it for the snapshot
    alternatives: List[str]       # other whole-string reads seen, most common first


@dataclass
class _Read:
    text: str
    confidence: float
    bbox: tuple
    frame_number: int


@dataclass
class PlateTrack:
    track_id: int
    reads: List[_Read] = field(default_factory=list)
    last_bbox: tuple = (0, 0, 0, 0)
    first_frame: int = 0
    last_frame: int = 0
    last_seen_at: float = field(default_factory=time.time)
    emitted: bool = False

    def add(self, read: _Read) -> None:
        if len(self.reads) < CONSENSUS_MAX_READS_PER_TRACK:
            self.reads.append(read)
        self.last_bbox = read.bbox
        self.last_frame = read.frame_number
        self.last_seen_at = time.time()


def _iou(a: tuple, b: tuple) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    area_a = max(ax2 - ax1, 0) * max(ay2 - ay1, 0)
    area_b = max(bx2 - bx1, 0) * max(by2 - by1, 0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _hamming(a: str, b: str) -> int:
    """Same-length character distance. Deliberately NOT full Levenshtein:
    an insertion/deletion means OCR found a different number of
    characters, which is a different kind of failure from a misread glyph
    and shouldn't be smoothed over into a match."""
    if len(a) != len(b):
        return 10_000
    return sum(1 for x, y in zip(a, b) if x != y)


def vote(reads: List[_Read]) -> Optional[Tuple[str, float, float]]:
    """
    Returns (plate_text, mean_confidence, weakest_agreement) or None.

    Two stages, in this order for a reason:

    1. LENGTH VOTE. Reads of different lengths cannot be compared
       position-by-position — 'MH12AB1234' and 'MH12AB123' disagree at
       every position after the missing character even though they are
       nearly the same read. So the modal length wins first (weighted by
       confidence), and only reads of that length vote on characters.
       Reads of other lengths are not evidence about position; they are
       evidence that segmentation failed on that frame.

    2. PER-CHARACTER VOTE, weighted by each read's confidence. Weighting
       matters: a 0.8-confidence read and a 0.3-confidence read
       disagreeing should not be a coin flip.

    The returned agreement is the WEAKEST position's agreement, not the
    average. One position where the vote was 51/49 makes the whole plate
    doubtful, and averaging would hide exactly that.
    """
    if not reads:
        return None

    by_length: Dict[int, float] = defaultdict(float)
    for read in reads:
        by_length[len(read.text)] += max(read.confidence, 0.01)
    best_length = max(by_length, key=by_length.get)

    voters = [r for r in reads if len(r.text) == best_length]
    if not voters:
        return None

    chars: List[str] = []
    weakest = 1.0
    for position in range(best_length):
        weights: Dict[str, float] = defaultdict(float)
        total = 0.0
        for read in voters:
            weight = max(read.confidence, 0.01)
            weights[read.text[position]] += weight
            total += weight
        if total <= 0:
            return None
        winner = max(weights, key=weights.get)
        agreement = weights[winner] / total
        chars.append(winner)
        weakest = min(weakest, agreement)

    text = "".join(chars)
    mean_conf = sum(r.confidence for r in voters) / len(voters)
    return text, round(float(mean_conf), 4), round(float(weakest), 4)


class PlateTracker:
    """
    Per-source tracker. One instance per ANPR job (NOT a process-wide
    singleton like overlay.latest_detections): two concurrent jobs on
    different cameras must never share track state, and a job's tracks
    are meaningless once it ends.

    Not internally locked, because a job's frame loop is single-threaded
    and is the only writer. If you ever call it from the stream relay
    thread as well, add a lock at that call site.
    """

    def __init__(self):
        self._tracks: Dict[int, PlateTrack] = {}
        self._next_id = 1

    def _match(self, text: str, bbox: tuple) -> Optional[PlateTrack]:
        best: Optional[PlateTrack] = None
        best_score = 0.0
        for track in self._tracks.values():
            if track.emitted:
                continue
            score = _iou(bbox, track.last_bbox)
            if score >= CONSENSUS_IOU_THRESHOLD and score > best_score:
                best, best_score = track, score
        if best is not None:
            return best

        # Text-similarity fallback for the fast-mover case: boxes barely
        # overlap between processed frames, but the read is one or two
        # glyphs off the same track's reads.
        for track in self._tracks.values():
            if track.emitted or not track.reads:
                continue
            recent = track.reads[-1].text
            if _hamming(text, recent) <= CONSENSUS_MAX_TEXT_DISTANCE:
                return track
        return None

    def update(self, frame_number: int, detections: List) -> List[ConsensusPlate]:
        """
        Feed one frame's plate detections (anpr_pipeline.PlateDetection
        objects) in. Returns any tracks that CLOSED on this frame, ready
        to be written as events.

        Call this on every processed frame, including frames with zero
        detections — that is how tracks time out and get emitted.
        """
        for det in detections:
            text = "".join(ch for ch in det.plate_text if ch.isalnum()).upper()
            if not text:
                continue
            bbox = tuple(det.bbox)
            track = self._match(text, bbox)
            if track is None:
                track = PlateTrack(track_id=self._next_id, first_frame=frame_number)
                self._tracks[self._next_id] = track
                self._next_id += 1
            track.add(_Read(text=text, confidence=float(det.confidence), bbox=bbox, frame_number=frame_number))

        closed: List[ConsensusPlate] = []
        for track_id in list(self._tracks):
            track = self._tracks[track_id]
            if frame_number - track.last_frame < CONSENSUS_TRACK_TIMEOUT_FRAMES:
                continue
            result = self._finalise(track)
            if result is not None:
                closed.append(result)
            del self._tracks[track_id]
        return closed

    def flush(self) -> List[ConsensusPlate]:
        """Closes and emits every open track. Call at end of clip / when
        a continuous job is stopped — otherwise the last vehicle in the
        footage never produces an event, which is a surprisingly easy bug
        to ship and a surprisingly annoying one to notice."""
        out = []
        for track in list(self._tracks.values()):
            result = self._finalise(track)
            if result is not None:
                out.append(result)
        self._tracks.clear()
        return out

    def emit_partial(self) -> List[ConsensusPlate]:
        """Current best guess for every OPEN track, without closing them.
        For a live overlay or an early of-interest alert, where waiting
        for the vehicle to leave frame defeats the purpose. Results here
        are provisional and will usually improve before the track closes,
        so don't write them as final events."""
        out = []
        for track in self._tracks.values():
            result = self._finalise(track, enforce_minimums=False)
            if result is not None:
                out.append(result)
        return out

    def _finalise(self, track: PlateTrack, enforce_minimums: bool = True) -> Optional[ConsensusPlate]:
        track.emitted = True
        if not track.reads:
            return None
        if enforce_minimums and len(track.reads) < CONSENSUS_MIN_READS:
            logger.debug(
                "Dropping track %d: only %d read(s), below CONSENSUS_MIN_READS=%d",
                track.track_id, len(track.reads), CONSENSUS_MIN_READS,
            )
            return None

        voted = vote(track.reads)
        if voted is None:
            return None
        text, mean_conf, agreement = voted

        if enforce_minimums and agreement < CONSENSUS_MIN_AGREEMENT:
            logger.debug(
                "Dropping track %d (%s): weakest character agreement %.2f below %.2f",
                track.track_id, text, agreement, CONSENSUS_MIN_AGREEMENT,
            )
            return None

        best_read = max(track.reads, key=lambda r: r.confidence)
        alternatives = [t for t, _ in Counter(r.text for r in track.reads).most_common() if t != text]

        return ConsensusPlate(
            plate_text=text,
            confidence=mean_conf,
            agreement=agreement,
            read_count=len(track.reads),
            first_frame=track.first_frame,
            last_frame=track.last_frame,
            best_bbox=best_read.bbox,
            best_frame=best_read.frame_number,
            alternatives=alternatives[:5],
        )


# ---------------------------------------------------------------------------
# INTEGRATION — routers/anpr.py job loop
# ---------------------------------------------------------------------------
#
#     tracker = plate_consensus.PlateTracker()
#     best_frames = {}          # frame_number -> frame, for snapshot saving
#
#     while ...:
#         ok, frame = cap.read()
#         ...
#         plates = anpr_pipeline.detect_plates_in_frame(frame)
#         best_frames[frame_number] = frame            # keep a bounded window
#         for result in tracker.update(frame_number, plates):
#             write_event(result, best_frames.get(result.best_frame))
#
#     for result in tracker.flush():
#         write_event(result, best_frames.get(result.best_frame))
#
# Two notes on that sketch:
#
#   - best_frames must be BOUNDED (a deque of the last
#     CONSENSUS_TRACK_TIMEOUT_FRAMES + a margin). Holding every frame of a
#     continuous job is an unbounded memory leak, and a continuous job is
#     exactly the mode that runs for hours.
#
#   - store `agreement` and `read_count` on the event row alongside
#     confidence. They are much better review signals than confidence
#     alone: "read 11 times, every character unanimous" and "read 3 times,
#     one position split 55/45" can have identical mean confidence and
#     completely different trustworthiness.
