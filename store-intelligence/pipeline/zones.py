"""Store layout & geometry — mapping a detection to a zone, and entry direction.

Reads store_layout.json. Because the exact field names in the supplied file may
vary, the loader is tolerant: it accepts a per-store object containing `cameras`
and `zones`, where a zone optionally carries an image-space `polygon`. If no
polygon is given, a camera's `role`/default zone is used. The entry camera may
define an `entry_line` plus an `inside` direction so we can classify a threshold
crossing as ENTRY (inbound) vs EXIT (outbound).

Geometry here is pure Python (no numpy/cv2) so it unit-tests without any heavy
dependency.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

Point = tuple[float, float]


def point_in_polygon(pt: Point, poly: list[Point]) -> bool:
    """Ray-casting point-in-polygon. Empty/degenerate polygon -> False."""
    if not poly or len(poly) < 3:
        return False
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


def side_of_line(pt: Point, a: Point, b: Point) -> float:
    """Signed area: >0 one side, <0 the other. Used for crossing direction."""
    return (b[0] - a[0]) * (pt[1] - a[1]) - (b[1] - a[1]) * (pt[0] - a[0])


@dataclass
class Zone:
    zone_id: str
    camera: str
    polygon: list[Point] = field(default_factory=list)
    sku_zone: Optional[str] = None
    is_billing: bool = False


@dataclass
class CameraCfg:
    camera_id: str
    role: str = "floor"  # entry | floor | billing
    entry_line: Optional[tuple[Point, Point]] = None
    inside_sign: float = 1.0  # which side of entry_line counts as "inside the store"
    default_zone: Optional[str] = None


@dataclass
class StoreLayout:
    store_id: str
    cameras: dict[str, CameraCfg] = field(default_factory=dict)
    zones: list[Zone] = field(default_factory=list)
    open_hours: dict = field(default_factory=dict)

    def zone_for(self, camera_id: str, pt: Point) -> Optional[Zone]:
        """Return the zone a point falls in for a given camera."""
        cands = [z for z in self.zones if z.camera == camera_id]
        for z in cands:
            if z.polygon and point_in_polygon(pt, z.polygon):
                return z
        # Fallback: a camera with a single default zone (no polygons supplied).
        cam = self.cameras.get(camera_id)
        if cam and cam.default_zone:
            for z in self.zones:
                if z.zone_id == cam.default_zone:
                    return z
        # Billing camera with no polygon -> treat its whole frame as BILLING.
        if cam and cam.role == "billing":
            return Zone(zone_id="BILLING", camera=camera_id, is_billing=True)
        return None

    def crossing_direction(self, camera_id: str, prev: Point, curr: Point) -> Optional[str]:
        """Classify an entry-line crossing as 'ENTRY' (inbound) or 'EXIT'."""
        cam = self.cameras.get(camera_id)
        if not cam or not cam.entry_line:
            return None
        a, b = cam.entry_line
        s_prev = side_of_line(prev, a, b)
        s_curr = side_of_line(curr, a, b)
        if s_prev == 0 or s_curr == 0 or (s_prev > 0) == (s_curr > 0):
            return None  # no crossing
        # Moving toward the 'inside' side => ENTRY.
        moved_to_inside = (s_curr > 0) == (cam.inside_sign > 0)
        return "ENTRY" if moved_to_inside else "EXIT"


def load_layout(path: str | Path, store_id: str) -> StoreLayout:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    store = (raw.get("stores", {}) or {}).get(store_id, raw.get(store_id, raw))

    cameras: dict[str, CameraCfg] = {}
    for cid, c in (store.get("cameras", {}) or {}).items():
        line = c.get("entry_line")
        entry_line = (tuple(line[0]), tuple(line[1])) if line and len(line) >= 2 else None
        cameras[cid] = CameraCfg(
            camera_id=cid,
            role=c.get("role", "floor"),
            entry_line=entry_line,
            inside_sign=float(c.get("inside_sign", 1.0)),
            default_zone=c.get("default_zone"),
        )

    zones: list[Zone] = []
    raw_zones = store.get("zones", [])
    if isinstance(raw_zones, dict):  # tolerate {name: {...}} form
        raw_zones = [{"zone_id": k, **v} for k, v in raw_zones.items()]
    for z in raw_zones:
        zid = z.get("zone_id") or z.get("name")
        zones.append(Zone(
            zone_id=zid,
            camera=z.get("camera", ""),
            polygon=[tuple(p) for p in z.get("polygon", [])],
            sku_zone=z.get("sku_zone"),
            is_billing=bool(z.get("is_billing")) or "BILL" in (zid or "").upper(),
        ))

    return StoreLayout(
        store_id=store_id,
        cameras=cameras,
        zones=zones,
        open_hours=store.get("open_hours", {}),
    )
