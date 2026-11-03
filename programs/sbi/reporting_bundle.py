"""Custom SBI PNG reporting pack, including all visible plot-exportable SBI figures.

The nine established reporting PNGs use the established server-side renderers.
Additional reports are captured directly from the data plotted by the existing
SBI dashboard tabs and rendered with Pillow; no Chrome or Kaleido is required.
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


# The order is the user's established reporting routine. Keys stay stable so
# checkbox states persist across Streamlit reruns.
_REPORT_OPTIONS = (
    ("reconciliation", "Municipality Reconciliation", "Municipality Reconciliation"),
    ("g5_coverage", "Coverage (%)", "Grade 5 HPV"),
    ("g5_count", "Vaccination Count", "Grade 5 HPV"),
    ("g7_coverage", "Coverage (%)", "Grade 7 MR"),
    ("g7_count", "Vaccination Count", "Grade 7 MR"),
    ("g4_count", "Vaccination Count", "Grade 4 HPV"),
    ("g4_coverage", "Coverage (%)", "Grade 4 HPV"),
    ("g1_coverage", "Coverage (%)", "Grade 1 MR"),
    ("g1_count", "Vaccination Count", "Grade 1 MR"),
)

_REPORT_SPECS = {
    "g5_coverage": ("SBI_G5_Vaccinated_vs_Remaining_Coverage.png", "g5", "HPV (Grade 5)", True, "Coverage"),
    "g5_count": ("SBI_G5_Vaccinated_vs_Remaining_Count.png", "g5", "HPV (Grade 5)", True, "Count"),
    "g7_coverage": ("SBI_Vaccinated_vs_Remaining_MR_Grade_7_Coverage.png", "g7", "MR (Grade 7)", False, "Coverage"),
    "g7_count": ("SBI_Vaccinated_vs_Remaining_MR_Grade_7_Count.png", "g7", "MR (Grade 7)", False, "Count"),
    "g4_count": ("SBI_Vaccinated_vs_Remaining_HPV_Grade_4_Count.png", "g4", "HPV (Grade 4)", False, "Count"),
    "g4_coverage": ("SBI_Vaccinated_vs_Remaining_HPV_Grade_4_Coverage.png", "g4", "HPV (Grade 4)", False, "Coverage"),
    "g1_coverage": ("SBI_Vaccinated_vs_Remaining_MR_Grade_1_Coverage.png", "g1", "MR (Grade 1)", False, "Coverage"),
    "g1_count": ("SBI_Vaccinated_vs_Remaining_MR_Grade_1_Count.png", "g1", "MR (Grade 1)", False, "Count"),
}


@st.cache_data(ttl=300, show_spinner=False)
def _zip_selected_reports(
    selected: tuple[str, ...],
    recon: pd.DataFrame | None,
    recon_context: dict | None,
    g1_mr: pd.DataFrame | None,
    g7_mr: pd.DataFrame | None,
    g4_hpv: pd.DataFrame | None,
    g5_hpv: pd.DataFrame | None,
    source: str,
    target_basis: str,
    scope: str,
    period: str,
    start_date: date | None,
    end_date: date | None,
    scope_municipality: str | None,
    extra_pngs: dict[str, bytes] | None = None,
) -> bytes:
    """Create exactly the selected images. Unselected reports are not rendered."""
    if not selected:
        raise ValueError("At least one reporting image must be selected.")
    frames = {"g1": g1_mr, "g7": g7_mr, "g4": g4_hpv, "g5": g5_hpv}
    archive = BytesIO()
    with ZipFile(archive, mode="w", compression=ZIP_DEFLATED, compresslevel=5) as zipfile:
        for key in selected:
            if key.startswith("chart:"):
                chart_key = key.split(":", 1)[1]
                if not extra_pngs or chart_key not in extra_pngs:
                    raise ValueError(f"Selected SBI chart is not currently available: {chart_key}")
                clean_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in chart_key).strip("_")
                zipfile.writestr(f"SBI_Extra_{clean_name}.png", extra_pngs[chart_key])
                continue
            if key == "reconciliation":
                if recon is None or recon_context is None:
                    raise ValueError("Reconciliation data are unavailable.")
                suffix = "Abra" if scope_municipality is None else scope_municipality.replace(" ", "_")
                zipfile.writestr(
                    f"SBI_Municipality_Reconciliation_{suffix}.png",
                    _grouped_reconciliation_png(recon, recon_context, start_date, end_date),
                )
                continue
            if key not in _REPORT_SPECS:
                raise ValueError(f"Unknown report selection: {key}")
            filename, frame_key, antigen, is_g5, mode = _REPORT_SPECS[key]
            frame = frames[frame_key]
            if frame is None or frame.empty:
                raise ValueError(f"No data are available to create {filename}.")
            png = _make_coverage_png(
                frame, antigen=antigen, is_g5=is_g5, mode=mode,
                source="RHU Workbook (operational/provisional)" if is_g5 else source,
                target_basis="Actual Targets (Unvaccinated G5 Female)" if is_g5 else target_basis,
                scope=scope, period=period,
            )
            zipfile.writestr(filename, png)
    return archive.getvalue()


def _all_report_options(captured: dict) -> list[tuple[str, str, str]]:
    """All current SBI charts beyond the default nine; optional by default."""
    excluded = {"sbi_exec_vaccinated_vs_remaining", "sbi_g5_vaccinated_remaining"}
    options = list(_REPORT_OPTIONS)
    for chart_key, record in sorted(captured.items()):
        if chart_key in excluded:
            continue  # These are already represented by the original nine options.
        key = f"chart:{chart_key}"
        label = record.get("label") or chart_key.replace("_", " ")
        if chart_key.startswith(("sbi_exec_",)):
            group = "Executive Summary — Other Charts"
        elif chart_key.startswith(("sbi_tgt_", "sbi_actual_", "sbi_compare_")) or any(
            word in chart_key.lower() for word in ("target", "baseline")
        ):
            group = "Targets Overview — Other Charts"
        elif chart_key.startswith("sbi_g5_"):
            group = "G5 Dashboard — Other Charts"
        elif chart_key.startswith("sbi_workbook_"):
            group = "Workbook Dashboard — Other Charts"
        elif chart_key.startswith("sbi_"):
            group = "VaccTrack Dashboard — Other Charts"
        else:
            group = "Additional SBI Charts"
        options.append((key, label, group))
    return options


def _current_selection(options: list[tuple[str, str, str]]) -> tuple[str, ...]:
    """Read checkbox widgets in stable order, nine default on; others default off."""
    original_keys = {key for key, _, _ in _REPORT_OPTIONS}
    return tuple(
        key for key, _label, _group in options
        if bool(st.session_state.get(f"sbi_pack_report_{key}", key in original_keys))
    )


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
    """Choose, prepare and download PNGs for any current SBI dashboard figure."""
    from programs.sbi.chart_capture import get_captured_reports
    captured = get_captured_reports()
    options = _all_report_options(captured)
    original_keys = {key for key, _, _ in _REPORT_OPTIONS}
    extra_count = len(options) - len(_REPORT_OPTIONS)

    st.markdown("#### Choose reports to include")
    st.caption(
        "The original nine reports are checked by default. All other currently "
        "available SBI PNG charts are listed below and initially unchecked. "
        "Only selected reports will be generated."
    )
    st.caption(f"{extra_count} additional SBI chart(s) found in this dashboard view.")

    # Controls precede checkboxes; Streamlit prohibits modifying instantiated
    # widget keys later in the same run.
    a,b,c,d = st.columns([1,1,1,2])
    with a:
        select_all = st.button("Select All",key="sbi_pack_select_all",width="stretch")
    with b:
        select_original = st.button("Original Nine",key="sbi_pack_original_nine",width="stretch")
    with c:
        clear_all = st.button("Clear All",key="sbi_pack_clear_all",width="stretch")
    if select_all or select_original or clear_all:
        for key, _label, _group in options:
            st.session_state[f"sbi_pack_report_{key}"] = (
                bool(select_all) or (bool(select_original) and key in original_keys)
            )

    from collections import OrderedDict
    grouped = OrderedDict()
    group_order = [
        "Municipality Reconciliation", "Grade 1 MR", "Grade 7 MR",
        "Grade 4 HPV", "Grade 5 HPV",
        "Executive Summary — Other Charts", "Targets Overview — Other Charts",
        "Workbook Dashboard — Other Charts", "G5 Dashboard — Other Charts",
        "VaccTrack Dashboard — Other Charts", "Additional SBI Charts",
    ]
    for group in group_order:
        grouped[group] = []
    for item in options:
        grouped.setdefault(item[2], []).append(item)
    for group, entries in grouped.items():
        if not entries:
            continue
        with st.expander(f"{group} ({len(entries)})", expanded=group in {
            "Municipality Reconciliation", "Grade 1 MR", "Grade 7 MR", "Grade 4 HPV", "Grade 5 HPV",
        }):
            cols = st.columns(2)
            for i,(key,label,_group) in enumerate(entries):
                with cols[i%2]:
                    widget_key=f"sbi_pack_report_{key}"
                    if widget_key not in st.session_state:
                        st.session_state[widget_key] = key in original_keys
                    st.checkbox(label,key=widget_key)
    selected = _current_selection(options)
    with d:
        st.metric("Reports selected", f"{len(selected)} / {len(options)}")
    st.caption(
        "G5 always uses RHU workbook accomplishments and Actual G5 targets. "
        "Municipality Reconciliation always compares RHU workbooks against VaccTrack. "
        "The source and target basis above apply to Grade 1, Grade 7 and Grade 4 charts."
    )
    if not selected:
        st.info("Select at least one report to prepare a ZIP.")
        return

    # Cache a prepared bundle for repeated downloads, but invalidate it when
    # selection or reporting context changes. We deliberately do not build
    # eight large PNGs on every unrelated dashboard rerun.
    signature = (
        selected, str(scope_municipality), str(period), str(start_date), str(end_date),
        str(source_label), str(target_basis),
    )
    prepare = st.button(
        f"Prepare {len(selected)} Selected Report{'s' if len(selected) != 1 else ''}",
        key="sbi_pack_prepare", type="primary", width="stretch",
    )
    if prepare:
        try:
            with st.spinner("Generating selected high-resolution PNG reports..."):
                from programs.sbi.chart_capture import render_captured_png
                extra_pngs = {}
                scope = (
                    "All Municipalities (Abra)" if scope_municipality is None
                    else f"{scope_municipality}, Abra (by School for charts)"
                )
                for key in selected:
                    if key.startswith("chart:"):
                        chart_key = key.split(":",1)[1]
                        if chart_key not in captured:
                            raise ValueError(f"Chart {chart_key} is not available with the current filters.")
                        extra_pngs[chart_key] = render_captured_png(captured[chart_key],scope,period)

                needs_g5 = any(key.startswith("g5_") for key in selected)
                needs_recon = "reconciliation" in selected
                workbook = (
                    fetch_workbook_dashboard_entries(supabase)
                    if needs_g5 or needs_recon else pd.DataFrame()
                )
                g5_figures = None
                if needs_g5:
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

                frames = {"g1": None, "g7": None, "g4": None}
                for grade, antigen in (
                    ("g1", "MR (Grade 1)"),
                    ("g7", "MR (Grade 7)"),
                    ("g4", "HPV (Grade 4)"),
                ):
                    if any(_REPORT_SPECS.get(key, (None, None))[1] == grade for key in selected):
                        frames[grade] = build_exec_rows(
                            selected_targets,
                            {"g1": g1_source, "g7": g7_source, "g4": g4_source},
                            antigen,
                            municipality=scope_municipality,
                        )

                recon = recon_context = None
                if needs_recon:
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
                payload = _zip_selected_reports(
                    selected, recon, recon_context,
                    frames["g1"], frames["g7"], frames["g4"], g5_figures,
                    source_label, target_basis, scope, period,
                    start_date, end_date, scope_municipality,
                    extra_pngs=extra_pngs,
                )
                st.session_state["sbi_pack_prepared"] = {"signature": signature, "data": payload}
        except Exception as exc:
            st.session_state.pop("sbi_pack_prepared", None)
            st.error(f"Unable to prepare selected SBI reports: {exc}")

    prepared = st.session_state.get("sbi_pack_prepared")
    if isinstance(prepared, dict) and prepared.get("signature") == signature:
        safe_period = (
            f"{start_date:%Y%m%d}_{end_date:%Y%m%d}"
            if start_date and end_date else "Selected_Period"
        )
        st.download_button(
            f"Download {len(selected)} Selected PNG{'s' if len(selected) != 1 else ''} (ZIP)",
            data=prepared["data"],
            file_name=f"SBI_2026_Reporting_Pack_{safe_period}_{len(selected)}_PNGs.zip",
            mime="application/zip", type="primary",
            key="sbi_reporting_pack_zip", width="stretch",
        )
        st.caption("Prepared ZIP is ready. To include newer uploaded data, click Prepare again.")
