from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

import pandas as pd
import pytz
import streamlit as st

from core.config import ABRA_MUNIS
from core.data import fetch_sbi_targets, fetch_sbi_vacctrack
from core.map_labels import canonical_municipality_name, normalize_municipality_key
from programs.sbi.aggregate_workbook import fetch_submission_history
from programs.sbi.analytics import prepare_hpv_events, prepare_mr_td_events
from programs.sbi.campaign_control import get_campaign_config


MANILA_TZ = pytz.timezone("Asia/Manila")
ACCOMPLISHMENT_TABLE = "sbi_rhu_accomplishments"

METRICS = {
    "G1 MR": {"grade": "G1", "tracker": ("mr_male", "mr_female"), "event": "g1", "event_col": "MR Doses"},
    "G1 Td": {"grade": "G1", "tracker": ("td_male", "td_female"), "event": "g1", "event_col": "Td Doses"},
    "G4 HPV Dose 1": {"grade": "G4", "tracker": ("hpv_dose1",), "event": "g4", "event_col": "HPV Dose 1"},
    "G4 HPV Dose 2": {"grade": "G4", "tracker": ("hpv_dose2",), "event": "g4", "event_col": "HPV Dose 2"},
    "G7 MR": {"grade": "G7", "tracker": ("mr_male", "mr_female"), "event": "g7", "event_col": "MR Doses"},
    "G7 Td": {"grade": "G7", "tracker": ("td_male", "td_female"), "event": "g7", "event_col": "Td Doses"},
}

GRADE_TARGET_COLUMN = {
    "G1": "G1 Total",
    "G4": "G4 Female",
    "G7": "G7 Total",
}

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


def _clean_school_id(value: object) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _same_muni(value: object, municipality: str) -> bool:
    return normalize_municipality_key(value) == normalize_municipality_key(municipality)


def _format_date(value: object) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%b %d, %Y")


def _format_datetime(value: object) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")


def _fetch_all_entries(supabase) -> pd.DataFrame:
    rows: list[dict] = []
    offset = 0
    page_size = 1000
    while True:
        response = (
            supabase.table(ACCOMPLISHMENT_TABLE)
            .select("*")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    if "municipality" in frame.columns:
        frame["municipality"] = frame["municipality"].map(lambda value: canonical_municipality_name(str(value or "").strip()))
    if "school_id" in frame.columns:
        frame["school_id"] = frame["school_id"].map(_clean_school_id)
    if "activity_date" in frame.columns:
        frame["activity_date"] = pd.to_datetime(frame["activity_date"], errors="coerce").dt.date
    return frame


def _prepare_targets() -> pd.DataFrame:
    try:
        targets = fetch_sbi_targets()
    except Exception:
        return pd.DataFrame()
    if targets is None or targets.empty:
        return pd.DataFrame()

    out = targets.copy()
    for column in ["Municipality", "School ID", "School Name", "G1 Total", "G4 Female", "G7 Total"]:
        if column not in out.columns:
            out[column] = 0 if column in {"G1 Total", "G4 Female", "G7 Total"} else ""
    out["Municipality"] = out["Municipality"].map(lambda value: canonical_municipality_name(str(value or "").strip()))
    out["School ID"] = out["School ID"].map(_clean_school_id)
    out["School Name"] = out["School Name"].fillna("").astype(str).str.strip()
    for column in ["G1 Total", "G4 Female", "G7 Total"]:
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0)
    out = out[out["School ID"].ne("")].copy()
    return (
        out.groupby(["Municipality", "School ID"], as_index=False)
        .agg({
            "School Name": "first",
            "G1 Total": "sum",
            "G4 Female": "sum",
            "G7 Total": "sum",
        })
    )


def _reason_total(value: object) -> int:
    if not isinstance(value, dict):
        return 0
    total = 0
    for count in value.values():
        try:
            total += max(0, int(float(count or 0)))
        except (TypeError, ValueError):
            continue
    return total


def _number(row: pd.Series, column: str) -> float:
    return float(pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").fillna(0).iloc[0])


def _issue(
    severity: str,
    municipality: str,
    category: str,
    detail: str,
    *,
    school: str = "",
    activity_date: object = None,
    grade: str = "",
    action: str = "Review the RHU workbook and upload a corrected complete copy.",
) -> dict:
    return {
        "Severity": severity,
        "Municipality": municipality,
        "Category": category,
        "School": school,
        "Activity Date": _format_date(activity_date),
        "Grade": grade,
        "Detail": detail,
        "Suggested Action": action,
    }


def build_data_quality_report(
    entries: pd.DataFrame,
    targets: pd.DataFrame,
    submissions: pd.DataFrame,
    config: dict[str, object],
    *,
    today: date | None = None,
) -> pd.DataFrame:
    today = today or datetime.now(MANILA_TZ).date()
    issues: list[dict] = []
    work = entries.copy() if isinstance(entries, pd.DataFrame) else pd.DataFrame()
    roster = targets.copy() if isinstance(targets, pd.DataFrame) else pd.DataFrame()

    if not work.empty:
        for column in ["municipality", "school_id", "school_name", "activity_date", "grade_level", "source_type", "reason_counts"] + COUNT_COLUMNS:
            if column not in work.columns:
                work[column] = None

        key_cols = ["municipality", "school_id", "activity_date", "grade_level"]
        duplicate_mask = work.duplicated(key_cols, keep=False)
        for _, row in work.loc[duplicate_mask].iterrows():
            issues.append(_issue(
                "Critical",
                str(row.get("municipality") or ""),
                "Duplicate activity row",
                "More than one active record exists for the same Activity Date + School + Grade.",
                school=str(row.get("school_name") or row.get("school_id") or ""),
                activity_date=row.get("activity_date"),
                grade=str(row.get("grade_level") or ""),
            ))

        roster_lookup: dict[str, tuple[str, str]] = {}
        if not roster.empty:
            for _, target in roster.iterrows():
                school_id = _clean_school_id(target.get("School ID"))
                if school_id:
                    roster_lookup[school_id] = (
                        str(target.get("Municipality") or ""),
                        str(target.get("School Name") or ""),
                    )

        start_date = config.get("start_date")
        end_date = config.get("end_date")

        for _, row in work.iterrows():
            municipality = str(row.get("municipality") or "").strip()
            school_id = _clean_school_id(row.get("school_id"))
            school_name = str(row.get("school_name") or school_id).strip()
            grade = str(row.get("grade_level") or "").strip()
            activity_date = row.get("activity_date")

            if not municipality or not school_id or activity_date is None or grade not in {"G1", "G4", "G7"}:
                issues.append(_issue(
                    "Critical",
                    municipality,
                    "Missing or invalid key field",
                    "Municipality, School ID, Activity Date, and Grade must all be valid.",
                    school=school_name,
                    activity_date=activity_date,
                    grade=grade,
                ))

            if school_id:
                assigned = roster_lookup.get(school_id)
                if assigned is None:
                    issues.append(_issue(
                        "Critical",
                        municipality,
                        "School not in target roster",
                        f"School ID {school_id} is not present in the current SBI target roster.",
                        school=school_name,
                        activity_date=activity_date,
                        grade=grade,
                        action="Verify the school selection and the current SBI target roster.",
                    ))
                elif municipality and not _same_muni(assigned[0], municipality):
                    issues.append(_issue(
                        "Critical",
                        municipality,
                        "School assigned to another RHU",
                        f"School ID {school_id} belongs to {assigned[0]} in the current target roster.",
                        school=school_name,
                        activity_date=activity_date,
                        grade=grade,
                        action="Correct the RHU workbook school selection before finalization.",
                    ))

            if isinstance(activity_date, date):
                if start_date and activity_date < start_date:
                    issues.append(_issue(
                        "Critical", municipality, "Activity date outside official period",
                        f"Activity date is before the configured SBI start date ({start_date:%b %d, %Y}).",
                        school=school_name, activity_date=activity_date, grade=grade,
                        action="Correct the activity date or update Campaign Control only if the official schedule changed.",
                    ))
                if end_date and activity_date > end_date:
                    issues.append(_issue(
                        "Critical", municipality, "Activity date outside official period",
                        f"Activity date is after the configured SBI end date ({end_date:%b %d, %Y}).",
                        school=school_name, activity_date=activity_date, grade=grade,
                        action="Correct the activity date or update Campaign Control only if the official schedule changed.",
                    ))

            for column in COUNT_COLUMNS:
                value = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
                if pd.notna(value) and value < 0:
                    issues.append(_issue(
                        "Critical", municipality, "Negative count", f"{column} contains a negative value.",
                        school=school_name, activity_date=activity_date, grade=grade,
                    ))

            if grade == "G4":
                mr_td_total = sum(_number(row, column) for column in [
                    "mr_male", "mr_female", "td_male", "td_female",
                    "mr_deferred", "td_deferred", "mr_refused", "td_refused",
                ])
                if mr_td_total > 0:
                    issues.append(_issue(
                        "Critical", municipality, "Wrong vaccine fields for grade",
                        "A Grade 4 row contains MR/Td counts. Grade 4 should use HPV fields only.",
                        school=school_name, activity_date=activity_date, grade=grade,
                    ))
            elif grade in {"G1", "G7"}:
                hpv_total = sum(_number(row, column) for column in [
                    "hpv_dose1", "hpv_dose2", "hpv1_deferred", "hpv2_deferred", "hpv1_refused", "hpv2_refused",
                ])
                if hpv_total > 0:
                    issues.append(_issue(
                        "Critical", municipality, "Wrong vaccine fields for grade",
                        f"A {grade} row contains HPV counts. Grade 1 and Grade 7 should use MR/Td fields only.",
                        school=school_name, activity_date=activity_date, grade=grade,
                    ))

            missed_total = sum(_number(row, column) for column in [
                "mr_deferred", "td_deferred", "mr_refused", "td_refused",
                "hpv1_deferred", "hpv2_deferred", "hpv1_refused", "hpv2_refused",
            ])
            reasons = _reason_total(row.get("reason_counts"))
            if reasons > missed_total:
                issues.append(_issue(
                    "Critical", municipality, "Reason counts exceed missed outcomes",
                    f"Reason-code total ({reasons}) is greater than deferred/refused total ({int(missed_total)}).",
                    school=school_name, activity_date=activity_date, grade=grade,
                ))
            elif missed_total > 0 and reasons == 0:
                issues.append(_issue(
                    "Warning", municipality, "No reason codes for missed outcomes",
                    f"This row has {int(missed_total)} deferred/refused outcome(s) but no reason-code count.",
                    school=school_name, activity_date=activity_date, grade=grade,
                    action="Review the reason-code columns in the RHU workbook before finalization.",
                ))

            source_type = str(row.get("source_type") or "").strip().lower()
            if source_type and source_type != "workbook":
                issues.append(_issue(
                    "Warning", municipality, "Legacy active record",
                    f"Active accomplishment data still uses legacy source type '{source_type}'.",
                    school=school_name, activity_date=activity_date, grade=grade,
                    action="Confirm this is intentional or clear old test/legacy data before production use.",
                ))

        if not roster.empty:
            target_lookup = roster.set_index(["Municipality", "School ID"]).to_dict("index")
            for (municipality, school_id, grade), group in work.groupby(["municipality", "school_id", "grade_level"], dropna=False):
                target_row = target_lookup.get((municipality, school_id))
                target_column = GRADE_TARGET_COLUMN.get(str(grade))
                if not target_row or not target_column:
                    continue
                target = float(pd.to_numeric(pd.Series([target_row.get(target_column)]), errors="coerce").fillna(0).iloc[0])
                if target <= 0:
                    continue

                checks: list[tuple[str, float]] = []
                if grade in {"G1", "G7"}:
                    checks = [
                        ("MR", sum(pd.to_numeric(group.get(col), errors="coerce").fillna(0).sum() for col in ["mr_male", "mr_female"])),
                        ("Td", sum(pd.to_numeric(group.get(col), errors="coerce").fillna(0).sum() for col in ["td_male", "td_female"])),
                    ]
                elif grade == "G4":
                    checks = [
                        ("HPV Dose 1", pd.to_numeric(group.get("hpv_dose1"), errors="coerce").fillna(0).sum()),
                        ("HPV Dose 2", pd.to_numeric(group.get("hpv_dose2"), errors="coerce").fillna(0).sum()),
                    ]
                for label, value in checks:
                    if float(value) > target:
                        school_name = str(group.iloc[-1].get("school_name") or school_id)
                        issues.append(_issue(
                            "Warning", str(municipality), "Vaccination total exceeds loaded target",
                            f"Cumulative {label} count is {int(value):,} while the loaded {grade} target is {int(target):,}.",
                            school=school_name, grade=str(grade),
                            action="Review the accomplishment rows and confirm the loaded target is current.",
                        ))

    current_submissions = pd.DataFrame()
    if isinstance(submissions, pd.DataFrame) and not submissions.empty:
        current_submissions = submissions.copy()
        if "is_current" in current_submissions.columns:
            current_submissions = current_submissions[current_submissions["is_current"].fillna(False).astype(bool)].copy()

    uploaded_munis = set()
    if not current_submissions.empty and "municipality" in current_submissions.columns:
        uploaded_munis = {
            canonical_municipality_name(str(value or "").strip())
            for value in current_submissions["municipality"].tolist()
            if str(value or "").strip()
        }

    for municipality in ABRA_MUNIS:
        canonical = canonical_municipality_name(municipality)
        if canonical not in uploaded_munis:
            issues.append(_issue(
                "Info", canonical, "No current RHU workbook",
                "No current workbook submission is recorded for this RHU.",
                action="Ask the RHU to test or upload its current complete workbook when reporting begins.",
            ))

    status = str(config.get("status") or "Pre-Implementation")
    if status == "Live" and not current_submissions.empty and "uploaded_at" in current_submissions.columns:
        for _, row in current_submissions.iterrows():
            uploaded_at = pd.to_datetime(row.get("uploaded_at"), errors="coerce")
            if pd.isna(uploaded_at):
                continue
            if uploaded_at.tzinfo is not None:
                uploaded_date = uploaded_at.tz_convert(MANILA_TZ).date()
            else:
                uploaded_date = uploaded_at.date()
            age_days = (today - uploaded_date).days
            if age_days >= 3:
                municipality = canonical_municipality_name(str(row.get("municipality") or ""))
                issues.append(_issue(
                    "Warning", municipality, "Stale RHU workbook during Live activity",
                    f"The current workbook was last uploaded {age_days} day(s) ago ({_format_date(uploaded_at)}).",
                    action="Confirm whether the RHU has new activity or corrections that still need to be uploaded.",
                ))

    issue_frame = pd.DataFrame(issues)
    if issue_frame.empty:
        return pd.DataFrame(columns=["Severity", "Municipality", "Category", "School", "Activity Date", "Grade", "Detail", "Suggested Action"])

    final_munis: set[str] = set()
    if not current_submissions.empty and "is_finalized" in current_submissions.columns:
        finalized = current_submissions[current_submissions["is_finalized"].fillna(False).astype(bool)]
        final_munis = {canonical_municipality_name(str(value or "")) for value in finalized.get("municipality", [])}
    for municipality in sorted(final_munis):
        unresolved = issue_frame[
            issue_frame["Municipality"].map(lambda value: _same_muni(value, municipality))
            & issue_frame["Severity"].isin(["Critical", "Warning"])
        ]
        if not unresolved.empty:
            issues.append(_issue(
                "Critical", municipality, "Finalized RHU has unresolved quality findings",
                f"The RHU is finalized but still has {len(unresolved)} Critical/Warning finding(s).",
                action="Review the findings and reopen the RHU submission if a corrected workbook is required.",
            ))

    result = pd.DataFrame(issues)
    severity_order = pd.Categorical(result["Severity"], categories=["Critical", "Warning", "Info"], ordered=True)
    result = result.assign(_severity_order=severity_order).sort_values(
        ["_severity_order", "Municipality", "Category", "School", "Activity Date"],
        na_position="last",
    ).drop(columns=["_severity_order"])
    return result.reset_index(drop=True)


def _filter_period(frame: pd.DataFrame, column: str, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    if frame is None or frame.empty or column not in frame.columns:
        return frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    out = frame.copy()
    parsed = pd.to_datetime(out[column], errors="coerce").dt.date
    if start_date:
        out = out.loc[parsed >= start_date].copy()
        parsed = pd.to_datetime(out[column], errors="coerce").dt.date
    if end_date:
        out = out.loc[parsed <= end_date].copy()
    return out


def _filter_muni(frame: pd.DataFrame, municipality: str) -> pd.DataFrame:
    if frame is None or frame.empty or "Municipality" not in frame.columns:
        return pd.DataFrame(columns=frame.columns if isinstance(frame, pd.DataFrame) else None)
    keys = frame["Municipality"].map(normalize_municipality_key)
    return frame.loc[keys.eq(normalize_municipality_key(municipality))].copy()


def _entries_for_muni(entries: pd.DataFrame, municipality: str, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    if entries is None or entries.empty:
        return pd.DataFrame(columns=entries.columns if isinstance(entries, pd.DataFrame) else None)
    keys = entries["municipality"].map(normalize_municipality_key)
    out = entries.loc[keys.eq(normalize_municipality_key(municipality))].copy()
    return _filter_period(out, "activity_date", start_date, end_date)


def _events_for_muni(events: dict[str, pd.DataFrame], municipality: str, start_date: date | None, end_date: date | None) -> dict[str, pd.DataFrame]:
    return {
        key: _filter_period(_filter_muni(frame, municipality), "Report Date", start_date, end_date)
        for key, frame in events.items()
    }


def _sum_tracker(entries: pd.DataFrame, metric: str) -> tuple[int, int]:
    config = METRICS[metric]
    if entries is None or entries.empty:
        return 0, 0
    subset = entries[entries["grade_level"].astype(str).eq(config["grade"])].copy()
    if subset.empty:
        return 0, 0
    total = pd.Series(0.0, index=subset.index)
    for column in config["tracker"]:
        values = pd.to_numeric(subset[column], errors="coerce").fillna(0) if column in subset.columns else pd.Series(0.0, index=subset.index)
        total = total.add(values, fill_value=0)
    return int(round(total.sum())), len(subset)


def _sum_vacctrack(events: dict[str, pd.DataFrame], metric: str) -> int:
    config = METRICS[metric]
    frame = events.get(config["event"], pd.DataFrame())
    if frame is None or frame.empty or config["event_col"] not in frame.columns:
        return 0
    return int(round(pd.to_numeric(frame[config["event_col"]], errors="coerce").fillna(0).sum()))


def _metric_cutoff(events: dict[str, pd.DataFrame], metric: str) -> date | None:
    frame = events.get(METRICS[metric]["event"], pd.DataFrame())
    if frame is None or frame.empty or "Report Date" not in frame.columns:
        return None
    dates = pd.to_datetime(frame["Report Date"], errors="coerce").dropna()
    return dates.dt.date.max() if not dates.empty else None


def _metric_rows(entries: pd.DataFrame, events: dict[str, pd.DataFrame], metric: str) -> dict:
    config = METRICS[metric]
    grade_entries = entries[entries["grade_level"].astype(str).eq(config["grade"])].copy() if not entries.empty else pd.DataFrame()
    cutoff = _metric_cutoff(events, metric)
    if grade_entries.empty:
        verified = grade_entries
        pending = grade_entries
    elif cutoff is None:
        verified = grade_entries.iloc[0:0].copy()
        pending = grade_entries
    else:
        dates = pd.to_datetime(grade_entries["activity_date"], errors="coerce").dt.date
        verified = grade_entries.loc[dates <= cutoff].copy()
        pending = grade_entries.loc[dates > cutoff].copy()

    all_total, all_rows = _sum_tracker(grade_entries, metric)
    verified_total, verified_rows = _sum_tracker(verified, metric)
    pending_total, _ = _sum_tracker(pending, metric)
    vacc_total = _sum_vacctrack(events, metric)
    difference = verified_total - vacc_total

    if all_rows == 0 and vacc_total == 0:
        status = "No Activity"
    elif cutoff is None and all_rows > 0:
        status = "Pending VaccTrack Verification"
    elif verified_rows == 0 and vacc_total > 0:
        status = "Check RHU Workbook"
    elif difference != 0:
        status = "Discrepancy"
    elif pending_total > 0:
        status = "Pending VaccTrack Verification"
    else:
        status = "Matched"

    return {
        "Metric": metric,
        "RHU Workbook": all_total,
        "Verified RHU": verified_total,
        "VaccTrack": vacc_total,
        "Difference": difference,
        "Pending RHU": pending_total,
        "VaccTrack Through": cutoff,
        "Status": status,
    }


def _metric_school_rows(entries: pd.DataFrame, events: dict[str, pd.DataFrame], metric: str) -> pd.DataFrame:
    config = METRICS[metric]
    cutoff = _metric_cutoff(events, metric)
    grade_entries = entries[entries["grade_level"].astype(str).eq(config["grade"])].copy() if not entries.empty else pd.DataFrame()

    verified = grade_entries.iloc[0:0].copy()
    pending = grade_entries.copy()
    if cutoff is not None and not grade_entries.empty:
        dates = pd.to_datetime(grade_entries["activity_date"], errors="coerce").dt.date
        verified = grade_entries.loc[dates <= cutoff].copy()
        pending = grade_entries.loc[dates > cutoff].copy()

    def tracker_group(frame: pd.DataFrame, output: str) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=["School ID", "School Name", output])
        work = frame.copy()
        work["School ID"] = work["school_id"].map(_clean_school_id)
        work["School Name"] = work.get("school_name", "").fillna("").astype(str)
        work[output] = 0.0
        for column in config["tracker"]:
            values = pd.to_numeric(work[column], errors="coerce").fillna(0) if column in work.columns else 0
            work[output] = work[output] + values
        return work.groupby("School ID", as_index=False).agg({"School Name": "last", output: "sum"})

    verified_group = tracker_group(verified, "Verified RHU")
    pending_group = tracker_group(pending, "Pending RHU")

    event_frame = events.get(config["event"], pd.DataFrame()).copy()
    if event_frame.empty or "School ID" not in event_frame.columns or config["event_col"] not in event_frame.columns:
        vacc_group = pd.DataFrame(columns=["School ID", "VaccTrack School Name", "VaccTrack"])
    else:
        event_frame["School ID"] = event_frame["School ID"].map(_clean_school_id)
        event_frame["VaccTrack School Name"] = event_frame.get("School Name", "").fillna("").astype(str)
        event_frame["VaccTrack"] = pd.to_numeric(event_frame[config["event_col"]], errors="coerce").fillna(0)
        vacc_group = event_frame.groupby("School ID", as_index=False).agg({"VaccTrack School Name": "last", "VaccTrack": "sum"})

    merged = verified_group.merge(pending_group, on=["School ID", "School Name"], how="outer")
    merged = merged.merge(vacc_group, on="School ID", how="outer")
    if merged.empty:
        return pd.DataFrame(columns=["Metric", "School ID", "School Name", "Verified RHU", "VaccTrack", "Difference", "Pending RHU", "Status"])

    merged["School Name"] = merged.get("School Name").fillna(merged.get("VaccTrack School Name")).fillna("")
    for column in ["Verified RHU", "Pending RHU", "VaccTrack"]:
        merged[column] = pd.to_numeric(merged.get(column), errors="coerce").fillna(0).astype(int)
    merged["Difference"] = merged["Verified RHU"] - merged["VaccTrack"]
    merged["Status"] = "Matched"
    merged.loc[merged["Difference"].ne(0), "Status"] = "Discrepancy"
    if cutoff is None:
        merged.loc[merged["Pending RHU"].gt(0), "Status"] = "Pending VaccTrack Verification"
    else:
        merged.loc[merged["Difference"].eq(0) & merged["Pending RHU"].gt(0), "Status"] = "Pending VaccTrack Verification"
    merged["Metric"] = metric
    return merged[["Metric", "School ID", "School Name", "Verified RHU", "VaccTrack", "Difference", "Pending RHU", "Status"]]


def prepare_reconciliation_data() -> dict[str, pd.DataFrame]:
    raw_g1, raw_g4, raw_g7 = fetch_sbi_vacctrack()
    return {
        "g1": prepare_mr_td_events(raw_g1, "G1"),
        "g4": prepare_hpv_events(raw_g4),
        "g7": prepare_mr_td_events(raw_g7, "G7"),
    }


def build_reconciliation_monitor(
    entries: pd.DataFrame,
    events: dict[str, pd.DataFrame],
    config: dict[str, object],
    municipalities: Iterable[str] = ABRA_MUNIS,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    start_date = config.get("start_date")
    end_date = config.get("end_date")
    overview_rows: list[dict] = []
    metric_details: dict[str, pd.DataFrame] = {}
    school_details: dict[str, pd.DataFrame] = {}

    for municipality in municipalities:
        canonical = canonical_municipality_name(municipality)
        muni_entries = _entries_for_muni(entries, canonical, start_date, end_date)
        muni_events = _events_for_muni(events, canonical, start_date, end_date)
        metrics = pd.DataFrame([_metric_rows(muni_entries, muni_events, metric) for metric in METRICS])
        metric_details[canonical] = metrics

        school_frames = [_metric_school_rows(muni_entries, muni_events, metric) for metric in METRICS]
        school_details[canonical] = pd.concat([frame for frame in school_frames if not frame.empty], ignore_index=True) if any(not frame.empty for frame in school_frames) else pd.DataFrame()

        latest_activity = None
        if not muni_entries.empty and "activity_date" in muni_entries.columns:
            valid_dates = pd.to_datetime(muni_entries["activity_date"], errors="coerce").dropna()
            latest_activity = valid_dates.dt.date.max() if not valid_dates.empty else None

        cutoffs = [value for value in metrics["VaccTrack Through"].tolist() if isinstance(value, date)]
        grade_cutoffs: list[date] = []
        for event_key in ("g1", "g4", "g7"):
            frame = muni_events.get(event_key, pd.DataFrame())
            if frame is None or frame.empty or "Report Date" not in frame.columns:
                continue
            parsed = pd.to_datetime(frame["Report Date"], errors="coerce").dropna()
            if not parsed.empty:
                grade_cutoffs.append(parsed.dt.date.max())
        all_grade_cutoff = min(grade_cutoffs) if len(grade_cutoffs) == 3 else None
        rhu_total = int(metrics["RHU Workbook"].sum())
        vacc_total = int(metrics["VaccTrack"].sum())
        difference = int(metrics["Difference"].sum())
        pending = int(metrics["Pending RHU"].sum())
        has_discrepancy = metrics["Difference"].ne(0).any()
        has_any_cutoff = bool(cutoffs)

        if muni_entries.empty:
            status = "No RHU Upload"
        elif not has_any_cutoff:
            status = "No VaccTrack Data"
        elif has_discrepancy:
            status = "Discrepancy"
        elif pending > 0:
            status = "Pending Verification"
        else:
            status = "Matched"

        overview_rows.append({
            "RHU": canonical,
            "RHU Workbook": rhu_total,
            "VaccTrack": vacc_total,
            "Verified Difference": difference,
            "Pending RHU": pending,
            "Latest Activity": latest_activity,
            "VaccTrack Through": all_grade_cutoff,
            "Status": status,
        })

    overview = pd.DataFrame(overview_rows)
    status_order = pd.Categorical(
        overview["Status"],
        categories=["Discrepancy", "No VaccTrack Data", "Pending Verification", "No RHU Upload", "Matched"],
        ordered=True,
    )
    overview = overview.assign(_status_order=status_order).sort_values(["_status_order", "RHU"]).drop(columns=["_status_order"])
    return overview.reset_index(drop=True), metric_details, school_details


def render_data_quality_center(supabase) -> None:
    st.markdown("### SBI Data Quality Center")
    st.caption("Province-wide checks of the current RHU accomplishment dataset. These checks do not change any records.")

    try:
        entries = _fetch_all_entries(supabase)
        targets = _prepare_targets()
        submissions = fetch_submission_history(supabase)
        config = get_campaign_config(supabase)
        issues = build_data_quality_report(entries, targets, submissions, config)
    except Exception as exc:
        st.error(f"Data Quality Center could not load: {exc}")
        return

    critical = int((issues["Severity"] == "Critical").sum()) if not issues.empty else 0
    warnings = int((issues["Severity"] == "Warning").sum()) if not issues.empty else 0
    info = int((issues["Severity"] == "Info").sum()) if not issues.empty else 0
    no_upload = int((issues["Category"] == "No current RHU workbook").sum()) if not issues.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Critical", critical)
    c2.metric("Warnings", warnings)
    c3.metric("Info", info)
    c4.metric("RHUs Without Upload", no_upload)

    if issues.empty:
        st.success("No data-quality findings were detected in the current RHU dataset.")
        return

    f1, f2, f3 = st.columns([1, 1.4, 1.6])
    with f1:
        severity = st.selectbox("Severity", ["All", "Critical", "Warning", "Info"], key="ops_quality_severity")
    with f2:
        municipalities = sorted({str(value) for value in issues["Municipality"].dropna() if str(value).strip()})
        municipality = st.selectbox("Municipality", ["All"] + municipalities, key="ops_quality_muni")
    with f3:
        categories = sorted({str(value) for value in issues["Category"].dropna() if str(value).strip()})
        category = st.selectbox("Finding", ["All"] + categories, key="ops_quality_category")

    view = issues.copy()
    if severity != "All":
        view = view[view["Severity"].eq(severity)]
    if municipality != "All":
        view = view[view["Municipality"].eq(municipality)]
    if category != "All":
        view = view[view["Category"].eq(category)]

    st.dataframe(view, width="stretch", hide_index=True)
    st.download_button(
        "Download Data Quality Findings (CSV)",
        data=view.to_csv(index=False).encode("utf-8-sig"),
        file_name="SBI_Data_Quality_Findings.csv",
        mime="text/csv",
        width="stretch",
        key="ops_quality_download",
    )

    if st.button("Refresh Data Quality Checks", width="stretch", key="ops_quality_refresh"):
        st.cache_data.clear()
        st.rerun()


def render_reconciliation_monitor(supabase) -> None:
    st.markdown("### Province-wide VaccTrack Reconciliation Monitor")
    st.caption(
        "Compares each RHU's current workbook against the latest available official VaccTrack data. "
        "RHU activity newer than the corresponding VaccTrack grade is kept as Pending Verification instead of being treated as a discrepancy."
    )

    try:
        entries = _fetch_all_entries(supabase)
        events = prepare_reconciliation_data()
        config = get_campaign_config(supabase)
        overview, metric_details, school_details = build_reconciliation_monitor(entries, events, config)
    except Exception as exc:
        st.error(f"VaccTrack reconciliation monitor could not load: {exc}")
        return

    matched = int((overview["Status"] == "Matched").sum())
    discrepancy = int((overview["Status"] == "Discrepancy").sum())
    pending = int((overview["Status"] == "Pending Verification").sum())
    no_upload = int((overview["Status"] == "No RHU Upload").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Matched RHUs", matched)
    c2.metric("With Discrepancy", discrepancy)
    c3.metric("Pending Verification", pending)
    c4.metric("No RHU Upload", no_upload)

    display = overview.copy()
    display["Latest Activity"] = display["Latest Activity"].map(_format_date)
    display["VaccTrack Through"] = display["VaccTrack Through"].map(_format_date)
    st.dataframe(display, width="stretch", hide_index=True)

    st.download_button(
        "Download Province Reconciliation Summary (CSV)",
        data=display.to_csv(index=False).encode("utf-8-sig"),
        file_name="SBI_Province_VaccTrack_Reconciliation.csv",
        mime="text/csv",
        width="stretch",
        key="ops_recon_download",
    )

    st.markdown("#### RHU Detail")
    municipality = st.selectbox("Municipality", overview["RHU"].tolist(), key="ops_recon_muni")
    selected = overview[overview["RHU"].eq(municipality)].iloc[0]
    status = str(selected["Status"])
    if status == "Matched":
        st.success(f"{municipality}: current verified RHU totals match the available VaccTrack data.")
    elif status in {"Pending Verification", "No VaccTrack Data"}:
        st.info(f"{municipality}: newer RHU activity is waiting for a sufficiently recent VaccTrack extract.")
    elif status == "No RHU Upload":
        st.warning(f"{municipality}: no RHU workbook data is currently available for reconciliation.")
    else:
        st.warning(f"{municipality}: one or more verified RHU totals differ from VaccTrack.")

    metrics = metric_details.get(municipality, pd.DataFrame()).copy()
    if not metrics.empty:
        metrics["VaccTrack Through"] = metrics["VaccTrack Through"].map(_format_date)
        st.markdown("##### By Vaccine / Grade")
        st.dataframe(metrics, width="stretch", hide_index=True)

    schools = school_details.get(municipality, pd.DataFrame()).copy()
    if not schools.empty:
        needs_review = schools[schools["Status"].ne("Matched")].copy()
        st.markdown("##### School-Level Items to Review")
        if needs_review.empty:
            st.write("No school-level discrepancy or pending item is currently visible.")
        else:
            st.dataframe(
                needs_review.sort_values(["Status", "Metric", "School Name"]),
                width="stretch",
                hide_index=True,
            )
            st.download_button(
                f"Download {municipality} Reconciliation Detail (CSV)",
                data=needs_review.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"SBI_Reconciliation_{municipality.replace(' ', '_')}.csv",
                mime="text/csv",
                width="stretch",
                key="ops_recon_detail_download",
            )

    if st.button("Refresh Reconciliation Monitor", width="stretch", key="ops_recon_refresh"):
        st.cache_data.clear()
        st.rerun()
