"""Detection pipeline: raw CCTV clips -> structured behavioural events.

Pure logic (zones, identity, event state machine, calibration) lives in modules
that import no heavy CV dependency, so it is unit-testable without a GPU or a
video file. The orchestrator (detect.py) imports OpenCV / Ultralytics lazily.
"""
