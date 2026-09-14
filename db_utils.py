"""Database safety helpers for the Abra NIP dashboard."""
from __future__ import annotations

import logging
import time
from typing import Iterable, Any

import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)


def update_session_log_throttled(supabase, prefix: str = "Session Duration", every_seconds: int = 300) -> None:
    """Update the access log at most once every `every_seconds` per session."""
    if "login_time" not in st.session_state or "log_id" not in st.session_state:
        return

    now = time.time()
    last_write = float(st.session_state.get("last_session_log_write", 0) or 0)
    if now - last_write < every_seconds:
        return

    elapsed = max(0, int(now - float(st.session_state["login_time"])))
    minutes, seconds = divmod(elapsed, 60)
    hours, minutes = divmod(minutes, 60)
    formatted = f"{hours}h {minutes}m {seconds}s"

    try:
        supabase.table("access_logs").update({"action": f"{prefix}: {formatted}"}).eq(
            "id", st.session_state["log_id"]
        ).execute()
        st.session_state["last_session_log_write"] = now
    except Exception:
        logger.exception("Unable to update session access log")



def _most_common_nonblank(series: pd.Series) -> str:
    """Choose a stable representative value from repeated text fields."""
    cleaned = series.fillna("").astype(str).str.strip()
    cleaned = cleaned[cleaned != ""]
    if cleaned.empty:
        return ""
    modes = cleaned.mode()
    if not modes.empty:
        # Prefer the longest value among equally common variants; this tends to
        # keep the more descriptive school/barangay name.
        return max((str(v) for v in modes.tolist()), key=len)
    return str(cleaned.iloc[0])


def consolidate_sbi_targets(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Clean repeated DepEd school rows without double-counting targets.

    The DepEd ``Target by School`` export can contain repeated BEIS School IDs.
    A repeated ID is not automatically treated as corruption.  We first remove
    exact duplicate rows, then collapse remaining rows for the same School ID.

    Numeric target columns use the *maximum* value per column rather than sum.
    This is deliberate: these columns represent school-level target totals, so
    summing duplicate export rows can silently inflate coverage denominators.
    Column-wise max also safely combines rows where one duplicate has a value
    for one grade and another row has that column blank/zero.

    One School ID appearing in more than one municipality is considered a real
    conflict and stops the sync, because that cannot be reconciled safely.
    """
    if df.empty:
        return df.copy(), {
            "input_rows": 0, "output_rows": 0, "exact_duplicates_removed": 0,
            "duplicate_ids_consolidated": 0, "name_variant_ids": 0,
            "barangay_variant_ids": 0,
        }

    out = df.copy()

    text_cols = ["municipality", "barangay", "school_id", "school_name"]
    for col in text_cols:
        if col in out.columns:
            out[col] = out[col].fillna("").astype(str).str.strip()

    # Google Sheets often exposes integer IDs as strings ending in '.0'.
    if "school_id" in out.columns:
        out["school_id"] = out["school_id"].str.replace(r"\.0$", "", regex=True)
        out.loc[out["school_id"].str.lower().isin(["nan", "none", "null"]), "school_id"] = ""

    numeric = ["g1_male", "g1_female", "g4_female", "g7_male", "g7_female"]
    for col in numeric:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).clip(lower=0).astype(int)

    # Recalculate derived totals; do not trust duplicate source totals.
    out["g1_total"] = out["g1_male"] + out["g1_female"]
    out["g7_total"] = out["g7_male"] + out["g7_female"]

    input_rows = len(out)
    before_exact = len(out)
    out = out.drop_duplicates().copy()
    exact_removed = before_exact - len(out)

    duplicate_mask = out["school_id"].ne("") & out["school_id"].duplicated(keep=False)
    duplicate_ids = out.loc[duplicate_mask, "school_id"].unique().tolist()

    if duplicate_ids:
        dup_df = out[out["school_id"].isin(duplicate_ids)].copy()

        # A BEIS School ID crossing municipalities is unsafe to auto-resolve.
        muni_counts = dup_df.groupby("school_id")["municipality"].apply(
            lambda s: s[s.astype(str).str.strip() != ""].str.casefold().nunique()
        )
        conflict_ids = muni_counts[muni_counts > 1].index.tolist()
        if conflict_ids:
            sample = ", ".join(map(str, conflict_ids[:10]))
            suffix = " ..." if len(conflict_ids) > 10 else ""
            raise ValueError(
                f"{len(conflict_ids)} School ID(s) are assigned to more than one municipality "
                f"(examples: {sample}{suffix}). These need source-data correction before sync."
            )

        def variant_count(col: str) -> int:
            if col not in dup_df.columns:
                return 0
            counts = dup_df.groupby("school_id")[col].apply(
                lambda s: s[s.astype(str).str.strip() != ""].str.casefold().nunique()
            )
            return int((counts > 1).sum())

        name_variant_ids = variant_count("school_name")
        barangay_variant_ids = variant_count("barangay")

        # Collapse to one target row per BEIS School ID.  Text uses the most
        # common value; target columns use max to prevent duplicate inflation.
        grouped = out.groupby("school_id", as_index=False, sort=False).agg({
            "municipality": _most_common_nonblank,
            "barangay": _most_common_nonblank,
            "school_name": _most_common_nonblank,
            "g1_male": "max",
            "g1_female": "max",
            "g4_female": "max",
            "g7_male": "max",
            "g7_female": "max",
        })
        grouped["g1_total"] = grouped["g1_male"] + grouped["g1_female"]
        grouped["g7_total"] = grouped["g7_male"] + grouped["g7_female"]
        out = grouped
    else:
        name_variant_ids = 0
        barangay_variant_ids = 0

    report = {
        "input_rows": int(input_rows),
        "output_rows": int(len(out)),
        "exact_duplicates_removed": int(exact_removed),
        "duplicate_ids_consolidated": int(len(duplicate_ids)),
        "name_variant_ids": int(name_variant_ids),
        "barangay_variant_ids": int(barangay_variant_ids),
    }
    return out.reset_index(drop=True), report


def validate_sbi_targets(df: pd.DataFrame) -> tuple[bool, str]:
    required = {
        "municipality", "barangay", "school_id", "school_name",
        "g1_male", "g1_female", "g4_female", "g7_male", "g7_female",
        "g1_total", "g7_total",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        return False, f"Missing required columns: {', '.join(missing)}"
    if df.empty:
        return False, "The prepared SBI target dataset is empty."
    if df["school_id"].isna().any() or (df["school_id"].astype(str).str.strip() == "").any():
        return False, "One or more records have a blank School ID."
    if df["school_id"].astype(str).duplicated().any():
        dupes = int(df["school_id"].astype(str).duplicated().sum())
        return False, f"Found {dupes} duplicate School ID record(s)."

    numeric = ["g1_male", "g1_female", "g4_female", "g7_male", "g7_female", "g1_total", "g7_total"]
    if (df[numeric].fillna(0) < 0).any().any():
        return False, "Negative target values were detected."
    return True, "OK"


def replace_table_with_rollback(supabase, table: str, records: list[dict], *, id_column: str = "id", batch_size: int = 500) -> int:
    """Best-effort replacement with rollback.

    Prefer the `replace_sbi_targets` RPC installed by supabase_migration.sql.
    This fallback takes an in-memory backup before replacement and restores it
    if insertion fails. It is not as strong as a server-side transaction but
    prevents the common delete-success/insert-failure data-loss scenario.
    """
    if not records:
        raise ValueError("Refusing to replace a table with zero records")

    # First try the server-side atomic RPC. It may not exist until the migration
    # is installed, so fall back cleanly.
    if table == "sbi_targets":
        try:
            result = supabase.rpc("replace_sbi_targets", {"payload": records}).execute()
            return len(records)
        except Exception:
            logger.info("replace_sbi_targets RPC unavailable; using rollback fallback", exc_info=True)

    backup_resp = supabase.table(table).select("*").execute()
    backup = list(backup_resp.data or [])

    def _delete_all() -> None:
        supabase.table(table).delete().neq(id_column, 0).execute()

    def _insert_batches(rows: Iterable[dict]) -> None:
        rows = list(rows)
        for start in range(0, len(rows), batch_size):
            supabase.table(table).insert(rows[start:start + batch_size]).execute()

    try:
        _delete_all()
        _insert_batches(records)
        return len(records)
    except Exception:
        logger.exception("Replacement of %s failed; attempting rollback", table)
        try:
            _delete_all()
            # Remove generated identity ids before restoration when present.
            clean_backup = []
            for row in backup:
                row = dict(row)
                row.pop(id_column, None)
                clean_backup.append(row)
            if clean_backup:
                _insert_batches(clean_backup)
        except Exception:
            logger.exception("Rollback of %s also failed", table)
        raise
