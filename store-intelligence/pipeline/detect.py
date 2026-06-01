"""Detection orchestrator — raw CCTV clips -> behavioural events.

Pipeline per camera clip:
  decode frames (OpenCV) -> detect persons (Ultralytics YOLO) -> track
  (YOLO/ByteTrack built-in) -> per track: coarse colour feature, foot-point ->
  zone (zones.py), entry-line crossing -> direction. Tracks are unified across
  cameras and across re-entry by identity.py, then turned into events by
  events_builder.py. Staff are classified once per track (staff.py).

Heavy dependencies (cv2, ultralytics, numpy) are imported lazily inside run(),
so importing this module — and unit-testing the logic modules — needs none of
them. Timestamps are synthesised: clip_start + frame_index / fps.

This is the integration layer; its building blocks are unit-tested in
tests/test_pipeline.py. Run it via pipeline/run.sh once the dataset is present.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .events_builder import EventBuilder
from .identity import IdentityResolver
from .zones import StoreLayout, load_layout

# Camera role inference from filename when the layout doesn't pin it.
ROLE_HINTS = {"ENTRY": "entry", "FLOOR": "floor", "MAIN": "floor", "BILL": "billing"}


def infer_camera_role(camera_id: str, layout: StoreLayout) -> str:
    cam = layout.cameras.get(camera_id)
    if cam and cam.role:
        return cam.role
    up = camera_id.upper()
    for key, role in ROLE_HINTS.items():
        if key in up:
            return role
    return "floor"


def _foot_point(box) -> tuple[float, float]:
    """Bottom-centre of an xyxy box — a person's ground contact, best for zones."""
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2.0, float(y2))


def _torso_hist(frame, box, bins: int = 12):
    """Coarse HSV hue histogram of the torso region — the weak re-ID feature."""
    import cv2  # lazy
    import numpy as np

    x1, y1, x2, y2 = [int(v) for v in box]
    h = y2 - y1
    ty1, ty2 = y1 + int(0.2 * h), y1 + int(0.55 * h)  # torso band
    crop = frame[max(0, ty1):max(1, ty2), max(0, x1):max(1, x2)]
    if crop.size == 0:
        return [0.0] * bins
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0], None, [bins], [0, 180]).flatten()
    s = hist.sum()
    return (hist / s).tolist() if s else [0.0] * bins


def run(
    clips_dir: str,
    store_id: str,
    layout_path: str,
    out_events: str,
    clip_start: Optional[datetime] = None,
    fps: float = 15.0,
    stride: int = 3,
    conf_threshold: float = 0.25,
    api: Optional[str] = None,
) -> dict:
    """Process all clips for one store and write/post events. Returns a summary."""
    import cv2  # lazy heavy imports
    from ultralytics import YOLO

    layout = load_layout(layout_path, store_id)
    resolver = IdentityResolver()
    builder = EventBuilder(store_id=store_id)
    model = YOLO("yolov8n.pt")  # auto-downloads; person class = 0

    clip_start = clip_start or datetime.now(timezone.utc)
    clips = sorted(Path(clips_dir).glob("*.*"))
    # Track local-track-id -> global visitor_id per camera.
    for clip in clips:
        camera_id = clip.stem.upper()
        role = infer_camera_role(camera_id, layout)
        cap = cv2.VideoCapture(str(clip))
        clip_fps = cap.get(cv2.CAP_PROP_FPS) or fps
        local_to_global: dict[int, str] = {}
        prev_foot: dict[int, tuple[float, float]] = {}
        frame_idx = -1

        # Ultralytics streaming tracker (ByteTrack) handles per-camera tracks.
        for result in model.track(source=str(clip), stream=True, persist=True,
                                   classes=[0], conf=conf_threshold, verbose=False,
                                   tracker="bytetrack.yaml"):
            frame_idx += 1
            if frame_idx % stride:
                continue
            ts = clip_start + timedelta(seconds=frame_idx / clip_fps)
            frame = result.orig_img
            if result.boxes is None or result.boxes.id is None:
                continue
            for box, tid, conf in zip(result.boxes.xyxy.tolist(),
                                      result.boxes.id.int().tolist(),
                                      result.boxes.conf.tolist()):
                foot = _foot_point(box)
                feat = _torso_hist(frame, box)

                if role == "entry":
                    prev = prev_foot.get(tid)
                    direction = layout.crossing_direction(camera_id, prev, foot) if prev else None
                    prev_foot[tid] = foot
                    if direction == "ENTRY":
                        vid, is_re = resolver.resolve_entry(ts.timestamp(), feat)
                        local_to_global[tid] = vid
                        builder.entry(vid, ts, camera_id, conf, is_reentry=is_re)
                    elif direction == "EXIT":
                        vid = local_to_global.get(tid)
                        if vid:
                            builder.exit(vid, ts, camera_id, conf)
                            resolver.note_exit(vid, ts.timestamp())
                    continue

                # Floor/billing: associate to an existing inside visitor (cross-cam dedup).
                vid = local_to_global.get(tid) or resolver.resolve_crosscam(ts.timestamp(), feat)
                if vid is None:
                    continue  # never mint a new visitor off a non-entry camera
                local_to_global[tid] = vid
                resolver.touch(vid, ts.timestamp())
                zone = layout.zone_for(camera_id, foot)
                queue_depth = 0
                if role == "billing":
                    # queue depth ~= persons currently tracked in the billing frame.
                    queue_depth = int(result.boxes.id.shape[0]) if result.boxes.id is not None else 0
                builder.update_zone(vid, ts, camera_id, zone, conf, queue_depth=queue_depth)
        cap.release()

    events = builder.events
    summary = {"store_id": store_id, "clips": len(clips), "events": len(events),
               "unique_visitors": resolver.unique_entries}

    from .emit import post_events, write_jsonl
    write_jsonl(events, out_events)
    if api:
        summary["ingest"] = post_events(events, api)
    return summary


def main():
    ap = argparse.ArgumentParser(description="Run detection on a store's clips.")
    ap.add_argument("--clips", required=True, help="dir of clips for ONE store")
    ap.add_argument("--store", required=True)
    ap.add_argument("--layout", required=True, help="store_layout.json")
    ap.add_argument("--out", default="out/events.jsonl")
    ap.add_argument("--start", default=None, help="clip start ISO time")
    ap.add_argument("--fps", type=float, default=15.0)
    ap.add_argument("--stride", type=int, default=3)
    ap.add_argument("--api", default=None, help="POST events here too (optional)")
    args = ap.parse_args()
    start = datetime.fromisoformat(args.start) if args.start else None
    summary = run(args.clips, args.store, args.layout, args.out,
                  clip_start=start, fps=args.fps, stride=args.stride, api=args.api)
    print(summary)


if __name__ == "__main__":
    main()
