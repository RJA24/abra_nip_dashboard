"""High-resolution, pixel-budget-aware Plotly PNG exports for SBI.

The previous dashboards exported most plots at 4×-5×. For report-quality PNGs,
request 8× for small and moderate plots. For tall school and 27-RHU charts,
limit the decoded RGBA canvas to reduce browser memory/rendering failures.
This changes only the PNG toolbar export, not the on-screen chart or data.
"""
from __future__ import annotations

from math import isqrt
from programs.sbi.reporting import plotly_export_config

# Roughly 320 MiB for an RGBA canvas; some extra browser memory is also needed.
MAX_PNG_PIXELS = 80_000_000
MAX_PNG_SIDE = 16_000


def ultra_png_export_config(
    filename: str,
    *,
    scale: int = 8,
    width: int | None = None,
    height: int | None = None,
) -> dict:
    """Return a PNG download config with 8× requested scale and safe limits.

    Plotly uses the existing chart width/height when they are not explicitly
    supplied, so 1,000×700 is a conservative export-size estimate for deciding
    the scale; charts with explicit size use their actual export dimensions.
    If the cap is hit, scale is reduced (not the underlying chart content).
    """
    requested_scale = max(int(scale), 1)
    base_width = max(int(width if width is not None else 1000), 1)
    base_height = max(int(height if height is not None else 700), 1)
    base_pixels = base_width * base_height
    scale_by_pixels = isqrt(MAX_PNG_PIXELS // base_pixels) if base_pixels <= MAX_PNG_PIXELS else 1
    scale_by_side = MAX_PNG_SIDE // max(base_width, base_height)
    effective_scale = max(1, min(requested_scale, scale_by_pixels, scale_by_side))
    return plotly_export_config(
        filename, scale=effective_scale, width=width, height=height,
    )
