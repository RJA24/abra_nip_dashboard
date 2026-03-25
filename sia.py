import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd

# 1. Page Configuration
st.set_page_config(
    page_title="Abra SIA 2026 Tracker", 
    page_icon="💉", 
    layout="wide"
)

st.title("Abra Supplemental Immunization Activity (SIA) 2026")
st.markdown("### Target Overview Dashboard")
st.divider()

try:
    # 2. Establish Connection
    conn = st.connection("gsheets", type=GSheetsConnection)
    
    # 3. Intelligent Data Extraction
    # We explicitly define the 11 columns from A to K so Pandas doesn't get confused by merged cells
    col_names = [
        "Code", "Location", 
        "6-12m_Male", "6-12m_Female", "6-12m_Total", 
        "13-23m_Male", "13-23m_Female", "13-23m_Total", 
        "24-59m_Male", "24-59m_Female", "24-59m_Total"
    ]
    
    # Read the sheet, skipping the first 2 messy header rows
    df_raw = conn.read(worksheet="Target(Barangay)", usecols=list(range(11)), skiprows=2, names=col_names, ttl="10m")
    
    # Drop rows where there is no location data
    df_raw = df_raw.dropna(subset=['Code'])
    
    # Clean the numbers (remove commas and convert from text to integers)
    numeric_cols = col_names[2:] # All columns from index 2 onwards
    for col in numeric_cols:
        df_raw[col] = pd.to_numeric(df_raw[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
        
    # 4. Intelligent Filtering
    # Filter OUT the province/municipality total rows (Abra, Bangued) 
    # We know it's a summary row if the Code ends with '000'
    df_barangays = df_raw[~df_raw['Code'].astype(str).str.endswith('000')].copy()
    
    # Calculate the Grand Total for each Barangay across all age groups
    df_barangays['Grand_Total'] = df_barangays['6-12m_Total'] + df_barangays['13-23m_Total'] + df_barangays['24-59m_Total']
    
    # Calculate the overall provincial target for the top metric
    total_target = df_barangays['Grand_Total'].sum()
    
    # 5. Dashboard UI
    st.metric(label="Total Provincial SIA Target", value=f"{total_target:,.0f}")
    st.divider()
    
    st.subheader("Target Distribution per Barangay")
    
    # Sort from highest to lowest target
    df_sorted = df_barangays.sort_values('Grand_Total', ascending=False)
    
    # Display the clean bar chart
    st.bar_chart(data=df_sorted, x='Location', y='Grand_Total', use_container_width=True)

    # 6. Raw Data Expander
    with st.expander("View Cleaned Target Database"):
        # We show the cleaned dataframe so you can verify the formatting worked
        st.dataframe(df_barangays, use_container_width=True)

except Exception as e:
    st.error(f"Error loading data: {e}")
