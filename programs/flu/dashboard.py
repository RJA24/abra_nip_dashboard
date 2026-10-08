from __future__ import annotations

from datetime import date, datetime
import logging
import math
import time

import pandas as pd
import plotly.express as px
import pytz
import streamlit as st

from core.config import ABRA_MUNIS
from core.data import fetch_flu_masterlist


logger = logging.getLogger("abra_nip_dashboard.flu")
MANILA_TZ = pytz.timezone("Asia/Manila")
TABLE_NAME = "flu_vaccination_entries"

PRIORITY_GROUPS = [
    ("senior_60_plus", "Persons aged 60 years and above", "60+"),
    ("health_care_workers", "Health care workers", "Health care workers"),
    ("pregnant_women", "Pregnant women", "Pregnant women"),
    ("adults_with_comorbidities", "Adults with comorbidities", "Adults with comorbidities"),
    (
        "frontline_personnel",
        "Personnel providing frontline services, such as military personnel",
        "Frontline personnel",
    ),
    ("other_healthy_adults", "Other healthy adults", "Other healthy adults"),
]

DISPLAY_BY_KEY = {key: short for key, _source, short in PRIORITY_GROUPS}
SOURCE_BY_KEY = {key: source for key, source, _short in PRIORITY_GROUPS}


def _norm_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).replace("\xa0", " ").strip()


def _norm_code(value) -> str:
    text = _norm_text(value)
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _canonical_muni(value: str) -> str:
    clean = _norm_text(value)
    lookup = {m.casefold(): m for m in ABRA_MUNIS}
    return lookup.get(clean.casefold(), clean)


def _find_column(columns, *needles: str):
    normalized = {str(c).replace("\xa0", " ").strip().casefold(): c for c in columns}
    for needle in needles:
        hit = normalized.get(needle.casefold())
        if hit is not None:
            return hit
    # Resilient fallback for the current source typo: "Barnagay Code".
    for normalized_name, original in normalized.items():
        if all(piece in normalized_name for piece in needles[0].casefold().split()):
            return original
    return None


def prepare_masterlist(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize the shared FLU Google Sheet into barangay-level target rows.

    Municipality subtotal/formula rows are deliberately ignored. Targets are summed
    from barangay rows so dashboard denominators cannot be double-counted.
    Blank source cells stay missing; a numeric zero means the RHU explicitly encoded 0.
    """
    columns = [
        "municipality",
        "barangay_code",
        "barangay",
        *[key for key, _source, _short in PRIORITY_GROUPS],
        "filled_cells",
        "expected_cells",
        "masterlist_complete",
    ]
    if raw is None or raw.empty:
        return pd.DataFrame(columns=columns)

    code_col = _find_column(raw.columns, "barangay code", "barnagay code")
    if code_col is None:
        # The uploaded/current sheet spells this "Barnagay Code".
        code_col = _find_column(raw.columns, "barnagay code")
    name_col = _find_column(
        raw.columns,
        "region, province/city, municipality and barangay",
    )
    if code_col is None or name_col is None:
        raise ValueError(
            "FLU Masterlist must contain the Barangay Code and Region/Province/City/Municipality/Barangay columns."
        )

    source_columns = {}
    for key, source_name, _short in PRIORITY_GROUPS:
        col = _find_column(raw.columns, source_name)
        if col is None:
            raise ValueError(f"FLU Masterlist is missing required column: {source_name}")
        source_columns[key] = col

    muni_lookup = {m.casefold(): m for m in ABRA_MUNIS}
    current_muni = ""
    records: list[dict] = []

    for _, row in raw.iterrows():
        code = _norm_code(row.get(code_col))
        place = _norm_text(row.get(name_col))
        if not code or not place:
            continue

        # Province total/header row.
        if code == "1400100000" and place.casefold() == "abra":
            continue

        # Municipality subtotal rows are identified by the PSGC-style xxx000 code
        # plus an exact municipality name. Do not treat their formulas as targets.
        canonical = muni_lookup.get(place.casefold())
        if code.endswith("000") and canonical:
            current_muni = canonical
            continue

        if not current_muni:
            continue

        record = {
            "municipality": current_muni,
            "barangay_code": code,
            "barangay": place,
        }
        filled = 0
        for key, source_col in source_columns.items():
            source_value = row.get(source_col)
            source_text = _norm_text(source_value)
            if source_text != "":
                filled += 1
            numeric = pd.to_numeric(
                source_text.replace(",", "") if source_text else None,
                errors="coerce",
            )
            record[key] = None if pd.isna(numeric) else max(int(numeric), 0)

        record["filled_cells"] = filled
        record["expected_cells"] = len(PRIORITY_GROUPS)
        record["masterlist_complete"] = filled == len(PRIORITY_GROUPS)
        records.append(record)

    return pd.DataFrame(records, columns=columns)


def _fetch_entries(supabase) -> pd.DataFrame:
    rows: list[dict] = []
    offset = 0
    page_size = 1000
    try:
        while True:
            response = (
                supabase.table(TABLE_NAME)
                .select("*")
                .order("activity_date")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            batch = response.data or []
            rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
    except Exception:
        logger.exception("Unable to read FLU vaccination entries")
        return pd.DataFrame()

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    if "activity_date" in out.columns:
        out["activity_date"] = pd.to_datetime(out["activity_date"], errors="coerce").dt.date
    for key, _source, _short in PRIORITY_GROUPS:
        if key not in out.columns:
            out[key] = 0
        out[key] = pd.to_numeric(out[key], errors="coerce").fillna(0).clip(lower=0).astype(int)
    return out


def _table_available(supabase) -> bool:
    try:
        supabase.table(TABLE_NAME).select("id").limit(1).execute()
        return True
    except Exception:
        return False


def _logout_session() -> None:
    for key in [
        "logged_in",
        "username",
        "user_name",
        "user_role",
        "assigned_muni",
        "must_change_password",
        "active_program",
    ]:
        if key == "logged_in":
            st.session_state[key] = False
        elif key == "must_change_password":
            st.session_state[key] = False
        elif key == "active_program":
            st.session_state[key] = None
        elif key == "assigned_muni":
            st.session_state[key] = "None"
        else:
            st.session_state[key] = ""
    st.rerun()


def _scope_masterlist(masterlist: pd.DataFrame, municipality: str | None) -> pd.DataFrame:
    if masterlist.empty or not municipality:
        return masterlist.copy()
    return masterlist[masterlist["municipality"].eq(municipality)].copy()


def _scope_entries(entries: pd.DataFrame, municipality: str | None) -> pd.DataFrame:
    if entries.empty or not municipality:
        return entries.copy()
    return entries[entries["municipality"].eq(municipality)].copy()


def _summary(masterlist: pd.DataFrame, entries: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    rows = []
    total_target = 0
    total_vaccinated = 0

    for key, _source, label in PRIORITY_GROUPS:
        target = int(pd.to_numeric(masterlist.get(key, pd.Series(dtype=float)), errors="coerce").sum()) if not masterlist.empty else 0
        vaccinated = int(pd.to_numeric(entries.get(key, pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not entries.empty else 0
        remaining = max(target - vaccinated, 0)
        coverage = (vaccinated / target * 100.0) if target > 0 else math.nan
        rows.append(
            {
                "Priority Group": label,
                "Masterlisted": target,
                "Vaccinated": vaccinated,
                "Remaining": remaining,
                "Coverage %": coverage,
            }
        )
        total_target += target
        total_vaccinated += vaccinated

    expected_cells = int(masterlist["expected_cells"].sum()) if not masterlist.empty else 0
    filled_cells = int(masterlist["filled_cells"].sum()) if not masterlist.empty else 0
    completeness = (filled_cells / expected_cells * 100.0) if expected_cells else 0.0

    totals = {
        "target": total_target,
        "vaccinated": total_vaccinated,
        "remaining": max(total_target - total_vaccinated, 0),
        "coverage": (total_vaccinated / total_target * 100.0) if total_target > 0 else math.nan,
        "masterlist_completeness": completeness,
        "barangays": int(len(masterlist)),
    }
    return pd.DataFrame(rows), totals


def _format_pct(value) -> str:
    return "—" if value is None or pd.isna(value) else f"{value:.1f}%"


def _render_dashboard(masterlist: pd.DataFrame, entries: pd.DataFrame, location_label: str) -> None:
    summary, totals = _summary(masterlist, entries)

    st.markdown(f"### Vaccination Dashboard — {location_label}")
    st.caption(
        "Masterlist denominators come from the shared Abra FLU Google Sheet. Vaccination counts come from daily RHU entries in NIP MIS."
    )

    if totals["masterlist_completeness"] < 100:
        st.warning(
            f"Masterlist completion for this scope is {totals['masterlist_completeness']:.1f}%. "
            "Coverage is therefore based only on masterlist values currently encoded in the shared Google Sheet. "
            "Use numeric 0 when a category has no eligible persons; do not leave the cell blank."
        )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Masterlisted", f"{totals['target']:,}")
    c2.metric("Vaccinated", f"{totals['vaccinated']:,}")
    c3.metric("Remaining", f"{totals['remaining']:,}")
    c4.metric("Coverage", _format_pct(totals["coverage"]))
    c5.metric("Masterlist Complete", f"{totals['masterlist_completeness']:.1f}%")

    st.markdown("#### Progress by Priority Group")
    long = summary.melt(
        id_vars=["Priority Group"],
        value_vars=["Masterlisted", "Vaccinated"],
        var_name="Measure",
        value_name="Count",
    )
    fig = px.bar(long, x="Priority Group", y="Count", color="Measure", barmode="group")
    fig.update_layout(xaxis_title=None, yaxis_title="Persons", legend_title=None, height=430)
    st.plotly_chart(fig, width="stretch", key=f"flu_priority_{location_label}")

    summary_view = summary.copy()
    summary_view["Coverage %"] = summary_view["Coverage %"].map(_format_pct)
    st.dataframe(summary_view, width="stretch", hide_index=True)

    st.markdown("#### Daily Vaccination Trend")
    if entries.empty or "activity_date" not in entries.columns:
        st.info("No vaccination entries have been submitted for this scope yet.")
    else:
        daily = entries.copy()
        daily["Daily Vaccinated"] = daily[[key for key, _source, _short in PRIORITY_GROUPS]].sum(axis=1)
        daily = daily.groupby("activity_date", as_index=False)["Daily Vaccinated"].sum().sort_values("activity_date")
        daily["Cumulative Vaccinated"] = daily["Daily Vaccinated"].cumsum()
        fig_daily = px.line(
            daily,
            x="activity_date",
            y=["Daily Vaccinated", "Cumulative Vaccinated"],
            markers=True,
        )
        fig_daily.update_layout(xaxis_title=None, yaxis_title="Persons", legend_title=None, height=420)
        st.plotly_chart(fig_daily, width="stretch", key=f"flu_daily_{location_label}")

    if masterlist.empty:
        return

    st.markdown("#### Barangay Progress")
    vaccinated_by_barangay = pd.DataFrame(columns=["barangay_code", *[key for key, _source, _short in PRIORITY_GROUPS]])
    if not entries.empty:
        vaccinated_by_barangay = (
            entries.groupby("barangay_code", as_index=False)[[key for key, _source, _short in PRIORITY_GROUPS]]
            .sum()
        )

    barangay = masterlist[["municipality", "barangay_code", "barangay", *[key for key, _source, _short in PRIORITY_GROUPS]]].copy()
    target_total = barangay[[key for key, _source, _short in PRIORITY_GROUPS]].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1).fillna(0)
    barangay["Masterlisted"] = target_total.astype(int)

    if not vaccinated_by_barangay.empty:
        vacc = vaccinated_by_barangay.copy()
        vacc["Vaccinated"] = vacc[[key for key, _source, _short in PRIORITY_GROUPS]].sum(axis=1)
        barangay = barangay.merge(vacc[["barangay_code", "Vaccinated"]], on="barangay_code", how="left")
    else:
        barangay["Vaccinated"] = 0
    barangay["Vaccinated"] = pd.to_numeric(barangay["Vaccinated"], errors="coerce").fillna(0).astype(int)
    barangay["Remaining"] = (barangay["Masterlisted"] - barangay["Vaccinated"]).clip(lower=0)
    barangay["Coverage %"] = barangay.apply(
        lambda r: (r["Vaccinated"] / r["Masterlisted"] * 100.0) if r["Masterlisted"] > 0 else math.nan,
        axis=1,
    )
    barangay["Coverage %"] = barangay["Coverage %"].map(_format_pct)
    st.dataframe(
        barangay[["barangay", "Masterlisted", "Vaccinated", "Remaining", "Coverage %"]].rename(columns={"barangay": "Barangay"}),
        width="stretch",
        hide_index=True,
    )


def _existing_daily_grid(masterlist: pd.DataFrame, entries: pd.DataFrame, activity_date: date) -> pd.DataFrame:
    base = masterlist[["barangay_code", "barangay"]].copy()
    for key, _source, label in PRIORITY_GROUPS:
        base[label] = 0

    if entries.empty:
        return base

    existing = entries[entries["activity_date"].eq(activity_date)].copy()
    if existing.empty:
        return base

    cols = ["barangay_code", *[key for key, _source, _short in PRIORITY_GROUPS]]
    existing = existing[cols].copy()
    rename = {key: label for key, _source, label in PRIORITY_GROUPS}
    existing = existing.rename(columns=rename)
    base = base.merge(existing, on="barangay_code", how="left", suffixes=("", "_existing"))
    for _key, _source, label in PRIORITY_GROUPS:
        existing_col = f"{label}_existing"
        if existing_col in base.columns:
            base[label] = pd.to_numeric(base[existing_col], errors="coerce").fillna(base[label]).fillna(0).astype(int)
            base = base.drop(columns=[existing_col])
    return base


def _render_daily_encoding(supabase, masterlist: pd.DataFrame, entries: pd.DataFrame, municipality: str) -> None:
    st.markdown(f"### Daily Vaccination Encoding — {municipality}")
    st.caption(
        "Enter the number vaccinated on the selected date only — not cumulative totals. "
        "Saving the same date again updates that day's values, so corrections do not create duplicates."
    )

    if masterlist.empty:
        st.warning("No barangays were found for your municipality in the Abra FLU Masterlist Google Sheet.")
        return

    selected_date = st.date_input("Vaccination date", value=date.today(), key="flu_entry_date")
    daily_grid = _existing_daily_grid(masterlist, entries, selected_date)

    editor_config = {
        "barangay_code": st.column_config.TextColumn("Barangay Code", disabled=True),
        "barangay": st.column_config.TextColumn("Barangay", disabled=True),
    }
    for _key, _source, label in PRIORITY_GROUPS:
        editor_config[label] = st.column_config.NumberColumn(label, min_value=0, step=1, format="%d")

    edited = st.data_editor(
        daily_grid,
        width="stretch",
        hide_index=True,
        disabled=["barangay_code", "barangay"],
        column_config=editor_config,
        key=f"flu_daily_editor_{municipality}_{selected_date.isoformat()}",
    )

    daily_total = int(
        edited[[label for _key, _source, label in PRIORITY_GROUPS]]
        .apply(pd.to_numeric, errors="coerce")
        .fillna(0)
        .sum()
        .sum()
    )
    st.metric("Vaccinated on Selected Date", f"{daily_total:,}")

    if not st.button("Save Daily Vaccination Data", type="primary", width="stretch", key="flu_save_daily"):
        return

    username = str(st.session_state.get("username") or st.session_state.get("user_name") or "")
    now_iso = datetime.now(MANILA_TZ).isoformat()
    payload = []
    label_to_key = {label: key for key, _source, label in PRIORITY_GROUPS}
    for _, row in edited.iterrows():
        item = {
            "municipality": municipality,
            "barangay_code": _norm_code(row.get("barangay_code")),
            "barangay": _norm_text(row.get("barangay")),
            "activity_date": selected_date.isoformat(),
            "encoded_by": username or None,
            "updated_at": now_iso,
        }
        for label, key in label_to_key.items():
            value = pd.to_numeric(row.get(label), errors="coerce")
            item[key] = max(int(value), 0) if not pd.isna(value) else 0
        payload.append(item)

    try:
        supabase.table(TABLE_NAME).upsert(
            payload,
            on_conflict="municipality,barangay_code,activity_date",
        ).execute()
        st.success(f"Saved {len(payload)} barangay record(s) for {selected_date.strftime('%b %d, %Y')}.")
        time.sleep(0.6)
        st.rerun()
    except Exception:
        logger.exception("Unable to save FLU daily vaccination data")
        st.error(
            "The daily vaccination data could not be saved. If this is the first FLU deployment, "
            "run supabase/013_flu_vaccination_monitoring.sql first."
        )


def _render_admin_monitoring(masterlist: pd.DataFrame, entries: pd.DataFrame) -> None:
    st.markdown("### Admin Monitoring — 27 RHUs")
    st.caption(
        "RHU readiness is based on completion of all six masterlist fields for every barangay. "
        "Vaccination reporting status comes from NIP MIS daily entries."
    )

    rows = []
    for municipality in ABRA_MUNIS:
        m_targets = masterlist[masterlist["municipality"].eq(municipality)].copy() if not masterlist.empty else pd.DataFrame()
        m_entries = entries[entries["municipality"].eq(municipality)].copy() if not entries.empty else pd.DataFrame()
        _summary_df, totals = _summary(m_targets, m_entries)
        last_report = ""
        reporting_days = 0
        if not m_entries.empty and "activity_date" in m_entries.columns:
            valid_dates = pd.Series(m_entries["activity_date"]).dropna()
            if not valid_dates.empty:
                last_report = max(valid_dates).strftime("%b %d, %Y")
                reporting_days = int(valid_dates.nunique())

        if totals["masterlist_completeness"] < 100:
            status = "Masterlist Incomplete"
        elif m_entries.empty:
            status = "Ready - No Vaccination Entry"
        elif totals["target"] > 0 and totals["vaccinated"] >= totals["target"]:
            status = "Completed / Target Reached"
        else:
            status = "Reporting"

        rows.append(
            {
                "Municipality": municipality,
                "Status": status,
                "Masterlist Complete %": round(totals["masterlist_completeness"], 1),
                "Masterlisted": totals["target"],
                "Vaccinated": totals["vaccinated"],
                "Coverage %": None if pd.isna(totals["coverage"]) else round(totals["coverage"], 1),
                "Reporting Days": reporting_days,
                "Last Report": last_report,
            }
        )

    status_df = pd.DataFrame(rows)
    ready = int((status_df["Masterlist Complete %"] >= 100).sum())
    reporting = int(status_df["Status"].isin(["Reporting", "Completed / Target Reached"]).sum())
    total_vaccinated = int(status_df["Vaccinated"].sum())
    total_masterlisted = int(status_df["Masterlisted"].sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("RHUs Masterlist Ready", f"{ready}/27")
    c2.metric("RHUs Reporting", f"{reporting}/27")
    c3.metric("Abra Masterlisted", f"{total_masterlisted:,}")
    c4.metric("Abra Vaccinated", f"{total_vaccinated:,}")

    st.dataframe(status_df, width="stretch", hide_index=True)

    chart_df = status_df.copy()
    chart_df["Coverage %"] = pd.to_numeric(chart_df["Coverage %"], errors="coerce").fillna(0)
    fig = px.bar(chart_df, x="Municipality", y="Coverage %")
    fig.update_layout(xaxis_title=None, yaxis_title="Coverage %", height=430)
    st.plotly_chart(fig, width="stretch", key="flu_admin_muni_coverage")

    st.download_button(
        "Download RHU Monitoring CSV",
        data=status_df.to_csv(index=False).encode("utf-8-sig"),
        file_name="Abra_FLU_RHU_Monitoring.csv",
        mime="text/csv",
        width="stretch",
        key="flu_admin_monitoring_csv",
    )


def render_flu_dashboard(supabase) -> None:
    user_role = str(st.session_state.get("user_role") or "Guest")
    user_name = str(st.session_state.get("user_name") or "")
    assigned_muni = _canonical_muni(st.session_state.get("assigned_muni") or "")
    is_admin = user_role in {"System Admin", "QA Admin"}
    is_rhu = user_role == "RHU Encoder"

    if not (is_admin or is_rhu):
        st.error("Your account does not have access to the Influenza Vaccination program.")
        if st.button("Return to Main Menu", key="flu_unauthorized_back"):
            st.session_state["active_program"] = None
            st.rerun()
        return

    st.title("Abra Influenza Vaccination Monitoring 2026–2027")

    with st.sidebar:
        st.markdown(
            f"""
            <div style="text-align:center;padding:10px 0 15px 0;">
                <img src="https://upload.wikimedia.org/wikipedia/commons/1/1a/Abra_provincial_seal.png" width="90" style="margin-bottom:15px;">
                <h3 style="margin:0;font-size:1.15rem;">{user_name}</h3>
                <p style="margin:2px 0 12px 0;font-size:0.85rem;opacity:0.8;font-style:italic;">{user_role}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.divider()
        if st.button("Main Menu", width="stretch", key="flu_main_menu"):
            st.session_state["active_program"] = None
            st.rerun()
        if st.button("Logout", width="stretch", key="flu_logout"):
            _logout_session()

        if is_admin:
            with st.expander("Dashboard Filters", expanded=True):
                view_mode = st.radio(
                    "Geographic Level:",
                    ["All Municipalities (Abra)", "Specific Municipality"],
                    key="flu_geo_mode",
                )
                if view_mode == "Specific Municipality":
                    selected_muni = st.selectbox("Select Municipality:", ABRA_MUNIS, key="flu_muni_sel")
                else:
                    selected_muni = None
        else:
            selected_muni = assigned_muni
            st.caption(f"RHU scope: **{selected_muni or 'Not assigned'}**")

        with st.expander("System Actions", expanded=False):
            if st.button("Refresh FLU Data", width="stretch", key="flu_refresh"):
                st.cache_data.clear()
                st.toast("Reloading the latest FLU masterlist and vaccination data...")
                time.sleep(0.4)
                st.rerun()

        st.caption(f"Last Sync: {datetime.now(MANILA_TZ).strftime('%b %d, %Y %I:%M %p')}")

    if not _table_available(supabase):
        st.error(
            "FLU vaccination storage is not ready yet. Run `supabase/013_flu_vaccination_monitoring.sql` in Supabase, then refresh this page."
        )
        return

    raw_masterlist = fetch_flu_masterlist()
    try:
        masterlist = prepare_masterlist(raw_masterlist)
    except Exception as exc:
        st.error(f"The Abra FLU Masterlist sheet could not be interpreted: {exc}")
        return

    if masterlist.empty:
        st.warning("The Abra FLU Masterlist Google Sheet is currently empty or unavailable.")

    entries = _fetch_entries(supabase)

    if is_admin:
        scope_masterlist = _scope_masterlist(masterlist, selected_muni)
        scope_entries = _scope_entries(entries, selected_muni)
        location_label = selected_muni or "Abra Province"
        tab_dashboard, tab_admin = st.tabs(["Vaccination Dashboard", "Admin Monitoring"])
        with tab_dashboard:
            _render_dashboard(scope_masterlist, scope_entries, location_label)
        with tab_admin:
            _render_admin_monitoring(masterlist, entries)
        return

    if assigned_muni not in ABRA_MUNIS:
        st.error("Your RHU account does not have a valid municipality assignment. Please contact the system administrator.")
        return

    rhu_masterlist = _scope_masterlist(masterlist, assigned_muni)
    rhu_entries = _scope_entries(entries, assigned_muni)
    tab_dashboard, tab_entry = st.tabs(["Vaccination Dashboard", "Daily Encoding"])
    with tab_dashboard:
        _render_dashboard(rhu_masterlist, rhu_entries, assigned_muni)
    with tab_entry:
        _render_daily_encoding(supabase, rhu_masterlist, rhu_entries, assigned_muni)
