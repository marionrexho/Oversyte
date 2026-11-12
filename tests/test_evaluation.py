"""Scoring logic: replay cached AI output against labels and count hits/misses/false alarms."""
from oversyte import evaluation as ev
from oversyte.concealment import Detection

from test_concealment import BOX, CFG, HANG, ON_SHELF, POCKET, person

SIZE = (600, 600)
# shelf zone in normalized coords, matching test_concealment.SHELF on a 600x600 frame
SHELF_N = [[[300 / 600, 100 / 600], [500 / 600, 100 / 600], [500 / 600, 400 / 600], [300 / 600, 400 / 600]]]


def fake_extract(script, fps=6):
    """Build what extract() would return, from a (seconds, wrist position) script."""
    frames, t = [], 0.0
    for dur, pos in script:
        for _ in range(int(dur * fps)):
            p = person(pos)
            frames.append((t, [(p.track_id, BOX, p.kpts)], [], []))
            t += 1 / fps
    return {"size": SIZE, "frames": frames, "seconds": t}


THEFT = [(2, HANG), (0.8, ON_SHELF), (0.4, (280, 280)), (0.8, POCKET), (2, HANG)]  # pocket at ~3.2s


def labels(events, done=True):
    return {**ev.empty_labels(), "events": events, "shelf": SHELF_N, "done": done}


def test_theft_caught():
    r = ev.score_video("a", fake_extract(THEFT), labels([{"t": 3.5, "type": "pocket"}]), CFG, 4)
    assert (r["caught"], r["events"], r["false_alarms"]) == (1, 1, 0)


def test_unlabeled_alert_is_false_alarm():
    r = ev.score_video("a", fake_extract(THEFT), labels([]), CFG, 4)
    assert (r["caught"], r["false_alarms"]) == (0, 1)


def test_label_far_away_is_miss_and_false_alarm():
    r = ev.score_video("a", fake_extract(THEFT), labels([{"t": 20, "type": "pocket"}]), CFG, 4)
    assert (r["caught"], len(r["missed"]), r["false_alarms"]) == (0, 1, 1)


def test_quiet_video_no_alerts():
    r = ev.score_video("a", fake_extract([(10, HANG)]), labels([]), CFG, 4)
    assert (r["caught"], r["false_alarms"]) == (0, 0)


def test_match_pairs_each_event_once():
    d = lambda t: Detection(1, t, t - 1, 0.7, "pocket", "right", "", (0, 0, 0, 0))
    pairs, missed, fas = ev.match([d(10.0), d(10.5)], [{"t": 10.2, "type": "pocket"}])
    assert len(pairs) == 1 and not missed and len(fas) == 1


def test_summary_and_sweep():
    data = [("a", fake_extract(THEFT), labels([{"t": 3.5, "type": "pocket"}])),
            ("b", fake_extract([(60, HANG)]), labels([{"t": 30, "type": "grab_run"}]))]
    s = ev.summarize([ev.score_video(n, ex, lb, CFG, 4) for n, ex, lb in data])
    assert s["events"] == 2 and s["caught"] == 1 and s["catch_rate"] == 0.5
    assert s["by_type"]["grab_run"] == {"caught": 0, "events": 1}
    rows = ev.sweep(data, CFG, 4)
    assert rows[0]["catch_rate"] == 0.5          # threshold 0.40 still catches the pocket theft
    assert rows[-1]["catch_rate"] == 0.0         # threshold 0.90 is too strict for a plain pocket


def test_quality_flags_bad_video():
    good = ev.quality(fake_extract([(10, HANG)]), {"width": 1280, "height": 720, "fps": 15}, 640)
    assert good["grade"] == "good" and good["issues"] == []
    ex = fake_extract([(10, HANG)])
    ex["image"] = {"brightness": 30, "sharpness": 20}
    bad = ev.quality(ex, {"width": 352, "height": 240, "fps": 5}, 640)
    assert bad["grade"] == "poor"
    assert {"dark", "blurry"} <= set(bad["issues"]) and any("352x240" in i for i in bad["issues"])
    empty = ev.quality({"size": (640, 480), "frames": [(0, [], [], [])], "seconds": 1},
                       {"width": 640, "height": 480, "fps": 15}, 640)
    assert empty["grade"] == "poor" and "no people detected" in empty["issues"]


def test_labels_roundtrip_and_old_format(tmp_path):
    # old single-zone format becomes "angle 1"
    saved = ev.save_labels(tmp_path, "v.mp4", {"events": [{"t": 5.123, "type": "bogus"}],
                                               "shelf": [[[0, 0], [2, 0], [1, 1]]], "done": 1})
    assert saved["events"] == [{"t": 5.12, "type": "other"}]
    assert saved["scenes"] == [{"start": 0.0, "shelf": [[[0, 0], [1, 0], [1, 1]]], "ignore": []}]
    assert saved["done"] is True and "shelf" not in saved
    assert ev.load_labels(tmp_path, "v.mp4") == saved


def test_scenes_sorted_first_starts_at_zero_and_deduped(tmp_path):
    saved = ev.save_labels(tmp_path, "v.mp4", {"scenes": [
        {"start": 30, "shelf": [], "ignore": []}, {"start": 2, "shelf": [], "ignore": []},
        {"start": 30.2, "shelf": [], "ignore": []}]})
    assert [s["start"] for s in saved["scenes"]] == [0.0, 30.0]


def two_angles(shelf_angle2):
    """Angle 1 (0-10s) has no shelf where the person reaches; angle 2 (from 10s) has it or not."""
    return {**ev.empty_labels(), "done": True, "scenes": [
        {"start": 0, "shelf": [[[0, 0], [0.1, 0], [0.1, 0.1]]], "ignore": []},
        {"start": 10, "shelf": SHELF_N if shelf_angle2 else [[[0, 0], [0.1, 0], [0.1, 0.1]]], "ignore": []}]}


def test_zones_follow_camera_angle():
    theft_in_angle2 = [(10, HANG)] + THEFT  # theft happens at ~13.2s, inside angle 2
    lb = two_angles(shelf_angle2=True)
    lb["events"] = [{"t": 13.5, "type": "pocket"}]
    r = ev.score_video("a", fake_extract(theft_in_angle2), lb, CFG, 4)
    assert (r["caught"], r["false_alarms"]) == (1, 0)
    lb2 = two_angles(shelf_angle2=False)  # same video, but angle 2's zones miss the shelf
    lb2["events"] = lb["events"]
    assert ev.score_video("a", fake_extract(theft_in_angle2), lb2, CFG, 4)["caught"] == 0


def test_reach_before_cut_does_not_pair_with_pocket_after_cut():
    # reaches into the shelf right before the switch, "pocket" pose right after it
    script = [(9, HANG), (0.9, ON_SHELF), (0.2, (280, 280)), (1, POCKET), (2, HANG)]
    lb = {**ev.empty_labels(), "done": True, "scenes": [
        {"start": 0, "shelf": SHELF_N, "ignore": []}, {"start": 10, "shelf": SHELF_N, "ignore": []}]}
    assert ev.score_video("a", fake_extract(script), lb, CFG, 4)["false_alarms"] == 0
    lb_one = {**lb, "scenes": lb["scenes"][:1]}  # without the cut it would alert
    assert ev.score_video("a", fake_extract(script), lb_one, CFG, 4)["false_alarms"] == 1
