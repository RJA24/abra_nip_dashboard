"""Admin-only cleanup and import-management tools for SBI operational data.

This module intentionally keeps deletion narrow and explicit. Line-list batches are
rolled back from their audit trail; official VaccTrack snapshots are deleted by import
ID (their rows cascade); manual fallback tracker rows can be removed separately.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import pandas as pd
import pytz
import streamlit as st

from core.config import ABRA_MUNIS
from core.map_labels import canonical_municipality_name
from programs.sbi.linelist import rebuild_aggregates_after_admin_change


MANILA_TZ = pytz.timezone("Asia/Manila")
LINE_IMPORT_TABLE = "sbi_linelist_imports"
LINE_RECORD_TABLE = "sbi_linelist_records"
LINE_AUDIT_TABLE = "sbi_linelist_audit"
RHU_AGG_TABLE = "sbi_rhu_accomplishments"
VACCTRACK_IMPORT_TABLE = "sbi_vacctrack_imports"
REGIONAL_SESSION_TABLE = "sbi_regional_sessions"


def _now_iso() -> str:
    return datetime.now(MANILA_TZ).isoformat()


def _to_date(value):  # noqa: ANN001
    parsed = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(parsed) else parsed.date()


def _to_dt(value):  # noqa: ANN001
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(parsed) else parsed.to_pydatetime()


def _fetch_all(build_query, page_size: int = 1000) -> list[dict]:
    """Page a Supabase query while rebuilding the query object each iteration."""
    rows: list[dict] = []
    offset = 0
    while True:
        response = build_query().range(offset, offset + page_size - 1).execute()
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def _line_imports(supabase) -> pd.DataFrame:  # noqa: ANN001
    rows = _fetch_all(
        lambda: supabase.table(LINE_IMPORT_TABLE)
        .select(
            "id,municipality,file_name,file_sha256,uploaded_by,uploaded_at,"
            "activity_date_min,activity_date_max,rows_uploaded,rows_added,rows_modified,"
            "rows_removed,rows_unchanged,notes"
        )
        .order("uploaded_at", desc=True),
        page_size=500,
    )
    return pd.DataFrame(rows)


def _vacctrack_imports(supabase) -> pd.DataFrame:  # noqa: ANN001
    rows = _fetch_all(
        lambda: supabase.table(VACCTRACK_IMPORT_TABLE)
        .select(
            "id,grade_level,filename,source_format,row_count,abra_row_count,report_date_min,"
            "report_date_max,imported_by,imported_at,completed_at,status,error_message"
        )
        .order("imported_at", desc=True),
        page_size=500,
    )
    return pd.DataFrame(rows)


def _manual_entries(supabase) -> pd.DataFrame:  # noqa: ANN001
    try:
        rows = _fetch_all(
            lambda: supabase.table(RHU_AGG_TABLE)
            .select(
                "id,municipality,school_id,school_name,activity_date,grade_level,"
                "mr_male,mr_female,td_male,td_female,hpv_dose1,hpv_dose2,"
                "source_type,updated_by,updated_at"
            )
            .eq("source_type", "manual")
            .order("activity_date", desc=True),
            page_size=500,
        )
    except Exception:
        # Older deployments can be missing source_type; do not expose a destructive
        # fallback that could accidentally delete line-list-derived rows.
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _regional_sessions(supabase) -> pd.DataFrame:  # noqa: ANN001
    try:
        rows = _fetch_all(
            lambda: supabase.table(REGIONAL_SESSION_TABLE)
            .select("id,municipality,school_id,school_name,activity_date,g5_female_enrolled,updated_by,updated_at")
            .order("activity_date", desc=True),
            page_size=500,
        )
    except Exception:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _overlap(a_min, a_max, b_min, b_max) -> bool:  # noqa: ANN001
    a0, a1 = _to_date(a_min), _to_date(a_max)
    b0, b1 = _to_date(b_min), _to_date(b_max)
    if not all([a0, a1, b0, b1]):
        return True  # unknown ranges are treated conservatively
    return a0 <= b1 and b0 <= a1


def _newer_overlapping_line_batches(imports: pd.DataFrame, target: dict) -> pd.DataFrame:
    if imports.empty:
        return pd.DataFrame()
    target_time = _to_dt(target.get("uploaded_at"))
    if target_time is None:
        return pd.DataFrame([{"id": "unknown"}])
    muni = canonical_municipality_name(target.get("municipality") or "")
    candidates = imports.copy()
    candidates = candidates[candidates["id"].astype(str).ne(str(target.get("id")))]
    candidates = candidates[candidates["municipality"].fillna("").map(canonical_municipality_name).eq(muni)]
    candidates["_uploaded_at"] = pd.to_datetime(candidates["uploaded_at"], errors="coerce", utc=True)
    candidates = candidates[candidates["_uploaded_at"].gt(target_time)]
    if candidates.empty:
        return candidates
    mask = candidates.apply(
        lambda row: _overlap(
            target.get("activity_date_min"), target.get("activity_date_max"),
            row.get("activity_date_min"), row.get("activity_date_max"),
        ),
        axis=1,
    )
    return candidates.loc[mask].copy()


def _fetch_batch_audit(supabase, batch_id: int) -> pd.DataFrame:  # noqa: ANN001
    rows = _fetch_all(
        lambda: supabase.table(LINE_AUDIT_TABLE)
        .select("id,row_key,municipality,action,old_values,new_values,import_batch_id,changed_by,changed_at")
        .eq("import_batch_id", batch_id)
        .order("id"),
        page_size=500,
    )
    return pd.DataFrame(rows)


def _fetch_records_for_batch(supabase, batch_id: int) -> pd.DataFrame:  # noqa: ANN001
    rows = _fetch_all(
        lambda: supabase.table(LINE_RECORD_TABLE)
        .select("*")
        .eq("import_batch_id", batch_id),
        page_size=500,
    )
    return pd.DataFrame(rows)


def _prior_audit_states(supabase, row_keys: list[str], target_batch_id: int) -> dict[str, dict]:  # noqa: ANN001
    """Return the latest state immediately before the selected batch for each row key."""
    prior: dict[str, dict] = {}
    for start in range(0, len(row_keys), 100):
        keys = row_keys[start:start + 100]
        response = (
            supabase.table(LINE_AUDIT_TABLE)
            .select("id,row_key,action,old_values,new_values,import_batch_id,changed_at")
            .in_("row_key", keys)
            .order("id", desc=True)
            .execute()
        )
        rows = response.data or []
        for item in rows:
            key = str(item.get("row_key") or "")
            if not key or key in prior:
                continue
            batch = item.get("import_batch_id")
            if batch is not None and int(batch) == int(target_batch_id):
                continue
            action = str(item.get("action") or "").upper()
            if action == "REMOVE":
                state = dict(item.get("old_values") or {})
                state["is_active"] = False
            else:
                state = dict(item.get("new_values") or {})
            prior[key] = {
                "state": state or None,
                "batch_id": int(batch) if batch is not None else None,
                "audit_id": item.get("id"),
            }
    return prior


def _scope_from_state(state: dict | None) -> tuple[str, str, str] | None:
    if not state:
        return None
    activity_date = str(state.get("activity_date") or "").strip()
    school_id = str(state.get("school_id") or "").strip()
    grade = str(state.get("grade_level") or "").strip()
    if activity_date and school_id and grade:
        return (activity_date, school_id, grade)
    return None


def _rollback_line_batch(supabase, target: dict, imports: pd.DataFrame, username: str) -> dict:  # noqa: ANN001
    batch_id = int(target["id"])
    municipality = canonical_municipality_name(target.get("municipality") or "")

    newer_overlap = _newer_overlapping_line_batches(imports, target)
    if not newer_overlap.empty:
        ids = ", ".join(str(x) for x in newer_overlap["id"].tolist()[:8])
        raise RuntimeError(
            "A newer line-list batch overlaps this batch's activity-date range. "
            f"Delete the newer overlapping batch first (batch ID(s): {ids})."
        )

    audit = _fetch_batch_audit(supabase, batch_id)
    current = _fetch_records_for_batch(supabase, batch_id)

    audit_keys = audit["row_key"].dropna().astype(str).tolist() if not audit.empty else []
    current_keys = current["row_key"].dropna().astype(str).tolist() if not current.empty else []
    row_keys = sorted(set(audit_keys) | set(current_keys))
    prior = _prior_audit_states(supabase, row_keys, batch_id) if row_keys else {}

    # If a changed row exists but now belongs to another batch, a later revision touched it.
    # This is a second safety net beyond the date-range overlap check.
    if not audit.empty:
        for key in audit_keys:
            response = (
                supabase.table(LINE_RECORD_TABLE)
                .select("row_key,import_batch_id")
                .eq("row_key", key)
                .limit(1)
                .execute()
            )
            rows = response.data or []
            if rows:
                current_batch = rows[0].get("import_batch_id")
                if current_batch is not None and int(current_batch) != batch_id:
                    raise RuntimeError(
                        f"Learner row {key[:12]}… was touched by a later batch. Delete the later revision first."
                    )

    now = _now_iso()
    scopes: set[tuple[str, str, str]] = set()
    audit_by_key = {str(row.get("row_key")): row for _, row in audit.iterrows()} if not audit.empty else {}
    current_by_key = {str(row.get("row_key")): row for _, row in current.iterrows()} if not current.empty else {}

    for key in row_keys:
        audit_row = audit_by_key.get(key)
        current_row = current_by_key.get(key)
        prior_info = prior.get(key, {"state": None, "batch_id": None})
        previous_batch = prior_info.get("batch_id")

        if audit_row is not None:
            action = str(audit_row.get("action") or "").upper()
            old_values = audit_row.get("old_values") if isinstance(audit_row.get("old_values"), dict) else None
            new_values = audit_row.get("new_values") if isinstance(audit_row.get("new_values"), dict) else None
            for state in [old_values, new_values]:
                scope = _scope_from_state(state)
                if scope:
                    scopes.add(scope)

            if action == "ADD":
                previous_state = prior_info.get("state")
                if previous_state:
                    restore = dict(previous_state)
                    restore.update({"import_batch_id": previous_batch, "updated_by": username, "updated_at": now})
                    supabase.table(LINE_RECORD_TABLE).update(restore).eq("row_key", key).execute()
                else:
                    supabase.table(LINE_RECORD_TABLE).delete().eq("row_key", key).execute()
            elif action in {"MODIFY", "RESTORE", "REMOVE"}:
                restore = dict(old_values or {})
                if action == "REMOVE" and "is_active" not in restore:
                    restore["is_active"] = True
                restore.update({"import_batch_id": previous_batch, "updated_by": username, "updated_at": now})
                supabase.table(LINE_RECORD_TABLE).update(restore).eq("row_key", key).execute()
        elif current_row is not None:
            # This row was unchanged in the selected batch; only its batch pointer
            # was advanced. Put that pointer back without changing learner values.
            scope = _scope_from_state(dict(current_row))
            if scope:
                scopes.add(scope)
            supabase.table(LINE_RECORD_TABLE).update(
                {"import_batch_id": previous_batch, "updated_by": username, "updated_at": now}
            ).eq("row_key", key).execute()

    # Remove learner-level audit payloads for the deleted import so dummy/test
    # names do not linger in the audit table. The higher-level admin audit log
    # still records who performed the deletion and which batch was removed.
    supabase.table(LINE_AUDIT_TABLE).delete().eq("import_batch_id", batch_id).execute()
    supabase.table(LINE_IMPORT_TABLE).delete().eq("id", batch_id).execute()

    if scopes:
        rebuild_aggregates_after_admin_change(supabase, municipality, scopes, username)

    return {
        "batch_id": batch_id,
        "municipality": municipality,
        "scopes": len(scopes),
        "row_keys": len(row_keys),
    }


def _render_line_list_cleanup(supabase, audit_callback=None, read_only: bool = False) -> None:  # noqa: ANN001
    st.markdown("#### Learner Line-List Imports")
    imports = _line_imports(supabase)
    if imports.empty:
        st.write("No SBI line-list import batches were found.")
        return

    municipality_options = ["All Municipalities"] + [m for m in ABRA_MUNIS if m in set(imports["municipality"].fillna("").map(canonical_municipality_name))]
    selected_muni = st.selectbox("Municipality", municipality_options, key="cleanup_line_muni")
    view = imports.copy()
    if selected_muni != "All Municipalities":
        view = view[view["municipality"].fillna("").map(canonical_municipality_name).eq(selected_muni)].copy()

    display = view[[
        "id", "municipality", "activity_date_min", "activity_date_max", "rows_uploaded",
        "rows_added", "rows_modified", "rows_removed", "file_name", "uploaded_by", "uploaded_at",
    ]].copy()
    display.columns = [
        "Batch ID", "Municipality", "From", "Through", "Rows", "Added", "Modified", "Removed",
        "File", "Uploaded By", "Uploaded At",
    ]
    total_rows = int(pd.to_numeric(display["Rows"], errors="coerce").fillna(0).sum())
    total_changes = int(
        pd.to_numeric(display["Added"], errors="coerce").fillna(0).sum()
        + pd.to_numeric(display["Modified"], errors="coerce").fillna(0).sum()
        + pd.to_numeric(display["Removed"], errors="coerce").fillna(0).sum()
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("Batches", len(display))
    c2.metric("Rows Processed", f"{total_rows:,}")
    c3.metric("Changes Applied", f"{total_changes:,}")
    st.dataframe(display, width="stretch", hide_index=True)
    st.download_button(
        "Download Line-List Import History (CSV)",
        data=display.to_csv(index=False).encode("utf-8-sig"),
        file_name="Abra_NIP_LineList_Import_History.csv",
        mime="text/csv",
        key="cleanup_line_history_download",
    )

    choices = []
    by_label: dict[str, dict] = {}
    for _, row in view.iterrows():
        item = row.to_dict()
        label = (
            f"Batch #{int(item['id'])} — {item.get('municipality','')} — "
            f"{item.get('activity_date_min') or '?'} to {item.get('activity_date_max') or '?'} — "
            f"{item.get('file_name') or ''}"
        )
        choices.append(label)
        by_label[label] = item
    selected = st.selectbox("Select line-list batch to remove", choices, key="cleanup_line_batch")
    target = by_label[selected]

    newer = _newer_overlapping_line_batches(imports, target)
    if not newer.empty:
        st.warning(
            "This batch has a newer overlapping import for the same municipality/date range. "
            "Delete the newer overlapping batch first so revisions are rolled back in the correct order."
        )
        safe = False
    else:
        safe = True
        st.write(
            f"Removing Batch #{int(target['id'])} will roll back its learner changes, rebuild the affected RHU totals, "
            "and remove the batch's learner-level audit payloads."
        )

    confirm = st.checkbox(
        f"I confirm that Batch #{int(target['id'])} is dummy/incorrect data and should be permanently removed.",
        key=f"cleanup_line_confirm_{int(target['id'])}",
    )
    if st.button(
        "Delete Selected Line-List Batch",
        type="primary",
        width="stretch",
        disabled=(read_only or not (safe and confirm)),
        key=f"cleanup_line_delete_{int(target['id'])}",
    ) and not read_only:
        username = str(st.session_state.get("username") or st.session_state.get("user_name") or "System Admin")
        try:
            with st.spinner("Rolling back the selected line-list batch..."):
                result = _rollback_line_batch(supabase, target, imports, username)
            if audit_callback:
                audit_callback(
                    supabase,
                    "SBI line-list batch deleted | "
                    f"batch={result['batch_id']} | municipality={result['municipality']} | "
                    f"affected_scopes={result['scopes']} | learner_rows={result['row_keys']}",
                )
            st.cache_data.clear()
            st.session_state["cleanup_notice"] = f"Line-list Batch #{result['batch_id']} was removed and its affected totals were rebuilt."
            st.rerun()
        except Exception as exc:
            st.error(f"Unable to delete this line-list batch safely: {exc}")


def _render_manual_cleanup(supabase, audit_callback=None, read_only: bool = False) -> None:  # noqa: ANN001
    st.markdown("#### Manual Fallback Records")
    entries = _manual_entries(supabase)
    if entries.empty:
        st.write("No manual fallback RHU records were found (or this deployment predates source tracking).")
        return

    muni_options = ["All Municipalities"] + sorted(entries["municipality"].dropna().astype(str).unique().tolist())
    muni = st.selectbox("Manual-record municipality", muni_options, key="cleanup_manual_muni")
    view = entries.copy()
    if muni != "All Municipalities":
        view = view[view["municipality"].astype(str).eq(muni)].copy()

    dates = sorted(view["activity_date"].dropna().astype(str).unique().tolist(), reverse=True)
    date_choice = st.selectbox("Activity date", ["All Dates"] + dates, key="cleanup_manual_date")
    if date_choice != "All Dates":
        view = view[view["activity_date"].astype(str).eq(date_choice)].copy()

    if view.empty:
        st.write("No manual fallback records match the selected filters.")
        return

    show_cols = [
        "id", "municipality", "activity_date", "school_id", "school_name", "grade_level",
        "mr_male", "mr_female", "td_male", "td_female", "hpv_dose1", "hpv_dose2", "updated_by",
    ]
    display = view[[c for c in show_cols if c in view.columns]].copy()
    st.dataframe(display, width="stretch", hide_index=True)

    labels: list[str] = []
    label_to_id: dict[str, int] = {}
    for _, row in view.iterrows():
        record_id = int(row["id"])
        label = (
            f"#{record_id} — {row.get('activity_date','')} — {row.get('school_name','')} "
            f"[{row.get('school_id','')}] — {row.get('grade_level','')}"
        )
        labels.append(label)
        label_to_id[label] = record_id

    selected_labels = st.multiselect("Select manual record(s) to delete", labels, key="cleanup_manual_selected")
    selected_ids = [label_to_id[label] for label in selected_labels]
    confirm = st.checkbox(
        f"I confirm that the selected {len(selected_ids)} manual record(s) are dummy/incorrect data.",
        key="cleanup_manual_confirm",
        disabled=not selected_ids,
    )
    if st.button(
        "Delete Selected Manual Records",
        type="primary",
        width="stretch",
        disabled=(read_only or not (selected_ids and confirm)),
        key="cleanup_manual_delete",
    ) and not read_only:
        try:
            for start in range(0, len(selected_ids), 100):
                supabase.table(RHU_AGG_TABLE).delete().in_("id", selected_ids[start:start + 100]).eq("source_type", "manual").execute()
            if audit_callback:
                audit_callback(supabase, f"SBI manual fallback records deleted | ids={selected_ids}")
            st.cache_data.clear()
            st.session_state["cleanup_notice"] = f"Deleted {len(selected_ids)} manual fallback record(s)."
            st.rerun()
        except Exception as exc:
            st.error(f"Unable to delete the selected manual records: {exc}")



def _render_legacy_regional_cleanup(supabase, audit_callback=None, read_only: bool = False) -> None:  # noqa: ANN001
    sessions = _regional_sessions(supabase)
    if sessions.empty:
        return

    with st.expander("Legacy v5.17 Regional test/session records", expanded=False):
        st.write(
            "Step 3 Regional Reporting is hidden from RHU Encoders in v5.18. If you saved dummy Grade 5 enrollment "
            "or vaccine-utilization session details during the v5.17 dry run, you can remove those old session rows here."
        )
        st.dataframe(sessions, width="stretch", hide_index=True)
        labels = []
        label_to_id: dict[str, int] = {}
        for _, row in sessions.iterrows():
            record_id = int(row["id"])
            label = (
                f"#{record_id} — {row.get('municipality','')} — {row.get('activity_date','')} — "
                f"{row.get('school_name','')} [{row.get('school_id','')}]"
            )
            labels.append(label)
            label_to_id[label] = record_id
        selected = st.multiselect("Select legacy regional session row(s) to delete", labels, key="cleanup_legacy_regional_selected")
        ids = [label_to_id[label] for label in selected]
        confirm = st.checkbox(
            f"I confirm that the selected {len(ids)} legacy regional session row(s) are dummy/incorrect data.",
            key="cleanup_legacy_regional_confirm",
            disabled=not ids,
        )
        if st.button(
            "Delete Selected Legacy Regional Sessions",
            type="primary",
            width="stretch",
            disabled=(read_only or not (ids and confirm)),
            key="cleanup_legacy_regional_delete",
        ) and not read_only:
            try:
                for start in range(0, len(ids), 100):
                    supabase.table(REGIONAL_SESSION_TABLE).delete().in_("id", ids[start:start + 100]).execute()
                if audit_callback:
                    audit_callback(supabase, f"Legacy SBI regional session records deleted | ids={ids}")
                st.cache_data.clear()
                st.session_state["cleanup_notice"] = f"Deleted {len(ids)} legacy regional session record(s)."
                st.rerun()
            except Exception as exc:
                st.error(f"Unable to delete the selected legacy regional session records: {exc}")

def _render_vacctrack_cleanup(supabase, audit_callback=None, read_only: bool = False) -> None:  # noqa: ANN001
    st.markdown("#### VaccTrack Direct-Upload Snapshots")
    imports = _vacctrack_imports(supabase)
    if imports.empty:
        st.write("No direct VaccTrack snapshots were found.")
        return

    grade_filter = st.selectbox("VaccTrack grade", ["All", "G1", "G4", "G7"], key="cleanup_vt_grade")
    view = imports.copy()
    if grade_filter != "All":
        view = view[view["grade_level"].astype(str).eq(grade_filter)].copy()

    latest_ids: dict[str, int] = {}
    complete = imports[imports["status"].astype(str).eq("Complete")].copy()
    if not complete.empty:
        complete["_when"] = pd.to_datetime(complete["completed_at"].fillna(complete["imported_at"]), errors="coerce", utc=True)
        for grade, grp in complete.groupby("grade_level"):
            grp = grp.sort_values("_when", ascending=False)
            latest_ids[str(grade)] = int(grp.iloc[0]["id"])

    display = view[[
        "id", "grade_level", "filename", "source_format", "row_count", "abra_row_count",
        "report_date_max", "imported_by", "imported_at", "status",
    ]].copy()
    display["Current Official"] = display.apply(
        lambda r: "Yes" if latest_ids.get(str(r["grade_level"])) == int(r["id"]) and str(r["status"]) == "Complete" else "",
        axis=1,
    )
    display.columns = [
        "Import ID", "Grade", "Filename", "Format", "Rows", "Abra Rows", "Latest Report Date",
        "Imported By", "Imported At", "Status", "Current Official",
    ]
    st.dataframe(display, width="stretch", hide_index=True)

    choices: list[str] = []
    by_label: dict[str, dict] = {}
    for _, row in view.iterrows():
        item = row.to_dict()
        current = latest_ids.get(str(item.get("grade_level"))) == int(item["id"]) and str(item.get("status")) == "Complete"
        label = (
            f"Import #{int(item['id'])} — {item.get('grade_level','')} — "
            f"{item.get('report_date_max') or '?'} — {item.get('filename') or ''}"
            + (" — CURRENT" if current else "")
        )
        choices.append(label)
        by_label[label] = item
    selected = st.selectbox("Select VaccTrack snapshot to delete", choices, key="cleanup_vt_import")
    target = by_label[selected]
    target_id = int(target["id"])
    grade = str(target.get("grade_level") or "")
    is_current = latest_ids.get(grade) == target_id and str(target.get("status")) == "Complete"

    if is_current:
        older = complete[(complete["grade_level"].astype(str).eq(grade)) & (complete["id"].astype(int).ne(target_id))].copy()
        if not older.empty:
            older = older.sort_values("_when", ascending=False)
            next_row = older.iloc[0]
            st.warning(
                f"This is the current official {grade} direct snapshot. After deletion, the dashboard will fall back to "
                f"the previous direct snapshot through {next_row.get('report_date_max') or 'an earlier date'}."
            )
        else:
            st.warning(
                f"This is the current official {grade} direct snapshot. After deletion, {grade} will fall back to the existing Google Sheet source."
            )
    else:
        st.write("Deleting this snapshot will not change the current official direct snapshot for this grade.")

    confirm = st.checkbox(
        f"I confirm that VaccTrack Import #{target_id} is dummy/incorrect and should be permanently removed.",
        key=f"cleanup_vt_confirm_{target_id}",
    )
    if st.button(
        "Delete Selected VaccTrack Snapshot",
        type="primary",
        width="stretch",
        disabled=(read_only or not confirm),
        key=f"cleanup_vt_delete_{target_id}",
    ) and not read_only:
        try:
            # sbi_vacctrack_rows uses ON DELETE CASCADE.
            supabase.table(VACCTRACK_IMPORT_TABLE).delete().eq("id", target_id).execute()
            if audit_callback:
                audit_callback(
                    supabase,
                    "VaccTrack snapshot deleted | "
                    f"import={target_id} | grade={grade} | filename={target.get('filename')} | "
                    f"latest_report_date={target.get('report_date_max')}",
                )
            st.cache_data.clear()
            st.session_state["cleanup_notice"] = f"VaccTrack Import #{target_id} was deleted."
            st.rerun()
        except Exception as exc:
            st.error(f"Unable to delete this VaccTrack snapshot: {exc}")


def render_import_management(
    supabase,
    audit_callback: Callable[[object, str], None] | None = None,
    read_only: bool = False,
) -> None:  # noqa: ANN001
    st.markdown(
        '''<div style="display:flex;align-items:center;gap:0.55rem;margin:0.25rem 0 0.7rem 0;">
        <i class="fa-solid fa-box-archive" style="color:#0033A0;font-size:1.25rem;"></i>
        <div style="font-size:1.32rem;font-weight:750;color:#1e293b;">SBI Import Management</div>
        </div>''',
        unsafe_allow_html=True,
    )
    if read_only:
        st.info("QA Admin mode: import history and cleanup targets are visible, but all delete actions are disabled.")
    else:
        st.markdown(
            "Use this admin-only area to remove dummy or incorrect SBI imports. Deletions are permanent, "
            "so select the exact batch/record/snapshot and review the confirmation before deleting."
        )

    notice = st.session_state.pop("cleanup_notice", None)
    if notice:
        st.success(notice)

    line_tab, manual_tab, vt_tab = st.tabs([
        "Line-List Batches",
        "Manual Fallback Records",
        "VaccTrack Snapshots",
    ])
    with line_tab:
        _render_line_list_cleanup(supabase, audit_callback, read_only=read_only)
    with manual_tab:
        _render_manual_cleanup(supabase, audit_callback, read_only=read_only)
        _render_legacy_regional_cleanup(supabase, audit_callback, read_only=read_only)
    with vt_tab:
        _render_vacctrack_cleanup(supabase, audit_callback, read_only=read_only)
