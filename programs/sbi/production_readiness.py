from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
import hashlib
import re
import zipfile

import pandas as pd
import pytz
import streamlit as st

from core.config import ABRA_MUNIS
from core.data import fetch_sbi_targets, fetch_sbi_vacctrack_source_info
from core.map_labels import canonical_municipality_name, normalize_municipality_key
from programs.sbi.aggregate_workbook import (
    SUBMISSION_TABLE,
    TABLE_NAME as ACCOMPLISHMENT_TABLE,
    WORKBOOK_VERSION,
    build_offline_workbook,
    fetch_submission_history,
)
from programs.sbi.admin_monitoring import build_data_quality_report
from programs.sbi.campaign_control import get_campaign_config


MANILA_TZ = pytz.timezone("Asia/Manila")
EXPECTED_RHU_COUNT = len(ABRA_MUNIS)


CORE_TABLES = [
    ("RHU accounts", "user_accounts", "username"),
    ("SBI targets", "sbi_targets", "school_id"),
    ("RHU accomplishments", ACCOMPLISHMENT_TABLE, "id"),
    ("VaccTrack imports", "sbi_vacctrack_imports", "id"),
    ("VaccTrack rows", "sbi_vacctrack_rows", "id"),
    ("SBI settings", "sbi_settings", "setting_key"),
    ("Workbook submissions", SUBMISSION_TABLE, "id"),
]


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


def _table_available(supabase, table: str, column: str) -> tuple[bool, str]:
    try:
        supabase.table(table).select(column).limit(1).execute()
        return True, "Available"
    except Exception as exc:
        detail = str(exc).strip()
        return False, detail[:140] if detail else "Unavailable"


def _safe_filename(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return text.strip("_") or "RHU"




def _targets_fingerprint(targets: pd.DataFrame) -> str:
    columns = ["Municipality", "Barangay", "School ID", "School Name", "G1 Total", "G4 Female", "G7 Total"]
    if targets is None or targets.empty:
        return "empty"
    work = targets.copy()
    for column in columns:
        if column not in work.columns:
            work[column] = 0 if column in {"G1 Total", "G4 Female", "G7 Total"} else ""
    work = work[columns].copy()
    work["Municipality"] = work["Municipality"].map(lambda value: canonical_municipality_name(str(value or "").strip()))
    work["School ID"] = work["School ID"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    work["School Name"] = work["School Name"].fillna("").astype(str).str.strip()
    work["Barangay"] = work["Barangay"].fillna("").astype(str).str.strip()
    for column in ["G1 Total", "G4 Female", "G7 Total"]:
        work[column] = pd.to_numeric(work[column], errors="coerce").fillna(0)
    work = work.sort_values(["Municipality", "School ID", "School Name"]).reset_index(drop=True)
    payload = work.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _prepare_quality_targets(targets: pd.DataFrame) -> pd.DataFrame:
    if targets is None or targets.empty:
        return pd.DataFrame()
    out = targets.copy()
    for column in ["Municipality", "School ID", "School Name", "G1 Total", "G4 Female", "G7 Total"]:
        if column not in out.columns:
            out[column] = 0 if column in {"G1 Total", "G4 Female", "G7 Total"} else ""
    out["Municipality"] = out["Municipality"].map(lambda value: canonical_municipality_name(str(value or "").strip()))
    out["School ID"] = out["School ID"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    out["School Name"] = out["School Name"].fillna("").astype(str).str.strip()
    for column in ["G1 Total", "G4 Female", "G7 Total"]:
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0)
    return (
        out[out["School ID"].ne("")]
        .groupby(["Municipality", "School ID"], as_index=False)
        .agg({"School Name": "first", "G1 Total": "sum", "G4 Female": "sum", "G7 Total": "sum"})
    )


def _school_count(targets: pd.DataFrame, municipality: str) -> int:
    if targets is None or targets.empty or "Municipality" not in targets.columns:
        return 0
    key = normalize_municipality_key(municipality)
    rows = targets[targets["Municipality"].map(normalize_municipality_key).eq(key)].copy()
    if rows.empty or "School ID" not in rows.columns:
        return 0
    school_ids = (
        rows["School ID"]
        .fillna("")
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
    )
    return int(school_ids[school_ids.ne("")].nunique())


def workbook_package_manifest(targets: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for municipality in ABRA_MUNIS:
        count = _school_count(targets, municipality)
        rows.append(
            {
                "Municipality": municipality,
                "Schools": count,
                "Workbook": f"SBI_Accomplishment_{_safe_filename(municipality)}_2026.xlsx",
                "Workbook Version": WORKBOOK_VERSION,
                "Status": "Ready" if count > 0 else "No school roster",
            }
        )
    return pd.DataFrame(rows)


def build_all_rhu_workbooks_zip(targets: pd.DataFrame) -> tuple[bytes, pd.DataFrame]:
    manifest = workbook_package_manifest(targets)
    missing = manifest[manifest["Schools"].eq(0)]
    if not missing.empty:
        names = ", ".join(missing["Municipality"].astype(str).tolist())
        raise ValueError(f"Cannot build the complete package because these RHUs have no school roster: {names}.")

    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for _, row in manifest.iterrows():
            municipality = str(row["Municipality"])
            workbook = build_offline_workbook(targets, municipality)
            archive.writestr(str(row["Workbook"]), workbook)

        manifest_buffer = BytesIO()
        manifest.to_csv(manifest_buffer, index=False, encoding="utf-8-sig")
        archive.writestr("WORKBOOK_MANIFEST.csv", manifest_buffer.getvalue())
        archive.writestr(
            "README.txt",
            (
                "Abra NIP Monitoring Information System\n"
                "School-Based Immunization 2026 - RHU Workbook Package\n\n"
                f"Workbook version: {WORKBOOK_VERSION}\n"
                f"Generated: {datetime.now(MANILA_TZ).isoformat()}\n"
                f"Municipalities: {EXPECTED_RHU_COUNT}\n\n"
                "Each workbook is municipality-specific. Distribute only the workbook intended for that RHU.\n"
                "If a newer workbook version is released, generate a fresh package from the system instead of reusing this ZIP.\n"
            ).encode("utf-8"),
        )
    return output.getvalue(), manifest


def render_all_rhu_workbook_package(read_only: bool = False) -> None:
    st.markdown("### All RHU SBI Workbooks")
    st.caption(
        "Generate one ZIP containing the current municipality-specific offline workbook for every Abra RHU. "
        "Keep a copy before implementation so workbooks can still be distributed if internet access is unstable."
    )

    try:
        targets = fetch_sbi_targets()
    except Exception as exc:
        st.error(f"Unable to load the SBI target roster: {exc}")
        return

    manifest = workbook_package_manifest(targets)
    ready = int(manifest["Status"].eq("Ready").sum())
    missing = int(manifest["Status"].ne("Ready").sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("RHUs Ready", f"{ready}/{EXPECTED_RHU_COUNT}")
    c2.metric("Workbook Version", WORKBOOK_VERSION)
    c3.metric("Roster Issues", missing)

    st.dataframe(manifest, width="stretch", hide_index=True)

    if missing:
        st.error(
            "The complete package cannot be generated until every RHU has at least one school in the current SBI target roster."
        )
        return

    if st.button(
        "Generate All 27 RHU Workbooks",
        type="primary",
        width="stretch",
        key="ops_generate_all_rhu_workbooks",
    ):
        try:
            with st.spinner("Generating the 27 RHU workbooks..."):
                payload, generated_manifest = build_all_rhu_workbooks_zip(targets)
        except Exception as exc:
            st.error(f"Workbook package could not be generated: {exc}")
            return
        st.session_state["ops_all_rhu_workbook_zip"] = payload
        st.session_state["ops_all_rhu_workbook_manifest"] = generated_manifest
        st.session_state["ops_all_rhu_workbook_created"] = datetime.now(MANILA_TZ).isoformat()
        st.session_state["ops_all_rhu_workbook_target_fingerprint"] = _targets_fingerprint(targets)
        st.session_state["ops_all_rhu_workbook_version"] = WORKBOOK_VERSION
        st.success("All 27 municipality-specific SBI workbooks were generated successfully.")

    payload = st.session_state.get("ops_all_rhu_workbook_zip")
    generated_at = st.session_state.get("ops_all_rhu_workbook_created")
    if payload:
        if generated_at:
            parsed = pd.to_datetime(generated_at, errors="coerce")
            if not pd.isna(parsed):
                st.caption(f"Package prepared {parsed.strftime('%b %d, %Y %I:%M %p').replace(' 0', ' ')}.")
        st.download_button(
            "Download All 27 RHU Workbooks (ZIP)",
            data=payload,
            file_name=f"Abra_SBI_All_RHU_Workbooks_{WORKBOOK_VERSION}.zip",
            mime="application/zip",
            width="stretch",
            key="ops_download_all_rhu_workbooks",
        )


def _normalize_entries(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    if "municipality" in frame.columns:
        frame["municipality"] = frame["municipality"].map(
            lambda value: canonical_municipality_name(str(value or "").strip())
        )
    if "school_id" in frame.columns:
        frame["school_id"] = (
            frame["school_id"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
        )
    if "activity_date" in frame.columns:
        frame["activity_date"] = pd.to_datetime(frame["activity_date"], errors="coerce").dt.date
    return frame


def _readiness_row(area: str, check: str, status: str, detail: str) -> dict:
    return {"Area": area, "Check": check, "Status": status, "Detail": detail}


def run_production_readiness_checks(supabase) -> pd.DataFrame:
    rows: list[dict] = []

    unavailable = []
    for label, table, column in CORE_TABLES:
        ready, detail = _table_available(supabase, table, column)
        if not ready:
            unavailable.append(f"{label}: {detail}")
    rows.append(
        _readiness_row(
            "Database",
            "Operational tables",
            "Ready" if not unavailable else "Blocked",
            "All required operational tables are available."
            if not unavailable
            else "Unavailable: " + " | ".join(unavailable),
        )
    )

    try:
        account_rows = _fetch_all(
            lambda: supabase.table("user_accounts")
            .select("username,role,assigned_muni,account_status,must_change_password")
            .eq("role", "RHU Encoder"),
            page_size=500,
        )
        accounts = pd.DataFrame(account_rows)
    except Exception as exc:
        accounts = pd.DataFrame()
        rows.append(_readiness_row("Accounts", "27 RHU Encoder accounts", "Blocked", f"Unable to read RHU accounts: {exc}"))
    else:
        expected_keys = {normalize_municipality_key(value) for value in ABRA_MUNIS}
        assigned = accounts.get("assigned_muni", pd.Series(dtype=object)).fillna("").astype(str)
        assigned_keys = [normalize_municipality_key(value) for value in assigned if str(value).strip()]
        covered = set(assigned_keys)
        missing_keys = expected_keys - covered
        duplicate_count = len(assigned_keys) - len(set(assigned_keys))
        account_status = "Ready"
        details = [f"{len(accounts)}/{EXPECTED_RHU_COUNT} RHU Encoder account(s)", f"{len(covered & expected_keys)}/{EXPECTED_RHU_COUNT} municipalities assigned"]
        if missing_keys or len(accounts) < EXPECTED_RHU_COUNT:
            account_status = "Blocked"
            missing_names = [m for m in ABRA_MUNIS if normalize_municipality_key(m) in missing_keys]
            if missing_names:
                details.append("Missing: " + ", ".join(missing_names))
        elif len(accounts) != EXPECTED_RHU_COUNT or duplicate_count:
            account_status = "Needs Review"
            details.append(f"Duplicate/extra municipality assignments: {duplicate_count}")
        rows.append(_readiness_row("Accounts", "27 RHU Encoder accounts", account_status, " • ".join(details)))

        if not accounts.empty and "must_change_password" in accounts.columns:
            pending = accounts["must_change_password"].map(
                lambda value: value is True or str(value or "").strip().lower() in {"1", "true", "yes", "y"}
            )
            changed = int((~pending).sum())
            rows.append(
                _readiness_row(
                    "Accounts",
                    "RHU password activation",
                    "Ready" if changed >= EXPECTED_RHU_COUNT else "Needs Review",
                    f"{changed}/{EXPECTED_RHU_COUNT} RHU account(s) have changed the temporary password.",
                )
            )

    try:
        targets = fetch_sbi_targets()
    except Exception as exc:
        targets = pd.DataFrame()
        rows.append(_readiness_row("Workbook", "SBI target roster", "Blocked", f"Unable to load SBI targets: {exc}"))
    else:
        manifest = workbook_package_manifest(targets)
        missing_roster = manifest[manifest["Schools"].eq(0)]
        rows.append(
            _readiness_row(
                "Workbook",
                "SBI target roster covers all RHUs",
                "Ready" if missing_roster.empty else "Blocked",
                f"All {EXPECTED_RHU_COUNT} RHUs have a school roster."
                if missing_roster.empty
                else "No school roster: " + ", ".join(missing_roster["Municipality"].astype(str).tolist()),
            )
        )

        generation_failures = []
        if missing_roster.empty:
            for municipality in ABRA_MUNIS:
                try:
                    payload = build_offline_workbook(targets, municipality)
                    if not payload or len(payload) < 5000:
                        generation_failures.append(f"{municipality}: empty/invalid output")
                except Exception as exc:
                    generation_failures.append(f"{municipality}: {exc}")
        rows.append(
            _readiness_row(
                "Workbook",
                "Generate all 27 RHU workbooks",
                "Ready" if not generation_failures and missing_roster.empty else "Blocked",
                "All municipality-specific workbooks generated successfully."
                if not generation_failures and missing_roster.empty
                else " | ".join(generation_failures[:8]) or "Target-roster issues prevent complete workbook generation.",
            )
        )

    config = get_campaign_config(supabase)
    campaign_status = str(config.get("status") or "Pre-Implementation")
    start_date = config.get("start_date")
    end_date = config.get("end_date")
    date_status = "Ready" if start_date and end_date and end_date >= start_date else "Blocked"
    date_detail = (
        f"Official activity period: {start_date:%b %d, %Y} to {end_date:%b %d, %Y}."
        if start_date and end_date and end_date >= start_date
        else "Set and enforce a valid official SBI activity start and end date in SBI Control."
    )
    rows.append(_readiness_row("Campaign", "Official activity date range", date_status, date_detail))
    rows.append(
        _readiness_row(
            "Campaign",
            "Campaign operating status",
            "Ready" if campaign_status in {"Pre-Implementation", "Live", "Post-Activity Correction", "Closed"} else "Blocked",
            f"Current status: {campaign_status}. Keep Pre-Implementation until the actual launch, then switch to Live.",
        )
    )

    try:
        source_info = fetch_sbi_vacctrack_source_info()
        source_details = []
        missing_sources = []
        for grade in ("G1", "G4", "G7"):
            info = source_info.get(grade, {}) or {}
            source = str(info.get("source") or "Unavailable")
            through = str(info.get("report_date_max") or "")
            source_details.append(f"{grade}: {source}" + (f" through {through}" if through else ""))
            if not source or source.lower() == "unavailable":
                missing_sources.append(grade)
        rows.append(
            _readiness_row(
                "VaccTrack",
                "VaccTrack sources available",
                "Ready" if not missing_sources else "Needs Review",
                " • ".join(source_details),
            )
        )
    except Exception as exc:
        rows.append(_readiness_row("VaccTrack", "VaccTrack sources available", "Needs Review", f"Unable to inspect VaccTrack sources: {exc}"))

    try:
        entry_rows = _fetch_all(lambda: supabase.table(ACCOMPLISHMENT_TABLE).select("*"), page_size=1000)
        entries = _normalize_entries(entry_rows)
        submissions = fetch_submission_history(supabase)
        target_for_quality = _prepare_quality_targets(targets) if "targets" in locals() else pd.DataFrame()
        findings = build_data_quality_report(entries, target_for_quality, submissions, config)
        critical = int(findings["Severity"].eq("Critical").sum()) if not findings.empty else 0
        warnings = int(findings["Severity"].eq("Warning").sum()) if not findings.empty else 0
        quality_status = "Blocked" if critical else ("Needs Review" if warnings else "Ready")
        rows.append(
            _readiness_row(
                "Data Quality",
                "Unresolved Critical/Warning findings",
                quality_status,
                f"Critical: {critical} • Warning: {warnings}. Use the Data Quality tab for details.",
            )
        )

        workbook_rows = entries[entries.get("source_type", pd.Series(dtype=object)).fillna("").astype(str).str.lower().eq("workbook")] if not entries.empty and "source_type" in entries.columns else pd.DataFrame()
        prelaunch_count = len(workbook_rows) if campaign_status == "Pre-Implementation" else 0
        rows.append(
            _readiness_row(
                "Pre-Launch",
                "Pre-implementation workbook data reviewed",
                "Ready" if prelaunch_count == 0 else "Needs Review",
                "No workbook-derived data is currently stored during Pre-Implementation."
                if prelaunch_count == 0
                else f"{prelaunch_count} workbook-derived row(s) exist while still in Pre-Implementation. Clear test data or explicitly review it before switching to Live.",
            )
        )
    except Exception as exc:
        rows.append(_readiness_row("Data Quality", "Unresolved Critical/Warning findings", "Needs Review", f"Unable to complete quality scan: {exc}"))

    backup_ready = bool(st.session_state.get("ops_backup_payload"))
    rows.append(
        _readiness_row(
            "Backup",
            "Final backup prepared in this session",
            "Ready" if backup_ready else "Needs Review",
            "A backup ZIP has been prepared and is ready to download."
            if backup_ready
            else "Open the Backup tab, prepare the final backup ZIP, and download it before launch.",
        )
    )

    package_payload = bool(st.session_state.get("ops_all_rhu_workbook_zip"))
    package_fingerprint = st.session_state.get("ops_all_rhu_workbook_target_fingerprint")
    package_version = st.session_state.get("ops_all_rhu_workbook_version")
    current_fingerprint = _targets_fingerprint(targets) if "targets" in locals() and not targets.empty else None
    package_ready = bool(
        package_payload
        and package_version == WORKBOOK_VERSION
        and current_fingerprint
        and package_fingerprint == current_fingerprint
    )
    rows.append(
        _readiness_row(
            "Offline Contingency",
            "All 27 RHU workbooks package prepared",
            "Ready" if package_ready else "Needs Review",
            "The all-RHU workbook ZIP matches the current workbook version and target roster."
            if package_ready
            else "Generate a fresh all-RHU workbook ZIP before implementation; regenerate it whenever the target roster or workbook version changes.",
        )
    )

    return pd.DataFrame(rows)


def render_production_readiness(supabase, read_only: bool = False) -> None:
    st.markdown("### Production Readiness")
    st.caption(
        "Run this before SBI implementation and again before final code freeze. The scan does not change production data."
    )

    if st.button("Run Production Readiness Check", type="primary", width="stretch", key="ops_run_readiness"):
        with st.spinner("Checking accounts, database, workbooks, campaign settings, VaccTrack, and data quality..."):
            st.session_state["ops_readiness_report"] = run_production_readiness_checks(supabase)
            st.session_state["ops_readiness_checked_at"] = datetime.now(MANILA_TZ).isoformat()

    report = st.session_state.get("ops_readiness_report")
    if report is None:
        st.info("Run the readiness check to evaluate the current production state.")
        return

    report = pd.DataFrame(report)
    blocked = int(report["Status"].eq("Blocked").sum())
    review = int(report["Status"].eq("Needs Review").sum())
    ready = int(report["Status"].eq("Ready").sum())

    c1, c2, c3 = st.columns(3)
    c1.metric("Ready", ready)
    c2.metric("Needs Review", review)
    c3.metric("Blocked", blocked)

    if blocked == 0 and review == 0:
        st.success("READY FOR SBI IMPLEMENTATION")
    elif blocked == 0:
        st.warning("PRE-LAUNCH REVIEW REQUIRED — no blocking failures, but some items still need confirmation.")
    else:
        st.error("NOT READY FOR LAUNCH — resolve the blocked items before switching the campaign to Live.")

    checked_at = st.session_state.get("ops_readiness_checked_at")
    parsed = pd.to_datetime(checked_at, errors="coerce")
    if not pd.isna(parsed):
        st.caption(f"Last checked: {parsed.strftime('%b %d, %Y %I:%M %p').replace(' 0', ' ')}")

    st.dataframe(report, width="stretch", hide_index=True)
    st.download_button(
        "Download Readiness Report (CSV)",
        data=report.to_csv(index=False).encode("utf-8-sig"),
        file_name="Abra_NIP_SBI_Production_Readiness.csv",
        mime="text/csv",
        width="stretch",
        key="ops_download_readiness_report",
    )
