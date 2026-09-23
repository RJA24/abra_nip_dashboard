"""SBI vaccine-requirement data and calculation helpers.

The Actual Targets worksheet remains the coverage denominator.  This module
stores only the number of learners already vaccinated *before* the upcoming
SBI activity, then derives the remaining operational workload.

Blank prior-vaccination fields are intentionally preserved as missing values;
they are never silently treated as zero.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Iterable

import numpy as np
import pandas as pd

from core.map_labels import canonical_municipality_name, normalize_municipality_key


CAMPAIGN_YEAR = 2026
PRIOR_TABLE = "sbi_prior_vaccination"
SUBMISSION_TABLE = "sbi_requirement_submissions"

PRIOR_DB_COLUMNS = [
    "g1_mr_prior",
    "g1_td_prior",
    "g4_hpv_d1_prior",
    "g4_hpv_d2_prior",
    "g7_mr_prior",
    "g7_td_prior",
]

PRIOR_DISPLAY_COLUMNS = {
    "g1_mr_prior": "G1 MR Prior",
    "g1_td_prior": "G1 Td Prior",
    "g4_hpv_d1_prior": "G4 HPV Dose 1 Prior",
    "g4_hpv_d2_prior": "G4 HPV Dose 2 Prior",
    "g7_mr_prior": "G7 MR Prior",
    "g7_td_prior": "G7 Td Prior",
}

DISPLAY_TO_DB = {v: k for k, v in PRIOR_DISPLAY_COLUMNS.items()}

IDENTITY_COLUMNS = ["Municipality", "Barangay", "School ID", "School Name"]
TARGET_COLUMNS = ["G1 Total", "G4 Female", "G7 Total"]


def _clean_school_id(value: object) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _coerce_nullable_int(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    # Keep blanks as pd.NA while rejecting fractional values later in validation.
    return numeric.astype("Float64")


def requirements_schema_available(supabase) -> tuple[bool, str]:
    """Check the two requirement tables and the RHU municipality account field."""
    try:
        supabase.table(PRIOR_TABLE).select("school_id").limit(1).execute()
    except Exception:
        return False, f"Missing table: {PRIOR_TABLE}"

    try:
        supabase.table(SUBMISSION_TABLE).select("municipality").limit(1).execute()
    except Exception:
        return False, f"Missing table: {SUBMISSION_TABLE}"

    try:
        supabase.table("user_accounts").select("assigned_muni").limit(1).execute()
    except Exception:
        return False, "Missing user_accounts.assigned_muni"

    return True, "Ready"


def _paged_select(supabase, table: str, select_clause: str = "*") -> list[dict]:
    rows: list[dict] = []
    offset = 0
    limit = 1000
    while True:
        response = (
            supabase.table(table)
            .select(select_clause)
            .range(offset, offset + limit - 1)
            .execute()
        )
        chunk = response.data or []
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    return rows


def fetch_prior_vaccination(
    supabase,
    campaign_year: int = CAMPAIGN_YEAR,
    municipality: str | None = None,
) -> pd.DataFrame:
    try:
        query = supabase.table(PRIOR_TABLE).select("*").eq("campaign_year", int(campaign_year))
        if municipality:
            query = query.eq("municipality_key", normalize_municipality_key(municipality))
        response = query.execute()
        rows = response.data or []
    except Exception:
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if "school_id" in df.columns:
        df["school_id"] = df["school_id"].map(_clean_school_id)
    for col in PRIOR_DB_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Float64")
    return df


def fetch_submission_status(
    supabase,
    campaign_year: int = CAMPAIGN_YEAR,
) -> pd.DataFrame:
    try:
        response = (
            supabase.table(SUBMISSION_TABLE)
            .select("*")
            .eq("campaign_year", int(campaign_year))
            .execute()
        )
        rows = response.data or []
    except Exception:
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if "municipality_key" not in df.columns and "municipality" in df.columns:
        df["municipality_key"] = df["municipality"].map(normalize_municipality_key)
    return df


def municipality_submission_record(
    submissions: pd.DataFrame,
    municipality: str,
) -> dict:
    if submissions is None or submissions.empty:
        return {}
    key = normalize_municipality_key(municipality)
    work = submissions.copy()
    if "municipality_key" not in work.columns:
        work["municipality_key"] = work.get("municipality", "").map(normalize_municipality_key)
    matched = work[work["municipality_key"].astype(str).eq(key)]
    if matched.empty:
        return {}
    return matched.iloc[0].to_dict()


def target_signature(actual_targets: pd.DataFrame, municipality: str) -> str:
    """Return a stable signature of the municipality's current Actual Targets."""
    if actual_targets is None or actual_targets.empty:
        return ""

    key = normalize_municipality_key(municipality)
    work = actual_targets.copy()
    work["_muni_key"] = work["Municipality"].map(normalize_municipality_key)
    work = work[work["_muni_key"].eq(key)].copy()
    if work.empty:
        return ""

    for col in ["School ID", "School Name", "Target Entry Status", *TARGET_COLUMNS]:
        if col not in work.columns:
            work[col] = "" if col not in TARGET_COLUMNS else 0

    work["School ID"] = work["School ID"].map(_clean_school_id)
    for col in TARGET_COLUMNS:
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0).round(0).astype(int)

    payload = []
    for _, row in work.sort_values(["School ID", "School Name"]).iterrows():
        payload.append(
            [
                row["School ID"],
                str(row["School Name"] or "").strip(),
                int(row["G1 Total"]),
                int(row["G4 Female"]),
                int(row["G7 Total"]),
                str(row.get("Target Entry Status") or ""),
            ]
        )
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _remaining(target: pd.Series, prior: pd.Series) -> pd.Series:
    target_num = pd.to_numeric(target, errors="coerce").fillna(0)
    prior_num = pd.to_numeric(prior, errors="coerce")
    result = pd.Series(pd.NA, index=target.index, dtype="Float64")
    zero_target = target_num.le(0)
    result.loc[zero_target] = 0
    known = (~zero_target) & prior_num.notna()
    result.loc[known] = np.maximum(target_num.loc[known] - prior_num.loc[known], 0)
    return result


def build_school_requirement_frame(
    actual_targets: pd.DataFrame,
    prior: pd.DataFrame | None = None,
    municipality: str | None = None,
) -> pd.DataFrame:
    """Join Actual Targets with prior-vaccination counts and derive remaining need."""
    if actual_targets is None or actual_targets.empty:
        return pd.DataFrame()

    work = actual_targets.copy()
    for col in IDENTITY_COLUMNS:
        if col not in work.columns:
            work[col] = ""
    for col in TARGET_COLUMNS:
        if col not in work.columns:
            work[col] = 0

    work["School ID"] = work["School ID"].map(_clean_school_id)
    work = work[work["School ID"].ne("")].copy()
    work["Municipality"] = work["Municipality"].map(canonical_municipality_name)
    work["_muni_key"] = work["Municipality"].map(normalize_municipality_key)

    if municipality:
        work = work[work["_muni_key"].eq(normalize_municipality_key(municipality))].copy()

    for col in TARGET_COLUMNS:
        work[col] = pd.to_numeric(work[col], errors="coerce").fillna(0).clip(lower=0).round(0).astype(int)

    if "Target Entry Status" not in work.columns:
        work["Target Entry Status"] = "Complete"
    work["Target Entry Status"] = work["Target Entry Status"].fillna("Pending").astype(str)

    prior_work = prior.copy() if prior is not None and not prior.empty else pd.DataFrame()
    if not prior_work.empty:
        prior_work["school_id"] = prior_work["school_id"].map(_clean_school_id)
        keep = ["school_id", *PRIOR_DB_COLUMNS, "updated_at", "updated_by"]
        for col in keep:
            if col not in prior_work.columns:
                prior_work[col] = pd.NA
        prior_work = prior_work[keep].drop_duplicates("school_id", keep="last")
        work = work.merge(prior_work, left_on="School ID", right_on="school_id", how="left")
    else:
        for col in PRIOR_DB_COLUMNS:
            work[col] = pd.NA
        work["updated_at"] = pd.NA
        work["updated_by"] = pd.NA

    for db_col, display_col in PRIOR_DISPLAY_COLUMNS.items():
        work[display_col] = _coerce_nullable_int(work[db_col])

    work["G1 MR Remaining"] = _remaining(work["G1 Total"], work["G1 MR Prior"])
    work["G1 Td Remaining"] = _remaining(work["G1 Total"], work["G1 Td Prior"])
    work["G7 MR Remaining"] = _remaining(work["G7 Total"], work["G7 MR Prior"])
    work["G7 Td Remaining"] = _remaining(work["G7 Total"], work["G7 Td Prior"])
    work["G4 HPV Dose 1 Remaining"] = _remaining(work["G4 Female"], work["G4 HPV Dose 1 Prior"])

    d1 = pd.to_numeric(work["G4 HPV Dose 1 Prior"], errors="coerce")
    d2 = pd.to_numeric(work["G4 HPV Dose 2 Prior"], errors="coerce")
    g4 = pd.to_numeric(work["G4 Female"], errors="coerce").fillna(0)
    hpv_d2_pending = pd.Series(pd.NA, index=work.index, dtype="Float64")
    hpv_d2_pending.loc[g4.le(0)] = 0
    hpv_known = g4.gt(0) & d1.notna() & d2.notna()
    hpv_d2_pending.loc[hpv_known] = np.maximum(d1.loc[hpv_known] - d2.loc[hpv_known], 0)
    work["G4 HPV Dose 2 Pending"] = hpv_d2_pending

    # Full-series outstanding doses are useful for longer-range supply planning,
    # but are kept separate from the immediate Dose 2 pending workload.
    hpv_series = pd.Series(pd.NA, index=work.index, dtype="Float64")
    hpv_series.loc[g4.le(0)] = 0
    hpv_series.loc[hpv_known] = (
        np.maximum(g4.loc[hpv_known] - d1.loc[hpv_known], 0)
        + np.maximum(g4.loc[hpv_known] - d2.loc[hpv_known], 0)
    )
    work["G4 HPV Series Doses Outstanding"] = hpv_series

    work["MR Remaining"] = work[["G1 MR Remaining", "G7 MR Remaining"]].sum(axis=1, min_count=2)
    work["Td Remaining"] = work[["G1 Td Remaining", "G7 Td Remaining"]].sum(axis=1, min_count=2)

    required_pairs = [
        ("G1 Total", "G1 MR Prior"),
        ("G1 Total", "G1 Td Prior"),
        ("G4 Female", "G4 HPV Dose 1 Prior"),
        ("G4 Female", "G4 HPV Dose 2 Prior"),
        ("G7 Total", "G7 MR Prior"),
        ("G7 Total", "G7 Td Prior"),
    ]
    required_count = pd.Series(0, index=work.index, dtype="int64")
    entered_count = pd.Series(0, index=work.index, dtype="int64")
    for target_col, prior_col in required_pairs:
        needed = pd.to_numeric(work[target_col], errors="coerce").fillna(0).gt(0)
        entered = pd.to_numeric(work[prior_col], errors="coerce").notna()
        required_count += needed.astype(int)
        entered_count += (needed & entered).astype(int)

    target_complete = work["Target Entry Status"].str.casefold().eq("complete")
    work["Requirement Fields Required"] = required_count
    work["Requirement Fields Entered"] = entered_count
    work["Requirement Entry Status"] = "Pending"
    work.loc[(entered_count.gt(0)) & (entered_count.lt(required_count)), "Requirement Entry Status"] = "Partial"
    work.loc[(entered_count.eq(required_count)) & target_complete, "Requirement Entry Status"] = "Complete"
    work.loc[~target_complete, "Requirement Entry Status"] = "Target Incomplete"
    work["Requirement Complete"] = work["Requirement Entry Status"].eq("Complete")

    return work.drop(columns=["_muni_key", "school_id"], errors="ignore")


def validate_requirement_frame(frame: pd.DataFrame, require_complete: bool = True) -> list[str]:
    """Return human-readable validation errors for an edited municipality frame.

    ``require_complete=False`` validates only values that were entered, which
    allows an RHU to save an incomplete Draft.
    """
    if frame is None or frame.empty:
        return ["No school rows are available to validate."]

    errors: list[str] = []
    for _, row in frame.iterrows():
        school = str(row.get("School Name") or row.get("School ID") or "Unknown school").strip()
        target_status = str(row.get("Target Entry Status") or "").strip().casefold()
        if require_complete and target_status != "complete":
            errors.append(f"{school}: Actual Target is not complete.")

        checks = [
            ("G1 MR Prior", "G1 Total"),
            ("G1 Td Prior", "G1 Total"),
            ("G4 HPV Dose 1 Prior", "G4 Female"),
            ("G4 HPV Dose 2 Prior", "G4 Female"),
            ("G7 MR Prior", "G7 Total"),
            ("G7 Td Prior", "G7 Total"),
        ]
        for prior_col, target_col in checks:
            value = pd.to_numeric(pd.Series([row.get(prior_col)]), errors="coerce").iloc[0]
            target = pd.to_numeric(pd.Series([row.get(target_col)]), errors="coerce").fillna(0).iloc[0]
            if pd.isna(value):
                if require_complete and target > 0:
                    errors.append(f"{school}: {prior_col} is still blank.")
                continue
            if value < 0:
                errors.append(f"{school}: {prior_col} cannot be negative.")
            if abs(float(value) - round(float(value))) > 1e-9:
                errors.append(f"{school}: {prior_col} must be a whole number.")
            if value > target:
                errors.append(
                    f"{school}: {prior_col} ({int(value)}) exceeds {target_col} ({int(target)})."
                )

        d1 = pd.to_numeric(pd.Series([row.get("G4 HPV Dose 1 Prior")]), errors="coerce").iloc[0]
        d2 = pd.to_numeric(pd.Series([row.get("G4 HPV Dose 2 Prior")]), errors="coerce").iloc[0]
        if pd.notna(d1) and pd.notna(d2) and d2 > d1:
            errors.append(
                f"{school}: HPV Dose 2 prior ({int(d2)}) cannot exceed Dose 1 prior ({int(d1)})."
            )

    # Keep the UI readable if many rows repeat the same issue.
    return errors


def _none_or_int(value: object) -> int | None:
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(numeric):
        return None
    return int(round(float(numeric)))


def save_requirement_rows(
    supabase,
    frame: pd.DataFrame,
    updated_by: str,
    campaign_year: int = CAMPAIGN_YEAR,
) -> int:
    if frame is None or frame.empty:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    records: list[dict] = []
    for _, row in frame.iterrows():
        school_id = _clean_school_id(row.get("School ID"))
        if not school_id:
            continue
        municipality = canonical_municipality_name(row.get("Municipality"))
        record = {
            "campaign_year": int(campaign_year),
            "school_id": school_id,
            "municipality": municipality,
            "municipality_key": normalize_municipality_key(municipality),
            "barangay": str(row.get("Barangay") or "").strip(),
            "school_name": str(row.get("School Name") or "").strip(),
            "g1_mr_prior": _none_or_int(row.get("G1 MR Prior")),
            "g1_td_prior": _none_or_int(row.get("G1 Td Prior")),
            "g4_hpv_d1_prior": _none_or_int(row.get("G4 HPV Dose 1 Prior")),
            "g4_hpv_d2_prior": _none_or_int(row.get("G4 HPV Dose 2 Prior")),
            "g7_mr_prior": _none_or_int(row.get("G7 MR Prior")),
            "g7_td_prior": _none_or_int(row.get("G7 Td Prior")),
            "updated_at": now,
            "updated_by": str(updated_by or "Unknown"),
        }
        records.append(record)

    if records:
        supabase.table(PRIOR_TABLE).upsert(
            records,
            on_conflict="campaign_year,school_id",
        ).execute()
    return len(records)


def set_submission_status(
    supabase,
    municipality: str,
    status: str,
    actor: str,
    campaign_year: int = CAMPAIGN_YEAR,
    signature: str | None = None,
    reopened: bool = False,
) -> None:
    status_clean = str(status or "Draft").strip().title()
    if status_clean not in {"Draft", "Submitted"}:
        raise ValueError("Submission status must be Draft or Submitted.")

    now = datetime.now(timezone.utc).isoformat()
    municipality_name = canonical_municipality_name(municipality)
    key = normalize_municipality_key(municipality_name)
    payload = {
        "campaign_year": int(campaign_year),
        "municipality": municipality_name,
        "municipality_key": key,
        "status": status_clean,
        "updated_at": now,
        "updated_by": str(actor or "Unknown"),
    }
    if status_clean == "Submitted":
        payload.update(
            {
                "submitted_at": now,
                "submitted_by": str(actor or "Unknown"),
                "target_signature": str(signature or ""),
            }
        )
    elif reopened:
        payload.update(
            {
                "reopened_at": now,
                "reopened_by": str(actor or "Unknown"),
            }
        )

    supabase.table(SUBMISSION_TABLE).upsert(
        payload,
        on_conflict="campaign_year,municipality_key",
    ).execute()


def submission_state(
    submissions: pd.DataFrame,
    actual_targets: pd.DataFrame,
    municipality: str,
) -> tuple[str, bool]:
    """Return (display status, stale_target_flag)."""
    record = municipality_submission_record(submissions, municipality)
    if not record:
        return "Not Started", False

    status = str(record.get("status") or "Draft").strip().title()
    if status != "Submitted":
        return "Draft", False

    saved_signature = str(record.get("target_signature") or "").strip()
    current_signature = target_signature(actual_targets, municipality)
    stale = bool(saved_signature and current_signature and saved_signature != current_signature)
    return ("Needs Review" if stale else "Submitted"), stale


def aggregate_requirement_by_municipality(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()

    metrics = [
        "MR Remaining",
        "Td Remaining",
        "G4 HPV Dose 1 Remaining",
        "G4 HPV Dose 2 Pending",
        "G4 HPV Series Doses Outstanding",
    ]
    work = frame.copy()
    for col in metrics:
        work[col] = pd.to_numeric(work[col], errors="coerce")

    grouped = (
        work.groupby("Municipality", dropna=False)[metrics]
        .sum(min_count=1)
        .reset_index()
    )
    completion = (
        work.groupby("Municipality", dropna=False)
        .agg(
            Schools=("School ID", "nunique"),
            Complete_Schools=("Requirement Complete", "sum"),
        )
        .reset_index()
    )
    result = grouped.merge(completion, on="Municipality", how="left")
    result["Completion %"] = np.where(
        result["Schools"].gt(0),
        result["Complete_Schools"] / result["Schools"] * 100,
        np.nan,
    )
    return result


def submitted_municipality_keys(
    submissions: pd.DataFrame,
    actual_targets: pd.DataFrame,
    municipalities: Iterable[str],
) -> set[str]:
    keys: set[str] = set()
    for municipality in municipalities:
        status, stale = submission_state(submissions, actual_targets, municipality)
        if status == "Submitted" and not stale:
            keys.add(normalize_municipality_key(municipality))
    return keys
