"""Streamlit UI for SBI pre-activity vaccine requirement planning."""
from __future__ import annotations

from datetime import datetime
import math

import numpy as np
import pandas as pd
import pytz
import streamlit as st

from core.config import ABRA_MUNIS
from core.map_labels import canonical_municipality_name, normalize_municipality_key
from core.vaccine_requirements import (
    CAMPAIGN_YEAR,
    PRIOR_DISPLAY_COLUMNS,
    aggregate_requirement_by_municipality,
    build_school_requirement_frame,
    fetch_prior_vaccination,
    fetch_submission_status,
    municipality_submission_record,
    requirements_schema_available,
    save_requirement_rows,
    set_submission_status,
    submission_state,
    submitted_municipality_keys,
    target_signature,
    validate_requirement_frame,
)
from programs.sbi.reporting import render_municipality_choropleth


MANILA_TZ = pytz.timezone("Asia/Manila")
STRONG_BLUE_SCALE = [
    [0.00, "#b7d4f4"],
    [0.25, "#78aee3"],
    [0.50, "#3f83c5"],
    [0.75, "#145da0"],
    [1.00, "#073763"],
]


def _heading(icon: str, text: str, level: int = 3) -> None:
    size = {3: "1.45rem", 4: "1.2rem"}.get(level, "1.1rem")
    st.markdown(
        f'''<div style="display:flex;align-items:center;gap:.55rem;margin:.45rem 0 .8rem 0;"
             ><i class="fa-solid {icon}" style="color:#0033A0;width:1.2rem;text-align:center;"></i>
             <span style="font-size:{size};font-weight:750;color:#1e293b;">{text}</span></div>''',
        unsafe_allow_html=True,
    )


def _audit(supabase, action: str) -> None:
    try:
        timestamp = datetime.now(MANILA_TZ).strftime("%Y-%m-%d %I:%M:%S %p")
        role = st.session_state.get("user_role") or "User"
        prefix = "Admin: SBI Requirements" if role == "System Admin" else "SBI Requirements"
        supabase.table("access_logs").insert(
            {
                "timestamp": timestamp,
                "name": st.session_state.get("user_name") or st.session_state.get("username") or "User",
                "role": role,
                "action": f"{prefix}: {action}",
            }
        ).execute()
    except Exception:
        pass


def _sum_metric(frame: pd.DataFrame, col: str) -> int:
    if frame is None or frame.empty or col not in frame.columns:
        return 0
    return int(pd.to_numeric(frame[col], errors="coerce").fillna(0).sum())


def _build_status_table(
    actual_targets: pd.DataFrame,
    all_frame: pd.DataFrame,
    submissions: pd.DataFrame,
) -> pd.DataFrame:
    agg = aggregate_requirement_by_municipality(all_frame)
    if agg.empty:
        agg = pd.DataFrame(columns=["Municipality"])
    agg["_key"] = agg.get("Municipality", pd.Series(dtype=str)).map(normalize_municipality_key)
    by_key = agg.set_index("_key") if not agg.empty else pd.DataFrame()

    rows = []
    for municipality in ABRA_MUNIS:
        name = canonical_municipality_name(municipality)
        key = normalize_municipality_key(name)
        status, stale = submission_state(submissions, actual_targets, name)
        submission = municipality_submission_record(submissions, name)
        submitted_at_raw = submission.get("submitted_at") if submission else None
        submitted_at = ""
        if submitted_at_raw:
            parsed = pd.to_datetime(submitted_at_raw, errors="coerce", utc=True)
            if pd.notna(parsed):
                submitted_at = parsed.tz_convert(MANILA_TZ).strftime("%b %d, %Y %I:%M %p")
            else:
                submitted_at = str(submitted_at_raw)
        source = by_key.loc[key] if not agg.empty and key in by_key.index else None
        if isinstance(source, pd.DataFrame):
            source = source.iloc[0]
        row = {
            "Municipality": name,
            "Status": status,
            "Schools": int(source.get("Schools", 0)) if source is not None else 0,
            "Complete Schools": int(source.get("Complete_Schools", 0)) if source is not None else 0,
            "Completion %": float(source.get("Completion %", 0) or 0) if source is not None else 0.0,
            "MR Remaining": source.get("MR Remaining", np.nan) if source is not None else np.nan,
            "Td Remaining": source.get("Td Remaining", np.nan) if source is not None else np.nan,
            "HPV Dose 1 Remaining": source.get("G4 HPV Dose 1 Remaining", np.nan) if source is not None else np.nan,
            "HPV Dose 2 Pending": source.get("G4 HPV Dose 2 Pending", np.nan) if source is not None else np.nan,
            "Submitted By": str(submission.get("submitted_by") or "") if submission else "",
            "Submitted At": submitted_at,
            "Target Changed": bool(stale),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def _editor_to_recalculated(
    actual_muni: pd.DataFrame,
    edited: pd.DataFrame,
) -> pd.DataFrame:
    prior = pd.DataFrame({"school_id": edited["School ID"].astype(str)})
    for db_col, display_col in PRIOR_DISPLAY_COLUMNS.items():
        prior[db_col] = pd.to_numeric(edited[display_col], errors="coerce")
    return build_school_requirement_frame(actual_muni, prior)


def _editable_frame(frame: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "Barangay",
        "School ID",
        "School Name",
        "Target Entry Status",
        "G1 Total",
        "G1 MR Prior",
        "G1 Td Prior",
        "G4 Female",
        "G4 HPV Dose 1 Prior",
        "G4 HPV Dose 2 Prior",
        "G7 Total",
        "G7 MR Prior",
        "G7 Td Prior",
    ]
    available = [c for c in cols if c in frame.columns]
    return frame[available].copy()


def _render_local_metrics(frame: pd.DataFrame) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("MR Remaining", f"{_sum_metric(frame, 'MR Remaining'):,}")
    c2.metric("Td Remaining", f"{_sum_metric(frame, 'Td Remaining'):,}")
    c3.metric("HPV Dose 1 Remaining", f"{_sum_metric(frame, 'G4 HPV Dose 1 Remaining'):,}")
    c4.metric("HPV Dose 2 Pending", f"{_sum_metric(frame, 'G4 HPV Dose 2 Pending'):,}")


def _render_supply_planning(official_frame: pd.DataFrame) -> None:
    _heading("fa-boxes-stacked", "Supply Planning", 4)
    st.write("Apply the NIP coordinator's approved planning allowance. A 0% allowance shows the raw remaining eligible doses.")

    a1, a2, a3, a4 = st.columns(4)
    with a1:
        mr_allow = st.number_input("MR allowance (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.5, key="req_mr_allow")
    with a2:
        td_allow = st.number_input("Td allowance (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.5, key="req_td_allow")
    with a3:
        hpv1_allow = st.number_input("HPV Dose 1 allowance (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.5, key="req_hpv1_allow")
    with a4:
        hpv2_allow = st.number_input("HPV Dose 2 allowance (%)", min_value=0.0, max_value=100.0, value=0.0, step=0.5, key="req_hpv2_allow")

    raw = {
        "MR": _sum_metric(official_frame, "MR Remaining"),
        "Td": _sum_metric(official_frame, "Td Remaining"),
        "HPV Dose 1": _sum_metric(official_frame, "G4 HPV Dose 1 Remaining"),
        "HPV Dose 2": _sum_metric(official_frame, "G4 HPV Dose 2 Pending"),
    }
    allowances = {
        "MR": mr_allow,
        "Td": td_allow,
        "HPV Dose 1": hpv1_allow,
        "HPV Dose 2": hpv2_allow,
    }
    plan = pd.DataFrame(
        [
            {
                "Vaccine / Dose": label,
                "Raw Remaining Doses": value,
                "Allowance %": allowances[label],
                "Planned Doses": int(math.ceil(value * (1 + allowances[label] / 100.0))),
            }
            for label, value in raw.items()
        ]
    )
    st.dataframe(plan, width="stretch", hide_index=True)


def _render_province_view(
    supabase,
    actual_targets: pd.DataFrame,
    all_frame: pd.DataFrame,
    submissions: pd.DataFrame,
    user_role: str,
) -> None:
    status_table = _build_status_table(actual_targets, all_frame, submissions)
    submitted_keys = submitted_municipality_keys(submissions, actual_targets, ABRA_MUNIS)

    official_frame = all_frame.copy()
    official_frame["_key"] = official_frame["Municipality"].map(normalize_municipality_key)
    official_frame = official_frame[official_frame["_key"].isin(submitted_keys)].drop(columns=["_key"])

    submitted_count = int(status_table["Status"].eq("Submitted").sum())
    review_count = int(status_table["Status"].eq("Needs Review").sum())

    c0, c1, c2, c3, c4 = st.columns(5)
    c0.metric("Submitted RHUs", f"{submitted_count} / {len(ABRA_MUNIS)}")
    c1.metric("MR Remaining", f"{_sum_metric(official_frame, 'MR Remaining'):,}")
    c2.metric("Td Remaining", f"{_sum_metric(official_frame, 'Td Remaining'):,}")
    c3.metric("HPV Dose 1 Remaining", f"{_sum_metric(official_frame, 'G4 HPV Dose 1 Remaining'):,}")
    c4.metric("HPV Dose 2 Pending", f"{_sum_metric(official_frame, 'G4 HPV Dose 2 Pending'):,}")

    if submitted_count < len(ABRA_MUNIS) or review_count:
        st.warning(
            "Province totals include only municipalities with a current Submitted record. "
            "Draft, Not Started, and Needs Review municipalities are excluded rather than treated as zero."
        )

    _heading("fa-list-check", "RHU Submission Status", 4)
    display_status = status_table.drop(columns=["Target Changed"], errors="ignore")
    st.dataframe(
        display_status,
        width="stretch",
        hide_index=True,
        column_config={
            "Completion %": st.column_config.NumberColumn("Completion", format="%.1f%%"),
            "MR Remaining": st.column_config.NumberColumn("MR Remaining", format="%d"),
            "Td Remaining": st.column_config.NumberColumn("Td Remaining", format="%d"),
            "HPV Dose 1 Remaining": st.column_config.NumberColumn("HPV D1 Remaining", format="%d"),
            "HPV Dose 2 Pending": st.column_config.NumberColumn("HPV D2 Pending", format="%d"),
        },
    )
    st.download_button(
        "Download Municipality Requirements (CSV)",
        data=display_status.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"SBI_Vaccine_Requirements_{CAMPAIGN_YEAR}_Municipality.csv",
        mime="text/csv",
        key="req_muni_download",
    )

    if user_role == "System Admin":
        reopenable = status_table[status_table["Status"].isin(["Submitted", "Needs Review"])] ["Municipality"].tolist()
        if reopenable:
            st.divider()
            _heading("fa-lock-open", "Reopen RHU Submission", 4)
            rc1, rc2 = st.columns([3, 1])
            with rc1:
                reopen_muni = st.selectbox("Municipality", reopenable, key="req_admin_reopen_muni")
            with rc2:
                st.write("")
                st.write("")
                if st.button("Reopen", width="stretch", key="req_admin_reopen"):
                    actor = st.session_state.get("username") or st.session_state.get("user_name") or "System Admin"
                    set_submission_status(supabase, reopen_muni, "Draft", actor, reopened=True)
                    _audit(supabase, f"Submission reopened | municipality={reopen_muni}")
                    st.toast(f"{reopen_muni} reopened for editing.")
                    st.rerun()

    st.divider()
    _heading("fa-map", "Submitted Vaccine Requirement Map", 4)
    if official_frame.empty:
        st.write("No municipality has a current Submitted requirement yet.")
    else:
        map_summary = aggregate_requirement_by_municipality(official_frame)
        metric_map = {
            "MR Remaining": "MR Remaining",
            "Td Remaining": "Td Remaining",
            "HPV Dose 1 Remaining": "G4 HPV Dose 1 Remaining",
            "HPV Dose 2 Pending": "G4 HPV Dose 2 Pending",
        }
        selected_metric = st.selectbox(
            "Map metric",
            list(metric_map),
            key="req_map_metric",
        )
        value_col = metric_map[selected_metric]
        map_data = map_summary[["Municipality", value_col]].copy()
        render_municipality_choropleth(
            map_data,
            value_col,
            f"{selected_metric} by Municipality",
            f"req_map_{normalize_municipality_key(selected_metric).lower()}",
            color_scale=STRONG_BLUE_SCALE,
            range_color=None,
            value_format=",.0f",
            value_suffix="",
            hover_format=":,.0f",
            colorbar_title="Learners / Doses",
            map_opacity=0.84,
        )

    st.divider()
    _render_supply_planning(official_frame)

    st.divider()
    _heading("fa-school", "School-Level Requirement Export", 4)
    export_cols = [
        "Municipality", "Barangay", "School ID", "School Name",
        "G1 Total", "G1 MR Prior", "G1 MR Remaining", "G1 Td Prior", "G1 Td Remaining",
        "G4 Female", "G4 HPV Dose 1 Prior", "G4 HPV Dose 1 Remaining",
        "G4 HPV Dose 2 Prior", "G4 HPV Dose 2 Pending",
        "G7 Total", "G7 MR Prior", "G7 MR Remaining", "G7 Td Prior", "G7 Td Remaining",
        "Requirement Entry Status",
    ]
    export = all_frame[[c for c in export_cols if c in all_frame.columns]].copy()
    st.download_button(
        "Download School-Level Requirements (CSV)",
        data=export.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"SBI_Vaccine_Requirements_{CAMPAIGN_YEAR}_Schools.csv",
        mime="text/csv",
        key="req_school_download",
    )


def _render_municipality_view(
    supabase,
    actual_targets: pd.DataFrame,
    all_frame: pd.DataFrame,
    submissions: pd.DataFrame,
    municipality: str,
    user_role: str,
    assigned_muni: str,
) -> None:
    municipality = canonical_municipality_name(municipality)
    muni_frame = all_frame[
        all_frame["Municipality"].map(normalize_municipality_key).eq(normalize_municipality_key(municipality))
    ].copy()
    actual_muni = actual_targets[
        actual_targets["Municipality"].map(normalize_municipality_key).eq(normalize_municipality_key(municipality))
    ].copy()

    if muni_frame.empty:
        st.warning(f"No Actual Target school rows were found for {municipality}.")
        return

    status, stale = submission_state(submissions, actual_targets, municipality)
    record = municipality_submission_record(submissions, municipality)
    complete_schools = int(muni_frame["Requirement Complete"].sum())
    total_schools = int(muni_frame["School ID"].nunique())

    s1, s2, s3 = st.columns(3)
    s1.metric("Submission Status", status)
    s2.metric("Schools Complete", f"{complete_schools} / {total_schools}")
    s3.metric("Actual Target Schools", f"{total_schools}")

    _render_local_metrics(muni_frame)

    if status == "Submitted":
        submitted_at = str(record.get("submitted_at") or "").strip()
        submitted_by = str(record.get("submitted_by") or "").strip()
        if submitted_at or submitted_by:
            st.write(f"Submitted by {submitted_by or 'RHU'}{(' on ' + submitted_at) if submitted_at else ''}.")

    can_encode = user_role in {"RHU Encoder", "System Admin"}
    if user_role == "RHU Encoder" and normalize_municipality_key(assigned_muni) != normalize_municipality_key(municipality):
        st.error("This RHU account is not authorized to edit the selected municipality.")
        can_encode = False

    if status == "Submitted" and not stale:
        if user_role == "System Admin":
            if st.button("Reopen Submission", key="req_specific_reopen"):
                actor = st.session_state.get("username") or st.session_state.get("user_name") or "System Admin"
                set_submission_status(supabase, municipality, "Draft", actor, reopened=True)
                _audit(supabase, f"Submission reopened | municipality={municipality}")
                st.rerun()
        else:
            st.write("This municipality is locked after submission. The NIP coordinator can reopen it for corrections.")
        return

    if stale:
        st.warning(
            "The Actual Targets changed after this municipality was submitted. Review the updated targets and resubmit before the municipality is counted in official province totals."
        )
        if can_encode and st.button("Reopen for Target Review", key="req_reopen_stale"):
            actor = st.session_state.get("username") or st.session_state.get("user_name") or "User"
            set_submission_status(supabase, municipality, "Draft", actor, reopened=True)
            _audit(supabase, f"Stale submission reopened | municipality={municipality}")
            st.rerun()
        return

    if not can_encode:
        st.write("Encoding is available only to the assigned RHU Encoder or System Admin.")
        return

    st.divider()
    _heading("fa-pen-to-square", "Prior Vaccination Encoding", 4)
    st.write(
        "Enter learners already vaccinated before the upcoming SBI activity. Leave a field blank if it has not yet been verified; blank is not treated as zero."
    )

    editor_source = _editable_frame(muni_frame)
    disabled_cols = [
        "Barangay", "School ID", "School Name", "Target Entry Status",
        "G1 Total", "G4 Female", "G7 Total",
    ]
    edited = st.data_editor(
        editor_source,
        width="stretch",
        hide_index=True,
        num_rows="fixed",
        disabled=[c for c in disabled_cols if c in editor_source.columns],
        column_config={
            "School ID": st.column_config.TextColumn("School ID"),
            "School Name": st.column_config.TextColumn("School Name", width="large"),
            "Target Entry Status": st.column_config.TextColumn("Actual Target Status"),
            "G1 Total": st.column_config.NumberColumn("G1 Target", min_value=0, step=1, format="%d"),
            "G4 Female": st.column_config.NumberColumn("G4 Female Target", min_value=0, step=1, format="%d"),
            "G7 Total": st.column_config.NumberColumn("G7 Target", min_value=0, step=1, format="%d"),
            "G1 MR Prior": st.column_config.NumberColumn("G1 MR Prior", min_value=0, step=1, format="%d"),
            "G1 Td Prior": st.column_config.NumberColumn("G1 Td Prior", min_value=0, step=1, format="%d"),
            "G4 HPV Dose 1 Prior": st.column_config.NumberColumn("G4 HPV D1 Prior", min_value=0, step=1, format="%d"),
            "G4 HPV Dose 2 Prior": st.column_config.NumberColumn("G4 HPV D2 Prior", min_value=0, step=1, format="%d"),
            "G7 MR Prior": st.column_config.NumberColumn("G7 MR Prior", min_value=0, step=1, format="%d"),
            "G7 Td Prior": st.column_config.NumberColumn("G7 Td Prior", min_value=0, step=1, format="%d"),
        },
        key=f"req_editor_{normalize_municipality_key(municipality)}",
    )

    recalculated = _editor_to_recalculated(actual_muni, edited)
    _heading("fa-calculator", "Calculated Remaining", 4)
    calc_cols = [
        "School Name",
        "G1 MR Remaining", "G1 Td Remaining",
        "G4 HPV Dose 1 Remaining", "G4 HPV Dose 2 Pending",
        "G7 MR Remaining", "G7 Td Remaining",
        "Requirement Entry Status",
    ]
    st.dataframe(
        recalculated[[c for c in calc_cols if c in recalculated.columns]],
        width="stretch",
        hide_index=True,
    )

    draft_errors = validate_requirement_frame(recalculated, require_complete=False)
    submit_errors = validate_requirement_frame(recalculated, require_complete=True)

    b1, b2 = st.columns(2)
    with b1:
        if st.button("Save Draft", width="stretch", key="req_save_draft"):
            if draft_errors:
                st.error(draft_errors[0])
                if len(draft_errors) > 1:
                    st.write(f"{len(draft_errors) - 1} additional validation issue(s) remain.")
            else:
                actor = st.session_state.get("username") or st.session_state.get("user_name") or "RHU Encoder"
                count = save_requirement_rows(supabase, recalculated, actor)
                set_submission_status(supabase, municipality, "Draft", actor)
                _audit(supabase, f"Draft saved | municipality={municipality} | schools={count}")
                st.toast("Draft saved.")
                st.rerun()

    with b2:
        if st.button("Submit to NIP Coordinator", type="primary", width="stretch", key="req_submit"):
            if submit_errors:
                st.error(submit_errors[0])
                if len(submit_errors) > 1:
                    st.write(f"{len(submit_errors) - 1} additional item(s) must be completed before submission.")
            else:
                actor = st.session_state.get("username") or st.session_state.get("user_name") or "RHU Encoder"
                count = save_requirement_rows(supabase, recalculated, actor)
                signature = target_signature(actual_targets, municipality)
                set_submission_status(
                    supabase,
                    municipality,
                    "Submitted",
                    actor,
                    signature=signature,
                )
                _audit(supabase, f"Submitted | municipality={municipality} | schools={count}")
                st.toast("Vaccine requirement data submitted.")
                st.rerun()

    if submit_errors:
        with st.expander(f"Submission checks ({len(submit_errors)} issue(s))", expanded=False):
            for error in submit_errors[:100]:
                st.write(f"- {error}")


def render_vaccine_requirements(
    supabase,
    actual_targets: pd.DataFrame,
    view_mode: str,
    selected_muni: str,
    user_role: str,
    assigned_muni: str,
) -> None:
    _heading("fa-syringe", "Vaccine Requirements")

    ready, message = requirements_schema_available(supabase)
    if not ready:
        st.error(
            f"Vaccine Requirements setup is not complete ({message}). Run supabase/002_sbi_vaccine_requirements.sql once, then reload the app."
        )
        return

    if actual_targets is None or actual_targets.empty:
        st.warning("Actual Targets are unavailable. Refresh SBI data after confirming the Actual Targets worksheet.")
        return

    prior = fetch_prior_vaccination(supabase, CAMPAIGN_YEAR)
    submissions = fetch_submission_status(supabase, CAMPAIGN_YEAR)
    all_frame = build_school_requirement_frame(actual_targets, prior)
    if all_frame.empty:
        st.warning("No school-level Actual Target rows are available for vaccine requirement planning.")
        return

    if user_role == "RHU Encoder":
        municipality = canonical_municipality_name(assigned_muni)
        if not normalize_municipality_key(municipality):
            st.error("This RHU Encoder account has no assigned municipality. Ask the System Admin to update the account.")
            return
        _render_municipality_view(
            supabase,
            actual_targets,
            all_frame,
            submissions,
            municipality,
            user_role,
            assigned_muni,
        )
        return

    if view_mode == "All Municipalities (Abra)":
        _render_province_view(supabase, actual_targets, all_frame, submissions, user_role)
    else:
        _render_municipality_view(
            supabase,
            actual_targets,
            all_frame,
            submissions,
            selected_muni,
            user_role,
            assigned_muni,
        )
