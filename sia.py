import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np

# 1. Page Configuration
st.set_page_config(page_title="Abra SIA 2026 Tracker", page_icon="💉", layout="wide")

st.title("Abra Supplemental Immunization Activity (SIA) 2026")
st.markdown("### Target Overview Dashboard")

# Add a refresh button to bypass the 10-minute cache
if st.sidebar.button("🔄 Refresh Data (Clear Cache)"):
    st.cache_data.clear()
    st.rerun()

st.divider()

try:
    # 2. Establish Connection & Extract Data
    conn = st.connection("gsheets", type=GSheetsConnection)
    col_names = [
        "Code", "Location", 
        "6-12m_Male", "6-12m_Female", "6-12m_Total", 
        "13-23m_Male", "13-23m_Female", "13-23m_Total", 
        "24-59m_Male", "24-59m_Female", "24-59m_Total"
    ]
    
    df_raw = conn.read(worksheet="Target(Barangay)", usecols=list(range(11)), skiprows=2, names=col_names, ttl="10m")
    
    # --- THE FIX ---
    # Convert Code to string, split by '.' to remove '.0', and drop any empty 'nan' rows
    df_raw['Code'] = df_raw['Code'].astype(str).str.split('.').str[0]
    df_raw = df_raw[df_raw['Code'] != 'nan']
    df_raw = df_raw[df_raw['Code'] != 'None']
    df_raw = df_raw[df_raw['Code'] != '']
    
    # Clean the numbers
    numeric_cols = col_names[2:] 
    for col in numeric_cols:
        df_raw[col] = pd.to_numeric(df_raw[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)

    # 3. Hierarchy Logic
    df_raw['Level'] = 'Barangay'
    df_raw.loc[df_raw['Code'].str.endswith('00000'), 'Level'] = 'Province'
    df_raw.loc[(df_raw['Code'].str.endswith('000')) & (~df_raw['Code'].str.endswith('00000')), 'Level'] = 'Municipality'

    # Assign Parent Municipality
    df_raw['Parent_Municipality'] = df_raw.apply(
        lambda row: row['Location'] if row['Level'] == 'Municipality' else np.nan, axis=1
    )
    df_raw['Parent_Municipality'] = df_raw['Parent_Municipality'].ffill()

    # Calculate Grand Total
    df_raw['Grand_Total'] = df_raw['6-12m_Total'] + df_raw['13-23m_Total'] + df_raw['24-59m_Total']

    # Top Metric
    total_provincial_target = df_raw[df_raw['Level'] == 'Province']['Grand_Total'].sum()
    if total_provincial_target == 0: 
        total_provincial_target = df_raw[df_raw['Level'] == 'Municipality']['Grand_Total'].sum()
        
    st.metric(label="Total Provincial SIA Target", value=f"{total_provincial_target:,.0f}")
    st.divider()

    # 4. Interactive Dashboard Controls
    st.subheader("Target Distribution")
    
    col1, col2 = st.columns([1, 2])
    
    with col1:
        view_mode = st.radio(
            "Select View Level:", 
            ["By Municipality (Province-wide)", "By Barangay (Specific Municipality)"]
        )

    # 5. Dynamic Visualizations
    with col2:
        if view_mode == "By Municipality (Province-wide)":
            df_view = df_raw[df_raw['Level'] == 'Municipality']
            df_sorted = df_view.sort_values('Grand_Total', ascending=False)
            
            st.markdown("**Provincial Overview (All 27 Municipalities)**")
            st.bar_chart(data=df_sorted, x='Location', y='Grand_Total', use_container_width=True)

        else:
            municipality_list = df_raw[df_raw['Level'] == 'Municipality']['Location'].unique()
            
            if len(municipality_list) > 0:
                selected_muni = st.selectbox("Select Municipality to view its Barangays:", municipality_list)
                
                df_view = df_raw[(df_raw['Level'] == 'Barangay') & (df_raw['Parent_Municipality'] == selected_muni)]
                df_sorted = df_view.sort_values('Grand_Total', ascending=False)
                
                st.markdown(f"**Barangay Breakdown for {selected_muni}**")
                st.bar_chart(data=df_sorted, x='Location', y='Grand_Total', use_container_width=True)
            else:
                st.warning("No municipalities found. Please check raw data.")

    # 6. Raw Data Expander
    with st.expander("View Cleaned Target Database"):
        st.dataframe(df_raw[['Code', 'Location', 'Level', 'Parent_Municipality', 'Grand_Total']], use_container_width=True)

except Exception as e:
    st.error(f"Error loading data: {e}")
