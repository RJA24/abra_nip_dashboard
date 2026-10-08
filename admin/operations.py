from __future__ import annotations

from datetime import date, datetime
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
from programs.sbi.aggregate_workbook import (
    SUBMISSION_TABLE,
    TABLE_NAME as ACCOMPLISHMENT_TABLE,
    WORKBOOK_VERSION,
    fetch_submission_history,
    get_current_submission,
    reopen_current_submission,
    restore_submission,
)
from programs.sbi.campaign_control import CAMPAIGN_STATUSES, get_campaign_config, save_campaign_config
from programs.sbi.admin_monitoring import render_data_quality_center, render_reconciliation_monitor
from programs.sbi.production_readiness import render_all_rhu_workbook_package, render_production_readiness


MANILA_TZ = pytz.timezone("Asia/Manila")
APP_VERSION = "v5.23.0"
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


def render_system_health(supabase, audit_callback=None, read_only: bool = False) -> None:
    st.markdown("### System Health")
    st.caption(f"System version: {APP_VERSION}")

    operational_checks = [
        ("Accounts", "user_accounts", "username"),
        ("Access Logs", "access_logs", "id"),
        ("SBI Targets", "sbi_targets", "school_id"),
        ("RHU Accomplishments", "sbi_rhu_accomplishments", "id"),
        ("VaccTrack Imports", "sbi_vacctrack_imports", "id"),
        ("VaccTrack Rows", "sbi_vacctrack_rows", "id"),
        ("RHU Feedback", FEEDBACK_TABLE, "id"),
        ("SBI Settings", SBI_SETTINGS_TABLE, "setting_key"),
        ("SBI Workbook Submissions", SUBMISSION_TABLE, "id"),
        ("FLU Vaccination Entries", "flu_vaccination_entries", "id"),
    ]
    legacy_checks = [
        ("Legacy Learner Records", "sbi_linelist_records", "id"),
        ("Legacy Line-List Imports", "sbi_linelist_imports", "id"),
        ("Legacy Line-List Audit", "sbi_linelist_audit", "id"),
        ("Legacy Regional Sessions", "sbi_regional_sessions", "id"),
    ]

    status_rows = []
    for label, table, column in operational_checks:
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
    c1.metric("Operational Components", f"{ready_count}/{len(status_rows)}")
    c2.metric("System Version", APP_VERSION)
    c3.metric("Server Time", datetime.now(MANILA_TZ).strftime("%I:%M %p"))

    st.dataframe(pd.DataFrame(status_rows), width="stretch", hide_index=True)

    with st.expander("Legacy / archival SBI tables", expanded=False):
        st.caption(
            "These tables belong to retired learner-level and regional test workflows. They are kept only for historical cleanup or audit and do not affect current aggregate-workbook production readiness."
        )
        legacy_rows = []
        for label, table, column in legacy_checks:
            ready, detail = _table_exists(supabase, table, column)
            legacy_rows.append(
                {
                    "Component": label,
                    "Table": table,
                    "Status": "Available" if ready else "Not installed / unavailable",
                    "Detail": "Legacy data retained" if ready else detail,
                }
            )
        st.dataframe(pd.DataFrame(legacy_rows), width="stretch", hide_index=True)

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
            disabled=read_only,
        )
        if desired != fallback_enabled and not read_only:
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
    st.caption("Use this during rollout to see who has signed in, changed the temporary password, and started uploading SBI workbooks.")

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
        submissions = fetch_submission_history(supabase)
    except Exception:
        submissions = pd.DataFrame()

    last_login: dict[str, object] = {}
    if not logs.empty:
        logs["_username"] = logs.get("action", pd.Series(dtype=object)).map(_login_username)
        logs = logs[logs["_username"].ne("")].copy()
        if not logs.empty:
            logs["_parsed"] = pd.to_datetime(logs["timestamp"], errors="coerce")
            logs = logs.sort_values("_parsed", ascending=False)
            last_login = logs.drop_duplicates("_username").set_index("_username")["timestamp"].to_dict()

    submission_summary: dict[str, dict] = {}
    if not submissions.empty:
        submissions["_muni"] = submissions["municipality"].fillna("").astype(str).str.strip()
        for municipality, group in submissions[submissions["_muni"].ne("")].groupby("_muni"):
            parsed_upload = pd.to_datetime(group["uploaded_at"], errors="coerce")
            current_rows = group[group["is_current"].fillna(False).astype(bool)] if "is_current" in group.columns else pd.DataFrame()
            current = current_rows.iloc[0] if not current_rows.empty else group.iloc[parsed_upload.argmax()] if parsed_upload.notna().any() else group.iloc[0]
            submission_summary[municipality] = {
                "First Upload": group.loc[parsed_upload.idxmin(), "uploaded_at"] if parsed_upload.notna().any() else "",
                "Latest Upload": current.get("uploaded_at"),
                "Latest Activity": current.get("activity_date_max"),
                "Uploads": int(len(group)),
                "Current Rows": int(current.get("row_count") or 0),
                "Finalized": bool(current.get("is_finalized")),
            }

    rows = []
    for _, account in accounts.iterrows():
        username = str(account.get("username") or "").strip()
        municipality = str(account.get("assigned_muni") or "").strip()
        summary = submission_summary.get(municipality, {})
        login_value = last_login.get(username)
        rows.append(
            {
                "Municipality": municipality,
                "Account": username,
                "Status": account.get("account_status") or "",
                "Last Login": _format_datetime(login_value) or "Not yet",
                "Password Changed": "No" if (
                    account.get("must_change_password") is True
                    or str(account.get("must_change_password") or "").strip().lower() in {"1", "true", "yes", "y"}
                ) else "Yes",
                "First Workbook Upload": _format_datetime(summary.get("First Upload")) or "Not yet",
                "Latest Activity": _format_date(summary.get("Latest Activity")) or "",
                "Uploads": summary.get("Uploads", 0),
                "Current Rows": summary.get("Current Rows", 0),
                "Submission": "Finalized" if summary.get("Finalized") else ("Uploaded" if summary else "No Upload"),
            }
        )

    rollout = pd.DataFrame(rows).sort_values(["Municipality", "Account"])
    logged_in = int((rollout["Last Login"] != "Not yet").sum())
    changed = int((rollout["Password Changed"] == "Yes").sum())
    started = int((rollout["First Workbook Upload"] != "Not yet").sum())

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


def render_sbi_control(supabase, audit_callback=None, read_only: bool = False) -> None:
    st.markdown("### SBI Campaign Control")
    st.caption("Set the operational state once here instead of changing code during implementation.")

    settings_ready, _ = _table_exists(supabase, SBI_SETTINGS_TABLE, "setting_key")
    if not settings_ready:
        st.info("SBI settings are unavailable. Apply the existing settings migration before using campaign controls.")
        return

    config = get_campaign_config(supabase)
    status = str(config.get("status") or "Pre-Implementation")
    start_config = config.get("start_date")
    end_config = config.get("end_date")
    if start_config and end_config:
        range_label = f"{start_config:%b %d} – {end_config:%b %d, %Y}"
    else:
        range_label = "Not enforced"

    st.markdown(
        """
        <style>
        .sbi-control-card {
            min-height: 116px;
            padding: 16px 18px;
            border: 1px solid #dbe3ef;
            border-bottom: 5px solid #0033A0;
            border-radius: 10px;
            background: #ffffff;
            overflow: hidden;
        }
        .sbi-control-label {
            font-size: 0.82rem;
            font-weight: 600;
            color: #334155;
            margin-bottom: 8px;
        }
        .sbi-control-value {
            font-size: clamp(1.15rem, 2vw, 1.75rem);
            line-height: 1.12;
            font-weight: 750;
            color: #0033A0;
            overflow-wrap: anywhere;
            word-break: break-word;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    c1, c2, c3 = st.columns(3)
    for column, label, value in [
        (c1, "Campaign Status", status),
        (c2, "Workbook Version", WORKBOOK_VERSION),
        (c3, "Activity Date Range", range_label),
    ]:
        with column:
            st.markdown(
                f'<div class="sbi-control-card"><div class="sbi-control-label">{label}</div>'
                f'<div class="sbi-control-value">{value}</div></div>',
                unsafe_allow_html=True,
            )

    today = datetime.now(MANILA_TZ).date()
    if status == "Pre-Implementation":
        st.info("Testing and workbook uploads are allowed. Use this status while preparing RHUs and cleaning test data.")
    elif status == "Live":
        st.success("SBI is live. RHU workbook uploads and final submission are enabled.")
        if end_config and today > end_config:
            st.warning(
                f"The configured SBI activity end date was {end_config:%b %d, %Y}. "
                "If field activities are finished, change the campaign to Post-Activity Correction so RHUs can submit only corrections or late reports for activity dates within the official range."
            )
    elif status == "Post-Activity Correction":
        st.info(
            "Field implementation is finished, but RHUs may still upload corrected or late workbooks. "
            "Activity dates must remain within the configured SBI activity date range. Final submission remains available."
        )
    else:
        st.warning("SBI is closed. RHUs can view their data and reconciliation, but workbook uploads are blocked.")

    prelaunch_summary = pd.DataFrame()
    if status == "Pre-Implementation":
        try:
            workbook_rows = _fetch_all(
                lambda: supabase.table(ACCOMPLISHMENT_TABLE)
                .select("municipality")
                .eq("source_type", "workbook"),
                page_size=1000,
            )
        except Exception:
            workbook_rows = []
        try:
            submission_rows = _fetch_all(
                lambda: supabase.table(SUBMISSION_TABLE).select("municipality"),
                page_size=1000,
            )
        except Exception:
            submission_rows = []

        accomplishment_counts: dict[str, int] = {}
        for row in workbook_rows:
            municipality = str(row.get("municipality") or "").strip()
            if municipality:
                accomplishment_counts[municipality] = accomplishment_counts.get(municipality, 0) + 1

        history_counts: dict[str, int] = {}
        for row in submission_rows:
            municipality = str(row.get("municipality") or "").strip()
            if municipality:
                history_counts[municipality] = history_counts.get(municipality, 0) + 1

        municipalities = sorted(set(accomplishment_counts) | set(history_counts))
        if municipalities:
            prelaunch_summary = pd.DataFrame(
                [
                    {
                        "Municipality": municipality,
                        "Workbook Rows": accomplishment_counts.get(municipality, 0),
                        "Upload History": history_counts.get(municipality, 0),
                    }
                    for municipality in municipalities
                ]
            )
            st.warning(
                f"Pre-launch check: existing workbook data was found for {len(municipalities)} RHU(s). "
                "Clear test data under RHU Submissions before switching to Live, unless these records are legitimate production data that should be kept."
            )
            st.dataframe(prelaunch_summary, width="stretch", hide_index=True)

    with st.form("ops_sbi_campaign_control"):
        status_index = CAMPAIGN_STATUSES.index(status) if status in CAMPAIGN_STATUSES else 0
        selected_status = st.selectbox("Campaign Status", list(CAMPAIGN_STATUSES), index=status_index, disabled=read_only)
        enforce_dates = st.checkbox(
            "Enforce official SBI activity date range on workbook uploads",
            value=bool(start_config or end_config),
            disabled=read_only,
        )
        start_default = start_config or datetime.now(MANILA_TZ).date()
        end_default = end_config or start_default
        d1, d2 = st.columns(2)
        with d1:
            start_date = st.date_input(
                "Activity Start Date",
                value=start_default,
                disabled=read_only,
                help="Used only when official activity date enforcement is enabled.",
            )
        with d2:
            end_date = st.date_input(
                "Activity End Date",
                value=end_default,
                disabled=read_only,
                help="Used only when official activity date enforcement is enabled.",
            )
        announcement = st.text_area(
            "RHU Announcement",
            value=str(config.get("announcement") or ""),
            height=100,
            placeholder="Example: VaccTrack data has been updated through Oct 18.",
            disabled=read_only,
        )

        keep_prelaunch_data = False
        if status == "Pre-Implementation" and not prelaunch_summary.empty:
            keep_prelaunch_data = st.checkbox(
                "If I switch to Live, I confirm that I reviewed the existing pre-implementation workbook data and it should be kept as legitimate production data.",
                value=False,
                disabled=read_only,
                help="Leave this unchecked if the existing workbook records are only test data. Clear those records first under RHU Submissions.",
            )

        save = st.form_submit_button("Save SBI Campaign Settings", type="primary", disabled=read_only)

    if save and not read_only:
        going_live = status == "Pre-Implementation" and selected_status == "Live"
        if going_live and not prelaunch_summary.empty and not keep_prelaunch_data:
            st.error(
                "Cannot switch to Live while pre-implementation workbook data still exists. "
                "Clear the test data under RHU Submissions, or explicitly confirm that the existing records are legitimate production data and should be kept."
            )
            return

        actor = st.session_state.get("username") or st.session_state.get("user_name") or "System Admin"
        try:
            save_campaign_config(
                supabase,
                status=selected_status,
                start_date=start_date if enforce_dates else None,
                end_date=end_date if enforce_dates else None,
                announcement=announcement,
                actor=str(actor),
            )
        except Exception as exc:
            st.error(f"Campaign settings could not be saved: {exc}")
            return
        if audit_callback:
            old_start = start_config.isoformat() if start_config else "off"
            old_end = end_config.isoformat() if end_config else "off"
            new_start_value = start_date if enforce_dates else None
            new_end_value = end_date if enforce_dates else None
            new_start = new_start_value.isoformat() if new_start_value else "off"
            new_end = new_end_value.isoformat() if new_end_value else "off"
            changes = []
            if selected_status != status:
                changes.append(f"status={status}->{selected_status}")
            if old_start != new_start or old_end != new_end:
                changes.append(f"activity_dates={old_start}..{old_end}->{new_start}..{new_end}")
            if str(announcement or "").strip() != str(config.get("announcement") or "").strip():
                changes.append("announcement=updated")
            if going_live and not prelaunch_summary.empty and keep_prelaunch_data:
                changes.append("prelaunch_data=reviewed_and_kept")
            audit_callback(
                supabase,
                "SBI campaign settings updated" + (" | " + " | ".join(changes) if changes else " | no_material_change"),
            )
        st.cache_data.clear()
        st.toast("SBI campaign settings saved.")
        st.rerun()


def render_submission_status(supabase, audit_callback=None, read_only: bool = False) -> None:
    st.markdown("### RHU Workbook Submissions")
    ready, _ = _table_exists(supabase, SUBMISSION_TABLE, "id")
    if not ready:
        st.info("Run supabase/012_sbi_workbook_submission_control.sql to enable workbook history, finalization, reopen, and restore controls.")
        return

    try:
        history = fetch_submission_history(supabase)
    except Exception as exc:
        st.error(f"Unable to load RHU workbook submissions: {exc}")
        return

    current_by_muni: dict[str, dict] = {}
    if not history.empty:
        current_rows = history[history["is_current"].fillna(False).astype(bool)].copy()
        for _, row in current_rows.iterrows():
            current_by_muni[str(row.get("municipality") or "")] = row.to_dict()

    rows = []
    for municipality in ABRA_MUNIS:
        current = current_by_muni.get(municipality, {})
        if current:
            state = "Finalized" if current.get("is_finalized") else "Uploaded"
        else:
            state = "No Upload"
        rows.append(
            {
                "Municipality": municipality,
                "Status": state,
                "Latest Upload": _format_datetime(current.get("uploaded_at")) or "",
                "Uploaded By": current.get("uploaded_by") or "",
                "Rows": int(current.get("row_count") or 0),
                "Activity From": _format_date(current.get("activity_date_min")) or "",
                "Activity Through": _format_date(current.get("activity_date_max")) or "",
                "Workbook Version": current.get("workbook_version") or "",
            }
        )
    status = pd.DataFrame(rows)
    uploaded = int(status["Status"].isin(["Uploaded", "Finalized"]).sum())
    finalized = int((status["Status"] == "Finalized").sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("RHUs With Upload", f"{uploaded}/{len(ABRA_MUNIS)}")
    c2.metric("Finalized", f"{finalized}/{len(ABRA_MUNIS)}")
    c3.metric("No Upload", int((status["Status"] == "No Upload").sum()))
    st.dataframe(status, width="stretch", hide_index=True)
    st.download_button(
        "Download RHU Submission Status (CSV)",
        status.to_csv(index=False).encode("utf-8-sig"),
        file_name="Abra_NIP_SBI_RHU_Submission_Status.csv",
        mime="text/csv",
        key="ops_submission_status_download",
    )

    st.divider()
    municipality = st.selectbox("Manage RHU submission", ABRA_MUNIS, key="ops_submission_muni")
    muni_history = history[history["municipality"].astype(str).eq(municipality)].copy() if not history.empty else pd.DataFrame()

    campaign_status = str(get_campaign_config(supabase).get("status") or "Pre-Implementation")
    try:
        workbook_rows = _fetch_all(
            lambda: supabase.table(ACCOMPLISHMENT_TABLE)
            .select("id")
            .eq("municipality", municipality)
            .eq("source_type", "workbook"),
            page_size=1000,
        )
    except Exception:
        workbook_rows = []

    with st.expander("Pre-Implementation Cleanup — Clear RHU Test Data", expanded=False):
        st.caption(
            "Use this only for test or dummy workbook uploads before SBI implementation. "
            "It deletes this RHU's workbook-derived accomplishment rows and workbook submission history."
        )
        st.info(
            "The RHU account, SBI targets, VaccTrack snapshots, and data from other municipalities are not deleted."
        )
        x1, x2 = st.columns(2)
        x1.metric("Workbook Accomplishment Rows", len(workbook_rows))
        x2.metric("Workbook History Entries", len(muni_history))

        if campaign_status != "Pre-Implementation":
            st.warning(
                f"Test-data cleanup is locked while the campaign status is {campaign_status}. "
                "Return the campaign to Pre-Implementation only if cleanup is genuinely required."
            )
        elif not workbook_rows and muni_history.empty:
            st.success(f"{municipality} has no workbook test data to clear.")
        else:
            confirm_clear = st.checkbox(
                f"I understand that clearing {municipality} will permanently remove its workbook test data and upload history.",
                key="ops_clear_test_data_confirm",
                disabled=read_only,
            )
            typed_muni = st.text_input(
                f"Type {municipality} to confirm",
                key="ops_clear_test_data_typed",
                disabled=read_only,
            )
            can_clear = (
                not read_only
                and confirm_clear
                and typed_muni.strip().casefold() == municipality.casefold()
            )
            if st.button(
                f"Clear {municipality} Test Workbook Data",
                type="primary",
                disabled=not can_clear,
                width="stretch",
                key="ops_clear_test_data",
            ):
                try:
                    (
                        supabase.table(SUBMISSION_TABLE)
                        .delete()
                        .eq("municipality", municipality)
                        .execute()
                    )
                    (
                        supabase.table(ACCOMPLISHMENT_TABLE)
                        .delete()
                        .eq("municipality", municipality)
                        .eq("source_type", "workbook")
                        .execute()
                    )
                except Exception as exc:
                    st.error(f"Test data could not be cleared: {exc}")
                    return

                if audit_callback:
                    audit_callback(
                        supabase,
                        f"SBI RHU workbook test data cleared | municipality={municipality} "
                        f"| accomplishment_rows={len(workbook_rows)} | submission_history={len(muni_history)}",
                    )
                st.cache_data.clear()
                st.success(
                    f"{municipality} test workbook data cleared: "
                    f"{len(workbook_rows):,} accomplishment row(s) and {len(muni_history):,} history record(s) removed."
                )
                st.rerun()

    if muni_history.empty:
        st.info(f"{municipality} has no workbook upload history yet.")
        return

    display = muni_history[[
        "id", "uploaded_at", "uploaded_by", "file_name", "workbook_version", "row_count",
        "activity_date_min", "activity_date_max", "added_count", "modified_count", "removed_count",
        "unchanged_count", "is_current", "is_finalized",
    ]].copy()
    display["uploaded_at"] = display["uploaded_at"].map(_format_datetime)
    display["activity_date_min"] = display["activity_date_min"].map(_format_date)
    display["activity_date_max"] = display["activity_date_max"].map(_format_date)
    display.columns = [
        "ID", "Uploaded", "Uploaded By", "File", "Version", "Rows", "Activity From", "Activity Through",
        "Added", "Modified", "Removed", "Unchanged", "Current", "Finalized",
    ]
    st.dataframe(display, width="stretch", hide_index=True)

    current = get_current_submission(supabase, municipality)
    if current and current.get("is_finalized"):
        st.success(f"{municipality}'s current workbook is finalized.")
        if st.button("Reopen RHU Submission", disabled=read_only, width="stretch", key="ops_reopen_submission"):
            if reopen_current_submission(supabase, municipality):
                if audit_callback:
                    audit_callback(supabase, f"RHU workbook submission reopened | municipality={municipality}")
                st.toast(f"{municipality} submission reopened.")
                st.rerun()

    restorable = muni_history[~muni_history["is_current"].fillna(False).astype(bool)].copy()
    if restorable.empty:
        return
    st.markdown("#### Restore a Previous Workbook")
    st.warning("Restore replaces the RHU's current dashboard accomplishment data with the selected previous workbook snapshot. A new history entry is created; the old history is not deleted.")
    restore_ids = restorable["id"].astype(int).tolist()
    restore_id = st.selectbox("Previous submission ID", restore_ids, key="ops_restore_submission_id")
    confirm_restore = st.checkbox(
        f"I understand this will replace {municipality}'s current RHU data with submission #{restore_id}.",
        key="ops_restore_submission_confirm",
        disabled=read_only,
    )
    if st.button(
        "Restore Selected Submission",
        type="primary",
        disabled=read_only or not confirm_restore,
        width="stretch",
        key="ops_restore_submission",
    ):
        actor = st.session_state.get("username") or st.session_state.get("user_name") or "System Admin"
        try:
            saved, removed = restore_submission(supabase, int(restore_id), str(actor))
        except Exception as exc:
            st.error(f"The previous submission could not be restored: {exc}")
            return
        if audit_callback:
            audit_callback(supabase, f"RHU workbook submission restored | municipality={municipality} | source_submission={restore_id}")
        st.cache_data.clear()
        st.success(f"Previous workbook restored: {saved:,} record(s) saved and {removed:,} current record(s) removed.")
        st.rerun()


def render_feedback_inbox(supabase, audit_callback=None, read_only: bool = False) -> None:
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
        save = st.form_submit_button("Save Feedback Update", type="primary", disabled=read_only)

    if save and not read_only:
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
        SUBMISSION_TABLE,
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
                f"Abra NIP Monitoring Information System backup\n"
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


def render_operations(supabase, audit_callback=None, read_only: bool = False) -> None:
    (
        health_tab,
        campaign_tab,
        submissions_tab,
        quality_tab,
        reconciliation_tab,
        workbook_package_tab,
        readiness_tab,
        rollout_tab,
        feedback_tab,
        backup_tab,
    ) = st.tabs(
        [
            "System Health",
            "SBI Control",
            "RHU Submissions",
            "Data Quality",
            "VaccTrack Monitor",
            "RHU Workbooks",
            "Production Readiness",
            "RHU Rollout",
            "Feedback",
            "Backup",
        ]
    )
    with health_tab:
        render_system_health(supabase, audit_callback=audit_callback, read_only=read_only)
    with campaign_tab:
        render_sbi_control(supabase, audit_callback=audit_callback, read_only=read_only)
    with submissions_tab:
        render_submission_status(supabase, audit_callback=audit_callback, read_only=read_only)
    with quality_tab:
        render_data_quality_center(supabase)
    with reconciliation_tab:
        render_reconciliation_monitor(supabase)
    with workbook_package_tab:
        render_all_rhu_workbook_package(read_only=read_only)
    with readiness_tab:
        render_production_readiness(supabase, read_only=read_only)
    with rollout_tab:
        render_rollout_status(supabase)
    with feedback_tab:
        render_feedback_inbox(supabase, audit_callback=audit_callback, read_only=read_only)
    with backup_tab:
        render_backup(supabase, audit_callback=audit_callback if not read_only else None)
