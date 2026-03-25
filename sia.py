import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np

# 1. Page Configuration
st.set_page_config(page_title="Abra SIA 2026 Tracker", page_icon="💉", layout="wide")

st.title("Abra Supplemental Immunization Activity (SIA) 2026")
st.markdown("### Target Overview Dashboard")
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
    df_raw = df_raw.dropna(subset=['Code'])
    
    # Clean the numbers
    numeric_cols = col_names[2:] 
    for col in numeric_cols:
        df_raw[col] = pd.to_numeric(df_raw[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)

    # 3. Hierarchy Logic: Identify Province, Municipality, and Barangay
    df_raw['Level'] = 'Barangay'
    # Province ends in 00000
    df_raw.loc[df_raw['Code'].astype(str).str.endswith('00000'), 'Level'] = 'Province'
    # Municipality ends in 000 (but is not the province)
    df_raw.loc[(df_raw['Code'].astype(str).str.endswith('000')) & (~df_raw['Code'].astype(str).str.endswith('00000')), 'Level'] = 'Municipality'

    # Assign Parent Municipality to each Barangay using forward fill
    df_raw['Parent_Municipality'] = df_raw.apply(
        lambda row: row['Location'] if row['Level'] == 'Municipality' else np.nan, axis=1
    )
    df_raw['Parent_Municipality'] = df_raw['Parent_Municipality'].ffill()

    # Calculate Grand Total
    df_raw['Grand_Total'] = df_raw['6-12m_Total'] + df_raw['13-23m_Total'] + df_raw['24-59m_Total']

    # Top Metric (Total for the whole province)
    total_provincial_target = df_raw[df_raw['Level'] == 'Province']['Grand_Total'].sum()
    # Fallback just in case the province row is missing
    if total_provincial_target == 0: 
        total_provincial_target = df_raw[df_raw['Level'] == 'Municipality']['Grand_Total'].sum()
        
    st.metric(label="Total Provincial SIA Target", value=f"{total_provincial_target:,.0f}")
    st.divider()

    # 4. Interactive Dashboard Controls
    st.subheader("Target Distribution")
    
    # Create two columns for layout
    col1, col2 = st.columns([1, 2])
    
    with col1:
        view_mode = st.radio(
            "Select View Level:", 
            ["By Municipality (Province-wide)", "By Barangay (Specific Municipality)"]
        )

    # 5. Dynamic Visualizations
    with col2:
        if view_mode == "By Municipality (Province-wide)":
            # Filter for Municipalities only
            df_view = df_raw[df_raw['Level'] == 'Municipality']
            df_sorted = df_view.sort_values('Grand_Total', ascending=False)
            
            st.markdown("**Provincial Overview (All 27 Municipalities)**")
            st.bar_chart(data=df_sorted, x='Location', y='Grand_Total', use_container_width=True)

        else:
            # Filter for Barangays, let user pick the Municipality
            municipality_list = df_raw[df_raw['Level'] == 'Municipality']['Location'].unique()
            
            # Default to the first municipality in the list, or set a specific default if needed
            selected_muni = st.selectbox("Select Municipality to view its Barangays:", municipality_list)
            
            df_view = df_raw[(df_raw['Level'] == 'Barangay') & (df_raw['Parent_Municipality'] == selected_muni)]
            df_sorted = df_view.sort_values('Grand_Total', ascending=False)
            
            st.markdown(f"**Barangay Breakdown for {selected_muni}**")
            st.bar_chart(data=df_sorted, x='Location', y='Grand_Total', use_container_width=True)

    # 6. Raw Data Expander
    with st.expander("View Cleaned Target Database"):
        st.dataframe(df_raw[['Code', 'Location', 'Level', 'Parent_Municipality', 'Grand_Total']], use_container_width=True)

except Exception as e:
    st.error(f"Error loading data: {e}")
