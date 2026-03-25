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
st.markdown("### DOH Tracking Dashboard")
st.divider()

# 2. Establish Connection to Google Sheets
# This looks for the credentials we will set up in Streamlit Secrets
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
    
    # Read the data (replace 'Sheet1' with your actual tab name)
    # ttl="10m" caches the data for 10 minutes to save API calls
    df = conn.read(worksheet="Target(Barangay)", ttl="10m")
    
    st.success("✅ Successfully connected to VaccTrack Sheets Database!")
    
    # 3. Basic Data Display
    if st.checkbox("View Raw Data"):
        st.dataframe(df, use_container_width=True)
        
    # Placeholder for your upcoming visualizations
    st.subheader("Immunization Coverage Metrics")
    st.info("Visualizations (Plotly/Altair) will be placed here once data is cleaned.")

except Exception as e:
    st.error(f"Connection Error: {e}")
    st.warning("Ensure your Streamlit Cloud secrets are configured and the Sheet is shared with your Service Account email.")
