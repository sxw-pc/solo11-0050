"""Stream-level epoch basis: shared ruler, jittered first samples, report fields."""

from __future__ import annotations

import re
from pathlib import Path

from sampleripple.config import load_config
from sampleripple.engine import run_verification
from sampleripple.report_json import build_report

GRID = 1717200000000  # 2024-06-01T00:00:00.000Z, exactly on the 60000ms grid
PERIOD = 60000


def _write_jittered_csv(path: Path) -> None:
    """Two groups on the same nominal grid.

    sensor-alpha: only the first sample is jittered (+120ms, inside the 500ms
    edge tolerance); every later sample lands exactly on the grid.
    sensor-beta: every sample is uniformly -80ms off the grid.
    """

    rows = ["site,line,sensor,seq,ts,value"]
    for k in range(6):
        ts = GRID + k * PERIOD + (120 if k == 0 else 0)
        rows.append(f"site-a,line-01,sensor-alpha,{k},{ts},{k}.5")
    for k in range(6):
        ts = GRID + k * PERIOD - 80
        rows.append(f"site-a,line-01,sensor-beta,{k},{ts},{k}.5")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")


def _report(config_path: Path) -> dict:
    return build_report(run_verification(load_config(config_path)))


def _groups(report: dict) -> dict[str, dict]:
    stream = report["streams"][0]
    return {group["group_key"]: group for group in stream["groups"]}


def test_first_point_jitter_inside_tolerance_stays_clean_on_epoch_basis(
    tmp_path: Path, config_writer, stream_factory
):
    source = tmp_path / "jittered.csv"
    _write_jittered_csv(source)
    config_path = config_writer(
        [stream_factory(source, epoch_align_ms=GRID)]
    )
    report = _report(config_path)
    groups = _groups(report)

    # The switched basis must not invent pseudo GAP / MISSING_SLOT / EDGE_SKEW.
    assert report["summary"]["anomaly_count"] == 0
    assert all(group["status"] == "ok" for group in groups.values())

    alpha = groups["site-a/line-01/sensor-alpha"]
    assert alpha["anchor_source"] == "epoch"
    assert alpha["anchor_ms"] == GRID
    assert alpha["anchor_offset_ms"] == 0
    # Slot 0 is rebuilt on the declared basis, so the jittered first sample
    # still fills it: no phantom expected/missing slot miscount.
    assert alpha["expected_points"] == 6
    assert alpha["missing_slots"] == 0
    assert alpha["alignment_offset_ms"] == 0.0

    beta = groups["site-a/line-01/sensor-beta"]
    assert beta["expected_points"] == 6
    assert beta["missing_slots"] == 0
    assert beta["alignment_offset_ms"] == -80.0

    # Both groups are measured on the same declared ruler.
    assert alpha["anchor_ms"] == beta["anchor_ms"] == GRID
    assert alpha["anchor_at"] == beta["anchor_at"] == "2024-06-01T00:00:00.000Z"


def test_group_first_default_keeps_legacy_anchor_behavior(
    tmp_path: Path, config_writer, stream_factory
):
    _write_jittered_csv(tmp_path / "jittered.csv")
    config_path = config_writer([stream_factory(tmp_path / "jittered.csv")])
    report = _report(config_path)
    groups = _groups(report)

    # Same benign data: still no anomalies without the declared basis...
    assert report["summary"]["anomaly_count"] == 0

    alpha = groups["site-a/line-01/sensor-alpha"]
    assert alpha["anchor_source"] == "group_first"
    assert alpha["anchor_ms"] == alpha["first_ms"] == GRID + 120
    assert alpha["anchor_offset_ms"] == 120
    # ...but each group anchors on its own first sample, so the legacy span
    # formula silently under-counts the expected window for alpha.
    assert alpha["expected_points"] == 5

    beta = groups["site-a/line-01/sensor-beta"]
    assert beta["anchor_ms"] == beta["first_ms"] == GRID - 80
    assert beta["anchor_offset_ms"] == -80
    # The two anchors differ: without a declared basis there is no shared ruler.
    assert alpha["anchor_ms"] != beta["anchor_ms"]


def test_report_groups_carry_anchor_provenance(demo_report):
    stamp = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    for stream in demo_report["streams"]:
        declared = {
            "clean-grid": 1717200000000,
            "drift-run": 1717286397000,
            "mixed-run": 1717632003000,
        }.get(stream["stream_id"])
        anchors = set()
        for group in stream["groups"]:
            assert {"anchor_at", "anchor_ms", "anchor_offset_ms", "anchor_source"} <= set(
                group
            )
            assert stamp.match(group["anchor_at"])
            anchors.add(group["anchor_ms"])
            if declared is None:
                assert group["anchor_source"] == "group_first"
                assert group["anchor_ms"] == group["first_ms"]
            else:
                assert group["anchor_source"] == "epoch"
                assert group["anchor_ms"] == declared
        # One stream, one basis: every group reports the same anchor.
        assert len(anchors) == 1


def test_demo_clean_grid_stays_anomaly_free_on_epoch_basis(demo_report):
    clean = next(
        stream for stream in demo_report["streams"] if stream["stream_id"] == "clean-grid"
    )
    assert clean["summary"]["anomaly_count"] == 0
    assert clean["anomalies"] == []
    for group in clean["groups"]:
        assert group["anchor_source"] == "epoch"
        assert group["anchor_ms"] == 1717200000000
        assert group["expected_points"] == 6
        assert group["missing_slots"] == 0
