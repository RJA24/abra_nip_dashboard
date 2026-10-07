"""Shared layout helpers for the SBI Workbook and VaccTrack dashboards.

Both dashboards use the same visual outline.  This module intentionally owns
only the source-agnostic KPI header and the two cross-program overview charts;
program-specific charts remain in their existing dashboard renderers.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from core.config import ABRA_MUNIS
from programs.sbi.analytics import reason_summary


def _numeric_sum(frame: pd.DataFrame, column: str) -> float:
    if frame is None or frame.empty or column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())


def _target_sum(targets: pd.DataFrame, column: str) -> float:
    if targets is None or targets.empty or column not in targets.columns:
        return 0.0
    return float(pd.to_numeric(targets[column], errors="coerce").fillna(0).sum())


def _schools_reporting(frame: pd.DataFrame) -> int:
    if frame is None or frame.empty or "School ID" not in frame.columns:
        return 0
    school_ids = frame["School ID"].fillna("").astype(str).str.strip()
    return int(school_ids.loc[school_ids.ne("")].nunique())


def _combined_school_count(*frames: pd.DataFrame) -> int:
    values: list[pd.Series] = []
    for frame in frames:
        if frame is None or frame.empty or "School ID" not in frame.columns:
            continue
        school_ids = frame["School ID"].fillna("").astype(str).str.strip()
        values.append(school_ids.loc[school_ids.ne("")])
    if not values:
        return 0
    return int(pd.concat(values, ignore_index=True).nunique())


def _rhus_reporting(*frames: pd.DataFrame) -> int:
    values: list[pd.Series] = []
    for frame in frames:
        if frame is None or frame.empty or "Municipality" not in frame.columns:
            continue
        municipalities = frame["Municipality"].fillna("").astype(str).str.strip()
        values.append(municipalities.loc[municipalities.ne("")])
    if not values:
        return 0
    return int(pd.concat(values, ignore_index=True).nunique())


def _latest_report_date(*frames: pd.DataFrame):
    dates: list[pd.Series] = []
    for frame in frames:
        if frame is None or frame.empty or "Report Date" not in frame.columns:
            continue
        parsed = pd.to_datetime(frame["Report Date"], errors="coerce").dropna()
        if not parsed.empty:
            dates.append(parsed)
    if not dates:
        return None
    return pd.concat(dates, ignore_index=True).max()


def _render_mr_td_kpi_group(
    title: str,
    events: pd.DataFrame,
    target_total: float,
    row_label: str,
) -> None:
    st.caption(title)
    mr_doses = _numeric_sum(events, "MR Doses")
    td_doses = _numeric_sum(events, "Td Doses")
    mr_cov = (mr_doses / target_total * 100) if target_total > 0 else 0.0
    td_cov = (td_doses / target_total * 100) if target_total > 0 else 0.0
    schools = _schools_reporting(events)
    rows = len(events) if events is not None else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Target", f"{target_total:,.0f}")
    c2.metric("MR Vaccinated", f"{mr_doses:,.0f}", f"{mr_cov:.1f}% coverage", delta_color="off")
    c3.metric("Td Vaccinated", f"{td_doses:,.0f}", f"{td_cov:.1f}% coverage", delta_color="off")
    c4.metric("Schools Reporting", f"{schools:,}", f"{rows:,} {row_label.lower()}", delta_color="off")


def render_source_kpi_summary(
    g1_events: pd.DataFrame,
    g7_events: pd.DataFrame,
    hpv_events: pd.DataFrame,
    targets: pd.DataFrame,
    *,
    all_municipalities: bool,
    key_prefix: str,
    row_label: str,
    latest_label: str,
) -> None:
    """Render every KPI card before the first chart for a source dashboard."""
    st.markdown("#### Dashboard KPIs")
    overview_tab, mrtd_tab, hpv_tab, missed_tab = st.tabs([
        "Overview",
        "MR & Td",
        "HPV",
        "Deferrals & Refusals",
    ])

    total_rows = sum(len(frame) for frame in (g1_events, g7_events, hpv_events) if frame is not None)
    latest = _latest_report_date(g1_events, g7_events, hpv_events)

    with overview_tab:
        o1, o2, o3, o4 = st.columns(4)
        reporting_rhus = _rhus_reporting(g1_events, g7_events, hpv_events)
        reporting_text = f"{reporting_rhus:,}" + (" / 27" if all_municipalities else "")
        o1.metric("RHUs Reporting", reporting_text)
        o2.metric("Schools Reporting", f"{_combined_school_count(g1_events, g7_events, hpv_events):,}")
        o3.metric(row_label, f"{total_rows:,}")
        o4.metric(latest_label, latest.strftime("%b %d, %Y") if latest is not None else "—")

    with mrtd_tab:
        g1_target = _target_sum(targets, "G1 Target")
        g7_target = _target_sum(targets, "G7 Target")
        combined = pd.concat([g1_events, g7_events], ignore_index=True, sort=False)
        _render_mr_td_kpi_group(
            "Combined Grades 1 & 7",
            combined,
            g1_target + g7_target,
            row_label,
        )
        _render_mr_td_kpi_group("Grade 1", g1_events, g1_target, row_label)
        _render_mr_td_kpi_group("Grade 7", g7_events, g7_target, row_label)

    with hpv_tab:
        hpv_target = _target_sum(targets, "G4 Target")
        dose1 = _numeric_sum(hpv_events, "HPV Dose 1")
        dose2 = _numeric_sum(hpv_events, "HPV Dose 2")
        cov1 = (dose1 / hpv_target * 100) if hpv_target > 0 else 0.0
        cov2 = (dose2 / hpv_target * 100) if hpv_target > 0 else 0.0
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Grade 4 Female Target", f"{hpv_target:,.0f}")
        h2.metric("HPV 1st Dose", f"{dose1:,.0f}", f"{cov1:.1f}% coverage", delta_color="off")
        h3.metric("HPV 2nd Dose", f"{dose2:,.0f}", f"{cov2:.1f}% coverage", delta_color="off")
        h4.metric(
            "Schools Reporting",
            f"{_schools_reporting(hpv_events):,}",
            f"{len(hpv_events) if hpv_events is not None else 0:,} {row_label.lower()}",
            delta_color="off",
        )

    with missed_tab:
        total_mr_deferred = _numeric_sum(g1_events, "MR Deferred") + _numeric_sum(g7_events, "MR Deferred")
        total_td_deferred = _numeric_sum(g1_events, "Td Deferred") + _numeric_sum(g7_events, "Td Deferred")
        total_hpv_deferred = _numeric_sum(hpv_events, "HPV Deferred 1") + _numeric_sum(hpv_events, "HPV Deferred 2")
        total_mr_refused = _numeric_sum(g1_events, "MR Refused") + _numeric_sum(g7_events, "MR Refused")
        total_td_refused = _numeric_sum(g1_events, "Td Refused") + _numeric_sum(g7_events, "Td Refused")
        total_hpv_refused = _numeric_sum(hpv_events, "HPV Refused 1") + _numeric_sum(hpv_events, "HPV Refused 2")
        reasons_df = reason_summary(g1_events, g7_events, hpv_events)
        reason_records = (
            float(pd.to_numeric(reasons_df["Count"], errors="coerce").fillna(0).sum())
            if reasons_df is not None and not reasons_df.empty
            else 0.0
        )

        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Total Deferred", f"{total_mr_deferred + total_td_deferred + total_hpv_deferred:,.0f}")
        d2.metric("Total Refused", f"{total_mr_refused + total_td_refused + total_hpv_refused:,.0f}")
        d3.metric("Recorded Missed-Vaccination Reasons", f"{reason_records:,.0f}")
        d4.metric(row_label, f"{total_rows:,}")


def render_activity_overview(
    g1_events: pd.DataFrame,
    g7_events: pd.DataFrame,
    hpv_events: pd.DataFrame,
    *,
    all_municipalities: bool,
    key_prefix: str,
    date_axis_title: str,
) -> None:
    """Render the two shared source-overview charts in a fixed order."""
    if all(
        frame is None or frame.empty
        for frame in (g1_events, g7_events, hpv_events)
    ):
        return

    if all_municipalities:
        municipality_parts: list[pd.DataFrame] = []
        for frame, series in [
            (g1_events, [("MR Doses", "G1 MR"), ("Td Doses", "G1 Td")]),
            (hpv_events, [("HPV Dose 1", "G4 HPV1"), ("HPV Dose 2", "G4 HPV2")]),
            (g7_events, [("MR Doses", "G7 MR"), ("Td Doses", "G7 Td")]),
        ]:
            if frame is None or frame.empty or "Municipality" not in frame.columns:
                continue
            valid_series = [(column, label) for column, label in series if column in frame.columns]
            if not valid_series:
                continue
            grouped = frame.groupby("Municipality", dropna=False)[[column for column, _ in valid_series]].sum().reset_index()
            for column, label in valid_series:
                part = grouped[["Municipality", column]].rename(columns={column: "Vaccinated"}).copy()
                part["Indicator"] = label
                municipality_parts.append(part)

        if municipality_parts:
            municipality_chart = pd.concat(municipality_parts, ignore_index=True)
            municipality_chart["Vaccinated"] = pd.to_numeric(
                municipality_chart["Vaccinated"], errors="coerce"
            ).fillna(0)
            municipality_chart = municipality_chart.loc[municipality_chart["Vaccinated"].gt(0)].copy()
            if not municipality_chart.empty:
                st.markdown("#### Vaccination Accomplishments by Municipality")
                fig_muni = px.bar(
                    municipality_chart,
                    x="Vaccinated",
                    y="Municipality",
                    color="Indicator",
                    orientation="h",
                    barmode="group",
                    text_auto=".0f",
                )
                fig_muni.update_layout(
                    dragmode=False,
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis_title="Vaccinated",
                    yaxis_title="",
                    height=max(460, len(ABRA_MUNIS) * 30),
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.08,
                        xanchor="center",
                        x=0.5,
                    ),
                    legend_title_text="",
                )
                st.plotly_chart(
                    fig_muni,
                    width="stretch",
                    key=f"{key_prefix}_muni_accomplishments_chart",
                )

    daily_parts: list[pd.DataFrame] = []
    for frame, series in [
        (g1_events, [("MR Doses", "G1 MR"), ("Td Doses", "G1 Td")]),
        (hpv_events, [("HPV Dose 1", "G4 HPV1"), ("HPV Dose 2", "G4 HPV2")]),
        (g7_events, [("MR Doses", "G7 MR"), ("Td Doses", "G7 Td")]),
    ]:
        if frame is None or frame.empty or "Report Date" not in frame.columns:
            continue
        valid_cols = [column for column, _ in series if column in frame.columns]
        if not valid_cols:
            continue
        part = frame[["Report Date", *valid_cols]].copy()
        part["Report Date"] = pd.to_datetime(part["Report Date"], errors="coerce")
        part = part.dropna(subset=["Report Date"])
        for column in valid_cols:
            part[column] = pd.to_numeric(part[column], errors="coerce").fillna(0)
        part = part.groupby("Report Date", as_index=False)[valid_cols].sum()
        rename_map = {column: label for column, label in series if column in valid_cols}
        part = part.rename(columns=rename_map).melt(
            id_vars=["Report Date"],
            value_vars=list(rename_map.values()),
            var_name="Indicator",
            value_name="Vaccinated",
        )
        daily_parts.append(part)

    if daily_parts:
        daily_chart = pd.concat(daily_parts, ignore_index=True)
        daily_chart = (
            daily_chart.groupby(["Report Date", "Indicator"], as_index=False)["Vaccinated"]
            .sum()
            .sort_values("Report Date")
        )
        if not daily_chart.empty:
            st.markdown("#### Daily Activity Trend")
            fig_daily = px.line(
                daily_chart,
                x="Report Date",
                y="Vaccinated",
                color="Indicator",
                markers=True,
            )
            fig_daily.update_layout(
                dragmode=False,
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title=date_axis_title,
                yaxis_title="Vaccinated",
                legend=dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.12,
                    xanchor="center",
                    x=0.5,
                ),
                legend_title_text="",
            )
            st.plotly_chart(
                fig_daily,
                width="stretch",
                key=f"{key_prefix}_daily_activity_overview_chart",
            )
