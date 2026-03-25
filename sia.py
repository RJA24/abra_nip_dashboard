import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np
import plotly.express as px
from datetime import datetime
import pytz

# 1. Page Configuration
st.set_page_config(page_title="CAR SIA 2026 Tracker", page_icon="💉", layout="wide")

st.title("Cordillera Administrative Region (CAR) SIA 2026")

# 2. Timestamp Function (Tied to the 10-minute cache)
@st.cache_data(ttl="10m")
def get_last_updated_time():
    tz = pytz.timezone('Asia/Manila')
    return datetime.now(tz).strftime("%B %d, %Y | %I:%M %p")

last_updated = get_last_updated_time()

# 3. Sidebar Command Center
with st.sidebar:
    st.header("⚙️ Dashboard Controls")
    
    st.caption(f"**Last Data Sync:**")
    st.caption(f"🕒 {last_updated}")
    
    if st.button("🔄 Force Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
        
    st.divider()
    
    # NEW: 3-Tier View Mode
    view_mode = st.radio(
        "Select View Level:", 
        [
            "Region-wide (Compare Provinces)", 
            "Province-wide (Compare Municipalities)", 
            "Specific Municipality (Compare Barangays)"
        ]
    )
    
    # NEW: 6-59 months is now explicitly in the data
    age_filter = st.selectbox(
        "Select Age Group to Chart:", 
        ["6 - 59 months (Grand Total)", "6 - 12 months", "13 - 23 months", "24 - 59 months"]
    )

# 4. Create the 4 Tabs
tab_target, tab_mr, tab_vita, tab_total = st.tabs([
    "🎯 Target Overview", 
    "💉 MR Accomplishment", 
    "💊 Vit A Accomplishment", 
    "📊 Total Accomplishment"
])

# 5. Helper Function for CAR Data Cleaning
def clean_and_process_car_data(df, col_names):
    # Remove invisible decimals and blanks from Code
    df['Code'] = df['Code'].astype(str).str.split('.').str[0]
    df = df[df['Code'] != 'nan']
    df = df[df['Code'] != 'None']
    df = df[df['Code'] != '']
    
    # Clean numeric columns (now starting at index 2)
    numeric_cols = col_names[2:] 
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        
    # Regional Hierarchy Engine
    df['Level'] = 'Barangay'
    df.loc[df['Code'].str.endswith('00000000'), 'Level'] = 'Region'
    df.loc[(df['Code'].str.endswith('00000')) & (~df['Code'].str.endswith('00000000')), 'Level'] = 'Province'
    df.loc[(df['Code'].str.endswith('000')) & (~df['Code'].str.endswith('00000')), 'Level'] = 'Municipality'

    # Forward-fill relationships
    df['Parent_Province'] = df.apply(lambda row: row['Location'] if row['Level'] == 'Province' else np.nan, axis=1).ffill()
    df['Parent_Municipality'] = df.apply(lambda row: row['Location'] if row['Level'] == 'Municipality' else np.nan, axis=1).ffill()
    
    return df

try:
    # Establish Connection
    conn = st.connection("gsheets", type=GSheetsConnection)
    sheet_url = "https://docs.google.com/spreadsheets/d/1hM0yhzLY5uCh-bxFRPV7u6MYAzimfG0f4uluUGkLogU"
    
    # Updated to 14 columns to prevent data shifting
    col_names = [
        "Code", "Location", 
        "6-59m_Male", "6-59m_Female", "6-59m_Total",
        "6-12m_Male", "6-12m_Female", "6-12m_Total", 
        "13-23m_Male", "13-23m_Female", "13-23m_Total", 
        "24-59m_Male", "24-59m_Female", "24-59m_Total"
    ]
    
    # ==========================================
    # TAB 1: TARGET OVERVIEW (CAR REGION)
    # ==========================================
    with tab_target:
        st.markdown("### Regional Target Baseline Overview")
        
        # Notice we changed usecols=list(range(16)) to usecols=list(range(14))
        df_targets_raw = conn.read(spreadsheet=sheet_url, worksheet="Target(CAR)", usecols=list(range(14)), skiprows=2, names=col_names, ttl="10m")
        df_targets = clean_and_process_car_data(df_targets_raw, col_names)
        
        # Map the filter selection to the exact column name
        col_map = {
            "6 - 59 months (Grand Total)": "6-59m_Total",
            "6 - 12 months": "6-12m_Total",
            "13 - 23 months": "13-23m_Total",
            "24 - 59 months": "24-59m_Total"
        }
        target_col = col_map[age_filter]

        # Cascading Filter Logic
        df_view = pd.DataFrame()
        chart_title = ""
        
        if view_mode == "Region-wide (Compare Provinces)":
            df_view = df_targets[df_targets['Level'] == 'Province']
            chart_title = f"CAR Regional Targets: {age_filter}"
            
        elif view_mode == "Province-wide (Compare Municipalities)":
            province_list = df_targets[df_targets['Level'] == 'Province']['Location'].unique().tolist()
            default_prov_idx = province_list.index("Abra") if "Abra" in province_list else 0
            
            with st.sidebar:
                selected_prov = st.selectbox("Select Province:", province_list, index=default_prov_idx)
                
            df_view = df_targets[(df_targets['Level'] == 'Municipality') & (df_targets['Parent_Province'] == selected_prov)]
            chart_title = f"Municipal Targets for {selected_prov}: {age_filter}"
            
        else: # Specific Municipality (Compare Barangays)
            province_list = df_targets[df_targets['Level'] == 'Province']['Location'].unique().tolist()
            default_prov_idx = province_list.index("Abra") if "Abra" in province_list else 0
            
            with st.sidebar:
                selected_prov = st.selectbox("Select Province:", province_list, index=default_prov_idx)
                
                muni_list = df_targets[(df_targets['Level'] == 'Municipality') & (df_targets['Parent_Province'] == selected_prov)]['Location'].unique().tolist()
                default_muni_idx = muni_list.index("Manabo") if "Manabo" in muni_list else 0
                
                selected_muni = st.selectbox("Select Municipality:", muni_list, index=default_muni_idx)
                
            df_view = df_targets[(df_targets['Level'] == 'Barangay') & (df_targets['Parent_Municipality'] == selected_muni)]
            chart_title = f"Barangay Targets for {selected_muni}, {selected_prov}: {age_filter}"

        # 1. The KPI Metric Row
        st.markdown("##### 👥 Target Population Breakdown")
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        kpi1.metric("6 - 59 months (Grand Total)", f"{df_view['6-59m_Total'].sum():,.0f}")
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
        with st.expander("View Cleaned Regional Target Database"):
            st.dataframe(df_targets[['Code', 'Location', 'Level', 'Parent_Province', 'Parent_Municipality', '6-59m_Total', '6-12m_Total', '13-23m_Total', '24-59m_Total']], use_container_width=True, hide_index=True)

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
