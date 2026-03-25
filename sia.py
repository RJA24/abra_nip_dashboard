import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np

# 1. Page Configuration
st.set_page_config(page_title="Abra SIA 2026 Tracker", page_icon="💉", layout="wide")

st.title("Abra Supplemental Immunization Activity (SIA) 2026")
st.markdown("### Target Overview Dashboard")

# 2. Sidebar Controls (The Command Center)
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

st.divider()

try:
    # 3. Establish Connection & Extract Data
    conn = st.connection("gsheets", type=GSheetsConnection)
    col_names = [
        "Code", "Location", 
        "6-12m_Male", "6-12m_Female", "6-12m_Total", 
        "13-23m_Male", "13-23m_Female", "13-23m_Total", 
        "24-59m_Male", "24-59m_Female", "24-59m_Total"
    ]
    
    df_raw = conn.read(worksheet="Target(Barangay)", usecols=list(range(11)), skiprows=2, names=col_names, ttl="10m")
    
    # Data Cleaning (Removing invisible decimals and blanks)
    df_raw['Code'] = df_raw['Code'].astype(str).str.split('.').str[0]
    df_raw = df_raw[df_raw['Code'] != 'nan']
    df_raw = df_raw[df_raw['Code'] != 'None']
    df_raw = df_raw[df_raw['Code'] != '']
    
    numeric_cols = col_names[2:] 
    for col in numeric_cols:
        df_raw[col] = pd.to_numeric(df_raw[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)

    # Hierarchy Logic
    df_raw['Level'] = 'Barangay'
    df_raw.loc[df_raw['Code'].str.endswith('00000'), 'Level'] = 'Province'
    df_raw.loc[(df_raw['Code'].str.endswith('000')) & (~df_raw['Code'].str.endswith('00000')), 'Level'] = 'Municipality'

    df_raw['Parent_Municipality'] = df_raw.apply(
        lambda row: row['Location'] if row['Level'] == 'Municipality' else np.nan, axis=1
    )
    df_raw['Parent_Municipality'] = df_raw['Parent_Municipality'].ffill()

    df_raw['Grand_Total'] = df_raw['6-12m_Total'] + df_raw['13-23m_Total'] + df_raw['24-59m_Total']

    # 4. Map the Sidebar Filter to the Data Column
    col_map = {
        "All Ages (Grand Total)": "Grand_Total",
        "6 - 12 months": "6-12m_Total",
        "13 - 23 months": "13-23m_Total",
        "24 - 59 months": "24-59m_Total"
    }
    target_col = col_map[age_filter]

    # 5. Apply Logic Based on View Mode
    selected_muni = None
    if view_mode == "Province-wide (Municipalities)":
        df_view = df_raw[df_raw['Level'] == 'Municipality']
        display_title = f"**Provincial Overview: {age_filter} Targets**"
    else:
        municipality_list = df_raw[df_raw['Level'] == 'Municipality']['Location'].unique().tolist()
        
        # Default to Manabo if it exists in the list, otherwise default to the first index
        default_idx = municipality_list.index("Manabo") if "Manabo" in municipality_list else 0
        
        with st.sidebar:
            selected_muni = st.selectbox("Select Municipality:", municipality_list, index=default_idx)
            
        df_view = df_raw[(df_raw['Level'] == 'Barangay') & (df_raw['Parent_Municipality'] == selected_muni)]
        display_title = f"**Barangay Breakdown for {selected_muni}: {age_filter} Targets**"

    # 6. Dynamic KPI Metrics Row
    # These recalculate based on whether you are looking at the Province or a Municipality
    st.markdown("##### 📊 Target Population Breakdown")
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    
    kpi1.metric("Grand Total (All Ages)", f"{df_view['Grand_Total'].sum():,.0f}")
    kpi2.metric("6 - 12 months", f"{df_view['6-12m_Total'].sum():,.0f}")
    kpi3.metric("13 - 23 months", f"{df_view['13-23m_Total'].sum():,.0f}")
    kpi4.metric("24 - 59 months", f"{df_view['24-59m_Total'].sum():,.0f}")
    
    st.divider()

    # 7. Dynamic Visualization
    st.markdown(display_title)
    
    if not df_view.empty:
        df_sorted = df_view.sort_values(target_col, ascending=False)
        st.bar_chart(data=df_sorted, x='Location', y=target_col, use_container_width=True)
    else:
        st.warning("No data available for this selection.")

    # 8. Raw Data Expander
    with st.expander("View Cleaned Target Database"):
        st.dataframe(df_raw[['Code', 'Location', 'Level', 'Parent_Municipality', 'Grand_Total', '6-12m_Total', '13-23m_Total', '24-59m_Total']], use_container_width=True)

except Exception as e:
    st.error(f"Error loading data: {e}")
