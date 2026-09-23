"""School-Based Immunization (SBI) dashboard renderer.

This module owns SBI-specific UI, filtering, analytics, target review,
and administrative target synchronization. The application shell and
authentication remain in the root ``app.py`` entry point.
"""

from datetime import date, datetime
import time

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pytz
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from core.config import ABRA_MUNIS
from core.data import fetch_sbi_actual_targets, fetch_sbi_targets, fetch_sbi_vacctrack
from programs.sbi.analytics import (
    available_date_bounds,
    build_effective_targets,
    filter_dates,
    filter_location,
    prepare_hpv_events,
    prepare_mr_td_events,
    reason_summary,
)
from programs.sbi.reporting import (
    render_campaign_burnup,
    render_daily_trend,
    render_municipality_choropleth,
    render_raw_export,
    render_tally_tabs,
)
from db_utils import update_session_log_throttled


def _get_last_updated_time() -> str:
    tz = pytz.timezone("Asia/Manila")
    return datetime.now(tz).strftime("%B %d, %Y | %I:%M %p")


def _safe_pct(numerator, denominator, default=0.0):
    """Return percentage values without dividing by zero.

    Works with pandas Series and coerces non-numeric values to NaN before
    calculation. Zero denominators are masked before division.
    """
    num = pd.to_numeric(numerator, errors="coerce")
    den = pd.to_numeric(denominator, errors="coerce")
    result = num.div(den.mask(den.eq(0))).mul(100)

    if pd.isna(default):
        return result
    return result.fillna(default)


def _logout_session() -> None:
    st.session_state.clear()
    st.rerun()


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

    # Normalize the three VaccTrack sheet variants once, before rendering tabs.
    # The source exports include CAR-wide rows and multiple historical form
    # versions; analytics.py filters to Abra and combines old/new schemas.
    g1_events = prepare_mr_td_events(df_g1, "G1")
    g7_events = prepare_mr_td_events(df_g7, "G7")
    hpv_events = prepare_hpv_events(df_g4)
    effective_targets = build_effective_targets(df_sbi_targets, df_sbi_actual_targets)
    available_min_date, available_max_date = available_date_bounds(
        g1_events, g7_events, hpv_events
    )

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
        
        if st.button("Main Menu", width="stretch", key="sbi_main_menu"):
            st.session_state['active_program'] = None
            st.rerun()

        if st.button("Logout", width="stretch", key="sbi_logout"):
            _logout_session()
            
        with st.expander("Dashboard Filters", expanded=True):
            view_mode = st.radio(
                "Geographic Level:",
                ["All Municipalities (Abra)", "Specific Municipality"],
                key="sbi_geo_mode"
            )
            if view_mode == "Specific Municipality":
                selected_muni = st.selectbox(
                    "Select Municipality:", ABRA_MUNIS, key="sbi_muni_sel"
                )
            else:
                selected_muni = "None"

            period_mode = st.selectbox(
                "Reporting Period:",
                ["2026 Campaign", "All Available Dates", "Custom Date Range"],
                index=0,
                key="sbi_reporting_period"
            )

            if period_mode == "2026 Campaign":
                report_start = date(2026, 1, 1)
                report_end = date(2026, 12, 31)
            elif period_mode == "All Available Dates":
                report_start = available_min_date
                report_end = available_max_date
            else:
                default_start = available_min_date or date(2026, 1, 1)
                default_end = available_max_date or date(2026, 12, 31)
                custom_range = st.date_input(
                    "Date Range:",
                    value=(default_start, default_end),
                    key="sbi_custom_date_range"
                )
                if isinstance(custom_range, (tuple, list)) and len(custom_range) == 2:
                    report_start, report_end = custom_range
                else:
                    report_start, report_end = default_start, default_end
                
        with st.expander("System Actions", expanded=False):
            if st.button("Refresh Data", width="stretch", key="sbi_refresh"):
                st.cache_data.clear()
                st.toast("SBI Database Refreshed!")
                time.sleep(0.5)
                st.rerun()
                
        st.markdown(
            f'<span style="color:#64748b;font-size:0.85rem;"><i class="fa-solid fa-clock" style="margin-right:6px;"></i>Last Sync: {last_updated}</span>',
            unsafe_allow_html=True
        )

    # Apply the global SBI filters once so every accomplishment tab uses the
    # same geographic scope and reporting period.
    selected_muni_filter = None if view_mode == "All Municipalities (Abra)" else selected_muni
    g1_view = filter_dates(filter_location(g1_events, selected_muni_filter), report_start, report_end)
    g7_view = filter_dates(filter_location(g7_events, selected_muni_filter), report_start, report_end)
    hpv_view = filter_dates(filter_location(hpv_events, selected_muni_filter), report_start, report_end)
    target_view = filter_location(effective_targets, selected_muni_filter)

    location_label = "Abra Province" if view_mode == "All Municipalities (Abra)" else f"{selected_muni}, Abra"
    if report_start and report_end:
        period_label = f"{report_start.strftime('%b %d, %Y')} to {report_end.strftime('%b %d, %Y')}"
    else:
        period_label = "All available dates"

    def _build_mr_td_school_summary(events, targets, target_col):
        school = events.groupby(
            ['Municipality', 'Barangay', 'School ID', 'School Name'],
            dropna=False,
        )[['MR Doses', 'Td Doses']].sum().reset_index()

        school['School ID'] = school['School ID'].astype(str).str.strip()
        if not targets.empty:
            target_school = targets[['School ID', target_col]].copy().rename(columns={target_col: 'Target'})
            target_school['School ID'] = target_school['School ID'].astype(str).str.strip()
            target_school['Target'] = pd.to_numeric(target_school['Target'], errors='coerce').fillna(0)
            target_school = target_school.groupby('School ID', as_index=False)['Target'].sum()
            school = school.merge(target_school, on='School ID', how='left')
        else:
            school['Target'] = 0

        school['Target'] = pd.to_numeric(school['Target'], errors='coerce').fillna(0)
        school['MR Coverage %'] = _safe_pct(school['MR Doses'], school['Target'], default=np.nan)
        school['Td Coverage %'] = _safe_pct(school['Td Doses'], school['Target'], default=np.nan)
        school['MR Remaining to 95%'] = np.maximum(np.ceil(school['Target'] * 0.95 - school['MR Doses']), 0)
        school['Td Remaining to 95%'] = np.maximum(np.ceil(school['Target'] * 0.95 - school['Td Doses']), 0)
        school['School Label'] = np.where(
            view_mode == "All Municipalities (Abra)",
            school['School Name'].astype(str) + ' - ' + school['Municipality'].astype(str),
            school['School Name'].astype(str),
        )
        return school

    def _render_mr_td_school_coverage(school, key_prefix, heading='Coverage by School'):
        st.markdown(
            f'''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-school" style="color:#0033A0; margin-right:8px;"></i>
            {heading}
            </h4>''',
            unsafe_allow_html=True,
        )

        chart_scope = st.selectbox(
            'Schools shown in chart:',
            ['Top 25 by Target', 'Top 50 by Target', 'All Schools'],
            key=f'{key_prefix}_school_scope',
        )
        school_plot = school.sort_values('Target', ascending=False).copy()
        if chart_scope.startswith('Top 25'):
            school_plot = school_plot.head(25)
        elif chart_scope.startswith('Top 50'):
            school_plot = school_plot.head(50)
        school_plot = school_plot.sort_values('Target', ascending=True)

        school_long = school_plot.melt(
            id_vars=['School Label'],
            value_vars=['MR Coverage %', 'Td Coverage %'],
            var_name='Vaccine',
            value_name='Coverage %',
        ).dropna(subset=['Coverage %'])

        if not school_long.empty:
            fig_school = px.bar(
                school_long,
                x='Coverage %',
                y='School Label',
                color='Vaccine',
                orientation='h',
                barmode='group',
                text_auto='.1f',
                color_discrete_sequence=['#1E88E5', '#43A047'],
            )
            fig_school.add_vline(x=95, line_dash='dash', line_color='red', annotation_text='95%')
            fig_school.update_layout(
                dragmode=False,
                plot_bgcolor='rgba(0,0,0,0)',
                xaxis_title='Coverage (%)',
                yaxis_title='',
                height=max(520, len(school_plot) * 40),
                margin=dict(l=10, r=45, t=25, b=60),
                legend=dict(orientation='h', yanchor='top', y=-0.08, xanchor='center', x=0.5),
                legend_title_text='',
            )
            st.plotly_chart(fig_school, width='stretch', key=f'{key_prefix}_school_chart')

        school_export = school.drop(columns=['School Label'], errors='ignore').sort_values(['Municipality', 'School Name'])
        st.dataframe(
            school_export,
            width='stretch',
            hide_index=True,
            column_config={
                'Target': st.column_config.NumberColumn('Target', format='%d'),
                'MR Doses': st.column_config.NumberColumn('MR Vaccinated', format='%d'),
                'Td Doses': st.column_config.NumberColumn('Td Vaccinated', format='%d'),
                'MR Coverage %': st.column_config.NumberColumn('MR Coverage', format='%.1f%%'),
                'Td Coverage %': st.column_config.NumberColumn('Td Coverage', format='%.1f%%'),
                'MR Remaining to 95%': st.column_config.NumberColumn('MR to 95%', format='%d'),
                'Td Remaining to 95%': st.column_config.NumberColumn('Td to 95%', format='%d'),
            },
        )
        st.download_button(
            label='Download School Performance (CSV)',
            data=school_export.to_csv(index=False).encode('utf-8-sig'),
            file_name=f'{key_prefix}_School_Performance_{location_label.replace(", ", "_")}.csv',
            mime='text/csv',
            key=f'{key_prefix}_school_download',
        )

    def _build_hpv_school_summary(events, targets):
        school = events.groupby(
            ['Municipality', 'Barangay', 'School ID', 'School Name'],
            dropna=False,
        )[['HPV Dose 1', 'HPV Dose 2']].sum().reset_index()
        school['School ID'] = school['School ID'].astype(str).str.strip()

        if not targets.empty:
            target_school = targets[['School ID', 'G4 Target']].copy().rename(columns={'G4 Target': 'Target'})
            target_school['School ID'] = target_school['School ID'].astype(str).str.strip()
            target_school['Target'] = pd.to_numeric(target_school['Target'], errors='coerce').fillna(0)
            target_school = target_school.groupby('School ID', as_index=False)['Target'].sum()
            school = school.merge(target_school, on='School ID', how='left')
        else:
            school['Target'] = 0

        school['Target'] = pd.to_numeric(school['Target'], errors='coerce').fillna(0)
        school['1st Dose Coverage %'] = _safe_pct(school['HPV Dose 1'], school['Target'], default=np.nan)
        school['2nd Dose Coverage %'] = _safe_pct(school['HPV Dose 2'], school['Target'], default=np.nan)
        school['1st Dose Remaining to 90%'] = np.maximum(np.ceil(school['Target'] * 0.90 - school['HPV Dose 1']), 0)
        school['2nd Dose Remaining to 90%'] = np.maximum(np.ceil(school['Target'] * 0.90 - school['HPV Dose 2']), 0)
        school['School Label'] = np.where(
            view_mode == "All Municipalities (Abra)",
            school['School Name'].astype(str) + ' - ' + school['Municipality'].astype(str),
            school['School Name'].astype(str),
        )
        return school

    def _render_hpv_school_coverage(school, heading='Coverage by School'):
        st.markdown(
            f'''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-school" style="color:#0033A0; margin-right:8px;"></i>
            {heading}
            </h4>''',
            unsafe_allow_html=True,
        )
        hpv_scope = st.selectbox(
            'Schools shown in chart:',
            ['Top 25 by Target', 'Top 50 by Target', 'All Schools'],
            key='sbi_hpv_school_scope',
        )
        hpv_school_plot = school.sort_values('Target', ascending=False).copy()
        if hpv_scope.startswith('Top 25'):
            hpv_school_plot = hpv_school_plot.head(25)
        elif hpv_scope.startswith('Top 50'):
            hpv_school_plot = hpv_school_plot.head(50)
        hpv_school_plot = hpv_school_plot.sort_values('Target', ascending=True)

        hpv_school_long = hpv_school_plot.melt(
            id_vars=['School Label'],
            value_vars=['1st Dose Coverage %', '2nd Dose Coverage %'],
            var_name='Dose',
            value_name='Coverage %',
        ).dropna(subset=['Coverage %'])

        if not hpv_school_long.empty:
            fig_hpv_school = px.bar(
                hpv_school_long,
                x='Coverage %',
                y='School Label',
                color='Dose',
                orientation='h',
                barmode='group',
                text_auto='.1f',
                color_discrete_sequence=['#D81B60', '#8E24AA'],
            )
            fig_hpv_school.add_vline(x=90, line_dash='dash', line_color='red', annotation_text='90%')
            fig_hpv_school.update_layout(
                dragmode=False,
                plot_bgcolor='rgba(0,0,0,0)',
                xaxis_title='Coverage (%)',
                yaxis_title='',
                height=max(520, len(hpv_school_plot) * 40),
                margin=dict(l=10, r=45, t=25, b=60),
                legend=dict(orientation='h', yanchor='top', y=-0.08, xanchor='center', x=0.5),
                legend_title_text='',
            )
            st.plotly_chart(fig_hpv_school, width='stretch', key='sbi_hpv_school_chart')

        hpv_export = school.drop(columns=['School Label'], errors='ignore').sort_values(['Municipality', 'School Name'])
        st.dataframe(
            hpv_export,
            width='stretch',
            hide_index=True,
            column_config={
                'Target': st.column_config.NumberColumn('Target', format='%d'),
                'HPV Dose 1': st.column_config.NumberColumn('1st Dose', format='%d'),
                'HPV Dose 2': st.column_config.NumberColumn('2nd Dose', format='%d'),
                '1st Dose Coverage %': st.column_config.NumberColumn('1st Dose Coverage', format='%.1f%%'),
                '2nd Dose Coverage %': st.column_config.NumberColumn('2nd Dose Coverage', format='%.1f%%'),
                '1st Dose Remaining to 90%': st.column_config.NumberColumn('1st Dose to 90%', format='%d'),
                '2nd Dose Remaining to 90%': st.column_config.NumberColumn('2nd Dose to 90%', format='%d'),
            },
        )
        st.download_button(
            label='Download HPV School Performance (CSV)',
            data=hpv_export.to_csv(index=False).encode('utf-8-sig'),
            file_name=f'SBI_HPV_School_Performance_{location_label.replace(", ", "_")}.csv',
            mime='text/csv',
            key='sbi_hpv_school_download',
        )

    def _render_mr_td_panel(events, targets, target_col, panel_label, key_prefix):
        st.markdown(
            f'''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-syringe" style="color:#0033A0; margin-right:8px;"></i>
            {panel_label}
            </h4>''',
            unsafe_allow_html=True,
        )

        if events.empty:
            st.info("No VaccTrack records are available for this selection and reporting period.")
            return

        target_total = pd.to_numeric(targets.get(target_col, 0), errors='coerce').fillna(0).sum() if not targets.empty else 0
        mr_doses = pd.to_numeric(events['MR Doses'], errors='coerce').fillna(0).sum()
        td_doses = pd.to_numeric(events['Td Doses'], errors='coerce').fillna(0).sum()
        mr_cov = (mr_doses / target_total * 100) if target_total > 0 else 0
        td_cov = (td_doses / target_total * 100) if target_total > 0 else 0
        schools_reporting = events.loc[events['School ID'].astype(str).str.strip().ne(''), 'School ID'].nunique()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Target", f"{target_total:,.0f}", f"{len(targets):,} target school rows" if not targets.empty else "No target data")
        m2.metric("MR Vaccinated", f"{mr_doses:,.0f}", f"{mr_cov:.1f}% coverage", delta_color="off")
        m3.metric("Td Vaccinated", f"{td_doses:,.0f}", f"{td_cov:.1f}% coverage", delta_color="off")
        m4.metric("Schools Reporting", f"{schools_reporting:,}", f"{len(events):,} report rows", delta_color="off")

        school = _build_mr_td_school_summary(events, targets, target_col)

        st.divider()

        if view_mode == "All Municipalities (Abra)":
            geo_col = 'Municipality'
            event_geo = events.groupby(geo_col, dropna=False)[['MR Doses', 'Td Doses']].sum().reset_index()
            if not targets.empty:
                target_geo = targets.groupby(geo_col, dropna=False)[target_col].sum().reset_index().rename(columns={target_col: 'Target'})
                geo = target_geo.merge(event_geo, on=geo_col, how='outer').fillna(0)
            else:
                geo = event_geo.copy()
                geo['Target'] = 0

            geo['MR Coverage %'] = _safe_pct(geo['MR Doses'], geo['Target'])
            geo['Td Coverage %'] = _safe_pct(geo['Td Doses'], geo['Target'])
            geo['MR Remaining to 95%'] = np.maximum(np.ceil(geo['Target'] * 0.95 - geo['MR Doses']), 0)
            geo['Td Remaining to 95%'] = np.maximum(np.ceil(geo['Target'] * 0.95 - geo['Td Doses']), 0)

            st.markdown(
                '''<h4 style="margin-bottom:0.25rem;">
                <i class="fa-solid fa-chart-bar" style="color:#0033A0; margin-right:8px;"></i>
                Coverage by Municipality
                </h4>''',
                unsafe_allow_html=True,
            )
            geo_chart = geo.sort_values('MR Coverage %', ascending=True).melt(
                id_vars=[geo_col],
                value_vars=['MR Coverage %', 'Td Coverage %'],
                var_name='Vaccine',
                value_name='Coverage %',
            )
            fig_geo = px.bar(
                geo_chart,
                x='Coverage %',
                y=geo_col,
                color='Vaccine',
                orientation='h',
                barmode='group',
                text_auto='.1f',
                color_discrete_sequence=['#1E88E5', '#43A047'],
            )
            fig_geo.add_vline(x=95, line_dash='dash', line_color='red', annotation_text='95%')
            fig_geo.update_layout(
                dragmode=False,
                plot_bgcolor='rgba(0,0,0,0)',
                xaxis_title='Coverage (%)',
                yaxis_title='',
                height=max(420, len(geo) * 46),
                margin=dict(l=10, r=45, t=35, b=60),
                legend=dict(orientation='h', yanchor='top', y=-0.10, xanchor='center', x=0.5),
                legend_title_text='',
            )
            fig_geo.update_traces(textposition='outside', cliponaxis=False)
            st.plotly_chart(fig_geo, width='stretch', key=f'{key_prefix}_geo_cov')

            st.dataframe(
                geo.sort_values('MR Coverage %', ascending=False),
                width='stretch',
                hide_index=True,
                column_config={
                    'Target': st.column_config.NumberColumn('Target', format='%d'),
                    'MR Doses': st.column_config.NumberColumn('MR Vaccinated', format='%d'),
                    'Td Doses': st.column_config.NumberColumn('Td Vaccinated', format='%d'),
                    'MR Coverage %': st.column_config.NumberColumn('MR Coverage', format='%.1f%%'),
                    'Td Coverage %': st.column_config.NumberColumn('Td Coverage', format='%.1f%%'),
                    'MR Remaining to 95%': st.column_config.NumberColumn('MR to 95%', format='%d'),
                    'Td Remaining to 95%': st.column_config.NumberColumn('Td to 95%', format='%d'),
                },
            )

            map_choice = st.selectbox(
                'Municipality coverage map:',
                ['MR Coverage', 'Td Coverage'],
                key=f'{key_prefix}_map_choice',
            )
            map_geo = geo.rename(columns={geo_col: 'Municipality'}).copy()
            if map_choice == 'MR Coverage':
                render_municipality_choropleth(
                    map_geo,
                    'MR Coverage %',
                    f'{panel_label} - MR Coverage by Municipality',
                    f'{key_prefix}_mr_map',
                    target_col='Target',
                    vaccinated_col='MR Doses',
                    remaining_col='MR Remaining to 95%',
                )
            else:
                render_municipality_choropleth(
                    map_geo,
                    'Td Coverage %',
                    f'{panel_label} - Td Coverage by Municipality',
                    f'{key_prefix}_td_map',
                    target_col='Target',
                    vaccinated_col='Td Doses',
                    remaining_col='Td Remaining to 95%',
                )
        else:
            _render_mr_td_school_coverage(school, key_prefix, heading='Coverage by School')

        st.divider()
        render_daily_trend(
            events,
            [('MR Doses', 'MR'), ('Td Doses', 'Td')],
            title='Daily Vaccination Activity by Report Date',
            key=f'{key_prefix}_daily_trend',
            colors=['#1E88E5', '#43A047'],
        )

        st.divider()
        st.markdown(
            '''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-chart-line" style="color:#0033A0; margin-right:8px;"></i>
            Cumulative Vaccinations Over Time
            </h4>''',
            unsafe_allow_html=True,
        )
        trend = events.dropna(subset=['Report Date']).groupby('Report Date')[['MR Doses', 'Td Doses']].sum().reset_index().sort_values('Report Date')
        if not trend.empty:
            trend['Cumulative MR'] = trend['MR Doses'].cumsum()
            trend['Cumulative Td'] = trend['Td Doses'].cumsum()
            trend_long = trend.melt(
                id_vars=['Report Date'],
                value_vars=['Cumulative MR', 'Cumulative Td'],
                var_name='Vaccine',
                value_name='Vaccinated',
            )
            fig_trend = px.line(
                trend_long,
                x='Report Date',
                y='Vaccinated',
                color='Vaccine',
                markers=True,
                color_discrete_sequence=['#1E88E5', '#43A047'],
            )
            if target_total > 0:
                fig_trend.add_hline(
                    y=target_total * 0.95,
                    line_dash='dash',
                    line_color='rgba(0,51,160,0.60)',
                    annotation_text='95% target',
                    annotation_position='top left',
                )
            fig_trend.update_layout(
                dragmode=False,
                plot_bgcolor='rgba(0,0,0,0)',
                xaxis_title='',
                yaxis_title='Cumulative vaccinated students',
                height=420,
                margin=dict(l=10, r=20, t=25, b=55),
                legend=dict(orientation='h', yanchor='top', y=-0.15, xanchor='center', x=0.5),
                legend_title_text='',
            )
            st.plotly_chart(fig_trend, width='stretch', key=f'{key_prefix}_trend')
        else:
            st.info("No valid report dates are available for the selected period.")

        st.divider()
        render_tally_tabs(
            events,
            [('MR Doses', 'MR'), ('Td Doses', 'Td')],
            geo_col='Municipality' if view_mode == "All Municipalities (Abra)" else 'School Name',
            key_prefix=f'{key_prefix}_tally',
            location_label=location_label,
        )

        if view_mode == "All Municipalities (Abra)":
            st.divider()
            _render_mr_td_school_coverage(school, key_prefix, heading='School-Level Performance')

        render_raw_export(
            events,
            title='View and download normalized VaccTrack rows',
            filename=f'{key_prefix}_VaccTrack_{location_label.replace(", ", "_").replace(" ", "_")}.csv',
            key=f'{key_prefix}_raw_download',
        )

    # --- DASHBOARD TABS ---
    sbi_tabs = st.tabs(["Executive Summary", "Targets Overview", "MR & Td (Grades 1 & 7)", "HPV (Grade 4)", "Deferrals & Refusals"])
    tab_sbi_exec, tab_sbi_target, tab_sbi_mr, tab_sbi_hpv, tab_sbi_def = sbi_tabs

    # 1. EXECUTIVE SUMMARY
    with tab_sbi_exec:
        st.markdown(f"### SBI Campaign Overview: {location_label}")

        if target_view.empty:
            st.warning("Target data is unavailable. Sync the target database first.")
        else:
            tgt_g1 = pd.to_numeric(target_view['G1 Target'], errors='coerce').fillna(0).sum()
            tgt_g7 = pd.to_numeric(target_view['G7 Target'], errors='coerce').fillna(0).sum()
            tgt_mr_td = tgt_g1 + tgt_g7
            tgt_hpv = pd.to_numeric(target_view['G4 Target'], errors='coerce').fillna(0).sum()

            g1_mr_doses = pd.to_numeric(g1_view.get('MR Doses', 0), errors='coerce').fillna(0).sum() if not g1_view.empty else 0
            g1_td_doses = pd.to_numeric(g1_view.get('Td Doses', 0), errors='coerce').fillna(0).sum() if not g1_view.empty else 0
            g7_mr_doses = pd.to_numeric(g7_view.get('MR Doses', 0), errors='coerce').fillna(0).sum() if not g7_view.empty else 0
            g7_td_doses = pd.to_numeric(g7_view.get('Td Doses', 0), errors='coerce').fillna(0).sum() if not g7_view.empty else 0
            hpv_1st = pd.to_numeric(hpv_view.get('HPV Dose 1', 0), errors='coerce').fillna(0).sum() if not hpv_view.empty else 0

            total_mr = g1_mr_doses + g7_mr_doses
            total_td = g1_td_doses + g7_td_doses
            mr_cov = (total_mr / tgt_mr_td * 100) if tgt_mr_td > 0 else 0
            td_cov = (total_td / tgt_mr_td * 100) if tgt_mr_td > 0 else 0
            hpv_cov = (hpv_1st / tgt_hpv * 100) if tgt_hpv > 0 else 0

            k1, k2, k3 = st.columns(3)
            k1.metric(
                "Measles-Rubella (MR) Coverage",
                f"{mr_cov:.1f}%",
                f"{total_mr:,.0f} / {tgt_mr_td:,.0f} target",
                delta_color="off"
            )
            k2.metric(
                "Tetanus-diphtheria (Td) Coverage",
                f"{td_cov:.1f}%",
                f"{total_td:,.0f} / {tgt_mr_td:,.0f} target",
                delta_color="off"
            )
            k3.metric(
                "HPV Coverage (1st Dose)",
                f"{hpv_cov:.1f}%",
                f"{hpv_1st:,.0f} / {tgt_hpv:,.0f} target",
                delta_color="off"
            )

            st.divider()

            c1, c2, c3 = st.columns(3)
            with c1:
                fig_gauge_mr = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=mr_cov,
                    title={'text': "MR (Grades 1 & 7)"},
                    gauge={
                        'axis': {'range': [None, 100]},
                        'bar': {'color': "#1E88E5"},
                        'bgcolor': "rgba(128,128,128,0.2)",
                        'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 95}
                    }
                ))
                fig_gauge_mr.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_gauge_mr, width="stretch", key="sbi_exec_gauge_mr")

            with c2:
                fig_gauge_td = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=td_cov,
                    title={'text': "Td (Grades 1 & 7)"},
                    gauge={
                        'axis': {'range': [None, 100]},
                        'bar': {'color': "#43A047"},
                        'bgcolor': "rgba(128,128,128,0.2)",
                        'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 95}
                    }
                ))
                fig_gauge_td.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_gauge_td, width="stretch", key="sbi_exec_gauge_td")

            with c3:
                fig_gauge_hpv = go.Figure(go.Indicator(
                    mode="gauge+number",
                    value=hpv_cov,
                    title={'text': "HPV 1st Dose (Grade 4 Female)"},
                    gauge={
                        'axis': {'range': [None, 100]},
                        'bar': {'color': "#D81B60"},
                        'bgcolor': "rgba(128,128,128,0.2)",
                        'threshold': {'line': {'color': "red", 'width': 4}, 'thickness': 0.75, 'value': 90}
                    }
                ))
                fig_gauge_hpv.update_layout(height=250, margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_gauge_hpv, width="stretch", key="sbi_exec_gauge_hpv")

            st.divider()

            st.markdown(
                '''<h4 style="margin-bottom:0.25rem;">
                <i class="fa-solid fa-arrow-trend-up" style="color:#0033A0; margin-right:8px;"></i>
                Cumulative Campaign Burn-Up
                </h4>''',
                unsafe_allow_html=True
            )
            exec_daily = render_campaign_burnup(
                g1_view, g7_view, hpv_view,
                mr_td_target=tgt_mr_td,
                hpv_target=tgt_hpv,
                key='sbi_exec_campaign_burnup',
            )
            if not exec_daily.empty:
                with st.expander('View and download daily campaign summary', expanded=False):
                    st.dataframe(exec_daily, width='stretch', hide_index=True)
                    st.download_button(
                        label='Download Daily Campaign Summary (CSV)',
                        data=exec_daily.to_csv(index=False).encode('utf-8-sig'),
                        file_name=f'SBI_Daily_Campaign_Summary_{location_label.replace(", ", "_").replace(" ", "_")}.csv',
                        mime='text/csv',
                        key='sbi_exec_daily_summary_download'
                    )

            st.divider()
            exec_targets = target_view.copy()
            exec_targets['MR/Td Target'] = (
                pd.to_numeric(exec_targets.get('G1 Target', 0), errors='coerce').fillna(0)
                + pd.to_numeric(exec_targets.get('G7 Target', 0), errors='coerce').fillna(0)
            )
            exec_targets['HPV Target'] = pd.to_numeric(exec_targets.get('G4 Target', 0), errors='coerce').fillna(0)
            mrtd_exec = pd.concat([g1_view, g7_view], ignore_index=True, sort=False)

            if view_mode == "All Municipalities (Abra)":
                geo_exec_col = 'Municipality'
                exec_target_geo = exec_targets.groupby(geo_exec_col, dropna=False)[['MR/Td Target', 'HPV Target']].sum().reset_index()
                mrtd_geo = (
                    mrtd_exec.groupby(geo_exec_col, dropna=False)[['MR Doses', 'Td Doses']].sum().reset_index()
                    if not mrtd_exec.empty
                    else pd.DataFrame(columns=[geo_exec_col, 'MR Doses', 'Td Doses'])
                )
                hpv_geo_exec = (
                    hpv_view.groupby(geo_exec_col, dropna=False)[['HPV Dose 1']].sum().reset_index()
                    if not hpv_view.empty
                    else pd.DataFrame(columns=[geo_exec_col, 'HPV Dose 1'])
                )
                exec_geo = exec_target_geo.merge(mrtd_geo, on=geo_exec_col, how='outer').merge(
                    hpv_geo_exec, on=geo_exec_col, how='outer'
                ).fillna(0)
                exec_geo['MR Coverage %'] = _safe_pct(exec_geo['MR Doses'], exec_geo['MR/Td Target'])
                exec_geo['Td Coverage %'] = _safe_pct(exec_geo['Td Doses'], exec_geo['MR/Td Target'])
                exec_geo['HPV 1st Dose Coverage %'] = _safe_pct(exec_geo['HPV Dose 1'], exec_geo['HPV Target'])
                exec_geo['MR Remaining to 95%'] = np.maximum(np.ceil(exec_geo['MR/Td Target'] * 0.95 - exec_geo['MR Doses']), 0)
                exec_geo['Td Remaining to 95%'] = np.maximum(np.ceil(exec_geo['MR/Td Target'] * 0.95 - exec_geo['Td Doses']), 0)
                exec_geo['HPV Remaining to 90%'] = np.maximum(np.ceil(exec_geo['HPV Target'] * 0.90 - exec_geo['HPV Dose 1']), 0)

                st.markdown(
                    '''<h4 style="margin-bottom:0.25rem;">
                    <i class="fa-solid fa-chart-column" style="color:#0033A0; margin-right:8px;"></i>
                    Coverage by Municipality
                    </h4>''',
                    unsafe_allow_html=True,
                )
                exec_geo_long = exec_geo.sort_values('MR Coverage %', ascending=True).melt(
                    id_vars=[geo_exec_col],
                    value_vars=['MR Coverage %', 'Td Coverage %', 'HPV 1st Dose Coverage %'],
                    var_name='Program',
                    value_name='Coverage %',
                )
                fig_exec_geo = px.bar(
                    exec_geo_long,
                    x='Coverage %',
                    y=geo_exec_col,
                    color='Program',
                    barmode='group',
                    orientation='h',
                    text_auto='.1f',
                    color_discrete_sequence=['#1E88E5', '#43A047', '#D81B60'],
                )
                fig_exec_geo.update_traces(textposition='outside', cliponaxis=False)
                fig_exec_geo.update_layout(
                    dragmode=False,
                    plot_bgcolor='rgba(0,0,0,0)',
                    xaxis_title='Coverage (%)',
                    yaxis_title='',
                    height=max(450, len(exec_geo) * 48),
                    margin=dict(l=10, r=55, t=25, b=65),
                    legend=dict(orientation='h', yanchor='top', y=-0.10, xanchor='center', x=0.5),
                    legend_title_text='',
                )
                st.plotly_chart(fig_exec_geo, width='stretch', key='sbi_exec_geo_coverage')

                with st.expander('View full municipality coverage table', expanded=False):
                    st.dataframe(exec_geo.sort_values('MR Coverage %', ascending=False), width='stretch', hide_index=True)
                    st.download_button(
                        label='Download Geographic Coverage (CSV)',
                        data=exec_geo.to_csv(index=False).encode('utf-8-sig'),
                        file_name=f'SBI_Coverage_by_Municipality_{location_label.replace(", ", "_").replace(" ", "_")}.csv',
                        mime='text/csv',
                        key='sbi_exec_geo_download',
                    )

                st.divider()
                exec_map_choice = st.selectbox(
                    'Provincial choropleth:',
                    ['MR Coverage', 'Td Coverage', 'HPV 1st Dose Coverage'],
                    key='sbi_exec_map_choice',
                )
                exec_map_df = exec_geo.rename(columns={geo_exec_col: 'Municipality'}).copy()
                if exec_map_choice == 'MR Coverage':
                    render_municipality_choropleth(
                        exec_map_df,
                        'MR Coverage %',
                        'MR Coverage by Municipality',
                        'sbi_exec_mr_map',
                        target_col='MR/Td Target',
                        vaccinated_col='MR Doses',
                        remaining_col='MR Remaining to 95%',
                    )
                elif exec_map_choice == 'Td Coverage':
                    render_municipality_choropleth(
                        exec_map_df,
                        'Td Coverage %',
                        'Td Coverage by Municipality',
                        'sbi_exec_td_map',
                        target_col='MR/Td Target',
                        vaccinated_col='Td Doses',
                        remaining_col='Td Remaining to 95%',
                    )
                else:
                    render_municipality_choropleth(
                        exec_map_df,
                        'HPV 1st Dose Coverage %',
                        'HPV 1st Dose Coverage by Municipality',
                        'sbi_exec_hpv_map',
                        target_col='HPV Target',
                        vaccinated_col='HPV Dose 1',
                        remaining_col='HPV Remaining to 90%',
                    )
            else:
                target_school = exec_targets[['School ID', 'School Name', 'MR/Td Target', 'HPV Target']].copy()
                target_school['School ID'] = target_school['School ID'].astype(str).str.strip()
                target_school = target_school.groupby('School ID', as_index=False).agg({
                    'School Name': 'first',
                    'MR/Td Target': 'sum',
                    'HPV Target': 'sum',
                })

                mrtd_school = (
                    mrtd_exec.groupby(['School ID', 'School Name'], dropna=False)[['MR Doses', 'Td Doses']].sum().reset_index()
                    if not mrtd_exec.empty
                    else pd.DataFrame(columns=['School ID', 'School Name', 'MR Doses', 'Td Doses'])
                )
                hpv_school = (
                    hpv_view.groupby(['School ID', 'School Name'], dropna=False)[['HPV Dose 1']].sum().reset_index()
                    if not hpv_view.empty
                    else pd.DataFrame(columns=['School ID', 'School Name', 'HPV Dose 1'])
                )
                for frame in [mrtd_school, hpv_school]:
                    frame['School ID'] = frame['School ID'].astype(str).str.strip()

                exec_school = target_school.merge(
                    mrtd_school.rename(columns={'School Name': 'MR School Name'}),
                    on='School ID',
                    how='outer',
                ).merge(
                    hpv_school.rename(columns={'School Name': 'HPV School Name'}),
                    on='School ID',
                    how='outer',
                )
                exec_school['School Name'] = (
                    exec_school['School Name']
                    .fillna(exec_school.get('MR School Name'))
                    .fillna(exec_school.get('HPV School Name'))
                    .fillna('Unknown School')
                )
                numeric_cols = ['MR/Td Target', 'HPV Target', 'MR Doses', 'Td Doses', 'HPV Dose 1']
                for col in numeric_cols:
                    exec_school[col] = pd.to_numeric(exec_school.get(col, 0), errors='coerce').fillna(0)
                exec_school['MR Coverage %'] = _safe_pct(exec_school['MR Doses'], exec_school['MR/Td Target'], default=np.nan)
                exec_school['Td Coverage %'] = _safe_pct(exec_school['Td Doses'], exec_school['MR/Td Target'], default=np.nan)
                exec_school['HPV 1st Dose Coverage %'] = _safe_pct(exec_school['HPV Dose 1'], exec_school['HPV Target'], default=np.nan)
                exec_school['MR Remaining to 95%'] = np.maximum(np.ceil(exec_school['MR/Td Target'] * 0.95 - exec_school['MR Doses']), 0)
                exec_school['Td Remaining to 95%'] = np.maximum(np.ceil(exec_school['MR/Td Target'] * 0.95 - exec_school['Td Doses']), 0)
                exec_school['HPV Remaining to 90%'] = np.maximum(np.ceil(exec_school['HPV Target'] * 0.90 - exec_school['HPV Dose 1']), 0)

                st.markdown(
                    '''<h4 style="margin-bottom:0.25rem;">
                    <i class="fa-solid fa-school" style="color:#0033A0; margin-right:8px;"></i>
                    Coverage by School
                    </h4>''',
                    unsafe_allow_html=True,
                )
                exec_school_long = exec_school.sort_values('MR/Td Target', ascending=True).melt(
                    id_vars=['School Name'],
                    value_vars=['MR Coverage %', 'Td Coverage %', 'HPV 1st Dose Coverage %'],
                    var_name='Program',
                    value_name='Coverage %',
                ).dropna(subset=['Coverage %'])
                if not exec_school_long.empty:
                    fig_exec_school = px.bar(
                        exec_school_long,
                        x='Coverage %',
                        y='School Name',
                        color='Program',
                        barmode='group',
                        orientation='h',
                        text_auto='.1f',
                        color_discrete_sequence=['#1E88E5', '#43A047', '#D81B60'],
                    )
                    fig_exec_school.update_traces(textposition='outside', cliponaxis=False)
                    fig_exec_school.update_layout(
                        dragmode=False,
                        plot_bgcolor='rgba(0,0,0,0)',
                        xaxis_title='Coverage (%)',
                        yaxis_title='',
                        height=max(500, len(exec_school) * 42),
                        margin=dict(l=10, r=55, t=25, b=65),
                        legend=dict(orientation='h', yanchor='top', y=-0.10, xanchor='center', x=0.5),
                        legend_title_text='',
                    )
                    st.plotly_chart(fig_exec_school, width='stretch', key='sbi_exec_school_coverage')

                school_export = exec_school.drop(columns=['MR School Name', 'HPV School Name'], errors='ignore').sort_values('School Name')
                with st.expander('View full school coverage table', expanded=False):
                    st.dataframe(school_export, width='stretch', hide_index=True)
                    st.download_button(
                        label='Download School Coverage (CSV)',
                        data=school_export.to_csv(index=False).encode('utf-8-sig'),
                        file_name=f'SBI_Coverage_by_School_{location_label.replace(", ", "_").replace(" ", "_")}.csv',
                        mime='text/csv',
                        key='sbi_exec_school_download',
                    )

            st.divider()

            report_rows = len(g1_view) + len(g7_view) + len(hpv_view)
            reporting_schools = len(set(g1_view.get('School ID', pd.Series(dtype=str)).astype(str)) |
                                    set(g7_view.get('School ID', pd.Series(dtype=str)).astype(str)) |
                                    set(hpv_view.get('School ID', pd.Series(dtype=str)).astype(str)))
            r1, r2, r3 = st.columns(3)
            r1.metric("VaccTrack Report Rows", f"{report_rows:,}")
            r2.metric("Schools with Reports", f"{reporting_schools:,}")
            r3.metric("Target Schools", f"{len(target_view):,}")

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
                st.warning("Target database is empty. Open Administration > Data Sync to sync SBI targets.")
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
                df_geo_table = df_geo_tgt.sort_values('Total Eligible', ascending=False).copy()
                st.dataframe(
                    df_geo_table,
                    width="stretch",
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
                st.plotly_chart(fig_tgt_geo, width="stretch", key="sbi_tgt_geo_bar")

                if view_mode == "All Municipalities (Abra)":
                    st.divider()
                    render_municipality_choropleth(
                        df_geo_tgt[["Municipality", "Total Eligible"]].copy(),
                        coverage_col="Total Eligible",
                        title="Total Baseline Targets by Municipality",
                        key="sbi_baseline_total_target_map",
                        color_scale=[
                            [0.00, "#B7D5EA"],
                            [0.20, "#7FB8D8"],
                            [0.45, "#3F8FC3"],
                            [0.70, "#1769A6"],
                            [1.00, "#08457E"],
                        ],
                        range_color=None,
                        value_format=",.0f",
                        value_suffix="",
                        hover_format=":,.0f",
                        colorbar_title="Total Target",
                        map_opacity=0.88,
                    )

                st.divider()

                st.markdown(
                    '''<h4 style="margin-bottom:0.5rem;">
                    <i class="fa-solid fa-school" style="color:#0033A0; margin-right:8px;"></i>
                    Targets by School
                    </h4>''',
                    unsafe_allow_html=True
                )

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
                    width="stretch",
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
                        width="stretch",
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
                df_actual_geo['Completion %'] = _safe_pct(
                    df_actual_geo['Complete'],
                    df_actual_geo['Schools']
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
                df_actual_geo_table = df_actual_geo.sort_values(
                    ['Completion %', 'Total Eligible'], ascending=[False, False]
                ).copy()
                st.dataframe(
                    df_actual_geo_table,
                    width="stretch",
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
                    width="stretch",
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
                    width="stretch",
                    key='sbi_actual_geo_chart'
                )

                if view_mode == "All Municipalities (Abra)":
                    st.divider()
                    render_municipality_choropleth(
                        df_actual_geo[["Municipality", "Total Eligible"]].copy(),
                        coverage_col="Total Eligible",
                        title="Total Actual Targets by Municipality",
                        key="sbi_actual_total_target_map",
                        color_scale=[
                            [0.00, "#B7D5EA"],
                            [0.20, "#7FB8D8"],
                            [0.45, "#3F8FC3"],
                            [0.70, "#1769A6"],
                            [1.00, "#08457E"],
                        ],
                        range_color=None,
                        value_format=",.0f",
                        value_suffix="",
                        hover_format=":,.0f",
                        colorbar_title="Total Target",
                        map_opacity=0.88,
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
                        width="stretch",
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
                        width="stretch",
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
                    df_target_compare['Change %'] = _safe_pct(
                        df_target_compare['Difference'],
                        df_target_compare['Baseline']
                    )

                    st.dataframe(
                        df_target_compare,
                        width="stretch",
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
                        width="stretch",
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
                    df_geo_compare['Change %'] = _safe_pct(
                        df_geo_compare['Difference'],
                        df_geo_compare['Baseline_Total']
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
                        width="stretch",
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
                        width="stretch",
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
                    df_school_compare['Change %'] = _safe_pct(
                        df_school_compare['Total Difference'],
                        df_school_compare['Baseline Total']
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
                        width="stretch",
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
                            width="stretch",
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
        mr_combined_tab, mr_g1_tab, mr_g7_tab = st.tabs([
            "Combined Grades 1 & 7",
            "Grade 1",
            "Grade 7",
        ])

        with mr_combined_tab:
            combined_events = pd.concat([g1_view, g7_view], ignore_index=True, sort=False)
            combined_targets = target_view.copy()
            if not combined_targets.empty:
                combined_targets['MR/Td Target'] = (
                    pd.to_numeric(combined_targets['G1 Target'], errors='coerce').fillna(0)
                    + pd.to_numeric(combined_targets['G7 Target'], errors='coerce').fillna(0)
                )
            _render_mr_td_panel(
                combined_events,
                combined_targets,
                'MR/Td Target',
                'Combined Grades 1 & 7 Performance',
                'sbi_mrtd_combined'
            )

        with mr_g1_tab:
            _render_mr_td_panel(
                g1_view,
                target_view,
                'G1 Target',
                'Grade 1 MR & Td Performance',
                'sbi_mrtd_g1'
            )

        with mr_g7_tab:
            _render_mr_td_panel(
                g7_view,
                target_view,
                'G7 Target',
                'Grade 7 MR & Td Performance',
                'sbi_mrtd_g7'
            )

    # 4. HPV (GRADE 4)
    with tab_sbi_hpv:
        st.markdown(f"### Human Papillomavirus (HPV) - Grade 4 Female Students: {location_label}")

        if hpv_view.empty:
            st.info("No Grade 4 HPV VaccTrack records are available for this selection and reporting period.")
        else:
            hpv_target = pd.to_numeric(target_view.get('G4 Target', 0), errors='coerce').fillna(0).sum() if not target_view.empty else 0
            dose1 = pd.to_numeric(hpv_view['HPV Dose 1'], errors='coerce').fillna(0).sum()
            dose2 = pd.to_numeric(hpv_view['HPV Dose 2'], errors='coerce').fillna(0).sum()
            cov1 = (dose1 / hpv_target * 100) if hpv_target > 0 else 0
            cov2 = (dose2 / hpv_target * 100) if hpv_target > 0 else 0
            schools_reporting = hpv_view.loc[hpv_view['School ID'].astype(str).str.strip().ne(''), 'School ID'].nunique()

            h1, h2, h3, h4 = st.columns(4)
            h1.metric("Grade 4 Female Target", f"{hpv_target:,.0f}")
            h2.metric("HPV 1st Dose", f"{dose1:,.0f}", f"{cov1:.1f}% coverage", delta_color="off")
            h3.metric("HPV 2nd Dose", f"{dose2:,.0f}", f"{cov2:.1f}% coverage", delta_color="off")
            h4.metric("Schools Reporting", f"{schools_reporting:,}", f"{len(hpv_view):,} report rows", delta_color="off")

            st.divider()

            hpv_school = _build_hpv_school_summary(hpv_view, target_view)

            if view_mode == "All Municipalities (Abra)":
                geo_col_hpv = 'Municipality'
                hpv_geo = hpv_view.groupby(geo_col_hpv, dropna=False)[['HPV Dose 1', 'HPV Dose 2']].sum().reset_index()
                if not target_view.empty:
                    hpv_target_geo = target_view.groupby(geo_col_hpv, dropna=False)['G4 Target'].sum().reset_index().rename(columns={'G4 Target': 'Target'})
                    hpv_geo = hpv_target_geo.merge(hpv_geo, on=geo_col_hpv, how='outer').fillna(0)
                else:
                    hpv_geo['Target'] = 0

                hpv_geo['1st Dose Coverage %'] = _safe_pct(hpv_geo['HPV Dose 1'], hpv_geo['Target'])
                hpv_geo['2nd Dose Coverage %'] = _safe_pct(hpv_geo['HPV Dose 2'], hpv_geo['Target'])
                hpv_geo['1st Dose Remaining to 90%'] = np.maximum(np.ceil(hpv_geo['Target'] * 0.90 - hpv_geo['HPV Dose 1']), 0)
                hpv_geo['2nd Dose Remaining to 90%'] = np.maximum(np.ceil(hpv_geo['Target'] * 0.90 - hpv_geo['HPV Dose 2']), 0)

                st.markdown(
                    '''<h4 style="margin-bottom:0.25rem;">
                    <i class="fa-solid fa-chart-bar" style="color:#0033A0; margin-right:8px;"></i>
                    HPV Coverage by Municipality
                    </h4>''',
                    unsafe_allow_html=True,
                )
                hpv_geo_long = hpv_geo.sort_values('1st Dose Coverage %', ascending=True).melt(
                    id_vars=[geo_col_hpv],
                    value_vars=['1st Dose Coverage %', '2nd Dose Coverage %'],
                    var_name='Dose',
                    value_name='Coverage %',
                )
                fig_hpv_geo = px.bar(
                    hpv_geo_long,
                    x='Coverage %',
                    y=geo_col_hpv,
                    color='Dose',
                    orientation='h',
                    barmode='group',
                    text_auto='.1f',
                    color_discrete_sequence=['#D81B60', '#8E24AA'],
                )
                fig_hpv_geo.add_vline(x=90, line_dash='dash', line_color='red', annotation_text='90%')
                fig_hpv_geo.update_layout(
                    dragmode=False,
                    plot_bgcolor='rgba(0,0,0,0)',
                    xaxis_title='Coverage (%)',
                    yaxis_title='',
                    height=max(420, len(hpv_geo) * 46),
                    margin=dict(l=10, r=45, t=35, b=60),
                    legend=dict(orientation='h', yanchor='top', y=-0.10, xanchor='center', x=0.5),
                    legend_title_text='',
                )
                fig_hpv_geo.update_traces(textposition='outside', cliponaxis=False)
                st.plotly_chart(fig_hpv_geo, width='stretch', key='sbi_hpv_geo')

                st.dataframe(
                    hpv_geo.sort_values('1st Dose Coverage %', ascending=False),
                    width='stretch',
                    hide_index=True,
                    column_config={
                        'Target': st.column_config.NumberColumn('Target', format='%d'),
                        'HPV Dose 1': st.column_config.NumberColumn('1st Dose', format='%d'),
                        'HPV Dose 2': st.column_config.NumberColumn('2nd Dose', format='%d'),
                        '1st Dose Coverage %': st.column_config.NumberColumn('1st Dose Coverage', format='%.1f%%'),
                        '2nd Dose Coverage %': st.column_config.NumberColumn('2nd Dose Coverage', format='%.1f%%'),
                        '1st Dose Remaining to 90%': st.column_config.NumberColumn('1st Dose to 90%', format='%d'),
                        '2nd Dose Remaining to 90%': st.column_config.NumberColumn('2nd Dose to 90%', format='%d'),
                    },
                )

                hpv_map_choice = st.selectbox(
                    'Municipality coverage map:',
                    ['HPV 1st Dose', 'HPV 2nd Dose'],
                    key='sbi_hpv_map_choice',
                )
                hpv_map_df = hpv_geo.rename(columns={geo_col_hpv: 'Municipality'}).copy()
                if hpv_map_choice == 'HPV 1st Dose':
                    render_municipality_choropleth(
                        hpv_map_df,
                        '1st Dose Coverage %',
                        'HPV 1st Dose Coverage by Municipality',
                        'sbi_hpv_dose1_map',
                        target_col='Target',
                        vaccinated_col='HPV Dose 1',
                        remaining_col='1st Dose Remaining to 90%',
                    )
                else:
                    render_municipality_choropleth(
                        hpv_map_df,
                        '2nd Dose Coverage %',
                        'HPV 2nd Dose Coverage by Municipality',
                        'sbi_hpv_dose2_map',
                        target_col='Target',
                        vaccinated_col='HPV Dose 2',
                        remaining_col='2nd Dose Remaining to 90%',
                    )
            else:
                _render_hpv_school_coverage(hpv_school, heading='Coverage by School')

            st.divider()
            render_daily_trend(
                hpv_view,
                [('HPV Dose 1', 'HPV 1st Dose'), ('HPV Dose 2', 'HPV 2nd Dose')],
                title='Daily HPV Vaccination Activity by Report Date',
                key='sbi_hpv_daily_trend', colors=['#D81B60', '#8E24AA'],
            )

            st.divider()

            st.markdown(
                '''<h4 style="margin-bottom:0.25rem;">
                <i class="fa-solid fa-chart-line" style="color:#0033A0; margin-right:8px;"></i>
                Cumulative HPV Doses Over Time
                </h4>''',
                unsafe_allow_html=True
            )
            hpv_trend = hpv_view.dropna(subset=['Report Date']).groupby('Report Date')[['HPV Dose 1', 'HPV Dose 2']].sum().reset_index().sort_values('Report Date')
            if not hpv_trend.empty:
                hpv_trend['Cumulative 1st Dose'] = hpv_trend['HPV Dose 1'].cumsum()
                hpv_trend['Cumulative 2nd Dose'] = hpv_trend['HPV Dose 2'].cumsum()
                hpv_trend_long = hpv_trend.melt(
                    id_vars=['Report Date'],
                    value_vars=['Cumulative 1st Dose', 'Cumulative 2nd Dose'],
                    var_name='Dose',
                    value_name='Vaccinated'
                )
                fig_hpv_trend = px.line(
                    hpv_trend_long,
                    x='Report Date',
                    y='Vaccinated',
                    color='Dose',
                    markers=True,
                    color_discrete_sequence=['#D81B60', '#8E24AA']
                )
                if hpv_target > 0:
                    fig_hpv_trend.add_hline(
                        y=hpv_target * 0.90, line_dash='dash', line_color='rgba(216,27,96,0.65)',
                        annotation_text='90% target', annotation_position='top left'
                    )
                fig_hpv_trend.update_layout(
                    dragmode=False,
                    plot_bgcolor='rgba(0,0,0,0)',
                    xaxis_title='',
                    yaxis_title='Cumulative vaccinated students',
                    height=420,
                    margin=dict(l=10, r=20, t=25, b=55),
                    legend=dict(orientation='h', yanchor='top', y=-0.15, xanchor='center', x=0.5),
                    legend_title_text=''
                )
                st.plotly_chart(fig_hpv_trend, width='stretch', key='sbi_hpv_trend')

            st.divider()
            render_tally_tabs(
                hpv_view,
                [('HPV Dose 1', 'HPV 1st Dose'), ('HPV Dose 2', 'HPV 2nd Dose')],
                geo_col='Municipality' if view_mode == "All Municipalities (Abra)" else 'School Name', key_prefix='sbi_hpv_tally', location_label=location_label,
            )

            if view_mode == "All Municipalities (Abra)":
                st.divider()
                _render_hpv_school_coverage(hpv_school, heading='School-Level HPV Performance')

            render_raw_export(
                hpv_view,
                title='View and download normalized Grade 4 VaccTrack rows',
                filename=f'SBI_HPV_VaccTrack_{location_label.replace(", ", "_").replace(" ", "_")}.csv',
                key='sbi_hpv_raw_download',
            )

    # 5. DEFERRALS & REFUSALS
    with tab_sbi_def:
        st.markdown(f"### Vaccine Deferrals & Refusals Analysis: {location_label}")

        total_mr_deferred = (
            (pd.to_numeric(g1_view.get('MR Deferred', 0), errors='coerce').fillna(0).sum() if not g1_view.empty else 0)
            + (pd.to_numeric(g7_view.get('MR Deferred', 0), errors='coerce').fillna(0).sum() if not g7_view.empty else 0)
        )
        total_td_deferred = (
            (pd.to_numeric(g1_view.get('Td Deferred', 0), errors='coerce').fillna(0).sum() if not g1_view.empty else 0)
            + (pd.to_numeric(g7_view.get('Td Deferred', 0), errors='coerce').fillna(0).sum() if not g7_view.empty else 0)
        )
        total_hpv_deferred = (
            pd.to_numeric(hpv_view.get('HPV Deferred 1', 0), errors='coerce').fillna(0).sum()
            + pd.to_numeric(hpv_view.get('HPV Deferred 2', 0), errors='coerce').fillna(0).sum()
            if not hpv_view.empty else 0
        )
        total_mr_refused = (
            (pd.to_numeric(g1_view.get('MR Refused', 0), errors='coerce').fillna(0).sum() if not g1_view.empty else 0)
            + (pd.to_numeric(g7_view.get('MR Refused', 0), errors='coerce').fillna(0).sum() if not g7_view.empty else 0)
        )
        total_td_refused = (
            (pd.to_numeric(g1_view.get('Td Refused', 0), errors='coerce').fillna(0).sum() if not g1_view.empty else 0)
            + (pd.to_numeric(g7_view.get('Td Refused', 0), errors='coerce').fillna(0).sum() if not g7_view.empty else 0)
        )
        total_hpv_refused = (
            pd.to_numeric(hpv_view.get('HPV Refused 1', 0), errors='coerce').fillna(0).sum()
            + pd.to_numeric(hpv_view.get('HPV Refused 2', 0), errors='coerce').fillna(0).sum()
            if not hpv_view.empty else 0
        )
        total_deferred = total_mr_deferred + total_td_deferred + total_hpv_deferred
        total_refused = total_mr_refused + total_td_refused + total_hpv_refused
        reasons_df = reason_summary(g1_view, g7_view, hpv_view)
        total_reason_records = pd.to_numeric(reasons_df['Count'], errors='coerce').fillna(0).sum() if not reasons_df.empty else 0

        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Total Deferred", f"{total_deferred:,.0f}")
        d2.metric("Total Refused", f"{total_refused:,.0f}")
        d3.metric("Recorded Missed-Vaccination Reasons", f"{total_reason_records:,.0f}")
        d4.metric("VaccTrack Report Rows", f"{len(g1_view) + len(g7_view) + len(hpv_view):,}")

        st.divider()

        st.markdown(
            '''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-chart-column" style="color:#0033A0; margin-right:8px;"></i>
            Deferrals and Refusals by Vaccine / Dose
            </h4>''',
            unsafe_allow_html=True
        )
        missed_summary = pd.DataFrame([
            {'Vaccine / Dose': 'MR - Grades 1 & 7', 'Deferred': total_mr_deferred, 'Refused': total_mr_refused},
            {'Vaccine / Dose': 'Td - Grades 1 & 7', 'Deferred': total_td_deferred, 'Refused': total_td_refused},
            {'Vaccine / Dose': 'HPV - Doses 1 & 2', 'Deferred': total_hpv_deferred, 'Refused': total_hpv_refused},
        ])
        missed_long = missed_summary.melt(
            id_vars=['Vaccine / Dose'],
            value_vars=['Deferred', 'Refused'],
            var_name='Outcome',
            value_name='Count'
        )
        fig_missed = px.bar(
            missed_long,
            x='Vaccine / Dose',
            y='Count',
            color='Outcome',
            barmode='group',
            text_auto='.0f',
            color_discrete_sequence=['#F9A825', '#D32F2F']
        )
        fig_missed.update_layout(
            dragmode=False,
            plot_bgcolor='rgba(0,0,0,0)',
            xaxis_title='',
            yaxis_title='Students',
            height=420,
            margin=dict(l=10, r=20, t=25, b=60),
            legend=dict(orientation='h', yanchor='top', y=-0.15, xanchor='center', x=0.5),
            legend_title_text=''
        )
        fig_missed.update_traces(textposition='outside', cliponaxis=False)
        st.plotly_chart(fig_missed, width='stretch', key='sbi_def_ref_summary')

        st.divider()

        outcome_frames = []
        for frame, deferred_cols, refused_cols in [
            (g1_view, ['MR Deferred', 'Td Deferred'], ['MR Refused', 'Td Refused']),
            (g7_view, ['MR Deferred', 'Td Deferred'], ['MR Refused', 'Td Refused']),
            (hpv_view, ['HPV Deferred 1', 'HPV Deferred 2'], ['HPV Refused 1', 'HPV Refused 2']),
        ]:
            if frame is None or frame.empty:
                continue
            part = frame[['Report Date']].copy()
            deferred_total = pd.Series(0.0, index=frame.index)
            refused_total = pd.Series(0.0, index=frame.index)
            for col in deferred_cols:
                if col in frame.columns:
                    deferred_total = deferred_total.add(pd.to_numeric(frame[col], errors='coerce').fillna(0), fill_value=0)
            for col in refused_cols:
                if col in frame.columns:
                    refused_total = refused_total.add(pd.to_numeric(frame[col], errors='coerce').fillna(0), fill_value=0)
            part['Deferred'] = deferred_total
            part['Refused'] = refused_total
            outcome_frames.append(part)

        if outcome_frames:
            render_daily_trend(
                pd.concat(outcome_frames, ignore_index=True),
                [('Deferred', 'Deferred'), ('Refused', 'Refused')],
                title='Daily Deferrals and Refusals by Report Date',
                key='sbi_def_ref_daily_trend', colors=['#F9A825', '#D32F2F'],
            )

        st.divider()

        st.markdown(
            '''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-list-ol" style="color:#0033A0; margin-right:8px;"></i>
            Reasons for Missed Vaccination
            </h4>''',
            unsafe_allow_html=True
        )
        reasons_nonzero = reasons_df[reasons_df['Count'] > 0].sort_values('Count', ascending=True).copy() if not reasons_df.empty else pd.DataFrame()
        if reasons_nonzero.empty:
            st.info("No reason counts were recorded for the selected period.")
        else:
            reasons_nonzero['Reason Label'] = reasons_nonzero['Reason Code'] + ' - ' + reasons_nonzero['Reason']
            fig_reasons = px.bar(
                reasons_nonzero,
                x='Count',
                y='Reason Label',
                orientation='h',
                text_auto='.0f',
                color_discrete_sequence=['#6D4C41']
            )
            fig_reasons.update_layout(
                dragmode=False,
                plot_bgcolor='rgba(0,0,0,0)',
                xaxis_title='Recorded cases',
                yaxis_title='',
                height=max(450, len(reasons_nonzero) * 38),
                margin=dict(l=10, r=35, t=25, b=40),
                showlegend=False
            )
            fig_reasons.update_traces(textposition='outside', cliponaxis=False)
            st.plotly_chart(fig_reasons, width='stretch', key='sbi_def_ref_reasons')

        st.divider()

        geo_col_def = 'Municipality' if view_mode == "All Municipalities (Abra)" else 'Barangay'
        geo_parts = []
        if not g1_view.empty:
            g1_geo = g1_view.groupby(geo_col_def, dropna=False)[['MR Deferred', 'Td Deferred', 'MR Refused', 'Td Refused']].sum().reset_index()
            g1_geo['Deferred'] = g1_geo['MR Deferred'] + g1_geo['Td Deferred']
            g1_geo['Refused'] = g1_geo['MR Refused'] + g1_geo['Td Refused']
            geo_parts.append(g1_geo[[geo_col_def, 'Deferred', 'Refused']])
        if not g7_view.empty:
            g7_geo = g7_view.groupby(geo_col_def, dropna=False)[['MR Deferred', 'Td Deferred', 'MR Refused', 'Td Refused']].sum().reset_index()
            g7_geo['Deferred'] = g7_geo['MR Deferred'] + g7_geo['Td Deferred']
            g7_geo['Refused'] = g7_geo['MR Refused'] + g7_geo['Td Refused']
            geo_parts.append(g7_geo[[geo_col_def, 'Deferred', 'Refused']])
        if not hpv_view.empty:
            hpv_geo_def = hpv_view.groupby(geo_col_def, dropna=False)[['HPV Deferred 1', 'HPV Deferred 2', 'HPV Refused 1', 'HPV Refused 2']].sum().reset_index()
            hpv_geo_def['Deferred'] = hpv_geo_def['HPV Deferred 1'] + hpv_geo_def['HPV Deferred 2']
            hpv_geo_def['Refused'] = hpv_geo_def['HPV Refused 1'] + hpv_geo_def['HPV Refused 2']
            geo_parts.append(hpv_geo_def[[geo_col_def, 'Deferred', 'Refused']])

        st.markdown(
            f'''<h4 style="margin-bottom:0.25rem;">
            <i class="fa-solid fa-location-dot" style="color:#0033A0; margin-right:8px;"></i>
            Missed Vaccination Outcomes by {geo_col_def}
            </h4>''',
            unsafe_allow_html=True
        )
        if geo_parts:
            geo_missed = pd.concat(geo_parts, ignore_index=True).groupby(geo_col_def, dropna=False)[['Deferred', 'Refused']].sum().reset_index()
            geo_missed['Total Missed'] = geo_missed['Deferred'] + geo_missed['Refused']
            geo_missed_long = geo_missed.sort_values('Total Missed', ascending=True).melt(
                id_vars=[geo_col_def],
                value_vars=['Deferred', 'Refused'],
                var_name='Outcome',
                value_name='Count'
            )
            fig_geo_missed = px.bar(
                geo_missed_long,
                x='Count',
                y=geo_col_def,
                color='Outcome',
                orientation='h',
                barmode='group',
                text_auto='.0f',
                color_discrete_sequence=['#F9A825', '#D32F2F']
            )
            fig_geo_missed.update_layout(
                dragmode=False,
                plot_bgcolor='rgba(0,0,0,0)',
                xaxis_title='Students',
                yaxis_title='',
                height=max(420, len(geo_missed) * 45),
                margin=dict(l=10, r=35, t=25, b=60),
                legend=dict(orientation='h', yanchor='top', y=-0.10, xanchor='center', x=0.5),
                legend_title_text=''
            )
            fig_geo_missed.update_traces(textposition='outside', cliponaxis=False)
            st.plotly_chart(fig_geo_missed, width='stretch', key='sbi_def_ref_geo')

            st.dataframe(
                geo_missed.sort_values('Total Missed', ascending=False),
                width='stretch',
                hide_index=True,
                column_config={
                    'Deferred': st.column_config.NumberColumn('Deferred', format='%d'),
                    'Refused': st.column_config.NumberColumn('Refused', format='%d'),
                    'Total Missed': st.column_config.NumberColumn('Total Missed', format='%d'),
                }
            )
            st.download_button(
                label='Download Deferral and Refusal Summary (CSV)',
                data=geo_missed.to_csv(index=False).encode('utf-8-sig'),
                file_name=f'SBI_Deferrals_Refusals_{location_label.replace(", ", "_")}.csv',
                mime='text/csv',
                key='sbi_def_ref_download'
            )
        else:
            st.info("No deferral or refusal records are available for this selection.")