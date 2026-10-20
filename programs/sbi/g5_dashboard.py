"""Grade 5 HPV catch-up dashboard sourced from RHU aggregate workbooks.

Grade 5 is intentionally kept separate from the official VaccTrack G1/G4/G7
analytics.  The denominator is the RHU-entered ``Unvaccinated G5 Female``
column from the Actual Targets worksheet; accomplishments come only from G5
rows in the offline RHU workbook.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from core.config import ABRA_MUNIS
from core.map_labels import canonical_municipality_name, normalize_municipality_key
from programs.sbi.aggregate_workbook import REASON_LABELS
from programs.sbi.reporting import plotly_export_config, render_daily_trend, render_municipality_choropleth
from programs.sbi.workbook_dashboard import prepare_workbook_entries


def _clean_school_id(value: object) -> str:
    text = str(value or "").strip()
    return text[:-2] if text.endswith(".0") else text


def _safe_pct(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) * 100.0 if float(denominator or 0) > 0 else np.nan


def _prepare_g5_targets(actual_targets: pd.DataFrame, municipality: str | None) -> tuple[pd.DataFrame, int]:
    columns = [
        "Municipality", "Barangay", "School ID", "School Name",
        "Unvaccinated G5 Female", "G5 Target Entry Status",
    ]
    if actual_targets is None or actual_targets.empty:
        return pd.DataFrame(columns=["Municipality", "Barangay", "School ID", "School Name", "G5 Target"]), 0

    work = actual_targets.copy()
    for col in columns:
        if col not in work.columns:
            work[col] = pd.NA if col == "Unvaccinated G5 Female" else ""

    work["Municipality"] = work["Municipality"].map(lambda v: canonical_municipality_name(str(v or "").strip()))
    work["School ID"] = work["School ID"].map(_clean_school_id)
    work["School Name"] = work["School Name"].astype("string").fillna("").str.strip()
    work["Barangay"] = work["Barangay"].astype("string").fillna("").str.strip()

    if municipality:
        key = normalize_municipality_key(municipality)
        work = work.loc[work["Municipality"].map(normalize_municipality_key).eq(key)].copy()

    status = work["G5 Target Entry Status"].astype(str).str.casefold().str.strip()
    complete = status.eq("complete")
    # Backward-compatible fallback if the status helper is absent in an older frame.
    if not complete.any() and "Unvaccinated G5 Female" in work.columns:
        complete = pd.to_numeric(work["Unvaccinated G5 Female"], errors="coerce").notna()

    pending_count = int((~complete).sum())
    work = work.loc[complete].copy()
    work["G5 Target"] = pd.to_numeric(work["Unvaccinated G5 Female"], errors="coerce")
    work = work.loc[work["G5 Target"].notna() & work["School ID"].ne("")].copy()

    if work.empty:
        return pd.DataFrame(columns=["Municipality", "Barangay", "School ID", "School Name", "G5 Target"]), pending_count

    grouped = (
        work.groupby(["Municipality", "Barangay", "School ID", "School Name"], dropna=False, as_index=False)["G5 Target"]
        .sum(min_count=1)
        .sort_values(["Municipality", "School Name"])
        .reset_index(drop=True)
    )
    return grouped, pending_count


def _targeted_entries(entries: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    if entries is None or entries.empty or targets is None or targets.empty:
        return pd.DataFrame(columns=entries.columns if isinstance(entries, pd.DataFrame) else None)
    positive = pd.to_numeric(targets.get("G5 Target", 0), errors="coerce").fillna(0).gt(0)
    ids = set(targets.loc[targets["School ID"].ne("") & positive, "School ID"].astype(str))
    return entries.loc[entries["school_id"].astype(str).isin(ids)].copy()


def _municipality_summary(entries: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    """G5 HPV1 is the single-dose reporting field; never combine it with legacy HPV2."""
    rows: list[dict] = []
    for muni in ABRA_MUNIS:
        key = normalize_municipality_key(muni)
        t = targets.loc[targets["Municipality"].map(normalize_municipality_key).eq(key)].copy() if not targets.empty else pd.DataFrame()
        e = entries.loc[entries["municipality"].map(normalize_municipality_key).eq(key)].copy() if not entries.empty else pd.DataFrame()
        ec = _targeted_entries(e, t)
        target = float(pd.to_numeric(t.get("G5 Target", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        vaccinated = float(pd.to_numeric(ec.get("hpv_dose1", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        rows.append({
            "Municipality": muni,
            "G5 Target": target,
            "HPV Vaccinated": vaccinated,
            "HPV Coverage %": _safe_pct(vaccinated, target),
            "Remaining": max(target - vaccinated, 0),
            "Schools Reporting": int(e["school_id"].replace("", pd.NA).dropna().nunique()) if not e.empty else 0,
        })
    return pd.DataFrame(rows)

def _school_summary(entries: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
    if targets is None or targets.empty:
        return pd.DataFrame()
    grouped = targets.copy()
    if entries is None or entries.empty:
        grouped["HPV Vaccinated"] = 0
    else:
        values = (
            entries.groupby("school_id", as_index=False)
            .agg({"hpv_dose1": "sum"})
            .rename(columns={"school_id": "School ID", "hpv_dose1": "HPV Vaccinated"})
        )
        grouped = grouped.merge(values, on="School ID", how="left")
        grouped["HPV Vaccinated"] = grouped["HPV Vaccinated"].fillna(0)
    grouped["HPV Coverage %"] = np.where(
        grouped["G5 Target"].gt(0), grouped["HPV Vaccinated"] / grouped["G5 Target"] * 100, np.nan
    )
    grouped["Remaining"] = np.maximum(grouped["G5 Target"] - grouped["HPV Vaccinated"], 0)
    return grouped.sort_values(["HPV Coverage %", "School Name"], ascending=[True, True], na_position="last").reset_index(drop=True)

def _reason_summary(entries: pd.DataFrame) -> pd.DataFrame:
    counts = {code: 0 for code in REASON_LABELS}
    if entries is not None and not entries.empty and "reason_counts" in entries.columns:
        for value in entries["reason_counts"]:
            if not isinstance(value, dict):
                continue
            for code in counts:
                counts[code] += int(pd.to_numeric(pd.Series([value.get(code, 0)]), errors="coerce").fillna(0).iloc[0])
    return pd.DataFrame([
        {"Code": code, "Reason": label, "Count": counts[code]}
        for code, label in REASON_LABELS.items()
        if counts[code] > 0
    ])


def render_g5_dashboard(
    entries: pd.DataFrame,
    actual_targets: pd.DataFrame,
    start_date: date | None,
    end_date: date | None,
    selected_muni: str | None,
) -> None:
    """Render the standalone Grade 5 HPV catch-up dashboard."""
    st.markdown("### Grade 5 HPV Catch-up Dashboard")
    st.caption(
        "Operational Grade 5 accomplishments from RHU workbooks. The denominator is "
        "Unvaccinated G5 Female from Actual Targets. Grade 5 is intentionally excluded "
        "from VaccTrack G1/G4/G7 sheets and VaccTrack reconciliation. "
        "Under the current single-dose G5 guidance, encode G5 HPV vaccinations under "
        "**HPV Dose 1** in the shared workbook. This is the **HPV1** value submitted to "
        "the Central Office G5 Google reporting platform; do not use the HPV Dose 2 fields for G5."
    )

    target_view, pending_targets = _prepare_g5_targets(actual_targets, selected_muni)
    if pending_targets:
        st.warning(
            f"{pending_targets:,} school(s) in this scope do not yet have an entered Unvaccinated G5 Female target. "
            "They are excluded from G5 coverage denominators until a value is entered; a confirmed 0 remains valid."
        )

    entries_view, legacy_count = prepare_workbook_entries(
        entries,
        start_date=start_date,
        end_date=end_date,
        municipality=selected_muni,
    )
    if legacy_count:
        st.info(f"{legacy_count:,} legacy/manual accomplishment row(s) are excluded. Only workbook-sourced records are shown.")
    g5 = entries_view.loc[entries_view["grade_level"].eq("G5")].copy() if not entries_view.empty else pd.DataFrame()

    for col in ["hpv_dose1", "hpv_dose2", "hpv1_deferred", "hpv2_deferred", "hpv1_refused", "hpv2_refused"]:
        if col not in g5.columns:
            g5[col] = 0
        g5[col] = pd.to_numeric(g5[col], errors="coerce").fillna(0)

    coverage_entries = _targeted_entries(g5, target_view)
    target_total = float(pd.to_numeric(target_view.get("G5 Target", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not target_view.empty else 0.0
    eligible_vaccinated = float(pd.to_numeric(coverage_entries.get("hpv_dose1", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    all_vaccinated = int(pd.to_numeric(g5.get("hpv_dose1", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    legacy_hpv2 = int(pd.to_numeric(g5.get("hpv_dose2", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    legacy_missed = int(sum(pd.to_numeric(g5.get(col, pd.Series(dtype=float)), errors="coerce").fillna(0).sum() for col in ["hpv2_deferred", "hpv2_refused"]))

    positive_target_ids = set(
        target_view.loc[pd.to_numeric(target_view.get("G5 Target", 0), errors="coerce").fillna(0).gt(0), "School ID"].astype(str)
    ) if not target_view.empty else set()
    unmatched = g5.loc[~g5["school_id"].astype(str).isin(positive_target_ids)].copy() if not g5.empty else pd.DataFrame()
    unmatched_doses = int(pd.to_numeric(unmatched.get("hpv_dose1", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    if unmatched_doses > 0:
        st.warning(
            "Some G5 HPV vaccinations belong to schools without a positive entered Unvaccinated G5 Female target. "
            "They remain visible in raw accomplishment totals but are excluded from G5 coverage percentages."
        )
    if legacy_hpv2 or legacy_missed:
        st.warning(
            f"G5 has {legacy_hpv2:,} legacy HPV Dose 2 vaccination(s) and {legacy_missed:,} "
            "legacy HPV2 deferred/refused count(s). These are NOT included in current "
            "single-dose HPV coverage and were not reassigned to HPV Dose 1. "
            "Please review and correct the RHU workbook if these were encoded in the wrong field."
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Unvaccinated G5 Female Target", f"{int(target_total):,}")
    c2.metric("HPV Vaccinated (Single Dose)", f"{all_vaccinated:,}", help="Grade 5 HPV1 / HPV Dose 1 accomplishments; does not include the legacy HPV Dose 2 field.")
    c3.metric("HPV Coverage", "N/A" if pd.isna(_safe_pct(eligible_vaccinated, target_total)) else f"{_safe_pct(eligible_vaccinated, target_total):.1f}%")
    c4.metric("Remaining", f"{int(max(target_total - eligible_vaccinated, 0)):,}")

    s1, s2 = st.columns(2)
    s1.metric("Schools Reporting", f"{int(g5['school_id'].replace('', pd.NA).dropna().nunique()) if not g5.empty else 0:,}")
    s2.metric("RHU(s) Reporting", f"{int(g5['municipality'].nunique()) if not g5.empty else 0:,}")

    if g5.empty:
        st.info("No Grade 5 RHU workbook accomplishments are available for this selection and reporting period yet.")
        return

    if target_view.empty:
        st.warning("No entered Unvaccinated G5 Female targets are available in this scope, so coverage percentages cannot yet be calculated.")

    st.divider()
    location_label = "Abra Province" if not selected_muni else f"{selected_muni}, Abra"

    muni = _municipality_summary(g5, target_view)
    if selected_muni is None:
        st.markdown("#### Municipality Progress")
        st.dataframe(
            muni, width="stretch", hide_index=True,
            column_config={
                "G5 Target": st.column_config.NumberColumn(format="%d"),
                "HPV Vaccinated": st.column_config.NumberColumn(format="%d"),
                "HPV Coverage %": st.column_config.NumberColumn(format="%.1f%%"),
                "Remaining": st.column_config.NumberColumn(format="%d"),
            },
        )
        render_municipality_choropleth(
            muni,
            coverage_col="HPV Coverage %",
            title="Grade 5 Single-Dose HPV Coverage by Municipality",
            key="sbi_g5_map_single_dose_hpv",
            target_col="G5 Target",
            vaccinated_col="HPV Vaccinated",
            remaining_col="Remaining",
            show_zero_target_as_na=True,
            export_context="Target basis: Actual Targets (Unvaccinated G5 Female)",
        )

        chart_rows = muni.copy()
        chart_rows["HPV Coverage %"] = pd.to_numeric(chart_rows["HPV Coverage %"], errors="coerce")
        chart_rows = chart_rows.sort_values(["HPV Coverage %", "Municipality"], ascending=[True, True], na_position="last")
        st.markdown("#### Coverage by Municipality")
        fig_muni = px.bar(
            chart_rows,
            x="HPV Coverage %",
            y="Municipality",
            orientation="h",
            text=chart_rows["HPV Coverage %"].map(lambda v: "N/A" if pd.isna(v) else f"{float(v):.1f}%"),
            custom_data=["G5 Target", "HPV Vaccinated", "Remaining"],
        )
        fig_muni.update_traces(
            textposition="outside",
            hovertemplate=(
                "<b>%{y}</b><br>Coverage: %{x:.1f}%<br>"
                "Target: %{customdata[0]:,.0f}<br>"
                "Vaccinated: %{customdata[1]:,.0f}<br>"
                "Remaining: %{customdata[2]:,.0f}<extra></extra>"
            ),
            cliponaxis=False,
        )
        fig_muni.add_vline(
            x=90,
            line_dash="dash",
            line_color="rgba(216,27,96,0.70)",
            annotation_text="90% target",
            annotation_position="top right",
        )
        fig_muni.update_layout(
            dragmode=False,
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="Coverage %",
            yaxis_title="",
            height=max(500, len(chart_rows) * 28),
            margin=dict(l=10, r=35, t=15, b=60),
            showlegend=False,
        )
        fig_muni.update_xaxes(range=[0, max(100, float(pd.to_numeric(chart_rows["HPV Coverage %"], errors="coerce").max() or 0) + 5)])
        st.plotly_chart(
            fig_muni,
            width="stretch",
            key="sbi_g5_coverage_by_municipality",
            config=plotly_export_config("sbi_g5_coverage_by_municipality", height=max(1200, len(chart_rows) * 55)),
        )
    else:
        st.caption("SBI Grade 5 monitoring is school-based. For a specific municipality, performance is shown by school rather than by barangay.")

    trend = g5.rename(columns={"activity_date": "Report Date", "hpv_dose1": "HPV Vaccinated"})
    render_daily_trend(
        trend,
        [("HPV Vaccinated", "HPV Vaccinated")],
        "Daily Grade 5 HPV Accomplishments",
        key="sbi_g5_daily_trend",
    )

    daily = (
        trend.dropna(subset=["Report Date"])
        .groupby("Report Date", as_index=False)["HPV Vaccinated"]
        .sum()
        .sort_values("Report Date")
    )
    if not daily.empty:
        daily["Cumulative HPV Vaccinated"] = daily["HPV Vaccinated"].cumsum()
        fig = px.line(daily, x="Report Date", y="Cumulative HPV Vaccinated", markers=True)
        if target_total > 0:
            fig.add_hline(y=target_total, line_dash="dash", annotation_text="G5 target", annotation_position="top left")
        fig.update_layout(
            dragmode=False,
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="",
            yaxis_title="Cumulative vaccinated students",
            height=430,
            margin=dict(l=10, r=20, t=35, b=65),
            hovermode="x unified",
        )
        st.markdown("#### Cumulative Grade 5 HPV Progress")
        st.plotly_chart(
            fig,
            width="stretch",
            key="sbi_g5_cumulative",
            config=plotly_export_config("sbi_g5_cumulative", height=1200),
        )

    st.divider()
    st.markdown("#### School-Level Grade 5 Performance")
    schools = _school_summary(g5, target_view)
    if schools.empty:
        st.info("No school-level G5 target rows are available for this selection.")
    else:
        st.dataframe(
            schools[["Municipality", "Barangay", "School ID", "School Name", "G5 Target", "HPV Vaccinated", "HPV Coverage %", "Remaining"]],
            width="stretch",
            hide_index=True,
            column_config={
                "G5 Target": st.column_config.NumberColumn(format="%d"),
                "HPV Vaccinated": st.column_config.NumberColumn(format="%d"),
                "HPV Coverage %": st.column_config.NumberColumn(format="%.1f%%"),
                "Remaining": st.column_config.NumberColumn(format="%d"),
            },
        )

    st.divider()
    st.markdown("#### Grade 5 HPV Deferrals & Refusals")
    d1, d2 = st.columns(2)
    d1.metric("HPV Deferred", f"{int(g5['hpv1_deferred'].sum()):,}")
    d2.metric("HPV Refused", f"{int(g5['hpv1_refused'].sum()):,}")

    reasons = _reason_summary(g5)
    if reasons.empty:
        st.caption("No Grade 5 Reason 01–19 counts are recorded in this selection.")
    else:
        st.dataframe(reasons, width="stretch", hide_index=True)

    st.divider()
    with st.expander("Legacy HPV2 fields (historical review only)", expanded=False):
        st.caption("These Grade 5 HPV2 fields remain in stored records for audit compatibility. They do not contribute to the current one-dose HPV coverage figures.")
        l1, l2, l3 = st.columns(3)
        l1.metric("Legacy HPV Dose 2", f"{legacy_hpv2:,}")
        l2.metric("Legacy HPV2 Deferred", f"{int(g5['hpv2_deferred'].sum()):,}")
        l3.metric("Legacy HPV2 Refused", f"{int(g5['hpv2_refused'].sum()):,}")

    with st.expander("View and download Grade 5 workbook records", expanded=False):
        export_cols = [
            "municipality", "activity_date", "school_id", "school_name", "barangay",
            "hpv_dose1", "hpv_dose2", "hpv1_deferred", "hpv2_deferred", "hpv1_refused", "hpv2_refused",
            "updated_by", "updated_at",
        ]
        raw = g5[[c for c in export_cols if c in g5.columns]].copy()
        raw = raw.rename(columns={
            "municipality": "Municipality", "activity_date": "Activity Date", "school_id": "School ID",
            "school_name": "School Name", "barangay": "Barangay", "hpv_dose1": "HPV Vaccinated (HPV1 field)",
            "hpv_dose2": "HPV Dose 2", "hpv1_deferred": "HPV1 Deferred", "hpv2_deferred": "HPV2 Deferred",
            "hpv1_refused": "HPV1 Refused", "hpv2_refused": "HPV2 Refused", "updated_by": "Updated By", "updated_at": "Updated At",
        })
        st.dataframe(raw, width="stretch", hide_index=True)
        st.download_button(
            "Download Grade 5 Records (CSV)",
            data=raw.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"SBI_G5_Accomplishments_{location_label.replace(', ', '_').replace(' ', '_')}.csv",
            mime="text/csv",
            key="sbi_g5_raw_download",
        )
