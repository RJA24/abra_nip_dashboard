"""SBI learner line-list upload, revisions, and VaccTrack encoding.

The learner-level records are the RHU operational source used to calculate what should be
encoded into VaccTrack. VaccTrack remains the official/final SBI dataset. The current RHU
workflow is intentionally limited to Grade 1, Grade 4, and Grade 7 to keep encoding simple.
"""

from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Iterable
from uuid import uuid4
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

CURRENT_REQUIRED_COLUMNS = [
    "Activity Date",
    "School ID",
    "School Name",
    "System Learner ID",
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

CURRENT_OPTIONAL_COLUMNS = [
    "Section",
    "MR Lot/Batch No.",
    "Td Lot/Batch No.",
    "HPV Lot/Batch No.",
    "Reason Details",
]

# v5.18 status-based template, accepted only as a transition path. The raw
# Learner ID / LRN value is never saved; it is converted to a legacy system ID.
LEGACY_STATUS_REQUIRED_COLUMNS = [
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

# Keep older vaccinated-only templates readable during the transition.
LEGACY_VACCINATED_REQUIRED_COLUMNS = [
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
        supabase.table(RECORD_TABLE).select("id,system_learner_id,mr_status,reason_code,section").limit(1).execute()
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


def _normalize_system_id(value: object) -> str:
    return _clean_text(value).upper()


def _legacy_system_id(
    municipality: str,
    school_id: str,
    activity_date: date | None,
    grade: str,
    legacy_id: str,
    last_name: str,
    first_name: str,
    middle_name: str,
    sex: str,
) -> str:
    # Transition-only deterministic ID. The original Learner ID / LRN is not
    # persisted. New activities should always use the v5.19 generated template.
    identity = legacy_id or "|".join([last_name, first_name, middle_name, sex])
    raw = "|".join([
        normalize_municipality_key(municipality),
        _clean_school_id(school_id),
        str(activity_date or ""),
        grade,
        identity.upper(),
    ])
    return "LEGACY-" + sha256(raw.encode("utf-8")).hexdigest()[:16].upper()


def _row_key(record: dict) -> str:
    raw = "|".join(
        [
            normalize_municipality_key(record.get("municipality")),
            _clean_school_id(record.get("school_id")),
            str(record.get("activity_date")),
            _normalize_system_id(record.get("system_learner_id")),
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
            "system_learner_id",
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
    """Normalize the v5.19 generated-ID template or a supported legacy template."""
    if raw_df is None:
        return pd.DataFrame(), pd.DataFrame(), []

    source = raw_df.copy()
    source.columns = [_clean_text(c) for c in source.columns]
    current = all(col in source.columns for col in CURRENT_REQUIRED_COLUMNS)
    legacy_status = all(col in source.columns for col in LEGACY_STATUS_REQUIRED_COLUMNS)
    legacy_vaccinated = all(col in source.columns for col in LEGACY_VACCINATED_REQUIRED_COLUMNS)
    if not current and not legacy_status and not legacy_vaccinated:
        missing = [col for col in CURRENT_REQUIRED_COLUMNS if col not in source.columns]
        issues = pd.DataFrame(
            [{"Row": "Header", "Learner": "", "Problem": f"Missing required column: {col}"} for col in missing]
        )
        return pd.DataFrame(), issues, []

    status_template = current or legacy_status
    if status_template:
        for col in CURRENT_OPTIONAL_COLUMNS:
            if col not in source.columns:
                source[col] = None
        selected_columns = (CURRENT_REQUIRED_COLUMNS if current else LEGACY_STATUS_REQUIRED_COLUMNS) + CURRENT_OPTIONAL_COLUMNS
    else:
        selected_columns = LEGACY_VACCINATED_REQUIRED_COLUMNS

    source = source[selected_columns].copy().dropna(how="all")
    # Fresh templates pre-fill System Learner ID on every available row. Follow-up
    # templates may also pre-fill learner identity fields while leaving Activity
    # Date and vaccine outcomes blank until that learner actually returns. Such
    # untouched rows are intentionally ignored. If an outcome is entered without
    # a date, the row is kept and validation will flag the missing Activity Date.
    if current:
        outcome_columns = [
            "Activity Date", "MR Status", "MR Lot/Batch No.", "Td Status", "Td Lot/Batch No.",
            "HPV Dose", "HPV Status", "HPV Lot/Batch No.", "Reason Code", "Reason Details", "Remarks",
        ]
        blank_mask = source[outcome_columns].apply(lambda r: all(_clean_text(v) == "" for v in r), axis=1)
    else:
        blank_check_columns = [col for col in selected_columns if col != "System Learner ID"]
        blank_mask = source[blank_check_columns].apply(lambda r: all(_clean_text(v) == "" for v in r), axis=1)
    source = source.loc[~blank_mask].copy()

    roster = _school_roster(targets, municipality)
    roster_map = roster.set_index("School ID").to_dict("index") if not roster.empty else {}

    valid_records: list[dict] = []
    issues: list[dict] = []
    warnings: list[str] = []
    if not current:
        warnings.append(
            "Legacy line-list template detected. The old Learner ID / LRN is used only to create a transition ID and is not saved. "
            "Use the v5.19 template for new activities."
        )

    for i, row in source.iterrows():
        excel_row = int(i) + 2
        school_id = _clean_school_id(row.get("School ID"))
        activity_date = _parse_date(row.get("Activity Date"))
        last_name = _norm_name(row.get("Last Name"))
        first_name = _norm_name(row.get("First Name"))
        middle_name = _norm_name(row.get("Middle Name"))
        sex = _norm_sex(row.get("Sex"))
        grade = _norm_grade(row.get("Grade Level"))
        section = _clean_text(row.get("Section")) if status_template else ""
        remarks = _clean_text(row.get("Remarks"))
        legacy_id = _clean_text(row.get("Learner ID / LRN"))
        if current:
            system_learner_id = _normalize_system_id(row.get("System Learner ID"))
        else:
            system_learner_id = _legacy_system_id(
                municipality, school_id, activity_date, grade, legacy_id,
                last_name, first_name, middle_name, sex,
            )
        learner_label = " ".join(x for x in [first_name, middle_name, last_name] if x).strip() or system_learner_id or f"Row {excel_row}"

        row_problems: list[str] = []
        if activity_date is None:
            row_problems.append("Activity Date is invalid or blank")
        if not school_id:
            row_problems.append("School ID is blank")
        elif school_id not in roster_map:
            row_problems.append(f"School ID {school_id} is not in the assigned municipality roster")
        if current:
            if not system_learner_id:
                row_problems.append("System Learner ID is blank. Download a fresh template; IDs are generated automatically.")
            elif not re.fullmatch(r"SBI-[A-Z0-9]{12,32}", system_learner_id):
                row_problems.append("System Learner ID is invalid or was edited. Download a fresh template and do not change the ID column.")
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

        if status_template:
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
            if status_template and (not mr_status or not td_status):
                row_problems.append("MR Status and Td Status must both be completed for Grade 1/7")
            if _clean_text(row.get("HPV Dose")) or (status_template and _clean_text(row.get("HPV Status"))):
                row_problems.append("HPV fields must be blank for Grade 1/7")
                hpv_dose = None
                hpv_status = ""
                hpv_lot = ""
            if status_template and mr_status == "Given" and not mr_lot:
                warnings.append(f"Row {excel_row}: MR was marked Given but MR Lot/Batch No. is blank.")
            if status_template and td_status == "Given" and not td_lot:
                warnings.append(f"Row {excel_row}: Td was marked Given but Td Lot/Batch No. is blank.")
        elif grade == "G4":
            if sex and sex != "Female":
                row_problems.append("Grade 4 HPV learner records must be Female")
            if hpv_dose not in {1, 2}:
                row_problems.append("HPV Dose must be 1 or 2 for Grade 4")
            if status_template and not hpv_status:
                row_problems.append("HPV Status is required for Grade 4")
            if status_template and (_clean_text(row.get("MR Status")) or _clean_text(row.get("Td Status"))):
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
            "system_learner_id": system_learner_id,
            # Names are retained only in this in-memory validated frame for the
            # current upload preview. They are deliberately omitted from DB writes.
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
        # The same System Learner ID may legitimately appear on multiple activity
        # dates. This is how a learner who receives MR on one day and Td on a
        # later day is tracked. Within one upload, however, the ID must still
        # refer to the same learner identity/school/grade.
        conflicting_ids: set[str] = set()
        for system_id, group in valid.groupby("system_learner_id", dropna=False):
            if len(group) <= 1:
                continue
            identity_cols = ["school_id", "grade_level", "sex"]
            if any(group[col].fillna("").astype(str).nunique(dropna=False) > 1 for col in identity_cols):
                conflicting_ids.add(str(system_id))
                conflict_rows = [
                    {
                        "Row": rec["_excel_row"],
                        "Learner": rec["_learner_label"],
                        "Problem": (
                            "This System Learner ID is attached to different school/grade/sex details in the same file. "
                            "Keep the same ID only for the same learner; a follow-up vaccination may use the same ID on a new Activity Date."
                        ),
                    }
                    for _, rec in group.iterrows()
                ]
                issue_df = pd.concat([issue_df, pd.DataFrame(conflict_rows)], ignore_index=True)
            else:
                name_cols = ["last_name", "first_name", "middle_name"]
                if any(group[col].fillna("").astype(str).nunique(dropna=False) > 1 for col in name_cols):
                    warnings.append(
                        f"System Learner ID {system_id} has different name text across activity rows. "
                        "This may be a spelling correction; verify that all rows still refer to the same learner."
                    )

        if conflicting_ids:
            valid = valid.loc[~valid["system_learner_id"].astype(str).isin(conflicting_ids)].copy()

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


def _system_id_conflicts(valid: pd.DataFrame, existing_all: pd.DataFrame) -> pd.DataFrame:
    """Block accidental reassignment of a System Learner ID, but allow new dates.

    A System Learner ID is stable for one learner. The same ID may therefore
    exist in several saved rows as long as school, grade, and sex remain the
    same. Activity Date is intentionally NOT part of the conflict check.
    """
    if valid is None or valid.empty or existing_all is None or existing_all.empty:
        return pd.DataFrame(columns=["Row", "Learner", "Problem"])
    if "system_learner_id" not in existing_all.columns:
        return pd.DataFrame(columns=["Row", "Learner", "Problem"])

    existing = existing_all.copy()
    existing["system_learner_id"] = existing["system_learner_id"].fillna("").astype(str).str.upper().str.strip()
    issues: list[dict] = []
    for _, rec in valid.iterrows():
        system_id = str(rec.get("system_learner_id") or "").upper().strip()
        if not system_id:
            continue
        matches = existing[existing["system_learner_id"].eq(system_id)]
        if matches.empty:
            continue

        # The ID may be reused for another Activity Date, but not for another
        # school/grade/sex identity. Section may legitimately change.
        rec_school = _clean_school_id(rec.get("school_id"))
        rec_grade = _clean_text(rec.get("grade_level"))
        rec_sex = _clean_text(rec.get("sex"))
        mismatch = matches.apply(
            lambda r: (
                _clean_school_id(r.get("school_id")) != rec_school
                or _clean_text(r.get("grade_level")) != rec_grade
                or _clean_text(r.get("sex")) != rec_sex
            ),
            axis=1,
        ).any()
        if mismatch:
            issues.append({
                "Row": rec.get("_excel_row", ""),
                "Learner": rec.get("_learner_label", system_id),
                "Problem": (
                    f"System Learner ID {system_id} is already saved for a different school, grade, or sex. "
                    "Keep an ID with the same learner. Reusing the same ID on a new Activity Date is allowed when it is the same learner."
                ),
            })
    return pd.DataFrame(issues, columns=["Row", "Learner", "Problem"])


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
                    "System Learner ID": str(rec.get("system_learner_id") or ""),
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
        "municipality", "school_id", "school_name", "barangay", "activity_date", "system_learner_id",
        "sex", "grade_level", "section",
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
            "system_learner_id", "sex", "grade_level", "section",
            "mr_given", "mr_status", "mr_lot_batch", "td_given", "td_status", "td_lot_batch",
            "hpv_dose", "hpv_status", "hpv_lot_batch", "reason_code", "reason_details",
            "remarks", "record_hash"
        ]}
        # PII from the uploaded workbook is intentionally not persisted. Setting
        # the legacy identity columns to NULL also scrubs them if a legacy row is revised.
        rec.update({"learner_id": None, "last_name": None, "first_name": None, "middle_name": None})
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


def _prepare_followup_rows(raw_df: pd.DataFrame) -> tuple[list[dict], str]:
    """Create one reusable identity row per System Learner ID from a v5.19 workbook.

    Vaccination/date fields are deliberately cleared. The encoder fills Activity
    Date and today's outcomes only for learners who actually have a follow-up
    activity. Prefilled identity-only rows remain safely ignored by validation.
    """
    if raw_df is None or raw_df.empty:
        return [], "The selected workbook has no learner rows."

    source = raw_df.copy()
    source.columns = [_clean_text(c) for c in source.columns]
    required = [
        "School ID", "School Name", "System Learner ID", "Last Name", "First Name",
        "Middle Name", "Sex", "Grade Level",
    ]
    missing = [col for col in required if col not in source.columns]
    if missing:
        return [], (
            "Follow-up generation requires a v5.19 System Learner ID workbook. "
            "Missing: " + ", ".join(missing)
        )
    if "Section" not in source.columns:
        source["Section"] = ""

    source["System Learner ID"] = source["System Learner ID"].map(_normalize_system_id)
    source = source[source["System Learner ID"].ne("")].copy()
    # Ignore untouched pre-generated blank rows from a fresh template.
    identity_check = ["School ID", "School Name", "Last Name", "First Name", "Sex", "Grade Level"]
    source = source.loc[
        ~source[identity_check].apply(lambda r: all(_clean_text(v) == "" for v in r), axis=1)
    ].copy()
    if source.empty:
        return [], "No completed learner identities were found in that workbook."

    # A single System Learner ID must describe one learner identity. Names are
    # available only inside the workbook, so check them here before building the
    # reusable follow-up roster.
    conflicts: list[str] = []
    identity_cols = ["School ID", "Grade Level", "Sex"]
    for system_id, group in source.groupby("System Learner ID", dropna=False):
        if any(group[col].fillna("").astype(str).map(_clean_text).nunique(dropna=False) > 1 for col in identity_cols):
            conflicts.append(str(system_id))
    if conflicts:
        preview = ", ".join(conflicts[:5])
        more = "..." if len(conflicts) > 5 else ""
        return [], (
            "The workbook contains System Learner IDs attached to conflicting school/grade/sex details: "
            f"{preview}{more}. Correct the source workbook before creating a follow-up copy."
        )

    # Keep the last occurrence so the most recent section/school display text is
    # used when the source workbook already contains several activity dates.
    source = source.drop_duplicates("System Learner ID", keep="last")
    rows: list[dict] = []
    for _, row in source.iterrows():
        rows.append({
            "Activity Date": "",
            "School ID": _clean_school_id(row.get("School ID")),
            "School Name": _clean_text(row.get("School Name")),
            "Section": _clean_text(row.get("Section")),
            "System Learner ID": _normalize_system_id(row.get("System Learner ID")),
            "Last Name": _clean_text(row.get("Last Name")),
            "First Name": _clean_text(row.get("First Name")),
            "Middle Name": _clean_text(row.get("Middle Name")),
            "Sex": _clean_text(row.get("Sex")),
            "Grade Level": _clean_text(row.get("Grade Level")),
        })
    return rows, ""


def _template_bytes(row_count: int = 2000, prefilled_rows: list[dict] | None = None) -> bytes | None:
    """Build a fresh or follow-up workbook with locked System Learner IDs."""
    try:
        import xlsxwriter
    except ImportError:
        return None

    prefilled_rows = list(prefilled_rows or [])
    row_count = max(int(row_count), len(prefilled_rows) + 100)

    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    blue = "#0033A0"
    light_blue = "#EAF1FF"
    border = "#CBD5E1"

    title_fmt = workbook.add_format({"bold": True, "font_size": 16, "font_color": blue})
    label_fmt = workbook.add_format({"bold": True, "font_color": "#1E293B", "valign": "top"})
    text_fmt = workbook.add_format({"text_wrap": True, "valign": "top"})
    header_fmt = workbook.add_format({
        "bold": True, "font_color": "#FFFFFF", "bg_color": blue,
        "border": 1, "border_color": border, "align": "center", "valign": "vcenter",
        "text_wrap": True, "locked": True,
    })
    unlocked_text = workbook.add_format({"locked": False, "border": 1, "border_color": "#E2E8F0", "valign": "top"})
    unlocked_center = workbook.add_format({"locked": False, "border": 1, "border_color": "#E2E8F0", "align": "center", "valign": "top"})
    unlocked_date = workbook.add_format({"locked": False, "border": 1, "border_color": "#E2E8F0", "num_format": "yyyy-mm-dd", "align": "center"})
    id_fmt = workbook.add_format({
        "locked": True, "bg_color": light_blue, "font_color": blue, "bold": True,
        "border": 1, "border_color": "#BFDBFE", "align": "center",
    })

    instructions = workbook.add_worksheet("Instructions")
    instructions.hide_gridlines(2)
    instructions.set_column("A:A", 24)
    instructions.set_column("B:B", 95)
    instructions.merge_range("A1:B1", "Abra NIP Dashboard — SBI Learner Line List v5.19.2", title_fmt)
    instruction_rows = [
        ("Purpose", "Use one row per learner per activity date. The dashboard calculates Grade 1, Grade 4 and Grade 7 VaccTrack figures, deferral/refusal counts, and reason totals."),
        ("Multiple activity dates", "A workbook may contain several activity dates. Every row describes what happened on that row's Activity Date."),
        ("System Learner ID", "Automatically generated and locked. Keep the same ID for the same learner across follow-up dates and revisions. It is not a DepEd LRN."),
        ("Follow-up vaccination", "A new vaccination day is a NEW ROW, not a revision. Keep the same System Learner ID and enter the new Activity Date. Use the dashboard's Create Follow-up Line List tool to carry IDs and learner identity safely."),
        ("Example", "MR yesterday, Td today: yesterday = MR Given / Td Not Given; today = MR Not Given / Td Given. Do not carry yesterday's Given status into today's row."),
        ("Revision / Correction", "Use this only to fix an already-saved activity on the SAME date. Keep the same System Learner ID and upload the complete Activity Date + School + Grade group. A new date is a follow-up, not a revision."),
        ("Authorized use", "For authorized RHU/NIP users only. Upload only the correct learner records for your assigned municipality. Do not place names, LRN, or unnecessary identifiers in Remarks or Reason Details."),
        ("Grade 1 / Grade 7", "Complete both MR Status and Td Status for each activity row. Leave HPV fields blank."),
        ("Grade 4 Female", "Set HPV Dose to 1 or 2 and complete HPV Status. Leave MR/Td fields blank."),
        ("Statuses", "Use Given, Deferred, Refused, or Not Given."),
        ("Deferred / Refused", "Select one Reason Code (01–19). Reason Details is optional and is useful when Code 19 is selected."),
        ("Lot / Batch", "Enter the corresponding lot/batch number when a vaccine is marked Given. Blank lot/batch values are accepted but flagged for review."),
        ("Follow-up roster rows", "In a generated follow-up workbook, identity rows with blank Activity Date and blank outcomes are ignored. Enter a date only for learners with a new activity to record."),
        ("Wrong Activity Date", "Do not simply add the correct date and leave the wrong-date record. Review the correction guide; if a whole batch/date was wrong, ask NIP/System Admin to remove the incorrect import first."),
        ("Wrong School / Grade", "Do not force the same System Learner ID into a different school/grade. Ask NIP/System Admin to correct the bad assignment first."),
        ("Before confirming", "Review Added / Modified / Removed / Unchanged. Never confirm a large unexpected number of Removed records."),
        ("Scope", "This line list covers Grade 1, Grade 4 and Grade 7 SBI reporting for VaccTrack."),
    ]
    for r, (label, text) in enumerate(instruction_rows, start=2):
        instructions.write(r, 0, label, label_fmt)
        instructions.write(r, 1, text, text_fmt)
    instructions.set_row(0, 26)

    refs = workbook.add_worksheet("Reference Values")
    refs.write_column("A1", ["Sex", "Male", "Female"])
    refs.write_column("B1", ["Grade Level", "Grade 1", "Grade 4", "Grade 7"])
    refs.write_column("C1", ["Status", "Given", "Deferred", "Refused", "Not Given"])
    refs.write_column("D1", ["HPV Dose", 1, 2])
    reason_values = [f"{code} - {label}" for code, label in REASON_LABELS.items()]
    refs.write_column("E1", ["Reason Code"] + reason_values)
    refs.hide()

    sheet = workbook.add_worksheet("Line List")
    sheet.hide_gridlines(2)
    sheet.freeze_panes(1, 5)
    headers = [
        "Activity Date", "School ID", "School Name", "Section", "System Learner ID",
        "Last Name", "First Name", "Middle Name", "Sex", "Grade Level",
        "MR Status", "MR Lot/Batch No.", "Td Status", "Td Lot/Batch No.",
        "HPV Dose", "HPV Status", "HPV Lot/Batch No.", "Reason Code", "Reason Details", "Remarks",
    ]
    for c, header in enumerate(headers):
        sheet.write(0, c, header, header_fmt)
    sheet.set_row(0, 34)
    widths = [13, 12, 28, 12, 23, 18, 18, 18, 10, 13, 13, 18, 13, 18, 10, 13, 18, 40, 28, 28]
    for c, width in enumerate(widths):
        fmt = id_fmt if c == 4 else (unlocked_date if c == 0 else unlocked_center if c in {1, 8, 9, 10, 12, 14, 15} else unlocked_text)
        sheet.set_column(c, c, width, fmt)

    # Prefill follow-up identity rows first. Vaccination/date fields are blank by
    # design. Remaining rows receive new IDs for learners not previously listed.
    header_index = {header: idx for idx, header in enumerate(headers)}
    for r in range(1, row_count + 1):
        if r <= len(prefilled_rows):
            src = prefilled_rows[r - 1]
            system_id = _normalize_system_id(src.get("System Learner ID")) or ("SBI-" + uuid4().hex[:16].upper())
            sheet.write(r, 4, system_id, id_fmt)
            for field in ["Activity Date", "School ID", "School Name", "Section", "Last Name", "First Name", "Middle Name", "Sex", "Grade Level"]:
                value = src.get(field, "")
                if _clean_text(value) == "":
                    continue
                c = header_index[field]
                fmt = unlocked_date if field == "Activity Date" else unlocked_center if field in {"School ID", "Sex", "Grade Level"} else unlocked_text
                sheet.write(r, c, value, fmt)
        else:
            sheet.write(r, 4, "SBI-" + uuid4().hex[:16].upper(), id_fmt)

    last_row = row_count + 1
    sheet.autofilter(0, 0, last_row - 1, len(headers) - 1)
    sheet.data_validation(f"I2:I{last_row}", {"validate": "list", "source": "='Reference Values'!$A$2:$A$3"})
    sheet.data_validation(f"J2:J{last_row}", {"validate": "list", "source": "='Reference Values'!$B$2:$B$4"})
    for col in ["K", "M", "P"]:
        sheet.data_validation(f"{col}2:{col}{last_row}", {"validate": "list", "source": "='Reference Values'!$C$2:$C$5"})
    sheet.data_validation(f"O2:O{last_row}", {"validate": "list", "source": "='Reference Values'!$D$2:$D$3"})
    sheet.data_validation(f"R2:R{last_row}", {"validate": "list", "source": "='Reference Values'!$E$2:$E$20"})
    sheet.write_comment("E1", "System Learner IDs are generated automatically. Keep the same ID for follow-up activity rows and revisions.")
    sheet.write_comment("A1", "A new vaccination day uses a new row/date. Do not overwrite the old activity row when a learner returns later.")
    sheet.write_comment("F1", "Names are used during upload validation but are not saved in the dashboard database.")
    sheet.protect("", {"select_locked_cells": False, "select_unlocked_cells": True, "autofilter": True, "sort": True})

    workbook.close()
    return output.getvalue()


def _followup_template_bytes(raw_df: pd.DataFrame) -> tuple[bytes | None, int, str]:
    rows, error = _prepare_followup_rows(raw_df)
    if error:
        return None, 0, error
    workbook_bytes = _template_bytes(row_count=max(2000, len(rows) + 100), prefilled_rows=rows)
    if workbook_bytes is None:
        return None, 0, "Follow-up workbook generation is unavailable. Add xlsxwriter>=3.2.0 and redeploy."
    return workbook_bytes, len(rows), ""


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
        "activity_date", "school_id", "school_name", "system_learner_id", "first_name", "middle_name", "last_name",
        "sex", "grade_level", "mr_status", "td_status", "hpv_dose", "hpv_status", "reason_code"
    ]
    for col in preview_cols:
        if col not in valid.columns:
            valid[col] = None
    preview = valid[preview_cols].copy()
    preview.columns = [
        "Activity Date", "School ID", "School Name", "System Learner ID", "First Name", "Middle Name", "Last Name",
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


def render_training_mode(targets: pd.DataFrame, municipality: str) -> None:
    st.info(
        "Training Mode does not save anything to the database. Files are validated only in your current browser session."
    )
    session_key = f"sbi_training_baseline_{normalize_municipality_key(municipality)}"

    template = _template_bytes()
    if template:
        st.download_button(
            "Download Practice Line List",
            data=template,
            file_name="SBI_Practice_LineList.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"training_template_{normalize_municipality_key(municipality)}",
        )

    with st.expander("Practice a follow-up activity", expanded=False):
        source = st.file_uploader(
            "Practice source workbook",
            type=["xlsx", "xls", "xlsm", "csv"],
            key=f"training_followup_source_{normalize_municipality_key(municipality)}",
        )
        if source is not None:
            source_df, source_error = _read_upload(source)
            if source_error:
                st.error(source_error)
            else:
                followup_bytes, learner_count, followup_error = _followup_template_bytes(source_df)
                if followup_error:
                    st.error(followup_error)
                elif followup_bytes:
                    st.success(f"Practice follow-up roster prepared for {learner_count:,} learner(s).")
                    st.download_button(
                        "Download Practice Follow-up Line List",
                        data=followup_bytes,
                        file_name="SBI_Practice_Followup_LineList.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"training_followup_download_{normalize_municipality_key(municipality)}",
                    )

    uploaded = st.file_uploader(
        "Upload a practice line list",
        type=["xlsx", "xls", "xlsm", "csv"],
        key=f"training_upload_{normalize_municipality_key(municipality)}",
    )

    baseline = st.session_state.get(session_key)
    has_baseline = isinstance(baseline, pd.DataFrame) and not baseline.empty
    if has_baseline:
        st.caption("A practice baseline is active. Your next upload will be compared against it like a revision.")

    if uploaded is not None:
        raw_df, read_error = _read_upload(uploaded)
        if read_error:
            st.error(read_error)
        else:
            valid, issues, warnings = _validate_upload(raw_df, municipality, targets)
            if not issues.empty or valid.empty:
                _render_validation_preview(valid, issues, warnings)
            else:
                comparison_base = baseline if has_baseline else pd.DataFrame()
                diff = _compare_revision(valid, comparison_base)
                _render_validation_preview(valid, issues, warnings, diff)

                summary = (
                    valid.groupby(["activity_date", "school_name", "grade_level"], dropna=False)
                    .size()
                    .reset_index(name="Learners")
                    .rename(
                        columns={
                            "activity_date": "Activity Date",
                            "school_name": "School",
                            "grade_level": "Grade",
                        }
                    )
                )
                st.dataframe(summary, width="stretch", hide_index=True)

                label = "Apply to Practice Baseline" if has_baseline else "Set as Practice Baseline"
                if st.button(
                    label,
                    type="primary",
                    width="stretch",
                    key=f"training_apply_{normalize_municipality_key(municipality)}",
                ):
                    st.session_state[session_key] = valid.copy()
                    st.toast("Practice baseline updated. No production data was saved.")
                    st.rerun()

    if has_baseline and st.button(
        "Reset Practice Session",
        width="stretch",
        key=f"training_reset_{normalize_municipality_key(municipality)}",
    ):
        st.session_state.pop(session_key, None)
        st.rerun()


def render_linelist_upload(supabase, targets: pd.DataFrame, municipality: str, username: str) -> None:
    st.markdown(
        '<h3><i class="fa-solid fa-file-arrow-up" style="color:#0033A0;margin-right:8px;"></i>Step 1 — Upload Line List</h3>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f"Upload SBI learner outcome records for **{municipality}**. The system validates each row, calculates the G1/G4/G7 VaccTrack figures, and updates the RHU tracker automatically."
    )
    st.markdown(
        "System Learner IDs are generated automatically and stay with the same learner. A follow-up vaccination on a new date is a **new activity row**, not a revision."
    )
    st.warning(
        "For authorized RHU/NIP users only. Please upload only the correct learner records for your assigned municipality."
    )

    template = _template_bytes()
    if template:
        st.download_button(
            "1A. Download Fresh SBI Line List Template (Excel)",
            data=template,
            file_name="SBI_Linelist_Template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="linelist_template_download",
        )
    else:
        st.error("Fresh template generation is unavailable. Add xlsxwriter>=3.2.0 to requirements.txt and redeploy.")

    with st.expander("Create Follow-up Line List", expanded=False):
        st.markdown(
            "Use this when learners return on another day. Upload a previous **v5.19+** workbook and the dashboard will create a new follow-up workbook "
            "with the same System Learner IDs and learner identity fields, but with Activity Date and vaccination outcomes blank. "
            "Enter the new date/outcomes only for learners who actually have a follow-up activity."
        )
        followup_source = st.file_uploader(
            "Previous System Learner ID workbook",
            type=["xlsx", "xls", "xlsm", "csv"],
            key="sbi_followup_source_upload",
        )
        if followup_source is not None:
            followup_df, followup_read_error = _read_upload(followup_source)
            if followup_read_error:
                st.error(followup_read_error)
            else:
                followup_bytes, learner_count, followup_error = _followup_template_bytes(followup_df)
                if followup_error:
                    st.error(followup_error)
                elif followup_bytes:
                    st.success(
                        f"Follow-up roster prepared for {learner_count:,} learner(s). Activity Date and vaccine outcome fields are blank by design."
                    )
                    st.download_button(
                        "Download Follow-up Line List",
                        data=followup_bytes,
                        file_name="SBI_Followup_LineList.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="sbi_followup_download",
                    )
                    st.markdown(
                        "**Follow-up example:** if MR was given yesterday and Td is given today, today's row should be **MR = Not Given** and **Td = Given**. "
                        "Do not carry yesterday's MR = Given into today's activity row."
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
        existing_all = _fetch_records(supabase, municipality, active_only=False)
    except Exception as exc:
        st.error(f"Unable to compare the upload with existing line-list records: {exc}")
        return

    conflicts = _system_id_conflicts(valid, existing_all)
    if not conflicts.empty:
        _render_validation_preview(valid, conflicts, warnings)
        return

    if existing_all.empty:
        existing_active = existing_all
    else:
        existing_active = existing_all.loc[existing_all["is_active"].eq(True)].copy() if "is_active" in existing_all.columns else existing_all.copy()

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


def _fetch_batch_audit(supabase, batch_id: int) -> pd.DataFrame:
    response = (
        supabase.table(AUDIT_TABLE)
        .select("id,row_key,action,old_values,new_values,changed_by,changed_at")
        .eq("import_batch_id", int(batch_id))
        .order("changed_at", desc=False)
        .limit(1000)
        .execute()
    )
    return pd.DataFrame(response.data or [])


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

    rows_total = int(pd.to_numeric(imports.get("rows_uploaded"), errors="coerce").fillna(0).sum())
    latest_raw = pd.to_datetime(imports.get("uploaded_at"), errors="coerce").max()
    latest_label = latest_raw.strftime("%b %d, %Y %I:%M %p") if not pd.isna(latest_raw) else "Not recorded"
    m1, m2, m3 = st.columns(3)
    m1.metric("Import Batches", f"{len(imports):,}")
    m2.metric("Rows Processed", f"{rows_total:,}")
    m3.metric("Latest Upload", latest_label)

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
    st.download_button(
        "Download Import History (CSV)",
        data=display.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"SBI_Import_History_{_canonical_muni(municipality).replace(' ', '_')}.csv",
        mime="text/csv",
        key="linelist_history_download",
    )

    batch_ids = pd.to_numeric(imports.get("id"), errors="coerce").dropna().astype(int).tolist()
    if batch_ids:
        selected_batch = st.selectbox(
            "Review a specific import batch",
            batch_ids,
            format_func=lambda value: f"Batch #{value}",
            key="linelist_history_batch",
        )
        try:
            audit = _fetch_batch_audit(supabase, selected_batch)
        except Exception:
            audit = pd.DataFrame()
        if not audit.empty:
            action_counts = audit["action"].fillna("Unknown").value_counts().rename_axis("Change").reset_index(name="Rows")
            st.dataframe(action_counts, width="stretch", hide_index=True)
            audit_export = audit.copy()
            for column in ["old_values", "new_values"]:
                if column in audit_export.columns:
                    audit_export[column] = audit_export[column].map(
                        lambda value: json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list)) else value
                    )
            st.download_button(
                f"Download Batch #{selected_batch} Change Log",
                data=audit_export.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"SBI_Batch_{selected_batch}_Change_Log.csv",
                mime="text/csv",
                key="linelist_batch_audit_download",
            )
        else:
            st.caption("No row-level change log was returned for this batch.")

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
                "school_name", "grade_level", "section", "system_learner_id",
                "sex", "mr_status", "mr_lot_batch", "td_status", "td_lot_batch", "hpv_dose", "hpv_status",
                "hpv_lot_batch", "reason_code", "reason_details", "remarks"
            ]
            for col in detail_cols:
                if col not in day.columns:
                    day[col] = None
            detail = day[detail_cols].copy()
            detail.columns = [
                "School", "Grade", "Section", "System Learner ID",
                "Sex", "MR Status", "MR Lot/Batch", "Td Status", "Td Lot/Batch", "HPV Dose", "HPV Status",
                "HPV Lot/Batch", "Reason Code", "Reason Details", "Remarks"
            ]
            st.dataframe(detail, width="stretch", hide_index=True)


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
