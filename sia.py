import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np

# 1. Page Configuration
st.set_page_config(page_title="Abra SIA 2026 Tracker", page_icon="💉", layout="wide")

st.title("Abra Supplemental Immunization Activity (SIA) 2026")
st.markdown("### Coverage & Accomplishment Dashboard")

# 2. Sidebar Command Center
with st.sidebar:
    st.header("⚙️ Dashboard Controls")
    
    if st.button("🔄 Refresh Data (Clear Cache)", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
        
    st.divider()
    view_mode = st.radio("Select View Level:", ["Province-wide (Municipalities)", "Specific Municipality (Barangays)"])
    age_filter = st.selectbox("Select Age Group:", ["All Ages (Grand Total)", "6 - 12 months", "13 - 23 months", "24 - 59 months"])

st.divider()

# Helper function to clean sheets data
def clean_sheet_data(df, col_names):
    df['Code'] = df['Code'].astype(str).str.split('.').str[0]
    df = df[df['Code'] != 'nan']
    df = df[df['Code'] != 'None']
    df = df[df['Code'] != '']
    numeric_cols = col_names[2:] 
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    return df

try:
    # 3. Establish Connection
    conn = st.connection("gsheets", type=GSheetsConnection)
    col_names = [
        "Code", "Location", 
        "6-12m_Male", "6-12m_Female", "6-12m_Total", 
        "13-23m_Male", "13-23m_Female", "13-23m_Total", 
        "24-59m_Male", "24-59m_Female", "24-59m_Total"
    ]
    
    # 4. Read Target Data
    df_targets = conn.read(worksheet="Target(Barangay)", usecols=list(range(11)), skiprows=2, names=col_names, ttl="10m")
    df_targets = clean_sheet_data(df_targets, col_names)
    
    # Calculate Grand Totals for Targets
    df_targets['Grand_Total'] = df_targets['6-12m_Total'] + df_targets['13-23m_Total'] + df_targets['24-59m_Total']

    # 5. Read or Simulate Accomplishment Data
    try:
        # Tries to read the real data from a new tab
        df_acc = conn.read(worksheet="Accomplishment(Barangay)", usecols=list(range(11)), skiprows=2, names=col_names, ttl="10m")
        df_acc = clean_sheet_data(df_acc, col_names)
        df_acc['Grand_Total'] = df_acc['6-12m_Total'] + df_acc['13-23m_Total'] + df_acc['24-59m_Total']
        st.success("✅ Live Accomplishment Data Loaded")
    except:
        # Fallback: Simulate data if the tab doesn't exist yet
        st.info("🛠️ 'Accomplishment(Barangay)' tab not found. Using simulated data for visualization testing.")
        df_acc = df_targets.copy()
        np.random.seed(42) # Keep simulation consistent
        for col in col_names[2:] + ['Grand_Total']:
            # Simulate coverage between 30% and 85%
            df_acc[col] = (df_acc[col] * np.random.uniform(0.3, 0.85, size=len(df_acc))).astype(int)

    # 6. Merge Targets and Accomplishments
    # Add suffixes to differentiate columns
    df_targets = df_targets.add_suffix('_Target').rename(columns={'Code_Target': 'Code', 'Location_Target': 'Location'})
    df_acc = df_acc.add_suffix('_Vaccinated').rename(columns={'Code_Vaccinated': 'Code'})
    
    df_merged = pd.merge(df_targets, df_acc, on='Code', how='left').fillna(0)

    # 7. Hierarchy Logic
    df_merged['Level'] = 'Barangay'
    df_merged.loc[df_merged['Code'].str.endswith('00000'), 'Level'] = 'Province'
    df_merged.loc[(df_merged['Code'].str.endswith('000')) & (~df_merged['Code'].str.endswith('00000')), 'Level'] = 'Municipality'

    df_merged['Parent_Municipality'] = df_merged.apply(
        lambda row: row['Location'] if row['Level'] == 'Municipality' else np.nan, axis=1
    )
    df_merged['Parent_Municipality'] = df_merged['Parent_Municipality'].ffill()

    # 8. Map Sidebar Filters to Data Columns
    col_map = {
        "All Ages (Grand Total)": ("Grand_Total_Target", "Grand_Total_Vaccinated"),
        "6 - 12 months": ("6-12m_Total_Target", "6-12m_Total_Vaccinated"),
        "13 - 23 months": ("13-23m_Total_Target", "13-23m_Total_Vaccinated"),
        "24 - 59 months": ("24-59m_Total_Target", "24-59m_Total_Vaccinated")
    }
    target_col, vacc_col = col_map[age_filter]

    # Calculate Coverage % for the specific filtered view
    df_merged['Coverage_%'] = (df_merged[vacc_col] / df_merged[target_col] * 100).fillna(0)

    # 9. Apply View Mode Filters
    selected_muni = None
    if view_mode == "Province-wide (Municipalities)":
        df_view = df_merged[df_merged['Level'] == 'Municipality']
        display_title = f"**Provincial Overview: {age_filter}**"
    else:
        municipality_list = df_merged[df_merged['Level'] == 'Municipality']['Location'].unique().tolist()
        default_idx = municipality_list.index("Manabo") if "Manabo" in municipality_list else 0
        
        with st.sidebar:
            selected_muni = st.selectbox("Select Municipality:", municipality_list, index=default_idx)
            
        df_view = df_merged[(df_merged['Level'] == 'Barangay') & (df_merged['Parent_Municipality'] == selected_muni)]
        display_title = f"**Barangay Breakdown for {selected_muni}: {age_filter}**"

    # 10. Dynamic KPI Metrics (Now with Coverage %)
    st.markdown(f"##### 📊 {age_filter} Performance Metrics")
    
    total_target = df_view[target_col].sum()
    total_vacc = df_view[vacc_col].sum()
    overall_coverage = (total_vacc / total_target * 100) if total_target > 0 else 0
    remaining = total_target - total_vacc

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("Eligible Target", f"{total_target:,.0f}")
    kpi2.metric("Vaccinated", f"{total_vacc:,.0f}")
    kpi3.metric("Coverage Rate", f"{overall_coverage:.1f}%")
    kpi4.metric("Remaining Unvaccinated", f"{remaining:,.0f}")
    
    st.divider()

    # 11. Grouped Bar Chart Visualization
    st.markdown(display_title)
    
    if not df_view.empty:
        df_sorted = df_view.sort_values('Coverage_%', ascending=False)
        
        # Streamlit automatically creates a grouped bar chart when given multiple Y columns
        st.bar_chart(
            data=df_sorted, 
            x='Location', 
            y=[target_col, vacc_col], 
            color=["#d3d3d3", "#00a4e4"], # Gray for Target, Blue for Vaccinated
            use_container_width=True
        )
    else:
        st.warning("No data available for this selection.")

    # 12. Detailed Coverage Table
    with st.expander("View Detailed Coverage Table"):
        display_cols = ['Code', 'Location', target_col, vacc_col, 'Coverage_%']
        df_display = df_sorted[display_cols].copy()
        
        # Format the numbers for readability
        df_display[target_col] = df_display[target_col].map("{:,.0f}".format)
        df_display[vacc_col] = df_display[vacc_col].map("{:,.0f}".format)
        df_display['Coverage_%'] = df_display['Coverage_%'].map("{:.1f}%".format)
        
        st.dataframe(df_display, use_container_width=True, hide_index=True)

except Exception as e:
    st.error(f"Error loading data: {e}")
