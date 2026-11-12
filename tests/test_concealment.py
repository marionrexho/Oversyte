"""Scenario tests for the concealment state machine using synthetic skeletons.

Person stands facing the camera: shoulders at y=200 (x=170/230), hips at y=320
(x=180/220). Shelf zone is to the person's right-hand side of the image (x>=300).
"""
import numpy as np

from oversyte.concealment import ConcealmentDetector, Person

CFG = {
    "reach_min_seconds": 0.25, "conceal_window_seconds": 4.0, "conceal_dwell_seconds": 0.35,
    "alert_threshold": 0.6, "person_cooldown_seconds": 30,
}
SHELF = [np.array([[300, 100], [500, 100], [500, 400], [300, 400]], np.float32)]
BOX = (140, 120, 260, 520)

HANG = (240, 390)        # relaxed arm, wrist at mid-thigh
ON_SHELF = (350, 230)
POCKET = (232, 315)      # right front pocket, hip level
CHEST = (205, 240)       # holding item up in front of chest (looking at it)
WAIST = (205, 265)


def person(right_wrist, left_wrist=(160, 390), tid=1):
    k = np.zeros((17, 3), np.float32)
    k[:, 2] = 0.9
    k[5] = (170, 200, .9); k[6] = (230, 200, .9)
    k[11] = (180, 320, .9); k[12] = (220, 320, .9)
    k[9] = (*left_wrist, .9); k[10] = (*right_wrist, .9)
    return Person(tid, BOX, k)


def run(script, shelf=SHELF, items_at=None, bags=(), fps=6, cfg=CFG):
    """script: list of (seconds, right_wrist_position). items_at: fn(t) -> list of boxes."""
    det = ConcealmentDetector(cfg)
    out, t = [], 1000.0
    for dur, pos in script:
        for _ in range(max(1, int(dur * fps))):
            items = items_at(t) if items_at else []
            out += det.update(t, [person(pos)], items, bags, shelf)
            t += 1 / fps
    return out, det


def item_box(p):
    return (p[0] - 10, p[1] - 15, p[0] + 10, p[1] + 15)


def test_shelf_to_pocket_alerts():
    out, _ = run([(1, HANG), (0.8, ON_SHELF), (0.4, (280, 280)), (0.8, POCKET), (1, HANG)])
    assert len(out) == 1
    assert out[0].region == "pocket" and out[0].hand == "right"
    assert out[0].score >= 0.6


def test_normal_shopping_no_alert():
    # takes item, holds it at chest to read, then lets the arm hang (carrying to checkout)
    out, det = run([(1, HANG), (0.8, ON_SHELF), (1.5, CHEST), (3, HANG)])
    assert out == []


def test_hand_in_pocket_without_shelf_reach_no_alert():
    out, _ = run([(1, HANG), (2, POCKET), (1, HANG)])
    assert out == []


def test_brief_touch_of_pocket_no_alert():
    out, _ = run([(0.8, ON_SHELF), (0.2, POCKET), (2, HANG)], fps=10)
    assert out == []


def test_too_late_no_alert():
    out, _ = run([(0.8, ON_SHELF), (5, HANG), (1, POCKET), (1, HANG)])
    assert out == []


def test_into_bag_scores_higher():
    bag = [(225, 330, 300, 420)]
    out, _ = run([(0.8, ON_SHELF), (0.4, (280, 300)), (0.8, (260, 370)), (1, (330, 260))], bags=bag)
    assert len(out) == 1 and out[0].region == "bag" and out[0].score >= 0.75


def test_item_seen_then_gone_boosts_score():
    # item tracked in the hand while carrying, vanishes once the hand is in the pocket
    state = {"pos": None}

    def items(t):
        return [item_box(state["pos"])] if state["pos"] in ((280, 280),) else []

    det = ConcealmentDetector(CFG)
    t, out = 0.0, []
    for dur, pos in [(0.8, ON_SHELF), (0.5, (280, 280)), (0.8, POCKET), (0.5, HANG)]:
        state["pos"] = pos
        for _ in range(int(dur * 6)):
            out += det.update(t, [person(pos)], items(t), [], SHELF)
            t += 1 / 6
    assert len(out) == 1
    assert "item seen in hand" in out[0].reason and "hand empty afterwards" in out[0].reason
    assert out[0].score >= 0.85


def test_waistband_alone_below_threshold():
    out, det = run([(0.8, ON_SHELF), (0.4, (280, 260)), (0.8, WAIST), (1, HANG)])
    assert out == [] and det.below_threshold and det.below_threshold[-1].region == "waistband"


def test_cooldown_dedupes_repeat():
    seq = [(0.8, ON_SHELF), (0.4, (280, 280)), (0.8, POCKET), (0.5, HANG)]
    out, _ = run(seq * 3)
    assert len(out) == 1


def test_ignore_zone_suppresses():
    det = ConcealmentDetector(CFG)
    ignore = [np.array([[0, 450], [400, 450], [400, 600], [0, 600]], np.float32)]
    t, out = 0.0, []
    for dur, pos in [(0.8, ON_SHELF), (0.4, (280, 280)), (0.8, POCKET), (1, HANG)]:
        for _ in range(int(dur * 6)):
            out += det.update(t, [person(pos)], [], [], SHELF, ignore)
            t += 1 / 6
    assert out == []


def test_fallback_without_zones():
    # no shelf zones: an outstretched arm counts as a reach
    out, _ = run([(0.8, (330, 210)), (0.4, (260, 280)), (0.8, POCKET), (1, HANG)], shelf=[])
    assert len(out) == 1 and "Reached out" in out[0].reason
