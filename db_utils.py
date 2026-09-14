"""Database safety helpers for the Abra NIP dashboard."""
from __future__ import annotations

import logging
import time
from typing import Iterable

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
