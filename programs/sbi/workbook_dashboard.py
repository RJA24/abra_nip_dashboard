"""Dashboard views for RHU offline-workbook accomplishment data.

The RHU workbook is an operational/provisional source. VaccTrack remains the
final official SBI dataset. This module intentionally reads only rows whose
source_type is ``workbook`` so legacy/manual data cannot silently mix into the
workbook dashboard.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

from core.config import ABRA_MUNIS
from core.map_labels import canonical_municipality_name, normalize_municipality_key
from programs.sbi.analytics import build_effective_targets

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
    """Render operational workbook data without changing any RHU records."""
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

    summary = build_workbook_summary(entries_view)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("RHUs Reporting", f"{summary['reporting_rhus']:,}" + (" / 27" if selected_muni is None else ""))
    c2.metric("Schools with Data", f"{summary['schools']:,}")
    c3.metric("Activity Rows", f"{summary['rows']:,}")
    latest = summary["latest_activity"]
    c4.metric("Latest Activity", latest.strftime("%b %d, %Y") if latest else "—")

    st.markdown("#### Vaccination Accomplishments")
    totals = pd.DataFrame(
        [
            {
                "G1 MR": summary["G1 MR"],
                "G1 Td": summary["G1 Td"],
                "G4 HPV1": summary["G4 HPV1"],
                "G4 HPV2": summary["G4 HPV2"],
                "G7 MR": summary["G7 MR"],
                "G7 Td": summary["G7 Td"],
            }
        ]
    )
    st.dataframe(totals, width="stretch", hide_index=True)

    effective_targets = build_effective_targets(
        baseline_targets if baseline_targets is not None else pd.DataFrame(),
        actual_targets if actual_targets is not None else pd.DataFrame(),
    )
    effective_targets = _filter_targets(effective_targets, selected_muni)
    target_totals = _target_totals(effective_targets)
    if any(target_totals.values()):
        coverage_rows = [
            {
                "Indicator": "G1 MR",
                "Target": int(target_totals["G1"]),
                "Vaccinated": summary["G1 MR"],
            },
            {
                "Indicator": "G1 Td",
                "Target": int(target_totals["G1"]),
                "Vaccinated": summary["G1 Td"],
            },
            {
                "Indicator": "G4 HPV Dose 1",
                "Target": int(target_totals["G4"]),
                "Vaccinated": summary["G4 HPV1"],
            },
            {
                "Indicator": "G4 HPV Dose 2",
                "Target": int(target_totals["G4"]),
                "Vaccinated": summary["G4 HPV2"],
            },
            {
                "Indicator": "G7 MR",
                "Target": int(target_totals["G7"]),
                "Vaccinated": summary["G7 MR"],
            },
            {
                "Indicator": "G7 Td",
                "Target": int(target_totals["G7"]),
                "Vaccinated": summary["G7 Td"],
            },
        ]
        coverage = pd.DataFrame(coverage_rows)
        coverage["Coverage %"] = coverage.apply(
            lambda row: (row["Vaccinated"] / row["Target"] * 100) if row["Target"] else 0.0,
            axis=1,
        )
        with st.expander("View workbook coverage against current effective targets", expanded=False):
            st.dataframe(
                coverage,
                width="stretch",
                hide_index=True,
                column_config={"Coverage %": st.column_config.NumberColumn("Coverage %", format="%.1f%%")},
            )

    if selected_muni is None:
        st.markdown("#### Municipality Summary")
        municipality = build_municipality_summary(entries_view)
        display = municipality.copy()
        display["Latest Activity"] = display["Latest Activity"].map(
            lambda value: value.strftime("%b %d, %Y") if hasattr(value, "strftime") else ""
        )
        st.dataframe(display, width="stretch", hide_index=True)
        st.download_button(
            "Download Workbook Municipality Summary (CSV)",
            data=municipality.to_csv(index=False).encode("utf-8-sig"),
            file_name="SBI_RHU_Workbook_Municipality_Summary.csv",
            mime="text/csv",
            width="stretch",
            key="sbi_workbook_muni_csv",
        )

        chart = municipality[VACCINE_COLUMNS + ["Municipality"]].melt(
            id_vars=["Municipality"],
            value_vars=VACCINE_COLUMNS,
            var_name="Indicator",
            value_name="Vaccinated",
        )
        chart = chart.loc[chart["Vaccinated"].gt(0)].copy()
        if not chart.empty:
            fig = px.bar(
                chart,
                x="Vaccinated",
                y="Municipality",
                color="Indicator",
                orientation="h",
                barmode="group",
                text_auto=".0f",
            )
            fig.update_layout(
                dragmode=False,
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="Vaccinated",
                yaxis_title="",
                height=max(460, len(ABRA_MUNIS) * 30),
                legend=dict(orientation="h", yanchor="top", y=-0.08, xanchor="center", x=0.5),
                legend_title_text="",
            )
            st.plotly_chart(fig, width="stretch", key="sbi_workbook_muni_chart")

    daily = build_daily_summary(entries_view)
    if not daily.empty:
        st.markdown("#### Daily Activity Trend")
        daily_long = daily.melt(
            id_vars=["Activity Date"],
            value_vars=VACCINE_COLUMNS,
            var_name="Indicator",
            value_name="Vaccinated",
        )
        fig_daily = px.line(
            daily_long,
            x="Activity Date",
            y="Vaccinated",
            color="Indicator",
            markers=True,
        )
        fig_daily.update_layout(
            dragmode=False,
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="Activity Date",
            yaxis_title="Vaccinated",
            legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="center", x=0.5),
            legend_title_text="",
        )
        st.plotly_chart(fig_daily, width="stretch", key="sbi_workbook_daily_chart")

    schools = build_school_summary(entries_view)
    if not schools.empty:
        st.markdown("#### School-Level Workbook Totals")
        if selected_muni is None:
            municipalities = [m for m in ABRA_MUNIS if normalize_municipality_key(m) in set(schools["Municipality"].map(normalize_municipality_key))]
            if municipalities:
                school_muni = st.selectbox(
                    "Municipality for school table",
                    municipalities,
                    key="sbi_workbook_school_muni",
                )
                schools = schools.loc[
                    schools["Municipality"].map(normalize_municipality_key).eq(normalize_municipality_key(school_muni))
                ].copy()
        st.dataframe(schools, width="stretch", hide_index=True)
        st.download_button(
            "Download School Workbook Totals (CSV)",
            data=schools.to_csv(index=False).encode("utf-8-sig"),
            file_name="SBI_RHU_Workbook_School_Totals.csv",
            mime="text/csv",
            width="stretch",
            key="sbi_workbook_school_csv",
        )

    st.markdown("#### Deferrals & Refusals")
    deferrals = int(
        entries_view[["mr_deferred", "td_deferred", "hpv1_deferred", "hpv2_deferred"]].sum().sum()
    )
    refusals = int(
        entries_view[["mr_refused", "td_refused", "hpv1_refused", "hpv2_refused"]].sum().sum()
    )
    d1, d2 = st.columns(2)
    d1.metric("Total Deferred", f"{deferrals:,}")
    d2.metric("Total Refused", f"{refusals:,}")
