"""Rebuilds the coverage grid from raw trajectories and reports what it found.

WHY THIS EXISTS.

Every change to the pose pipeline so far has been argued from the code
and checked against synthetic poses. That is how four hypotheses about
one symptom got raised in a single session and two of them were refuted
against the very code that inspired them.

The grid is a DERIVATION, not a source: `MissionTrajectoryStore` keeps
the raw pose points of the last ten missions in millimetres. So a change
can be measured on real data before it ships, and applied retroactively
to what is already stored -- neither of which needs the robot to drive.

WHAT IT REPORTS.

Three numbers decide what is worth building next, and none of them can
be guessed:

  segments per mission   A frame break happens WITHIN a mission (a
                         pickup), not between missions. If the stored
                         trajectories barely contain discontinuities,
                         the whole anchoring apparatus is unnecessary.

  segment size           Decides which anchoring method can apply at
                         all: grid alignment needs area, constriction
                         matching needs a feature, and a segment with
                         neither is unanchorable.

  step speed spread      `_observed_speed_mm_s` measures a percentile of
                         this at runtime. Seeing the actual distribution
                         says whether the fallback is set sensibly, or
                         whether it is another borrowed constant.

Plus a rebuilt-vs-stored grid comparison: cells the rebuild adds are the
holes the old point-only stamping left; cells it drops would be a
regression and are worth looking at closely.

USAGE.

    python scripts/rebuild_grid_from_trajectories.py <backup.zip>
    python scripts/rebuild_grid_from_trajectories.py <storage-dir>

Accepts either a `roomba_plus.create_backup` archive or a directory
holding the raw `.storage` files. Reads only; writes nothing.
"""

from __future__ import annotations

import json
import math
import sys
import zipfile
from pathlib import Path
from typing import Any

#: Mirrors grid_store.CELL_SIZE_MM. Duplicated rather than imported so
#: this runs without Home Assistant present -- the point is to analyse a
#: backup on any machine, not only inside a working install.
CELL_SIZE_MM = 150.0
#: Chassis radius used for the disk stamp (340 mm diameter).
ROBOT_RADIUS_MM = 170.0


def _load(source: Path, stem: str) -> dict[str, Any] | None:
    """One store's payload, from a backup zip or a storage directory."""
    if source.is_file() and source.suffix == ".zip":
        with zipfile.ZipFile(source) as z:
            match = [n for n in z.namelist() if stem in n and n.endswith(".json")]
            if not match:
                return None
            raw = json.loads(z.read(match[0]))
    else:
        match_files = sorted(source.glob(f"*{stem}*"))
        if not match_files:
            return None
        raw = json.loads(match_files[0].read_text())
    # HA wraps payloads in {version, key, data}; backups may not.
    payload = raw.get("data", raw)
    return payload if isinstance(payload, dict) else None


def _mm_to_cell(x_mm: float, y_mm: float) -> tuple[int, int]:
    return (int(math.floor(x_mm / CELL_SIZE_MM)), int(math.floor(y_mm / CELL_SIZE_MM)))


def _stamp(points: list[tuple[float, float]], densify: bool) -> set[tuple[int, int]]:
    """The disk-fill stamp, optionally with along-the-path densification.

    Two modes on purpose: the difference between them IS the measurement
    the caller is after.
    """
    dense: list[tuple[float, float]] = list(points)
    if densify and len(points) >= 2:
        step = max(ROBOT_RADIUS_MM, CELL_SIZE_MM / 2.0)
        dense = [points[0]]
        for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
            dist = math.hypot(x1 - x0, y1 - y0)
            if step < dist <= 1200.0:
                for i in range(1, int(dist / step)):
                    f = i * step / dist
                    dense.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
            dense.append((x1, y1))

    touched: set[tuple[int, int]] = set()
    cell_radius = int(ROBOT_RADIUS_MM // CELL_SIZE_MM) + 1
    for x_mm, y_mm in dense:
        cx, cy = _mm_to_cell(x_mm, y_mm)
        touched.add((cx, cy))
        for dx in range(-cell_radius, cell_radius + 1):
            for dy in range(-cell_radius, cell_radius + 1):
                ccx = (cx + dx + 0.5) * CELL_SIZE_MM
                ccy = (cy + dy + 0.5) * CELL_SIZE_MM
                if (ccx - x_mm) ** 2 + (ccy - y_mm) ** 2 <= ROBOT_RADIUS_MM**2:
                    touched.add((cx + dx, cy + dy))
    return touched


def _segments(points: list[tuple[float, float]], limit_mm: float) -> list[int]:
    """Lengths of the continuous runs, split where a step exceeds limit."""
    if not points:
        return []
    runs: list[int] = []
    current = 1
    for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
        if math.hypot(x1 - x0, y1 - y0) > limit_mm:
            runs.append(current)
            current = 1
        else:
            current += 1
    runs.append(current)
    return runs


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    source = Path(argv[1])
    if not source.exists():
        print(f"not found: {source}")
        return 2

    traj = _load(source, "trajectories")
    if traj is None:
        print(
            "No trajectory store in this source.\n\n"
            "It is written at mission end for EPHEMERAL robots with the map\n"
            "enabled, and `roomba_plus.create_backup` includes it. Without it\n"
            "none of the numbers below can be measured -- a grid alone has the\n"
            "poses already resolved into cells."
        )
        return 1

    missions = traj.get("missions") or []
    print(f"missions with stored trajectories: {len(missions)}")
    if not missions:
        return 1

    all_speeds: list[float] = []
    all_steps: list[float] = []
    rebuilt: set[tuple[int, int]] = set()
    point_only: set[tuple[int, int]] = set()

    print("\nper mission:")
    for m in missions:
        pts = [(float(p[0]), float(p[1])) for p in (m.get("points") or [])]
        thetas = m.get("thetas") or []
        if len(pts) < 2:
            continue
        steps = [
            math.hypot(b[0] - a[0], b[1] - a[1])
            for a, b in zip(pts, pts[1:], strict=False)
        ]
        all_steps.extend(steps)
        # Speed needs a time base the store does not keep; step length is
        # the honest proxy, and it is what the threshold compares anyway.
        all_speeds.extend(steps)
        runs = _segments(pts, limit_mm=1200.0)
        rebuilt |= _stamp(pts, densify=True)
        point_only |= _stamp(pts, densify=False)
        print(
            f"  {m.get('mission_key', '?'):24} points={len(pts):5}  "
            f"thetas={'yes' if thetas else 'NO':3}  "
            f"segments={len(runs):3}  longest={max(runs):5}"
        )

    print("\nstep length (mm):")
    for q in (0.50, 0.90, 0.99):
        print(f"  p{int(q * 100):02}  {_percentile(all_steps, q):7.0f}")
    print(f"  max  {max(all_steps):7.0f}" if all_steps else "  max      n/a")

    print("\ngrid:")
    print(f"  rebuilt (along the path)  {len(rebuilt):6} cells")
    print(f"  point-only (old stamping) {len(point_only):6} cells")
    print(f"  holes the old way left    {len(rebuilt - point_only):6} cells")

    stored = _load(source, "grid")
    if stored and isinstance(stored.get("cells"), dict):
        cells = {
            (int(k.split(",")[0]), int(k.split(",")[1])) for k in stored["cells"]
        }
        print(f"  stored grid               {len(cells):6} cells")
        print(f"  rebuilt adds              {len(rebuilt - cells):6} cells")
        # A cell the rebuild loses is worth a hard look: the rebuild sees
        # ten missions, the stored grid may span more, so a difference is
        # expected -- but a LARGE one means the rebuild is dropping data.
        print(f"  rebuilt lacks             {len(cells - rebuilt):6} cells")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
