"""One-click, server-generated nine-PNG SBI reporting pack.

Unlike Plotly's browser-side camera button, this creates a single ZIP on the
Streamlit server. The tables come from the same helper functions as the live
G1/G4/G7, Grade 5, and reconciliation dashboards; no screenshot scraping or
Kaleido/Chrome installation is required.
"""
from __future__ import annotations

from datetime import date
from io import BytesIO
from math import ceil, floor, log10
from functools import lru_cache
from zipfile import ZipFile, ZIP_DEFLATED

import numpy as np
import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

from core.config import ABRA_MUNIS
from programs.sbi.g5_dashboard import _build_g5_remaining_rows, _prepare_g5_targets
from programs.sbi.rhu_tracker import (
    _grouped_reconciliation_png,
    _grouped_reconciliation_rows,
    _with_reconciliation_total,
    fetch_workbook_dashboard_entries,
)
from programs.sbi.workbook_dashboard import prepare_workbook_entries

# Keep PNGs legible at normal zoom, and sharp when pasted into reporting slides.
_IMAGE_W = 2600
_BG = "#fbfcfe"
_NAVY = "#122a44"
_TEAL = "#14566b"
_BLUE = "#1b7094"
_ORANGE = "#ed732b"
_PURPLE = "#8b64aa"
_MUTED = "#627487"
_GRID = "#e5ebf1"

_FONT_CANDIDATES = {
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ],
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ],
}


@lru_cache(maxsize=64)
def _font(size: int, bold: bool = False):
    for file in _FONT_CANDIDATES["bold" if bold else "regular"]:
        try:
            return ImageFont.truetype(file, size)
        except OSError:
            pass
    # Pillow's scalable built-in font works on current Streamlit deployments.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _txt(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, *,
         size: int = 26, bold: bool = False, color: str = _NAVY,
         anchor: str = "la", max_width: int | None = None) -> None:
    font = _font(size, bold)
    if max_width is not None:
        while size > 16 and draw.textbbox((0, 0), text, font=font)[2] > max_width:
            size -= 1
            font = _font(size, bold)
    draw.text((x, y), text, font=font, fill=color, anchor=anchor)


def _nice_tick(maximum: float) -> float:
    raw = max(maximum / 5, 1)
    mag = 10 ** floor(log10(raw))
    for multiple in (1, 2, 2.5, 5, 10):
        if raw <= multiple * mag:
            return multiple * mag
    return 10 * mag


def _fmt_count(value: float) -> str:
    return f"{int(round(value)):,}"


def _make_coverage_png(frame: pd.DataFrame, *,
                       antigen: str, is_g5: bool, mode: str,
                       source: str, target_basis: str,
                       scope: str, period: str) -> bytes:
    """Draw a reporting-ready PNG from exactly the figures in live chart tables.

    Count mode adds unmatched G5 doses as a separate purple segment; percentage
    mode excludes them from the denominator and calls them out in text instead.
    """
    percent_mode = mode == "Coverage"
    work = frame.copy()
    for name in ("Vaccinated", "Remaining Target", "Coverage %", "Total Target"):
        if name not in work.columns:
            work[name] = np.nan if name in ("Coverage %", "Total Target") else 0
        work[name] = pd.to_numeric(work[name], errors="coerce")
    if is_g5:
        if "Unmatched Vaccinations" not in work.columns:
            work["Unmatched Vaccinations"] = 0
        work["Unmatched Vaccinations"] = pd.to_numeric(
            work["Unmatched Vaccinations"], errors="coerce"
        ).fillna(0)
    else:
        work["Unmatched Vaccinations"] = 0

    if percent_mode:
        work = work.sort_values(
            ["Coverage %", "Vaccinated", "Category"],
            ascending=[False, False, True], na_position="last",
        ).reset_index(drop=True)
    else:
        order = "Total Vaccinated" if is_g5 and "Total Vaccinated" in work.columns else "Vaccinated"
        work = work.sort_values(
            [order, "Coverage %", "Category"],
            ascending=[False, False, True], na_position="last",
        ).reset_index(drop=True)

    work["valid_target"] = work["Total Target"].gt(0)
    work["blue"] = work["Coverage %"].clip(lower=0).fillna(0) if percent_mode else work["Vaccinated"].clip(lower=0).fillna(0)
    work["orange"] = (
        (100.0 - work["Coverage %"]).clip(lower=0).fillna(0)
        if percent_mode else work["Remaining Target"].clip(lower=0).fillna(0)
    )
    # An absent/zero target is never represented as 0% coverage.
    if percent_mode:
        work.loc[~work["valid_target"], ["blue", "orange"]] = 0
    work["purple"] = 0 if percent_mode else work["Unmatched Vaccinations"].clip(lower=0)

    n = len(work)
    bar_pitch = 78
    graph_top = 390
    axis_h = 125
    height = max(900, graph_top + n * bar_pitch + axis_h)
    im = Image.new("RGB", (_IMAGE_W, height), _BG)
    draw = ImageDraw.Draw(im)
    x_left, x_right = 485, _IMAGE_W - 96
    plot_width = x_right - x_left
    chart_title = "SBI 2026 | Grade 5 HPV Vaccinated vs Remaining Target" if is_g5 else "SBI 2026 | Vaccinated vs Remaining Target"

    _txt(draw, chart_title, 74, 42, size=42, bold=True)
    _txt(draw, f"Antigen: {antigen}", 74, 108, size=39, bold=True, color=_TEAL)
    _txt(draw, f"Display: {'Coverage (%)' if percent_mode else 'Vaccination Count'}", 74, 168, size=25, bold=True)
    _txt(draw, f"Accomplishment source: {source}  |  Target basis: {target_basis}", 74, 207, size=25)
    _txt(draw, f"Scope: {scope}", 74, 245, size=25)
    _txt(draw, f"Reporting period: {period}", 74, 282, size=25)
    if is_g5:
        note = "Unmatched G5 HPV vaccinations are shown separately and excluded from target-based coverage."
    else:
        note = "Remaining Target = max(Target - Vaccinated, 0); target-based coverage excludes unavailable targets."
    _txt(draw, note, 74, 320, size=22, color=_MUTED)

    legend_y = graph_top - 30
    legend_items = [("Vaccinated (matched to target)" if is_g5 else "Vaccinated", _BLUE),
                    ("Remaining Target", _ORANGE)]
    if is_g5 and not percent_mode:
        legend_items.append(("Unmatched vaccinations", _PURPLE))
    legend_x = x_left
    for label, color in legend_items:
        draw.rectangle((legend_x, legend_y, legend_x + 20, legend_y + 20), fill=color)
        legend_x += 29
        _txt(draw, label, legend_x, legend_y - 2, size=20)
        legend_x += int(draw.textbbox((0, 0), label, font=_font(20))[2]) + 46

    max_stack = float((work["blue"] + work["orange"] + work["purple"]).max()) if n else 0
    if percent_mode:
        x_max = max(100, ceil(max_stack / 10) * 10)
        step = 20 if x_max <= 120 else _nice_tick(x_max)
    else:
        step = _nice_tick(max(1, max_stack))
        x_max = max(step, ceil(max_stack / step) * step)

    # Grid lines and x-axis ticks.
    bottom_y = graph_top + n * bar_pitch
    tick = 0.0
    while tick <= x_max + 0.001:
        xx = x_left + int(tick / x_max * plot_width)
        draw.line((xx, graph_top - 3, xx, bottom_y + 9), fill=_GRID, width=2)
        label = f"{tick:g}%" if percent_mode else _fmt_count(tick)
        _txt(draw, label, xx, bottom_y + 35, size=21, color=_MUTED, anchor="ma")
        tick += step

    for i, row in work.iterrows():
        cy = graph_top + i * bar_pitch + 28
        top, bottom = cy - 24, cy + 24
        category = str(row["Category"])
        _txt(draw, category, x_left - 30, cy, size=23, color=_MUTED,
             anchor="rm", max_width=x_left - 98)
        blue = float(row["blue"])
        orange = float(row["orange"])
        purple = float(row["purple"])
        valid = bool(row["valid_target"])
        if not valid and percent_mode:
            _txt(draw, "N/A — target unavailable", x_left + 12, cy,
                 size=22, color=_MUTED, anchor="lm")
            # Unmatched vaccinations still get explicit visibility in % view.
        segments = (("Vaccinated", blue, _BLUE), ("Remaining", orange, _ORANGE),
                    ("Unmatched", purple, _PURPLE))
        pos = 0.0
        small_labels: list[str] = []
        for label, value, color in segments:
            if value <= 0:
                continue
            start_x = x_left + round(pos / x_max * plot_width)
            pos += value
            end_x = x_left + round(pos / x_max * plot_width)
            if end_x > start_x:
                draw.rectangle((start_x, top, end_x, bottom), fill=color)
            written = f"{value:.1f}%" if percent_mode else _fmt_count(value)
            available = end_x - start_x
            required = draw.textbbox((0, 0), written, font=_font(21, bold=True))[2] + 25
            if available >= max(55, required):
                _txt(draw, written, (start_x + end_x) // 2, cy,
                     size=21, bold=True, color="#ffffff", anchor="mm")
            else:
                small_labels.append(f"{label}: {written}")
        # Small segments need labels even when next to very wide bars. Put the
        # callout ABOVE the bar, not inside another colored segment.
        if small_labels:
            label = " | ".join(small_labels)
            fn = _font(19, bold=True)
            box = draw.textbbox((0, 0), label, font=fn)
            bw = box[2] + 19
            label_x = min(x_right - bw, max(x_left + 6, x_left + round(pos / x_max * plot_width) + 9))
            label_y = top - 25
            draw.rounded_rectangle((label_x, label_y, label_x + bw, label_y + 26), radius=4,
                                   fill="#ffffff", outline="#b9c9d4", width=1)
            _txt(draw, label, label_x + 9, label_y + 2, size=19, bold=True, color=_TEAL)
        if is_g5 and percent_mode and float(row["Unmatched Vaccinations"]) > 0:
            note = f"+{_fmt_count(float(row['Unmatched Vaccinations']))} unmatched"
            start_note = x_left + round(min(pos, x_max) / x_max * plot_width) + 12
            note_w = draw.textbbox((0, 0), note, font=_font(19, bold=True))[2]
            # A margin badge avoids interpreting the unmatched count as coverage.
            badge_x = min(x_right - note_w - 20, max(x_left + 12, start_note))
            draw.rounded_rectangle((badge_x, top - 27, badge_x + note_w + 16, top - 1),
                                   radius=4, fill="#f2e9f8", outline="#c9b0d9")
            _txt(draw, note, badge_x + 8, top - 26, size=19, bold=True, color=_PURPLE)

    units = "Coverage of selected target (%)" if percent_mode else "Number of students"
    _txt(draw, units, (x_left + x_right) // 2, height - 45, size=26, color=_MUTED, anchor="ma")
    out = BytesIO()
    im.save(out, format="PNG", optimize=True, dpi=(300, 300))
    return out.getvalue()


@st.cache_data(ttl=300, show_spinner="Preparing all 9 high-resolution SBI PNG reports...")
def _zip_nine_reports(
    recon: pd.DataFrame,
    recon_context: dict,
    g1_mr: pd.DataFrame,
    g7_mr: pd.DataFrame,
    g4_hpv: pd.DataFrame,
    g5_hpv: pd.DataFrame,
    source: str,
    target_basis: str,
    scope: str,
    period: str,
    start_date: date | None,
    end_date: date | None,
) -> bytes:
    archive = BytesIO()
    with ZipFile(archive, mode="w", compression=ZIP_DEFLATED, compresslevel=5) as zipfile:
        zipfile.writestr(
            "SBI_Municipality_Reconciliation_Abra.png",
            _grouped_reconciliation_png(recon, recon_context, start_date, end_date),
        )
        reports = [
            ("SBI_G5_Vaccinated_vs_Remaining_Coverage.png", g5_hpv, "HPV (Grade 5)", True, "Coverage"),
            ("SBI_G5_Vaccinated_vs_Remaining_Count.png", g5_hpv, "HPV (Grade 5)", True, "Count"),
            ("SBI_Vaccinated_vs_Remaining_MR_Grade_7_Coverage.png", g7_mr, "MR (Grade 7)", False, "Coverage"),
            ("SBI_Vaccinated_vs_Remaining_MR_Grade_7_Count.png", g7_mr, "MR (Grade 7)", False, "Count"),
            ("SBI_Vaccinated_vs_Remaining_HPV_Grade_4_Count.png", g4_hpv, "HPV (Grade 4)", False, "Count"),
            ("SBI_Vaccinated_vs_Remaining_HPV_Grade_4_Coverage.png", g4_hpv, "HPV (Grade 4)", False, "Coverage"),
            ("SBI_Vaccinated_vs_Remaining_MR_Grade_1_Coverage.png", g1_mr, "MR (Grade 1)", False, "Coverage"),
            ("SBI_Vaccinated_vs_Remaining_MR_Grade_1_Count.png", g1_mr, "MR (Grade 1)", False, "Count"),
        ]
        for name, frame, antigen, is_g5, mode in reports:
            data = _make_coverage_png(
                frame, antigen=antigen, is_g5=is_g5, mode=mode,
                source="RHU Workbook (operational/provisional)" if is_g5 else source,
                target_basis="Actual Targets (Unvaccinated G5 Female)" if is_g5 else target_basis,
                scope=scope, period=period,
            )
            zipfile.writestr(name, data)
    return archive.getvalue()


def render_reporting_bundle_download(
    *, supabase,
    actual_targets: pd.DataFrame,
    selected_targets: pd.DataFrame,
    source_label: str,
    target_basis: str,
    scope_municipality: str | None,
    period: str,
    start_date: date | None,
    end_date: date | None,
    g1_source: pd.DataFrame,
    g7_source: pd.DataFrame,
    g4_source: pd.DataFrame,
    g1_vacctrack: pd.DataFrame,
    g7_vacctrack: pd.DataFrame,
    g4_vacctrack: pd.DataFrame,
    build_exec_rows,
) -> None:
    """Show a single ready-to-download ZIP button in the Executive Summary."""
    st.markdown("#### One-click SBI Reporting Pack")
    st.caption(
        "Download the 9 PNGs usually prepared for reporting: municipality reconciliation; "
        "Grade 5 HPV count + coverage; and Grade 1 MR, Grade 7 MR, and Grade 4 HPV count + coverage. "
        "The reporting period and Executive Summary source/target selections are applied automatically."
    )
    try:
        # Fetch once for all nine images. Reconciliation ALWAYS uses workbook
        # against VaccTrack; G5 ALWAYS uses workbook vs its own actual targets.
        workbook = fetch_workbook_dashboard_entries(supabase)
        g5_all, _ = prepare_workbook_entries(
            workbook, start_date=start_date, end_date=end_date,
            municipality=scope_municipality,
        )
        g5_only = (
            g5_all.loc[g5_all["grade_level"].eq("G5")].copy()
            if isinstance(g5_all, pd.DataFrame) and not g5_all.empty
            else pd.DataFrame()
        )
        for col in ("hpv_dose1", "hpv_dose2"):
            if col not in g5_only.columns:
                g5_only[col] = 0
            g5_only[col] = pd.to_numeric(g5_only[col], errors="coerce").fillna(0)
        g5_targets, _ = _prepare_g5_targets(actual_targets, scope_municipality)
        g5_figures = _build_g5_remaining_rows(g5_only, g5_targets, scope_municipality)

        antigen_frames = {}
        for antigen in ("MR (Grade 1)", "MR (Grade 7)", "HPV (Grade 4)"):
            antigen_frames[antigen] = build_exec_rows(
                selected_targets,
                {"g1": g1_source, "g7": g7_source, "g4": g4_source},
                antigen,
                municipality=scope_municipality,
            )
        if not all(not f.empty for f in antigen_frames.values()):
            st.warning("Reporting pack requires target data for the three G1/G7/G4 charts.")
            return
        municipalities = [scope_municipality] if scope_municipality else ABRA_MUNIS
        recon, recon_context = _grouped_reconciliation_rows(
            workbook, g1_vacctrack, g7_vacctrack, g4_vacctrack,
            municipalities, start_date, end_date,
        )
        recon, recon_context = _with_reconciliation_total(recon, recon_context)
        scope = (
            "All Municipalities (Abra)" if scope_municipality is None
            else f"{scope_municipality}, Abra (by School for charts)"
        )
        zip_bytes = _zip_nine_reports(
            recon, recon_context,
            antigen_frames["MR (Grade 1)"],
            antigen_frames["MR (Grade 7)"],
            antigen_frames["HPV (Grade 4)"],
            g5_figures,
            source_label, target_basis, scope, period,
            start_date, end_date,
        )
        safe_period = (
            f"{start_date:%Y%m%d}_{end_date:%Y%m%d}"
            if start_date and end_date else "Selected_Period"
        )
        st.download_button(
            "Download All 9 Reporting PNGs (ZIP)",
            data=zip_bytes,
            file_name=f"SBI_2026_Reporting_Pack_{safe_period}.zip",
            mime="application/zip",
            type="primary",
            key="sbi_reporting_pack_zip",
            width="stretch",
            help="One ZIP containing 9 high-resolution PNGs generated from the dashboard's current data.",
        )
    except Exception as exc:
        st.error(f"Unable to prepare the SBI reporting pack: {exc}")
