"""Concealment detection: pure logic on top of pose keypoints.

The pattern we look for, per tracked person and per hand:

    1. REACH    the wrist enters a shelf zone (or, with no zones drawn, the arm is
                extended away from the body) for at least `reach_min_seconds`
    2. CARRY    the wrist leaves the shelf
    3. CONCEAL  within `conceal_window_seconds`, the wrist goes to a pocket, the
                waistband/jacket, or into a bag, and stays there for
                `conceal_dwell_seconds`
    4. RELEASE  the hand leaves the concealment spot (or a timeout passes), and we
                score the sequence

Extra evidence raises the score: a product seen in the hand while carrying, and
that product no longer being in the hand afterwards. This is a heuristic first
pass. Every alert is reviewed by a person, and the Theft / False alarm labels can
later be used to train a proper action-recognition model.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

# COCO-17 keypoint indices
L_SHOULDER, R_SHOULDER = 5, 6
L_WRIST, R_WRIST = 9, 10
L_HIP, R_HIP = 11, 12

KPT_CONF = 0.3

# Waistband alone stays below the default threshold (people hold items at their
# stomach all the time); it alerts when the item is also seen to disappear.
REGION_SCORES = {"bag": 0.75, "pocket": 0.6, "waistband": 0.45}
REGION_TEXT = {
    "bag": "into a bag",
    "pocket": "to a pocket",
    "waistband": "to the waistband / under clothing",
}


@dataclass
class Person:
    track_id: int
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels
    kpts: np.ndarray                         # (17, 3): x, y, conf


@dataclass
class Detection:
    track_id: int
    ts: float          # moment concealment started
    reach_ts: float    # moment the hand reached into the shelf
    score: float
    region: str
    hand: str
    reason: str
    box: tuple[float, float, float, float]


@dataclass
class BodyGeom:
    sw: float                 # shoulder width (px)
    torso: float              # shoulder-to-hip height (px)
    shoulder_y: float
    shoulder_xs: tuple[float, float]
    hip_y: float
    hip_xs: tuple[float, float]


@dataclass
class HandState:
    phase: str = "idle"  # idle | reaching | carrying | concealing
    t_reach: float = 0.0
    t_left: float = 0.0
    t_conceal: float | None = None
    region: str | None = None
    item_seen: bool = False
    used_fallback: bool = False


@dataclass
class TrackState:
    hands: dict = field(default_factory=lambda: {"left": HandState(), "right": HandState()})
    last_seen: float = 0.0
    cooldown_until: float = 0.0


def body_geometry(k: np.ndarray, box) -> BodyGeom | None:
    ls, rs = k[L_SHOULDER], k[R_SHOULDER]
    if ls[2] < KPT_CONF or rs[2] < KPT_CONF:
        return None
    sw = float(np.hypot(*(ls[:2] - rs[:2])))
    sw = max(sw, (box[2] - box[0]) * 0.3, 4.0)
    shoulder_y = float((ls[1] + rs[1]) / 2)
    lh, rh = k[L_HIP], k[R_HIP]
    if lh[2] >= KPT_CONF and rh[2] >= KPT_CONF:
        hip_y = float((lh[1] + rh[1]) / 2)
        hip_xs = (float(lh[0]), float(rh[0]))
    else:  # estimate hips from shoulders
        hip_y = shoulder_y + 1.5 * sw
        hip_xs = (float(ls[0]), float(rs[0]))
    torso = max(hip_y - shoulder_y, 0.8 * sw)
    return BodyGeom(sw, torso, shoulder_y, (float(ls[0]), float(rs[0])), hip_y, hip_xs)


def _in_box(p, box, pad=0.0) -> bool:
    x1, y1, x2, y2 = box
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    return x1 - px <= p[0] <= x2 + px and y1 - py <= p[1] <= y2 + py


def conceal_region(p, g: BodyGeom, bags) -> str | None:
    """Which concealment spot (if any) the wrist point p is at."""
    for b in bags:
        if _in_box(p, b, 0.15):
            return "bag"
    x, y = p
    hx0, hx1 = min(g.hip_xs), max(g.hip_xs)
    if g.hip_y - 0.35 * g.torso <= y <= g.hip_y + 0.2 * g.torso and \
            hx0 - 0.45 * g.sw <= x <= hx1 + 0.45 * g.sw:
        return "pocket"
    tx0 = min(hx0, *g.shoulder_xs)
    tx1 = max(hx1, *g.shoulder_xs)
    if g.shoulder_y + 0.45 * g.torso <= y < g.hip_y - 0.35 * g.torso and tx0 <= x <= tx1:
        return "waistband"
    return None


def arm_extended(p, g: BodyGeom) -> bool:
    """Fallback 'reach' signal when no shelf zones are drawn."""
    dx = min(abs(p[0] - g.shoulder_xs[0]), abs(p[0] - g.shoulder_xs[1]))
    outside = not (min(g.shoulder_xs) <= p[0] <= max(g.shoulder_xs))
    return outside and dx > 0.9 * g.sw and p[1] < g.hip_y


def in_polygons(p, polys) -> bool:
    pt = (float(p[0]), float(p[1]))
    return any(cv2.pointPolygonTest(poly, pt, False) >= 0 for poly in polys)


def item_near(p, items, g: BodyGeom) -> bool:
    r = 0.6 * g.sw
    for b in items:
        if _in_box(p, b, 0.2):
            return True
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        if np.hypot(cx - p[0], cy - p[1]) < r:
            return True
    return False


class ConcealmentDetector:
    def __init__(self, cfg: dict):
        self.reach_min = float(cfg["reach_min_seconds"])
        self.window = float(cfg["conceal_window_seconds"])
        self.dwell = float(cfg["conceal_dwell_seconds"])
        self.threshold = float(cfg["alert_threshold"])
        self.cooldown = float(cfg["person_cooldown_seconds"])
        self.max_conceal = self.dwell + 2.0
        self.tracks: dict[int, TrackState] = {}
        self.below_threshold: list[Detection] = []  # kept for debugging/tuning

    def busy(self) -> bool:
        """True if anyone is mid-sequence, i.e. bag/item detection is worth running."""
        return any(h.phase != "idle" for st in self.tracks.values() for h in st.hands.values())

    def update(self, ts: float, persons: list[Person], items=(), bags=(),
               shelf_polys=(), ignore_polys=()) -> list[Detection]:
        out: list[Detection] = []
        for person in persons:
            if ignore_polys:
                foot = ((person.box[0] + person.box[2]) / 2, person.box[3])
                if in_polygons(foot, ignore_polys):
                    self.tracks.pop(person.track_id, None)
                    continue
            g = body_geometry(person.kpts, person.box)
            if g is None:
                continue
            st = self.tracks.setdefault(person.track_id, TrackState())
            st.last_seen = ts
            for hand, idx in (("left", L_WRIST), ("right", R_WRIST)):
                w = person.kpts[idx]
                if w[2] < KPT_CONF:
                    self._timeout_only(st.hands[hand], ts)
                    continue
                det = self._step(st.hands[hand], hand, (float(w[0]), float(w[1])), ts, g,
                                 items, bags, shelf_polys)
                if det is None:
                    continue
                det.track_id, det.box = person.track_id, person.box
                if det.score >= self.threshold and ts >= st.cooldown_until:
                    st.cooldown_until = ts + self.cooldown
                    out.append(det)
                else:
                    self.below_threshold.append(det)
                    del self.below_threshold[:-50]
        for tid in [t for t, s in self.tracks.items() if ts - s.last_seen > 10]:
            del self.tracks[tid]
        return out

    def _timeout_only(self, h: HandState, ts: float):
        if h.phase == "carrying" and ts - h.t_left > self.window:
            self._reset(h)

    @staticmethod
    def _reset(h: HandState):
        h.phase, h.t_conceal, h.region, h.item_seen, h.used_fallback = "idle", None, None, False, False

    def _step(self, h: HandState, hand, p, ts, g, items, bags, shelf_polys) -> Detection | None:
        if shelf_polys:
            on_shelf, fallback = in_polygons(p, shelf_polys), False
        else:
            on_shelf, fallback = arm_extended(p, g), True

        if h.phase == "concealing":
            region = conceal_region(p, g, bags)
            if region and not on_shelf and ts - h.t_conceal < self.max_conceal:
                return None
            item_after = item_near(p, items, g)
            det = self._score(h, hand, item_after, ts)
            self._reset(h)
            if on_shelf:
                h.phase, h.t_reach, h.used_fallback = "reaching", ts, fallback
            return det

        if on_shelf:
            if h.phase != "reaching":
                self._reset(h)
                h.phase, h.t_reach, h.used_fallback = "reaching", ts, fallback
            return None

        if h.phase == "reaching":
            if ts - h.t_reach >= self.reach_min:
                h.phase, h.t_left = "carrying", ts
            else:
                self._reset(h)
                return None

        if h.phase == "carrying":
            if ts - h.t_left > self.window:
                self._reset(h)
                return None
            if item_near(p, items, g):
                h.item_seen = True
            region = conceal_region(p, g, bags)
            if region is None:
                h.t_conceal, h.region = None, None
            elif h.t_conceal is None or region != h.region:
                h.t_conceal, h.region = ts, region
            elif ts - h.t_conceal >= self.dwell:
                h.phase = "concealing"
        return None

    def _score(self, h: HandState, hand: str, item_after: bool, ts: float) -> Detection:
        score = REGION_SCORES[h.region]
        notes = []
        if h.item_seen:
            score += 0.1
            notes.append("item seen in hand")
            if not item_after:
                score += 0.15
                notes.append("hand empty afterwards")
        if ts - h.t_conceal > 1.0:
            score += 0.05
        score = round(min(max(score, 0.0), 0.99), 2)
        reach = "Reached into shelf" if not h.used_fallback else "Reached out"
        reason = f"{reach}, then {hand} hand went {REGION_TEXT[h.region]}"
        if notes:
            reason += " (" + ", ".join(notes) + ")"
        return Detection(track_id=-1, ts=h.t_conceal, reach_ts=h.t_reach, score=score,
                         region=h.region, hand=hand, reason=reason, box=(0, 0, 0, 0))
