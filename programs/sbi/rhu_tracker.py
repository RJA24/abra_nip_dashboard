"""RHU accomplishment entry and VaccTrack reconciliation for SBI.

VaccTrack remains the official/final SBI dataset. This module stores a small,
independent RHU accomplishment tracker so the provincial team can identify
encoding gaps between RHU-reported accomplishments and VaccTrack.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

import numpy as np
import pandas as pd
import pytz
import streamlit as st

from core.config import ABRA_MUNIS
from core.data import fetch_sbi_vacctrack_source_info, vacctrack_google_fallback_enabled
from core.map_labels import canonical_municipality_name, normalize_municipality_key
from programs.sbi.help_content import RHU_FAQ_MD, RHU_FULL_GUIDE_MD
from programs.sbi.aggregate_workbook import render_workbook_download, render_workbook_upload
from programs.sbi.campaign_control import get_campaign_config
from programs.sbi.support import render_feedback_form
from programs.sbi.workbook_dashboard import prepare_workbook_entries, render_workbook_dashboard

MANILA_TZ = pytz.timezone("Asia/Manila")
TABLE_NAME = "sbi_rhu_accomplishments"

GRADE_CONFIG = {
    "Grade 1": {
        "code": "G1",
        "target": "G1 Total",
        "fields": ["MR Male", "MR Female", "Td Male", "Td Female"],
    },
    "Grade 4": {
        "code": "G4",
        "target": "G4 Female",
        "fields": ["HPV Dose 1", "HPV Dose 2"],
    },
    "Grade 5": {
        "code": "G5",
        "target": "Unvaccinated G5 Female",
        "fields": ["HPV Dose 1", "HPV Dose 2"],
    },
    "Grade 7": {
        "code": "G7",
        "target": "G7 Total",
        "fields": ["MR Male", "MR Female", "Td Male", "Td Female"],
    },
}

METRICS = {
    "G1 MR": {"grade": "G1", "tracker": ("mr_male", "mr_female"), "event": "g1", "event_col": "MR Doses"},
    "G1 Td": {"grade": "G1", "tracker": ("td_male", "td_female"), "event": "g1", "event_col": "Td Doses"},
    "G4 HPV Dose 1": {"grade": "G4", "tracker": ("hpv_dose1",), "event": "g4", "event_col": "HPV Dose 1"},
    "G4 HPV Dose 2": {"grade": "G4", "tracker": ("hpv_dose2",), "event": "g4", "event_col": "HPV Dose 2"},
    "G7 MR": {"grade": "G7", "tracker": ("mr_male", "mr_female"), "event": "g7", "event_col": "MR Doses"},
    "G7 Td": {"grade": "G7", "tracker": ("td_male", "td_female"), "event": "g7", "event_col": "Td Doses"},
}

DB_TO_UI = {
    "mr_male": "MR Male",
    "mr_female": "MR Female",
    "td_male": "Td Male",
    "td_female": "Td Female",
    "hpv_dose1": "HPV Dose 1",
    "hpv_dose2": "HPV Dose 2",
}
UI_TO_DB = {value: key for key, value in DB_TO_UI.items()}


def schema_available(supabase) -> tuple[bool, str]:
    try:
        supabase.table(TABLE_NAME).select("id").limit(1).execute()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _canonical_muni(value: object) -> str:
    return canonical_municipality_name(str(value or "").strip())


def _same_muni(value: object, municipality: str) -> bool:
    return normalize_municipality_key(value) == normalize_municipality_key(municipality)


def _clean_school_id(value: object) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _fetch_entries(supabase, municipality: str | None = None) -> pd.DataFrame:
    rows: list[dict] = []
    offset = 0
    limit = 1000
    while True:
        query = supabase.table(TABLE_NAME).select("*")
        if municipality:
            query = query.eq("municipality", _canonical_muni(municipality))
        response = query.range(offset, offset + limit - 1).execute()
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if "activity_date" in df.columns:
        df["activity_date"] = pd.to_datetime(df["activity_date"], errors="coerce").dt.date
    if "school_id" in df.columns:
        df["school_id"] = df["school_id"].map(_clean_school_id)
    if "municipality" in df.columns:
        df["municipality"] = df["municipality"].map(_canonical_muni)
    return df


def _filter_period(df: pd.DataFrame, column: str, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    if df is None or df.empty or column not in df.columns:
        return df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
    out = df.copy()
    dates = pd.to_datetime(out[column], errors="coerce").dt.date
    if start_date:
        out = out.loc[dates >= start_date].copy()
        dates = pd.to_datetime(out[column], errors="coerce").dt.date
    if end_date:
        out = out.loc[dates <= end_date].copy()
    return out


def _filter_event_muni(df: pd.DataFrame, municipality: str) -> pd.DataFrame:
    if df is None or df.empty or "Municipality" not in df.columns:
        return pd.DataFrame(columns=df.columns if isinstance(df, pd.DataFrame) else None)
    keys = df["Municipality"].map(normalize_municipality_key)
    return df.loc[keys.eq(normalize_municipality_key(municipality))].copy()


def _filter_event_period(df: pd.DataFrame, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    return _filter_period(df, "Report Date", start_date, end_date)


def _school_roster(targets: pd.DataFrame, municipality: str, grade_label: str) -> pd.DataFrame:
    config = GRADE_CONFIG[grade_label]
    columns = ["Municipality", "Barangay", "School ID", "School Name", config["target"]]
    if targets is None or targets.empty:
        return pd.DataFrame(columns=["School ID", "School Name", "Barangay", "Target"])

    work = targets.copy()
    if "Municipality" not in work.columns:
        return pd.DataFrame(columns=["School ID", "School Name", "Barangay", "Target"])
    work = work.loc[work["Municipality"].map(lambda x: _same_muni(x, municipality))].copy()
    for col in columns:
        if col not in work.columns:
            work[col] = "" if col not in {config["target"]} else 0
    work = work[columns].copy()
    work["School ID"] = work["School ID"].map(_clean_school_id)
    work["Target"] = pd.to_numeric(work[config["target"]], errors="coerce").fillna(0)
    work["School Name"] = work["School Name"].fillna("").astype(str).str.strip()
    work["Barangay"] = work["Barangay"].fillna("").astype(str).str.strip()
    work = work[work["School ID"].ne("")].copy()
    work = (
        work.groupby("School ID", as_index=False)
        .agg({"School Name": "first", "Barangay": "first", "Target": "sum"})
        .sort_values("School Name")
        .reset_index(drop=True)
    )
    return work


def _existing_for(entries: pd.DataFrame, activity_date: date, grade_code: str) -> pd.DataFrame:
    if entries.empty:
        return pd.DataFrame()
    out = entries.copy()
    if "activity_date" not in out.columns or "grade_level" not in out.columns:
        return pd.DataFrame()
    return out[(out["activity_date"] == activity_date) & (out["grade_level"].astype(str) == grade_code)].copy()


def _prepare_editor(roster: pd.DataFrame, existing: pd.DataFrame, grade_label: str) -> pd.DataFrame:
    config = GRADE_CONFIG[grade_label]
    editor = roster.copy()
    fields = config["fields"]
    for field in fields:
        editor[field] = pd.Series([pd.NA] * len(editor), dtype="Float64")

    if not existing.empty:
        existing = existing.copy()
        existing["school_id"] = existing["school_id"].map(_clean_school_id)
        existing = existing.set_index("school_id", drop=False)
        for idx, row in editor.iterrows():
            school_id = row["School ID"]
            if school_id not in existing.index:
                continue
            source = existing.loc[school_id]
            if isinstance(source, pd.DataFrame):
                source = source.iloc[-1]
            for field in fields:
                db_col = UI_TO_DB[field]
                value = source.get(db_col)
                if value is not None and not pd.isna(value):
                    editor.at[idx, field] = float(value)

    if grade_label in {"Grade 1", "Grade 7"}:
        order = ["School ID", "School Name", "Barangay", "Target", "MR Male", "MR Female", "Td Male", "Td Female"]
    else:
        order = ["School ID", "School Name", "Barangay", "Target", "HPV Dose 1", "HPV Dose 2"]
    return editor[order]


def _row_state(row: pd.Series, fields: list[str]) -> str:
    values = [row.get(field) for field in fields]
    present = [not pd.isna(v) and v is not None for v in values]
    if not any(present):
        return "blank"
    if not all(present):
        return "partial"
    return "complete"


def _validate_editor(df: pd.DataFrame, fields: list[str]) -> list[str]:
    errors: list[str] = []
    for _, row in df.iterrows():
        state = _row_state(row, fields)
        if state == "blank":
            continue
        school = str(row.get("School Name") or row.get("School ID") or "School")
        if state == "partial":
            errors.append(f"{school}: complete all accomplishment fields or leave the entire row blank.")
            continue
        for field in fields:
            value = pd.to_numeric(pd.Series([row.get(field)]), errors="coerce").iloc[0]
            if pd.isna(value) or value < 0 or float(value) != int(value):
                errors.append(f"{school}: {field} must be a whole number of 0 or higher.")
    return errors


def _save_editor(
    supabase,
    municipality: str,
    activity_date: date,
    grade_label: str,
    edited: pd.DataFrame,
    valid_school_ids: set[str],
    username: str,
    existing: pd.DataFrame,
) -> tuple[int, int]:
    config = GRADE_CONFIG[grade_label]
    grade_code = config["code"]
    fields = config["fields"]
    canonical = _canonical_muni(municipality)
    records: list[dict] = []
    blank_ids: list[str] = []

    for _, row in edited.iterrows():
        school_id = _clean_school_id(row.get("School ID"))
        if school_id not in valid_school_ids:
            continue
        state = _row_state(row, fields)
        if state == "blank":
            blank_ids.append(school_id)
            continue
        record = {
            "municipality": canonical,
            "school_id": school_id,
            "school_name": str(row.get("School Name") or "").strip(),
            "barangay": str(row.get("Barangay") or "").strip(),
            "activity_date": activity_date.isoformat(),
            "grade_level": grade_code,
            "mr_male": None,
            "mr_female": None,
            "td_male": None,
            "td_female": None,
            "hpv_dose1": None,
            "hpv_dose2": None,
            "updated_by": username,
            "updated_at": datetime.now(MANILA_TZ).isoformat(),
            "source_type": "manual",
            "source_batch_id": None,
        }
        for field in fields:
            record[UI_TO_DB[field]] = int(float(row[field]))
        records.append(record)

    if records:
        supabase.table(TABLE_NAME).upsert(
            records,
            on_conflict="municipality,school_id,activity_date,grade_level",
        ).execute()

    deleted = 0
    if blank_ids and not existing.empty:
        existing_ids = set(existing["school_id"].map(_clean_school_id).tolist())
        to_delete = sorted(set(blank_ids) & existing_ids)
        if to_delete:
            (
                supabase.table(TABLE_NAME)
                .delete()
                .eq("municipality", canonical)
                .eq("activity_date", activity_date.isoformat())
                .eq("grade_level", grade_code)
                .in_("school_id", to_delete)
                .execute()
            )
            deleted = len(to_delete)
    return len(records), deleted


def _sum_tracker_metric(entries: pd.DataFrame, metric: str) -> tuple[float, int]:
    config = METRICS[metric]
    if entries.empty:
        return 0.0, 0
    subset = entries[entries["grade_level"].astype(str).eq(config["grade"])].copy()
    if subset.empty:
        return 0.0, 0
    total = pd.Series(0.0, index=subset.index)
    for col in config["tracker"]:
        total = total.add(pd.to_numeric(subset.get(col), errors="coerce").fillna(0), fill_value=0)
    return float(total.sum()), len(subset)


def _sum_event_metric(events: dict[str, pd.DataFrame], metric: str) -> float:
    config = METRICS[metric]
    frame = events.get(config["event"], pd.DataFrame())
    if frame.empty or config["event_col"] not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[config["event_col"]], errors="coerce").fillna(0).sum())


def _status(diff: float, tracker_rows: int) -> str:
    if tracker_rows == 0:
        return "Not Updated"
    if abs(diff) < 0.5:
        return "Matched"
    if diff > 0:
        return "Check VaccTrack"
    return "Check RHU Workbook"


def _summary_table(entries: pd.DataFrame, events: dict[str, pd.DataFrame], metrics: Iterable[str] | None = None) -> pd.DataFrame:
    rows = []
    for metric in metrics or METRICS.keys():
        tracker_value, tracker_rows = _sum_tracker_metric(entries, metric)
        vacc_value = _sum_event_metric(events, metric)
        diff = tracker_value - vacc_value
        rows.append(
            {
                "Metric": metric,
                "RHU Workbook": int(round(tracker_value)),
                "VaccTrack": int(round(vacc_value)),
                "Difference": int(round(diff)),
                "Status": _status(diff, tracker_rows),
            }
        )
    return pd.DataFrame(rows)


def _events_for_exact_date(events: dict[str, pd.DataFrame], target_date: date) -> dict[str, pd.DataFrame]:
    """Return VaccTrack event rows whose Report Date matches one calendar date."""
    result: dict[str, pd.DataFrame] = {}
    for key, frame in events.items():
        if frame is None or frame.empty or "Report Date" not in frame.columns:
            result[key] = pd.DataFrame(columns=frame.columns if isinstance(frame, pd.DataFrame) else None)
            continue
        dates = pd.to_datetime(frame["Report Date"], errors="coerce").dt.date
        result[key] = frame.loc[dates.eq(target_date)].copy()
    return result


def _entries_for_exact_date(entries: pd.DataFrame, target_date: date) -> pd.DataFrame:
    if entries is None or entries.empty or "activity_date" not in entries.columns:
        return pd.DataFrame(columns=entries.columns if isinstance(entries, pd.DataFrame) else None)
    dates = pd.to_datetime(entries["activity_date"], errors="coerce").dt.date
    return entries.loc[dates.eq(target_date)].copy()


def _latest_vacctrack_report_date(events: dict[str, pd.DataFrame]) -> date | None:
    dates: list[date] = []
    for frame in events.values():
        if frame is None or frame.empty or "Report Date" not in frame.columns:
            continue
        parsed = pd.to_datetime(frame["Report Date"], errors="coerce").dropna()
        if not parsed.empty:
            dates.extend(parsed.dt.date.tolist())
    return max(dates) if dates else None


def _metric_vacctrack_report_date(events: dict[str, pd.DataFrame], metric: str) -> date | None:
    config = METRICS[metric]
    frame = events.get(config["event"], pd.DataFrame())
    if frame is None or frame.empty or "Report Date" not in frame.columns:
        return None
    parsed = pd.to_datetime(frame["Report Date"], errors="coerce").dropna()
    return parsed.dt.date.max() if not parsed.empty else None


def _safe_school_cutoff(events: dict[str, pd.DataFrame]) -> date | None:
    cutoffs = []
    for event_key in ["g1", "g4", "g7"]:
        frame = events.get(event_key, pd.DataFrame())
        if frame is None or frame.empty or "Report Date" not in frame.columns:
            continue
        parsed = pd.to_datetime(frame["Report Date"], errors="coerce").dropna()
        if not parsed.empty:
            cutoffs.append(parsed.dt.date.max())
    return min(cutoffs) if cutoffs else None


def _fresh_summary_table(entries: pd.DataFrame, events: dict[str, pd.DataFrame], metrics: Iterable[str] | None = None) -> pd.DataFrame:
    rows = []
    for metric in metrics or METRICS.keys():
        cutoff = _metric_vacctrack_report_date(events, metric)
        if entries is None or entries.empty:
            verified = pd.DataFrame()
            pending = pd.DataFrame()
        else:
            metric_grade = METRICS[metric]["grade"]
            metric_entries = entries.loc[entries["grade_level"].astype(str).eq(metric_grade)].copy()
            if cutoff is None:
                verified = metric_entries.iloc[0:0].copy()
                pending = metric_entries
            else:
                dates = pd.to_datetime(metric_entries["activity_date"], errors="coerce").dt.date
                verified = metric_entries.loc[dates <= cutoff].copy()
                pending = metric_entries.loc[dates > cutoff].copy()
        tracker_value, tracker_rows = _sum_tracker_metric(verified, metric)
        pending_value, pending_rows = _sum_tracker_metric(pending, metric)
        vacc_value = _sum_event_metric(events, metric)
        diff = tracker_value - vacc_value
        rows.append({
            "Metric": metric,
            "RHU Workbook": int(round(tracker_value)),
            "VaccTrack": int(round(vacc_value)),
            "Difference": int(round(diff)),
            "Status": _status(diff, tracker_rows),
            "Pending RHU": int(round(pending_value)),
            "VaccTrack Through": cutoff,
        })
    return pd.DataFrame(rows)


def _verified_entries(entries: pd.DataFrame, latest_vacctrack_date: date | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if entries is None or entries.empty:
        empty = entries.copy() if isinstance(entries, pd.DataFrame) else pd.DataFrame()
        return empty, empty
    dates = pd.to_datetime(entries["activity_date"], errors="coerce").dt.date
    if latest_vacctrack_date is None:
        return entries.iloc[0:0].copy(), entries.copy()
    verified = entries.loc[dates <= latest_vacctrack_date].copy()
    pending = entries.loc[dates > latest_vacctrack_date].copy()
    return verified, pending


def _daily_reconciliation(entries: pd.DataFrame, events: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Compare RHU Activity Date with the latest available VaccTrack Report Date.

    RHU dates newer than the latest report date present in the current VaccTrack
    extract are marked Pending VaccTrack Verification instead of being treated as
    discrepancies. This prevents stale/manual extracts from creating false alarms.
    """
    tracker_dates: set[date] = set()
    if entries is not None and not entries.empty and "activity_date" in entries.columns:
        tracker_dates = set(
            pd.to_datetime(entries["activity_date"], errors="coerce").dropna().dt.date.tolist()
        )

    vacc_dates: set[date] = set()
    for frame in events.values():
        if frame is None or frame.empty or "Report Date" not in frame.columns:
            continue
        vacc_dates.update(
            pd.to_datetime(frame["Report Date"], errors="coerce").dropna().dt.date.tolist()
        )

    rows: list[dict] = []
    for target_date in sorted(tracker_dates | vacc_dates):
        day_entries = _entries_for_exact_date(entries, target_date)
        day_events = _events_for_exact_date(events, target_date)

        for metric in METRICS.keys():
            tracker_value, tracker_rows = _sum_tracker_metric(day_entries, metric)
            vacc_value = _sum_event_metric(day_events, metric)

            if tracker_rows == 0 and abs(vacc_value) < 0.5:
                continue

            metric_cutoff = _metric_vacctrack_report_date(events, metric)
            if tracker_rows > 0 and (metric_cutoff is None or target_date > metric_cutoff):
                rows.append(
                    {
                        "Date": target_date,
                        "Metric": metric,
                        "RHU Workbook": int(round(tracker_value)),
                        "VaccTrack": pd.NA,
                        "Difference": pd.NA,
                        "Status": "Pending VaccTrack Verification",
                    }
                )
                continue

            diff = tracker_value - vacc_value
            rows.append(
                {
                    "Date": target_date,
                    "Metric": metric,
                    "RHU Workbook": int(round(tracker_value)),
                    "VaccTrack": int(round(vacc_value)),
                    "Difference": int(round(diff)),
                    "Status": _status(diff, tracker_rows),
                }
            )

    return pd.DataFrame(
        rows,
        columns=["Date", "Metric", "RHU Workbook", "VaccTrack", "Difference", "Status"],
    )

def _daily_difference_matrix(daily: pd.DataFrame) -> pd.DataFrame:
    if daily is None or daily.empty:
        return pd.DataFrame(columns=["Date", *METRICS.keys(), "Issues"])

    pivot = daily.pivot_table(
        index="Date",
        columns="Metric",
        values="Difference",
        aggfunc="sum",
    ).reindex(columns=list(METRICS.keys()))

    issues = (
        daily.assign(_issue=(~daily["Status"].isin(["Matched", "Pending VaccTrack Verification"])).astype(int))
        .groupby("Date")["_issue"]
        .sum()
    )

    def _fmt(value):
        if pd.isna(value):
            return "—"
        value = int(round(float(value)))
        return f"+{value}" if value > 0 else str(value)

    out = pivot.map(_fmt).reset_index()
    out["Issues"] = out["Date"].map(issues).fillna(0).astype(int)
    return out.sort_values("Date", ascending=False).reset_index(drop=True)


def _render_daily_discrepancy_tally(
    entries: pd.DataFrame,
    events: dict[str, pd.DataFrame],
    key_prefix: str,
    filename_prefix: str,
) -> None:
    daily = _daily_reconciliation(entries, events)
    if daily.empty:
        return

    st.markdown(
        '''<h4 style="margin-bottom:0.25rem;">
        <i class="fa-solid fa-calendar-days" style="color:#0033A0;margin-right:8px;"></i>
        Daily Discrepancy Tally
        </h4>''',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="color:#64748b;font-size:0.9rem;margin-bottom:0.7rem;">'
        'Date comparison uses <strong>RHU Workbook Activity Date</strong> versus '
        '<strong>VaccTrack Report Date</strong>. If cumulative totals match but daily rows do not, '
        'check whether VaccTrack was encoded under a later report date.</div>',
        unsafe_allow_html=True,
    )

    issue_dates = sorted(
        daily.loc[~daily["Status"].isin(["Matched", "Pending VaccTrack Verification"]), "Date"].dropna().unique().tolist(),
        reverse=True,
    )

    actual_issues = daily.loc[~daily["Status"].isin(["Matched", "Pending VaccTrack Verification"])].copy()
    abs_difference = int(pd.to_numeric(actual_issues.get("Difference"), errors="coerce").abs().fillna(0).sum()) if not actual_issues.empty else 0
    affected_school_ids: set[str] = set()
    for issue_date in issue_dates:
        day_entries = _entries_for_exact_date(entries, issue_date)
        day_events = _events_for_exact_date(events, issue_date)
        day_school = _school_comparison(day_entries, day_events)
        if not day_school.empty:
            day_school = day_school.loc[day_school["Status"].ne("Matched")].copy()
            affected_school_ids.update(day_school["School ID"].dropna().astype(str).tolist())

    c1, c2, c3 = st.columns(3)
    c1.metric("Discrepancy Dates", f"{len(issue_dates):,}")
    c2.metric("Affected Schools", f"{len(affected_school_ids):,}")
    c3.metric("Absolute Dose Difference", f"{abs_difference:,}")

    pending_count = int(daily["Status"].eq("Pending VaccTrack Verification").sum())
    if pending_count:
        g1_cutoff = _metric_vacctrack_report_date(events, "G1 MR")
        g4_cutoff = _metric_vacctrack_report_date(events, "G4 HPV Dose 1")
        g7_cutoff = _metric_vacctrack_report_date(events, "G7 MR")
        def _fmt_cutoff(value):
            return value.strftime("%b %d, %Y") if value else "none"
        st.info(
            f"{pending_count:,} daily metric row(s) are pending VaccTrack verification. "
            f"Current extract report dates — G1: {_fmt_cutoff(g1_cutoff)} | G4: {_fmt_cutoff(g4_cutoff)} | G7: {_fmt_cutoff(g7_cutoff)}."
        )

    show_issues_only = st.toggle(
        "Show discrepancy dates only",
        value=True,
        key=f"{key_prefix}_daily_issues_only",
    )

    matrix = _daily_difference_matrix(daily)
    if show_issues_only:
        matrix = matrix[matrix["Issues"].gt(0)].copy()

    if matrix.empty:
        st.success("No daily discrepancies were found for this selection.")
    else:
        st.dataframe(
            matrix,
            width="stretch",
            hide_index=True,
            column_config={
                "Date": st.column_config.DateColumn("Date", format="MMM DD, YYYY"),
                "Issues": st.column_config.NumberColumn("Issues", format="%d"),
            },
        )

    with st.expander("View detailed daily reconciliation", expanded=False):
        detail = daily.sort_values(
            ["Date", "Status", "Metric"],
            ascending=[False, True, True],
        ).copy()
        st.dataframe(detail, width="stretch", hide_index=True)
        st.download_button(
            "Download Daily Discrepancy Tally (CSV)",
            data=detail.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{filename_prefix}_Daily_Discrepancy_Tally.csv",
            mime="text/csv",
            key=f"{key_prefix}_daily_csv",
        )

    if not issue_dates:
        return

    inspect_date = st.selectbox(
        "Inspect discrepancy date",
        issue_dates,
        format_func=lambda d: d.strftime("%b %d, %Y") if hasattr(d, "strftime") else str(d),
        key=f"{key_prefix}_daily_date",
    )
    day_entries = _entries_for_exact_date(entries, inspect_date)
    day_events = _events_for_exact_date(events, inspect_date)
    school = _school_comparison(day_entries, day_events)
    if school.empty:
        return

    school = school[school["Status"].ne("Matched")].copy()
    if school.empty:
        st.write("No school-level discrepancy remains for the selected date.")
        return

    st.markdown(f"##### School-Level Discrepancies: {inspect_date.strftime('%b %d, %Y')}")
    st.dataframe(
        school.sort_values(["Status", "Metric", "School Name"]),
        width="stretch",
        hide_index=True,
    )


def _tracker_school_metric(entries: pd.DataFrame, metric: str) -> pd.DataFrame:
    config = METRICS[metric]
    if entries.empty:
        return pd.DataFrame(columns=["School ID", "School Name", "RHU Workbook", "Tracker Rows"])
    subset = entries[entries["grade_level"].astype(str).eq(config["grade"])].copy()
    if subset.empty:
        return pd.DataFrame(columns=["School ID", "School Name", "RHU Workbook", "Tracker Rows"])
    subset["School ID"] = subset["school_id"].map(_clean_school_id)
    subset["School Name"] = subset.get("school_name", "").fillna("").astype(str)
    subset["_value"] = 0.0
    for col in config["tracker"]:
        subset["_value"] += pd.to_numeric(subset.get(col), errors="coerce").fillna(0)
    return (
        subset.groupby("School ID", as_index=False)
        .agg({"School Name": "last", "_value": "sum", "school_id": "count"})
        .rename(columns={"_value": "RHU Workbook", "school_id": "Tracker Rows"})
    )


def _event_school_metric(events: dict[str, pd.DataFrame], metric: str) -> pd.DataFrame:
    config = METRICS[metric]
    frame = events.get(config["event"], pd.DataFrame()).copy()
    if frame.empty or "School ID" not in frame.columns or config["event_col"] not in frame.columns:
        return pd.DataFrame(columns=["School ID", "VaccTrack School Name", "VaccTrack"])
    frame["School ID"] = frame["School ID"].map(_clean_school_id)
    frame["VaccTrack School Name"] = frame.get("School Name", "").fillna("").astype(str)
    frame["_value"] = pd.to_numeric(frame[config["event_col"]], errors="coerce").fillna(0)
    return (
        frame.groupby("School ID", as_index=False)
        .agg({"VaccTrack School Name": "last", "_value": "sum"})
        .rename(columns={"_value": "VaccTrack"})
    )


def _school_comparison(entries: pd.DataFrame, events: dict[str, pd.DataFrame], metrics: Iterable[str] | None = None) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for metric in metrics or METRICS.keys():
        tracker = _tracker_school_metric(entries, metric)
        vacc = _event_school_metric(events, metric)
        merged = tracker.merge(vacc, on="School ID", how="outer")
        if merged.empty:
            continue
        merged["School Name"] = merged.get("School Name").fillna(merged.get("VaccTrack School Name")).fillna("")
        merged["RHU Workbook"] = pd.to_numeric(merged.get("RHU Workbook"), errors="coerce").fillna(0)
        merged["VaccTrack"] = pd.to_numeric(merged.get("VaccTrack"), errors="coerce").fillna(0)
        merged["Tracker Rows"] = pd.to_numeric(merged.get("Tracker Rows"), errors="coerce").fillna(0).astype(int)
        merged["Difference"] = merged["RHU Workbook"] - merged["VaccTrack"]
        merged["Status"] = merged.apply(lambda r: _status(r["Difference"], int(r["Tracker Rows"])), axis=1)
        merged["Metric"] = metric
        frames.append(merged[["Metric", "School ID", "School Name", "RHU Workbook", "VaccTrack", "Difference", "Status"]])
    if not frames:
        return pd.DataFrame(columns=["Metric", "School ID", "School Name", "RHU Workbook", "VaccTrack", "Difference", "Status"])
    return pd.concat(frames, ignore_index=True)


def _events_for_muni_period(
    g1_events: pd.DataFrame,
    g7_events: pd.DataFrame,
    hpv_events: pd.DataFrame,
    municipality: str,
    start_date: date | None,
    end_date: date | None,
) -> dict[str, pd.DataFrame]:
    return {
        "g1": _filter_event_period(_filter_event_muni(g1_events, municipality), start_date, end_date),
        "g7": _filter_event_period(_filter_event_muni(g7_events, municipality), start_date, end_date),
        "g4": _filter_event_period(_filter_event_muni(hpv_events, municipality), start_date, end_date),
    }


def _entries_for_muni_period(entries: pd.DataFrame, municipality: str, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    if entries.empty:
        return entries.copy()
    out = entries.loc[entries["municipality"].map(lambda x: _same_muni(x, municipality))].copy()
    return _filter_period(out, "activity_date", start_date, end_date)


def _render_entry(supabase, targets: pd.DataFrame, assigned_muni: str, username: str) -> None:
    st.markdown(
        f'''<h3 style="margin-bottom:0.35rem;"><i class="fa-solid fa-pen-to-square" style="color:#0033A0;margin-right:8px;"></i>Accomplishment Entry</h3>
        <div style="color:#475569;margin-bottom:1rem;">Encoding area: <strong>{assigned_muni}</strong></div>''',
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        activity_date = st.date_input("Activity Date", value=datetime.now(MANILA_TZ).date(), key="rhu_tracker_activity_date")
    with c2:
        grade_label = st.selectbox("Grade Level", list(GRADE_CONFIG.keys()), key="rhu_tracker_grade")

    roster = _school_roster(targets, assigned_muni, grade_label)
    if roster.empty:
        st.warning(f"No school roster was found for {assigned_muni}.")
        return

    all_entries = _fetch_entries(supabase, assigned_muni)
    config = GRADE_CONFIG[grade_label]
    existing = _existing_for(all_entries, activity_date, config["code"])
    if not existing.empty and "source_type" in existing.columns and existing["source_type"].astype(str).eq("linelist").any():
        st.info("This date and grade already contain line-list-derived accomplishments. Revise the learner line list instead of using the manual fallback so the aggregate totals stay traceable.")
        return
    editor_df = _prepare_editor(roster, existing, grade_label)

    column_config = {
        "School ID": st.column_config.TextColumn("School ID", disabled=True, width="small"),
        "School Name": st.column_config.TextColumn("School Name", disabled=True, width="large"),
        "Barangay": st.column_config.TextColumn("Barangay", disabled=True, width="medium"),
        "Target": st.column_config.NumberColumn("Target", disabled=True, format="%d", width="small"),
    }
    for field in config["fields"]:
        column_config[field] = st.column_config.NumberColumn(field, min_value=0, step=1, format="%d", width="small")
    edited = st.data_editor(
        editor_df,
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        column_config=column_config,
        key=f"rhu_entry_{assigned_muni}_{activity_date}_{config['code']}",
    )

    # Live totals are derived from the editable sex/dose fields; RHUs never type totals.
    if grade_label in {"Grade 1", "Grade 7"}:
        mr_total = pd.to_numeric(edited["MR Male"], errors="coerce").fillna(0).sum() + pd.to_numeric(edited["MR Female"], errors="coerce").fillna(0).sum()
        td_total = pd.to_numeric(edited["Td Male"], errors="coerce").fillna(0).sum() + pd.to_numeric(edited["Td Female"], errors="coerce").fillna(0).sum()
        m1, m2 = st.columns(2)
        m1.metric("MR Total", f"{mr_total:,.0f}")
        m2.metric("Td Total", f"{td_total:,.0f}")
    else:
        hpv1_total = pd.to_numeric(edited["HPV Dose 1"], errors="coerce").fillna(0).sum()
        hpv2_total = pd.to_numeric(edited["HPV Dose 2"], errors="coerce").fillna(0).sum()
        m1, m2 = st.columns(2)
        m1.metric("HPV Dose 1 Total", f"{hpv1_total:,.0f}")
        m2.metric("HPV Dose 2 Total", f"{hpv2_total:,.0f}")

    if st.button("Save Accomplishments", type="primary", width="stretch", key="rhu_save_accomplishments"):
        # Security check: the writable school IDs are rebuilt from the logged-in
        # RHU's assigned municipality and never accepted from a municipality selector.
        valid_school_ids = set(roster["School ID"].map(_clean_school_id))
        errors = _validate_editor(edited, config["fields"])
        if errors:
            st.error("Please correct the following before saving:\n\n" + "\n".join(f"- {e}" for e in errors[:12]))
            return
        try:
            saved, deleted = _save_editor(
                supabase,
                assigned_muni,
                activity_date,
                grade_label,
                edited,
                valid_school_ids,
                username,
                existing,
            )
            message = f"Saved {saved} school record{'s' if saved != 1 else ''}."
            if deleted:
                message += f" Cleared {deleted} blanked record{'s' if deleted != 1 else ''}."
            st.toast(message)
            st.rerun()
        except Exception as exc:
            st.error(f"Unable to save RHU accomplishments: {exc}")


def _render_my_accomplishments(supabase, assigned_muni: str) -> None:
    st.markdown(
        '<h3><i class="fa-solid fa-list-check" style="color:#0033A0;margin-right:8px;"></i>My Accomplishments</h3>',
        unsafe_allow_html=True,
    )
    entries = _fetch_entries(supabase, assigned_muni)
    if entries.empty:
        st.write("No RHU accomplishment records have been saved yet.")
        return

    work = entries.copy()
    if "grade_level" in work.columns:
        work = work[work["grade_level"].astype(str).isin(["G1", "G4", "G5", "G7"])].copy()
    if work.empty:
        st.write("No Grade 1, Grade 4, Grade 5, or Grade 7 accomplishment records are available yet.")
        return
    for col in ["mr_male", "mr_female", "td_male", "td_female", "hpv_dose1", "hpv_dose2"]:
        if col not in work.columns:
            work[col] = 0
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0)
    work["MR Total"] = work["mr_male"] + work["mr_female"]
    work["Td Total"] = work["td_male"] + work["td_female"]
    work["HPV Dose 1"] = work["hpv_dose1"]
    work["HPV Dose 2"] = work["hpv_dose2"]

    summary = (
        work.groupby(["activity_date", "grade_level"], as_index=False)
        .agg(
            Schools=("school_id", "nunique"),
            MR=("MR Total", "sum"),
            Td=("Td Total", "sum"),
            HPV1=("HPV Dose 1", "sum"),
            HPV2=("HPV Dose 2", "sum"),
        )
        .sort_values(["activity_date", "grade_level"], ascending=[False, True])
        .rename(columns={"activity_date": "Activity Date", "grade_level": "Grade"})
    )
    st.dataframe(summary, width="stretch", hide_index=True)

    with st.expander("View detailed RHU entries", expanded=False):
        detail = work[
            ["activity_date", "grade_level", "school_id", "school_name", "barangay", "MR Total", "Td Total", "HPV Dose 1", "HPV Dose 2", "updated_by", "updated_at"]
        ].copy()
        detail.columns = ["Activity Date", "Grade", "School ID", "School Name", "Barangay", "MR", "Td", "HPV Dose 1", "HPV Dose 2", "Updated By", "Updated At"]
        detail = detail.sort_values(["Activity Date", "Grade", "School Name"], ascending=[False, True, True])
        st.dataframe(detail, width="stretch", hide_index=True)
        st.download_button(
            "Download My Accomplishments (CSV)",
            data=detail.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"SBI_RHU_Accomplishments_{assigned_muni.replace(' ', '_')}.csv",
            mime="text/csv",
            key="rhu_my_accomplishments_csv",
        )


def _render_check(
    supabase,
    assigned_muni: str,
    g1_events: pd.DataFrame,
    g7_events: pd.DataFrame,
    hpv_events: pd.DataFrame,
    start_date: date | None,
    end_date: date | None,
) -> None:
    st.markdown(
        '<h3><i class="fa-solid fa-circle-check" style="color:#0033A0;margin-right:8px;"></i>Step 3 — Refresh & VaccTrack Check</h3>',
        unsafe_allow_html=True,
    )
    if vacctrack_google_fallback_enabled():
        st.markdown(
            "The comparison uses the latest official VaccTrack data available to the dashboard. "
            "Direct extracts uploaded by the System Admin are used first; if a grade has not yet been "
            "directly imported, the Google Sheet worksheet is used as fallback."
        )
    else:
        st.markdown(
            "The comparison is currently using direct VaccTrack uploads only. "
            "Google Sheet fallback has been turned off by the System Admin."
        )
    if st.button(
        "Refresh VaccTrack Data",
        width="stretch",
        key="rhu_refresh_vacctrack",
        help="Clears the dashboard cache and reloads the latest official VaccTrack source for Grade 1, Grade 4, and Grade 7.",
    ):
        st.cache_data.clear()
        st.toast("Reloading the latest VaccTrack data...")
        st.rerun()

    source_info = fetch_sbi_vacctrack_source_info()
    source_rows = []
    for grade in ("G1", "G4", "G7"):
        info = source_info.get(grade, {}) or {}
        report_date = pd.to_datetime(info.get("report_date_max"), errors="coerce")
        source_rows.append(
            {
                "Grade": grade,
                "Source": info.get("source") or "Unavailable",
                "Data Through": report_date.strftime("%b %d, %Y") if not pd.isna(report_date) else "Not available",
            }
        )
    st.dataframe(pd.DataFrame(source_rows), width="stretch", hide_index=True)

    all_entries = _fetch_entries(supabase, assigned_muni)
    entries = _entries_for_muni_period(all_entries, assigned_muni, start_date, end_date)
    events = _events_for_muni_period(g1_events, g7_events, hpv_events, assigned_muni, start_date, end_date)
    summary = _fresh_summary_table(entries, events)
    st.dataframe(summary, width="stretch", hide_index=True)
    g1_cutoff = _metric_vacctrack_report_date(events, "G1 MR")
    g4_cutoff = _metric_vacctrack_report_date(events, "G4 HPV Dose 1")
    g7_cutoff = _metric_vacctrack_report_date(events, "G7 MR")
    def _fmt_cutoff(value):
        return value.strftime("%b %d, %Y") if value else "No report date"
    st.markdown(
        f"**VaccTrack current extract through:** G1 {_fmt_cutoff(g1_cutoff)} · G4 {_fmt_cutoff(g4_cutoff)} · G7 {_fmt_cutoff(g7_cutoff)}"
    )
    if pd.to_numeric(summary.get("Pending RHU"), errors="coerce").fillna(0).sum() > 0:
        st.info("RHU workbook accomplishment values newer than the corresponding VaccTrack report date are shown as Pending RHU and are excluded from discrepancy totals until a newer extract is available.")

    school_cutoff = _safe_school_cutoff(events)
    verified_entries, _ = _verified_entries(entries, school_cutoff)

    st.divider()
    _render_daily_discrepancy_tally(
        entries,
        events,
        key_prefix="rhu_check",
        filename_prefix=f"SBI_RHU_vs_VaccTrack_{assigned_muni.replace(' ', '_')}",
    )

    st.divider()
    school = _school_comparison(verified_entries, events)
    if school.empty:
        return
    only_issues = st.toggle("Show discrepancies only", value=True, key="rhu_check_issues_only")
    if only_issues:
        school = school[school["Status"].ne("Matched")].copy()
    st.markdown("#### School-Level Check")
    st.dataframe(school.sort_values(["Status", "Metric", "School Name"]), width="stretch", hide_index=True)


def _render_coordinator_view(
    supabase,
    g1_events: pd.DataFrame,
    g7_events: pd.DataFrame,
    hpv_events: pd.DataFrame,
    start_date: date | None,
    end_date: date | None,
    selected_muni: str | None,
    all_entries: pd.DataFrame | None = None,
    *,
    key_prefix: str = "rhu_coord",
) -> None:
    st.markdown(
        '<h3><i class="fa-solid fa-scale-balanced" style="color:#0033A0;margin-right:8px;"></i>RHU Workbook vs VaccTrack</h3>',
        unsafe_allow_html=True,
    )
    st.markdown("VaccTrack remains the official final dataset. This page highlights differences against the latest RHU workbook accomplishment totals.")
    if all_entries is None:
        try:
            all_entries = _fetch_entries(supabase)
        except Exception as exc:
            st.error(f"Unable to load RHU accomplishment records: {exc}")
            return

    if selected_muni in ABRA_MUNIS:
        scope_entries = (
            _entries_for_muni_period(all_entries, selected_muni, start_date, end_date)
            if not all_entries.empty else pd.DataFrame()
        )
        scope_events = _events_for_muni_period(
            g1_events, g7_events, hpv_events, selected_muni, start_date, end_date
        )
        scope_summary = _fresh_summary_table(scope_entries, scope_events)
        st.markdown(f"#### {selected_muni} Summary")
        st.dataframe(scope_summary, width="stretch", hide_index=True)
        if pd.to_numeric(scope_summary.get("Pending RHU"), errors="coerce").fillna(0).sum() > 0:
            st.info("Some RHU workbook values are newer than the corresponding VaccTrack report date and are pending verification.")
        drill_muni = selected_muni
    else:
        province_entries = _filter_period(all_entries, "activity_date", start_date, end_date) if not all_entries.empty else all_entries
        province_events = {
            "g1": _filter_event_period(g1_events, start_date, end_date),
            "g7": _filter_event_period(g7_events, start_date, end_date),
            "g4": _filter_event_period(hpv_events, start_date, end_date),
        }
        province_summary = _fresh_summary_table(province_entries, province_events)
        st.markdown("#### Abra Summary")
        st.dataframe(province_summary, width="stretch", hide_index=True)
        if pd.to_numeric(province_summary.get("Pending RHU"), errors="coerce").fillna(0).sum() > 0:
            st.info("Some RHU workbook values are newer than their corresponding VaccTrack report date and are pending verification.")

        metric = st.selectbox("Municipality reconciliation metric", list(METRICS.keys()), key=f"{key_prefix}_metric")
        muni_rows = []
        for muni in ABRA_MUNIS:
            muni_entries = _entries_for_muni_period(all_entries, muni, start_date, end_date) if not all_entries.empty else pd.DataFrame()
            muni_events = _events_for_muni_period(g1_events, g7_events, hpv_events, muni, start_date, end_date)
            row = _fresh_summary_table(muni_entries, muni_events, [metric]).iloc[0].to_dict()
            row["Municipality"] = muni
            muni_rows.append(row)
        muni_table = pd.DataFrame(muni_rows)[["Municipality", "RHU Workbook", "VaccTrack", "Difference", "Status", "Pending RHU", "VaccTrack Through"]]
        st.markdown("#### Municipality Reconciliation")
        st.dataframe(muni_table, width="stretch", hide_index=True)

        drill_index = 0
        drill_muni = st.selectbox("School discrepancy municipality", ABRA_MUNIS, index=drill_index, key=f"{key_prefix}_drill_muni")
    drill_entries = _entries_for_muni_period(all_entries, drill_muni, start_date, end_date) if not all_entries.empty else pd.DataFrame()
    drill_events = _events_for_muni_period(g1_events, g7_events, hpv_events, drill_muni, start_date, end_date)
    drill_cutoff = _safe_school_cutoff(drill_events)
    drill_verified, _ = _verified_entries(drill_entries, drill_cutoff)

    st.divider()
    _render_daily_discrepancy_tally(
        drill_entries,
        drill_events,
        key_prefix=key_prefix,
        filename_prefix=f"SBI_RHU_vs_VaccTrack_{drill_muni.replace(' ', '_')}",
    )

    st.divider()
    school = _school_comparison(drill_verified, drill_events)
    if school.empty:
        st.write(f"No RHU Workbook or VaccTrack school data is available for {drill_muni} in this period.")
        return
    show_all = st.toggle("Show matched schools too", value=False, key=f"{key_prefix}_show_all")
    if not show_all:
        school = school[school["Status"].ne("Matched")].copy()
    st.markdown(f"#### School-Level Reconciliation: {drill_muni}")
    st.dataframe(school.sort_values(["Status", "Metric", "School Name"]), width="stretch", hide_index=True)
    st.download_button(
        "Download Reconciliation (CSV)",
        data=school.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"SBI_RHU_vs_VaccTrack_{drill_muni.replace(' ', '_')}.csv",
        mime="text/csv",
        key=f"{key_prefix}_recon_csv",
    )



def _render_rhu_encoder_process_guide(assigned_muni: str) -> None:
    """Simple three-step workflow for RHU encoders with mixed computer skills."""
    st.markdown(
        f"""
        <div style="margin:0.15rem 0 1rem 0;padding:1rem;border:1px solid #dbe4f0;border-radius:14px;background:#f8fafc;">
          <div style="font-size:1.02rem;font-weight:700;color:#0f172a;margin-bottom:0.8rem;">
            <i class="fa-solid fa-route" style="color:#0033A0;margin-right:7px;"></i>RHU Encoder Process — {assigned_muni}
          </div>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:0.7rem;">
            <div style="background:white;border:1px solid #e2e8f0;border-radius:12px;padding:0.85rem;">
              <div style="font-weight:800;color:#0033A0;font-size:1.05rem;">1. Maintain One Offline Workbook</div>
              <div style="color:#475569;font-size:0.9rem;margin-top:0.35rem;">Download your RHU workbook once, then encode every activity date and school in Excel even when internet is unavailable.</div>
            </div>
            <div style="background:white;border:1px solid #e2e8f0;border-radius:12px;padding:0.85rem;">
              <div style="font-weight:800;color:#0033A0;font-size:1.05rem;">2. Encode in VaccTrack & Upload</div>
              <div style="color:#475569;font-size:0.9rem;margin-top:0.35rem;">Use the workbook's VaccTrack sheets for daily encoding, then upload the complete current workbook when internet is available.</div>
            </div>
            <div style="background:white;border:1px solid #e2e8f0;border-radius:12px;padding:0.85rem;">
              <div style="font-weight:800;color:#0033A0;font-size:1.05rem;">3. Refresh & Check</div>
              <div style="color:#475569;font-size:0.9rem;margin-top:0.35rem;">After the latest VaccTrack extract is uploaded by the NIP coordinator, refresh and check for matched, pending, or discrepant records.</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Need help? Step-by-step guide, corrections & FAQs", expanded=False):
        guide_tab, faq_tab = st.tabs(["Full Guide", "FAQs"])
        with guide_tab:
            st.markdown(RHU_FULL_GUIDE_MD)
            st.download_button(
                "Download Full Guide (Markdown)",
                data=RHU_FULL_GUIDE_MD.encode("utf-8"),
                file_name="SBI_RHU_Encoder_Full_Guide.md",
                mime="text/markdown",
                key="sbi_rhu_full_guide_download",
            )
        with faq_tab:
            st.markdown(RHU_FAQ_MD)
            st.download_button(
                "Download FAQs (Markdown)",
                data=RHU_FAQ_MD.encode("utf-8"),
                file_name="SBI_RHU_Encoder_FAQ.md",
                mime="text/markdown",
                key="sbi_rhu_faq_download",
            )


def fetch_workbook_dashboard_entries(supabase) -> pd.DataFrame:  # noqa: ANN001
    """Load current workbook-only RHU accomplishments for read-only dashboards."""
    ready, _ = schema_available(supabase)
    if not ready:
        return pd.DataFrame()
    entries = _fetch_entries(supabase)
    workbook_entries, _ = prepare_workbook_entries(entries)
    return workbook_entries


def render_vacctrack_vs_workbook(
    supabase,
    g1_events: pd.DataFrame,
    g7_events: pd.DataFrame,
    hpv_events: pd.DataFrame,
    report_start: date | None,
    report_end: date | None,
    selected_muni: str | None = None,
    workbook_entries: pd.DataFrame | None = None,
) -> None:
    """Province-wide read-only reconciliation between RHU workbooks and VaccTrack."""
    if workbook_entries is None:
        try:
            workbook_entries = fetch_workbook_dashboard_entries(supabase)
        except Exception as exc:
            st.error(f"Unable to load current RHU workbook data: {exc}")
            return

    _render_coordinator_view(
        supabase,
        g1_events,
        g7_events,
        hpv_events,
        report_start,
        report_end,
        selected_muni,
        all_entries=workbook_entries,
        key_prefix="rhu_vtw",
    )


def render_rhu_accomplishments(
    supabase,
    targets: pd.DataFrame,
    g1_events: pd.DataFrame,
    g7_events: pd.DataFrame,
    hpv_events: pd.DataFrame,
    user_role: str,
    assigned_muni: str,
    report_start: date | None,
    report_end: date | None,
    selected_muni: str | None = None,
    actual_targets: pd.DataFrame | None = None,
) -> None:
    """Render the RHU-specific operational workflow only.

    Province-wide workbook analytics and VaccTrack reconciliation live in their
    own top-level SBI tabs. RHU Encoder write actions remain locked to the
    account's assigned municipality.
    """
    ready, _ = schema_available(supabase)
    if not ready:
        st.error("RHU Accomplishment Tracker is not initialized. Run supabase/003_sbi_rhu_accomplishments.sql once, then reload the app.")
        return

    if user_role in {"RHU Encoder", "RHU QA Encoder"}:
        is_qa_encoder = user_role == "RHU QA Encoder"
        canonical = _canonical_muni(assigned_muni)
        valid = {normalize_municipality_key(m): m for m in ABRA_MUNIS}
        if normalize_municipality_key(canonical) not in valid:
            st.error("This RHU account has no valid assigned municipality. Ask the System Administrator to update the RHU account assignment.")
            return
        canonical = valid[normalize_municipality_key(canonical)]
        username = str(st.session_state.get("username") or st.session_state.get("user_name") or canonical)

        if is_qa_encoder:
            st.warning(
                f"RHU QA TEST ACCOUNT — simulating {canonical} RHU. Workbook uploads are validation-only and do not save, replace, finalize, reopen, or alter production RHU data."
            )

        campaign = get_campaign_config(supabase)
        status = str(campaign.get("status") or "Pre-Implementation")
        announcement = str(campaign.get("announcement") or "").strip()
        if status == "Live":
            st.success("SBI campaign status: LIVE — field implementation and workbook uploads are active.")
        elif status == "Post-Activity Correction":
            st.warning(
                "SBI campaign status: POST-ACTIVITY CORRECTION — field implementation has ended, but corrected or late workbook uploads are still allowed for Activity Dates inside the official campaign period."
            )
        elif status == "Closed":
            st.warning("SBI campaign status: CLOSED — workbook uploads are no longer accepted unless the System Administrator reopens the campaign.")
        else:
            st.info("SBI campaign status: PRE-IMPLEMENTATION — testing and preparation are still in progress.")
        if announcement:
            st.info(f"NIP Coordinator Announcement: {announcement}")

        _render_rhu_encoder_process_guide(canonical)
        render_feedback_form(supabase, canonical, username, role=user_role)

        upload_label = "2. QA Validate Workbook" if is_qa_encoder else "2. Upload Current Workbook"
        mine_label = "Production RHU Accomplishments" if is_qa_encoder else "My Accomplishments"
        download_tab, upload_tab, check_tab, mine_tab = st.tabs([
            "1. Offline Workbook",
            upload_label,
            "3. VaccTrack Check",
            mine_label,
        ])

        workbook_targets = actual_targets if actual_targets is not None and not actual_targets.empty else targets
        with download_tab:
            render_workbook_download(workbook_targets, canonical)
        with upload_tab:
            render_workbook_upload(
                supabase,
                workbook_targets,
                canonical,
                username,
                dry_run=is_qa_encoder,
            )
        with check_tab:
            _render_check(
                supabase,
                canonical,
                g1_events,
                g7_events,
                hpv_events,
                report_start,
                report_end,
            )
        with mine_tab:
            _render_my_accomplishments(supabase, canonical)
        return

    # System Admin / QA Admin can inspect the complete RHU accomplishment view
    # directly in this tab. Respect the sidebar scope: All Municipalities shows
    # the Abra-wide reconciliation and municipality drill-down; Specific
    # Municipality shows the full reconciliation for that RHU. Keep the view
    # read-only here so administrative inspection cannot overwrite RHU data.
    try:
        all_entries = _fetch_entries(supabase)
        workbook_entries, _ = prepare_workbook_entries(all_entries)
    except Exception as exc:
        st.error(f"Unable to load current RHU workbook data: {exc}")
        return

    canonical_selected = _canonical_muni(selected_muni) if selected_muni else None
    if canonical_selected not in ABRA_MUNIS:
        canonical_selected = None

    _render_coordinator_view(
        supabase,
        g1_events,
        g7_events,
        hpv_events,
        report_start,
        report_end,
        canonical_selected,
        all_entries=workbook_entries,
        key_prefix="rhu_admin_accomp",
    )
