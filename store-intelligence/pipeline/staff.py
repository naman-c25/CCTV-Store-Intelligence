"""Staff vs customer classification.

Two-tier, by design (see CHOICES.md):
  1. A cheap, always-available colour-histogram heuristic: retail staff wear a
     consistent uniform, so a track whose dominant torso colour matches the
     store's configured uniform colour scores high.
  2. An optional VLM pass (Claude/GPT-4V/Gemini) on the track's median crop for
     the ambiguous cases, with the heuristic as the fallback when the VLM is
     unavailable or unsure.

We classify once per TRACK (not per frame) to bound cost. The VLM prompt is kept
here verbatim so it can be quoted in DESIGN.md and evaluated honestly.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Optional

# The exact VLM prompt (quoted in DESIGN.md). Kept as a constant for traceability.
VLM_STAFF_PROMPT = (
    "This is a cropped image from retail store CCTV. Faces are blurred for "
    "privacy. Decide whether this person is a STAFF member (store uniform, "
    "apron, lanyard, or standing behind a counter) or a CUSTOMER (street "
    "clothes, carrying/browsing products). Respond ONLY with compact JSON: "
    '{"role":"STAFF"|"CUSTOMER","confidence":0.0-1.0,"reason":"<short>"}'
)


@dataclass
class StaffDecision:
    is_staff: bool
    confidence: float
    source: str  # "vlm" | "histogram" | "vlm+histogram"
    reason: str = ""


def histogram_staff_score(torso_hsv_hist, uniform_hsv_hist) -> float:
    """Similarity of a track's torso colour histogram to the uniform's.

    Both args are equal-length sequences (HSV histograms). Returns 0..1.
    Numpy is used if present but not required.
    """
    if not torso_hsv_hist or not uniform_hsv_hist:
        return 0.0
    try:
        import numpy as np  # noqa

        a = np.asarray(torso_hsv_hist, dtype=float)
        b = np.asarray(uniform_hsv_hist, dtype=float)
        a = a / (a.sum() or 1.0)
        b = b / (b.sum() or 1.0)
        # Bhattacharyya coefficient -> similarity in [0,1].
        return float(np.sqrt(a * b).sum())
    except Exception:
        s = sum((x * y) ** 0.5 for x, y in zip(torso_hsv_hist, uniform_hsv_hist))
        ta, tb = sum(torso_hsv_hist) or 1.0, sum(uniform_hsv_hist) or 1.0
        return s / ((ta * tb) ** 0.5)


def classify_with_vlm(jpeg_bytes: bytes) -> Optional[StaffDecision]:
    """Call a vision LLM if configured (ANTHROPIC_API_KEY). Returns None if the
    VLM is unavailable so the caller falls back to the heuristic."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic  # type: ignore

        client = anthropic.Anthropic(api_key=api_key)
        b64 = base64.b64encode(jpeg_bytes).decode()
        msg = client.messages.create(
            model=os.getenv("VLM_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=120,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": VLM_STAFF_PROMPT},
                ],
            }],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        return StaffDecision(
            is_staff=str(data.get("role", "")).upper() == "STAFF",
            confidence=float(data.get("confidence", 0.5)),
            source="vlm",
            reason=str(data.get("reason", "")),
        )
    except Exception:
        return None


def decide_staff(
    torso_hist=None,
    uniform_hist=None,
    jpeg_bytes: Optional[bytes] = None,
    hist_threshold: float = 0.6,
    use_vlm: bool = True,
) -> StaffDecision:
    """Combine heuristic + optional VLM. VLM wins when confident; otherwise the
    histogram decides. Always returns a decision (never raises)."""
    hist_score = histogram_staff_score(torso_hist, uniform_hist)
    hist_decision = StaffDecision(
        is_staff=hist_score >= hist_threshold,
        confidence=round(hist_score, 3),
        source="histogram",
    )

    if use_vlm and jpeg_bytes is not None:
        vlm = classify_with_vlm(jpeg_bytes)
        if vlm is not None and vlm.confidence >= 0.7:
            return StaffDecision(vlm.is_staff, vlm.confidence,
                                 "vlm+histogram" if hist_score else "vlm", vlm.reason)
    return hist_decision
