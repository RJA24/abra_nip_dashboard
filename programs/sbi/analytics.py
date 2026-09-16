"""Normalization and aggregation helpers for the SBI dashboard.

VaccTrack exports have changed over time. Grade 1 and Grade 7 have both
sex-disaggregated vaccination fields and older aggregate fields, while Grade 4
uses HPV dose-specific fields. This module converts those variants into stable
canonical columns before the Streamlit UI renders charts.
"""

from __future__ import annotations

from datetime import date
import re
import unicodedata

import numpy as np
import pandas as pd

from core.config import ABRA_MUNIS


REASON_LABELS = {
    "01": "Parent/caregiver or decision-maker unavailable",
    "02": "Fear of vaccine side effects",
    "03": "Concerns over vaccine safety",
    "04": "Refused extra dose / already completed routine vaccines",
    "05": "No time / no one to accompany child",
    "06": "Belief vaccine is ineffective, low quality, or expired",
    "07": "Belief child is too young",
    "08": "Already vaccinated / advised against by private doctor",
    "09": "Religious or personal beliefs",
    "10": "Lack of trust in vaccinator",
    "11": "Child sick, recently recovered, or recently discharged",
    "12": "Unaware of vaccination schedule/activity",
    "13": "No health worker visit or vaccine promotion",
    "14": "Visiting, recently moved, or not in target list",
    "15": "Too far / no transportation",
    "16": "Language or communication barrier",
    "17": "Caregiver disability or health condition limiting access",
    "18": "Fear of injection",
    "19": "Refused with no reason / other",
}


def _ascii_key(value: object) -> str:
    text = str(value or "").upper().replace("?", "N")
    text = unicodedata.normalize("NFKD", text).encode("ASCII", "ignore").decode("ASCII")
    return re.sub(r"[^A-Z0-9]", "", text)


_ABRA_KEY_TO_NAME = {_ascii_key(m): m.title() for m in ABRA_MUNIS}
_ABRA_KEY_TO_NAME[_ascii_key("PEÑARRUBIA")] = "Peñarrubia"
_ABRA_KEY_TO_NAME[_ascii_key("BANGUED")] = "Bangued"


def normalize_municipality_name(value: object) -> str:
    raw = str(value or "").strip()
    key = _ascii_key(raw.replace("(CAPITAL)", ""))

    # Known Abra municipality naming variants.
    if "BANGUED" in key:
        return "Bangued"

    if "PENARRUBIA" in key:
        return "Peñarrubia"

    # Target/GeoJSON sources may use:
    # "Licuan-Baay (Licuan)"
    # while VaccTrack uses "Licuan-Baay".
    if key.startswith("LICUANBAAY"):
        return "Licuan-Baay"

    # Handle both spellings found in source datasets.
    if key in {"SALAPADAN", "SALLAPADAN"}:
        return "Sallapadan"

    for muni_key, muni_name in _ABRA_KEY_TO_NAME.items():
        if key == muni_key:
            return muni_name

    return raw.title()


def _clean_identity(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()
    out.columns = [str(c).strip() for c in out.columns]

    # VaccTrack dummy/master exports contain all CAR provinces. Prefer the
    # explicit province marker when present; fall back to municipality matching.
    province_col = next((c for c in out.columns if c.strip().lower() == "province name"), None)
    if province_col:
        province = out[province_col].astype("string").fillna("").str.strip().str.upper()
        if province.eq("ABRA").any():
            out = out.loc[province.eq("ABRA")].copy()

    muni_col = "City/Municipality Name"
    if muni_col not in out.columns:
        out[muni_col] = ""

    out["Municipality"] = out[muni_col].map(normalize_municipality_name)
    abra_title = {m.title() for m in ABRA_MUNIS}
    abra_title.add("Peñarrubia")
    out = out[out["Municipality"].isin(abra_title)].copy()

    out["Barangay"] = out.get("Barangay Name", "").astype("string").fillna("").str.strip().str.title()
    out["Facility"] = out.get("Facility Name", "").astype("string").fillna("").str.strip()
    out["School ID"] = (
        out.get("School id", "")
        .astype("string")
        .fillna("")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )
    out["School Name"] = out.get("School name", "").astype("string").fillna("").str.strip()
    out["Report Date"] = pd.to_datetime(out.get("Report date", pd.NaT), errors="coerce", format="mixed")
    return out


def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(0.0, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").fillna(0.0)


def _reason_columns(df: pd.DataFrame) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for col in df.columns:
        match = re.match(r"^(\d{2})\s+", str(col).strip())
        if match and match.group(1) in REASON_LABELS:
            mapping[match.group(1)] = col
    return mapping


def _attach_reasons(source: pd.DataFrame, canonical: pd.DataFrame) -> pd.DataFrame:
    reason_cols = _reason_columns(source)
    for code in REASON_LABELS:
        canonical[f"Reason {code}"] = (
            _numeric_series(source, reason_cols[code]) if code in reason_cols
            else pd.Series(0.0, index=source.index, dtype="float64")
        )
    return canonical


def prepare_mr_td_events(df: pd.DataFrame, grade: str) -> pd.DataFrame:
    """Normalize Grade 1 or Grade 7 VaccTrack rows into one stable schema."""
    source = _clean_identity(df)
    if source.empty:
        return pd.DataFrame()

    grade = grade.upper().strip()
    if grade not in {"G1", "G7"}:
        raise ValueError("grade must be 'G1' or 'G7'")

    if grade == "G1":
        sex_mr_m = "G1.A Number of Students vaccinated with MR (Male)"
        sex_mr_f = "G1.B Number of Students vaccinated with MR (Female)"
        sex_td_m = "G1.C Number of Students vaccinated with TD (Male)"
        sex_td_f = "G1.D Number of Students vaccinated with TD (Female)"
        aggregate_mr = "G1.B Number of Students vaccinated with MR"
        aggregate_td = "G1.C Number of Students vaccinated with TD"
        reported_target = "G1.A Actual total number Grade 1 Students"
        deferred_mr = "G1.D Number of Students Deferred for the MR Vaccine"
        deferred_td = "G1.E Number of Students Deferred for the TD Vaccine"
        refused_mr = "G1.F Number of Students Who Refused the MR Vaccine"
        refused_td = "G1.G Number of Students Who Refused the TD Vaccine"
        grade_label = "Grade 1"
    else:
        sex_mr_m = "G7.A Number of Students vaccinated with MR (Male)"
        sex_mr_f = "G7.B Number of Students vaccinated with MR (Female)"
        sex_td_m = "G7.C Number of Students vaccinated with TD (Male)"
        sex_td_f = "G7.D Number of Students vaccinated with TD (Female)"
        aggregate_mr = "G7.B Number of Students Who Received the MR Vaccine"
        aggregate_td = "G7.C Number of Students Who Received the TD Vaccine"
        reported_target = "G7.A Actual total number Grade 7 Students"
        deferred_mr = "G7.D Number of Students Deferred for the MR Vaccine"
        deferred_td = "G7.E Number of Students Deferred for the TD Vaccine"
        refused_mr = "G7.F Number of Students Who Refused the MR Vaccine"
        refused_td = "G7.G Number of Students Who Refused the TD Vaccine"
        grade_label = "Grade 7"

    canonical = source[[
        "Municipality", "Barangay", "Facility", "Report Date", "School ID", "School Name"
    ]].copy()
    canonical["Grade"] = grade_label
    canonical["MR Male"] = _numeric_series(source, sex_mr_m)
    canonical["MR Female"] = _numeric_series(source, sex_mr_f)
    canonical["Td Male"] = _numeric_series(source, sex_td_m)
    canonical["Td Female"] = _numeric_series(source, sex_td_f)

    # The source changed format across reporting periods. In the supplied data,
    # sex-disaggregated and aggregate vaccination columns do not overlap on the
    # same populated row, so adding both preserves all administrations.
    canonical["MR Doses"] = (
        canonical["MR Male"] + canonical["MR Female"] + _numeric_series(source, aggregate_mr)
    )
    canonical["Td Doses"] = (
        canonical["Td Male"] + canonical["Td Female"] + _numeric_series(source, aggregate_td)
    )
    canonical["Reported Target"] = _numeric_series(source, reported_target)
    canonical["MR Deferred"] = _numeric_series(source, deferred_mr)
    canonical["Td Deferred"] = _numeric_series(source, deferred_td)
    canonical["MR Refused"] = _numeric_series(source, refused_mr)
    canonical["Td Refused"] = _numeric_series(source, refused_td)
    canonical = _attach_reasons(source, canonical)

    # Remove true duplicate exports while retaining separate reporting dates.
    canonical = canonical.drop_duplicates().reset_index(drop=True)
    return canonical


def prepare_hpv_events(df: pd.DataFrame) -> pd.DataFrame:
    source = _clean_identity(df)
    if source.empty:
        return pd.DataFrame()

    canonical = source[[
        "Municipality", "Barangay", "Facility", "Report Date", "School ID", "School Name"
    ]].copy()
    canonical["Grade"] = "Grade 4 Female"
    canonical["Reported Target"] = _numeric_series(
        source, "G4.A Actual total number Grade 4 (Female) Students"
    )
    canonical["HPV Dose 1"] = _numeric_series(
        source, "G4.B Number of Students Who Received the First Dose of the HPV Vaccine"
    )
    canonical["HPV Dose 2"] = _numeric_series(
        source, "G4.C Number of Students Who Received the Second Dose of the HPV Vaccine"
    )
    canonical["HPV Deferred 1"] = _numeric_series(
        source, "G4.D Number of Students Deferred for the First Dose of the HPV Vaccine"
    )
    canonical["HPV Deferred 2"] = _numeric_series(
        source, "G4.E Number of Students Deferred for the Second Dose of the HPV Vaccine"
    )
    canonical["HPV Refused 1"] = _numeric_series(
        source, "G4.F Number of Students Who Refused the First Dose of the HPV Vaccine"
    )
    canonical["HPV Refused 2"] = _numeric_series(
        source, "G4.G Number of Students Who Refused the Second Dose of the HPV Vaccine"
    )
    canonical = _attach_reasons(source, canonical)
    canonical = canonical.drop_duplicates().reset_index(drop=True)
    return canonical


def build_effective_targets(
    baseline: pd.DataFrame,
    actual: pd.DataFrame,
) -> pd.DataFrame:
    """Build one per-school denominator table.

    Complete RHU actual targets replace the baseline for that school. Pending or
    partial actual entries fall back to the official baseline so province-wide
    coverage remains usable during target validation.
    """
    baseline = pd.DataFrame() if baseline is None else baseline.copy()
    actual = pd.DataFrame() if actual is None else actual.copy()

    if baseline.empty and actual.empty:
        return pd.DataFrame()

    if baseline.empty:
        base = actual.copy()
        base = base[base.get("Target Entry Status", "") == "Complete"].copy()
        if base.empty:
            return pd.DataFrame()
        base["G1 Target"] = pd.to_numeric(base.get("G1 Total", 0), errors="coerce").fillna(0)
        base["G4 Target"] = pd.to_numeric(base.get("G4 Female", 0), errors="coerce").fillna(0)
        base["G7 Target"] = pd.to_numeric(base.get("G7 Total", 0), errors="coerce").fillna(0)
        base["Target Source"] = "Actual"
        return base[["Municipality", "Barangay", "School ID", "School Name", "G1 Target", "G4 Target", "G7 Target", "Target Source"]]

    for col in ["Municipality", "Barangay", "School ID", "School Name"]:
        if col not in baseline.columns:
            baseline[col] = ""
        baseline[col] = baseline[col].astype("string").fillna("").str.strip()
    baseline["Municipality"] = baseline["Municipality"].map(normalize_municipality_name)
    baseline["School ID"] = baseline["School ID"].str.replace(r"\.0$", "", regex=True)

    for col in ["G1 Total", "G4 Female", "G7 Total"]:
        baseline[col] = pd.to_numeric(baseline.get(col, 0), errors="coerce").fillna(0)

    out = baseline[[
        "Municipality", "Barangay", "School ID", "School Name", "G1 Total", "G4 Female", "G7 Total"
    ]].copy()
    out = out.rename(columns={
        "G1 Total": "G1 Baseline",
        "G4 Female": "G4 Baseline",
        "G7 Total": "G7 Baseline",
    })

    actual_complete = pd.DataFrame()
    if not actual.empty and "Target Entry Status" in actual.columns:
        actual_complete = actual[actual["Target Entry Status"] == "Complete"].copy()

    if not actual_complete.empty:
        actual_complete["School ID"] = (
            actual_complete["School ID"].astype("string").fillna("").str.strip().str.replace(r"\.0$", "", regex=True)
        )
        for col in ["G1 Total", "G4 Female", "G7 Total"]:
            actual_complete[col] = pd.to_numeric(actual_complete.get(col, 0), errors="coerce").fillna(0)
        actual_small = actual_complete[["School ID", "G1 Total", "G4 Female", "G7 Total"]].drop_duplicates("School ID")
        actual_small = actual_small.rename(columns={
            "G1 Total": "G1 Actual",
            "G4 Female": "G4 Actual",
            "G7 Total": "G7 Actual",
        })
        out = out.merge(actual_small, on="School ID", how="left")
    else:
        out["G1 Actual"] = np.nan
        out["G4 Actual"] = np.nan
        out["G7 Actual"] = np.nan

    has_actual = out["G1 Actual"].notna() & out["G4 Actual"].notna() & out["G7 Actual"].notna()
    out["G1 Target"] = np.where(has_actual, out["G1 Actual"], out["G1 Baseline"])
    out["G4 Target"] = np.where(has_actual, out["G4 Actual"], out["G4 Baseline"])
    out["G7 Target"] = np.where(has_actual, out["G7 Actual"], out["G7 Baseline"])
    out["Target Source"] = np.where(has_actual, "Actual", "Baseline fallback")
    return out


def filter_location(df: pd.DataFrame, selected_muni: str | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if not selected_muni or selected_muni == "None":
        return df.copy()
    return df[df["Municipality"].astype(str).str.upper() == selected_muni.upper()].copy()


def filter_dates(df: pd.DataFrame, start_date: date | None, end_date: date | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "Report Date" not in out.columns:
        return out
    if start_date is not None:
        out = out[out["Report Date"] >= pd.Timestamp(start_date)]
    if end_date is not None:
        out = out[out["Report Date"] <= pd.Timestamp(end_date)]
    return out.copy()


def available_date_bounds(*frames: pd.DataFrame) -> tuple[date | None, date | None]:
    dates: list[pd.Timestamp] = []
    for df in frames:
        if df is not None and not df.empty and "Report Date" in df.columns:
            valid = pd.to_datetime(df["Report Date"], errors="coerce").dropna()
            if not valid.empty:
                dates.extend([valid.min(), valid.max()])
    if not dates:
        return None, None
    return min(dates).date(), max(dates).date()


def reason_summary(*frames: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for code, label in REASON_LABELS.items():
        total = 0.0
        for df in frames:
            col = f"Reason {code}"
            if df is not None and not df.empty and col in df.columns:
                total += pd.to_numeric(df[col], errors="coerce").fillna(0).sum()
        rows.append({"Reason Code": code, "Reason": label, "Count": total})
    return pd.DataFrame(rows)