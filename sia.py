import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np

# 1. Page Configuration
st.set_page_config(page_title="Abra SIA 2026 Tracker", page_icon="💉", layout="wide")

st.title("Abra Supplemental Immunization Activity (SIA) 2026")

# 2. Sidebar Command Center
with st.sidebar:
    st.header("⚙️ Dashboard Controls")
    
    if st.button("🔄 Refresh Data (Clear Cache)", use_container_width=True):
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

# 3. Create the Tabs
tab_target, tab_accomplishment = st.tabs(["🎯 Target Overview", "📈 Accomplishment Tracking"])

# 4. Helper Function for Data Cleaning
# (We put this in a function so both tabs can use it cleanly later)
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
        st.markdown("### Target Baseline")
        
        df_targets_raw = conn.read(worksheet="Target(Barangay)", usecols=list(range(11)), skiprows=2, names=col_names, ttl="10m")
        df_targets = clean_and_process_data(df_targets_raw, col_names)
        
        # Map Sidebar Filter
        col_map = {
            "All Ages (Grand Total)": "Grand_Total",
            "6 - 12 months": "6-12m_Total",
            "13 - 23 months": "13-23m_Total",
            "24 - 59 months": "24-59m_Total"
        }
        target_col = col_map[age_filter]

        # Apply View Mode
        selected_muni = None
        if view_mode == "Province-wide (Municipalities)":
            df_view = df_targets[df_targets['Level'] == 'Municipality']
            display_title = f"**Provincial Target Overview: {age_filter}**"
        else:
            municipality_list = df_targets[df_targets['Level'] == 'Municipality']['Location'].unique().tolist()
            default_idx = municipality_list.index("Manabo") if "Manabo" in municipality_list else 0
            
            with st.sidebar:
                # We place the municipality dropdown inside the sidebar if the second radio button is clicked
                selected_muni = st.selectbox("Select Municipality:", municipality_list, index=default_idx)
                
            df_view = df_targets[(df_targets['Level'] == 'Barangay') & (df_targets['Parent_Municipality'] == selected_muni)]
            display_title = f"**Barangay Breakdown for {selected_muni}: {age_filter}**"

        # KPI Metrics
        st.markdown("##### 📊 Target Population Breakdown")
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        kpi1.metric("Grand Total (All Ages)", f"{df_view['Grand_Total'].sum():,.0f}")
        kpi2.metric("6 - 12 months", f"{df_view['6-12m_Total'].sum():,.0f}")
        kpi3.metric("13 - 23 months", f"{df_view['13-23m_Total'].sum():,.0f}")
        kpi4.metric("24 - 59 months", f"{df_view['24-59m_Total'].sum():,.0f}")
        st.divider()

        # Visuals
        st.markdown(display_title)
        if not df_view.empty:
            df_sorted = df_view.sort_values(target_col, ascending=False)
            st.bar_chart(data=df_sorted, x='Location', y=target_col, use_container_width=True)
        else:
            st.warning("No data available for this selection.")

        # Raw Data Expander
        with st.expander("View Cleaned Target Database"):
            st.dataframe(df_targets[['Code', 'Location', 'Level', 'Parent_Municipality', 'Grand_Total', '6-12m_Total', '13-23m_Total', '24-59m_Total']], use_container_width=True)

    # ==========================================
    # TAB 2: ACCOMPLISHMENT TRACKING
    # ==========================================
    with tab_accomplishment:
        st.markdown("### Daily Accomplishment & Coverage")
        st.info("🚧 This section is ready to receive your daily VaccTrack exports. We will activate the live coverage metrics, grouped bar charts, and remaining targets here once your new Google Sheet tab is set up.")

except Exception as e:
    st.error(f"Error loading data: {e}")
