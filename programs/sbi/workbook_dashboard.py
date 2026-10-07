"""Dashboard views for RHU offline-workbook accomplishment data.

The RHU workbook is an operational/provisional source. VaccTrack remains the
final official SBI dataset. This module intentionally reads only rows whose
source_type is ``workbook`` so legacy/manual data cannot silently mix into the
workbook dashboard.
"""

from __future__ import annotations

from datetime import date
import json

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

from core.config import ABRA_MUNIS
from core.map_labels import canonical_municipality_name, normalize_municipality_key
from programs.sbi.analytics import REASON_LABELS, build_effective_targets, reason_summary
from programs.sbi.source_dashboard_layout import render_activity_overview, render_source_kpi_summary
from programs.sbi.reporting import (
    render_daily_trend,
    render_municipality_choropleth,
    render_raw_export,
    render_tally_tabs,
)

COUNT_COLUMNS = [
    "mr_male",
    "mr_female",
    "td_male",
    "td_female",
    "hpv_dose1",
    "hpv_dose2",
    "mr_deferred",
    "td_deferred",
    "mr_refused",
    "td_refused",
    "hpv1_deferred",
    "hpv2_deferred",
    "hpv1_refused",
    "hpv2_refused",
]

VACCINE_COLUMNS = ["G1 MR", "G1 Td", "G4 HPV1", "G4 HPV2", "G7 MR", "G7 Td"]


def _safe_pct(numerator, denominator, default=0.0):
    num = pd.to_numeric(numerator, errors="coerce")
    den = pd.to_numeric(denominator, errors="coerce")
    result = num.div(den.mask(den.eq(0))).mul(100)
    if pd.isna(default):
        return result
    return result.fillna(default)


def _reason_dict(value: object) -> dict[str, float]:
    """Normalize the workbook JSON reason-count payload into 01-19 keys."""
    if isinstance(value, dict):
        raw = value
    elif isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
            raw = decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
    else:
        raw = {}

    normalized: dict[str, float] = {}
    for code in REASON_LABELS:
        candidates = (code, str(int(code)), f"Reason {code}")
        found = 0
        for candidate in candidates:
            if candidate in raw:
                found = raw[candidate]
                break
        normalized[code] = float(pd.to_numeric(pd.Series([found]), errors="coerce").fillna(0).iloc[0])
    return normalized


def build_workbook_event_frames(entries: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Convert workbook rows to the same canonical event shape used by VaccTrack.

    The dashboard renderer can then use the same visual outline for both data
    sources while still keeping the workbook operational/provisional and
    VaccTrack official/final.
    """
    if entries is None or entries.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    work = entries.copy()

    def text_series(column: str) -> pd.Series:
        if column not in work.columns:
            return pd.Series("", index=work.index, dtype="object")
        return work[column].fillna("").astype(str)

    common = pd.DataFrame(index=work.index)
    common["Municipality"] = text_series("municipality")
    common["Barangay"] = text_series("barangay")
    common["School ID"] = text_series("school_id").map(_clean_school_id)
    common["School Name"] = text_series("school_name").str.strip()
    activity_date = work["activity_date"] if "activity_date" in work.columns else pd.Series(pd.NaT, index=work.index)
    common["Report Date"] = pd.to_datetime(activity_date, errors="coerce")

    reason_payload = work.get("reason_counts", pd.Series([{}] * len(work), index=work.index))
    parsed_reasons = reason_payload.map(_reason_dict)
    for code in REASON_LABELS:
        common[f"Reason {code}"] = parsed_reasons.map(lambda payload, c=code: payload.get(c, 0.0))

    grade = text_series("grade_level").str.strip().str.upper()

    def numeric(column: str) -> pd.Series:
        if column not in work.columns:
            return pd.Series(0.0, index=work.index)
        return pd.to_numeric(work[column], errors="coerce").fillna(0)

    g1_mask = grade.eq("G1")
    g7_mask = grade.eq("G7")
    g4_mask = grade.eq("G4")

    def mr_td_frame(mask: pd.Series, grade_label: str) -> pd.DataFrame:
        frame = common.loc[mask].copy()
        frame["Grade"] = grade_label
        frame["MR Doses"] = (numeric("mr_male") + numeric("mr_female")).loc[mask].to_numpy()
        frame["Td Doses"] = (numeric("td_male") + numeric("td_female")).loc[mask].to_numpy()
        frame["MR Deferred"] = numeric("mr_deferred").loc[mask].to_numpy()
        frame["Td Deferred"] = numeric("td_deferred").loc[mask].to_numpy()
        frame["MR Refused"] = numeric("mr_refused").loc[mask].to_numpy()
        frame["Td Refused"] = numeric("td_refused").loc[mask].to_numpy()
        return frame.reset_index(drop=True)

    g1 = mr_td_frame(g1_mask, "Grade 1")
    g7 = mr_td_frame(g7_mask, "Grade 7")

    hpv = common.loc[g4_mask].copy()
    hpv["Grade"] = "Grade 4"
    hpv["HPV Dose 1"] = numeric("hpv_dose1").loc[g4_mask].to_numpy()
    hpv["HPV Dose 2"] = numeric("hpv_dose2").loc[g4_mask].to_numpy()
    hpv["HPV Deferred 1"] = numeric("hpv1_deferred").loc[g4_mask].to_numpy()
    hpv["HPV Deferred 2"] = numeric("hpv2_deferred").loc[g4_mask].to_numpy()
    hpv["HPV Refused 1"] = numeric("hpv1_refused").loc[g4_mask].to_numpy()
    hpv["HPV Refused 2"] = numeric("hpv2_refused").loc[g4_mask].to_numpy()
    hpv = hpv.reset_index(drop=True)

    return g1, g7, hpv


def _clean_school_id(value: object) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def prepare_workbook_entries(
    entries: pd.DataFrame,
    start_date: date | None = None,
    end_date: date | None = None,
    municipality: str | None = None,
) -> tuple[pd.DataFrame, int]:
    """Return normalized workbook-only rows plus number of excluded legacy rows."""
    if entries is None or entries.empty:
        return pd.DataFrame(), 0

    work = entries.copy()
    legacy_count = 0
    if "source_type" in work.columns:
        source = work["source_type"].fillna("").astype(str).str.strip().str.lower()
        workbook_mask = source.eq("workbook")
        legacy_count = int((~workbook_mask).sum())
        work = work.loc[workbook_mask].copy()

    if work.empty:
        return work, legacy_count

    if "municipality" in work.columns:
        work["municipality"] = work["municipality"].map(
            lambda value: canonical_municipality_name(str(value or "").strip())
        )
    if "school_id" in work.columns:
        work["school_id"] = work["school_id"].map(_clean_school_id)
    if "activity_date" in work.columns:
        parsed = pd.to_datetime(work["activity_date"], errors="coerce")
        work["activity_date"] = parsed.dt.date
        if start_date:
            work = work.loc[work["activity_date"].ge(start_date)].copy()
        if end_date:
            work = work.loc[work["activity_date"].le(end_date)].copy()

    if municipality:
        target_key = normalize_municipality_key(municipality)
        keys = work["municipality"].map(normalize_municipality_key)
        work = work.loc[keys.eq(target_key)].copy()

    for column in COUNT_COLUMNS:
        if column not in work.columns:
            work[column] = 0
        work[column] = pd.to_numeric(work[column], errors="coerce").fillna(0)

    if "grade_level" not in work.columns:
        work["grade_level"] = ""
    work["grade_level"] = work["grade_level"].fillna("").astype(str).str.strip().str.upper()
    return work.reset_index(drop=True), legacy_count


def build_workbook_summary(entries: pd.DataFrame) -> dict[str, object]:
    if entries is None or entries.empty:
        return {
            "reporting_rhus": 0,
            "schools": 0,
            "rows": 0,
            "latest_activity": None,
            **{column: 0 for column in VACCINE_COLUMNS},
        }

    grade = entries["grade_level"].astype(str)
    g1 = entries.loc[grade.eq("G1")]
    g4 = entries.loc[grade.eq("G4")]
    g7 = entries.loc[grade.eq("G7")]

    latest_activity = None
    if "activity_date" in entries.columns:
        valid_dates = pd.to_datetime(entries["activity_date"], errors="coerce").dropna()
        latest_activity = valid_dates.dt.date.max() if not valid_dates.empty else None

    return {
        "reporting_rhus": int(entries["municipality"].nunique()) if "municipality" in entries.columns else 0,
        "schools": int(entries["school_id"].replace("", pd.NA).dropna().nunique()) if "school_id" in entries.columns else 0,
        "rows": int(len(entries)),
        "latest_activity": latest_activity,
        "G1 MR": int((g1["mr_male"] + g1["mr_female"]).sum()),
        "G1 Td": int((g1["td_male"] + g1["td_female"]).sum()),
        "G4 HPV1": int(g4["hpv_dose1"].sum()),
        "G4 HPV2": int(g4["hpv_dose2"].sum()),
        "G7 MR": int((g7["mr_male"] + g7["mr_female"]).sum()),
        "G7 Td": int((g7["td_male"] + g7["td_female"]).sum()),
    }


def build_municipality_summary(entries: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for municipality in ABRA_MUNIS:
        if entries is None or entries.empty:
            subset = pd.DataFrame()
        else:
            key = normalize_municipality_key(municipality)
            subset = entries.loc[entries["municipality"].map(normalize_municipality_key).eq(key)].copy()
        summary = build_workbook_summary(subset)
        rows.append(
            {
                "Municipality": municipality,
                "RHU Rows": summary["rows"],
                "Latest Activity": summary["latest_activity"],
                "G1 MR": summary["G1 MR"],
                "G1 Td": summary["G1 Td"],
                "G4 HPV1": summary["G4 HPV1"],
                "G4 HPV2": summary["G4 HPV2"],
                "G7 MR": summary["G7 MR"],
                "G7 Td": summary["G7 Td"],
            }
        )
    return pd.DataFrame(rows)


def build_daily_summary(entries: pd.DataFrame) -> pd.DataFrame:
    if entries is None or entries.empty or "activity_date" not in entries.columns:
        return pd.DataFrame(columns=["Activity Date", *VACCINE_COLUMNS])

    rows: list[dict] = []
    for activity_date, day in entries.groupby("activity_date", dropna=True):
        summary = build_workbook_summary(day)
        rows.append({"Activity Date": activity_date, **{column: summary[column] for column in VACCINE_COLUMNS}})
    return pd.DataFrame(rows).sort_values("Activity Date").reset_index(drop=True)


def build_school_summary(entries: pd.DataFrame) -> pd.DataFrame:
    if entries is None or entries.empty:
        return pd.DataFrame(
            columns=["Municipality", "Barangay", "School ID", "School Name", *VACCINE_COLUMNS]
        )

    id_cols = ["municipality", "barangay", "school_id", "school_name"]
    work = entries.copy()
    for col in id_cols:
        if col not in work.columns:
            work[col] = ""

    work["G1 MR"] = ((work["mr_male"] + work["mr_female"]) * work["grade_level"].eq("G1")).astype(int)
    work["G1 Td"] = ((work["td_male"] + work["td_female"]) * work["grade_level"].eq("G1")).astype(int)
    work["G4 HPV1"] = (work["hpv_dose1"] * work["grade_level"].eq("G4")).astype(int)
    work["G4 HPV2"] = (work["hpv_dose2"] * work["grade_level"].eq("G4")).astype(int)
    work["G7 MR"] = ((work["mr_male"] + work["mr_female"]) * work["grade_level"].eq("G7")).astype(int)
    work["G7 Td"] = ((work["td_male"] + work["td_female"]) * work["grade_level"].eq("G7")).astype(int)

    grouped = work.groupby(id_cols, dropna=False)[VACCINE_COLUMNS].sum().reset_index()
    return grouped.rename(
        columns={
            "municipality": "Municipality",
            "barangay": "Barangay",
            "school_id": "School ID",
            "school_name": "School Name",
        }
    ).sort_values(["Municipality", "School Name"]).reset_index(drop=True)


def _target_totals(targets: pd.DataFrame) -> dict[str, float]:
    if targets is None or targets.empty:
        return {"G1": 0.0, "G4": 0.0, "G7": 0.0}

    def total(column: str) -> float:
        if column not in targets.columns:
            return 0.0
        return float(pd.to_numeric(targets[column], errors="coerce").fillna(0).sum())

    return {"G1": total("G1 Target"), "G4": total("G4 Target"), "G7": total("G7 Target")}


def _filter_targets(targets: pd.DataFrame, municipality: str | None) -> pd.DataFrame:
    if targets is None or targets.empty or not municipality or "Municipality" not in targets.columns:
        return targets.copy() if isinstance(targets, pd.DataFrame) else pd.DataFrame()
    key = normalize_municipality_key(municipality)
    keys = targets["Municipality"].map(normalize_municipality_key)
    return targets.loc[keys.eq(key)].copy()


def render_workbook_dashboard(
    entries: pd.DataFrame,
    baseline_targets: pd.DataFrame,
    actual_targets: pd.DataFrame | None,
    start_date: date | None,
    end_date: date | None,
    selected_muni: str | None,
) -> None:
    '''Render the workbook source with the same analytical outline as VaccTrack.'''
    st.markdown("### RHU Workbook Data Dashboard")
    st.caption(
        "Operational/provisional accomplishment data from the latest RHU workbook uploads. "
        "VaccTrack remains the official/final SBI reporting source."
    )

    entries_view, legacy_count = prepare_workbook_entries(
        entries,
        start_date=start_date,
        end_date=end_date,
        municipality=selected_muni,
    )
    if legacy_count:
        st.info(
            f"{legacy_count:,} legacy/manual accomplishment row(s) are excluded from this workbook dashboard. "
            "Only source_type = workbook is shown."
        )

    if entries_view.empty:
        st.info("No RHU workbook accomplishment data is available for this selection and reporting period.")
        return

    location_label = "Abra Province" if selected_muni is None else f"{selected_muni}, Abra"
    all_municipalities = selected_muni is None

    effective_targets = build_effective_targets(
        baseline_targets if baseline_targets is not None else pd.DataFrame(),
        actual_targets if actual_targets is not None else pd.DataFrame(),
    )
    target_view = _filter_targets(effective_targets, selected_muni)

    g1_view, g7_view, hpv_view = build_workbook_event_frames(entries_view)

    render_source_kpi_summary(
        g1_view,
        g7_view,
        hpv_view,
        target_view,
        all_municipalities=all_municipalities,
        key_prefix="sbi_workbook",
        row_label="Activity Rows",
        latest_label="Latest Activity",
    )
    st.divider()
    render_activity_overview(
        g1_view,
        g7_view,
        hpv_view,
        all_municipalities=all_municipalities,
        key_prefix="sbi_workbook",
        date_axis_title="Activity Date",
    )
    st.divider()

    def _build_mr_td_school_summary(events: pd.DataFrame, targets: pd.DataFrame, target_col: str) -> pd.DataFrame:
        if events is None or events.empty:
            return pd.DataFrame()
        school = events.groupby(
            ["Municipality", "Barangay", "School ID", "School Name"],
            dropna=False,
        )[["MR Doses", "Td Doses"]].sum().reset_index()
        school["School ID"] = school["School ID"].astype(str).str.strip()
        if targets is not None and not targets.empty and target_col in targets.columns:
            target_school = targets[["School ID", target_col]].copy().rename(columns={target_col: "Target"})
            target_school["School ID"] = target_school["School ID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            target_school["Target"] = pd.to_numeric(target_school["Target"], errors="coerce").fillna(0)
            target_school = target_school.groupby("School ID", as_index=False)["Target"].sum()
            school = school.merge(target_school, on="School ID", how="left")
        else:
            school["Target"] = 0
        school["Target"] = pd.to_numeric(school["Target"], errors="coerce").fillna(0)
        school["MR Coverage %"] = _safe_pct(school["MR Doses"], school["Target"], default=np.nan)
        school["Td Coverage %"] = _safe_pct(school["Td Doses"], school["Target"], default=np.nan)
        school["MR Remaining to 95%"] = np.maximum(np.ceil(school["Target"] * 0.95 - school["MR Doses"]), 0)
        school["Td Remaining to 95%"] = np.maximum(np.ceil(school["Target"] * 0.95 - school["Td Doses"]), 0)
        school["School Label"] = np.where(
            all_municipalities,
            school["School Name"].astype(str) + " - " + school["Municipality"].astype(str),
            school["School Name"].astype(str),
        )
        return school

    def _render_mr_td_school_coverage(school: pd.DataFrame, key_prefix: str, heading: str = "Coverage by School") -> None:
        st.markdown(
            f'''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-school" style="color:#0033A0; margin-right:8px;"></i>
            {heading}
            </h4>''',
            unsafe_allow_html=True,
        )
        if school is None or school.empty:
            st.info("No school-level workbook data are available for this selection.")
            return
        chart_scope = st.selectbox(
            "Schools shown in chart:",
            ["Top 25 by Target", "Top 50 by Target", "All Schools"],
            key=f"{key_prefix}_school_scope",
        )
        school_plot = school.sort_values("Target", ascending=False).copy()
        if chart_scope.startswith("Top 25"):
            school_plot = school_plot.head(25)
        elif chart_scope.startswith("Top 50"):
            school_plot = school_plot.head(50)
        school_plot = school_plot.sort_values("Target", ascending=True)
        school_long = school_plot.melt(
            id_vars=["School Label"],
            value_vars=["MR Coverage %", "Td Coverage %"],
            var_name="Vaccine",
            value_name="Coverage %",
        ).dropna(subset=["Coverage %"])
        if not school_long.empty:
            fig_school = px.bar(
                school_long,
                x="Coverage %",
                y="School Label",
                color="Vaccine",
                orientation="h",
                barmode="group",
                text_auto=".1f",
                color_discrete_sequence=["#1E88E5", "#43A047"],
            )
            fig_school.add_vline(x=95, line_dash="dash", line_color="red", annotation_text="95%")
            fig_school.update_layout(
                dragmode=False,
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="Coverage (%)",
                yaxis_title="",
                height=max(520, len(school_plot) * 40),
                margin=dict(l=10, r=45, t=25, b=60),
                legend=dict(orientation="h", yanchor="top", y=-0.08, xanchor="center", x=0.5),
                legend_title_text="",
            )
            st.plotly_chart(fig_school, width="stretch", key=f"{key_prefix}_school_chart")

        school_export = school.drop(columns=["School Label"], errors="ignore").sort_values(["Municipality", "School Name"])
        st.dataframe(
            school_export,
            width="stretch",
            hide_index=True,
            column_config={
                "Target": st.column_config.NumberColumn("Target", format="%d"),
                "MR Doses": st.column_config.NumberColumn("MR Vaccinated", format="%d"),
                "Td Doses": st.column_config.NumberColumn("Td Vaccinated", format="%d"),
                "MR Coverage %": st.column_config.NumberColumn("MR Coverage", format="%.1f%%"),
                "Td Coverage %": st.column_config.NumberColumn("Td Coverage", format="%.1f%%"),
                "MR Remaining to 95%": st.column_config.NumberColumn("MR to 95%", format="%d"),
                "Td Remaining to 95%": st.column_config.NumberColumn("Td to 95%", format="%d"),
            },
        )
        st.download_button(
            label="Download School Performance (CSV)",
            data=school_export.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{key_prefix}_Workbook_School_Performance_{location_label.replace(', ', '_')}.csv",
            mime="text/csv",
            key=f"{key_prefix}_school_download",
        )

    def _build_hpv_school_summary(events: pd.DataFrame, targets: pd.DataFrame) -> pd.DataFrame:
        if events is None or events.empty:
            return pd.DataFrame()
        school = events.groupby(
            ["Municipality", "Barangay", "School ID", "School Name"],
            dropna=False,
        )[["HPV Dose 1", "HPV Dose 2"]].sum().reset_index()
        school["School ID"] = school["School ID"].astype(str).str.strip()
        if targets is not None and not targets.empty and "G4 Target" in targets.columns:
            target_school = targets[["School ID", "G4 Target"]].copy().rename(columns={"G4 Target": "Target"})
            target_school["School ID"] = target_school["School ID"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
            target_school["Target"] = pd.to_numeric(target_school["Target"], errors="coerce").fillna(0)
            target_school = target_school.groupby("School ID", as_index=False)["Target"].sum()
            school = school.merge(target_school, on="School ID", how="left")
        else:
            school["Target"] = 0
        school["Target"] = pd.to_numeric(school["Target"], errors="coerce").fillna(0)
        school["1st Dose Coverage %"] = _safe_pct(school["HPV Dose 1"], school["Target"], default=np.nan)
        school["2nd Dose Coverage %"] = _safe_pct(school["HPV Dose 2"], school["Target"], default=np.nan)
        school["1st Dose Remaining to 90%"] = np.maximum(np.ceil(school["Target"] * 0.90 - school["HPV Dose 1"]), 0)
        school["2nd Dose Remaining to 90%"] = np.maximum(np.ceil(school["Target"] * 0.90 - school["HPV Dose 2"]), 0)
        school["School Label"] = np.where(
            all_municipalities,
            school["School Name"].astype(str) + " - " + school["Municipality"].astype(str),
            school["School Name"].astype(str),
        )
        return school

    def _render_hpv_school_coverage(school: pd.DataFrame, key_prefix: str, heading: str = "Coverage by School") -> None:
        st.markdown(
            f'''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-school" style="color:#0033A0; margin-right:8px;"></i>
            {heading}
            </h4>''',
            unsafe_allow_html=True,
        )
        if school is None or school.empty:
            st.info("No school-level workbook HPV data are available for this selection.")
            return
        hpv_scope = st.selectbox(
            "Schools shown in chart:",
            ["Top 25 by Target", "Top 50 by Target", "All Schools"],
            key=f"{key_prefix}_school_scope",
        )
        hpv_school_plot = school.sort_values("Target", ascending=False).copy()
        if hpv_scope.startswith("Top 25"):
            hpv_school_plot = hpv_school_plot.head(25)
        elif hpv_scope.startswith("Top 50"):
            hpv_school_plot = hpv_school_plot.head(50)
        hpv_school_plot = hpv_school_plot.sort_values("Target", ascending=True)
        hpv_school_long = hpv_school_plot.melt(
            id_vars=["School Label"],
            value_vars=["1st Dose Coverage %", "2nd Dose Coverage %"],
            var_name="Dose",
            value_name="Coverage %",
        ).dropna(subset=["Coverage %"])
        if not hpv_school_long.empty:
            fig_hpv_school = px.bar(
                hpv_school_long,
                x="Coverage %",
                y="School Label",
                color="Dose",
                orientation="h",
                barmode="group",
                text_auto=".1f",
                color_discrete_sequence=["#D81B60", "#8E24AA"],
            )
            fig_hpv_school.add_vline(x=90, line_dash="dash", line_color="red", annotation_text="90%")
            fig_hpv_school.update_layout(
                dragmode=False,
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="Coverage (%)",
                yaxis_title="",
                height=max(520, len(hpv_school_plot) * 40),
                margin=dict(l=10, r=45, t=25, b=60),
                legend=dict(orientation="h", yanchor="top", y=-0.08, xanchor="center", x=0.5),
                legend_title_text="",
            )
            st.plotly_chart(fig_hpv_school, width="stretch", key=f"{key_prefix}_school_chart")

        hpv_export = school.drop(columns=["School Label"], errors="ignore").sort_values(["Municipality", "School Name"])
        st.dataframe(
            hpv_export,
            width="stretch",
            hide_index=True,
            column_config={
                "Target": st.column_config.NumberColumn("Target", format="%d"),
                "HPV Dose 1": st.column_config.NumberColumn("1st Dose", format="%d"),
                "HPV Dose 2": st.column_config.NumberColumn("2nd Dose", format="%d"),
                "1st Dose Coverage %": st.column_config.NumberColumn("1st Dose Coverage", format="%.1f%%"),
                "2nd Dose Coverage %": st.column_config.NumberColumn("2nd Dose Coverage", format="%.1f%%"),
                "1st Dose Remaining to 90%": st.column_config.NumberColumn("1st Dose to 90%", format="%d"),
                "2nd Dose Remaining to 90%": st.column_config.NumberColumn("2nd Dose to 90%", format="%d"),
            },
        )
        st.download_button(
            label="Download HPV School Performance (CSV)",
            data=hpv_export.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"SBI_Workbook_HPV_School_Performance_{location_label.replace(', ', '_')}.csv",
            mime="text/csv",
            key=f"{key_prefix}_school_download",
        )

    def _workbook_export_frame(events: pd.DataFrame) -> pd.DataFrame:
        export = events.copy()
        if "Report Date" in export.columns:
            export = export.rename(columns={"Report Date": "Activity Date"})
        return export

    def _render_mr_td_panel(
        events: pd.DataFrame,
        targets: pd.DataFrame,
        target_col: str,
        panel_label: str,
        key_prefix: str,
    ) -> None:
        st.markdown(
            f'''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-syringe" style="color:#0033A0; margin-right:8px;"></i>
            {panel_label}
            </h4>''',
            unsafe_allow_html=True,
        )
        if events is None or events.empty:
            st.info("No RHU workbook records are available for this selection and reporting period.")
            return

        target_total = pd.to_numeric(targets.get(target_col, 0), errors="coerce").fillna(0).sum() if targets is not None and not targets.empty else 0
        school = _build_mr_td_school_summary(events, targets, target_col)

        if all_municipalities:
            geo_col = "Municipality"
            event_geo = events.groupby(geo_col, dropna=False)[["MR Doses", "Td Doses"]].sum().reset_index()
            if targets is not None and not targets.empty and target_col in targets.columns:
                target_geo = targets.groupby(geo_col, dropna=False)[target_col].sum().reset_index().rename(columns={target_col: "Target"})
                geo = target_geo.merge(event_geo, on=geo_col, how="outer").fillna(0)
            else:
                geo = event_geo.copy()
                geo["Target"] = 0
            geo["MR Coverage %"] = _safe_pct(geo["MR Doses"], geo["Target"])
            geo["Td Coverage %"] = _safe_pct(geo["Td Doses"], geo["Target"])
            geo["MR Remaining to 95%"] = np.maximum(np.ceil(geo["Target"] * 0.95 - geo["MR Doses"]), 0)
            geo["Td Remaining to 95%"] = np.maximum(np.ceil(geo["Target"] * 0.95 - geo["Td Doses"]), 0)

            st.markdown(
                '''<h4 style="margin-bottom:0.25rem;">
                <i class="fa-solid fa-chart-bar" style="color:#0033A0; margin-right:8px;"></i>
                Coverage by Municipality
                </h4>''',
                unsafe_allow_html=True,
            )
            geo_chart = geo.sort_values("MR Coverage %", ascending=True).melt(
                id_vars=[geo_col],
                value_vars=["MR Coverage %", "Td Coverage %"],
                var_name="Vaccine",
                value_name="Coverage %",
            )
            fig_geo = px.bar(
                geo_chart,
                x="Coverage %",
                y=geo_col,
                color="Vaccine",
                orientation="h",
                barmode="group",
                text_auto=".1f",
                color_discrete_sequence=["#1E88E5", "#43A047"],
            )
            fig_geo.add_vline(x=95, line_dash="dash", line_color="red", annotation_text="95%")
            fig_geo.update_layout(
                dragmode=False,
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="Coverage (%)",
                yaxis_title="",
                height=max(420, len(geo) * 46),
                margin=dict(l=10, r=45, t=35, b=60),
                legend=dict(orientation="h", yanchor="top", y=-0.10, xanchor="center", x=0.5),
                legend_title_text="",
            )
            fig_geo.update_traces(textposition="outside", cliponaxis=False)
            st.plotly_chart(fig_geo, width="stretch", key=f"{key_prefix}_geo_cov")

            st.dataframe(
                geo.sort_values("MR Coverage %", ascending=False),
                width="stretch",
                hide_index=True,
                column_config={
                    "Target": st.column_config.NumberColumn("Target", format="%d"),
                    "MR Doses": st.column_config.NumberColumn("MR Vaccinated", format="%d"),
                    "Td Doses": st.column_config.NumberColumn("Td Vaccinated", format="%d"),
                    "MR Coverage %": st.column_config.NumberColumn("MR Coverage", format="%.1f%%"),
                    "Td Coverage %": st.column_config.NumberColumn("Td Coverage", format="%.1f%%"),
                    "MR Remaining to 95%": st.column_config.NumberColumn("MR to 95%", format="%d"),
                    "Td Remaining to 95%": st.column_config.NumberColumn("Td to 95%", format="%d"),
                },
            )

            map_choice = st.selectbox(
                "Municipality coverage map:",
                ["MR Coverage", "Td Coverage"],
                key=f"{key_prefix}_map_choice",
            )
            map_geo = geo.rename(columns={geo_col: "Municipality"}).copy()
            if map_choice == "MR Coverage":
                render_municipality_choropleth(
                    map_geo,
                    "MR Coverage %",
                    f"{panel_label} - MR Coverage by Municipality",
                    f"{key_prefix}_mr_map",
                    target_col="Target",
                    vaccinated_col="MR Doses",
                    remaining_col="MR Remaining to 95%",
                )
            else:
                render_municipality_choropleth(
                    map_geo,
                    "Td Coverage %",
                    f"{panel_label} - Td Coverage by Municipality",
                    f"{key_prefix}_td_map",
                    target_col="Target",
                    vaccinated_col="Td Doses",
                    remaining_col="Td Remaining to 95%",
                )
        else:
            _render_mr_td_school_coverage(school, key_prefix, heading="Coverage by School")

        st.divider()
        render_daily_trend(
            events,
            [("MR Doses", "MR"), ("Td Doses", "Td")],
            title="Daily Vaccination Activity by Activity Date",
            key=f"{key_prefix}_daily_trend",
            colors=["#1E88E5", "#43A047"],
        )

        st.divider()
        st.markdown(
            '''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-chart-line" style="color:#0033A0; margin-right:8px;"></i>
            Cumulative Vaccinations Over Time
            </h4>''',
            unsafe_allow_html=True,
        )
        trend = events.dropna(subset=["Report Date"]).groupby("Report Date")[["MR Doses", "Td Doses"]].sum().reset_index().sort_values("Report Date")
        if not trend.empty:
            trend["Cumulative MR"] = trend["MR Doses"].cumsum()
            trend["Cumulative Td"] = trend["Td Doses"].cumsum()
            trend_long = trend.melt(
                id_vars=["Report Date"],
                value_vars=["Cumulative MR", "Cumulative Td"],
                var_name="Vaccine",
                value_name="Vaccinated",
            )
            fig_trend = px.line(
                trend_long,
                x="Report Date",
                y="Vaccinated",
                color="Vaccine",
                markers=True,
                color_discrete_sequence=["#1E88E5", "#43A047"],
            )
            if target_total > 0:
                fig_trend.add_hline(
                    y=target_total * 0.95,
                    line_dash="dash",
                    line_color="rgba(0,51,160,0.60)",
                    annotation_text="95% target",
                    annotation_position="top left",
                )
            fig_trend.update_layout(
                dragmode=False,
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="Activity Date",
                yaxis_title="Cumulative vaccinated students",
                height=420,
                margin=dict(l=10, r=20, t=25, b=55),
                legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
                legend_title_text="",
            )
            st.plotly_chart(fig_trend, width="stretch", key=f"{key_prefix}_trend")
        else:
            st.info("No valid activity dates are available for the selected period.")

        st.divider()
        render_tally_tabs(
            events,
            [("MR Doses", "MR"), ("Td Doses", "Td")],
            geo_col="Municipality" if all_municipalities else "School Name",
            key_prefix=f"{key_prefix}_tally",
            location_label=location_label,
        )

        if all_municipalities:
            st.divider()
            _render_mr_td_school_coverage(school, key_prefix, heading="School-Level Performance")

        render_raw_export(
            _workbook_export_frame(events),
            title="View and download normalized RHU workbook rows",
            filename=f"{key_prefix}_Workbook_{location_label.replace(', ', '_').replace(' ', '_')}.csv",
            key=f"{key_prefix}_raw_download",
        )

    vacc_mr_tab, vacc_hpv_tab, vacc_def_tab = st.tabs([
        "MR & Td (Grades 1 & 7)",
        "HPV (Grade 4)",
        "Deferrals & Refusals",
    ])

    with vacc_mr_tab:
        st.markdown(f"### Measles-Rubella (MR) & Tetanus-diphtheria (Td): {location_label}")
        mr_combined_tab, mr_g1_tab, mr_g7_tab = st.tabs([
            "Combined Grades 1 & 7",
            "Grade 1",
            "Grade 7",
        ])
        with mr_combined_tab:
            combined_events = pd.concat([g1_view, g7_view], ignore_index=True, sort=False)
            combined_targets = target_view.copy()
            if not combined_targets.empty:
                combined_targets["MR/Td Target"] = (
                    pd.to_numeric(combined_targets.get("G1 Target", 0), errors="coerce").fillna(0)
                    + pd.to_numeric(combined_targets.get("G7 Target", 0), errors="coerce").fillna(0)
                )
            _render_mr_td_panel(
                combined_events,
                combined_targets,
                "MR/Td Target",
                "Combined Grades 1 & 7 Performance",
                "sbi_workbook_mrtd_combined",
            )
        with mr_g1_tab:
            _render_mr_td_panel(
                g1_view,
                target_view,
                "G1 Target",
                "Grade 1 MR & Td Performance",
                "sbi_workbook_mrtd_g1",
            )
        with mr_g7_tab:
            _render_mr_td_panel(
                g7_view,
                target_view,
                "G7 Target",
                "Grade 7 MR & Td Performance",
                "sbi_workbook_mrtd_g7",
            )

    with vacc_hpv_tab:
        st.markdown(f"### Human Papillomavirus (HPV) - Grade 4 Female Students: {location_label}")
        if hpv_view.empty:
            st.info("No Grade 4 HPV RHU workbook records are available for this selection and reporting period.")
        else:
            hpv_target = pd.to_numeric(target_view.get("G4 Target", 0), errors="coerce").fillna(0).sum() if not target_view.empty else 0
            hpv_school = _build_hpv_school_summary(hpv_view, target_view)

            if all_municipalities:
                geo_col_hpv = "Municipality"
                hpv_geo = hpv_view.groupby(geo_col_hpv, dropna=False)[["HPV Dose 1", "HPV Dose 2"]].sum().reset_index()
                if not target_view.empty:
                    hpv_target_geo = target_view.groupby(geo_col_hpv, dropna=False)["G4 Target"].sum().reset_index().rename(columns={"G4 Target": "Target"})
                    hpv_geo = hpv_target_geo.merge(hpv_geo, on=geo_col_hpv, how="outer").fillna(0)
                else:
                    hpv_geo["Target"] = 0
                hpv_geo["1st Dose Coverage %"] = _safe_pct(hpv_geo["HPV Dose 1"], hpv_geo["Target"])
                hpv_geo["2nd Dose Coverage %"] = _safe_pct(hpv_geo["HPV Dose 2"], hpv_geo["Target"])
                hpv_geo["1st Dose Remaining to 90%"] = np.maximum(np.ceil(hpv_geo["Target"] * 0.90 - hpv_geo["HPV Dose 1"]), 0)
                hpv_geo["2nd Dose Remaining to 90%"] = np.maximum(np.ceil(hpv_geo["Target"] * 0.90 - hpv_geo["HPV Dose 2"]), 0)

                st.markdown(
                    '''<h4 style="margin-bottom:0.25rem;">
                    <i class="fa-solid fa-chart-bar" style="color:#0033A0; margin-right:8px;"></i>
                    HPV Coverage by Municipality
                    </h4>''',
                    unsafe_allow_html=True,
                )
                hpv_geo_long = hpv_geo.sort_values("1st Dose Coverage %", ascending=True).melt(
                    id_vars=[geo_col_hpv],
                    value_vars=["1st Dose Coverage %", "2nd Dose Coverage %"],
                    var_name="Dose",
                    value_name="Coverage %",
                )
                fig_hpv_geo = px.bar(
                    hpv_geo_long,
                    x="Coverage %",
                    y=geo_col_hpv,
                    color="Dose",
                    orientation="h",
                    barmode="group",
                    text_auto=".1f",
                    color_discrete_sequence=["#D81B60", "#8E24AA"],
                )
                fig_hpv_geo.add_vline(x=90, line_dash="dash", line_color="red", annotation_text="90%")
                fig_hpv_geo.update_layout(
                    dragmode=False,
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis_title="Coverage (%)",
                    yaxis_title="",
                    height=max(420, len(hpv_geo) * 46),
                    margin=dict(l=10, r=45, t=35, b=60),
                    legend=dict(orientation="h", yanchor="top", y=-0.10, xanchor="center", x=0.5),
                    legend_title_text="",
                )
                fig_hpv_geo.update_traces(textposition="outside", cliponaxis=False)
                st.plotly_chart(fig_hpv_geo, width="stretch", key="sbi_workbook_hpv_geo")

                st.dataframe(
                    hpv_geo.sort_values("1st Dose Coverage %", ascending=False),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Target": st.column_config.NumberColumn("Target", format="%d"),
                        "HPV Dose 1": st.column_config.NumberColumn("1st Dose", format="%d"),
                        "HPV Dose 2": st.column_config.NumberColumn("2nd Dose", format="%d"),
                        "1st Dose Coverage %": st.column_config.NumberColumn("1st Dose Coverage", format="%.1f%%"),
                        "2nd Dose Coverage %": st.column_config.NumberColumn("2nd Dose Coverage", format="%.1f%%"),
                        "1st Dose Remaining to 90%": st.column_config.NumberColumn("1st Dose to 90%", format="%d"),
                        "2nd Dose Remaining to 90%": st.column_config.NumberColumn("2nd Dose to 90%", format="%d"),
                    },
                )

                hpv_map_choice = st.selectbox(
                    "Municipality coverage map:",
                    ["HPV 1st Dose", "HPV 2nd Dose"],
                    key="sbi_workbook_hpv_map_choice",
                )
                hpv_map_df = hpv_geo.rename(columns={geo_col_hpv: "Municipality"}).copy()
                if hpv_map_choice == "HPV 1st Dose":
                    render_municipality_choropleth(
                        hpv_map_df,
                        "1st Dose Coverage %",
                        "HPV 1st Dose Coverage by Municipality",
                        "sbi_workbook_hpv_dose1_map",
                        target_col="Target",
                        vaccinated_col="HPV Dose 1",
                        remaining_col="1st Dose Remaining to 90%",
                    )
                else:
                    render_municipality_choropleth(
                        hpv_map_df,
                        "2nd Dose Coverage %",
                        "HPV 2nd Dose Coverage by Municipality",
                        "sbi_workbook_hpv_dose2_map",
                        target_col="Target",
                        vaccinated_col="HPV Dose 2",
                        remaining_col="2nd Dose Remaining to 90%",
                    )
            else:
                _render_hpv_school_coverage(hpv_school, "sbi_workbook_hpv", heading="Coverage by School")

            st.divider()
            render_daily_trend(
                hpv_view,
                [("HPV Dose 1", "HPV 1st Dose"), ("HPV Dose 2", "HPV 2nd Dose")],
                title="Daily HPV Vaccination Activity by Activity Date",
                key="sbi_workbook_hpv_daily_trend",
                colors=["#D81B60", "#8E24AA"],
            )

            st.divider()
            st.markdown(
                '''<h4 style="margin-bottom:0.25rem;">
                <i class="fa-solid fa-chart-line" style="color:#0033A0; margin-right:8px;"></i>
                Cumulative HPV Doses Over Time
                </h4>''',
                unsafe_allow_html=True,
            )
            hpv_trend = hpv_view.dropna(subset=["Report Date"]).groupby("Report Date")[["HPV Dose 1", "HPV Dose 2"]].sum().reset_index().sort_values("Report Date")
            if not hpv_trend.empty:
                hpv_trend["Cumulative HPV 1st Dose"] = hpv_trend["HPV Dose 1"].cumsum()
                hpv_trend["Cumulative HPV 2nd Dose"] = hpv_trend["HPV Dose 2"].cumsum()
                hpv_trend_long = hpv_trend.melt(
                    id_vars=["Report Date"],
                    value_vars=["Cumulative HPV 1st Dose", "Cumulative HPV 2nd Dose"],
                    var_name="Dose",
                    value_name="Vaccinated",
                )
                fig_hpv_trend = px.line(
                    hpv_trend_long,
                    x="Report Date",
                    y="Vaccinated",
                    color="Dose",
                    markers=True,
                    color_discrete_sequence=["#D81B60", "#8E24AA"],
                )
                if hpv_target > 0:
                    fig_hpv_trend.add_hline(
                        y=hpv_target * 0.90,
                        line_dash="dash",
                        line_color="rgba(216,27,96,0.65)",
                        annotation_text="90% target",
                        annotation_position="top left",
                    )
                fig_hpv_trend.update_layout(
                    dragmode=False,
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis_title="Activity Date",
                    yaxis_title="Cumulative vaccinated students",
                    height=420,
                    margin=dict(l=10, r=20, t=25, b=55),
                    legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
                    legend_title_text="",
                )
                st.plotly_chart(fig_hpv_trend, width="stretch", key="sbi_workbook_hpv_trend")
            else:
                st.info("No valid activity dates are available for the selected period.")

            st.divider()
            render_tally_tabs(
                hpv_view,
                [("HPV Dose 1", "HPV 1st Dose"), ("HPV Dose 2", "HPV 2nd Dose")],
                geo_col="Municipality" if all_municipalities else "School Name",
                key_prefix="sbi_workbook_hpv_tally",
                location_label=location_label,
            )

            if all_municipalities:
                st.divider()
                _render_hpv_school_coverage(hpv_school, "sbi_workbook_hpv_all", heading="School-Level HPV Performance")

            render_raw_export(
                _workbook_export_frame(hpv_view),
                title="View and download normalized RHU workbook HPV rows",
                filename=f"SBI_Workbook_HPV_{location_label.replace(', ', '_').replace(' ', '_')}.csv",
                key="sbi_workbook_hpv_raw_download",
            )

    with vacc_def_tab:
        st.markdown(f"### Vaccine Deferrals & Refusals Analysis: {location_label}")
        total_mr_deferred = sum(
            pd.to_numeric(frame.get("MR Deferred", 0), errors="coerce").fillna(0).sum()
            for frame in (g1_view, g7_view) if frame is not None and not frame.empty
        )
        total_td_deferred = sum(
            pd.to_numeric(frame.get("Td Deferred", 0), errors="coerce").fillna(0).sum()
            for frame in (g1_view, g7_view) if frame is not None and not frame.empty
        )
        total_mr_refused = sum(
            pd.to_numeric(frame.get("MR Refused", 0), errors="coerce").fillna(0).sum()
            for frame in (g1_view, g7_view) if frame is not None and not frame.empty
        )
        total_td_refused = sum(
            pd.to_numeric(frame.get("Td Refused", 0), errors="coerce").fillna(0).sum()
            for frame in (g1_view, g7_view) if frame is not None and not frame.empty
        )
        total_hpv_deferred = 0
        total_hpv_refused = 0
        if hpv_view is not None and not hpv_view.empty:
            total_hpv_deferred = (
                pd.to_numeric(hpv_view.get("HPV Deferred 1", 0), errors="coerce").fillna(0).sum()
                + pd.to_numeric(hpv_view.get("HPV Deferred 2", 0), errors="coerce").fillna(0).sum()
            )
            total_hpv_refused = (
                pd.to_numeric(hpv_view.get("HPV Refused 1", 0), errors="coerce").fillna(0).sum()
                + pd.to_numeric(hpv_view.get("HPV Refused 2", 0), errors="coerce").fillna(0).sum()
            )

        total_deferred = total_mr_deferred + total_td_deferred + total_hpv_deferred
        total_refused = total_mr_refused + total_td_refused + total_hpv_refused
        reasons_df = reason_summary(g1_view, g7_view, hpv_view)
        total_reason_records = pd.to_numeric(reasons_df["Count"], errors="coerce").fillna(0).sum() if not reasons_df.empty else 0

        st.markdown(
            '''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-chart-column" style="color:#0033A0; margin-right:8px;"></i>
            Deferrals and Refusals by Vaccine / Dose
            </h4>''',
            unsafe_allow_html=True,
        )
        missed_summary = pd.DataFrame([
            {"Vaccine / Dose": "MR - Grades 1 & 7", "Deferred": total_mr_deferred, "Refused": total_mr_refused},
            {"Vaccine / Dose": "Td - Grades 1 & 7", "Deferred": total_td_deferred, "Refused": total_td_refused},
            {"Vaccine / Dose": "HPV - Doses 1 & 2", "Deferred": total_hpv_deferred, "Refused": total_hpv_refused},
        ])
        missed_long = missed_summary.melt(
            id_vars=["Vaccine / Dose"],
            value_vars=["Deferred", "Refused"],
            var_name="Outcome",
            value_name="Count",
        )
        fig_missed = px.bar(
            missed_long,
            x="Vaccine / Dose",
            y="Count",
            color="Outcome",
            barmode="group",
            text_auto=".0f",
            color_discrete_sequence=["#F9A825", "#D32F2F"],
        )
        fig_missed.update_layout(
            dragmode=False,
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="",
            yaxis_title="Students",
            height=420,
            margin=dict(l=10, r=20, t=25, b=60),
            legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
            legend_title_text="",
        )
        fig_missed.update_traces(textposition="outside", cliponaxis=False)
        st.plotly_chart(fig_missed, width="stretch", key="sbi_workbook_def_ref_summary")

        st.divider()
        outcome_frames = []
        for frame, deferred_cols, refused_cols in [
            (g1_view, ["MR Deferred", "Td Deferred"], ["MR Refused", "Td Refused"]),
            (g7_view, ["MR Deferred", "Td Deferred"], ["MR Refused", "Td Refused"]),
            (hpv_view, ["HPV Deferred 1", "HPV Deferred 2"], ["HPV Refused 1", "HPV Refused 2"]),
        ]:
            if frame is None or frame.empty:
                continue
            part = frame[["Report Date"]].copy()
            deferred_total = pd.Series(0.0, index=frame.index)
            refused_total = pd.Series(0.0, index=frame.index)
            for col in deferred_cols:
                if col in frame.columns:
                    deferred_total = deferred_total.add(pd.to_numeric(frame[col], errors="coerce").fillna(0), fill_value=0)
            for col in refused_cols:
                if col in frame.columns:
                    refused_total = refused_total.add(pd.to_numeric(frame[col], errors="coerce").fillna(0), fill_value=0)
            part["Deferred"] = deferred_total
            part["Refused"] = refused_total
            outcome_frames.append(part)
        if outcome_frames:
            render_daily_trend(
                pd.concat(outcome_frames, ignore_index=True),
                [("Deferred", "Deferred"), ("Refused", "Refused")],
                title="Daily Deferrals and Refusals by Activity Date",
                key="sbi_workbook_def_ref_daily_trend",
                colors=["#F9A825", "#D32F2F"],
            )

        st.divider()
        st.markdown(
            '''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-list-ol" style="color:#0033A0; margin-right:8px;"></i>
            Reasons for Missed Vaccination
            </h4>''',
            unsafe_allow_html=True,
        )
        reasons_nonzero = reasons_df[reasons_df["Count"] > 0].sort_values("Count", ascending=True).copy() if not reasons_df.empty else pd.DataFrame()
        if reasons_nonzero.empty:
            st.info("No reason counts were recorded for the selected period.")
        else:
            reasons_nonzero["Reason Label"] = reasons_nonzero["Reason Code"] + " - " + reasons_nonzero["Reason"]
            fig_reasons = px.bar(
                reasons_nonzero,
                x="Count",
                y="Reason Label",
                orientation="h",
                text_auto=".0f",
                color_discrete_sequence=["#6D4C41"],
            )
            fig_reasons.update_layout(
                dragmode=False,
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="Recorded cases",
                yaxis_title="",
                height=max(450, len(reasons_nonzero) * 38),
                margin=dict(l=10, r=35, t=25, b=40),
                showlegend=False,
            )
            fig_reasons.update_traces(textposition="outside", cliponaxis=False)
            st.plotly_chart(fig_reasons, width="stretch", key="sbi_workbook_def_ref_reasons")

        st.divider()
        geo_col_def = "Municipality" if all_municipalities else "Barangay"
        geo_parts = []
        if not g1_view.empty:
            g1_geo = g1_view.groupby(geo_col_def, dropna=False)[["MR Deferred", "Td Deferred", "MR Refused", "Td Refused"]].sum().reset_index()
            g1_geo["Deferred"] = g1_geo["MR Deferred"] + g1_geo["Td Deferred"]
            g1_geo["Refused"] = g1_geo["MR Refused"] + g1_geo["Td Refused"]
            geo_parts.append(g1_geo[[geo_col_def, "Deferred", "Refused"]])
        if not g7_view.empty:
            g7_geo = g7_view.groupby(geo_col_def, dropna=False)[["MR Deferred", "Td Deferred", "MR Refused", "Td Refused"]].sum().reset_index()
            g7_geo["Deferred"] = g7_geo["MR Deferred"] + g7_geo["Td Deferred"]
            g7_geo["Refused"] = g7_geo["MR Refused"] + g7_geo["Td Refused"]
            geo_parts.append(g7_geo[[geo_col_def, "Deferred", "Refused"]])
        if not hpv_view.empty:
            hpv_geo_def = hpv_view.groupby(geo_col_def, dropna=False)[["HPV Deferred 1", "HPV Deferred 2", "HPV Refused 1", "HPV Refused 2"]].sum().reset_index()
            hpv_geo_def["Deferred"] = hpv_geo_def["HPV Deferred 1"] + hpv_geo_def["HPV Deferred 2"]
            hpv_geo_def["Refused"] = hpv_geo_def["HPV Refused 1"] + hpv_geo_def["HPV Refused 2"]
            geo_parts.append(hpv_geo_def[[geo_col_def, "Deferred", "Refused"]])

        st.markdown(
            f'''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-location-dot" style="color:#0033A0; margin-right:8px;"></i>
            Missed Vaccination Outcomes by {geo_col_def}
            </h4>''',
            unsafe_allow_html=True,
        )
        if geo_parts:
            geo_missed = pd.concat(geo_parts, ignore_index=True).groupby(geo_col_def, dropna=False)[["Deferred", "Refused"]].sum().reset_index()
            geo_missed["Total Missed"] = geo_missed["Deferred"] + geo_missed["Refused"]
            geo_missed_long = geo_missed.sort_values("Total Missed", ascending=True).melt(
                id_vars=[geo_col_def],
                value_vars=["Deferred", "Refused"],
                var_name="Outcome",
                value_name="Count",
            )
            fig_geo_missed = px.bar(
                geo_missed_long,
                x="Count",
                y=geo_col_def,
                color="Outcome",
                orientation="h",
                barmode="group",
                text_auto=".0f",
                color_discrete_sequence=["#F9A825", "#D32F2F"],
            )
            fig_geo_missed.update_layout(
                dragmode=False,
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="Students",
                yaxis_title="",
                height=max(420, len(geo_missed) * 45),
                margin=dict(l=10, r=35, t=25, b=60),
                legend=dict(orientation="h", yanchor="top", y=-0.10, xanchor="center", x=0.5),
                legend_title_text="",
            )
            fig_geo_missed.update_traces(textposition="outside", cliponaxis=False)
            st.plotly_chart(fig_geo_missed, width="stretch", key="sbi_workbook_def_ref_geo")
            st.dataframe(
                geo_missed.sort_values("Total Missed", ascending=False),
                width="stretch",
                hide_index=True,
                column_config={
                    "Deferred": st.column_config.NumberColumn("Deferred", format="%d"),
                    "Refused": st.column_config.NumberColumn("Refused", format="%d"),
                    "Total Missed": st.column_config.NumberColumn("Total Missed", format="%d"),
                },
            )
            st.download_button(
                label="Download Deferral and Refusal Summary (CSV)",
                data=geo_missed.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"SBI_Workbook_Deferrals_Refusals_{location_label.replace(', ', '_')}.csv",
                mime="text/csv",
                key="sbi_workbook_def_ref_download",
            )
        else:
            st.info("No deferral or refusal records are available for this selection.")
