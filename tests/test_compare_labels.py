import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "compare_labels", Path(__file__).resolve().parent.parent / "tools" / "compare_labels.py")
cl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cl)

SHELF = [[[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]]]


def write(d: Path, name: str, events, shelf=SHELF, done=True):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(
        {"events": events, "scenes": [{"start": 0, "shelf": shelf, "ignore": []}], "done": done}))


def test_save_moves_only_finished_calibration_videos(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cl, "LABELS", tmp_path / "labels")
    monkeypatch.setattr(cl, "CALIB", tmp_path / "calibration")
    (tmp_path / "calibration").mkdir()
    (tmp_path / "calibration" / "videos.txt").write_text("# calibration\na.mp4\nb.mp4\n")
    write(tmp_path / "labels", "a.mp4", [])
    write(tmp_path / "labels", "b.mp4", [], done=False)
    write(tmp_path / "labels", "someone_elses.mp4", [])            # finished, but not a calibration video
    assert cl.save("alex") == 0
    assert (tmp_path / "calibration" / "alex" / "a.mp4.json").exists()
    assert not (tmp_path / "labels" / "a.mp4.json").exists()      # moved, not copied
    assert (tmp_path / "labels" / "b.mp4.json").exists()           # unfinished stays
    assert (tmp_path / "labels" / "someone_elses.mp4.json").exists()  # never touched
    assert "b.mp4" in capsys.readouterr().out                      # told it's not finished


def test_compare_reports_differences(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cl, "CALIB", tmp_path)
    write(tmp_path / "alex", "v.mp4", [{"t": 10, "type": "pocket"}, {"t": 30, "type": "bag"}, {"t": 50, "type": "pocket"}])
    write(tmp_path / "sam", "v.mp4", [{"t": 10.4, "type": "pocket"}, {"t": 31.5, "type": "waistband"}],
          shelf=[[[0.3, 0.1], [0.7, 0.1], [0.7, 0.5], [0.3, 0.5]]])
    assert cl.compare("alex", "sam") == 0
    out = capsys.readouterr().out
    assert "OK" in out and "DIFFER" in out and "ONLY alex: 0:50.0" in out
    assert "Agreement: 1 of 3" in out
    assert "ZONES: only 3" in out  # the two shelf outlines overlap about a third
