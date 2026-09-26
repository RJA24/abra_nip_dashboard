from __future__ import annotations

from datetime import datetime
from io import BytesIO
import json
import re
import zipfile

import pandas as pd
import pytz
import streamlit as st

from core.config import ABRA_MUNIS
from core.data import (
    SBI_SETTINGS_TABLE,
    VACCTRACK_GOOGLE_FALLBACK_KEY,
    fetch_sbi_vacctrack_source_info,
    vacctrack_google_fallback_enabled,
)


MANILA_TZ = pytz.timezone("Asia/Manila")
APP_VERSION = "v5.20.1"
FEEDBACK_TABLE = "sbi_user_feedback"


def _fetch_all(build_query, page_size: int = 1000) -> list[dict]:
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


def _table_exists(supabase, table: str, column: str = "id") -> tuple[bool, str]:
    try:
        supabase.table(table).select(column).limit(1).execute()
        return True, "Ready"
    except Exception as exc:
        text = str(exc).strip()
        return False, text[:160] if text else "Unavailable"


def _format_date(value) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%b %d, %Y")


def _format_datetime(value) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")


def render_system_health(supabase, audit_callback=None) -> None:
    st.markdown("### System Health")
    st.caption(f"Dashboard version: {APP_VERSION}")

    checks = [
        ("Accounts", "user_accounts", "username"),
        ("Access Logs", "access_logs", "id"),
        ("SBI Targets", "sbi_targets", "school_id"),
        ("RHU Accomplishments", "sbi_rhu_accomplishments", "id"),
        ("Learner Records", "sbi_linelist_records", "id"),
        ("Line-List Imports", "sbi_linelist_imports", "id"),
        ("Line-List Audit", "sbi_linelist_audit", "id"),
        ("VaccTrack Imports", "sbi_vacctrack_imports", "id"),
        ("VaccTrack Rows", "sbi_vacctrack_rows", "id"),
        ("RHU Feedback", FEEDBACK_TABLE, "id"),
        ("SBI Settings", SBI_SETTINGS_TABLE, "setting_key"),
    ]

    status_rows = []
    for label, table, column in checks:
        ready, detail = _table_exists(supabase, table, column)
        status_rows.append(
            {
                "Component": label,
                "Table": table,
                "Status": "Ready" if ready else "Needs attention",
                "Detail": "Available" if ready else detail,
            }
        )

    ready_count = sum(row["Status"] == "Ready" for row in status_rows)
    c1, c2, c3 = st.columns(3)
    c1.metric("Components Ready", f"{ready_count}/{len(status_rows)}")
    c2.metric("Dashboard Version", APP_VERSION)
    c3.metric("Server Time", datetime.now(MANILA_TZ).strftime("%I:%M %p"))

    st.dataframe(pd.DataFrame(status_rows), width="stretch", hide_index=True)

    st.markdown("#### VaccTrack Data Sources")
    fallback_enabled = vacctrack_google_fallback_enabled()
    current_label = "On" if fallback_enabled else "Off"
    st.caption(
        f"Google Sheet fallback: {current_label}. When it is on, Google Sheets is used only for a grade that has no direct VaccTrack upload."
    )

    settings_ready, _ = _table_exists(supabase, SBI_SETTINGS_TABLE, "setting_key")
    if settings_ready:
        desired = st.toggle(
            "Enable Google Sheet fallback when a direct VaccTrack upload is missing",
            value=fallback_enabled,
            key="ops_vacctrack_google_fallback",
        )
        if desired != fallback_enabled:
            actor = st.session_state.get("username") or st.session_state.get("user_name") or "System Admin"
            supabase.table(SBI_SETTINGS_TABLE).upsert(
                {
                    "setting_key": VACCTRACK_GOOGLE_FALLBACK_KEY,
                    "setting_value": "true" if desired else "false",
                    "updated_at": datetime.now(MANILA_TZ).isoformat(),
                    "updated_by": actor,
                },
                on_conflict="setting_key",
            ).execute()
            if audit_callback:
                audit_callback(supabase, f"VaccTrack Google Sheet fallback {'enabled' if desired else 'disabled'}")
            st.cache_data.clear()
            st.toast(f"Google Sheet fallback turned {'on' if desired else 'off'}.")
            st.rerun()
    else:
        st.info("Run supabase/010_sbi_vacctrack_fallback_setting.sql to manage the Google Sheet fallback from the dashboard.")

    source_info = fetch_sbi_vacctrack_source_info()
    source_rows = []
    for grade in ("G1", "G4", "G7"):
        info = source_info.get(grade, {}) or {}
        source_rows.append(
            {
                "Grade": grade,
                "Source": info.get("source") or "Unavailable",
                "Data Through": _format_date(info.get("report_date_max")) or "Not available",
                "Rows": info.get("abra_row_count") if info.get("abra_row_count") is not None else info.get("row_count"),
                "Imported": _format_datetime(info.get("imported_at")) or "",
            }
        )
    st.dataframe(pd.DataFrame(source_rows), width="stretch", hide_index=True)

    if st.button("Refresh Health Check", width="stretch", key="ops_refresh_health"):
        st.cache_data.clear()
        st.rerun()


def _login_username(action: object) -> str:
    match = re.search(r"(?:^|\|)\s*username=([^|]+)", str(action or ""), flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def render_rollout_status(supabase) -> None:
    st.markdown("### RHU Rollout Status")
    st.caption("Use this during rollout to see who has signed in, changed the temporary password, and started uploading SBI records.")

    accounts = pd.DataFrame(
        _fetch_all(
            lambda: supabase.table("user_accounts")
            .select("username,name,role,assigned_muni,account_status,must_change_password")
            .eq("role", "RHU Encoder")
            .order("assigned_muni"),
            page_size=500,
        )
    )
    if accounts.empty:
        st.info("No RHU Encoder accounts are configured yet.")
        return

    try:
        logs = pd.DataFrame(
            _fetch_all(
                lambda: supabase.table("access_logs")
                .select("timestamp,name,role,action")
                .eq("role", "RHU Encoder")
                .order("id", desc=True),
                page_size=1000,
            )
        )
    except Exception:
        logs = pd.DataFrame()

    try:
        imports = pd.DataFrame(
            _fetch_all(
                lambda: supabase.table("sbi_linelist_imports")
                .select("municipality,uploaded_by,uploaded_at,activity_date_min,activity_date_max,rows_uploaded")
                .order("uploaded_at", desc=True),
                page_size=500,
            )
        )
    except Exception:
        imports = pd.DataFrame()

    last_login: dict[str, object] = {}
    if not logs.empty:
        logs["_username"] = logs.get("action", pd.Series(dtype=object)).map(_login_username)
        logs = logs[logs["_username"].ne("")].copy()
        if not logs.empty:
            logs["_parsed"] = pd.to_datetime(logs["timestamp"], errors="coerce")
            logs = logs.sort_values("_parsed", ascending=False)
            last_login = logs.drop_duplicates("_username").set_index("_username")["timestamp"].to_dict()

    import_summary: dict[str, dict] = {}
    if not imports.empty:
        imports["uploaded_by"] = imports["uploaded_by"].fillna("").astype(str).str.strip()
        imports["rows_uploaded"] = pd.to_numeric(imports.get("rows_uploaded"), errors="coerce").fillna(0).astype(int)
        for username, group in imports[imports["uploaded_by"].ne("")].groupby("uploaded_by"):
            parsed_upload = pd.to_datetime(group["uploaded_at"], errors="coerce")
            activity = pd.to_datetime(group["activity_date_max"], errors="coerce")
            import_summary[username] = {
                "First Upload": group.loc[parsed_upload.idxmin(), "uploaded_at"] if parsed_upload.notna().any() else "",
                "Latest Upload": group.loc[parsed_upload.idxmax(), "uploaded_at"] if parsed_upload.notna().any() else "",
                "Latest Activity": activity.max(),
                "Batches": int(len(group)),
                "Rows Uploaded": int(group["rows_uploaded"].sum()),
            }

    rows = []
    for _, account in accounts.iterrows():
        username = str(account.get("username") or "").strip()
        summary = import_summary.get(username, {})
        login_value = last_login.get(username)
        rows.append(
            {
                "Municipality": account.get("assigned_muni") or "",
                "Account": username,
                "Status": account.get("account_status") or "",
                "Last Login": _format_datetime(login_value) or "Not yet",
                "Password Changed": "No" if (
                    account.get("must_change_password") is True
                    or str(account.get("must_change_password") or "").strip().lower() in {"1", "true", "yes", "y"}
                ) else "Yes",
                "First Upload": _format_datetime(summary.get("First Upload")) or "Not yet",
                "Latest Activity": _format_date(summary.get("Latest Activity")) or "",
                "Batches": summary.get("Batches", 0),
                "Rows Uploaded": summary.get("Rows Uploaded", 0),
            }
        )

    rollout = pd.DataFrame(rows).sort_values(["Municipality", "Account"])
    logged_in = int((rollout["Last Login"] != "Not yet").sum())
    changed = int((rollout["Password Changed"] == "Yes").sum())
    started = int((rollout["First Upload"] != "Not yet").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("RHU Accounts", len(rollout))
    c2.metric("Logged In", logged_in)
    c3.metric("Password Changed", changed)
    c4.metric("Started Uploading", started)

    st.dataframe(rollout, width="stretch", hide_index=True)
    st.download_button(
        "Download Rollout Status (CSV)",
        data=rollout.to_csv(index=False).encode("utf-8-sig"),
        file_name="Abra_NIP_RHU_Rollout_Status.csv",
        mime="text/csv",
        key="ops_rollout_download",
    )


def render_feedback_inbox(supabase, audit_callback=None) -> None:
    st.markdown("### RHU Feedback")
    ready, _ = _table_exists(supabase, FEEDBACK_TABLE)
    if not ready:
        st.info("Run supabase/009_sbi_operations.sql to enable the built-in feedback inbox.")
        return

    rows = _fetch_all(
        lambda: supabase.table(FEEDBACK_TABLE)
        .select("id,submitted_at,username,role,municipality,category,page,app_version,message,status,admin_note,resolved_at,resolved_by")
        .order("submitted_at", desc=True),
        page_size=500,
    )
    feedback = pd.DataFrame(rows)
    if feedback.empty:
        st.info("No feedback has been submitted yet.")
        return

    f1, f2 = st.columns(2)
    with f1:
        status_filter = st.selectbox("Status", ["All", "Open", "In Review", "Resolved"], key="ops_feedback_status")
    with f2:
        muni_options = ["All"] + [m for m in ABRA_MUNIS if m in set(feedback["municipality"].dropna().astype(str))]
        muni_filter = st.selectbox("Municipality", muni_options, key="ops_feedback_muni")

    view = feedback.copy()
    if status_filter != "All":
        view = view[view["status"].eq(status_filter)]
    if muni_filter != "All":
        view = view[view["municipality"].eq(muni_filter)]

    table = view[["id", "submitted_at", "municipality", "username", "category", "page", "status", "message"]].copy()
    table["submitted_at"] = table["submitted_at"].map(_format_datetime)
    table.columns = ["ID", "Submitted", "Municipality", "Username", "Category", "Page", "Status", "Message"]
    st.dataframe(table, width="stretch", hide_index=True)

    if view.empty:
        return

    ids = view["id"].astype(int).tolist()
    selected_id = st.selectbox("Open feedback item", ids, key="ops_feedback_item")
    selected = feedback[feedback["id"].astype(int).eq(int(selected_id))].iloc[0]
    st.markdown(f"**{selected.get('category', '')} — {selected.get('municipality', '')}**")
    st.write(str(selected.get("message") or ""))

    with st.form("ops_feedback_update_form"):
        current_status = str(selected.get("status") or "Open")
        options = ["Open", "In Review", "Resolved"]
        status = st.selectbox("Status", options, index=options.index(current_status) if current_status in options else 0)
        note = st.text_area("Admin Note", value=str(selected.get("admin_note") or ""), height=100)
        save = st.form_submit_button("Save Feedback Update", type="primary")

    if save:
        update = {"status": status, "admin_note": note.strip() or None}
        if status == "Resolved":
            update["resolved_at"] = datetime.now(MANILA_TZ).isoformat()
            update["resolved_by"] = st.session_state.get("username") or st.session_state.get("user_name")
        else:
            update["resolved_at"] = None
            update["resolved_by"] = None
        supabase.table(FEEDBACK_TABLE).update(update).eq("id", int(selected_id)).execute()
        if audit_callback:
            audit_callback(supabase, f"Feedback updated | id={selected_id} | status={status}")
        st.toast("Feedback updated.")
        st.rerun()


def _csv_bytes(rows: list[dict]) -> bytes:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return b""
    for column in frame.columns:
        if frame[column].map(lambda value: isinstance(value, (dict, list))).any():
            frame[column] = frame[column].map(
                lambda value: json.dumps(value, ensure_ascii=False, default=str)
                if isinstance(value, (dict, list))
                else value
            )
    return frame.to_csv(index=False).encode("utf-8-sig")


def _build_backup(supabase, include_vacctrack_rows: bool) -> tuple[bytes, list[dict]]:
    tables = [
        "user_accounts",
        "access_logs",
        "sbi_targets",
        "sbi_rhu_accomplishments",
        "sbi_linelist_imports",
        "sbi_linelist_records",
        "sbi_linelist_audit",
        "sbi_vacctrack_imports",
        SBI_SETTINGS_TABLE,
        FEEDBACK_TABLE,
    ]
    if include_vacctrack_rows:
        tables.append("sbi_vacctrack_rows")

    output = BytesIO()
    report = []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for table in tables:
            try:
                if table == "user_accounts":
                    rows = _fetch_all(
                        lambda: supabase.table("user_accounts").select(
                            "username,name,role,assigned_muni,account_status,failed_attempts,must_change_password"
                        ),
                        page_size=1000,
                    )
                else:
                    rows = _fetch_all(lambda table=table: supabase.table(table).select("*"), page_size=1000)
                archive.writestr(f"{table}.csv", _csv_bytes(rows))
                report.append({"Table": table, "Rows": len(rows), "Status": "Included"})
            except Exception as exc:
                report.append({"Table": table, "Rows": 0, "Status": f"Skipped: {str(exc)[:80]}"})

        archive.writestr(
            "backup_info.txt",
            (
                f"Abra NIP Dashboard backup\n"
                f"Version: {APP_VERSION}\n"
                f"Created: {datetime.now(MANILA_TZ).isoformat()}\n"
                f"VaccTrack raw rows included: {'Yes' if include_vacctrack_rows else 'No'}\n"
            ).encode("utf-8"),
        )
    return output.getvalue(), report


def render_backup(supabase, audit_callback=None) -> None:
    st.markdown("### Data Backup / Export")
    st.caption("Creates a ZIP of CSV files for review or safekeeping. It does not change any database records.")

    include_rows = st.checkbox(
        "Include raw VaccTrack rows (larger backup)",
        value=False,
        key="ops_backup_vacctrack_rows",
    )

    if st.button("Prepare Backup", type="primary", width="stretch", key="ops_prepare_backup"):
        with st.spinner("Preparing backup files..."):
            payload, report = _build_backup(supabase, include_rows)
        st.session_state["ops_backup_payload"] = payload
        st.session_state["ops_backup_report"] = report
        if audit_callback:
            audit_callback(supabase, f"Backup prepared | raw_vacctrack_rows={'yes' if include_rows else 'no'}")

    payload = st.session_state.get("ops_backup_payload")
    report = st.session_state.get("ops_backup_report")
    if report:
        st.dataframe(pd.DataFrame(report), width="stretch", hide_index=True)
    if payload:
        stamp = datetime.now(MANILA_TZ).strftime("%Y%m%d_%H%M")
        st.download_button(
            "Download Backup ZIP",
            data=payload,
            file_name=f"Abra_NIP_Backup_{stamp}.zip",
            mime="application/zip",
            width="stretch",
            key="ops_download_backup",
        )


def render_operations(supabase, audit_callback=None) -> None:
    health_tab, rollout_tab, feedback_tab, backup_tab = st.tabs(
        ["System Health", "RHU Rollout", "Feedback", "Backup"]
    )
    with health_tab:
        render_system_health(supabase, audit_callback=audit_callback)
    with rollout_tab:
        render_rollout_status(supabase)
    with feedback_tab:
        render_feedback_inbox(supabase, audit_callback=audit_callback)
    with backup_tab:
        render_backup(supabase, audit_callback=audit_callback)
