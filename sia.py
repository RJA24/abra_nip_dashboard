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
    
    # Read the specific Target tab
    df_targets = conn.read(worksheet="Target(Barangay)", ttl="10m")
    
    # Basic Cleaning: Google Sheets often loads empty rows, this drops them
    df_targets = df_targets.dropna(how="all")
    
    # 3. Top-Level Metrics
    # Check if the expected columns exist to prevent crash errors
    if 'Barangay' in df_targets.columns and 'Target' in df_targets.columns:
        
        # Calculate the total target sum
        total_target = df_targets['Target'].sum()
        
        # Display a prominent metric card
        st.metric(label="Total Provincial SIA Target", value=f"{total_target:,.0f}")
        st.divider()
        
        # 4. Visualization
        st.subheader("Target Distribution per Barangay")
        
        # Sort the data from highest target to lowest for a cleaner chart
        df_sorted = df_targets.sort_values('Target', ascending=False)
        
        # Display the bar chart
        st.bar_chart(data=df_sorted, x='Barangay', y='Target', use_container_width=True)
        
    else:
        st.info("💡 To see the charts, ensure your columns in the sheet are named exactly 'Barangay' and 'Target', or update the Python code to match your actual column names.")

    # 5. Raw Data Preview (hidden inside an expander to keep the UI clean)
    with st.expander("View Raw Target Database"):
        st.dataframe(df_targets, use_container_width=True)

except Exception as e:
    st.error(f"Error loading data: {e}")
