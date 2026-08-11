"""Pure helpers shared by Audio Master's Edit track controls.

Keeping the normalisation and sample processing outside the Tk view makes the
track-management model easy to test and prevents a UI redraw from becoming a
hidden part of audio correctness.  This module deliberately knows nothing
about ``AudioBalancerApp`` or ``EditWindow``.
"""

from __future__ import annotations

import math
from typing import Any, Dict

import numpy as np


TRACK_GAIN_MIN_DB = -60.0
TRACK_GAIN_MAX_DB = 12.0
TRACK_PAN_MIN = -1.0
TRACK_PAN_MAX = 1.0

# Names are intentionally descriptive rather than numeric so the Track
# Inspector remains usable for people who do not want to remember hex values.
TRACK_COLOR_PRESETS = {
    "Ocean": "#2A4D6E",
    "Forest": "#2E6E4D",
    "Amber": "#6E5A2A",
    "Violet": "#5A2A6E",
    "Rose": "#6E2A3D",
    "Teal": "#2A6E6E",
    "Olive": "#5A6E2A",
    "Copper": "#6E3D2A",
}


def _finite_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: Any, lower: float, upper: float, default: float) -> float:
    return max(lower, min(upper, _finite_float(value, default)))


def _safe_color(value: Any, fallback: str) -> str:
    color = str(value or "").strip()
    if len(color) == 7 and color.startswith("#"):
        try:
            int(color[1:], 16)
            return color.upper()
        except ValueError:
            pass
    return fallback


def normalize_track_metadata(
    value: Any,
    *,
    fallback_name: str,
    fallback_color: str,
    fallback_order: int,
) -> Dict[str, Any]:
    """Return project-safe metadata for one Edit track.

    Old projects have no ``edit_track`` object, and hand-edited JSON can have
    malformed values.  The returned dictionary is always safe to draw,
    serialise and feed into the mixer.  Names are deliberately short enough to
    keep the track header responsive on compact Edit panes.
    """
    raw = value if isinstance(value, dict) else {}
    default_name = str(fallback_name or "Track").strip() or "Track"
    name = str(raw.get("name", default_name) or "").strip()[:80] or default_name[:80]
    try:
        order = int(raw.get("order", fallback_order))
    except (TypeError, ValueError):
        order = fallback_order
    return {
        "name": name,
        "color": _safe_color(raw.get("color"), fallback_color),
        "gain_db": round(_clamp(raw.get("gain_db"), TRACK_GAIN_MIN_DB, TRACK_GAIN_MAX_DB, 0.0), 4),
        "pan": round(_clamp(raw.get("pan"), TRACK_PAN_MIN, TRACK_PAN_MAX, 0.0), 4),
        "order": max(0, order),
    }


def track_pan_label(value: Any) -> str:
    """Human-friendly pan/balance text for a compact track header."""
    pan = _clamp(value, TRACK_PAN_MIN, TRACK_PAN_MAX, 0.0)
    if abs(pan) < 0.005:
        return "C"
    side = "L" if pan < 0 else "R"
    return f"{side}{abs(pan) * 100:.0f}"


def apply_track_mix_controls(samples: np.ndarray, gain_db: Any = 0.0, pan: Any = 0.0) -> np.ndarray:
    """Apply one track's level and stereo balance without mutating its input.

    ``pan`` is intentionally implemented as a stereo *balance*: centre keeps
    each source channel at unity, while moving left/right attenuates only the
    opposite channel.  That is predictable for already-stereo assets and does
    not introduce a surprise +3 dB boost at either edge.  Mono and surround
    buffers receive gain only; the renderer will already have conformed mono
    material to the chosen output layout before this helper is called.
    """
    source = np.asarray(samples, dtype=np.float32)
    result = source.copy()
    gain = 10.0 ** (_clamp(gain_db, TRACK_GAIN_MIN_DB, TRACK_GAIN_MAX_DB, 0.0) / 20.0)
    if gain != 1.0:
        result *= gain
    pan_value = _clamp(pan, TRACK_PAN_MIN, TRACK_PAN_MAX, 0.0)
    if result.ndim == 2 and result.shape[1] == 2 and abs(pan_value) >= 1e-7:
        if pan_value < 0:
            result[:, 1] *= 1.0 + pan_value
        else:
            result[:, 0] *= 1.0 - pan_value
    return result.astype(np.float32, copy=False)
