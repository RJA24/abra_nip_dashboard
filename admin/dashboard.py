from __future__ import annotations

from datetime import datetime
import hashlib
import json
import time

import numpy as np
import pandas as pd
import pytz
import streamlit as st
from streamlit_gsheets import GSheetsConnection

from auth_utils import hash_password
from core.config import ABRA_MUNIS, SIA_SHEET_URL, SBI_SHEET_URL
from core.geo import clean_and_process_car_data, fetch_abra_geojson
from core.map_labels import (
    build_label_records,
    canonical_municipality_name,
    invalidate_runtime_label_cache,
    reset_all_label_positions,
    reset_label_position,
    save_label_records,
    table_available as map_label_table_available,
)
from db_utils import consolidate_sbi_targets, replace_table_with_rollback, validate_sbi_targets
from programs.sbi.vacctrack_import import render_vacctrack_importer
from programs.sbi.import_management import render_import_management
from admin.operations import render_operations


MANILA_TZ = pytz.timezone("Asia/Manila")


def _now_string() -> str:
    return datetime.now(MANILA_TZ).strftime("%Y-%m-%d %I:%M:%S %p")


def _audit(supabase, action: str) -> None:
    try:
        supabase.table("access_logs").insert(
            {
                "timestamp": _now_string(),
                "name": st.session_state.get("user_name") or st.session_state.get("username") or "System Admin",
                "role": "System Admin",
                "action": f"Admin: {action}",
            }
        ).execute()
    except Exception:
        pass


def _logout_session() -> None:
    st.session_state.clear()
    st.rerun()


def _table_row_count(supabase, table: str, key_column: str) -> int:
    total = 0
    offset = 0
    limit = 1000
    while True:
        response = (
            supabase.table(table)
            .select(key_column)
            .range(offset, offset + limit - 1)
            .execute()
        )
        rows = response.data or []
        total += len(rows)
        if len(rows) < limit:
            break
        offset += limit
    return total


def _load_accounts(supabase) -> pd.DataFrame:
    response = supabase.table("user_accounts").select("*").execute()
    if not response.data:
        return pd.DataFrame()

    df = pd.DataFrame(response.data)
    for col in ["username", "name", "role", "account_status", "assigned_muni"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("").astype(str).str.strip()

    if "must_change_password" not in df.columns:
        df["must_change_password"] = False
    df["must_change_password"] = df["must_change_password"].map(
        lambda value: value is True or str(value or "").strip().lower() in {"1", "true", "yes", "y"}
    )

    df.loc[df["account_status"].eq(""), "account_status"] = "Approved"
    return df


def _load_admin_logs(supabase, limit: int = 200) -> pd.DataFrame:
    response = (
        supabase.table("access_logs")
        .select("*")
        .order("id", desc=True)
        .limit(limit)
        .execute()
    )
    if not response.data:
        return pd.DataFrame(columns=["timestamp", "name", "role", "action"])

    df = pd.DataFrame(response.data)
    if "action" not in df.columns:
        df["action"] = ""
    df["action"] = df["action"].fillna("").astype(str)
    df = df[df["action"].str.startswith("Admin:", na=False)].copy()
    display_cols = [c for c in ["timestamp", "name", "action"] if c in df.columns]
    return df[display_cols]



def _load_login_logs(supabase, limit: int = 500) -> pd.DataFrame:
    """Load one-time login events for visitors and registered accounts."""
    response = (
        supabase.table("access_logs")
        .select("*")
        .order("id", desc=True)
        .limit(limit)
        .execute()
    )
    if not response.data:
        return pd.DataFrame(columns=["timestamp", "name", "role", "action", "Username", "Municipality"])

    df = pd.DataFrame(response.data)
    if "action" not in df.columns:
        df["action"] = ""
    df["action"] = df["action"].fillna("").astype(str)
    mask = df["action"].str.startswith("Login", na=False) | df["action"].eq("Active Session")
    df = df.loc[mask].copy()
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "name", "role", "action", "Username", "Municipality"])

    def parse_detail(action: object, key: str) -> str:
        raw = str(action or "")
        for part in raw.split("|"):
            part = part.strip()
            if part.lower().startswith(key.lower() + "="):
                return part.split("=", 1)[1].strip()
        return ""

    df["Username"] = df["action"].map(lambda x: parse_detail(x, "username"))
    df["Municipality"] = df["action"].map(lambda x: parse_detail(x, "municipality"))
    return df


def _rhu_account_schema_available(supabase) -> tuple[bool, str]:
    try:
        supabase.table("user_accounts").select(
            "username,assigned_muni,must_change_password"
        ).limit(1).execute()
        return True, ""
    except Exception as exc:
        return False, str(exc)

def _active_admin_mask(accounts: pd.DataFrame) -> pd.Series:
    if accounts.empty:
        return pd.Series(dtype=bool)
    status = accounts["account_status"].fillna("Approved").astype(str).str.strip().str.lower()
    return accounts["role"].eq("System Admin") & status.isin({"approved", "active"})


def _section_heading(icon: str, text: str) -> None:
    st.markdown(
        f"""
        <div class="admin-section-heading">
            <i class="fa-solid {icon}" aria-hidden="true"></i>
            <span>{text}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_kpi_card(icon: str, label: str, value: str, detail: str = "") -> None:
    detail_html = f'<div class="admin-kpi-detail">{detail}</div>' if detail else ""
    st.markdown(
        f"""
        <div class="admin-kpi-card">
            <div class="admin-kpi-top">
                <i class="fa-solid {icon}" aria-hidden="true"></i>
                <span>{label}</span>
            </div>
            <div class="admin-kpi-value">{value}</div>
            {detail_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _format_timestamp(value: object) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw or raw.lower() in {"none", "nan", "not recorded"}:
        return "Not recorded", ""

    parsed = pd.to_datetime(raw, errors="coerce")
    if pd.isna(parsed):
        return raw, ""

    return parsed.strftime("%b %d, %Y"), parsed.strftime("%I:%M %p").lstrip("0")


def _clean_activity(action: object) -> tuple[str, str]:
    raw = str(action or "").strip()
    if raw.lower().startswith("admin:"):
        raw = raw.split(":", 1)[1].strip()

    parts = [part.strip() for part in raw.split("|") if part.strip()]
    if not parts:
        return "", ""

    activity = parts[0]
    detail_labels = {
        "rows": "Rows",
        "exact_duplicates": "Exact duplicates",
        "repeated_ids": "Repeated IDs",
        "username": "Username",
    }

    details = []
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            label = detail_labels.get(key.strip(), key.strip().replace("_", " ").title())
            details.append(f"{label}: {value.strip()}")
        else:
            details.append(part)

    return activity, " · ".join(details)


def _prepare_sia_targets() -> pd.DataFrame:
    conn = st.connection("gsheets", type=GSheetsConnection)

    mr_cols = [
        "Code", "Location", "6-59m_M", "6-59m_F", "6-59m_Total",
        "6-12m_M", "6-12m_F", "6-12m_Total",
        "13-23m_M", "13-23m_F", "13-23m_Total",
        "24-59m_M", "24-59m_F", "24-59m_Total",
    ]
    df_mr_nat = clean_and_process_car_data(
        conn.read(
            spreadsheet=SIA_SHEET_URL,
            worksheet="MR Target(CAR)",
            usecols=list(range(14)),
            skiprows=2,
            names=mr_cols,
            ttl=0,
        ),
        mr_cols,
    )

    mr_act_cols = [
        "Code", "Location", "Act_MR_6-59m_M", "Act_MR_6-59m_F", "Act_MR_6-59m_Total",
        "Act_MR_6-12m_M", "Act_MR_6-12m_F", "Act_MR_6-12m_Total",
        "Act_MR_13-23m_M", "Act_MR_13-23m_F", "Act_MR_13-23m_Total",
        "Act_MR_24-59m_M", "Act_MR_24-59m_F", "Act_MR_24-59m_Total",
    ]
    df_mr_act = clean_and_process_car_data(
        conn.read(
            spreadsheet=SIA_SHEET_URL,
            worksheet="MR Actual Target(UPDATE THIS)",
            usecols=list(range(14)),
            skiprows=2,
            names=mr_act_cols,
            ttl=0,
        ),
        mr_act_cols,
    )

    vita_cols = [
        "Code", "Location", "VitA_6-11m_M", "VitA_6-11m_F", "VitA_6-11m_Total",
        "VitA_12-59m_M", "VitA_12-59m_F", "VitA_12-59m_Total", "VitA_Total",
    ]
    df_vita_nat = clean_and_process_car_data(
        conn.read(
            spreadsheet=SIA_SHEET_URL,
            worksheet="Vitamin A Target",
            usecols=[0, 2, 3, 4, 5, 6, 7, 8, 9],
            skiprows=2,
            names=vita_cols,
            ttl=0,
        ),
        vita_cols,
    )

    vita_act_cols = [
        "Code", "Location", "Act_VitA_6-11m_M", "Act_VitA_6-11m_F", "Act_VitA_6-11m_Total",
        "Act_VitA_12-59m_M", "Act_VitA_12-59m_F", "Act_VitA_12-59m_Total", "Act_VitA_Total",
    ]
    df_vita_act = clean_and_process_car_data(
        conn.read(
            spreadsheet=SIA_SHEET_URL,
            worksheet="Vitamin A Actual Target(UPDATE THIS)",
            usecols=[0, 2, 3, 4, 5, 6, 7, 8, 9],
            skiprows=2,
            names=vita_act_cols,
            ttl=0,
        ),
        vita_act_cols,
    )

    if df_mr_nat.empty:
        raise ValueError("The MR projected target sheet returned no records.")

    for c in ["VitA_6-11m_M", "VitA_12-59m_M", "VitA_6-11m_F", "VitA_12-59m_F"]:
        df_vita_nat[c] = pd.to_numeric(
            df_vita_nat[c].astype(str).str.replace(",", ""), errors="coerce"
        ).fillna(0)
    df_vita_nat["VitA_Total_M"] = df_vita_nat["VitA_6-11m_M"] + df_vita_nat["VitA_12-59m_M"]
    df_vita_nat["VitA_Total_F"] = df_vita_nat["VitA_6-11m_F"] + df_vita_nat["VitA_12-59m_F"]

    for c in ["Act_VitA_6-11m_M", "Act_VitA_12-59m_M", "Act_VitA_6-11m_F", "Act_VitA_12-59m_F"]:
        df_vita_act[c] = pd.to_numeric(
            df_vita_act[c].astype(str).str.replace(",", ""), errors="coerce"
        ).fillna(0)
    df_vita_act["Act_VitA_Total_M"] = df_vita_act["Act_VitA_6-11m_M"] + df_vita_act["Act_VitA_12-59m_M"]
    df_vita_act["Act_VitA_Total_F"] = df_vita_act["Act_VitA_6-11m_F"] + df_vita_act["Act_VitA_12-59m_F"]

    df_merged = df_mr_nat.copy()
    df_merged = pd.merge(
        df_merged,
        df_mr_act[
            [
                "Code", "Act_MR_6-59m_Total", "Act_MR_6-59m_M", "Act_MR_6-59m_F",
                "Act_MR_6-12m_Total", "Act_MR_6-12m_M", "Act_MR_6-12m_F",
                "Act_MR_13-23m_Total", "Act_MR_13-23m_M", "Act_MR_13-23m_F",
                "Act_MR_24-59m_Total", "Act_MR_24-59m_M", "Act_MR_24-59m_F",
            ]
        ],
        on="Code",
        how="left",
    )
    df_merged = pd.merge(
        df_merged,
        df_vita_nat[
            [
                "Code", "VitA_6-11m_Total", "VitA_12-59m_Total", "VitA_Total",
                "VitA_6-11m_M", "VitA_6-11m_F", "VitA_12-59m_M", "VitA_12-59m_F",
                "VitA_Total_M", "VitA_Total_F",
            ]
        ],
        on="Code",
        how="left",
    )
    df_merged = pd.merge(
        df_merged,
        df_vita_act[
            [
                "Code", "Act_VitA_6-11m_Total", "Act_VitA_6-11m_M", "Act_VitA_6-11m_F",
                "Act_VitA_12-59m_Total", "Act_VitA_12-59m_M", "Act_VitA_12-59m_F",
                "Act_VitA_Total", "Act_VitA_Total_M", "Act_VitA_Total_F",
            ]
        ],
        on="Code",
        how="left",
    )

    source_columns = [
        "Code", "Location", "Level", "Parent_Province", "Parent_Municipality",
        "6-59m_Total", "6-12m_Total", "13-23m_Total", "24-59m_Total",
        "6-59m_M", "6-59m_F", "6-12m_M", "6-12m_F", "13-23m_M", "13-23m_F", "24-59m_M", "24-59m_F",
        "VitA_6-11m_Total", "VitA_12-59m_Total", "VitA_Total",
        "VitA_6-11m_M", "VitA_6-11m_F", "VitA_12-59m_M", "VitA_12-59m_F", "VitA_Total_M", "VitA_Total_F",
        "Act_MR_6-59m_Total", "Act_MR_6-12m_Total", "Act_MR_13-23m_Total", "Act_MR_24-59m_Total",
        "Act_VitA_6-11m_Total", "Act_VitA_12-59m_Total", "Act_VitA_Total",
        "Act_MR_6-59m_M", "Act_MR_6-59m_F", "Act_MR_6-12m_M", "Act_MR_6-12m_F",
        "Act_MR_13-23m_M", "Act_MR_13-23m_F", "Act_MR_24-59m_M", "Act_MR_24-59m_F",
        "Act_VitA_6-11m_M", "Act_VitA_6-11m_F", "Act_VitA_12-59m_M", "Act_VitA_12-59m_F",
        "Act_VitA_Total_M", "Act_VitA_Total_F",
    ]
    df_push = df_merged[source_columns].copy()
    df_push.columns = [
        "code", "location", "level", "parent_province", "parent_municipality",
        "grand_total_6_59m", "grand_total_6_12m", "grand_total_13_23m", "grand_total_24_59m",
        "mr_6_59m_m", "mr_6_59m_f", "mr_6_12m_m", "mr_6_12m_f", "mr_13_23m_m", "mr_13_23m_f", "mr_24_59m_m", "mr_24_59m_f",
        "vita_6_11m", "vita_12_59m", "vita_total",
        "vita_6_11m_m", "vita_6_11m_f", "vita_12_59m_m", "vita_12_59m_f", "vita_total_m", "vita_total_f",
        "actual_mr_6_59m_total", "actual_mr_6_12m_total", "actual_mr_13_23m_total", "actual_mr_24_59m_total",
        "actual_vita_6_11m_total", "actual_vita_12_59m_total", "actual_vita_total",
        "actual_mr_6_59m_m", "actual_mr_6_59m_f", "actual_mr_6_12m_m", "actual_mr_6_12m_f",
        "actual_mr_13_23m_m", "actual_mr_13_23m_f", "actual_mr_24_59m_m", "actual_mr_24_59m_f",
        "actual_vita_6_11m_m", "actual_vita_6_11m_f", "actual_vita_12_59m_m", "actual_vita_12_59m_f",
        "actual_vita_total_m", "actual_vita_total_f",
    ]

    numeric_cols = df_push.columns[5:]
    for c in numeric_cols:
        df_push[c] = pd.to_numeric(df_push[c], errors="coerce").fillna(0).astype(int)

    df_push["code"] = df_push["code"].fillna("").astype(str).str.strip()
    if (df_push["code"] == "").any():
        raise ValueError("One or more MR SIA target rows have a blank geographic code.")
    if df_push["code"].duplicated().any():
        raise ValueError("Duplicate geographic codes were detected in the prepared MR SIA targets.")

    return df_push.replace({np.nan: None})


def _sync_sia_targets(supabase) -> int:
    df_push = _prepare_sia_targets()
    records = df_push.to_dict(orient="records")
    if not records:
        raise ValueError("Refusing to sync an empty MR SIA target dataset.")

    # Preserve the established SIA behavior: upsert by the table's configured key.
    for start in range(0, len(records), 500):
        supabase.table("targets").upsert(records[start:start + 500]).execute()
    return len(records)


def _prepare_sbi_targets() -> tuple[pd.DataFrame, dict]:
    conn = st.connection("gsheets", type=GSheetsConnection)
    df_raw = conn.read(
        spreadsheet=SBI_SHEET_URL,
        worksheet="Target by School",
        skiprows=5,
        ttl=0,
    )
    if df_raw.empty:
        raise ValueError("The SBI Target by School sheet returned no records.")

    df_raw.columns = [str(c).strip() for c in df_raw.columns]
    if "Province" in df_raw.columns:
        df_raw = df_raw[df_raw["Province"].astype(str).str.upper() == "ABRA"].copy()

    required_source = {
        "Municipality", "Barangay", "beis_school_id", "School_name",
        "g1male", "g1female", "g4female", "g7male", "g7female",
    }
    missing = sorted(required_source - set(df_raw.columns))
    if missing:
        raise ValueError(f"Missing SBI source columns: {', '.join(missing)}")

    # Canonicalize DepEd naming variants (for example, "Bangued (Capital)")
    # before persisting baseline targets so RHU account assignments match cleanly.
    df_raw["Municipality"] = df_raw["Municipality"].map(canonical_municipality_name)
    df_raw["School_name"] = df_raw["School_name"].astype(str).str.strip()
    df_raw["beis_school_id"] = df_raw["beis_school_id"].astype(str).str.replace(r"\.0$", "", regex=True)

    target_cols = {
        "Municipality": "municipality",
        "Barangay": "barangay",
        "beis_school_id": "school_id",
        "School_name": "school_name",
        "g1male": "g1_male",
        "g1female": "g1_female",
        "g4female": "g4_female",
        "g7male": "g7_male",
        "g7female": "g7_female",
    }
    df_push = df_raw[list(target_cols)].rename(columns=target_cols)

    for c in ["g1_male", "g1_female", "g4_female", "g7_male", "g7_female"]:
        df_push[c] = pd.to_numeric(df_push[c], errors="coerce").fillna(0).astype(int)
    df_push["g1_total"] = df_push["g1_male"] + df_push["g1_female"]
    df_push["g7_total"] = df_push["g7_male"] + df_push["g7_female"]

    df_push, dedupe_report = consolidate_sbi_targets(df_push)
    valid, validation_message = validate_sbi_targets(df_push)
    if not valid:
        raise ValueError(validation_message)

    return df_push.replace({np.nan: None}), dedupe_report


def _sync_sbi_targets(supabase) -> tuple[int, dict]:
    df_push, dedupe_report = _prepare_sbi_targets()
    records = df_push.to_dict(orient="records")
    inserted = replace_table_with_rollback(supabase, "sbi_targets", records)
    return inserted, dedupe_report


def _render_sidebar() -> None:
    with st.sidebar:
        st.markdown(
            f"""
            <div style="text-align:center;padding:10px 0 15px 0;">
                <img src="https://upload.wikimedia.org/wikipedia/commons/1/1a/Abra_provincial_seal.png"
                     width="90" style="margin-bottom:15px;filter:drop-shadow(0 4px 6px rgba(0,0,0,0.1));">
                <h3 style="margin:0;font-size:1.15rem;font-weight:700;">{st.session_state.get('user_name', 'System Admin')}</h3>
                <p style="margin:2px 0 12px 0;font-size:0.85rem;opacity:0.8;font-style:italic;">System Admin</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.divider()
        if st.button("Main Menu", width="stretch", key="admin_main_menu"):
            st.session_state["active_program"] = None
            st.rerun()
        if st.button("Logout", width="stretch", key="admin_logout"):
            _logout_session()


def _render_overview(supabase) -> None:
    accounts = _load_accounts(supabase)
    active_admins = int(_active_admin_mask(accounts).sum()) if not accounts.empty else 0

    try:
        sia_count = _table_row_count(supabase, "targets", "code")
    except Exception:
        sia_count = 0

    try:
        sbi_count = _table_row_count(supabase, "sbi_targets", "school_id")
    except Exception:
        sbi_count = 0

    logs = _load_admin_logs(supabase, 200)
    sync_logs = (
        logs[logs["action"].str.contains("target sync complete", case=False, na=False)]
        if not logs.empty
        else logs
    )
    last_sync_raw = (
        sync_logs.iloc[0]["timestamp"]
        if not sync_logs.empty and "timestamp" in sync_logs.columns
        else "Not recorded"
    )
    last_sync_date, last_sync_time = _format_timestamp(last_sync_raw)

    c1, c2, c3, c4 = st.columns(4, gap="medium")
    with c1:
        _render_kpi_card("fa-user-shield", "Active Admins", f"{active_admins:,}")
    with c2:
        _render_kpi_card("fa-syringe", "MR SIA Target Rows", f"{sia_count:,}")
    with c3:
        _render_kpi_card("fa-school", "SBI School Rows", f"{sbi_count:,}")
    with c4:
        _render_kpi_card("fa-clock-rotate-left", "Last Target Sync", last_sync_date, last_sync_time)

    try:
        login_logs = _load_login_logs(supabase, 1000)
    except Exception:
        login_logs = pd.DataFrame()
    rhu_accounts = int(accounts["role"].eq("RHU Encoder").sum()) if not accounts.empty else 0
    logins_today = 0
    latest_login_name = "Not recorded"
    latest_login_time = ""
    if not login_logs.empty:
        parsed = pd.to_datetime(login_logs.get("timestamp"), errors="coerce")
        logins_today = int((parsed.dt.date == datetime.now(MANILA_TZ).date()).sum())
        latest_login_name = str(login_logs.iloc[0].get("name") or "Unknown")
        _, latest_login_time = _format_timestamp(login_logs.iloc[0].get("timestamp"))
    l1, l2, l3 = st.columns(3, gap="medium")
    with l1:
        _render_kpi_card("fa-hospital-user", "RHU Accounts", f"{rhu_accounts:,}")
    with l2:
        _render_kpi_card("fa-right-to-bracket", "Logins Today", f"{logins_today:,}")
    with l3:
        _render_kpi_card("fa-user-clock", "Latest Login", latest_login_name, latest_login_time)

    _section_heading("fa-database", "Data Status")
    status = pd.DataFrame(
        [
            {
                "Program": "MR SIA",
                "Database": "targets",
                "Rows": sia_count,
                "Status": "Ready" if sia_count else "Empty",
            },
            {
                "Program": "SBI",
                "Database": "sbi_targets",
                "Rows": sbi_count,
                "Status": "Ready" if sbi_count else "Empty",
            },
        ]
    )
    st.dataframe(
        status,
        width="stretch",
        hide_index=True,
        column_config={"Rows": st.column_config.NumberColumn("Rows", format="%d")},
    )

    _section_heading("fa-clock-rotate-left", "Recent Admin Activity")
    if logs.empty:
        st.write("No admin activity has been recorded yet.")
    else:
        recent = logs.head(10).copy()
        recent[["Activity", "Details"]] = recent["action"].apply(
            lambda value: pd.Series(_clean_activity(value))
        )
        recent["Date / Time"] = recent["timestamp"].apply(
            lambda value: " ".join(filter(None, _format_timestamp(value)))
        )
        recent = recent.rename(columns={"name": "Admin"})
        display_cols = ["Date / Time", "Admin", "Activity", "Details"]
        st.dataframe(
            recent[[c for c in display_cols if c in recent.columns]],
            width="stretch",
            hide_index=True,
            column_config={
                "Date / Time": st.column_config.TextColumn("Date / Time", width="medium"),
                "Admin": st.column_config.TextColumn("Admin", width="medium"),
                "Activity": st.column_config.TextColumn("Activity", width="medium"),
                "Details": st.column_config.TextColumn("Details", width="large"),
            },
        )


    _section_heading("fa-right-to-bracket", "Recent Logins")
    if login_logs.empty:
        st.write("No login events have been recorded yet.")
    else:
        recent_logins = login_logs.head(10).copy()
        recent_logins["Date / Time"] = recent_logins["timestamp"].apply(
            lambda value: " ".join(filter(None, _format_timestamp(value)))
        )
        recent_logins = recent_logins.rename(columns={"name": "Name", "role": "Role"})
        recent_cols = ["Date / Time", "Name", "Role", "Municipality"]
        for col in recent_cols:
            if col not in recent_logins.columns:
                recent_logins[col] = ""
        st.dataframe(recent_logins[recent_cols], width="stretch", hide_index=True)


def _render_data_sync(supabase) -> None:
    _section_heading("fa-syringe", "MR SIA Targets")
    try:
        current_sia = _table_row_count(supabase, "targets", "code")
    except Exception:
        current_sia = 0
    st.metric("Current Database Rows", f"{current_sia:,}")

    if st.button("Sync MR SIA Targets", type="primary", width="stretch", key="admin_sync_sia"):
        with st.spinner("Syncing MR SIA targets..."):
            try:
                rows = _sync_sia_targets(supabase)
                _audit(supabase, f"MR SIA target sync complete | rows={rows}")
                st.cache_data.clear()
                st.toast(f"MR SIA targets synced: {rows:,} rows.")
                time.sleep(0.5)
                st.rerun()
            except Exception as exc:
                _audit(supabase, f"MR SIA target sync failed | {type(exc).__name__}")
                st.error(f"MR SIA target sync failed: {exc}")

    st.divider()
    _section_heading("fa-school", "SBI Targets")
    try:
        current_sbi = _table_row_count(supabase, "sbi_targets", "school_id")
    except Exception:
        current_sbi = 0
    st.metric("Current Database Rows", f"{current_sbi:,}")

    if st.button("Sync SBI Targets", type="primary", width="stretch", key="admin_sync_sbi"):
        with st.spinner("Syncing SBI targets..."):
            try:
                rows, report = _sync_sbi_targets(supabase)
                _audit(
                    supabase,
                    "SBI target sync complete | "
                    f"rows={rows} | exact_duplicates={report.get('exact_duplicates_removed', 0)} | "
                    f"repeated_ids={report.get('duplicate_ids_consolidated', 0)}",
                )
                st.cache_data.clear()
                st.toast(f"SBI targets synced: {rows:,} schools.")
                time.sleep(0.5)
                st.rerun()
            except Exception as exc:
                _audit(supabase, f"SBI target sync failed | {type(exc).__name__}")
                st.error(f"SBI target sync failed: {exc}")

    st.divider()
    render_vacctrack_importer(supabase, audit_callback=_audit)

    st.divider()
    _section_heading("fa-clock-rotate-left", "Sync History")
    logs = _load_admin_logs(supabase, 200)
    if logs.empty:
        st.write("No target sync history has been recorded yet.")
    else:
        sync_logs = logs[logs["action"].str.contains("target sync", case=False, na=False)].head(20)
        if sync_logs.empty:
            st.write("No target sync history has been recorded yet.")
        else:
            st.dataframe(sync_logs, width="stretch", hide_index=True)



def _bump_map_label_editor_revision() -> None:
    st.session_state["_map_label_editor_revision"] = int(
        st.session_state.get("_map_label_editor_revision", 0)
    ) + 1
    st.session_state.pop("_map_label_editor_payload", None)



def _build_map_label_editor(records: pd.DataFrame):
    import folium
    from branca.element import MacroElement, Template
    from folium.plugins import Draw
    from streamlit_folium import st_folium

    geojson = fetch_abra_geojson()
    if not geojson:
        st.error("Abra municipality boundary data could not be loaded.")
        return None

    m = folium.Map(
        location=[17.58, 120.80],
        zoom_start=9,
        tiles=None,
        control_scale=True,
        prefer_canvas=False,
    )
    folium.TileLayer(
        tiles=(
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
        ),
        attr="Esri World Light Gray Canvas",
        name="Light Gray",
        overlay=False,
        control=False,
    ).add_to(m)

    folium.GeoJson(
        geojson,
        name="Abra Municipalities",
        style_function=lambda _feature: {
            "fillColor": "#dbeafe",
            "color": "#475569",
            "weight": 1.2,
            "fillOpacity": 0.38,
        },
        highlight_function=lambda _feature: {
            "weight": 2,
            "color": "#0033A0",
            "fillOpacity": 0.50,
        },
    ).add_to(m)

    draw = Draw(
        export=False,
        position="topright",
        draw_options={
            "polyline": False,
            "polygon": False,
            "rectangle": False,
            "circle": False,
            "circlemarker": False,
            "marker": False,
        },
        edit_options={"edit": True, "remove": False},
    )
    draw.add_to(m)

    labels = []
    for _, row in records.iterrows():
        labels.append(
            {
                "key": str(row["municipality_key"]),
                "name": str(row["municipality_name"]),
                "lat": float(row["label_lat"]),
                "lon": float(row["label_lon"]),
            }
        )

    class _EditableLabelLayer(MacroElement):
        def __init__(self, draw_group_var: str, label_rows: list[dict]):
            super().__init__()
            self._name = "EditableLabelLayer"
            self.draw_group_var = draw_group_var
            self.labels_json = json.dumps(label_rows, ensure_ascii=False)
            self._template = Template(
                r'''
                {% macro script(this, kwargs) %}
                (function() {
                    var group = {{ this.draw_group_var|safe }};
                    var labels = {{ this.labels_json|safe }};
                    labels.forEach(function(item) {
                        var safeName = String(item.name)
                            .replace(/&/g, "&amp;")
                            .replace(/</g, "&lt;")
                            .replace(/>/g, "&gt;")
                            .replace(/"/g, "&quot;")
                            .replace(/'/g, "&#039;");
                        var icon = L.divIcon({
                            className: "nip-label-anchor",
                            html: '<div class="nip-label-chip">' + safeName + '</div>',
                            iconSize: [126, 30],
                            iconAnchor: [63, 15]
                        });
                        var marker = L.marker([item.lat, item.lon], {
                            icon: icon,
                            keyboard: true,
                            title: item.name
                        });
                        marker.feature = {
                            type: "Feature",
                            properties: {
                                municipality_key: item.key,
                                municipality_name: item.name
                            }
                        };
                        group.addLayer(marker);
                    });
                })();
                {% endmacro %}
                '''
            )

    _EditableLabelLayer(f"drawnItems_{draw.get_name()}", labels).add_to(m)

    m.get_root().header.add_child(
        folium.Element(
            """
            <style>
            .nip-label-anchor { background: transparent !important; border: 0 !important; }
            .nip-label-chip {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                min-width: 76px;
                padding: 3px 7px;
                border: 1px solid rgba(15, 23, 42, 0.42);
                border-radius: 5px;
                background: rgba(255, 255, 255, 0.90);
                color: #0f172a;
                font: 700 11px/1.15 Arial, sans-serif;
                text-align: center;
                white-space: nowrap;
                box-shadow: 0 1px 3px rgba(15, 23, 42, 0.18);
                cursor: move;
                user-select: none;
            }
            .leaflet-edit-marker-selected .nip-label-chip {
                border-color: #0033A0;
                box-shadow: 0 0 0 2px rgba(0, 51, 160, 0.18);
            }
            </style>
            """
        )
    )

    return st_folium(
        m,
        key=f"nip_municipality_label_editor_map_{st.session_state.get('_map_label_editor_revision', 0)}",
        height=650,
        use_container_width=True,
        returned_objects=["all_drawings", "last_clicked"],
    )


def _positions_from_drawings(drawings) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    for feature in drawings or []:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        key = str(props.get("municipality_key") or "").strip()
        if not key or geometry.get("type") != "Point" or len(coords) < 2:
            continue
        try:
            positions[key] = (float(coords[1]), float(coords[0]))
        except (TypeError, ValueError):
            continue
    return positions


def _merge_pending_label_positions(records: pd.DataFrame) -> pd.DataFrame:
    work = records.copy()
    pending = st.session_state.get("_map_label_pending", {})
    if not pending or work.empty:
        return work

    for key, position in pending.items():
        mask = work["municipality_key"].eq(key)
        if not mask.any():
            continue
        lat, lon = position
        work.loc[mask, "label_lat"] = float(lat)
        work.loc[mask, "label_lon"] = float(lon)
        work.loc[mask, "lat_nudge"] = float(lat) - work.loc[mask, "centroid_lat"]
        work.loc[mask, "lon_nudge"] = float(lon) - work.loc[mask, "centroid_lon"]
        work.loc[mask, "source"] = "Unsaved"
    return work


def _render_map_label_editor(supabase) -> None:
    _section_heading("fa-map-location-dot", "Municipality Label Editor")

    table_ready = map_label_table_available(supabase)
    if not table_ready:
        st.error(
            "Map label storage is not configured yet. Run "
            "supabase/001_map_label_positions.sql in the Supabase SQL Editor, then reload this page."
        )

    base_records = build_label_records(supabase)
    if base_records.empty:
        st.error("Abra municipality label positions could not be prepared.")
        return

    effective = _merge_pending_label_positions(base_records)

    st.markdown(
        """
        <div style="margin:0.1rem 0 0.8rem 0;color:#475569;font-size:0.92rem;">
            Use the map's <strong>Edit layers</strong> tool, drag the municipality labels, then click
            <strong>Save</strong> in the map toolbar. The exact coordinates and centroid nudges are calculated automatically.
        </div>
        """,
        unsafe_allow_html=True,
    )

    map_state = _build_map_label_editor(effective)
    if map_state:
        moved = _positions_from_drawings(map_state.get("all_drawings"))
        if moved:
            payload = "|".join(
                f"{key}:{lat:.8f}:{lon:.8f}" for key, (lat, lon) in sorted(moved.items())
            )
            payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if st.session_state.get("_map_label_editor_payload") != payload_hash:
                pending = dict(st.session_state.get("_map_label_pending", {}))
                current_by_key = effective.set_index("municipality_key")
                for key, (lat, lon) in moved.items():
                    if key not in current_by_key.index:
                        continue
                    current = current_by_key.loc[key]
                    if isinstance(current, pd.DataFrame):
                        current = current.iloc[0]
                    if (
                        abs(float(current["label_lat"]) - lat) > 1e-7
                        or abs(float(current["label_lon"]) - lon) > 1e-7
                    ):
                        pending[key] = (lat, lon)
                st.session_state["_map_label_pending"] = pending
                st.session_state["_map_label_editor_payload"] = payload_hash
                _bump_map_label_editor_revision()
                st.rerun()

    effective = _merge_pending_label_positions(base_records)
    pending = st.session_state.get("_map_label_pending", {})

    if pending:
        st.markdown(
            f'<div style="font-weight:700;color:#0033A0;margin:0.35rem 0 0.5rem 0;">'
            f'{len(pending)} unsaved label change{"s" if len(pending) != 1 else ""}</div>',
            unsafe_allow_html=True,
        )

    names = effective["municipality_name"].tolist()
    selected_name = st.selectbox("Fine-tune municipality", names, key="map_label_selected_municipality")
    selected = effective[effective["municipality_name"].eq(selected_name)].iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Label Latitude", f"{float(selected['label_lat']):.6f}")
    c2.metric("Label Longitude", f"{float(selected['label_lon']):.6f}")
    c3.metric("Latitude Nudge", f"{float(selected['lat_nudge']):+.6f}")
    c4.metric("Longitude Nudge", f"{float(selected['lon_nudge']):+.6f}")

    edit1, edit2, action1, action2 = st.columns([1.25, 1.25, 1, 1])
    with edit1:
        manual_lat = st.number_input(
            "Exact Latitude",
            value=float(selected["label_lat"]),
            format="%.6f",
            step=0.001,
            key=f"map_label_lat_{selected['municipality_key']}_{st.session_state.get('_map_label_editor_revision', 0)}",
        )
    with edit2:
        manual_lon = st.number_input(
            "Exact Longitude",
            value=float(selected["label_lon"]),
            format="%.6f",
            step=0.001,
            key=f"map_label_lon_{selected['municipality_key']}_{st.session_state.get('_map_label_editor_revision', 0)}",
        )
    with action1:
        st.markdown("<div style='height:1.72rem'></div>", unsafe_allow_html=True)
        if st.button("Apply Coordinates", width="stretch", key="map_label_apply_manual"):
            pending = dict(st.session_state.get("_map_label_pending", {}))
            pending[str(selected["municipality_key"])] = (float(manual_lat), float(manual_lon))
            st.session_state["_map_label_pending"] = pending
            _bump_map_label_editor_revision()
            st.rerun()
    with action2:
        st.markdown("<div style='height:1.72rem'></div>", unsafe_allow_html=True)
        if st.button(
            "Reset Selected",
            width="stretch",
            disabled=not table_ready,
            key="map_label_reset_selected",
        ):
            reset_label_position(supabase, str(selected["municipality_key"]))
            pending = dict(st.session_state.get("_map_label_pending", {}))
            pending.pop(str(selected["municipality_key"]), None)
            st.session_state["_map_label_pending"] = pending
            _bump_map_label_editor_revision()
            _audit(supabase, f"Map label reset | municipality={selected_name}")
            st.rerun()

    last_clicked = map_state.get("last_clicked") if map_state else None
    if last_clicked and isinstance(last_clicked, dict):
        click_col, use_col = st.columns([2.6, 1])
        with click_col:
            st.markdown(
                f"Last map click: `{float(last_clicked.get('lat', 0)):.6f}, "
                f"{float(last_clicked.get('lng', 0)):.6f}`"
            )
        with use_col:
            if st.button("Use Click Position", width="stretch", key="map_label_use_click"):
                pending = dict(st.session_state.get("_map_label_pending", {}))
                pending[str(selected["municipality_key"])] = (
                    float(last_clicked["lat"]),
                    float(last_clicked["lng"]),
                )
                st.session_state["_map_label_pending"] = pending
                _bump_map_label_editor_revision()
                st.rerun()

    save_col, discard_col, reset_col = st.columns([1.2, 1.0, 1.0])
    with save_col:
        if st.button(
            "Save All Changes",
            type="primary",
            width="stretch",
            disabled=(not table_ready or not pending),
            key="map_label_save_all",
        ):
            final_records = _merge_pending_label_positions(base_records)
            changed = final_records[final_records["municipality_key"].isin(pending.keys())].copy()
            save_label_records(
                supabase,
                changed,
                st.session_state.get("user_name") or st.session_state.get("username") or "System Admin",
            )
            _audit(supabase, f"Map label positions saved | municipalities={len(changed)}")
            st.session_state.pop("_map_label_pending", None)
            st.session_state.pop("_map_label_editor_payload", None)
            invalidate_runtime_label_cache()
            _bump_map_label_editor_revision()
            st.toast(f"Saved {len(changed)} municipality label position(s).")
            st.rerun()
    with discard_col:
        if st.button(
            "Discard Changes",
            width="stretch",
            disabled=not pending,
            key="map_label_discard",
        ):
            st.session_state.pop("_map_label_pending", None)
            st.session_state.pop("_map_label_editor_payload", None)
            _bump_map_label_editor_revision()
            st.rerun()
    with reset_col:
        if st.button(
            "Reset All Defaults",
            width="stretch",
            disabled=not table_ready,
            key="map_label_reset_all",
        ):
            st.session_state["_map_label_confirm_reset_all"] = True

    if st.session_state.get("_map_label_confirm_reset_all"):
        confirm1, confirm2 = st.columns(2)
        with confirm1:
            if st.button("Confirm Reset All", type="primary", width="stretch", key="map_label_confirm_reset"):
                reset_all_label_positions(supabase)
                st.session_state.pop("_map_label_pending", None)
                st.session_state.pop("_map_label_editor_payload", None)
                st.session_state.pop("_map_label_confirm_reset_all", None)
                _bump_map_label_editor_revision()
                _audit(supabase, "All map label positions reset to defaults")
                st.rerun()
        with confirm2:
            if st.button("Cancel", width="stretch", key="map_label_cancel_reset"):
                st.session_state.pop("_map_label_confirm_reset_all", None)
                st.rerun()

    st.divider()
    _section_heading("fa-location-crosshairs", "Current Label Coordinates")
    display = effective[
        [
            "municipality_name",
            "label_lat",
            "label_lon",
            "lat_nudge",
            "lon_nudge",
            "source",
        ]
    ].copy()
    display.columns = [
        "Municipality",
        "Label Lat",
        "Label Lon",
        "Lat Nudge",
        "Lon Nudge",
        "Source",
    ]
    for col in ["Label Lat", "Label Lon", "Lat Nudge", "Lon Nudge"]:
        display[col] = pd.to_numeric(display[col], errors="coerce").round(6)
    st.dataframe(display, width="stretch", hide_index=True)



def _render_rhu_accounts(supabase) -> None:
    ready, message = _rhu_account_schema_available(supabase)
    if not ready:
        st.error(
            f"RHU Encoder setup is not complete ({message}). Run the required RHU account migrations, including supabase/008_user_password_change.sql, then reload this page."
        )
        return

    accounts = _load_accounts(supabase)
    if accounts.empty:
        rhu_df = pd.DataFrame(columns=["name", "username", "assigned_muni", "account_status"])
    else:
        rhu_df = accounts[accounts["role"].eq("RHU Encoder")].copy()

    _section_heading("fa-users-gear", "RHU Encoder Accounts")
    if not rhu_df.empty:
        display = rhu_df[["name", "username", "assigned_muni", "account_status", "must_change_password"]].rename(
            columns={
                "name": "Name",
                "username": "Username",
                "assigned_muni": "Municipality",
                "account_status": "Status",
                "must_change_password": "Password Change Required",
            }
        )
        display["Password Change Required"] = display["Password Change Required"].map(
            lambda value: "Yes" if bool(value) else "No"
        )
        try:
            login_logs = _load_login_logs(supabase, 1000)
            if not login_logs.empty and "Username" in login_logs.columns:
                latest = login_logs[login_logs["Username"].astype(str).str.strip().ne("")].drop_duplicates("Username", keep="first")
                latest = latest[["Username", "timestamp"]].rename(columns={"timestamp": "Last Login"})
                display = display.merge(latest, on="Username", how="left")
        except Exception:
            display["Last Login"] = ""
        st.dataframe(display.sort_values(["Municipality", "Username"]), width="stretch", hide_index=True)
    else:
        st.write("No RHU Encoder accounts are configured yet.")

    _section_heading("fa-user-plus", "Create RHU Encoder")
    with st.form("rhu_create_account_form"):
        c1, c2 = st.columns(2)
        with c1:
            new_name = st.text_input("Display Name", placeholder="e.g., La Paz RHU")
            new_username = st.text_input("Username", key="rhu_new_username")
        with c2:
            new_muni = st.selectbox("Assigned Municipality", ABRA_MUNIS, key="rhu_new_muni")
            new_password = st.text_input("Temporary Password", type="password", key="rhu_new_password")
        create_rhu = st.form_submit_button("Create RHU Encoder", type="primary")

    if create_rhu:
        username = new_username.strip()
        display_name = new_name.strip() or f"{new_muni} RHU"
        if not username or not new_password:
            st.error("Username and password are required.")
        elif len(new_password) < 8:
            st.error("Use a password with at least 8 characters.")
        else:
            existing = (
                supabase.table("user_accounts")
                .select("username")
                .eq("username", username)
                .limit(1)
                .execute()
            )
            if existing.data:
                st.error("That username already exists.")
            else:
                supabase.table("user_accounts").insert(
                    {
                        "username": username,
                        "password_hash": hash_password(new_password),
                        "name": display_name,
                        "role": "RHU Encoder",
                        "assigned_muni": new_muni,
                        "account_status": "Approved",
                        "failed_attempts": 0,
                        "must_change_password": True,
                    }
                ).execute()
                _audit(supabase, f"RHU Encoder created | username={username} | municipality={new_muni}")
                st.toast(f"RHU Encoder created: {username}")
                st.rerun()

    if rhu_df.empty:
        return

    usernames = sorted(rhu_df["username"].dropna().astype(str).tolist())

    st.divider()
    _section_heading("fa-location-dot", "Municipality Assignment")
    with st.form("rhu_assignment_form"):
        assign_username = st.selectbox("RHU Account", usernames, key="rhu_assign_username")
        current_row = rhu_df[rhu_df["username"].eq(assign_username)].iloc[0]
        current_muni = str(current_row.get("assigned_muni") or ABRA_MUNIS[0]).strip()
        current_index = ABRA_MUNIS.index(current_muni) if current_muni in ABRA_MUNIS else 0
        assign_muni = st.selectbox("Assigned Municipality", ABRA_MUNIS, index=current_index, key="rhu_assign_muni")
        assign_submit = st.form_submit_button("Update Assignment")
    if assign_submit:
        supabase.table("user_accounts").update({"assigned_muni": assign_muni}).eq("username", assign_username).execute()
        _audit(supabase, f"RHU assignment updated | username={assign_username} | municipality={assign_muni}")
        st.toast(f"Updated {assign_username} to {assign_muni}.")
        st.rerun()

    st.divider()
    _section_heading("fa-key", "Reset RHU Password")
    with st.form("rhu_reset_password_form"):
        reset_username = st.selectbox("RHU Account", usernames, key="rhu_reset_username")
        reset_password = st.text_input("Temporary Password", type="password", key="rhu_reset_password")
        confirm_password = st.text_input("Confirm Temporary Password", type="password", key="rhu_reset_confirm")
        reset_submit = st.form_submit_button("Reset Password")
    if reset_submit:
        if len(reset_password) < 8:
            st.error("Use a password with at least 8 characters.")
        elif reset_password != confirm_password:
            st.error("The passwords do not match.")
        else:
            supabase.table("user_accounts").update(
                {
                    "password_hash": hash_password(reset_password),
                    "failed_attempts": 0,
                    "must_change_password": True,
                }
            ).eq("username", reset_username).execute()
            _audit(supabase, f"RHU password reset | username={reset_username}")
            st.toast(f"Temporary password set for {reset_username}. A password change will be required at the next login.")
            st.rerun()

    st.divider()
    _section_heading("fa-user-lock", "RHU Account Status")
    action_username = st.selectbox("RHU Account", usernames, key="rhu_status_username")
    selected_row = rhu_df[rhu_df["username"].eq(action_username)].iloc[0]
    selected_status = str(selected_row.get("account_status") or "Approved").strip()
    selected_active = selected_status.lower() in {"approved", "active"}
    e1, e2 = st.columns(2)
    with e1:
        if st.button("Enable Account", width="stretch", disabled=selected_active, key="rhu_enable_account"):
            supabase.table("user_accounts").update(
                {"account_status": "Approved", "failed_attempts": 0}
            ).eq("username", action_username).execute()
            _audit(supabase, f"RHU account enabled | username={action_username}")
            st.rerun()
    with e2:
        if st.button("Disable Account", width="stretch", disabled=not selected_active, key="rhu_disable_account"):
            supabase.table("user_accounts").update(
                {"account_status": "Disabled"}
            ).eq("username", action_username).execute()
            _audit(supabase, f"RHU account disabled | username={action_username}")
            st.rerun()

    _section_heading("fa-user-xmark", "Delete RHU Account")
    delete_confirm = st.text_input("Type the RHU username to confirm deletion", key="rhu_delete_confirm")
    if st.button("Delete RHU Account", type="secondary", key="rhu_delete_account"):
        if delete_confirm.strip() != action_username:
            st.error("The confirmation username does not match.")
        else:
            supabase.table("user_accounts").delete().eq("username", action_username).execute()
            _audit(supabase, f"RHU account deleted | username={action_username}")
            st.toast(f"Deleted {action_username}.")
            st.rerun()

def _render_admin_accounts(supabase) -> None:
    accounts = _load_accounts(supabase)
    current_username = str(st.session_state.get("username") or "").strip()

    if accounts.empty:
        admin_df = pd.DataFrame(columns=["name", "username", "account_status"])
    else:
        admin_df = accounts[accounts["role"].eq("System Admin")].copy()

    if not admin_df.empty:
        admin_df["Current"] = admin_df["username"].eq(current_username)
        display = admin_df[["name", "username", "account_status", "Current"]].rename(
            columns={"name": "Name", "username": "Username", "account_status": "Status"}
        )
        st.dataframe(display, width="stretch", hide_index=True)
    else:
        st.write("No System Admin accounts were returned by the database.")

    active_admin_count = int(_active_admin_mask(accounts).sum()) if not accounts.empty else 0

    _section_heading("fa-user-plus", "Add Backup Admin")
    if active_admin_count >= 2:
        st.write("Two active System Admin accounts are already configured.")
        create_admin = False
        new_name = new_username = new_password = ""
    else:
        with st.form("admin_add_backup_form"):
            new_name = st.text_input("Display Name")
            new_username = st.text_input("Username")
            new_password = st.text_input("Temporary Password", type="password")
            create_admin = st.form_submit_button("Create Admin Account", type="primary")

    if create_admin:
        username = new_username.strip()
        display_name = new_name.strip() or username
        if not username or not new_password:
            st.error("Username and password are required.")
        elif len(new_password) < 8:
            st.error("Use a password with at least 8 characters.")
        else:
            existing = (
                supabase.table("user_accounts")
                .select("username")
                .eq("username", username)
                .limit(1)
                .execute()
            )
            if existing.data:
                st.error("That username already exists.")
            else:
                supabase.table("user_accounts").insert(
                    {
                        "username": username,
                        "password_hash": hash_password(new_password),
                        "name": display_name,
                        "role": "System Admin",
                        "account_status": "Approved",
                        "failed_attempts": 0,
                    }
                ).execute()
                _audit(supabase, f"Admin account created | username={username}")
                st.toast(f"Admin account created: {username}")
                st.rerun()

    if admin_df.empty:
        return

    usernames = admin_df["username"].tolist()

    st.divider()
    _section_heading("fa-key", "Reset Password")
    with st.form("admin_reset_password_form"):
        reset_username = st.selectbox("Admin Account", usernames, key="admin_reset_username")
        reset_password = st.text_input("New Password", type="password", key="admin_reset_password")
        confirm_password = st.text_input("Confirm New Password", type="password", key="admin_reset_confirm")
        reset_submit = st.form_submit_button("Reset Password")

    if reset_submit:
        if len(reset_password) < 8:
            st.error("Use a password with at least 8 characters.")
        elif reset_password != confirm_password:
            st.error("The passwords do not match.")
        else:
            supabase.table("user_accounts").update(
                {"password_hash": hash_password(reset_password), "failed_attempts": 0}
            ).eq("username", reset_username).execute()
            _audit(supabase, f"Password reset | username={reset_username}")
            st.toast(f"Password reset for {reset_username}.")

    st.divider()
    _section_heading("fa-user-shield", "Account Status")
    action_username = st.selectbox("Admin Account", usernames, key="admin_status_username")
    selected_row = admin_df[admin_df["username"].eq(action_username)].iloc[0]
    selected_status = str(selected_row.get("account_status") or "Approved").strip()
    selected_is_active = selected_status.lower() in {"approved", "active"}
    active_count = int(_active_admin_mask(accounts).sum())

    col_enable, col_disable = st.columns(2)
    with col_enable:
        enable_blocked = selected_is_active or active_count >= 2
        if st.button("Enable Account", width="stretch", disabled=enable_blocked, key="admin_enable_account"):
            supabase.table("user_accounts").update(
                {"account_status": "Approved", "failed_attempts": 0}
            ).eq("username", action_username).execute()
            _audit(supabase, f"Admin account enabled | username={action_username}")
            st.toast(f"Enabled {action_username}.")
            st.rerun()

    with col_disable:
        disable_blocked = action_username == current_username or (selected_is_active and active_count <= 1)
        if st.button("Disable Account", width="stretch", disabled=disable_blocked, key="admin_disable_account"):
            supabase.table("user_accounts").update(
                {"account_status": "Disabled"}
            ).eq("username", action_username).execute()
            _audit(supabase, f"Admin account disabled | username={action_username}")
            st.toast(f"Disabled {action_username}.")
            st.rerun()

    _section_heading("fa-user-xmark", "Delete Admin Account")
    delete_confirm = st.text_input(
        "Type the username to confirm deletion",
        key="admin_delete_confirm",
    )
    delete_blocked = action_username == current_username or (selected_is_active and active_count <= 1)
    if st.button("Delete Admin Account", type="secondary", disabled=delete_blocked, key="admin_delete_account"):
        if delete_confirm.strip() != action_username:
            st.error("The confirmation username does not match.")
        else:
            supabase.table("user_accounts").delete().eq("username", action_username).execute()
            _audit(supabase, f"Admin account deleted | username={action_username}")
            st.toast(f"Deleted {action_username}.")
            st.rerun()


def _render_login_history(supabase) -> None:
    logs = _load_login_logs(supabase, 500)
    if logs.empty:
        st.write("No login events have been recorded yet.")
        return

    view = logs.copy()
    view["Date / Time"] = view["timestamp"].apply(lambda value: " ".join(filter(None, _format_timestamp(value))))
    view = view.rename(columns={"name": "Name", "role": "Role"})
    cols = ["Date / Time", "Name", "Role", "Username", "Municipality"]
    for col in cols:
        if col not in view.columns:
            view[col] = ""
    st.dataframe(view[cols], width="stretch", hide_index=True)
    st.download_button(
        "Download Login History (CSV)",
        data=view[cols].to_csv(index=False).encode("utf-8-sig"),
        file_name="Abra_NIP_Login_History.csv",
        mime="text/csv",
        key="admin_login_history_download",
    )


def _render_audit_log(supabase) -> None:
    logs = _load_admin_logs(supabase, 300)
    if logs.empty:
        st.write("No admin activity has been recorded yet.")
        return

    st.dataframe(logs, width="stretch", hide_index=True)
    st.download_button(
        "Download Audit Log (CSV)",
        data=logs.to_csv(index=False).encode("utf-8-sig"),
        file_name="Abra_NIP_Admin_Audit_Log.csv",
        mime="text/csv",
        key="admin_audit_download",
    )


def render_admin_dashboard(supabase) -> None:
    if st.session_state.get("user_role") != "System Admin":
        st.session_state["active_program"] = None
        st.rerun()

    _render_sidebar()

    st.markdown(
        """
        <style>
        .admin-page-title {
            display: flex;
            align-items: center;
            gap: 0.8rem;
            margin: 0.15rem 0 1.25rem 0;
        }
        .admin-page-title i {
            color: #0033A0;
            font-size: 1.9rem;
        }
        .admin-page-title h1 {
            margin: 0;
            font-size: clamp(2rem, 3vw, 2.8rem);
            line-height: 1.05;
            font-weight: 800;
            letter-spacing: -0.03em;
        }
        .admin-kpi-card {
            min-height: 138px;
            background: #ffffff;
            border: 1px solid #dfe5ee;
            border-bottom: 5px solid #0033A0;
            border-radius: 10px;
            padding: 1rem 1.05rem 0.9rem 1.05rem;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.06);
            overflow: hidden;
        }
        .admin-kpi-top {
            display: flex;
            align-items: center;
            gap: 0.55rem;
            color: #334155;
            font-size: 0.91rem;
            font-weight: 700;
            white-space: nowrap;
        }
        .admin-kpi-top i {
            color: #0033A0;
            width: 1.1rem;
            text-align: center;
        }
        .admin-kpi-value {
            color: #0033A0;
            font-size: clamp(1.7rem, 2.3vw, 2.35rem);
            line-height: 1.05;
            font-weight: 800;
            margin-top: 0.8rem;
            overflow-wrap: anywhere;
        }
        .admin-kpi-detail {
            color: #64748b;
            font-size: 0.88rem;
            font-weight: 600;
            margin-top: 0.35rem;
        }
        .admin-section-heading {
            display: flex;
            align-items: center;
            gap: 0.6rem;
            margin: 1.55rem 0 0.8rem 0;
            color: #1e293b;
            font-size: 1.35rem;
            font-weight: 750;
        }
        .admin-section-heading i {
            color: #0033A0;
            width: 1.35rem;
            text-align: center;
        }
        @media (max-width: 900px) {
            .admin-kpi-top {
                white-space: normal;
            }
            .admin-kpi-value {
                font-size: 1.55rem;
            }
            .admin-page-title {
                margin-top: 0;
            }
            .admin-page-title h1 {
                font-size: 1.9rem;
            }
            .admin-kpi-card {
                min-height: 118px;
            }
        }
        </style>

        <div class="admin-page-title">
            <i class="fa-solid fa-shield-halved" aria-hidden="true"></i>
            <h1>System Administration</h1>
        </div>
        """,
        unsafe_allow_html=True,
    )

    overview_tab, operations_tab, sync_tab, imports_tab, map_tab, rhu_accounts_tab, accounts_tab, login_tab, audit_tab = st.tabs(
        ["Overview", "Operations", "Data Sync", "Import Management", "Map Labels", "RHU Accounts", "Admin Accounts", "Login History", "Audit Log"]
    )

    with overview_tab:
        _render_overview(supabase)

    with operations_tab:
        render_operations(supabase, audit_callback=_audit)

    with sync_tab:
        _render_data_sync(supabase)

    with imports_tab:
        render_import_management(supabase, audit_callback=_audit)

    with map_tab:
        _render_map_label_editor(supabase)

    with rhu_accounts_tab:
        _render_rhu_accounts(supabase)

    with accounts_tab:
        _render_admin_accounts(supabase)

    with login_tab:
        _section_heading("fa-right-to-bracket", "Login History")
        _render_login_history(supabase)

    with audit_tab:
        _section_heading("fa-clipboard-list", "Audit Log")
        _render_audit_log(supabase)
