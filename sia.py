import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np
import plotly.express as px
from datetime import datetime
import pytz

# 1. Page Configuration
st.set_page_config(page_title="Abra SIA 2026 Tracker", page_icon="💉", layout="wide")

st.title("Abra Supplemental Immunization Activity (SIA) 2026")

# 2. Timestamp Function (Tied to the 10-minute cache)
@st.cache_data(ttl="10m")
def get_last_updated_time():
    tz = pytz.timezone('Asia/Manila')
    return datetime.now(tz).strftime("%B %d, %Y | %I:%M %p")

last_updated = get_last_updated_time()

# 3. Sidebar Command Center
with st.sidebar:
    st.header("⚙️ Dashboard Controls")
    
    # Display the timestamp
    st.caption(f"**Last Data Sync:**")
    st.caption(f"🕒 {last_updated}")
    
    if st.button("🔄 Force Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
        
    st.divider()
    
    view_mode = st.radio(
        "Select View Level:", 
        ["Province-wide (Municipalities)", "Specific Municipality (Barangays)"]
    )
    
    age_filter = st.selectbox(
        "Select Age Group to Chart:", 
        ["All Ages (Grand Total)", "6 - 12 months", "13 - 23 months", "24 - 59 months"]
    )

# 4. Create the 4 Tabs
tab_target, tab_mr, tab_vita, tab_total = st.tabs([
    "🎯 Target Overview", 
    "💉 MR Accomplishment", 
    "💊 Vit A Accomplishment", 
    "📊 Total Accomplishment"
])

# 5. Helper Function for Data Cleaning
def clean_and_process_data(df, col_names):
    df['Code'] = df['Code'].astype(str).str.split('.').str[0]
    df = df[df['Code'] != 'nan']
    df = df[df['Code'] != 'None']
    df = df[df['Code'] != '']
    
    numeric_cols = col_names[2:] 
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        
    df['Level'] = 'Barangay'
    df.loc[df['Code'].str.endswith('00000'), 'Level'] = 'Province'
    df.loc[(df['Code'].str.endswith('000')) & (~df['Code'].str.endswith('00000')), 'Level'] = 'Municipality'

    df['Parent_Municipality'] = df.apply(
        lambda row: row['Location'] if row['Level'] == 'Municipality' else np.nan, axis=1
    )
    df['Parent_Municipality'] = df['Parent_Municipality'].ffill()
    df['Grand_Total'] = df['6-12m_Total'] + df['13-23m_Total'] + df['24-59m_Total']
    return df

try:
    # Establish Connection
    conn = st.connection("gsheets", type=GSheetsConnection)
    col_names = [
        "Code", "Location", 
        "6-12m_Male", "6-12m_Female", "6-12m_Total", 
        "13-23m_Male", "13-23m_Female", "13-23m_Total", 
        "24-59m_Male", "24-59m_Female", "24-59m_Total"
    ]
    
    # ==========================================
    # TAB 1: TARGET OVERVIEW
    # ==========================================
    with tab_target:
        st.markdown("### Target Baseline Overview")
        
        df_targets_raw = conn.read(worksheet="Target(Barangay)", usecols=list(range(11)), skiprows=2, names=col_names, ttl="10m")
        df_targets = clean_and_process_data(df_targets_raw, col_names)
        
        col_map = {
            "All Ages (Grand Total)": "Grand_Total",
            "6 - 12 months": "6-12m_Total",
            "13 - 23 months": "13-23m_Total",
            "24 - 59 months": "24-59m_Total"
        }
        target_col = col_map[age_filter]

        selected_muni = None
        if view_mode == "Province-wide (Municipalities)":
            df_view = df_targets[df_targets['Level'] == 'Municipality']
            chart_title = f"Provincial Targets: {age_filter}"
        else:
            municipality_list = df_targets[df_targets['Level'] == 'Municipality']['Location'].unique().tolist()
            default_idx = municipality_list.index("Manabo") if "Manabo" in municipality_list else 0
            
            with st.sidebar:
                selected_muni = st.selectbox("Select Municipality:", municipality_list, index=default_idx)
                
            df_view = df_targets[(df_targets['Level'] == 'Barangay') & (df_targets['Parent_Municipality'] == selected_muni)]
            chart_title = f"Barangay Targets for {selected_muni}: {age_filter}"

        # 1. The KPI Metric Row
        st.markdown("##### 👥 Target Population Breakdown")
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        kpi1.metric("Grand Total (All Ages)", f"{df_view['Grand_Total'].sum():,.0f}")
        kpi2.metric("6 - 12 months", f"{df_view['6-12m_Total'].sum():,.0f}")
        kpi3.metric("13 - 23 months", f"{df_view['13-23m_Total'].sum():,.0f}")
        kpi4.metric("24 - 59 months", f"{df_view['24-59m_Total'].sum():,.0f}")
        st.divider()

        # 2. Interactive Plotly Charts
        if not df_view.empty:
            col_chart1, col_chart2 = st.columns([7, 3])
            
            with col_chart1:
                df_sorted = df_view.sort_values(target_col, ascending=True) 
                
                fig_bar = px.bar(
                    df_sorted, 
                    x=target_col, 
                    y='Location', 
                    orientation='h',
                    title=chart_title,
                    text_auto='.0f', 
                    color_discrete_sequence=['#1E88E5'] 
                )
                fig_bar.update_layout(
                    xaxis_title="Number of Eligible Children", 
                    yaxis_title="",
                    plot_bgcolor='rgba(0,0,0,0)', 
                    height=500
                )
                st.plotly_chart(fig_bar, use_container_width=True)
                
            with col_chart2:
                age_data = pd.DataFrame({
                    'Age Group': ['6-12 months', '13-23 months', '24-59 months'],
                    'Target': [
                        df_view['6-12m_Total'].sum(), 
                        df_view['13-23m_Total'].sum(), 
                        df_view['24-59m_Total'].sum()
                    ]
                })
                
                fig_donut = px.pie(
                    age_data, 
                    names='Age Group', 
                    values='Target', 
                    hole=0.4,
                    title="Age Distribution",
                    color_discrete_sequence=['#43A047', '#FFB300', '#E53935'] 
                )
                fig_donut.update_layout(
                    legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
                    height=500
                )
                st.plotly_chart(fig_donut, use_container_width=True)
                
        else:
            st.warning("No data available for this selection.")

        # 3. Clean Data Expander
        with st.expander("View Target Database Table"):
            st.dataframe(df_targets[['Code', 'Location', 'Level', 'Parent_Municipality', 'Grand_Total', '6-12m_Total', '13-23m_Total', '24-59m_Total']], use_container_width=True, hide_index=True)

    # ==========================================
    # TAB 2: MR ACCOMPLISHMENT (Standby)
    # ==========================================
    with tab_mr:
        st.markdown("### Measles-Rubella (MR) Coverage")
        st.info("🚧 Dashboard framework ready. Awaiting structural verification of the VaccTrack MR export file.")

    # ==========================================
    # TAB 3: VITAMIN A ACCOMPLISHMENT (Standby)
    # ==========================================
    with tab_vita:
        st.markdown("### Vitamin A Supplementation Coverage")
        st.info("🚧 Dashboard framework ready. Awaiting structural verification of the VaccTrack Vitamin A export file.")

    # ==========================================
    # TAB 4: TOTAL ACCOMPLISHMENT (Standby)
    # ==========================================
    with tab_total:
        st.markdown("### Executive Summary: Campaign Performance")
        st.info("🚧 This view will automatically populate once the individual MR and Vitamin A streams are connected.")

except Exception as e:
    st.error(f"Error loading data: {e}")
