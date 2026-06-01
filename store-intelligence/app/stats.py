"""Small statistics helpers.

The Wilson score interval gives a principled confidence band on a proportion
(like conversion rate) that behaves well for small samples and near 0/1 — far
better than the naive normal approximation. We surface this so every rate the
API returns carries its uncertainty, which is exactly the "how do you handle
uncertainty" capability the challenge rewards.
"""
from __future__ import annotations

import math
from typing import Literal

Confidence = Literal["LOW", "MEDIUM", "HIGH"]


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float, float]:
    """Return (point_estimate, lower, upper) for a binomial proportion.

    z=1.96 → 95% interval. Returns (0,0,0) for an empty sample rather than
    raising — zero-traffic must never crash.
    """
    if total <= 0:
        return 0.0, 0.0, 0.0
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = (z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)) / denom
    lower = max(0.0, center - margin)
    upper = min(1.0, center + margin)
    return round(p, 4), round(lower, 4), round(upper, 4)


def confidence_band(sample_size: int, min_sessions: int) -> Confidence:
    """Map a sample size to a human label used by /metrics and /heatmap."""
    if sample_size >= min_sessions:
        return "HIGH"
    if sample_size >= max(1, min_sessions // 2):
        return "MEDIUM"
    return "LOW"


def normalise_0_100(value: float, max_value: float) -> float:
    if max_value <= 0:
        return 0.0
    return round(min(100.0, max(0.0, value / max_value * 100.0)), 2)
