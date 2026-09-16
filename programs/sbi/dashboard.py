"""School-Based Immunization (SBI) dashboard renderer.

This module owns SBI-specific UI, filtering, analytics, target review,
and administrative target synchronization. The application shell and
authentication remain in the root ``sia.py`` entry point.
"""

from datetime import datetime
import time

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pytz
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from streamlit_gsheets import GSheetsConnection

from core.config import ABRA_MUNIS, SBI_SHEET_URL
from core.data import fetch_sbi_actual_targets, fetch_sbi_targets, fetch_sbi_vacctrack
from db_utils import (
    consolidate_sbi_targets,
    replace_table_with_rollback,
    update_session_log_throttled,
    validate_sbi_targets,
)


def _get_last_updated_time() -> str:
    tz = pytz.timezone("Asia/Manila")
    return datetime.now(tz).strftime("%B %d, %Y | %I:%M %p")


def render_sbi_dashboard(supabase) -> None:
    st.title("Abra School-Based Immunization (SBI) 2026")
    
    last_updated = _get_last_updated_time()
    st_autorefresh(interval=3600000, limit=None, key="sbi_hourly_data_refresh")

    # --- SESSION TRACKING (throttled to reduce database writes) ---
    update_session_log_throttled(supabase, prefix="SBI Session Duration")

    # --- FETCH DATA ---
    df_g1, df_g4, df_g7 = fetch_sbi_vacctrack()
    df_sbi_targets = fetch_sbi_targets()
    df_sbi_actual_targets = fetch_sbi_actual_targets()

    # --- SIDEBAR & FILTERS ---
    with st.sidebar:
        st.markdown(f"""
        <div style="text-align: center; padding: 10px 0px 15px 0px;">
            <img src="https://upload.wikimedia.org/wikipedia/commons/1/1a/Abra_provincial_seal.png" width="90" style="margin-bottom: 15px; filter: drop-shadow(0px 4px 6px rgba(0,0,0,0.1));">
            <h3 style="margin: 0; padding: 0; font-size: 1.15rem; font-weight: 700;">{st.session_state['user_name']}</h3>
            <p style="margin: 2px 0 12px 0; font-size: 0.85rem; opacity: 0.8; font-style: italic;">{st.session_state['user_role']}</p>
        </div>
        """, unsafe_allow_html=True)
        
        st.divider()
        
        if st.button("Main Menu", use_container_width=True):
            st.session_state['active_program'] = None
            st.rerun()
            
        with st.expander("Dashboard Filters", expanded=True):
            view_mode = st.radio("Geographic Level:", ["All Municipalities (Abra)", "Specific Municipality"], key="sbi_geo_mode")
            if view_mode == "Specific Municipality":
                selected_muni = st.selectbox("Select Municipality:", ABRA_MUNIS, key="sbi_muni_sel")
            else:
                selected_muni = "None"
                
        with st.expander("System Actions", expanded=False):
            if st.button("Refresh Data", use_container_width=True, key="sbi_refresh"):
                st.cache_data.clear()
                st.toast("SBI Database Refreshed!")
                time.sleep(0.5)
                st.rerun()
                
        st.markdown(
            f'<span style="color:#64748b;font-size:0.85rem;"><i class="fa-solid fa-clock" style="margin-right:6px;"></i>Last Sync: {last_updated}</span>',
            unsafe_allow_html=True
        )

    # --- DASHBOARD TABS ---
    sbi_tabs = st.tabs(["Executive Summary", "Targets Overview", "MR & Td (Grades 1 & 7)", "HPV (Grade 4)", "Deferrals & Refusals", "Admin Panel"])
    tab_sbi_exec, tab_sbi_target, tab_sbi_mr, tab_sbi_hpv, tab_sbi_def, tab_sbi_admin = sbi_tabs

    # 1. EXECUTIVE SUMMARY
    with tab_sbi_exec:
        location_label = "Abra Province" if view_mode == "All Municipalities (Abra)" else f"{selected_muni}, Abra"
        st.markdown(f"### SBI Campaign Overview: {location_label}")
        
        if df_sbi_targets.empty:
            st.warning("Target database is empty. Please go to the Admin Panel tab to sync the database.")
        else:
            # Apply Geographic Filter
            df_view = df_sbi_targets.copy()
            if view_mode == "Specific Municipality":
                df_view = df_view[df_view['Municipality'].str.upper() == selected_muni.upper()]
                
            # Target Math
            tgt_g1 = df_view['G1 Total'].sum()
            tgt_g7 = df_view['G7 Total'].sum()
            tgt_mr_td = tgt_g1 + tgt_g7
            tgt_hpv = df_view['G4 Female'].sum()
            
            # Accomplishment Math (VaccTrack)
            g1_mr_doses, g1_td_doses, g7_mr_doses, g7_td_doses, hpv_1st = 0, 0, 0, 0, 0
            
            if not df_g1.empty:
                df_g1_view = df_g1 if view_mode == "All Municipalities (Abra)" else df_g1[df_g1['City/Municipality Name'].str.upper() == selected_muni.upper()]
                mr_m = [c for c in df_g1.columns if 'MR' in c and 'Male' in c and 'vaccinated' in c]
                mr_f = [c for c in df_g1.columns if 'MR' in c and 'Female' in c and 'vaccinated' in c]
                td_m = [c for c in df_g1.columns if 'TD' in c.upper() and 'Male' in c and 'vaccinated' in c]
                td_f = [c for c in df_g1.columns if 'TD' in c.upper() and 'Female' in c and 'vaccinated' in c]
                
                if mr_m and mr_f: g1_mr_doses = pd.to_numeric(df_g1_view[mr_m[0]], errors='coerce').fillna(0).sum() + pd.to_numeric(df_g1_view[mr_f[0]], errors='coerce').fillna(0).sum()
                if td_m and td_f: g1_td_doses = pd.to_numeric(df_g1_view[td_m[0]], errors='coerce').fillna(0).sum() + pd.to_numeric(df_g1_view[td_f[0]], errors='coerce').fillna(0).sum()

            if not df_g7.empty:
                df_g7_view = df_g7 if view_mode == "All Municipalities (Abra)" else df_g7[df_g7['City/Municipality Name'].str.upper() == selected_muni.upper()]
                mr_m = [c for c in df_g7.columns if 'MR' in c and 'Male' in c and 'vaccinated' in c]
                mr_f = [c for c in df_g7.columns if 'MR' in c and 'Female' in c and 'vaccinated' in c]
                td_m = [c for c in df_g7.columns if 'TD' in c.upper() and 'Male' in c and 'vaccinated' in c]
                td_f = [c for c in df_g7.columns if 'TD' in c.upper() and 'Female' in c and 'vaccinated' in c]
                
                if mr_m and mr_f: g7_mr_doses = pd.to_numeric(df_g7_view[mr_m[0]], errors='coerce').fillna(0).sum() + pd.to_numeric(df_g7_view[mr_f[0]], errors='coerce').fillna(0).sum()
                if td_m and td_f: g7_td_doses = pd.to_numeric(df_g7_view[td_m[0]], errors='coerce').fillna(0).sum() + pd.to_numeric(df_g7_view[td_f[0]], errors='coerce').fillna(0).sum()
                
            if not df_g4.empty:
                df_g4_view = df_g4 if view_mode == "All Municipalities (Abra)" else df_g4[df_g4['City/Municipality Name'].str.upper() == selected_muni.upper()]
                dose1 = [c for c in df_g4.columns if 'First Dose' in c and 'HPV' in c]
                if dose1: hpv_1st = pd.to_numeric(df_g4_view[dose1[0]], errors='coerce').fillna(0).sum()
                
            # Overall Math
            total_mr = g1_mr_doses + g7_mr_doses
            total_td = g1_td_doses + g7_td_doses
            mr_cov = (total_mr / tgt_mr_td * 100) if tgt_mr_td > 0 else 0
            td_cov = (total_td / tgt_mr_td * 100) if tgt_mr_td > 0 else 0
            hpv_cov = (hpv_1st / tgt_hpv * 100) if tgt_hpv > 0 else 0
            
            # KPI Cards
            k1, k2, k3 = st.columns(3)
            k1.metric("Measles-Rubella (MR) Coverage", f"{mr_cov:.1f}%", f"{total_mr:,.0f} / {tgt_mr_td:,.0f} Target", delta_color="off")
            k2.metric("Tetanus-diphtheria (Td) Coverage", f"{td_cov:.1f}%", f"{total_td:,.0f} / {tgt_mr_td:,.0f} Target", delta_color="off")
            k3.metric("HPV Coverage (1st Dose)", f"{hpv_cov:.1f}%", f"{hpv_1st:,.0f} / {tgt_hpv:,.0f} Target", delta_color="off")
            
            st.divider()
            
            c1, c2, c3 = st.columns(3)
            with c1:
                fig_gauge_mr = go.Figure(go.Indicator(mode = "gauge+number", value = mr_cov, title = {'text': "MR (Grades 1 & 7)"}, gauge = {'axis': {'range': [None, 100]}, 'bar': {'color': "#1E88E5"}, 'bgcolor': "rgba(128,128,128,0.2)", 'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 95}}))
                fig_gauge_mr.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_gauge_mr, use_container_width=True, key="sbi_exec_gauge_mr")
                
            with c2:
                fig_gauge_td = go.Figure(go.Indicator(mode = "gauge+number", value = td_cov, title = {'text': "Td (Grades 1 & 7)"}, gauge = {'axis': {'range': [None, 100]}, 'bar': {'color': "#43A047"}, 'bgcolor': "rgba(128,128,128,0.2)", 'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 95}}))
                fig_gauge_td.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_gauge_td, use_container_width=True, key="sbi_exec_gauge_td")
                
            with c3:
                fig_gauge_hpv = go.Figure(go.Indicator(mode = "gauge+number", value = hpv_cov, title = {'text': "HPV 1st Dose (Grade 4 Female)"}, gauge = {'axis': {'range': [None, 100]}, 'bar': {'color': "#D81B60"}, 'bgcolor': "rgba(128,128,128,0.2)", 'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 90}}))
                fig_gauge_hpv.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_gauge_hpv, use_container_width=True, key="sbi_exec_gauge_hpv")

    # 2. TARGETS OVERVIEW
    with tab_sbi_target:
        st.markdown(f"### SBI Target Overview: {location_label}")

        target_tab_baseline, target_tab_actual, target_tab_compare = st.tabs([
            "Baseline Targets",
            "Actual Targets",
            "Baseline vs Actual",
        ])

        # ------------------------------------------
        # BASELINE TARGETS
        # ------------------------------------------
        with target_tab_baseline:
            if df_sbi_targets.empty:
                st.warning("Target database is empty. Please go to the Admin Panel tab to sync the database.")
            else:
                df_tgt_view = df_sbi_targets.copy()
                if view_mode == "Specific Municipality":
                    df_tgt_view = df_tgt_view[df_tgt_view['Municipality'].str.upper() == selected_muni.upper()]

                # Keep target fields numeric before building summaries/charts.
                sbi_target_numeric_cols = ['G1 Male', 'G1 Female', 'G1 Total', 'G4 Female', 'G7 Male', 'G7 Female', 'G7 Total']
                for col in sbi_target_numeric_cols:
                    if col in df_tgt_view.columns:
                        df_tgt_view[col] = pd.to_numeric(df_tgt_view[col], errors='coerce').fillna(0)

                st.markdown("#### Eligible Student Population by Grade Level")
                t1, t2, t3 = st.columns(3)
                t1.metric("Grade 1 (MR & Td)", f"{df_tgt_view['G1 Total'].sum():,.0f}", "Male & Female")
                t2.metric("Grade 4 (HPV)", f"{df_tgt_view['G4 Female'].sum():,.0f}", "Female Only")
                t3.metric("Grade 7 (MR & Td)", f"{df_tgt_view['G7 Total'].sum():,.0f}", "Male & Female")

                st.divider()

                # Shared geographic summary used by both the table and chart.
                geo_col = 'Municipality' if view_mode == "All Municipalities (Abra)" else 'Barangay'
                df_geo_tgt = (
                    df_tgt_view
                    .groupby(geo_col, dropna=False)[['G1 Total', 'G4 Female', 'G7 Total']]
                    .sum()
                    .reset_index()
                )
                df_geo_tgt['Total Eligible'] = df_geo_tgt['G1 Total'] + df_geo_tgt['G4 Female'] + df_geo_tgt['G7 Total']

                st.markdown(
                    '''<h4 style="margin-bottom:0.5rem;">
                    <i class="fa-solid fa-table-list" style="color:#0033A0; margin-right:8px;"></i>
                    Geographic Target Summary
                    </h4>''',
                    unsafe_allow_html=True
                )
                st.caption(
                    "Municipality-level target totals across Abra."
                    if view_mode == "All Municipalities (Abra)"
                    else f"Barangay-level target totals for {selected_muni}."
                )

                df_geo_table = df_geo_tgt.sort_values('Total Eligible', ascending=False).copy()
                st.dataframe(
                    df_geo_table,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        geo_col: st.column_config.TextColumn(geo_col),
                        'G1 Total': st.column_config.NumberColumn('Grade 1', format='%d'),
                        'G4 Female': st.column_config.NumberColumn('Grade 4 Female', format='%d'),
                        'G7 Total': st.column_config.NumberColumn('Grade 7', format='%d'),
                        'Total Eligible': st.column_config.NumberColumn('Total Eligible', format='%d'),
                    },
                )

                st.divider()

                st.markdown("#### Geographic Distribution of Eligible Students")
                df_geo_chart = df_geo_tgt.sort_values('Total Eligible', ascending=True).copy()
                df_melt_tgt = df_geo_chart.melt(
                    id_vars=[geo_col],
                    value_vars=['G1 Total', 'G4 Female', 'G7 Total'],
                    var_name='Grade Level',
                    value_name='Students'
                )

                fig_tgt_geo = px.bar(
                    df_melt_tgt,
                    x='Students',
                    y=geo_col,
                    color='Grade Level',
                    orientation='h',
                    text_auto='.0f',
                    color_discrete_sequence=['#1E88E5', '#D81B60', '#43A047']
                )
                fig_tgt_geo.update_layout(
                    dragmode=False,
                    plot_bgcolor='rgba(0,0,0,0)',
                    xaxis_title="Number of Eligible Students",
                    yaxis_title="",
                    height=max(400, len(df_geo_chart) * 45),
                    margin=dict(l=10, r=10, t=30, b=50),
                    legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
                    legend_title_text=""
                )
                st.plotly_chart(fig_tgt_geo, use_container_width=True, key="sbi_tgt_geo_bar")

                st.divider()

                st.markdown(
                    '''<h4 style="margin-bottom:0.5rem;">
                    <i class="fa-solid fa-school" style="color:#0033A0; margin-right:8px;"></i>
                    Targets by School
                    </h4>''',
                    unsafe_allow_html=True
                )
                st.caption("Grade 1, Grade 4 female, and Grade 7 targets for individual schools.")

                school_limit_label = st.selectbox(
                    "Schools shown in chart:",
                    ["Top 25", "Top 50", "All Schools"],
                    index=0,
                    key="sbi_school_target_chart_limit"
                )

                school_chart_cols = ['School ID', 'School Name', 'Municipality', 'Barangay', 'G1 Total', 'G4 Female', 'G7 Total']
                df_school_chart = df_tgt_view[[c for c in school_chart_cols if c in df_tgt_view.columns]].copy()
                df_school_chart['Total Eligible'] = (
                    df_school_chart.get('G1 Total', 0)
                    + df_school_chart.get('G4 Female', 0)
                    + df_school_chart.get('G7 Total', 0)
                )

                if view_mode == "All Municipalities (Abra)":
                    df_school_chart['School Label'] = (
                        df_school_chart['School Name'].astype(str).str.strip()
                        + ' - ' + df_school_chart['Municipality'].astype(str).str.strip()
                        + ' [' + df_school_chart['School ID'].astype(str).str.strip() + ']'
                    )
                else:
                    df_school_chart['School Label'] = (
                        df_school_chart['School Name'].astype(str).str.strip()
                        + ' [' + df_school_chart['School ID'].astype(str).str.strip() + ']'
                    )

                df_school_chart = df_school_chart.sort_values('Total Eligible', ascending=False)
                if school_limit_label == "Top 25":
                    df_school_chart_plot = df_school_chart.head(25).copy()
                elif school_limit_label == "Top 50":
                    df_school_chart_plot = df_school_chart.head(50).copy()
                else:
                    df_school_chart_plot = df_school_chart.copy()

                # Horizontal charts read from bottom to top, so reverse the selected ranking.
                df_school_chart_plot = df_school_chart_plot.sort_values('Total Eligible', ascending=True)
                df_school_melt = df_school_chart_plot.melt(
                    id_vars=['School Label'],
                    value_vars=['G1 Total', 'G4 Female', 'G7 Total'],
                    var_name='Grade Level',
                    value_name='Students'
                )

                fig_tgt_school = px.bar(
                    df_school_melt,
                    x='Students',
                    y='School Label',
                    color='Grade Level',
                    orientation='h',
                    text_auto='.0f',
                    color_discrete_sequence=['#1E88E5', '#D81B60', '#43A047']
                )
                fig_tgt_school.update_traces(textposition='inside', insidetextanchor='middle')
                fig_tgt_school.update_layout(
                    dragmode=False,
                    plot_bgcolor='rgba(0,0,0,0)',
                    xaxis_title="Number of Eligible Students",
                    yaxis_title="",
                    height=max(550, len(df_school_chart_plot) * 38),
                    margin=dict(l=10, r=10, t=30, b=60),
                    legend=dict(orientation="h", yanchor="top", y=-0.10, xanchor="center", x=0.5),
                    legend_title_text="",
                    bargap=0.18,
                )
                st.plotly_chart(
                    fig_tgt_school,
                    use_container_width=True,
                    key="sbi_tgt_school_bar",
                    config={
                        'scrollZoom': False,
                        'displayModeBar': True,
                        'toImageButtonOptions': {
                            'format': 'png',
                            'filename': f"SBI_School_Targets_{location_label.replace(', ', '_')}",
                            'scale': 2
                        }
                    }
                )

                st.divider()

                st.markdown(
                    '''<h4 style="margin-bottom:0.5rem;">
                    <i class="fa-solid fa-list" style="color:#0033A0; margin-right:8px;"></i>
                    Detailed School Target Baseline
                    </h4>''',
                    unsafe_allow_html=True
                )
                with st.expander("View and download detailed school targets", expanded=False):
                    df_school_view = df_tgt_view[['Municipality', 'Barangay', 'School ID', 'School Name', 'G1 Male', 'G1 Female', 'G1 Total', 'G4 Female', 'G7 Male', 'G7 Female', 'G7 Total']].copy()
                    df_school_view['Total Eligible'] = df_school_view['G1 Total'] + df_school_view['G4 Female'] + df_school_view['G7 Total']
                    df_school_view = df_school_view.sort_values(['Municipality', 'School Name'])

                    st.dataframe(
                        df_school_view,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            'G1 Male': st.column_config.NumberColumn('G1 Male', format='%d'),
                            'G1 Female': st.column_config.NumberColumn('G1 Female', format='%d'),
                            'G1 Total': st.column_config.NumberColumn('G1 Total', format='%d'),
                            'G4 Female': st.column_config.NumberColumn('G4 Female', format='%d'),
                            'G7 Male': st.column_config.NumberColumn('G7 Male', format='%d'),
                            'G7 Female': st.column_config.NumberColumn('G7 Female', format='%d'),
                            'G7 Total': st.column_config.NumberColumn('G7 Total', format='%d'),
                            'Total Eligible': st.column_config.NumberColumn('Total Eligible', format='%d'),
                        }
                    )

                    csv_sbi_tgt = df_school_view.to_csv(index=False).encode('utf-8-sig')
                    st.download_button(
                        label="Download School Targets (CSV)",
                        data=csv_sbi_tgt,
                        file_name=f"SBI_Targets_{location_label.replace(', ', '_')}.csv",
                        mime="text/csv",
                        key="dl_sbi_targets"
                    )

        # ------------------------------------------
        # ACTUAL TARGETS
        # ------------------------------------------
        with target_tab_actual:
            st.markdown(
                '''<h4 style="margin-bottom:0.25rem;">
                <i class="fa-solid fa-bullseye" style="color:#0033A0; margin-right:8px;"></i>
                RHU Actual Targets
                </h4>''',
                unsafe_allow_html=True
            )
            st.caption(
                "School-level actual targets are read directly from the 'Actual Targets' worksheet in the SBI Google Sheet."
            )

            if df_sbi_actual_targets.empty:
                st.warning(
                    "No Actual Targets data is available. Confirm that the SBI Google Sheet contains a worksheet named 'Actual Targets'."
                )
            else:
                df_actual_view = df_sbi_actual_targets.copy()
                df_baseline_actual_view = df_sbi_targets.copy()

                # Normalize School IDs so Google Sheets and Supabase values merge reliably.
                for df_norm in [df_actual_view, df_baseline_actual_view]:
                    if 'School ID' in df_norm.columns:
                        df_norm['School ID'] = (
                            df_norm['School ID']
                            .astype(str)
                            .str.strip()
                            .str.replace(r'\.0$', '', regex=True)
                        )

                if view_mode == "Specific Municipality":
                    df_actual_view = df_actual_view[
                        df_actual_view['Municipality'].str.upper() == selected_muni.upper()
                    ]
                    df_baseline_actual_view = df_baseline_actual_view[
                        df_baseline_actual_view['Municipality'].str.upper() == selected_muni.upper()
                    ]

                actual_numeric_cols = [
                    'G1 Male', 'G1 Female', 'G1 Total', 'G4 Female',
                    'G7 Male', 'G7 Female', 'G7 Total', 'Total Eligible'
                ]
                for col in actual_numeric_cols:
                    if col in df_actual_view.columns:
                        df_actual_view[col] = pd.to_numeric(df_actual_view[col], errors='coerce').fillna(0)

                baseline_numeric_cols = ['G1 Total', 'G4 Female', 'G7 Total']
                for col in baseline_numeric_cols:
                    if col in df_baseline_actual_view.columns:
                        df_baseline_actual_view[col] = pd.to_numeric(
                            df_baseline_actual_view[col], errors='coerce'
                        ).fillna(0)

                # Reporting status.
                total_actual_schools = len(df_actual_view)
                complete_schools = int((df_actual_view['Target Entry Status'] == 'Complete').sum())
                partial_schools = int((df_actual_view['Target Entry Status'] == 'Partial').sum())
                pending_schools = int((df_actual_view['Target Entry Status'] == 'Pending').sum())
                completion_pct = (
                    complete_schools / total_actual_schools * 100
                    if total_actual_schools > 0 else 0
                )

                # Actual target totals are intentionally limited to Complete rows so
                # partially entered schools do not produce misleading totals.
                df_actual_complete = df_actual_view[
                    df_actual_view['Target Entry Status'] == 'Complete'
                ].copy()
                actual_g1 = df_actual_complete['G1 Total'].sum()
                actual_g4 = df_actual_complete['G4 Female'].sum()
                actual_g7 = df_actual_complete['G7 Total'].sum()

                a1, a2, a3, a4 = st.columns(4)
                a1.metric(
                    "Schools Complete",
                    f"{complete_schools:,} / {total_actual_schools:,}",
                    f"{completion_pct:.1f}% complete",
                    delta_color="off"
                )
                a2.metric(
                    "Grade 1 Actual Target",
                    f"{actual_g1:,.0f}",
                    "Complete schools only",
                    delta_color="off"
                )
                a3.metric(
                    "Grade 4 Actual Target",
                    f"{actual_g4:,.0f}",
                    "Complete schools only",
                    delta_color="off"
                )
                a4.metric(
                    "Grade 7 Actual Target",
                    f"{actual_g7:,.0f}",
                    "Complete schools only",
                    delta_color="off"
                )

                st.caption(
                    f"Partial entries: {partial_schools:,} | Pending schools: {pending_schools:,}. "
                    "Actual-target KPI totals use Complete school submissions only."
                )

                st.progress(
                    min(max(completion_pct / 100, 0.0), 1.0),
                    text=f"Actual target reporting completion: {completion_pct:.1f}%"
                )

                st.divider()

                # Geographic reporting and actual-target summary.
                geo_col_actual = 'Municipality' if view_mode == "All Municipalities (Abra)" else 'Barangay'
                df_actual_geo_source = df_actual_view.copy()
                df_actual_geo_source['Complete Count'] = (df_actual_geo_source['Target Entry Status'] == 'Complete').astype(int)
                df_actual_geo_source['Partial Count'] = (df_actual_geo_source['Target Entry Status'] == 'Partial').astype(int)
                df_actual_geo_source['Pending Count'] = (df_actual_geo_source['Target Entry Status'] == 'Pending').astype(int)

                df_actual_geo = (
                    df_actual_geo_source
                    .groupby(geo_col_actual, dropna=False)
                    .agg(
                        Schools=('School ID', 'count'),
                        Complete=('Complete Count', 'sum'),
                        Partial=('Partial Count', 'sum'),
                        Pending=('Pending Count', 'sum'),
                        G1_Actual=('G1 Total', 'sum'),
                        G4_Actual=('G4 Female', 'sum'),
                        G7_Actual=('G7 Total', 'sum'),
                        Total_Actual=('Total Eligible', 'sum'),
                    )
                    .reset_index()
                )
                df_actual_geo['Completion %'] = np.where(
                    df_actual_geo['Schools'] > 0,
                    df_actual_geo['Complete'] / df_actual_geo['Schools'] * 100,
                    0
                )
                df_actual_geo = df_actual_geo.rename(columns={
                    'G1_Actual': 'G1 Total',
                    'G4_Actual': 'G4 Female',
                    'G7_Actual': 'G7 Total',
                    'Total_Actual': 'Total Eligible',
                })

                st.markdown(
                    '''<h4 style="margin-bottom:0.5rem;">
                    <i class="fa-solid fa-clipboard-check" style="color:#0033A0; margin-right:8px;"></i>
                    Actual Target Reporting Summary
                    </h4>''',
                    unsafe_allow_html=True
                )
                st.caption(
                    "Municipality-level reporting progress and entered actual targets across Abra."
                    if view_mode == "All Municipalities (Abra)"
                    else f"Barangay-level reporting progress and entered actual targets for {selected_muni}."
                )

                df_actual_geo_table = df_actual_geo.sort_values(
                    ['Completion %', 'Total Eligible'], ascending=[False, False]
                ).copy()
                st.dataframe(
                    df_actual_geo_table,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        geo_col_actual: st.column_config.TextColumn(geo_col_actual),
                        'Schools': st.column_config.NumberColumn('Schools', format='%d'),
                        'Complete': st.column_config.NumberColumn('Complete', format='%d'),
                        'Partial': st.column_config.NumberColumn('Partial', format='%d'),
                        'Pending': st.column_config.NumberColumn('Pending', format='%d'),
                        'Completion %': st.column_config.NumberColumn('Completion', format='%.1f%%'),
                        'G1 Total': st.column_config.NumberColumn('Grade 1 Actual', format='%d'),
                        'G4 Female': st.column_config.NumberColumn('Grade 4 Female Actual', format='%d'),
                        'G7 Total': st.column_config.NumberColumn('Grade 7 Actual', format='%d'),
                        'Total Eligible': st.column_config.NumberColumn('Total Actual', format='%d'),
                    }
                )

                # Reporting status chart.
                df_status_chart = df_actual_geo[[
                    geo_col_actual, 'Complete', 'Partial', 'Pending'
                ]].melt(
                    id_vars=[geo_col_actual],
                    value_vars=['Complete', 'Partial', 'Pending'],
                    var_name='Reporting Status',
                    value_name='Schools'
                )
                fig_actual_status = px.bar(
                    df_status_chart,
                    x='Schools',
                    y=geo_col_actual,
                    color='Reporting Status',
                    orientation='h',
                    barmode='stack',
                    text_auto='.0f',
                    color_discrete_sequence=['#43A047', '#FB8C00', '#90A4AE']
                )
                fig_actual_status.update_layout(
                    dragmode=False,
                    plot_bgcolor='rgba(0,0,0,0)',
                    xaxis_title='Number of Schools',
                    yaxis_title='',
                    height=max(400, len(df_actual_geo) * 45),
                    margin=dict(l=10, r=10, t=20, b=50),
                    legend=dict(orientation='h', yanchor='top', y=-0.15, xanchor='center', x=0.5),
                    legend_title_text=''
                )
                st.plotly_chart(
                    fig_actual_status,
                    use_container_width=True,
                    key='sbi_actual_reporting_status_chart'
                )

                st.divider()

                # Geographic distribution of the actual targets entered so far.
                st.markdown("#### Geographic Distribution of Actual Targets")
                df_actual_geo_chart = df_actual_geo.sort_values('Total Eligible', ascending=True).copy()
                df_actual_geo_melt = df_actual_geo_chart.melt(
                    id_vars=[geo_col_actual],
                    value_vars=['G1 Total', 'G4 Female', 'G7 Total'],
                    var_name='Grade Level',
                    value_name='Students'
                )

                fig_actual_geo = px.bar(
                    df_actual_geo_melt,
                    x='Students',
                    y=geo_col_actual,
                    color='Grade Level',
                    orientation='h',
                    text_auto='.0f',
                    color_discrete_sequence=['#1E88E5', '#D81B60', '#43A047']
                )
                fig_actual_geo.update_layout(
                    dragmode=False,
                    plot_bgcolor='rgba(0,0,0,0)',
                    xaxis_title='Number of Eligible Students',
                    yaxis_title='',
                    height=max(400, len(df_actual_geo_chart) * 45),
                    margin=dict(l=10, r=10, t=30, b=50),
                    legend=dict(orientation='h', yanchor='top', y=-0.15, xanchor='center', x=0.5),
                    legend_title_text=''
                )
                st.plotly_chart(
                    fig_actual_geo,
                    use_container_width=True,
                    key='sbi_actual_geo_chart'
                )

                st.divider()

                # School-level actual target chart. Pending rows are excluded so the
                # ranking is based on schools that have at least started reporting.
                st.markdown(
                    '''<h4 style="margin-bottom:0.5rem;">
                    <i class="fa-solid fa-school" style="color:#0033A0; margin-right:8px;"></i>
                    Actual Targets by School
                    </h4>''',
                    unsafe_allow_html=True
                )
                st.caption("Schools with Pending target entries are excluded from this chart.")

                actual_school_limit = st.selectbox(
                    "Schools shown in actual-target chart:",
                    ["Top 25", "Top 50", "All Schools"],
                    index=0,
                    key="sbi_actual_school_chart_limit"
                )

                df_actual_school = df_actual_view[
                    df_actual_view['Target Entry Status'] != 'Pending'
                ].copy()

                if df_actual_school.empty:
                    st.info("No school has entered an actual target yet.")
                else:
                    if view_mode == "All Municipalities (Abra)":
                        df_actual_school['School Label'] = (
                            df_actual_school['School Name'].astype(str).str.strip()
                            + ' - ' + df_actual_school['Municipality'].astype(str).str.strip()
                            + ' [' + df_actual_school['School ID'].astype(str).str.strip() + ']'
                        )
                    else:
                        df_actual_school['School Label'] = (
                            df_actual_school['School Name'].astype(str).str.strip()
                            + ' [' + df_actual_school['School ID'].astype(str).str.strip() + ']'
                        )

                    df_actual_school = df_actual_school.sort_values('Total Eligible', ascending=False)
                    if actual_school_limit == "Top 25":
                        df_actual_school_plot = df_actual_school.head(25).copy()
                    elif actual_school_limit == "Top 50":
                        df_actual_school_plot = df_actual_school.head(50).copy()
                    else:
                        df_actual_school_plot = df_actual_school.copy()

                    df_actual_school_plot = df_actual_school_plot.sort_values('Total Eligible', ascending=True)
                    df_actual_school_melt = df_actual_school_plot.melt(
                        id_vars=['School Label'],
                        value_vars=['G1 Total', 'G4 Female', 'G7 Total'],
                        var_name='Grade Level',
                        value_name='Students'
                    )

                    fig_actual_school = px.bar(
                        df_actual_school_melt,
                        x='Students',
                        y='School Label',
                        color='Grade Level',
                        orientation='h',
                        text_auto='.0f',
                        color_discrete_sequence=['#1E88E5', '#D81B60', '#43A047']
                    )
                    fig_actual_school.update_traces(textposition='inside', insidetextanchor='middle')
                    fig_actual_school.update_layout(
                        dragmode=False,
                        plot_bgcolor='rgba(0,0,0,0)',
                        xaxis_title='Number of Eligible Students',
                        yaxis_title='',
                        height=max(550, len(df_actual_school_plot) * 38),
                        margin=dict(l=10, r=10, t=30, b=60),
                        legend=dict(orientation='h', yanchor='top', y=-0.10, xanchor='center', x=0.5),
                        legend_title_text='',
                        bargap=0.18,
                    )
                    st.plotly_chart(
                        fig_actual_school,
                        use_container_width=True,
                        key='sbi_actual_school_chart',
                        config={
                            'scrollZoom': False,
                            'displayModeBar': True,
                            'toImageButtonOptions': {
                                'format': 'png',
                                'filename': f"SBI_Actual_Targets_{location_label.replace(', ', '_')}",
                                'scale': 2
                            }
                        }
                    )

                st.divider()

                # Detailed actual-target table. Comparison fields live in the
                # dedicated Baseline vs Actual sub-tab below.
                st.markdown(
                    '''<h4 style="margin-bottom:0.5rem;">
                    <i class="fa-solid fa-list-check" style="color:#0033A0; margin-right:8px;"></i>
                    Detailed Actual School Targets
                    </h4>''',
                    unsafe_allow_html=True
                )

                with st.expander("View and download detailed actual school targets", expanded=False):
                    actual_table_cols = [
                        'Municipality', 'Barangay', 'School ID', 'School Name',
                        'Target Entry Status', 'Target Fields Entered',
                        'G1 Male', 'G1 Female', 'G1 Total', 'G4 Female',
                        'G7 Male', 'G7 Female', 'G7 Total', 'Total Eligible'
                    ]
                    df_actual_table = df_actual_view[
                        [c for c in actual_table_cols if c in df_actual_view.columns]
                    ].copy()

                    status_order = pd.CategoricalDtype(
                        categories=['Complete', 'Partial', 'Pending'],
                        ordered=True
                    )
                    df_actual_table['Target Entry Status'] = df_actual_table['Target Entry Status'].astype(status_order)
                    df_actual_table = df_actual_table.sort_values(
                        ['Target Entry Status', 'Municipality', 'School Name']
                    )
                    df_actual_table['Target Entry Status'] = df_actual_table['Target Entry Status'].astype(str)

                    st.dataframe(
                        df_actual_table,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            'Target Fields Entered': st.column_config.NumberColumn('Fields Entered', format='%d'),
                            'G1 Male': st.column_config.NumberColumn('G1 Male', format='%d'),
                            'G1 Female': st.column_config.NumberColumn('G1 Female', format='%d'),
                            'G1 Total': st.column_config.NumberColumn('Actual G1', format='%d'),
                            'G4 Female': st.column_config.NumberColumn('Actual G4 Female', format='%d'),
                            'G7 Male': st.column_config.NumberColumn('G7 Male', format='%d'),
                            'G7 Female': st.column_config.NumberColumn('G7 Female', format='%d'),
                            'G7 Total': st.column_config.NumberColumn('Actual G7', format='%d'),
                            'Total Eligible': st.column_config.NumberColumn('Actual Total', format='%d'),
                        }
                    )

                    csv_actual_targets = df_actual_table.to_csv(index=False).encode('utf-8-sig')
                    st.download_button(
                        label="Download Actual School Targets (CSV)",
                        data=csv_actual_targets,
                        file_name=f"SBI_Actual_Targets_{location_label.replace(', ', '_')}.csv",
                        mime="text/csv",
                        key="dl_sbi_actual_targets"
                    )

        # ------------------------------------------
        # BASELINE VS ACTUAL COMPARISON
        # ------------------------------------------
        with target_tab_compare:
            st.markdown(
                '''<h4 style="margin-bottom:0.25rem;">
                <i class="fa-solid fa-code-compare" style="color:#0033A0; margin-right:8px;"></i>
                Baseline vs Actual Target Comparison
                </h4>''',
                unsafe_allow_html=True
            )
            st.caption(
                "Comparisons use only schools with Complete actual-target submissions. "
                "Partial and pending entries are excluded to avoid understating actual targets."
            )

            if df_sbi_targets.empty:
                st.warning("Baseline target data is unavailable.")
            elif df_sbi_actual_targets.empty:
                st.warning("Actual target data is unavailable.")
            else:
                df_cmp_actual = df_sbi_actual_targets.copy()
                df_cmp_baseline = df_sbi_targets.copy()

                # Normalize School IDs so the two sources join reliably.
                for df_norm in [df_cmp_actual, df_cmp_baseline]:
                    if 'School ID' in df_norm.columns:
                        df_norm['School ID'] = (
                            df_norm['School ID']
                            .astype(str)
                            .str.strip()
                            .str.replace(r'\.0$', '', regex=True)
                        )

                if view_mode == "Specific Municipality":
                    df_cmp_actual = df_cmp_actual[
                        df_cmp_actual['Municipality'].str.upper() == selected_muni.upper()
                    ].copy()
                    df_cmp_baseline = df_cmp_baseline[
                        df_cmp_baseline['Municipality'].str.upper() == selected_muni.upper()
                    ].copy()

                cmp_actual_numeric = [
                    'G1 Total', 'G4 Female', 'G7 Total', 'Total Eligible'
                ]
                for col in cmp_actual_numeric:
                    if col in df_cmp_actual.columns:
                        df_cmp_actual[col] = pd.to_numeric(
                            df_cmp_actual[col], errors='coerce'
                        ).fillna(0)

                cmp_baseline_numeric = ['G1 Total', 'G4 Female', 'G7 Total']
                for col in cmp_baseline_numeric:
                    if col in df_cmp_baseline.columns:
                        df_cmp_baseline[col] = pd.to_numeric(
                            df_cmp_baseline[col], errors='coerce'
                        ).fillna(0)

                df_cmp_baseline['Baseline Total'] = (
                    df_cmp_baseline['G1 Total']
                    + df_cmp_baseline['G4 Female']
                    + df_cmp_baseline['G7 Total']
                )

                df_cmp_actual_complete = df_cmp_actual[
                    df_cmp_actual['Target Entry Status'] == 'Complete'
                ].copy()

                complete_cmp_ids = set(
                    df_cmp_actual_complete['School ID'].astype(str).str.strip().tolist()
                )
                df_cmp_baseline_complete = df_cmp_baseline[
                    df_cmp_baseline['School ID'].astype(str).str.strip().isin(complete_cmp_ids)
                ].copy()

                if df_cmp_actual_complete.empty:
                    st.info(
                        "No school has a Complete actual-target submission yet, so comparison analytics are not available."
                    )
                else:
                    # ------------------------------------------
                    # OVERALL COMPARISON KPIs
                    # ------------------------------------------
                    baseline_total_cmp = df_cmp_baseline_complete['Baseline Total'].sum()
                    actual_total_cmp = df_cmp_actual_complete['Total Eligible'].sum()
                    total_diff_cmp = actual_total_cmp - baseline_total_cmp
                    total_change_cmp = (
                        total_diff_cmp / baseline_total_cmp * 100
                        if baseline_total_cmp > 0 else 0
                    )

                    k1, k2, k3, k4 = st.columns(4)
                    k1.metric(
                        "Schools Compared",
                        f"{len(df_cmp_actual_complete):,}",
                        "Complete submissions only",
                        delta_color="off"
                    )
                    k2.metric(
                        "Baseline Total",
                        f"{baseline_total_cmp:,.0f}",
                        "Matched schools",
                        delta_color="off"
                    )
                    k3.metric(
                        "Actual Total",
                        f"{actual_total_cmp:,.0f}",
                        "Matched schools",
                        delta_color="off"
                    )
                    k4.metric(
                        "Overall Difference",
                        f"{total_diff_cmp:+,.0f}",
                        f"{total_change_cmp:+.1f}% vs baseline",
                        delta_color="off"
                    )

                    st.divider()

                    # ------------------------------------------
                    # GRADE-LEVEL COMPARISON
                    # ------------------------------------------
                    st.markdown(
                        '''<h4 style="margin-bottom:0.5rem;">
                        <i class="fa-solid fa-chart-column" style="color:#0033A0; margin-right:8px;"></i>
                        Comparison by Grade Level
                        </h4>''',
                        unsafe_allow_html=True
                    )

                    df_target_compare = pd.DataFrame({
                        'Grade Level': ['Grade 1', 'Grade 4 Female', 'Grade 7'],
                        'Baseline': [
                            df_cmp_baseline_complete['G1 Total'].sum(),
                            df_cmp_baseline_complete['G4 Female'].sum(),
                            df_cmp_baseline_complete['G7 Total'].sum(),
                        ],
                        'Actual': [
                            df_cmp_actual_complete['G1 Total'].sum(),
                            df_cmp_actual_complete['G4 Female'].sum(),
                            df_cmp_actual_complete['G7 Total'].sum(),
                        ],
                    })
                    df_target_compare['Difference'] = (
                        df_target_compare['Actual'] - df_target_compare['Baseline']
                    )
                    df_target_compare['Change %'] = np.where(
                        df_target_compare['Baseline'] > 0,
                        df_target_compare['Difference'] / df_target_compare['Baseline'] * 100,
                        0
                    )

                    st.dataframe(
                        df_target_compare,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            'Baseline': st.column_config.NumberColumn('Baseline', format='%d'),
                            'Actual': st.column_config.NumberColumn('Actual', format='%d'),
                            'Difference': st.column_config.NumberColumn('Difference', format='%+d'),
                            'Change %': st.column_config.NumberColumn('Change', format='%+.1f%%'),
                        }
                    )

                    df_target_compare_melt = df_target_compare.melt(
                        id_vars='Grade Level',
                        value_vars=['Baseline', 'Actual'],
                        var_name='Target Type',
                        value_name='Students'
                    )
                    fig_target_compare = px.bar(
                        df_target_compare_melt,
                        x='Grade Level',
                        y='Students',
                        color='Target Type',
                        barmode='group',
                        text_auto='.0f',
                        color_discrete_sequence=['#1E88E5', '#43A047']
                    )
                    fig_target_compare.update_traces(textposition='outside')
                    fig_target_compare.update_layout(
                        dragmode=False,
                        plot_bgcolor='rgba(0,0,0,0)',
                        xaxis_title='',
                        yaxis_title='Number of Eligible Students',
                        height=450,
                        margin=dict(l=10, r=10, t=30, b=50),
                        legend=dict(
                            orientation='h',
                            yanchor='top', y=-0.15,
                            xanchor='center', x=0.5
                        ),
                        legend_title_text=''
                    )
                    st.plotly_chart(
                        fig_target_compare,
                        use_container_width=True,
                        key='sbi_compare_grade_chart'
                    )

                    st.divider()

                    # ------------------------------------------
                    # GEOGRAPHIC COMPARISON
                    # ------------------------------------------
                    geo_col_cmp = (
                        'Municipality'
                        if view_mode == "All Municipalities (Abra)"
                        else 'Barangay'
                    )

                    baseline_geo_cmp = (
                        df_cmp_baseline_complete
                        .groupby(geo_col_cmp, dropna=False)
                        .agg(
                            Baseline_G1=('G1 Total', 'sum'),
                            Baseline_G4=('G4 Female', 'sum'),
                            Baseline_G7=('G7 Total', 'sum'),
                            Baseline_Total=('Baseline Total', 'sum'),
                            Schools=('School ID', 'nunique'),
                        )
                        .reset_index()
                    )
                    actual_geo_cmp = (
                        df_cmp_actual_complete
                        .groupby(geo_col_cmp, dropna=False)
                        .agg(
                            Actual_G1=('G1 Total', 'sum'),
                            Actual_G4=('G4 Female', 'sum'),
                            Actual_G7=('G7 Total', 'sum'),
                            Actual_Total=('Total Eligible', 'sum'),
                        )
                        .reset_index()
                    )
                    df_geo_compare = baseline_geo_cmp.merge(
                        actual_geo_cmp,
                        on=geo_col_cmp,
                        how='outer'
                    ).fillna(0)
                    df_geo_compare['Difference'] = (
                        df_geo_compare['Actual_Total'] - df_geo_compare['Baseline_Total']
                    )
                    df_geo_compare['Change %'] = np.where(
                        df_geo_compare['Baseline_Total'] > 0,
                        df_geo_compare['Difference'] / df_geo_compare['Baseline_Total'] * 100,
                        0
                    )

                    st.markdown(
                        '''<h4 style="margin-bottom:0.5rem;">
                        <i class="fa-solid fa-map-location-dot" style="color:#0033A0; margin-right:8px;"></i>
                        Geographic Comparison
                        </h4>''',
                        unsafe_allow_html=True
                    )

                    df_geo_compare_table = df_geo_compare.sort_values(
                        'Actual_Total', ascending=False
                    ).copy()
                    st.dataframe(
                        df_geo_compare_table,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            'Schools': st.column_config.NumberColumn('Schools', format='%d'),
                            'Baseline_G1': st.column_config.NumberColumn('Baseline G1', format='%d'),
                            'Actual_G1': st.column_config.NumberColumn('Actual G1', format='%d'),
                            'Baseline_G4': st.column_config.NumberColumn('Baseline G4 Female', format='%d'),
                            'Actual_G4': st.column_config.NumberColumn('Actual G4 Female', format='%d'),
                            'Baseline_G7': st.column_config.NumberColumn('Baseline G7', format='%d'),
                            'Actual_G7': st.column_config.NumberColumn('Actual G7', format='%d'),
                            'Baseline_Total': st.column_config.NumberColumn('Baseline Total', format='%d'),
                            'Actual_Total': st.column_config.NumberColumn('Actual Total', format='%d'),
                            'Difference': st.column_config.NumberColumn('Difference', format='%+d'),
                            'Change %': st.column_config.NumberColumn('Change', format='%+.1f%%'),
                        }
                    )

                    df_geo_compare_chart = df_geo_compare.sort_values(
                        'Actual_Total', ascending=True
                    ).copy()
                    df_geo_compare_melt = df_geo_compare_chart.melt(
                        id_vars=[geo_col_cmp],
                        value_vars=['Baseline_Total', 'Actual_Total'],
                        var_name='Target Type',
                        value_name='Students'
                    )
                    df_geo_compare_melt['Target Type'] = df_geo_compare_melt['Target Type'].map({
                        'Baseline_Total': 'Baseline',
                        'Actual_Total': 'Actual',
                    })
                    fig_geo_compare = px.bar(
                        df_geo_compare_melt,
                        x='Students',
                        y=geo_col_cmp,
                        color='Target Type',
                        orientation='h',
                        barmode='group',
                        text_auto='.0f',
                        color_discrete_sequence=['#1E88E5', '#43A047']
                    )
                    fig_geo_compare.update_layout(
                        dragmode=False,
                        plot_bgcolor='rgba(0,0,0,0)',
                        xaxis_title='Number of Eligible Students',
                        yaxis_title='',
                        height=max(450, len(df_geo_compare_chart) * 55),
                        margin=dict(l=10, r=20, t=30, b=55),
                        legend=dict(
                            orientation='h',
                            yanchor='top', y=-0.12,
                            xanchor='center', x=0.5
                        ),
                        legend_title_text=''
                    )
                    st.plotly_chart(
                        fig_geo_compare,
                        use_container_width=True,
                        key='sbi_compare_geo_chart'
                    )

                    st.divider()

                    # ------------------------------------------
                    # SCHOOL-LEVEL COMPARISON
                    # ------------------------------------------
                    st.markdown(
                        '''<h4 style="margin-bottom:0.5rem;">
                        <i class="fa-solid fa-school" style="color:#0033A0; margin-right:8px;"></i>
                        School-Level Comparison
                        </h4>''',
                        unsafe_allow_html=True
                    )

                    baseline_school_cmp = df_cmp_baseline_complete[[
                        'School ID', 'G1 Total', 'G4 Female', 'G7 Total', 'Baseline Total'
                    ]].copy().rename(columns={
                        'G1 Total': 'Baseline G1',
                        'G4 Female': 'Baseline G4 Female',
                        'G7 Total': 'Baseline G7',
                    })

                    actual_school_cols = [
                        'Municipality', 'Barangay', 'School ID', 'School Name',
                        'G1 Total', 'G4 Female', 'G7 Total', 'Total Eligible'
                    ]
                    df_school_compare = df_cmp_actual_complete[
                        [c for c in actual_school_cols if c in df_cmp_actual_complete.columns]
                    ].copy().rename(columns={
                        'G1 Total': 'Actual G1',
                        'G4 Female': 'Actual G4 Female',
                        'G7 Total': 'Actual G7',
                        'Total Eligible': 'Actual Total',
                    })
                    df_school_compare = df_school_compare.merge(
                        baseline_school_cmp,
                        on='School ID',
                        how='left'
                    )
                    df_school_compare['G1 Difference'] = (
                        df_school_compare['Actual G1'] - df_school_compare['Baseline G1']
                    )
                    df_school_compare['G4 Difference'] = (
                        df_school_compare['Actual G4 Female'] - df_school_compare['Baseline G4 Female']
                    )
                    df_school_compare['G7 Difference'] = (
                        df_school_compare['Actual G7'] - df_school_compare['Baseline G7']
                    )
                    df_school_compare['Total Difference'] = (
                        df_school_compare['Actual Total'] - df_school_compare['Baseline Total']
                    )
                    df_school_compare['Change %'] = np.where(
                        df_school_compare['Baseline Total'] > 0,
                        df_school_compare['Total Difference'] / df_school_compare['Baseline Total'] * 100,
                        0
                    )

                    if view_mode == "All Municipalities (Abra)":
                        df_school_compare['School Label'] = (
                            df_school_compare['School Name'].astype(str).str.strip()
                            + ' - ' + df_school_compare['Municipality'].astype(str).str.strip()
                            + ' [' + df_school_compare['School ID'].astype(str).str.strip() + ']'
                        )
                    else:
                        df_school_compare['School Label'] = (
                            df_school_compare['School Name'].astype(str).str.strip()
                            + ' [' + df_school_compare['School ID'].astype(str).str.strip() + ']'
                        )

                    compare_school_limit = st.selectbox(
                        "Schools shown in comparison chart:",
                        ["Top 25 by absolute difference", "Top 50 by absolute difference", "All Schools"],
                        index=0,
                        key='sbi_compare_school_limit'
                    )

                    df_school_compare_plot = df_school_compare.copy()
                    df_school_compare_plot['Absolute Difference'] = (
                        df_school_compare_plot['Total Difference'].abs()
                    )
                    df_school_compare_plot = df_school_compare_plot.sort_values(
                        'Absolute Difference', ascending=False
                    )
                    if compare_school_limit.startswith('Top 25'):
                        df_school_compare_plot = df_school_compare_plot.head(25)
                    elif compare_school_limit.startswith('Top 50'):
                        df_school_compare_plot = df_school_compare_plot.head(50)

                    df_school_compare_plot = df_school_compare_plot.sort_values(
                        'Actual Total', ascending=True
                    )
                    df_school_compare_melt = df_school_compare_plot.melt(
                        id_vars=['School Label'],
                        value_vars=['Baseline Total', 'Actual Total'],
                        var_name='Target Type',
                        value_name='Students'
                    )
                    fig_school_compare = px.bar(
                        df_school_compare_melt,
                        x='Students',
                        y='School Label',
                        color='Target Type',
                        orientation='h',
                        barmode='group',
                        text_auto='.0f',
                        color_discrete_sequence=['#1E88E5', '#43A047']
                    )
                    fig_school_compare.update_layout(
                        dragmode=False,
                        plot_bgcolor='rgba(0,0,0,0)',
                        xaxis_title='Number of Eligible Students',
                        yaxis_title='',
                        height=max(550, len(df_school_compare_plot) * 42),
                        margin=dict(l=10, r=20, t=30, b=60),
                        legend=dict(
                            orientation='h',
                            yanchor='top', y=-0.10,
                            xanchor='center', x=0.5
                        ),
                        legend_title_text='',
                        bargap=0.15
                    )
                    st.plotly_chart(
                        fig_school_compare,
                        use_container_width=True,
                        key='sbi_compare_school_chart',
                        config={
                            'scrollZoom': False,
                            'displayModeBar': True,
                            'toImageButtonOptions': {
                                'format': 'png',
                                'filename': f"SBI_Baseline_vs_Actual_{location_label.replace(', ', '_')}",
                                'scale': 2
                            }
                        }
                    )

                    st.divider()

                    # ------------------------------------------
                    # DETAILED COMPARISON TABLE
                    # ------------------------------------------
                    st.markdown(
                        '''<h4 style="margin-bottom:0.5rem;">
                        <i class="fa-solid fa-table" style="color:#0033A0; margin-right:8px;"></i>
                        Detailed School Comparison
                        </h4>''',
                        unsafe_allow_html=True
                    )

                    with st.expander(
                        "View and download detailed baseline vs actual comparison",
                        expanded=False
                    ):
                        comparison_display_cols = [
                            'Municipality', 'Barangay', 'School ID', 'School Name',
                            'Baseline G1', 'Actual G1', 'G1 Difference',
                            'Baseline G4 Female', 'Actual G4 Female', 'G4 Difference',
                            'Baseline G7', 'Actual G7', 'G7 Difference',
                            'Baseline Total', 'Actual Total', 'Total Difference', 'Change %'
                        ]
                        df_school_compare_table = df_school_compare[
                            [c for c in comparison_display_cols if c in df_school_compare.columns]
                        ].copy().sort_values(
                            ['Municipality', 'School Name']
                        )

                        st.dataframe(
                            df_school_compare_table,
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                'Baseline G1': st.column_config.NumberColumn('Baseline G1', format='%d'),
                                'Actual G1': st.column_config.NumberColumn('Actual G1', format='%d'),
                                'G1 Difference': st.column_config.NumberColumn('G1 Difference', format='%+d'),
                                'Baseline G4 Female': st.column_config.NumberColumn('Baseline G4 Female', format='%d'),
                                'Actual G4 Female': st.column_config.NumberColumn('Actual G4 Female', format='%d'),
                                'G4 Difference': st.column_config.NumberColumn('G4 Difference', format='%+d'),
                                'Baseline G7': st.column_config.NumberColumn('Baseline G7', format='%d'),
                                'Actual G7': st.column_config.NumberColumn('Actual G7', format='%d'),
                                'G7 Difference': st.column_config.NumberColumn('G7 Difference', format='%+d'),
                                'Baseline Total': st.column_config.NumberColumn('Baseline Total', format='%d'),
                                'Actual Total': st.column_config.NumberColumn('Actual Total', format='%d'),
                                'Total Difference': st.column_config.NumberColumn('Total Difference', format='%+d'),
                                'Change %': st.column_config.NumberColumn('Change', format='%+.1f%%'),
                            }
                        )

                        csv_compare = df_school_compare_table.to_csv(index=False).encode('utf-8-sig')
                        st.download_button(
                            label="Download Baseline vs Actual Comparison (CSV)",
                            data=csv_compare,
                            file_name=f"SBI_Baseline_vs_Actual_{location_label.replace(', ', '_')}.csv",
                            mime='text/csv',
                            key='dl_sbi_target_comparison'
                        )



    # 3. MR & TD (GRADES 1 & 7)
    with tab_sbi_mr:
        st.markdown(f"### Measles-Rubella (MR) & Tetanus-diphtheria (Td): {location_label}")
        st.info("Chart module under construction.")
        
    # 4. HPV (GRADE 4)
    with tab_sbi_hpv:
        st.markdown(f"### Human Papillomavirus (HPV) - Female Students: {location_label}")
        st.info("Chart module under construction.")

    # 5. DEFERRALS & REFUSALS
    with tab_sbi_def:
        st.markdown(f"### Vaccine Deferrals & Refusals Analysis: {location_label}")
        st.info("Chart module under construction.")

    # 6. ADMIN PANEL (SBI SYNC)
    with tab_sbi_admin:
        st.markdown("### System Administration")
        if st.session_state.get('user_role') != "System Admin":
            st.info("This section is restricted to authenticated System Administrators.")
        else:
            st.success("Administrator controls unlocked.")
            st.divider()
            
            st.markdown("### Phase 1: SBI Target Database Sync")
            st.write("Pull, clean, and compress the official DepEd Enrollment baseline.")
            
            if st.button("Sync SBI Target Database", type="secondary", use_container_width=True, key="sync_sbi_targets_btn"):
                with st.spinner("Downloading and processing DepEd master sheet..."):
                    try:
                        conn = st.connection("gsheets", type=GSheetsConnection)
                        
                        # THE FIX: Changed skiprows from 4 to 5 to accurately grab the header row!
                        df_raw = conn.read(spreadsheet=SBI_SHEET_URL, worksheet="Target by School", skiprows=5, ttl=0)
                        
                        if df_raw.empty:
                            st.error("Failed to read the DepEd Target sheet.")
                        else:
                            df_raw.columns = [str(c).strip() for c in df_raw.columns]
                            if 'Province' in df_raw.columns:
                                df_raw = df_raw[df_raw['Province'].astype(str).str.upper() == 'ABRA'].copy()
                                
                            df_raw['Municipality'] = df_raw['Municipality'].astype(str).str.strip().str.title()
                            df_raw['School_name'] = df_raw['School_name'].astype(str).str.strip()
                            df_raw['beis_school_id'] = df_raw['beis_school_id'].astype(str).str.replace(r'\.0$', '', regex=True)
                            
                            target_cols = {
                                'Municipality': 'municipality', 'Barangay': 'barangay',
                                'beis_school_id': 'school_id', 'School_name': 'school_name',
                                'g1male': 'g1_male', 'g1female': 'g1_female',
                                'g4female': 'g4_female', 'g7male': 'g7_male', 'g7female': 'g7_female'
                            }
                            df_push = df_raw[[c for c in target_cols.keys() if c in df_raw.columns]].rename(columns=target_cols)
                            
                            for c in ['g1_male', 'g1_female', 'g4_female', 'g7_male', 'g7_female']:
                                if c in df_push.columns:
                                    df_push[c] = pd.to_numeric(df_push[c], errors='coerce').fillna(0).astype(int)
                                    
                            df_push['g1_total'] = df_push.get('g1_male', 0) + df_push.get('g1_female', 0)
                            df_push['g7_total'] = df_push.get('g7_male', 0) + df_push.get('g7_female', 0)

                            # DepEd exports can legitimately repeat a BEIS School ID.
                            # Consolidate repeated rows before validating uniqueness so
                            # duplicate exports do not inflate school-level targets.
                            df_push, dedupe_report = consolidate_sbi_targets(df_push)

                            valid, validation_message = validate_sbi_targets(df_push)
                            if not valid:
                                raise ValueError(f"SBI target validation failed: {validation_message}")

                            df_push = df_push.replace({np.nan: None})

                            if dedupe_report['duplicate_ids_consolidated'] or dedupe_report['exact_duplicates_removed']:
                                st.info(
                                    "DepEd duplicate cleanup: "
                                    f"{dedupe_report['input_rows']:,} source rows → "
                                    f"{dedupe_report['output_rows']:,} unique schools; "
                                    f"{dedupe_report['exact_duplicates_removed']:,} exact duplicate row(s) removed; "
                                    f"{dedupe_report['duplicate_ids_consolidated']:,} repeated School ID(s) consolidated."
                                )
                                if dedupe_report['name_variant_ids'] or dedupe_report['barangay_variant_ids']:
                                    st.caption(
                                        f"Name variants: {dedupe_report['name_variant_ids']:,} School ID(s); "
                                        f"barangay variants: {dedupe_report['barangay_variant_ids']:,} School ID(s). "
                                        "The most common text value was retained."
                                    )

                            records = df_push.to_dict(orient='records')
                            inserted = replace_table_with_rollback(supabase, 'sbi_targets', records)

                            st.success(f"SBI Targets successfully synced to Supabase ({inserted:,} records).")
                            st.cache_data.clear()
                    except Exception as e:
                        st.error(f"SBI Target Sync Failed: {e}")

