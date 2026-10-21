from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
import hashlib
import json
import re
import zipfile

import pandas as pd
import pytz
import streamlit as st

from core.config import ABRA_MUNIS
from core.data import fetch_sbi_targets
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
    reconstruct_current_submission_workbook,
)
from programs.sbi.campaign_control import CAMPAIGN_STATUSES, get_campaign_config, save_campaign_config
from programs.sbi.admin_monitoring import render_data_quality_center, render_reconciliation_monitor
from programs.sbi.production_readiness import render_all_rhu_workbook_package, render_production_readiness


MANILA_TZ = pytz.timezone("Asia/Manila")
APP_VERSION = "v5.23.1"
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
    finalization_enabled = bool(config.get("finalization_enabled", False))
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
        st.success("SBI is live. RHUs may continue daily workbook uploads.")
        if end_config and today > end_config:
            st.warning(
                f"The configured SBI activity end date was {end_config:%b %d, %Y}. "
                "If field activities are finished, change the campaign to Post-Activity Correction so RHUs can submit only corrections or late reports for activity dates within the official range."
            )
    elif status == "Post-Activity Correction":
        st.info(
            "Field implementation is finished, but RHUs may still upload corrected or late workbooks. "
            "Activity dates must remain within the configured SBI activity date range. "
            "Final submission is controlled separately by the System Admin."
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
        st.markdown("**RHU Final Report Submission**")
        allow_finalization = st.checkbox(
            "Enable final RHU submission (locks further uploads for an RHU)",
            value=finalization_enabled,
            disabled=read_only,
            help="Disabled by default. While disabled, RHUs cannot finalize even when SBI is Live. "
                 "Enable only once you are ready to accept final RHU reports after the activity.",
        )
        st.caption(
            "Leave this OFF during daily SBI reporting. RHU finalization will be hidden until you explicitly enable it. "
            "Turning it OFF does not reopen already finalized RHUs; reopen those individually under RHU Submissions."
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
                finalization_enabled=bool(allow_finalization),
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
            if bool(allow_finalization) != finalization_enabled:
                changes.append(f"rhu_finalization={'enabled' if allow_finalization else 'disabled'}")
            if going_live and not prelaunch_summary.empty and keep_prelaunch_data:
                changes.append("prelaunch_data=reviewed_and_kept")
            audit_callback(
                supabase,
                "SBI campaign settings updated" + (" | " + " | ".join(changes) if changes else " | no_material_change"),
            )
        st.cache_data.clear()
        st.toast("SBI campaign settings saved.")
        st.rerun()



def _load_sbi_bulk_cleanup_snapshot(supabase) -> dict:
    """Read only the two current SBI workbook-related datasets for the 27 RHUs."""
    accomplishment_rows = _fetch_all(
        lambda: supabase.table(ACCOMPLISHMENT_TABLE)
        .select("*")
        .eq("source_type", "workbook")
        .in_("municipality", ABRA_MUNIS),
        page_size=1000,
    )
    submission_rows = _fetch_all(
        lambda: supabase.table(SUBMISSION_TABLE)
        .select("*")
        .in_("municipality", ABRA_MUNIS),
        page_size=1000,
    )
    return {"accomplishments": accomplishment_rows, "submissions": submission_rows}


def _sbi_bulk_cleanup_fingerprint(snapshot: dict) -> str:
    """Detect uploads/edits that happened after the cleanup preview was prepared."""
    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sbi_bulk_cleanup_summary(snapshot: dict) -> pd.DataFrame:
    rows = []
    for municipality in ABRA_MUNIS:
        accomplishments = sum(
            str(row.get("municipality") or "") == municipality
            for row in snapshot["accomplishments"]
        )
        submissions = sum(
            str(row.get("municipality") or "") == municipality
            for row in snapshot["submissions"]
        )
        rows.append({
            "Municipality": municipality,
            "Workbook Accomplishment Rows": accomplishments,
            "Workbook History Entries": submissions,
        })
    return pd.DataFrame(rows)


def _sbi_bulk_cleanup_backup(snapshot: dict, summary: pd.DataFrame) -> bytes:
    """Complete record-level backup, including restorable aggregate upload snapshots."""
    data = BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for label in ("accomplishments", "submissions"):
            archive.writestr(
                f"sbi_{label}.json",
                json.dumps(snapshot[label], ensure_ascii=False, indent=2, default=str),
            )
        archive.writestr("sbi_cleanup_preview.csv", summary.to_csv(index=False))
        archive.writestr(
            "README.txt",
            "SBI pre-implementation workbook test-data backup.\n"
            "Contains full aggregate workbook accomplishments and upload history before deletion.\n"
            "This is an archival backup, NOT an automatic one-click restore file.\n"
            "Store securely and confirm it is downloaded before running cleanup.\n"
            "No RHU accounts, targets, VaccTrack, or FLU records are included.\n",
        )
    return data.getvalue()


def _sbi_bulk_cleanup_delete_ids(supabase, table: str, rows: list[dict]) -> None:
    """Delete exact previewed IDs, not newly uploaded data that appeared later."""
    ids = [row.get("id") for row in rows]
    if len(ids) != len(set(ids)) or any(value is None for value in ids):
        raise ValueError(f"Unsafe deletion: invalid or repeated IDs in {table}.")
    for start in range(0, len(ids), 200):
        supabase.table(table).delete().in_("id", ids[start:start + 200]).execute()


def _render_sbi_bulk_test_cleanup(supabase, audit_callback=None, read_only: bool = False) -> None:
    """Deliberate two-stage province-wide wipe; never enabled for a live campaign."""
    with st.expander("Pre-Implementation Cleanup — All 27 RHUs", expanded=False):
        st.warning(
            "Permanent deletion of SBI **workbook-derived accomplishments** and "
            "**workbook submission history** for all 27 RHUs. "
            "Use only when every affected record is TEST/DUMMY data. "
            "Real vaccination accomplishments must never be cleared."
        )
        st.caption(
            "Excluded: RHU accounts, school targets, VaccTrack snapshots, FLU data, "
            "and non-workbook/legacy SBI records. Admin audit logs are retained."
        )
        current_status = str(get_campaign_config(supabase).get("status") or "Pre-Implementation")
        if read_only:
            st.info("QA Admin is read-only. Only System Admin can clear test data.")
            return
        if current_status != "Pre-Implementation":
            st.error(
                f"Bulk cleanup is LOCKED: SBI status is {current_status}. "
                "This tool cannot delete records while implementation is Live "
                "or during post-activity correction/closure. "
                "Do not switch a live campaign back just to wipe records."
            )
            st.session_state.pop("sbi_bulk_cleanup_snapshot", None)
            return

        st.info(
            "Before proceeding: stop all RHU workbook uploads and confirm there are "
            "no genuine vaccination records in the system. "
            "First review and download the backup, then confirm the deletion."
        )
        if st.button(
            "Review all RHU test data and prepare backup",
            key="sbi_bulk_cleanup_prepare",
            width="stretch",
        ):
            # Any renewed review requires a fresh, explicit confirmation.
            st.session_state["sbi_bulk_cleanup_backup_confirmed"] = False
            st.session_state["sbi_bulk_cleanup_actual_confirmed"] = False
            st.session_state["sbi_bulk_cleanup_typed"] = ""
            try:
                st.session_state["sbi_bulk_cleanup_snapshot"] = _load_sbi_bulk_cleanup_snapshot(supabase)
            except Exception as exc:
                st.session_state.pop("sbi_bulk_cleanup_snapshot", None)
                st.error(f"Unable to read workbook data. Nothing was deleted: {exc}")

        snapshot = st.session_state.get("sbi_bulk_cleanup_snapshot")
        if snapshot is None:
            return
        summary = _sbi_bulk_cleanup_summary(snapshot)
        accomplishment_count = len(snapshot["accomplishments"])
        submission_count = len(snapshot["submissions"])
        a, b, c = st.columns(3)
        a.metric("RHUs With Records", int(((summary.iloc[:, 1] + summary.iloc[:, 2]) > 0).sum()))
        b.metric("Workbook Rows", accomplishment_count)
        c.metric("Upload History Entries", submission_count)
        st.dataframe(summary, width="stretch", hide_index=True)

        if accomplishment_count + submission_count == 0:
            st.success("No SBI workbook test data remains to be cleared.")
            return

        archive = _sbi_bulk_cleanup_backup(snapshot, summary)
        timestamp = datetime.now(MANILA_TZ).strftime("%Y%m%d_%H%M")
        st.download_button(
            "Download PRE-DELETION backup (ZIP)",
            data=archive,
            file_name=f"SBI_27_RHU_Test_Data_Backup_{timestamp}.zip",
            mime="application/zip",
            key="sbi_bulk_cleanup_download_backup",
            width="stretch",
        )
        backup_confirmed = st.checkbox(
            "I downloaded and securely saved the backup ZIP.",
            key="sbi_bulk_cleanup_backup_confirmed",
        )
        actual_data_confirmed = st.checkbox(
            "I personally verified that ALL listed records are test/dummy data "
            "and no RHU has uploaded actual vaccination accomplishments.",
            key="sbi_bulk_cleanup_actual_confirmed",
        )
        typed = st.text_input(
            "Type CLEAR ALL SBI TEST DATA to confirm",
            key="sbi_bulk_cleanup_typed",
        )
        confirmed = (
            backup_confirmed and actual_data_confirmed
            and typed.strip() == "CLEAR ALL SBI TEST DATA"
        )
        if st.button(
            "Permanently clear all 27 RHUs' test workbook data",
            type="primary",
            disabled=not confirmed,
            key="sbi_bulk_cleanup_execute",
            width="stretch",
        ):
            # Re-check both status and snapshot immediately before destructive operations.
            # An upload/restore since review must invalidate this confirmation.
            if str(get_campaign_config(supabase).get("status") or "") != "Pre-Implementation":
                st.error("SBI campaign status changed. Cleanup cancelled; no records deleted.")
                return
            try:
                fresh = _load_sbi_bulk_cleanup_snapshot(supabase)
            except Exception as exc:
                st.error(f"Unable to re-check data. Nothing deleted: {exc}")
                return
            if _sbi_bulk_cleanup_fingerprint(fresh) != _sbi_bulk_cleanup_fingerprint(snapshot):
                st.session_state.pop("sbi_bulk_cleanup_snapshot", None)
                st.error(
                    "Data changed after your preview (new upload, edit or restore). "
                    "Nothing was deleted. Review and back up the new data again."
                )
                return
            try:
                # Matches the existing per-RHU cleanup order.
                _sbi_bulk_cleanup_delete_ids(supabase, SUBMISSION_TABLE, snapshot["submissions"])
                _sbi_bulk_cleanup_delete_ids(supabase, ACCOMPLISHMENT_TABLE, snapshot["accomplishments"])
                remaining = _load_sbi_bulk_cleanup_snapshot(supabase)
                if remaining["submissions"] or remaining["accomplishments"]:
                    raise RuntimeError("Cleanup did not remove every previewed record.")
            except Exception as exc:
                st.error(
                    "Cleanup may have PARTIALLY completed. Keep the backup ZIP. "
                    "Review the RHU submission table before doing anything else. "
                    f"Details: {exc}"
                )
                return

            if audit_callback:
                try:
                    audit_callback(
                        supabase,
                        "SBI province-wide pre-implementation workbook TEST data cleared "
                        f"| RHUs={len(ABRA_MUNIS)} "
                        f"| accomplishment_rows={accomplishment_count} "
                        f"| submission_history={submission_count}",
                    )
                except Exception as exc:
                    st.warning(f"Data cleared, but audit logging failed: {exc}")
            st.session_state.pop("sbi_bulk_cleanup_snapshot", None)
            st.session_state["sbi_bulk_cleanup_success"] = (
                f"All 27 RHUs cleaned: {accomplishment_count:,} workbook row(s) "
                f"and {submission_count:,} submission history entry/entries deleted."
            )
            st.cache_data.clear()
            st.rerun()

def _current_workbook_signature(current_by_muni: dict[str, dict]) -> str:
    """Invalidate prepared exports as soon as the latest RHU upload changes."""
    details = [
        (
            muni,
            current_by_muni.get(muni, {}).get("id"),
            current_by_muni.get(muni, {}).get("batch_id"),
            current_by_muni.get(muni, {}).get("uploaded_at"),
            current_by_muni.get(muni, {}).get("row_count"),
        )
        for muni in ABRA_MUNIS
    ]
    return hashlib.sha256(json.dumps(details, default=str).encode("utf-8")).hexdigest()


def _submitted_workbook_filename(municipality: str, submission: dict) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", municipality).strip("_")
    return f"SBI_Current_{safe}_Submission_{submission['id']}.xlsx"


def _build_current_workbooks_zip(targets: pd.DataFrame, current_by_muni: dict[str, dict]) -> tuple[bytes, pd.DataFrame]:
    """Include one reconstructed .xlsx per current RHU upload and a complete 27-RHU manifest."""
    manifest_rows = []
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for municipality in ABRA_MUNIS:
            current = current_by_muni.get(municipality)
            if not current:
                manifest_rows.append({
                    "Municipality": municipality,
                    "Status": "No current upload",
                    "Submission ID": "",
                    "Uploaded At": "",
                    "Uploaded By": "",
                    "Rows": 0,
                    "Original File": "",
                    "Reconstructed File": "",
                })
                continue
            # Fail closed if any current snapshot cannot be faithfully rebuilt.
            # Never silently put an empty or partially populated file in the ZIP.
            try:
                workbook = reconstruct_current_submission_workbook(targets, current)
            except Exception as exc:
                raise ValueError(f"{municipality}: {exc}") from exc
            name = _submitted_workbook_filename(municipality, current)
            archive.writestr(name, workbook)
            manifest_rows.append({
                "Municipality": municipality,
                "Status": "Reconstructed current upload",
                "Submission ID": current.get("id"),
                "Uploaded At": current.get("uploaded_at") or "",
                "Uploaded By": current.get("uploaded_by") or "",
                "Rows": current.get("row_count") or 0,
                "Original File": current.get("file_name") or "",
                "Reconstructed File": name,
            })
        manifest = pd.DataFrame(manifest_rows)
        archive.writestr("CURRENT_SUBMISSION_MANIFEST.csv", manifest.to_csv(index=False).encode("utf-8-sig"))
        archive.writestr(
            "README.txt",
            (
                "Abra NIP MIS - SBI Current RHU Submissions\n\n"
                "Each .xlsx is reconstructed from its RHU's latest current SAVED "
                "submission snapshot. These are NOT byte-for-byte copies of the "
                "original uploaded files. No data was changed in the system.\n"
                "The current offline workbook template is used, preserving "
                "Setup, Reference, Accomplishments and VaccTrack G1/G4/G7.\n"
                "RHUs with no current upload have no XLSX and are listed in the manifest.\n"
                "Review the Accomplishments sheet before continuing offline encoding.\n"
            ).encode("utf-8"),
        )
    return buffer.getvalue(), manifest


def _render_current_workbook_downloads(current_by_muni: dict[str, dict]) -> None:
    """Read-only Admin/QA Admin downloads from current, restorable snapshots."""
    with st.expander("Current Uploaded Workbooks — Preview & Download", expanded=False):
        st.caption(
            "Inspect the current saved workbook for any RHU, reconstruct an Excel copy, "
            "or download all current RHU submissions in one ZIP. "
            "This is read-only and does not change submissions, finalization, or upload history."
        )
        st.info(
            "These are reconstructed workbooks from the latest saved RHU submission snapshots, "
            "NOT the original Excel files uploaded by the RHUs. "
            "They include the current template and VaccTrack G1/G4/G7 worksheets."
        )
        signature = _current_workbook_signature(current_by_muni)
        if st.session_state.get("ops_current_workbooks_signature") != signature:
            for key in ("ops_current_one_workbook", "ops_current_one_id", "ops_current_all_zip", "ops_current_all_manifest"):
                st.session_state.pop(key, None)
            st.session_state["ops_current_workbooks_signature"] = signature

        count = sum(bool(current_by_muni.get(muni)) for muni in ABRA_MUNIS)
        st.metric("RHUs With Current Uploaded Workbooks", f"{count}/{len(ABRA_MUNIS)}")
        if count == 0:
            st.info("No RHUs have a current saved workbook upload yet.")
            return

        municipality = st.selectbox("Inspect RHU current workbook", ABRA_MUNIS, key="ops_current_workbook_muni")
        current = current_by_muni.get(municipality)
        if current:
            st.caption(
                f"Current submission #{current.get('id')} | "
                f"Uploaded {_format_datetime(current.get('uploaded_at'))} | "
                f"By {current.get('uploaded_by') or 'Unknown'} | "
                f"{int(current.get('row_count') or 0):,} activity rows | "
                f"{'Finalized' if current.get('is_finalized') else 'Not finalized'}"
            )
            snapshot = current.get("snapshot")
            if isinstance(snapshot, list) and snapshot:
                preview = pd.DataFrame(snapshot)
                cols = ["activity_date", "school_name", "school_id", "barangay", "grade_level"]
                cols += [c for c in ("MR Male", "MR Female", "Td Male", "Td Female", "HPV Dose 1") if c in preview.columns]
                st.dataframe(preview[[c for c in cols if c in preview.columns]], width="stretch", hide_index=True)
                if st.button("Prepare Current RHU Workbook", key="ops_prepare_current_one", width="stretch"):
                    try:
                        targets = fetch_sbi_targets()
                        payload = reconstruct_current_submission_workbook(targets, current)
                    except Exception as exc:
                        st.session_state.pop("ops_current_one_workbook", None)
                        st.error(f"Unable to reconstruct {municipality}: {exc}")
                    else:
                        st.session_state["ops_current_one_workbook"] = payload
                        st.session_state["ops_current_one_id"] = current.get("id")
                if st.session_state.get("ops_current_one_id") == current.get("id") and st.session_state.get("ops_current_one_workbook"):
                    st.download_button(
                        "Download Current RHU Workbook (.xlsx)",
                        data=st.session_state["ops_current_one_workbook"],
                        file_name=_submitted_workbook_filename(municipality, current),
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="ops_download_current_one",
                        width="stretch",
                    )
            else:
                st.warning("The current submission has no saved snapshot to reconstruct.")
        else:
            st.info(f"{municipality} has no current uploaded workbook.")

        st.divider()
        st.markdown("#### Current Workbooks for All RHUs")
        st.caption(
            "Prepare a ZIP containing one current workbook for each RHU that has uploaded, "
            "plus a 27-RHU manifest. RHUs without uploads are listed in the manifest "
            "but no blank workbook is substituted."
        )
        if st.button("Prepare Current RHU Workbooks ZIP", key="ops_prepare_current_all", width="stretch"):
            try:
                targets = fetch_sbi_targets()
                with st.spinner(f"Reconstructing {count} current RHU workbook(s)..."):
                    archive, manifest = _build_current_workbooks_zip(targets, current_by_muni)
            except Exception as exc:
                st.session_state.pop("ops_current_all_zip", None)
                st.session_state.pop("ops_current_all_manifest", None)
                st.error(f"Unable to prepare current workbook package: {exc}")
            else:
                st.session_state["ops_current_all_zip"] = archive
                st.session_state["ops_current_all_manifest"] = manifest
                st.success(f"Prepared {count} current workbook(s); {len(ABRA_MUNIS) - count} RHU(s) have no current upload.")
        payload = st.session_state.get("ops_current_all_zip")
        if payload:
            st.download_button(
                "Download Current RHU Workbooks (ZIP)",
                data=payload,
                file_name="Abra_SBI_Current_RHU_Uploaded_Workbooks.zip",
                mime="application/zip",
                key="ops_download_current_all",
                width="stretch",
            )
            st.dataframe(st.session_state["ops_current_all_manifest"], width="stretch", hide_index=True)


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

    _render_current_workbook_downloads(current_by_muni)

    if st.session_state.get("sbi_bulk_cleanup_success"):
        st.success(st.session_state.pop("sbi_bulk_cleanup_success"))
    _render_sbi_bulk_test_cleanup(supabase, audit_callback=audit_callback, read_only=read_only)

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
