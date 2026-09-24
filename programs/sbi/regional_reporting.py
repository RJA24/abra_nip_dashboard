"""Regional SBI reporting derived from RHU learner records.

This module mirrors the metrics in the regional RHU Consolidated Accomplishment
Form while keeping VaccTrack as the official national reporting source. Grade 5
HPV dose 2 is retained here as a regional/operational category only.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Iterable

import numpy as np
import pandas as pd
import pytz
import streamlit as st

from core.map_labels import canonical_municipality_name, normalize_municipality_key
from programs.sbi.linelist import REASON_LABELS


MANILA_TZ = pytz.timezone("Asia/Manila")
RECORD_TABLE = "sbi_linelist_records"
SESSION_TABLE = "sbi_regional_sessions"

USAGE_ROWS = [
    ("G1 MR", "g1_mr"),
    ("G1 Td", "g1_td"),
    ("G4 HPV", "g4_hpv"),
    ("G5 HPV", "g5_hpv"),
    ("G7 MR", "g7_mr"),
    ("G7 Td", "g7_td"),
]


def _canonical_muni(value: object) -> str:
    return canonical_municipality_name(str(value or "").strip())


def _clean_school_id(value: object) -> str:
    text = str(value or "").strip()
    return text[:-2] if text.endswith(".0") else text


def schema_available(supabase) -> tuple[bool, str]:
    try:
        supabase.table(SESSION_TABLE).select("id").limit(1).execute()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _fetch_records(supabase, municipality: str) -> pd.DataFrame:
    rows: list[dict] = []
    offset = 0
    while True:
        response = (
            supabase.table(RECORD_TABLE)
            .select("*")
            .eq("municipality", _canonical_muni(municipality))
            .eq("is_active", True)
            .range(offset, offset + 999)
            .execute()
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    df = pd.DataFrame(rows)
    if not df.empty:
        df["activity_date"] = pd.to_datetime(df.get("activity_date"), errors="coerce").dt.date
        df["school_id"] = df.get("school_id", "").map(_clean_school_id)
    return df


def _fetch_sessions(supabase, municipality: str, start_date: date | None = None, end_date: date | None = None) -> pd.DataFrame:
    rows: list[dict] = []
    offset = 0
    while True:
        query = supabase.table(SESSION_TABLE).select("*").eq("municipality", _canonical_muni(municipality))
        if start_date:
            query = query.gte("activity_date", start_date.isoformat())
        if end_date:
            query = query.lte("activity_date", end_date.isoformat())
        response = query.range(offset, offset + 999).execute()
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    df = pd.DataFrame(rows)
    if not df.empty:
        df["activity_date"] = pd.to_datetime(df.get("activity_date"), errors="coerce").dt.date
        df["school_id"] = df.get("school_id", "").map(_clean_school_id)
    return df


def _filter_records(records: pd.DataFrame, start_date: date, end_date: date) -> pd.DataFrame:
    if records is None or records.empty:
        return pd.DataFrame(columns=records.columns if isinstance(records, pd.DataFrame) else None)
    work = records.copy()
    dates = pd.to_datetime(work["activity_date"], errors="coerce").dt.date
    return work.loc[(dates >= start_date) & (dates <= end_date)].copy()


def _status_series(frame: pd.DataFrame, status_col: str, given_col: str | None = None, hpv: bool = False) -> pd.Series:
    if status_col in frame.columns:
        series = frame[status_col].astype("string").fillna("").str.strip()
        if series.ne("").any():
            return series
    if hpv:
        dose = pd.to_numeric(frame.get("hpv_dose"), errors="coerce")
        return pd.Series(np.where(dose.notna(), "Given", ""), index=frame.index, dtype="string")
    if given_col and given_col in frame.columns:
        return frame[given_col].map(lambda value: "Given" if value is True else ("Not Given" if value is False else "")).astype("string")
    return pd.Series("", index=frame.index, dtype="string")


def _target_table(baseline: pd.DataFrame, actual: pd.DataFrame, municipality: str) -> pd.DataFrame:
    """Return one row per school using complete Actual Targets when available."""
    cols = ["Municipality", "Barangay", "School ID", "School Name", "G1 Total", "G4 Female", "G7 Total"]
    base = pd.DataFrame() if baseline is None else baseline.copy()
    act = pd.DataFrame() if actual is None else actual.copy()

    def prep(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "Municipality" not in df.columns:
            return pd.DataFrame(columns=cols)
        out = df.copy()
        out = out.loc[out["Municipality"].map(normalize_municipality_key).eq(normalize_municipality_key(municipality))].copy()
        for col in cols:
            if col not in out.columns:
                out[col] = 0 if col in {"G1 Total", "G4 Female", "G7 Total"} else ""
        out["School ID"] = out["School ID"].map(_clean_school_id)
        for col in ["G1 Total", "G4 Female", "G7 Total"]:
            out[col] = pd.to_numeric(out[col], errors="coerce")
        return out[cols]

    base = prep(base)
    if not act.empty and "Target Entry Status" in act.columns:
        act = act[act["Target Entry Status"].astype(str).eq("Complete")].copy()
    act = prep(act)

    base_map = base.set_index("School ID").to_dict("index") if not base.empty else {}
    act_map = act.set_index("School ID").to_dict("index") if not act.empty else {}
    ids = sorted(set(base_map) | set(act_map))
    rows = []
    for school_id in ids:
        row = dict(base_map.get(school_id, {}))
        if school_id in act_map:
            row.update(act_map[school_id])
            source = "Actual"
        else:
            source = "Baseline"
        row["School ID"] = school_id
        row["Target Source"] = source
        rows.append(row)
    return pd.DataFrame(rows)


def _pct(value: int | float | None, denominator: int | float | None) -> float | None:
    try:
        den = float(denominator)
        num = float(value)
    except Exception:
        return None
    if den <= 0:
        return None
    return num / den * 100.0


def _school_metrics(grp: pd.DataFrame) -> dict:
    metric_names = [
        "G1 MR", "G1 Td", "G1 MR Deferred", "G1 Td Deferred", "G1 MR Refused", "G1 Td Refused",
        "G4 HPV1", "G4 HPV2", "G4 HPV1 Deferred", "G4 HPV2 Deferred", "G4 HPV1 Refused", "G4 HPV2 Refused",
        "G5 HPV2", "G5 HPV2 Deferred", "G5 HPV2 Refused", "G5 Without HPV2", "G5 Tracked",
        "G7 MR", "G7 Td", "G7 MR Deferred", "G7 Td Deferred", "G7 MR Refused", "G7 Td Refused",
    ]
    if grp.empty:
        return {name: 0 for name in metric_names}
    mr = _status_series(grp, "mr_status", "mr_given")
    td = _status_series(grp, "td_status", "td_given")
    hpv_status = _status_series(grp, "hpv_status", hpv=True)
    hpv_dose = pd.to_numeric(grp.get("hpv_dose"), errors="coerce")
    grade = grp.get("grade_level", pd.Series("", index=grp.index)).astype(str)

    def count(mask) -> int:  # noqa: ANN001
        return int(mask.fillna(False).sum())

    return {
        "G1 MR": count(grade.eq("G1") & mr.eq("Given")),
        "G1 Td": count(grade.eq("G1") & td.eq("Given")),
        "G1 MR Deferred": count(grade.eq("G1") & mr.eq("Deferred")),
        "G1 Td Deferred": count(grade.eq("G1") & td.eq("Deferred")),
        "G1 MR Refused": count(grade.eq("G1") & mr.eq("Refused")),
        "G1 Td Refused": count(grade.eq("G1") & td.eq("Refused")),
        "G4 HPV1": count(grade.eq("G4") & hpv_dose.eq(1) & hpv_status.eq("Given")),
        "G4 HPV2": count(grade.eq("G4") & hpv_dose.eq(2) & hpv_status.eq("Given")),
        "G4 HPV1 Deferred": count(grade.eq("G4") & hpv_dose.eq(1) & hpv_status.eq("Deferred")),
        "G4 HPV2 Deferred": count(grade.eq("G4") & hpv_dose.eq(2) & hpv_status.eq("Deferred")),
        "G4 HPV1 Refused": count(grade.eq("G4") & hpv_dose.eq(1) & hpv_status.eq("Refused")),
        "G4 HPV2 Refused": count(grade.eq("G4") & hpv_dose.eq(2) & hpv_status.eq("Refused")),
        "G5 HPV2": count(grade.eq("G5") & hpv_dose.eq(2) & hpv_status.eq("Given")),
        "G5 HPV2 Deferred": count(grade.eq("G5") & hpv_dose.eq(2) & hpv_status.eq("Deferred")),
        "G5 HPV2 Refused": count(grade.eq("G5") & hpv_dose.eq(2) & hpv_status.eq("Refused")),
        "G5 Without HPV2": count(grade.eq("G5") & hpv_dose.eq(2) & hpv_status.eq("Not Given")),
        "G5 Tracked": int(grp.loc[grade.eq("G5"), "row_key"].nunique()) if "row_key" in grp.columns else count(grade.eq("G5")),
        "G7 MR": count(grade.eq("G7") & mr.eq("Given")),
        "G7 Td": count(grade.eq("G7") & td.eq("Given")),
        "G7 MR Deferred": count(grade.eq("G7") & mr.eq("Deferred")),
        "G7 Td Deferred": count(grade.eq("G7") & td.eq("Deferred")),
        "G7 MR Refused": count(grade.eq("G7") & mr.eq("Refused")),
        "G7 Td Refused": count(grade.eq("G7") & td.eq("Refused")),
    }


def build_regional_summary(
    records: pd.DataFrame,
    baseline_targets: pd.DataFrame,
    actual_targets: pd.DataFrame,
    sessions: pd.DataFrame,
    municipality: str,
) -> pd.DataFrame:
    targets = _target_table(baseline_targets, actual_targets, municipality)
    target_map = targets.set_index("School ID").to_dict("index") if not targets.empty else {}

    session_map: dict[str, dict] = {}
    if sessions is not None and not sessions.empty:
        session_work = sessions.copy()
        for col in ["g5_female_enrolled"]:
            if col not in session_work.columns:
                session_work[col] = np.nan
        for school_id, grp in session_work.groupby("school_id", dropna=False):
            vals = pd.to_numeric(grp["g5_female_enrolled"], errors="coerce").dropna()
            session_map[_clean_school_id(school_id)] = {
                "g5_female_enrolled": int(vals.max()) if not vals.empty else None,
                "school_name": str(grp.get("school_name", pd.Series([""])).iloc[0] or ""),
                "barangay": str(grp.get("barangay", pd.Series([""])).iloc[0] or ""),
            }

    record_ids = set(records["school_id"].map(_clean_school_id)) if records is not None and not records.empty else set()
    school_ids = sorted(record_ids | set(session_map))
    rows = []
    for school_id in school_ids:
        grp = records.loc[records["school_id"].map(_clean_school_id).eq(school_id)].copy() if records is not None and not records.empty else pd.DataFrame()
        metrics = _school_metrics(grp)
        tgt = target_map.get(school_id, {})
        sess = session_map.get(school_id, {})
        school_name = str(tgt.get("School Name") or sess.get("school_name") or (grp["school_name"].iloc[0] if not grp.empty else ""))
        barangay = str(tgt.get("Barangay") or sess.get("barangay") or (grp["barangay"].iloc[0] if not grp.empty else ""))
        g1_enrolled = pd.to_numeric(pd.Series([tgt.get("G1 Total")]), errors="coerce").iloc[0]
        g4_enrolled = pd.to_numeric(pd.Series([tgt.get("G4 Female")]), errors="coerce").iloc[0]
        g7_enrolled = pd.to_numeric(pd.Series([tgt.get("G7 Total")]), errors="coerce").iloc[0]
        g5_enrolled = sess.get("g5_female_enrolled")

        row = {
            "Municipality": _canonical_muni(municipality),
            "Barangay": barangay,
            "School ID": school_id,
            "School": school_name,
            "G1 Enrolled": None if pd.isna(g1_enrolled) else int(g1_enrolled),
            "G4 Female Enrolled": None if pd.isna(g4_enrolled) else int(g4_enrolled),
            "G5 Female Enrolled": g5_enrolled,
            "G7 Enrolled": None if pd.isna(g7_enrolled) else int(g7_enrolled),
            **metrics,
        }
        percentage_pairs = [
            ("G1 MR", "G1 Enrolled"), ("G1 Td", "G1 Enrolled"),
            ("G1 MR Deferred", "G1 Enrolled"), ("G1 Td Deferred", "G1 Enrolled"),
            ("G1 MR Refused", "G1 Enrolled"), ("G1 Td Refused", "G1 Enrolled"),
            ("G4 HPV1", "G4 Female Enrolled"), ("G4 HPV2", "G4 Female Enrolled"),
            ("G4 HPV1 Deferred", "G4 Female Enrolled"), ("G4 HPV2 Deferred", "G4 Female Enrolled"),
            ("G4 HPV1 Refused", "G4 Female Enrolled"), ("G4 HPV2 Refused", "G4 Female Enrolled"),
            ("G5 HPV2", "G5 Female Enrolled"), ("G5 HPV2 Deferred", "G5 Female Enrolled"),
            ("G5 HPV2 Refused", "G5 Female Enrolled"),
            ("G7 MR", "G7 Enrolled"), ("G7 Td", "G7 Enrolled"),
            ("G7 MR Deferred", "G7 Enrolled"), ("G7 Td Deferred", "G7 Enrolled"),
            ("G7 MR Refused", "G7 Enrolled"), ("G7 Td Refused", "G7 Enrolled"),
        ]
        for value_col, den_col in percentage_pairs:
            row[f"{value_col} %"] = _pct(row.get(value_col, 0), row.get(den_col))
        rows.append(row)
    return pd.DataFrame(rows)


def build_reason_summary(records: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if records is None or records.empty:
        return pd.DataFrame(columns=["Code", "Reason", "Learners"])
    work = records.copy()
    mr = _status_series(work, "mr_status", "mr_given")
    td = _status_series(work, "td_status", "td_given")
    hpv = _status_series(work, "hpv_status", hpv=True)
    missed = mr.isin(["Deferred", "Refused"]) | td.isin(["Deferred", "Refused"]) | hpv.isin(["Deferred", "Refused"])
    codes = work.get("reason_code", pd.Series("", index=work.index)).astype("string").fillna("").str.zfill(2)
    for code, label in REASON_LABELS.items():
        rows.append({"Code": code, "Reason": label, "Learners": int((missed & codes.eq(code)).sum())})
    return pd.DataFrame(rows)


def _usage_display(sessions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for label, prefix in USAGE_ROWS:
        row = {"Vaccine / Grade": label}
        for suffix, title in [("received", "Received (vials)"), ("used", "Used (vials)"), ("unused", "Unused (vials)")]:
            col = f"{prefix}_{suffix}"
            value = pd.to_numeric(sessions.get(col, pd.Series(dtype=float)), errors="coerce").sum() if not sessions.empty else 0
            row[title] = int(value or 0)
        rows.append(row)
    return pd.DataFrame(rows)


def _session_for(sessions: pd.DataFrame, school_id: str, activity_date: date) -> dict:
    if sessions is None or sessions.empty:
        return {}
    match = sessions[
        sessions["school_id"].map(_clean_school_id).eq(_clean_school_id(school_id))
        & sessions["activity_date"].eq(activity_date)
    ]
    return match.iloc[0].to_dict() if not match.empty else {}


def _save_session(supabase, municipality: str, school: dict, activity_date: date, g5_enrolled: int | None, usage: pd.DataFrame, username: str) -> None:
    existing = {}
    record = {
        "municipality": _canonical_muni(municipality),
        "school_id": _clean_school_id(school.get("School ID")),
        "school_name": str(school.get("School Name") or ""),
        "barangay": str(school.get("Barangay") or ""),
        "activity_date": activity_date.isoformat(),
        "g5_female_enrolled": int(g5_enrolled) if g5_enrolled is not None and int(g5_enrolled) > 0 else None,
        "updated_by": username,
        "updated_at": datetime.now(MANILA_TZ).isoformat(),
    }
    for _, row in usage.iterrows():
        label = str(row.get("Vaccine / Grade") or "")
        prefix = dict(USAGE_ROWS).get(label)
        if not prefix:
            continue
        for source, suffix in [("Received (vials)", "received"), ("Used (vials)", "used"), ("Unused (vials)", "unused")]:
            value = pd.to_numeric(pd.Series([row.get(source)]), errors="coerce").fillna(0).iloc[0]
            record[f"{prefix}_{suffix}"] = int(max(value, 0))
    supabase.table(SESSION_TABLE).upsert(
        record,
        on_conflict="municipality,school_id,activity_date",
    ).execute()


def _build_excel(summary: pd.DataFrame, usage: pd.DataFrame, reasons: pd.DataFrame, municipality: str, start_date: date, end_date: date) -> bytes:
    try:
        import xlsxwriter
    except ImportError as exc:
        raise RuntimeError("Excel report export requires xlsxwriter>=3.2.0 in requirements.txt") from exc

    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    ws = workbook.add_worksheet("RHU Consolidated Report")
    ws.set_landscape()
    ws.fit_to_pages(1, 0)
    ws.freeze_panes(6, 4)

    title = workbook.add_format({"bold": True, "font_size": 15, "align": "center", "valign": "vcenter", "font_color": "#0033A0"})
    subtitle = workbook.add_format({"bold": True, "font_size": 11, "align": "center"})
    group = workbook.add_format({"bold": True, "align": "center", "valign": "vcenter", "bg_color": "#DCE6F1", "border": 1})
    header = workbook.add_format({"bold": True, "align": "center", "valign": "vcenter", "text_wrap": True, "bg_color": "#EAF1FB", "border": 1})
    text_fmt = workbook.add_format({"border": 1, "valign": "vcenter"})
    int_fmt = workbook.add_format({"border": 1, "align": "center", "num_format": "0"})
    pct_fmt = workbook.add_format({"border": 1, "align": "center", "num_format": "0.0%"})
    total_fmt = workbook.add_format({"bold": True, "border": 1, "bg_color": "#F3F4F6"})

    ws.merge_range("A1:AU1", "SCHOOL-BASED IMMUNIZATION", title)
    ws.merge_range("A2:AU2", "RHU Consolidated Accomplishment Report", subtitle)
    period = start_date.strftime("%b %d, %Y") if start_date == end_date else f"{start_date:%b %d, %Y} to {end_date:%b %d, %Y}"
    ws.write("A3", "Region: CAR", text_fmt)
    ws.write("D3", "Province: Abra", text_fmt)
    ws.write("G3", f"Municipality: {municipality}", text_fmt)
    ws.write("K3", f"Reporting Period: {period}", text_fmt)

    # 47 output columns, grouped to reflect the regional summary form.
    flat_cols = [
        "School", "G1 Enrolled", "G1 MR", "G1 MR %", "G1 Td", "G1 Td %",
        "G1 MR Deferred", "G1 MR Deferred %", "G1 Td Deferred", "G1 Td Deferred %",
        "G1 MR Refused", "G1 MR Refused %", "G1 Td Refused", "G1 Td Refused %",
        "G4 Female Enrolled", "G4 HPV1", "G4 HPV1 %", "G4 HPV2", "G4 HPV2 %",
        "G4 HPV1 Deferred", "G4 HPV1 Deferred %", "G4 HPV2 Deferred", "G4 HPV2 Deferred %",
        "G4 HPV1 Refused", "G4 HPV1 Refused %", "G4 HPV2 Refused", "G4 HPV2 Refused %",
        "G5 Female Enrolled", "G5 HPV2", "G5 HPV2 %", "G5 HPV2 Deferred", "G5 HPV2 Deferred %",
        "G5 HPV2 Refused", "G5 HPV2 Refused %", "G7 Enrolled", "G7 MR", "G7 MR %", "G7 Td", "G7 Td %",
        "G7 MR Deferred", "G7 MR Deferred %", "G7 Td Deferred", "G7 Td Deferred %",
        "G7 MR Refused", "G7 MR Refused %", "G7 Td Refused", "G7 Td Refused %",
    ]
    ws.merge_range(4, 0, 5, 0, "Name of Schools", group)
    ws.merge_range(4, 1, 4, 13, "Grade 1", group)
    ws.merge_range(4, 14, 4, 26, "Grade 4 Female", group)
    ws.merge_range(4, 27, 4, 33, "Grade 5 Female", group)
    ws.merge_range(4, 34, 4, 46, "Grade 7", group)
    for col_idx, col in enumerate(flat_cols[1:], start=1):
        ws.write(5, col_idx, col.replace("G1 ", "").replace("G4 ", "").replace("G5 ", "").replace("G7 ", ""), header)

    row_idx = 6
    for _, row in summary.iterrows():
        for col_idx, col in enumerate(flat_cols):
            value = row.get(col)
            if col.endswith(" %"):
                ws.write(row_idx, col_idx, None if pd.isna(value) else float(value) / 100.0, pct_fmt)
            elif col == "School":
                ws.write(row_idx, col_idx, str(value or ""), text_fmt)
            else:
                ws.write(row_idx, col_idx, None if pd.isna(value) else int(value), int_fmt)
        row_idx += 1

    if not summary.empty:
        ws.write(row_idx, 0, "Total", total_fmt)
        for col_idx, col in enumerate(flat_cols[1:], start=1):
            if col.endswith(" %"):
                # Percent totals are intentionally blank; denominators differ by grade.
                ws.write_blank(row_idx, col_idx, None, total_fmt)
            else:
                value = pd.to_numeric(summary.get(col), errors="coerce").sum()
                ws.write(row_idx, col_idx, int(value), total_fmt)

    ws.set_column(0, 0, 30)
    ws.set_column(1, 46, 12)

    usage_ws = workbook.add_worksheet("Vaccine Utilization")
    usage_ws.write_row(0, 0, ["Vaccine / Grade", "Received (vials)", "Used (vials)", "Unused (vials)"], header)
    for r, values in enumerate(usage.fillna(0).itertuples(index=False, name=None), start=1):
        usage_ws.write_row(r, 0, list(values), int_fmt)
    usage_ws.set_column(0, 0, 22)
    usage_ws.set_column(1, 3, 16)

    reason_ws = workbook.add_worksheet("Reason Summary")
    reason_ws.write_row(0, 0, ["Code", "VaccTrack / Regional Reason", "Learners"], header)
    for r, values in enumerate(reasons.itertuples(index=False, name=None), start=1):
        reason_ws.write_row(r, 0, list(values), text_fmt)
    reason_ws.set_column(0, 0, 8)
    reason_ws.set_column(1, 1, 60)
    reason_ws.set_column(2, 2, 12)

    g5_ws = workbook.add_worksheet("G5 Tracking")
    g5_cols = ["School", "G5 Female Enrolled", "G5 Tracked", "G5 Without HPV2", "G5 HPV2", "G5 HPV2 Deferred", "G5 HPV2 Refused"]
    g5_ws.write_row(0, 0, g5_cols, header)
    if not summary.empty:
        g5_view = summary[[c for c in g5_cols if c in summary.columns]].copy()
        for r, values in enumerate(g5_view.itertuples(index=False, name=None), start=1):
            for c, value in enumerate(values):
                if c == 0:
                    g5_ws.write(r, c, str(value or ""), text_fmt)
                else:
                    g5_ws.write(r, c, None if pd.isna(value) else int(value), int_fmt)
    g5_ws.set_column(0, 0, 30)
    g5_ws.set_column(1, 6, 18)

    workbook.close()
    return output.getvalue()


def _school_roster(targets: pd.DataFrame, municipality: str) -> pd.DataFrame:
    if targets is None or targets.empty or "Municipality" not in targets.columns:
        return pd.DataFrame(columns=["School ID", "School Name", "Barangay"])
    work = targets.loc[targets["Municipality"].map(normalize_municipality_key).eq(normalize_municipality_key(municipality))].copy()
    for col in ["School ID", "School Name", "Barangay"]:
        if col not in work.columns:
            work[col] = ""
    work["School ID"] = work["School ID"].map(_clean_school_id)
    return work[["School ID", "School Name", "Barangay"]].drop_duplicates("School ID").sort_values("School Name")


def render_regional_reporting(
    supabase,
    municipality: str,
    baseline_targets: pd.DataFrame,
    actual_targets: pd.DataFrame,
    username: str,
) -> None:
    st.markdown(
        '<h3><i class="fa-solid fa-file-excel" style="color:#0033A0;margin-right:8px;"></i>Step 3 — Regional Reporting</h3>',
        unsafe_allow_html=True,
    )
    st.markdown(
        "Build the RHU consolidated accomplishment report from learner records. Grade 5 HPV Dose 2 is kept here for regional reporting only and is not included in VaccTrack."
    )

    ready, _ = schema_available(supabase)
    if not ready:
        st.error("Regional reporting is not initialized. Run supabase/006_sbi_regional_reporting.sql once, then reload the app.")
        return

    today = datetime.now(MANILA_TZ).date()
    mode = st.radio("Reporting period", ["Daily", "Custom Date Range"], horizontal=True, key="regional_report_period_mode")
    if mode == "Daily":
        selected = st.date_input("Report date", value=today, key="regional_report_date")
        start_date = end_date = selected
    else:
        selected = st.date_input(
            "Date range",
            value=(today - timedelta(days=6), today),
            key="regional_report_range",
        )
        if isinstance(selected, (tuple, list)) and len(selected) == 2:
            start_date, end_date = selected
        else:
            start_date = end_date = today

    try:
        records_all = _fetch_records(supabase, municipality)
        sessions = _fetch_sessions(supabase, municipality, start_date, end_date)
    except Exception as exc:
        st.error(f"Unable to load regional reporting data: {exc}")
        return
    records = _filter_records(records_all, start_date, end_date)

    summary = build_regional_summary(records, baseline_targets, actual_targets, sessions, municipality)
    reasons = build_reason_summary(records)
    usage = _usage_display(sessions)

    tracked_g5 = int(pd.to_numeric(summary.get("G5 Tracked", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not summary.empty else 0
    deferred_cols = [c for c in summary.columns if "Deferred" in c and not c.endswith(" %")] if not summary.empty else []
    refused_cols = [c for c in summary.columns if "Refused" in c and not c.endswith(" %")] if not summary.empty else []
    total_deferred = int(summary[deferred_cols].apply(pd.to_numeric, errors="coerce").fillna(0).sum().sum()) if deferred_cols else 0
    total_refused = int(summary[refused_cols].apply(pd.to_numeric, errors="coerce").fillna(0).sum().sum()) if refused_cols else 0
    g5_without = int(pd.to_numeric(summary.get("G5 Without HPV2", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not summary.empty else 0
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Schools", f"{summary['School ID'].nunique() if not summary.empty else 0:,}")
    k2.metric("G5 Tracked", f"{tracked_g5:,}")
    k3.metric("G5 Without HPV2", f"{g5_without:,}")
    k4.metric("Deferred", f"{total_deferred:,}")
    k5.metric("Refused", f"{total_refused:,}")

    if summary.empty:
        st.write("No learner records or regional session data are available for this reporting period.")
    else:
        compact_cols = [
            "School", "G1 MR", "G1 Td", "G4 HPV1", "G4 HPV2", "G5 Tracked", "G5 Without HPV2", "G5 HPV2", "G7 MR", "G7 Td",
            "G1 MR Deferred", "G1 Td Deferred", "G4 HPV1 Deferred", "G4 HPV2 Deferred", "G5 HPV2 Deferred",
            "G7 MR Deferred", "G7 Td Deferred",
        ]
        compact_cols = [c for c in compact_cols if c in summary.columns]
        st.markdown("#### Regional Accomplishment Preview")
        st.dataframe(summary[compact_cols], width="stretch", hide_index=True)
        with st.expander("View full regional summary", expanded=False):
            st.dataframe(summary, width="stretch", hide_index=True)

        missing_g5_enrollment = summary.loc[
            pd.to_numeric(summary.get("G5 Tracked", 0), errors="coerce").fillna(0).gt(0)
            & pd.to_numeric(summary.get("G5 Female Enrolled"), errors="coerce").isna()
        ]
        if not missing_g5_enrollment.empty:
            schools = ", ".join(missing_g5_enrollment["School"].astype(str).head(5))
            st.info(f"Grade 5 learner records exist but Grade 5 Female Enrolled is not yet entered for: {schools}. Enter it below if the regional report requires the denominator.")

    st.divider()
    st.markdown("#### Daily Vaccine Utilization / Grade 5 Enrollment")
    if mode != "Daily":
        st.write("Switch to Daily reporting mode to edit vaccine utilization. The table below sums saved daily entries in the selected range.")
        st.dataframe(usage, width="stretch", hide_index=True)
    else:
        roster = _school_roster(baseline_targets, municipality)
        if roster.empty:
            st.write("No schools are available in the SBI roster.")
        else:
            options = roster.to_dict("records")
            labels = [f"{row['School Name']} [{row['School ID']}]" for row in options]
            selected_label = st.selectbox("School", labels, key="regional_session_school")
            school = options[labels.index(selected_label)]
            existing = _session_for(sessions, school["School ID"], start_date)

            g5_default = existing.get("g5_female_enrolled")
            g5_default = int(g5_default) if g5_default is not None and not pd.isna(g5_default) else 0
            g5_enrolled = st.number_input(
                "Grade 5 Female Enrolled (regional denominator)",
                min_value=0,
                step=1,
                value=g5_default,
                help="VaccTrack currently has no Grade 5 field. Enter this only when the regional report needs a Grade 5 denominator.",
                key="regional_g5_enrolled",
            )

            def _existing_int(key: str) -> int:
                value = pd.to_numeric(pd.Series([existing.get(key)]), errors="coerce").fillna(0).iloc[0]
                return int(value)

            editor_rows = []
            for label, prefix in USAGE_ROWS:
                editor_rows.append({
                    "Vaccine / Grade": label,
                    "Received (vials)": _existing_int(f"{prefix}_received"),
                    "Used (vials)": _existing_int(f"{prefix}_used"),
                    "Unused (vials)": _existing_int(f"{prefix}_unused"),
                })
            edited = st.data_editor(
                pd.DataFrame(editor_rows),
                width="stretch",
                hide_index=True,
                disabled=["Vaccine / Grade"],
                column_config={
                    "Received (vials)": st.column_config.NumberColumn(min_value=0, step=1, format="%d"),
                    "Used (vials)": st.column_config.NumberColumn(min_value=0, step=1, format="%d"),
                    "Unused (vials)": st.column_config.NumberColumn(min_value=0, step=1, format="%d"),
                },
                key="regional_usage_editor",
            )
            if st.button("Save Regional Session Details", type="primary", width="stretch", key="regional_save_session"):
                try:
                    _save_session(supabase, municipality, school, start_date, int(g5_enrolled), edited, username)
                    st.toast("Regional session details saved.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Unable to save regional session details: {exc}")

    st.divider()
    st.markdown("#### Deferral / Refusal Reasons")
    st.dataframe(reasons, width="stretch", hide_index=True)

    if not summary.empty:
        period_tag = start_date.isoformat() if start_date == end_date else f"{start_date.isoformat()}_to_{end_date.isoformat()}"
        st.download_button(
            "Download Regional Summary (CSV)",
            data=summary.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"SBI_Regional_RHU_Summary_{municipality.replace(' ', '_')}_{period_tag}.csv",
            mime="text/csv",
            key="regional_summary_csv",
        )
        try:
            excel_bytes = _build_excel(summary, usage, reasons, municipality, start_date, end_date)
            st.download_button(
                "Download Regional RHU Report (Excel)",
                data=excel_bytes,
                file_name=f"SBI_Regional_RHU_Report_{municipality.replace(' ', '_')}_{period_tag}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="regional_summary_xlsx",
            )
        except Exception as exc:
            st.error(str(exc))
