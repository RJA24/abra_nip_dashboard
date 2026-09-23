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
from core.map_labels import canonical_municipality_name, normalize_municipality_key


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
    return "Check RHU Tracker"


def _summary_table(entries: pd.DataFrame, events: dict[str, pd.DataFrame], metrics: Iterable[str] | None = None) -> pd.DataFrame:
    rows = []
    for metric in metrics or METRICS.keys():
        tracker_value, tracker_rows = _sum_tracker_metric(entries, metric)
        vacc_value = _sum_event_metric(events, metric)
        diff = tracker_value - vacc_value
        rows.append(
            {
                "Metric": metric,
                "RHU Tracker": int(round(tracker_value)),
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


def _daily_reconciliation(entries: pd.DataFrame, events: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Compare RHU Activity Date with VaccTrack Report Date, metric by metric.

    Rows where neither source has activity for a metric are omitted so a Grade 1
    activity date does not incorrectly flag Grade 4 or Grade 7 as Not Updated.
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

            # Skip vaccine/grade combinations with no activity from either source.
            if tracker_rows == 0 and abs(vacc_value) < 0.5:
                continue

            diff = tracker_value - vacc_value
            rows.append(
                {
                    "Date": target_date,
                    "Metric": metric,
                    "RHU Tracker": int(round(tracker_value)),
                    "VaccTrack": int(round(vacc_value)),
                    "Difference": int(round(diff)),
                    "Status": _status(diff, tracker_rows),
                }
            )

    return pd.DataFrame(
        rows,
        columns=["Date", "Metric", "RHU Tracker", "VaccTrack", "Difference", "Status"],
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
        daily.assign(_issue=daily["Status"].ne("Matched").astype(int))
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
        'Date comparison uses <strong>RHU Tracker Activity Date</strong> versus '
        '<strong>VaccTrack Report Date</strong>. If cumulative totals match but daily rows do not, '
        'check whether VaccTrack was encoded under a later report date.</div>',
        unsafe_allow_html=True,
    )

    issue_dates = sorted(
        daily.loc[daily["Status"].ne("Matched"), "Date"].dropna().unique().tolist(),
        reverse=True,
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
        return pd.DataFrame(columns=["School ID", "School Name", "RHU Tracker", "Tracker Rows"])
    subset = entries[entries["grade_level"].astype(str).eq(config["grade"])].copy()
    if subset.empty:
        return pd.DataFrame(columns=["School ID", "School Name", "RHU Tracker", "Tracker Rows"])
    subset["School ID"] = subset["school_id"].map(_clean_school_id)
    subset["School Name"] = subset.get("school_name", "").fillna("").astype(str)
    subset["_value"] = 0.0
    for col in config["tracker"]:
        subset["_value"] += pd.to_numeric(subset.get(col), errors="coerce").fillna(0)
    return (
        subset.groupby("School ID", as_index=False)
        .agg({"School Name": "last", "_value": "sum", "school_id": "count"})
        .rename(columns={"_value": "RHU Tracker", "school_id": "Tracker Rows"})
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
        merged["RHU Tracker"] = pd.to_numeric(merged.get("RHU Tracker"), errors="coerce").fillna(0)
        merged["VaccTrack"] = pd.to_numeric(merged.get("VaccTrack"), errors="coerce").fillna(0)
        merged["Tracker Rows"] = pd.to_numeric(merged.get("Tracker Rows"), errors="coerce").fillna(0).astype(int)
        merged["Difference"] = merged["RHU Tracker"] - merged["VaccTrack"]
        merged["Status"] = merged.apply(lambda r: _status(r["Difference"], int(r["Tracker Rows"])), axis=1)
        merged["Metric"] = metric
        frames.append(merged[["Metric", "School ID", "School Name", "RHU Tracker", "VaccTrack", "Difference", "Status"]])
    if not frames:
        return pd.DataFrame(columns=["Metric", "School ID", "School Name", "RHU Tracker", "VaccTrack", "Difference", "Status"])
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
        '<h3><i class="fa-solid fa-scale-balanced" style="color:#0033A0;margin-right:8px;"></i>VaccTrack Check</h3>',
        unsafe_allow_html=True,
    )
    st.markdown("RHU Tracker values are compared with cumulative VaccTrack values for the selected dashboard reporting period.")
    all_entries = _fetch_entries(supabase, assigned_muni)
    entries = _entries_for_muni_period(all_entries, assigned_muni, start_date, end_date)
    events = _events_for_muni_period(g1_events, g7_events, hpv_events, assigned_muni, start_date, end_date)
    summary = _summary_table(entries, events)
    st.dataframe(summary, width="stretch", hide_index=True)

    st.divider()
    _render_daily_discrepancy_tally(
        entries,
        events,
        key_prefix="rhu_check",
        filename_prefix=f"SBI_RHU_vs_VaccTrack_{assigned_muni.replace(' ', '_')}",
    )

    st.divider()
    school = _school_comparison(entries, events)
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
) -> None:
    st.markdown(
        '<h3><i class="fa-solid fa-scale-balanced" style="color:#0033A0;margin-right:8px;"></i>RHU Tracker vs VaccTrack</h3>',
        unsafe_allow_html=True,
    )
    st.markdown("VaccTrack remains the official final dataset. This page highlights differences against RHU-entered accomplishment totals.")
    try:
        all_entries = _fetch_entries(supabase)
    except Exception as exc:
        st.error(f"Unable to load RHU accomplishment records: {exc}")
        return

    province_entries = _filter_period(all_entries, "activity_date", start_date, end_date) if not all_entries.empty else all_entries
    province_events = {
        "g1": _filter_event_period(g1_events, start_date, end_date),
        "g7": _filter_event_period(g7_events, start_date, end_date),
        "g4": _filter_event_period(hpv_events, start_date, end_date),
    }
    province_summary = _summary_table(province_entries, province_events)
    st.markdown("#### Abra Summary")
    st.dataframe(province_summary, width="stretch", hide_index=True)

    metric = st.selectbox("Municipality reconciliation metric", list(METRICS.keys()), key="rhu_coord_metric")
    muni_rows = []
    for muni in ABRA_MUNIS:
        muni_entries = _entries_for_muni_period(all_entries, muni, start_date, end_date) if not all_entries.empty else pd.DataFrame()
        muni_events = _events_for_muni_period(g1_events, g7_events, hpv_events, muni, start_date, end_date)
        row = _summary_table(muni_entries, muni_events, [metric]).iloc[0].to_dict()
        row["Municipality"] = muni
        muni_rows.append(row)
    muni_table = pd.DataFrame(muni_rows)[["Municipality", "RHU Tracker", "VaccTrack", "Difference", "Status"]]
    st.markdown("#### Municipality Reconciliation")
    st.dataframe(muni_table, width="stretch", hide_index=True)

    drill_muni = selected_muni if selected_muni in ABRA_MUNIS else None
    drill_index = ABRA_MUNIS.index(drill_muni) if drill_muni in ABRA_MUNIS else 0
    drill_muni = st.selectbox("School discrepancy municipality", ABRA_MUNIS, index=drill_index, key="rhu_coord_drill_muni")
    drill_entries = _entries_for_muni_period(all_entries, drill_muni, start_date, end_date) if not all_entries.empty else pd.DataFrame()
    drill_events = _events_for_muni_period(g1_events, g7_events, hpv_events, drill_muni, start_date, end_date)

    st.divider()
    _render_daily_discrepancy_tally(
        drill_entries,
        drill_events,
        key_prefix="rhu_coord",
        filename_prefix=f"SBI_RHU_vs_VaccTrack_{drill_muni.replace(' ', '_')}",
    )

    st.divider()
    school = _school_comparison(drill_entries, drill_events)
    if school.empty:
        st.write(f"No RHU Tracker or VaccTrack school data is available for {drill_muni} in this period.")
        return
    show_all = st.toggle("Show matched schools too", value=False, key="rhu_coord_show_all")
    if not show_all:
        school = school[school["Status"].ne("Matched")].copy()
    st.markdown(f"#### School-Level Reconciliation: {drill_muni}")
    st.dataframe(school.sort_values(["Status", "Metric", "School Name"]), width="stretch", hide_index=True)
    st.download_button(
        "Download Reconciliation (CSV)",
        data=school.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"SBI_RHU_vs_VaccTrack_{drill_muni.replace(' ', '_')}.csv",
        mime="text/csv",
        key="rhu_coord_recon_csv",
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
) -> None:
    ready, _ = schema_available(supabase)
    if not ready:
        st.error("RHU Accomplishment Tracker is not initialized. Run supabase/003_sbi_rhu_accomplishments.sql once, then reload the app.")
        return

    if user_role == "RHU Encoder":
        canonical = _canonical_muni(assigned_muni)
        valid = {normalize_municipality_key(m): m for m in ABRA_MUNIS}
        if normalize_municipality_key(canonical) not in valid:
            st.error("This RHU account has no valid assigned municipality. Ask the System Administrator to update the RHU account assignment.")
            return
        canonical = valid[normalize_municipality_key(canonical)]
        entry_tab, mine_tab, check_tab = st.tabs(["Accomplishment Entry", "My Accomplishments", "VaccTrack Check"])
        with entry_tab:
            _render_entry(supabase, targets, canonical, str(st.session_state.get("username") or st.session_state.get("user_name") or canonical))
        with mine_tab:
            _render_my_accomplishments(supabase, canonical)
        with check_tab:
            _render_check(supabase, canonical, g1_events, g7_events, hpv_events, report_start, report_end)
    else:
        _render_coordinator_view(
            supabase,
            g1_events,
            g7_events,
            hpv_events,
            report_start,
            report_end,
            selected_muni,
        )
