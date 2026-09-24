"""SBI learner line-list upload, revisions, and VaccTrack encoding.

The learner-level records are the RHU operational source used to calculate what should be
encoded into VaccTrack. VaccTrack remains the official/final SBI dataset. The v5.18 RHU
workflow is intentionally limited to Grade 1, Grade 4, and Grade 7 to keep encoding simple.
"""

from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Iterable
import json
import re

import numpy as np
import pandas as pd
import pytz
import streamlit as st

from core.map_labels import canonical_municipality_name, normalize_municipality_key


MANILA_TZ = pytz.timezone("Asia/Manila")
IMPORT_TABLE = "sbi_linelist_imports"
RECORD_TABLE = "sbi_linelist_records"
AUDIT_TABLE = "sbi_linelist_audit"
AGG_TABLE = "sbi_rhu_accomplishments"

REGIONAL_REQUIRED_COLUMNS = [
    "Activity Date",
    "School ID",
    "School Name",
    "Learner ID / LRN",
    "Last Name",
    "First Name",
    "Middle Name",
    "Sex",
    "Grade Level",
    "MR Status",
    "Td Status",
    "HPV Dose",
    "HPV Status",
    "Reason Code",
    "Remarks",
]

REGIONAL_OPTIONAL_COLUMNS = [
    "Section",
    "MR Lot/Batch No.",
    "Td Lot/Batch No.",
    "HPV Lot/Batch No.",
    "Reason Details",
]

# Keep the v5.14/v5.15 vaccinated-only template readable during the transition.
LEGACY_REQUIRED_COLUMNS = [
    "Activity Date",
    "School ID",
    "School Name",
    "Learner ID / LRN",
    "Last Name",
    "First Name",
    "Middle Name",
    "Sex",
    "Grade Level",
    "MR Given",
    "Td Given",
    "HPV Dose",
    "Remarks",
]

GRADE_MAP = {
    "1": "G1",
    "g1": "G1",
    "grade1": "G1",
    "grade 1": "G1",
    "4": "G4",
    "g4": "G4",
    "grade4": "G4",
    "grade 4": "G4",
    "7": "G7",
    "g7": "G7",
    "grade7": "G7",
    "grade 7": "G7",
}

STATUS_VALUES = {"Given", "Deferred", "Refused", "Not Given"}

REASON_LABELS = {
    "01": 'Parent/caregiver not home or decision-maker (e.g., spouse) unavailable',
    "02": 'Fear of vaccine side effects',
    "03": 'Concerns over vaccine safety (e.g., past adverse reaction, Dengvaxia)',
    "04": 'Refused extra dose (already completed routine vaccines) [campaign-specific]',
    "05": 'No time due to work or caregiving; no one to accompany child',
    "06": 'Belief that vaccine is not effective, low quality, or expired',
    "07": 'Belief that child is too young for vaccination',
    "08": 'Already vaccinated or advised against it by private doctor',
    "09": 'Religious or personal beliefs not aligned with vaccination',
    "10": 'Lack of trust in the vaccinator',
    "11": 'Child was sick, just recovered, or recently discharged from hospital',
    "12": 'Unaware of vaccination schedule/activity',
    "13": 'No health worker visit or vaccine promotion in the area',
    "14": 'Child is visiting, recently moved, or not in target client list',
    "15": 'Too far from site or no transportation (geographical challenges)',
    "16": 'Language or communication barrier',
    "17": 'Caregiver has disability or health condition limiting access',
    "18": 'Fear of injection',
    "19": 'Refused with no reason or Other',
}


def schema_available(supabase) -> tuple[bool, str]:
    try:
        supabase.table(RECORD_TABLE).select("id,mr_status,reason_code,section").limit(1).execute()
        supabase.table(IMPORT_TABLE).select("id").limit(1).execute()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def _clean_school_id(value: object) -> str:
    text = _clean_text(value)
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _norm_name(value: object) -> str:
    return _clean_text(value).upper()


def _norm_grade(value: object) -> str:
    key = _clean_text(value).lower()
    return GRADE_MAP.get(key, "")


def _norm_sex(value: object) -> str:
    key = _clean_text(value).lower()
    if key in {"m", "male"}:
        return "Male"
    if key in {"f", "female"}:
        return "Female"
    return ""


def _norm_yes_no(value: object) -> bool | None:
    if value is None or pd.isna(value) or _clean_text(value) == "":
        return None
    key = _clean_text(value).lower()
    if key in {"yes", "y", "true", "1", "given"}:
        return True
    if key in {"no", "n", "false", "0", "not given"}:
        return False
    return None


def _norm_status(value: object) -> str:
    key = _clean_text(value).lower().replace("_", " ").replace("-", " ")
    key = re.sub(r"\s+", " ", key).strip()
    mapping = {
        "given": "Given",
        "vaccinated": "Given",
        "yes": "Given",
        "deferred": "Deferred",
        "defer": "Deferred",
        "refused": "Refused",
        "refusal": "Refused",
        "not given": "Not Given",
        "notgiven": "Not Given",
        "no": "Not Given",
    }
    return mapping.get(key, "")


def _norm_reason_code(value: object) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    match = re.match(r"^(\d{1,2})", text)
    if not match:
        return ""
    code = match.group(1).zfill(2)
    return code if code in REASON_LABELS else ""


def _norm_hpv_dose(value: object) -> int | None:
    if value is None or pd.isna(value) or _clean_text(value) == "":
        return None
    try:
        number = int(float(value))
    except Exception:
        return None
    return number if number in {1, 2} else None


def _parse_date(value: object) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _canonical_muni(value: object) -> str:
    return canonical_municipality_name(_clean_text(value))


def _row_key(record: dict) -> str:
    learner_identity = _clean_text(record.get("learner_id"))
    if not learner_identity:
        learner_identity = "|".join(
            [
                _norm_name(record.get("last_name")),
                _norm_name(record.get("first_name")),
                _norm_name(record.get("middle_name")),
                _clean_text(record.get("sex")),
            ]
        )
    raw = "|".join(
        [
            normalize_municipality_key(record.get("municipality")),
            _clean_school_id(record.get("school_id")),
            str(record.get("activity_date")),
            learner_identity.upper(),
            _clean_text(record.get("grade_level")),
        ]
    )
    return sha256(raw.encode("utf-8")).hexdigest()


def _record_hash(record: dict) -> str:
    significant = {
        key: record.get(key)
        for key in [
            "municipality",
            "school_id",
            "school_name",
            "barangay",
            "activity_date",
            "learner_id",
            "last_name",
            "first_name",
            "middle_name",
            "sex",
            "grade_level",
            "section",
            "mr_given",
            "mr_status",
            "mr_lot_batch",
            "td_given",
            "td_status",
            "td_lot_batch",
            "hpv_dose",
            "hpv_status",
            "hpv_lot_batch",
            "reason_code",
            "reason_details",
            "remarks",
        ]
    }
    text = json.dumps(significant, sort_keys=True, default=str, ensure_ascii=False)
    return sha256(text.encode("utf-8")).hexdigest()


def _school_roster(targets: pd.DataFrame, municipality: str) -> pd.DataFrame:
    if targets is None or targets.empty:
        return pd.DataFrame(columns=["School ID", "School Name", "Barangay"])
    work = targets.copy()
    if "Municipality" not in work.columns:
        return pd.DataFrame(columns=["School ID", "School Name", "Barangay"])
    target_key = normalize_municipality_key(municipality)
    work = work.loc[work["Municipality"].map(normalize_municipality_key).eq(target_key)].copy()
    for col in ["School ID", "School Name", "Barangay"]:
        if col not in work.columns:
            work[col] = ""
    work["School ID"] = work["School ID"].map(_clean_school_id)
    work["School Name"] = work["School Name"].map(_clean_text)
    work["Barangay"] = work["Barangay"].map(_clean_text)
    work = work[work["School ID"].ne("")]
    return (
        work.groupby("School ID", as_index=False)
        .agg({"School Name": "first", "Barangay": "first"})
        .sort_values("School Name")
        .reset_index(drop=True)
    )


def _read_upload(uploaded_file) -> tuple[pd.DataFrame | None, str]:
    raw = uploaded_file.getvalue()
    suffix = Path(uploaded_file.name).suffix.lower()
    try:
        if suffix == ".csv":
            return pd.read_csv(BytesIO(raw)), ""
        if suffix in {".xlsx", ".xlsm", ".xls"}:
            try:
                return pd.read_excel(BytesIO(raw), sheet_name="Line List", engine="calamine"), ""
            except ImportError:
                return None, "Excel upload support is not installed. Add python-calamine>=0.3.1 to requirements.txt, or upload a CSV copy for now."
            except ValueError:
                return pd.read_excel(BytesIO(raw), sheet_name=0, engine="calamine"), ""
        return None, "Use the provided .xlsx template or upload an .xlsx, .xls, .xlsm, or .csv learner file."
    except Exception as exc:
        return None, f"Unable to read the learner file: {exc}"


def _validate_upload(
    raw_df: pd.DataFrame,
    municipality: str,
    targets: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Normalize the regional v5.17 template or the older vaccinated-only template."""
    if raw_df is None:
        return pd.DataFrame(), pd.DataFrame(), []

    source = raw_df.copy()
    source.columns = [_clean_text(c) for c in source.columns]
    regional = all(col in source.columns for col in REGIONAL_REQUIRED_COLUMNS)
    legacy = all(col in source.columns for col in LEGACY_REQUIRED_COLUMNS)
    if not regional and not legacy:
        missing = [col for col in REGIONAL_REQUIRED_COLUMNS if col not in source.columns]
        issues = pd.DataFrame(
            [{"Row": "Header", "Learner": "", "Problem": f"Missing required column: {col}"} for col in missing]
        )
        return pd.DataFrame(), issues, []

    if regional:
        for col in REGIONAL_OPTIONAL_COLUMNS:
            if col not in source.columns:
                source[col] = None
        selected_columns = REGIONAL_REQUIRED_COLUMNS + REGIONAL_OPTIONAL_COLUMNS
    else:
        selected_columns = LEGACY_REQUIRED_COLUMNS

    source = source[selected_columns].copy().dropna(how="all")
    blank_mask = source.apply(lambda r: all(_clean_text(v) == "" for v in r), axis=1)
    source = source.loc[~blank_mask].copy()

    roster = _school_roster(targets, municipality)
    roster_map = roster.set_index("School ID").to_dict("index") if not roster.empty else {}

    valid_records: list[dict] = []
    issues: list[dict] = []
    warnings: list[str] = []

    for i, row in source.iterrows():
        excel_row = int(i) + 2
        school_id = _clean_school_id(row.get("School ID"))
        activity_date = _parse_date(row.get("Activity Date"))
        learner_id = _clean_text(row.get("Learner ID / LRN"))
        last_name = _norm_name(row.get("Last Name"))
        first_name = _norm_name(row.get("First Name"))
        middle_name = _norm_name(row.get("Middle Name"))
        sex = _norm_sex(row.get("Sex"))
        grade = _norm_grade(row.get("Grade Level"))
        section = _clean_text(row.get("Section")) if regional else ""
        remarks = _clean_text(row.get("Remarks"))
        learner_label = " ".join(x for x in [first_name, middle_name, last_name] if x).strip() or learner_id or f"Row {excel_row}"

        row_problems: list[str] = []
        if activity_date is None:
            row_problems.append("Activity Date is invalid or blank")
        if not school_id:
            row_problems.append("School ID is blank")
        elif school_id not in roster_map:
            row_problems.append(f"School ID {school_id} is not in the assigned municipality roster")
        if not last_name:
            row_problems.append("Last Name is required")
        if not first_name:
            row_problems.append("First Name is required")
        if not sex:
            row_problems.append("Sex must be Male or Female")
        if not grade:
            row_problems.append("Grade Level must be Grade 1, Grade 4, or Grade 7")

        mr_status = td_status = hpv_status = ""
        mr_lot = td_lot = hpv_lot = ""
        reason_code = reason_details = ""
        mr_given: bool | None = None
        td_given: bool | None = None
        hpv_dose = _norm_hpv_dose(row.get("HPV Dose"))

        if regional:
            raw_mr_status = _clean_text(row.get("MR Status"))
            raw_td_status = _clean_text(row.get("Td Status"))
            raw_hpv_status = _clean_text(row.get("HPV Status"))
            mr_status = _norm_status(raw_mr_status)
            td_status = _norm_status(raw_td_status)
            hpv_status = _norm_status(raw_hpv_status)
            mr_lot = _clean_text(row.get("MR Lot/Batch No."))
            td_lot = _clean_text(row.get("Td Lot/Batch No."))
            hpv_lot = _clean_text(row.get("HPV Lot/Batch No."))
            raw_reason = _clean_text(row.get("Reason Code"))
            reason_code = _norm_reason_code(raw_reason)
            reason_details = _clean_text(row.get("Reason Details"))

            if raw_mr_status and not mr_status:
                row_problems.append("MR Status must be Given, Deferred, Refused, or Not Given")
            if raw_td_status and not td_status:
                row_problems.append("Td Status must be Given, Deferred, Refused, or Not Given")
            if raw_hpv_status and not hpv_status:
                row_problems.append("HPV Status must be Given, Deferred, Refused, or Not Given")
            if raw_reason and not reason_code:
                row_problems.append("Reason Code must be one of 01 to 19")

            mr_given = True if mr_status == "Given" else (False if mr_status else None)
            td_given = True if td_status == "Given" else (False if td_status else None)
        else:
            legacy_mr = _norm_yes_no(row.get("MR Given"))
            legacy_td = _norm_yes_no(row.get("Td Given"))
            if grade in {"G1", "G7"}:
                if _clean_text(row.get("MR Given")) and legacy_mr is None:
                    row_problems.append("MR Given must be Yes or No")
                if _clean_text(row.get("Td Given")) and legacy_td is None:
                    row_problems.append("Td Given must be Yes or No")
                if legacy_mr is None or legacy_td is None:
                    row_problems.append("MR Given and Td Given must both be completed for Grade 1/7")
                elif not legacy_mr and not legacy_td:
                    row_problems.append("At least one vaccine must be marked Yes in the legacy vaccinated-only template")
                mr_given = legacy_mr
                td_given = legacy_td
                mr_status = "Given" if legacy_mr else "Not Given"
                td_status = "Given" if legacy_td else "Not Given"
            elif grade == "G4":
                hpv_status = "Given"

        if grade in {"G1", "G7"}:
            if regional and (not mr_status or not td_status):
                row_problems.append("MR Status and Td Status must both be completed for Grade 1/7")
            if _clean_text(row.get("HPV Dose")) or (regional and _clean_text(row.get("HPV Status"))):
                row_problems.append("HPV fields must be blank for Grade 1/7")
                hpv_dose = None
                hpv_status = ""
                hpv_lot = ""
            if regional and mr_status == "Given" and not mr_lot:
                warnings.append(f"Row {excel_row}: MR was marked Given but MR Lot/Batch No. is blank.")
            if regional and td_status == "Given" and not td_lot:
                warnings.append(f"Row {excel_row}: Td was marked Given but Td Lot/Batch No. is blank.")
        elif grade == "G4":
            if sex and sex != "Female":
                row_problems.append("Grade 4 HPV learner records must be Female")
            if hpv_dose not in {1, 2}:
                row_problems.append("HPV Dose must be 1 or 2 for Grade 4")
            if regional and not hpv_status:
                row_problems.append("HPV Status is required for Grade 4")
            if regional and (_clean_text(row.get("MR Status")) or _clean_text(row.get("Td Status"))):
                row_problems.append("MR Status and Td Status must be blank for Grade 4")
            mr_given = None
            td_given = None
            mr_status = td_status = ""
            mr_lot = td_lot = ""
            if hpv_status == "Given" and not hpv_lot:
                warnings.append(f"Row {excel_row}: HPV was marked Given but HPV Lot/Batch No. is blank.")

        needs_reason = any(status in {"Deferred", "Refused"} for status in [mr_status, td_status, hpv_status])
        if needs_reason and not reason_code:
            row_problems.append("Reason Code is required when a vaccine is Deferred or Refused")

        if row_problems:
            for problem in dict.fromkeys(row_problems):
                issues.append({"Row": excel_row, "Learner": learner_label, "Problem": problem})
            continue

        roster_row = roster_map[school_id]
        uploaded_school_name = _clean_text(row.get("School Name"))
        canonical_school_name = _clean_text(roster_row.get("School Name")) or uploaded_school_name
        if uploaded_school_name and canonical_school_name and uploaded_school_name.casefold() != canonical_school_name.casefold():
            warnings.append(
                f"Row {excel_row}: School Name '{uploaded_school_name}' was normalized to '{canonical_school_name}' using School ID {school_id}."
            )

        record = {
            "municipality": _canonical_muni(municipality),
            "school_id": school_id,
            "school_name": canonical_school_name,
            "barangay": _clean_text(roster_row.get("Barangay")),
            "activity_date": activity_date.isoformat(),
            "learner_id": learner_id or None,
            "last_name": last_name,
            "first_name": first_name,
            "middle_name": middle_name or None,
            "sex": sex,
            "grade_level": grade,
            "section": section or None,
            "mr_given": mr_given,
            "mr_status": mr_status or None,
            "mr_lot_batch": mr_lot or None,
            "td_given": td_given,
            "td_status": td_status or None,
            "td_lot_batch": td_lot or None,
            "hpv_dose": hpv_dose,
            "hpv_status": hpv_status or None,
            "hpv_lot_batch": hpv_lot or None,
            "reason_code": reason_code or None,
            "reason_details": reason_details or None,
            "remarks": remarks or None,
        }
        record["row_key"] = _row_key(record)
        record["record_hash"] = _record_hash(record)
        record["_excel_row"] = excel_row
        record["_learner_label"] = learner_label
        valid_records.append(record)

    valid = pd.DataFrame(valid_records)
    issue_df = pd.DataFrame(issues, columns=["Row", "Learner", "Problem"])
    if not valid.empty:
        duplicates = valid[valid.duplicated("row_key", keep=False)]
        if not duplicates.empty:
            duplicate_keys = set(duplicates["row_key"])
            duplicate_issues = [
                {"Row": rec["_excel_row"], "Learner": rec["_learner_label"], "Problem": "Duplicate learner/date/school/grade row in the uploaded file"}
                for _, rec in duplicates.iterrows()
            ]
            issue_df = pd.concat([issue_df, pd.DataFrame(duplicate_issues)], ignore_index=True)
            valid = valid.loc[~valid["row_key"].isin(duplicate_keys)].copy()

    return valid.reset_index(drop=True), issue_df.reset_index(drop=True), warnings


def _fetch_records(supabase, municipality: str, active_only: bool = False) -> pd.DataFrame:
    rows: list[dict] = []
    offset = 0
    limit = 1000
    while True:
        query = supabase.table(RECORD_TABLE).select("*").eq("municipality", _canonical_muni(municipality))
        if active_only:
            query = query.eq("is_active", True)
        response = query.range(offset, offset + limit - 1).execute()
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return pd.DataFrame(rows)


def _fetch_imports(supabase, municipality: str) -> pd.DataFrame:
    response = (
        supabase.table(IMPORT_TABLE)
        .select("*")
        .eq("municipality", _canonical_muni(municipality))
        .order("uploaded_at", desc=True)
        .limit(250)
        .execute()
    )
    return pd.DataFrame(response.data or [])


def _scope_tuples(df: pd.DataFrame) -> set[tuple[str, str, str]]:
    if df is None or df.empty:
        return set()
    return set(zip(df["activity_date"].astype(str), df["school_id"].astype(str), df["grade_level"].astype(str)))


def _compare_revision(valid: pd.DataFrame, existing_active: pd.DataFrame) -> dict:
    scopes = _scope_tuples(valid)
    if existing_active.empty or not scopes:
        existing_scope = pd.DataFrame(columns=existing_active.columns if isinstance(existing_active, pd.DataFrame) else None)
    else:
        mask = existing_active.apply(
            lambda r: (str(r.get("activity_date")), str(r.get("school_id")), str(r.get("grade_level"))) in scopes,
            axis=1,
        )
        existing_scope = existing_active.loc[mask].copy()

    upload_map = {str(r["row_key"]): r for _, r in valid.iterrows()}
    existing_map = {str(r["row_key"]): r for _, r in existing_scope.iterrows()} if not existing_scope.empty else {}

    added = sorted(set(upload_map) - set(existing_map))
    removed = sorted(set(existing_map) - set(upload_map))
    common = sorted(set(upload_map) & set(existing_map))
    modified = [key for key in common if str(upload_map[key].get("record_hash")) != str(existing_map[key].get("record_hash"))]
    unchanged = [key for key in common if key not in set(modified)]

    def _preview(keys: Iterable[str], source: dict, change: str) -> pd.DataFrame:
        rows = []
        for key in list(keys)[:100]:
            rec = source[key]
            rows.append(
                {
                    "Change": change,
                    "Activity Date": str(rec.get("activity_date", "")),
                    "School": str(rec.get("school_name", "")),
                    "Grade": str(rec.get("grade_level", "")),
                    "Learner": " ".join(
                        x for x in [str(rec.get("first_name") or ""), str(rec.get("middle_name") or ""), str(rec.get("last_name") or "")] if x
                    ).strip(),
                    "Learner ID / LRN": str(rec.get("learner_id") or ""),
                }
            )
        return pd.DataFrame(rows)

    preview_parts = [
        _preview(added, upload_map, "ADD"),
        _preview(modified, upload_map, "MODIFY"),
        _preview(removed, existing_map, "REMOVE"),
    ]
    preview = pd.concat([p for p in preview_parts if not p.empty], ignore_index=True) if any(not p.empty for p in preview_parts) else pd.DataFrame()
    return {
        "scopes": scopes,
        "existing_scope": existing_scope,
        "upload_map": upload_map,
        "existing_map": existing_map,
        "added": added,
        "modified": modified,
        "removed": removed,
        "unchanged": unchanged,
        "preview": preview,
    }


def _jsonable_record(record: dict | pd.Series | None) -> dict | None:
    if record is None:
        return None
    src = dict(record)
    keep = [
        "municipality", "school_id", "school_name", "barangay", "activity_date", "learner_id",
        "last_name", "first_name", "middle_name", "sex", "grade_level", "section",
        "mr_given", "mr_status", "mr_lot_batch", "td_given", "td_status", "td_lot_batch",
        "hpv_dose", "hpv_status", "hpv_lot_batch", "reason_code", "reason_details",
        "remarks", "record_hash", "is_active"
    ]
    out = {}
    for key in keep:
        value = src.get(key)
        if pd.isna(value) if not isinstance(value, (list, dict)) else False:
            value = None
        out[key] = value
    return out


def _audit_rows(diff: dict, municipality: str, batch_id: int, username: str) -> list[dict]:
    now = datetime.now(MANILA_TZ).isoformat()
    rows: list[dict] = []
    for key in diff["added"]:
        new = dict(diff["upload_map"][key])
        rows.append({
            "row_key": key,
            "municipality": municipality,
            "action": "ADD",
            "old_values": None,
            "new_values": _jsonable_record(new),
            "import_batch_id": batch_id,
            "changed_by": username,
            "changed_at": now,
        })
    for key in diff["modified"]:
        old = dict(diff["existing_map"][key])
        new = dict(diff["upload_map"][key])
        action = "RESTORE" if not bool(old.get("is_active", True)) else "MODIFY"
        rows.append({
            "row_key": key,
            "municipality": municipality,
            "action": action,
            "old_values": _jsonable_record(old),
            "new_values": _jsonable_record(new),
            "import_batch_id": batch_id,
            "changed_by": username,
            "changed_at": now,
        })
    for key in diff["removed"]:
        old = dict(diff["existing_map"][key])
        rows.append({
            "row_key": key,
            "municipality": municipality,
            "action": "REMOVE",
            "old_values": _jsonable_record(old),
            "new_values": None,
            "import_batch_id": batch_id,
            "changed_by": username,
            "changed_at": now,
        })
    return rows


def _aggregate_scope(active_scope: pd.DataFrame, grade: str) -> dict:
    empty = {
        "mr_male": None, "mr_female": None, "td_male": None, "td_female": None,
        "hpv_dose1": None, "hpv_dose2": None,
        "mr_deferred": None, "mr_refused": None, "td_deferred": None, "td_refused": None,
        "hpv_dose1_deferred": None, "hpv_dose1_refused": None,
        "hpv_dose2_deferred": None, "hpv_dose2_refused": None,
    }
    if active_scope.empty:
        return empty

    def _status_series(name: str, given_col: str | None = None, hpv: bool = False) -> pd.Series:
        if name in active_scope.columns:
            series = active_scope[name].astype("string").fillna("").str.strip()
            if series.ne("").any():
                return series
        if hpv:
            dose = pd.to_numeric(active_scope.get("hpv_dose"), errors="coerce")
            return pd.Series(np.where(dose.notna(), "Given", ""), index=active_scope.index, dtype="string")
        if given_col and given_col in active_scope.columns:
            values = active_scope[given_col]
            return values.map(lambda v: "Given" if v is True else ("Not Given" if v is False else "")).astype("string")
        return pd.Series("", index=active_scope.index, dtype="string")

    if grade in {"G1", "G7"}:
        male = active_scope["sex"].astype(str).eq("Male")
        female = active_scope["sex"].astype(str).eq("Female")
        mr_status = _status_series("mr_status", "mr_given")
        td_status = _status_series("td_status", "td_given")
        return {
            "mr_male": int((male & mr_status.eq("Given")).sum()),
            "mr_female": int((female & mr_status.eq("Given")).sum()),
            "td_male": int((male & td_status.eq("Given")).sum()),
            "td_female": int((female & td_status.eq("Given")).sum()),
            "hpv_dose1": None,
            "hpv_dose2": None,
            "mr_deferred": int(mr_status.eq("Deferred").sum()),
            "mr_refused": int(mr_status.eq("Refused").sum()),
            "td_deferred": int(td_status.eq("Deferred").sum()),
            "td_refused": int(td_status.eq("Refused").sum()),
            "hpv_dose1_deferred": None, "hpv_dose1_refused": None,
            "hpv_dose2_deferred": None, "hpv_dose2_refused": None,
        }

    hpv = pd.to_numeric(active_scope.get("hpv_dose"), errors="coerce")
    hpv_status = _status_series("hpv_status", hpv=True)
    return {
        "mr_male": None, "mr_female": None, "td_male": None, "td_female": None,
        "hpv_dose1": int((hpv.eq(1) & hpv_status.eq("Given")).sum()),
        "hpv_dose2": int((hpv.eq(2) & hpv_status.eq("Given")).sum()),
        "mr_deferred": None, "mr_refused": None, "td_deferred": None, "td_refused": None,
        "hpv_dose1_deferred": int((hpv.eq(1) & hpv_status.eq("Deferred")).sum()),
        "hpv_dose1_refused": int((hpv.eq(1) & hpv_status.eq("Refused")).sum()),
        "hpv_dose2_deferred": int((hpv.eq(2) & hpv_status.eq("Deferred")).sum()),
        "hpv_dose2_refused": int((hpv.eq(2) & hpv_status.eq("Refused")).sum()),
    }


def _rebuild_aggregates(supabase, municipality: str, scopes: set[tuple[str, str, str]], batch_id: int | None, username: str) -> None:
    municipality = _canonical_muni(municipality)
    now = datetime.now(MANILA_TZ).isoformat()
    for activity_date, school_id, grade in sorted(scopes):
        response = (
            supabase.table(RECORD_TABLE)
            .select("*")
            .eq("municipality", municipality)
            .eq("activity_date", activity_date)
            .eq("school_id", school_id)
            .eq("grade_level", grade)
            .eq("is_active", True)
            .execute()
        )
        active = pd.DataFrame(response.data or [])
        if active.empty:
            # Never delete a manual fallback row; only remove a line-list-derived aggregate.
            (
                supabase.table(AGG_TABLE)
                .delete()
                .eq("municipality", municipality)
                .eq("activity_date", activity_date)
                .eq("school_id", school_id)
                .eq("grade_level", grade)
                .eq("source_type", "linelist")
                .execute()
            )
            continue

        first = active.iloc[0]
        totals = _aggregate_scope(active, grade)
        source_batch_id = batch_id
        if source_batch_id is None and "import_batch_id" in active.columns:
            batch_values = pd.to_numeric(active["import_batch_id"], errors="coerce").dropna()
            source_batch_id = int(batch_values.max()) if not batch_values.empty else None

        record = {
            "municipality": municipality,
            "school_id": school_id,
            "school_name": _clean_text(first.get("school_name")),
            "barangay": _clean_text(first.get("barangay")),
            "activity_date": activity_date,
            "grade_level": grade,
            **totals,
            "updated_by": username,
            "updated_at": now,
            "source_type": "linelist",
            "source_batch_id": source_batch_id,
        }
        supabase.table(AGG_TABLE).upsert(
            record,
            on_conflict="municipality,school_id,activity_date,grade_level",
        ).execute()


def rebuild_aggregates_after_admin_change(
    supabase,
    municipality: str,
    scopes: set[tuple[str, str, str]],
    username: str,
) -> None:
    """Recalculate line-list-derived RHU totals after an admin rollback/delete."""
    _rebuild_aggregates(supabase, municipality, scopes, None, username)


def _apply_import(
    supabase,
    valid: pd.DataFrame,
    diff: dict,
    municipality: str,
    username: str,
    filename: str,
    raw_bytes: bytes,
) -> int:
    municipality = _canonical_muni(municipality)
    now = datetime.now(MANILA_TZ).isoformat()
    min_date = min(valid["activity_date"]) if not valid.empty else None
    max_date = max(valid["activity_date"]) if not valid.empty else None
    meta = {
        "municipality": municipality,
        "file_name": filename,
        "file_sha256": sha256(raw_bytes).hexdigest(),
        "uploaded_by": username,
        "uploaded_at": now,
        "activity_date_min": min_date,
        "activity_date_max": max_date,
        "rows_uploaded": int(len(valid)),
        "rows_added": int(len(diff["added"])),
        "rows_modified": int(len(diff["modified"])),
        "rows_removed": int(len(diff["removed"])),
        "rows_unchanged": int(len(diff["unchanged"])),
    }
    batch_response = supabase.table(IMPORT_TABLE).insert(meta).execute()
    if not batch_response.data:
        raise RuntimeError("Supabase did not return an import batch ID.")
    batch_id = int(batch_response.data[0]["id"])

    upsert_rows: list[dict] = []
    for _, row in valid.iterrows():
        rec = {k: row.get(k) for k in [
            "row_key", "municipality", "school_id", "school_name", "barangay", "activity_date",
            "learner_id", "last_name", "first_name", "middle_name", "sex", "grade_level", "section",
            "mr_given", "mr_status", "mr_lot_batch", "td_given", "td_status", "td_lot_batch",
            "hpv_dose", "hpv_status", "hpv_lot_batch", "reason_code", "reason_details",
            "remarks", "record_hash"
        ]}
        rec.update(
            {
                "is_active": True,
                "import_batch_id": batch_id,
                "updated_by": username,
                "updated_at": now,
                "created_by": username,
            }
        )
        # Convert pandas/NumPy scalar values to plain Python values before
        # sending them to PostgREST. ``hpv_dose`` becomes a float column in
        # pandas whenever some rows are blank (e.g. 1.0 / NaN), while the
        # Supabase column is INTEGER. Sending 1.0 can therefore fail with
        # PostgreSQL error 22P02 (invalid input syntax for type integer).
        for key, value in list(rec.items()):
            if value is None or (not isinstance(value, (bool, int, str, date, datetime)) and pd.isna(value)):
                rec[key] = None
                continue

            if key == "hpv_dose" and value is not None:
                rec[key] = int(float(value))
            elif isinstance(value, np.integer):
                rec[key] = int(value)
            elif isinstance(value, np.floating):
                rec[key] = float(value)
            elif isinstance(value, np.bool_):
                rec[key] = bool(value)

        upsert_rows.append(rec)

    if upsert_rows:
        # Keep chunks comfortably below PostgREST payload limits.
        for start in range(0, len(upsert_rows), 300):
            supabase.table(RECORD_TABLE).upsert(upsert_rows[start:start + 300], on_conflict="row_key").execute()

    if diff["removed"]:
        for start in range(0, len(diff["removed"]), 300):
            keys = diff["removed"][start:start + 300]
            (
                supabase.table(RECORD_TABLE)
                .update({"is_active": False, "import_batch_id": batch_id, "updated_by": username, "updated_at": now})
                .in_("row_key", keys)
                .execute()
            )

    audit = _audit_rows(diff, municipality, batch_id, username)
    if audit:
        for start in range(0, len(audit), 300):
            supabase.table(AUDIT_TABLE).insert(audit[start:start + 300]).execute()

    _rebuild_aggregates(supabase, municipality, diff["scopes"], batch_id, username)
    return batch_id


def _template_bytes() -> bytes | None:
    path = Path(__file__).resolve().parent / "assets" / "SBI_Linelist_Template.xlsx"
    if path.exists():
        return path.read_bytes()
    return None


def _render_validation_preview(valid: pd.DataFrame, issues: pd.DataFrame, warnings: list[str], diff: dict | None = None) -> None:
    c1, c2, c3 = st.columns(3)
    c1.metric("Validated Learners", f"{len(valid):,}")
    c2.metric("Validation Issues", f"{len(issues):,}")
    scope_count = len(_scope_tuples(valid)) if not valid.empty else 0
    c3.metric("Date-School-Grade Groups", f"{scope_count:,}")

    if warnings:
        with st.expander(f"Validation notes ({len(warnings)})", expanded=False):
            for warning in warnings[:100]:
                st.write(warning)

    if not issues.empty:
        st.markdown("#### Rows requiring correction")
        st.dataframe(issues, width="stretch", hide_index=True)
        return

    if valid.empty:
        st.write("No learner rows were found in the uploaded file.")
        return

    preview_cols = [
        "activity_date", "school_id", "school_name", "first_name", "middle_name", "last_name",
        "sex", "grade_level", "mr_status", "td_status", "hpv_dose", "hpv_status", "reason_code"
    ]
    for col in preview_cols:
        if col not in valid.columns:
            valid[col] = None
    preview = valid[preview_cols].copy()
    preview.columns = [
        "Activity Date", "School ID", "School Name", "First Name", "Middle Name", "Last Name",
        "Sex", "Grade", "MR Status", "Td Status", "HPV Dose", "HPV Status", "Reason Code"
    ]
    st.markdown("#### Validated preview")
    st.dataframe(preview.head(100), width="stretch", hide_index=True)

    if diff is not None:
        st.markdown("#### Revision comparison")
        a, m, r, u = st.columns(4)
        a.metric("Added", f"{len(diff['added']):,}")
        m.metric("Modified", f"{len(diff['modified']):,}")
        r.metric("Removed", f"{len(diff['removed']):,}")
        u.metric("Unchanged", f"{len(diff['unchanged']):,}")
        if not diff["preview"].empty:
            st.dataframe(diff["preview"], width="stretch", hide_index=True)


def render_linelist_upload(supabase, targets: pd.DataFrame, municipality: str, username: str) -> None:
    st.markdown(
        '<h3><i class="fa-solid fa-file-arrow-up" style="color:#0033A0;margin-right:8px;"></i>Step 1 — Upload Line List</h3>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f"Upload SBI learner outcome records for **{municipality}**. The system validates each row, calculates the G1/G4/G7 VaccTrack figures, and updates the RHU tracker automatically."
    )
    st.warning("Learner records contain identifiable vaccination information. Use only authorized RHU/NIP accounts. The current deployment still relies on application-level municipality restrictions; database-side per-RHU RLS is not yet enforced.")

    template = _template_bytes()
    if template:
        st.download_button(
            "1A. Download SBI Line List Template (Excel)",
            data=template,
            file_name="SBI_Linelist_Template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="linelist_template_download",
        )

    # Rotate the uploader key after a successful import so Streamlit clears the
    # selected file. This prevents the just-imported file from being re-evaluated
    # on rerun and showing an unnecessary "no changes" message.
    upload_reset = int(st.session_state.get("sbi_linelist_upload_reset", 0))

    success_notice = st.session_state.pop("sbi_linelist_import_success_notice", None)
    if success_notice:
        st.success(success_notice)

    uploaded = st.file_uploader(
        "1B. Upload completed line list",
        type=["xlsx", "xls", "xlsm", "csv"],
        key=f"sbi_linelist_upload_{upload_reset}",
        help="For a revision, upload the complete learner list for each Activity Date + School + Grade group included in the file.",
    )
    if uploaded is None:
        return

    raw_bytes = uploaded.getvalue()
    raw_df, read_error = _read_upload(uploaded)
    if read_error:
        st.error(read_error)
        return

    valid, issues, warnings = _validate_upload(raw_df, municipality, targets)
    if not issues.empty or valid.empty:
        _render_validation_preview(valid, issues, warnings)
        return

    try:
        existing_active = _fetch_records(supabase, municipality, active_only=True)
    except Exception as exc:
        st.error(f"Unable to compare the upload with existing line-list records: {exc}")
        return

    diff = _compare_revision(valid, existing_active)
    _render_validation_preview(valid, issues, warnings, diff)

    st.markdown(
        "For every **Activity Date + School + Grade** group included in this upload, the file is treated as the complete current list. "
        "Existing learners missing from those groups will be marked removed only after you confirm the import."
    )

    total_changes = len(diff["added"]) + len(diff["modified"]) + len(diff["removed"])
    if total_changes == 0:
        st.info(
            "Already up to date. This line list matches the saved learner records, so there is nothing to import. "
            "If this was the file you just imported, you may continue to Step 2 — VaccTrack Encoding."
        )
        return

    confirm = st.checkbox(
        f"I reviewed the comparison and want to apply {total_changes:,} change(s).",
        key=f"linelist_revision_confirm_{upload_reset}",
    )
    if st.button("1C. Import / Apply Revision", type="primary", width="stretch", disabled=not confirm, key="linelist_apply_import"):
        try:
            with st.spinner("Saving learner records and rebuilding accomplishment totals..."):
                batch_id = _apply_import(
                    supabase,
                    valid,
                    diff,
                    municipality,
                    username,
                    uploaded.name,
                    raw_bytes,
                )
            st.session_state["sbi_linelist_import_success_notice"] = (
                f"Line list imported successfully (Batch #{batch_id}). The upload field was cleared. "
                "You can now continue to Step 2 — VaccTrack Encoding."
            )
            st.session_state["sbi_linelist_upload_reset"] = upload_reset + 1
            st.toast(f"Line list imported successfully. Batch #{batch_id}")
            st.rerun()
        except Exception as exc:
            st.error(f"Unable to import the line list: {exc}")


def render_linelist_history(supabase, municipality: str) -> None:
    st.markdown(
        '<h3><i class="fa-solid fa-clock-rotate-left" style="color:#0033A0;margin-right:8px;"></i>Corrections / Line List History</h3>',
        unsafe_allow_html=True,
    )
    try:
        imports = _fetch_imports(supabase, municipality)
    except Exception as exc:
        st.error(f"Unable to load line-list history: {exc}")
        return
    if imports.empty:
        st.write("No line-list imports have been saved yet.")
        return

    display = imports.copy()
    rename = {
        "id": "Batch",
        "file_name": "File",
        "uploaded_by": "Uploaded By",
        "uploaded_at": "Uploaded At",
        "activity_date_min": "First Activity Date",
        "activity_date_max": "Last Activity Date",
        "rows_uploaded": "Rows",
        "rows_added": "Added",
        "rows_modified": "Modified",
        "rows_removed": "Removed",
        "rows_unchanged": "Unchanged",
    }
    cols = [c for c in rename if c in display.columns]
    display = display[cols].rename(columns=rename)
    st.dataframe(display, width="stretch", hide_index=True)

    try:
        active = _fetch_records(supabase, municipality, active_only=True)
    except Exception:
        active = pd.DataFrame()
    if not active.empty:
        active["activity_date"] = pd.to_datetime(active["activity_date"], errors="coerce").dt.date
        date_choices = sorted(active["activity_date"].dropna().unique().tolist(), reverse=True)
        if date_choices:
            chosen_date = st.selectbox(
                "Review active learner records for activity date",
                date_choices,
                format_func=lambda d: d.strftime("%b %d, %Y") if hasattr(d, "strftime") else str(d),
                key="linelist_history_date",
            )
            day = active[active["activity_date"].eq(chosen_date)].copy()
            detail_cols = [
                "school_name", "grade_level", "section", "learner_id", "last_name", "first_name", "middle_name",
                "sex", "mr_status", "mr_lot_batch", "td_status", "td_lot_batch", "hpv_dose", "hpv_status",
                "hpv_lot_batch", "reason_code", "reason_details", "remarks"
            ]
            for col in detail_cols:
                if col not in day.columns:
                    day[col] = None
            detail = day[detail_cols].copy()
            detail.columns = [
                "School", "Grade", "Section", "Learner ID / LRN", "Last Name", "First Name", "Middle Name",
                "Sex", "MR Status", "MR Lot/Batch", "Td Status", "Td Lot/Batch", "HPV Dose", "HPV Status",
                "HPV Lot/Batch", "Reason Code", "Reason Details", "Remarks"
            ]
            with st.expander(f"Active learner records: {chosen_date.strftime('%b %d, %Y')}", expanded=False):
                st.dataframe(detail.sort_values(["School", "Grade", "Last Name", "First Name"]), width="stretch", hide_index=True)


def _g4_actual_target(actual_targets: pd.DataFrame, municipality: str, school_id: str) -> int | None:
    if actual_targets is None or actual_targets.empty or "School ID" not in actual_targets.columns:
        return None
    work = actual_targets.copy()
    if "Municipality" in work.columns:
        work = work.loc[work["Municipality"].map(normalize_municipality_key).eq(normalize_municipality_key(municipality))].copy()
    work["School ID"] = work["School ID"].map(_clean_school_id)
    work = work[work["School ID"].eq(_clean_school_id(school_id))].copy()
    if work.empty:
        return None
    if "Target Entry Status" in work.columns:
        complete = work[work["Target Entry Status"].astype(str).eq("Complete")]
        if not complete.empty:
            work = complete
    if "G4 Female" not in work.columns:
        return None
    value = pd.to_numeric(work["G4 Female"], errors="coerce").dropna()
    if value.empty:
        return None
    return int(value.sum())


def _encoding_rows(active: pd.DataFrame, actual_targets: pd.DataFrame, municipality: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if active.empty:
        empty = pd.DataFrame()
        return empty, empty, empty
    work = active.copy()
    work["activity_date"] = pd.to_datetime(work["activity_date"], errors="coerce").dt.date
    work["school_id"] = work["school_id"].map(_clean_school_id)

    def _status(name: str, given_col: str | None = None, hpv: bool = False) -> pd.Series:
        if name in work.columns:
            series = work[name].astype("string").fillna("").str.strip()
            if series.ne("").any():
                return series
        if hpv:
            dose = pd.to_numeric(work.get("hpv_dose"), errors="coerce")
            return pd.Series(np.where(dose.notna(), "Given", ""), index=work.index, dtype="string")
        if given_col and given_col in work.columns:
            return work[given_col].map(lambda v: "Given" if v is True else ("Not Given" if v is False else "")).astype("string")
        return pd.Series("", index=work.index, dtype="string")

    work["MR Status Norm"] = _status("mr_status", "mr_given")
    work["Td Status Norm"] = _status("td_status", "td_given")
    work["HPV Status Norm"] = _status("hpv_status", hpv=True)
    work["HPV"] = pd.to_numeric(work.get("hpv_dose"), errors="coerce")
    work["Reason Code Norm"] = work.get("reason_code", pd.Series("", index=work.index)).astype("string").fillna("").str.zfill(2)

    def _reason_counts(grp: pd.DataFrame) -> dict:
        missed = (
            grp["MR Status Norm"].isin(["Deferred", "Refused"])
            | grp["Td Status Norm"].isin(["Deferred", "Refused"])
            | grp["HPV Status Norm"].isin(["Deferred", "Refused"])
        )
        codes = grp.loc[missed, "Reason Code Norm"]
        return {f"Reason {code}": int(codes.eq(code).sum()) for code in REASON_LABELS}

    def _mr_td(grade: str, prefix: str) -> pd.DataFrame:
        sub = work[work["grade_level"].astype(str).eq(grade)].copy()
        if sub.empty:
            return pd.DataFrame()
        keys = ["activity_date", "school_id", "school_name"]
        out_rows = []
        for key, grp in sub.groupby(keys, dropna=False):
            male = grp["sex"].eq("Male")
            female = grp["sex"].eq("Female")
            mr = grp["MR Status Norm"]
            td = grp["Td Status Norm"]
            row = {
                "Report Date": key[0],
                "School ID": key[1],
                "School": key[2],
                f"{prefix}.A MR (Male)": int((male & mr.eq("Given")).sum()),
                f"{prefix}.B MR (Female)": int((female & mr.eq("Given")).sum()),
                f"{prefix}.C Td (Male)": int((male & td.eq("Given")).sum()),
                f"{prefix}.D Td (Female)": int((female & td.eq("Given")).sum()),
                "MR Deferred": int(mr.eq("Deferred").sum()),
                "Td Deferred": int(td.eq("Deferred").sum()),
                "MR Refused": int(mr.eq("Refused").sum()),
                "Td Refused": int(td.eq("Refused").sum()),
            }
            row.update(_reason_counts(grp))
            out_rows.append(row)
        return pd.DataFrame(out_rows).sort_values(["Report Date", "School"])

    g1 = _mr_td("G1", "G1")
    g7 = _mr_td("G7", "G7")

    g4_sub = work[work["grade_level"].astype(str).eq("G4")].copy()
    g4_rows = []
    if not g4_sub.empty:
        for key, grp in g4_sub.groupby(["activity_date", "school_id", "school_name"], dropna=False):
            hpv = grp["HPV"]
            status = grp["HPV Status Norm"]
            row = {
                "Report Date": key[0],
                "School ID": key[1],
                "School": key[2],
                "G4.A Actual Grade 4 Female": _g4_actual_target(actual_targets, municipality, key[1]),
                "G4.B HPV First Dose": int((hpv.eq(1) & status.eq("Given")).sum()),
                "G4.C HPV Second Dose": int((hpv.eq(2) & status.eq("Given")).sum()),
                "G4.D Deferred First Dose": int((hpv.eq(1) & status.eq("Deferred")).sum()),
                "G4.E Deferred Second Dose": int((hpv.eq(2) & status.eq("Deferred")).sum()),
                "G4.F Refused First Dose": int((hpv.eq(1) & status.eq("Refused")).sum()),
                "G4.G Refused Second Dose": int((hpv.eq(2) & status.eq("Refused")).sum()),
            }
            row.update(_reason_counts(grp))
            g4_rows.append(row)
    g4 = pd.DataFrame(g4_rows)
    if not g4.empty:
        g4 = g4.sort_values(["Report Date", "School"])
    return g1, g4, g7


def render_vacctrack_encoding_summary(supabase, municipality: str, actual_targets: pd.DataFrame) -> None:
    st.markdown(
        '<h3><i class="fa-solid fa-clipboard-list" style="color:#0033A0;margin-right:8px;"></i>Step 2 — VaccTrack Encoding Summary</h3>',
        unsafe_allow_html=True,
    )
    st.markdown("These counts are calculated directly from active learner records. Copy the generated G1, G4, and G7 values into VaccTrack.")
    try:
        active = _fetch_records(supabase, municipality, active_only=True)
    except Exception as exc:
        st.error(f"Unable to load active learner records: {exc}")
        return
    if active.empty:
        st.write("No active learner line-list records are available yet.")
        return

    g1, g4, g7 = _encoding_rows(active, actual_targets, municipality)
    selected_date = st.date_input(
        "Activity / VaccTrack report date",
        value=datetime.now(MANILA_TZ).date(),
        key="vacctrack_encoding_date",
        help="Defaults to today's date. Change it when encoding or reviewing an earlier activity date.",
    )

    tabs = st.tabs(["Grade 1", "Grade 4", "Grade 7"])
    for tab, frame, label in zip(tabs, [g1, g4, g7], ["Grade 1", "Grade 4", "Grade 7"]):
        with tab:
            if frame.empty:
                st.write(f"No {label} learner records are available.")
                continue
            day = frame[frame["Report Date"].eq(selected_date)].copy()
            if day.empty:
                st.write(f"No {label} learner records were uploaded for this date.")
                continue
            if label in {"Grade 1", "Grade 7"}:
                prefix = "G1" if label == "Grade 1" else "G7"
                main_cols = [
                    "Report Date", "School ID", "School",
                    f"{prefix}.A MR (Male)", f"{prefix}.B MR (Female)",
                    f"{prefix}.C Td (Male)", f"{prefix}.D Td (Female)",
                ]
            else:
                main_cols = [
                    "Report Date", "School ID", "School",
                    "G4.A Actual Grade 4 Female", "G4.B HPV First Dose", "G4.C HPV Second Dose",
                    "G4.D Deferred First Dose", "G4.E Deferred Second Dose",
                    "G4.F Refused First Dose", "G4.G Refused Second Dose",
                ]
            st.dataframe(day[[c for c in main_cols if c in day.columns]], width="stretch", hide_index=True)
            with st.expander("View VaccTrack reason counts", expanded=False):
                reason_cols = [f"Reason {code}" for code in REASON_LABELS if f"Reason {code}" in day.columns]
                if reason_cols:
                    reason_totals = day[reason_cols].sum().reset_index()
                    reason_totals.columns = ["Reason", "Count"]
                    reason_totals["Code"] = reason_totals["Reason"].str.replace("Reason ", "", regex=False)
                    reason_totals["Description"] = reason_totals["Code"].map(REASON_LABELS)
                    reason_totals = reason_totals[["Code", "Description", "Count"]]
                    st.dataframe(reason_totals, width="stretch", hide_index=True)
            st.download_button(
                f"Download {label} VaccTrack Summary (CSV)",
                data=day.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"SBI_{municipality.replace(' ', '_')}_{selected_date}_{label.replace(' ', '_')}_VaccTrack.csv",
                mime="text/csv",
                key=f"vacctrack_encoding_{label}",
            )
            if label == "Grade 4" and day.get("G4.A Actual Grade 4 Female", pd.Series(dtype=float)).isna().any():
                st.warning("One or more Grade 4 schools do not have a usable Actual Target in the dashboard. Verify G4.A in VaccTrack before encoding.")


def render_live_linelist_summary(supabase, municipality: str) -> None:
    """Small operational summary derived from active learner records."""
    try:
        active = _fetch_records(supabase, municipality, active_only=True)
    except Exception:
        return
    if active.empty:
        return

    def _status(frame: pd.DataFrame, status_col: str, given_col: str | None = None, hpv: bool = False) -> pd.Series:
        if status_col in frame.columns:
            out = frame[status_col].astype("string").fillna("").str.strip()
            if out.ne("").any():
                return out
        if hpv:
            dose = pd.to_numeric(frame.get("hpv_dose"), errors="coerce")
            return pd.Series(np.where(dose.notna(), "Given", ""), index=frame.index, dtype="string")
        if given_col and given_col in frame.columns:
            return frame[given_col].map(lambda v: "Given" if v is True else ("Not Given" if v is False else "")).astype("string")
        return pd.Series("", index=frame.index, dtype="string")

    active["MR Status Norm"] = _status(active, "mr_status", "mr_given")
    active["Td Status Norm"] = _status(active, "td_status", "td_given")
    active["HPV Status Norm"] = _status(active, "hpv_status", hpv=True)
    active["HPV Dose Norm"] = pd.to_numeric(active.get("hpv_dose"), errors="coerce")

    mr_total = int(active["MR Status Norm"].eq("Given").sum())
    td_total = int(active["Td Status Norm"].eq("Given").sum())
    g4 = active[active["grade_level"].astype(str).eq("G4")]
    g4_hpv1 = int((g4["HPV Dose Norm"].eq(1) & g4["HPV Status Norm"].eq("Given")).sum())
    g4_hpv2 = int((g4["HPV Dose Norm"].eq(2) & g4["HPV Status Norm"].eq("Given")).sum())

    st.markdown("#### Provisional RHU Learner-Record Totals")
    a, b, c, d = st.columns(4)
    a.metric("MR", f"{mr_total:,}")
    b.metric("Td", f"{td_total:,}")
    c.metric("G4 HPV1", f"{g4_hpv1:,}")
    d.metric("G4 HPV2", f"{g4_hpv2:,}")
    st.caption("Operational/provisional figures from the uploaded learner records. VaccTrack remains the official dataset.")
