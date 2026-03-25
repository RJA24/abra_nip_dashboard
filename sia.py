import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np
import plotly.express as px
from datetime import datetime
import pytz
import time
import hashlib
from supabase import create_client, Client

st.error(f"I currently see these secrets: {list(st.secrets.keys())}")

# ==========================================
# 1. PAGE CONFIGURATION & UI/UX STYLING
# ==========================================
st.set_page_config(page_title="CAR SIA 2026 Tracker", page_icon="💉", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    footer {visibility: hidden;}
    [data-testid="stMetric"] {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 15px 20px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        transition: transform 0.2s ease-in-out;
    }
    [data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 12px rgba(0,0,0,0.1);
    }
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        background-color: #f1f3f4;
        border-radius: 4px 4px 0px 0px;
        padding: 10px 20px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #ffffff;
        border-bottom: 2px solid #1E88E5;
        font-weight: bold;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. SUPABASE INITIALIZATION & SECRETS
# ==========================================
# Initialize Supabase client using Streamlit secrets
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

try:
    supabase = init_supabase()
except Exception as e:
    st.error("⚠️ Supabase Connection Error: Please ensure SUPABASE_URL and SUPABASE_KEY are set in Streamlit Secrets.")
    st.stop()

# Keep Google Sheets URL for the Sync feature
sheet_url = "https://docs.google.com/spreadsheets/d/1hM0yhzLY5uCh-bxFRPV7u6MYAzimfG0f4uluUGkLogU"

# ==========================================
# 3. SECURITY & SESSION STATE
# ==========================================
def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    return make_hashes(password) == hashed_text

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
    st.session_state['user_name'] = ""
    st.session_state['user_role'] = ""

# ==========================================
# 4. THE GATEWAY (Login, Registration & Recovery)
# ==========================================
if not st.session_state['logged_in']:
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.title("🔒 CAR SIA 2026")
        st.markdown("##### Secure Regional Command Center")
        st.divider()
        
        tab_login, tab_signup, tab_forgot = st.tabs(["🔑 Log In", "📝 Request Account", "❓ Forgot Password"])
        
        # --- LOGIN TAB ---
        with tab_login:
            with st.form("login_form"):
                input_username = st.text_input("Username").strip()
                input_password = st.text_input("Password", type="password")
                submit_login = st.form_submit_button("Log In", type="primary", use_container_width=True)
                
                if submit_login:
                    if not input_username or not input_password:
                        st.warning("Please enter both username and password.")
                    else:
                        try:
                            # Fetch user from Supabase
                            res = supabase.table('user_accounts').select('*').eq('username', input_username).execute()
                            user_records = res.data
                            
                            if user_records:
                                user_data = user_records[0]
                                stored_hash = user_data.get('password_hash', '')
                                account_status = user_data.get('account_status', 'Pending')
                                failed_attempts = user_data.get('failed_attempts', 0)
                                MAX_ATTEMPTS = 3
                                
                                if account_status == "Locked":
                                    st.error("🚨 Your account is locked due to too many failed login attempts. Please contact a System Admin to unlock it.")
                                elif account_status == "Approved":
                                    if check_hashes(input_password, stored_hash):
                                        # SUCCESS
                                        if failed_attempts > 0:
                                            supabase.table('user_accounts').update({'failed_attempts': 0}).eq('username', input_username).execute()
                                        
                                        db_name = user_data['name']
                                        db_role = user_data['role']
                                        
                                        # Log Access to Supabase
                                        manila_tz = pytz.timezone('Asia/Manila')
                                        current_time = datetime.now(manila_tz).strftime("%Y-%m-%d %I:%M:%S %p")
                                        supabase.table('access_logs').insert({
                                            'timestamp': current_time, 'name': db_name, 'role': db_role
                                        }).execute()
                                        
                                        st.session_state['logged_in'] = True
                                        st.session_state['user_name'] = db_name
                                        st.session_state['user_role'] = db_role
                                        
                                        st.toast(f"Welcome back, {db_name}!", icon="👋")
                                        time.sleep(1)
                                        st.rerun()
                                    else:
                                        # FAILED PASSWORD
                                        failed_attempts += 1
                                        if failed_attempts >= MAX_ATTEMPTS:
                                            supabase.table('user_accounts').update({'failed_attempts': failed_attempts, 'account_status': 'Locked'}).eq('username', input_username).execute()
                                            st.error("🚨 Maximum login attempts reached. Your account is now locked.")
                                        else:
                                            supabase.table('user_accounts').update({'failed_attempts': failed_attempts}).eq('username', input_username).execute()
                                            st.error(f"❌ Incorrect Password. Attempt {failed_attempts} of {MAX_ATTEMPTS}.")
                                
                                elif account_status == "Pending":
                                    st.warning("⏳ Your account request is still pending admin approval.")
                                elif account_status == "Pending Reset":
                                    st.warning("🔄 Your password reset request is pending admin approval.")
                                else:
                                    st.error("🚫 Your account access has been denied or revoked.")
                            else:
                                st.error("❌ Username not found.")
                        except Exception as e:
                            st.error(f"System Error: {e}")

        # --- SIGN UP TAB ---
        with tab_signup:
            with st.form("signup_form"):
                st.info("Submitted requests are reviewed by a System Admin before access is granted.")
                new_name = st.text_input("Full Name")
                new_role = st.selectbox("Designation / Role", ["DOH Regional Office", "Provincial Health Office", "Municipal Health Office", "Data Encoder", "Guest / Viewer"])
                new_contact = st.text_input("Official Contact (Email or Viber Number)", placeholder="Used for account verification")
                new_username = st.text_input("Desired Username").strip()
                new_password = st.text_input("Create Password", type="password")
                confirm_password = st.text_input("Confirm Password", type="password")
                
                submit_signup = st.form_submit_button("Submit Request", type="primary", use_container_width=True)
                
                if submit_signup:
                    if not all([new_name, new_role, new_contact, new_username, new_password, confirm_password]):
                        st.warning("Please fill out all fields.")
                    elif new_password != confirm_password:
                        st.error("Passwords do not match!")
                    else:
                        try:
                            res = supabase.table('user_accounts').select('username').eq('username', new_username).execute()
                            if res.data:
                                st.error("⚠️ That username is already taken. Please choose another.")
                            else:
                                hashed_pw = make_hashes(new_password)
                                supabase.table('user_accounts').insert({
                                    "username": new_username,
                                    "password_hash": hashed_pw,
                                    "name": new_name.strip(),
                                    "role": new_role,
                                    "account_status": "Pending",
                                    "contact_info": new_contact.strip(),
                                    "failed_attempts": 0 
                                }).execute()
                                st.success("✅ Request submitted! Please wait for admin approval.")
                        except Exception as e:
                            st.error(f"Registration Error: {e}")

        # --- FORGOT PASSWORD TAB ---
        with tab_forgot:
            with st.form("forgot_password_form"):
                st.info("An Admin must approve this reset before you can log in.")
                reset_username = st.text_input("Your Username").strip()
                reset_new_password = st.text_input("New Password", type="password")
                reset_confirm_password = st.text_input("Confirm New Password", type="password")
                
                submit_reset = st.form_submit_button("Request Password Reset", use_container_width=True)
                
                if submit_reset:
                    if not all([reset_username, reset_new_password, reset_confirm_password]):
                        st.warning("Please fill out all fields.")
                    elif reset_new_password != reset_confirm_password:
                        st.error("Passwords do not match!")
                    else:
                        try:
                            res = supabase.table('user_accounts').select('username').eq('username', reset_username).execute()
                            if res.data:
                                supabase.table('user_accounts').update({
                                    'password_hash': make_hashes(reset_new_password),
                                    'account_status': 'Pending Reset',
                                    'failed_attempts': 0
                                }).eq('username', reset_username).execute()
                                st.success("✅ Reset request sent! Your account is locked until Admin approval.")
                            else:
                                st.error("⚠️ Username not found.")
                        except Exception as e:
                            st.error(f"Reset Error: {e}")
    st.stop()

# ==========================================
# MAIN DASHBOARD CODE (Only runs if logged in)
# ==========================================
st.title("Cordillera Administrative Region (CAR) SIA 2026")

@st.cache_data(ttl="15s") # Faster load times
def get_last_updated_time():
    tz = pytz.timezone('Asia/Manila')
    return datetime.now(tz).strftime("%B %d, %Y | %I:%M %p")

last_updated = get_last_updated_time()

with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/e/e4/Department_of_Health_%28Philippines%29_Seal.svg/240px-Department_of_Health_%28Philippines%29_Seal.svg.png", width=80)
    st.header("Dashboard Controls")
    st.info(f"👤 **{st.session_state['user_name']}**\n\n*({st.session_state['user_role']})*")
    
    if st.button("🚪 Logout", use_container_width=True):
        st.session_state['logged_in'] = False
        st.session_state['user_name'] = ""
        st.session_state['user_role'] = ""
        st.rerun()
        
    st.divider()
    st.caption("🕒 **Last Interface Sync:**")
    st.caption(f"{last_updated}")
    
    if st.button("🔄 Refresh Interface", use_container_width=True):
        st.cache_data.clear()
        st.toast("Dashboard Interface Refreshed!", icon="🔄")
        time.sleep(0.5)
        st.rerun()
        
    st.divider()
    view_mode = st.radio("Select View Level:", ["Region-wide (Compare Provinces)", "Province-wide (Compare Municipalities)", "Specific Municipality (Compare Barangays)"])
    age_filter = st.selectbox("Select Age Group to Chart:", ["6 - 59 months (Grand Total)", "6 - 12 months", "13 - 23 months", "24 - 59 months"])

tab_names = ["🎯 Target Overview", "💉 MR Accomplishment", "💊 Vit A Accomplishment", "📊 Total Accomplishment"]
is_admin = st.session_state['user_role'] == "System Admin"

if is_admin:
    tab_names.append("🛡️ Admin Panel")

tabs = st.tabs(tab_names)
tab_target, tab_mr, tab_vita, tab_total = tabs[0], tabs[1], tabs[2], tabs[3]
if is_admin:
    tab_admin = tabs[4]

# Function to clean raw Sheets data before pushing to Supabase
def clean_and_process_car_data(df, col_names):
    df['Code'] = df['Code'].astype(str).str.split('.').str[0]
    df = df[df['Code'] != 'nan']
    df = df[df['Code'] != 'None']
    df = df[df['Code'] != '']
    numeric_cols = col_names[2:] 
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    df['Level'] = 'Barangay'
    df.loc[df['Code'].str.endswith('00000000'), 'Level'] = 'Region'
    df.loc[(df['Code'].str.endswith('00000')) & (~df['Code'].str.endswith('00000000')), 'Level'] = 'Province'
    df.loc[(df['Code'].str.endswith('000')) & (~df['Code'].str.endswith('00000')), 'Level'] = 'Municipality'
    df['Parent_Province'] = df.apply(lambda row: row['Location'] if row['Level'] == 'Province' else np.nan, axis=1).ffill()
    df['Parent_Municipality'] = df.apply(lambda row: row['Location'] if row['Level'] == 'Municipality' else np.nan, axis=1).ffill()
    return df

# Fetch fast data from Supabase Database
@st.cache_data(ttl="15s")
def fetch_targets_from_supabase():
    res = supabase.table('targets').select('*').execute()
    if not res.data:
        return pd.DataFrame()
    df = pd.DataFrame(res.data)
    # Map back to UI friendly column names
    col_mapping = {
        'code': 'Code', 'location': 'Location', 'level': 'Level',
        'parent_province': 'Parent_Province', 'parent_municipality': 'Parent_Municipality',
        'grand_total_6_59m': '6-59m_Total', 'grand_total_6_12m': '6-12m_Total',
        'grand_total_13_23m': '13-23m_Total', 'grand_total_24_59m': '24-59m_Total'
    }
    return df.rename(columns=col_mapping)

try:
    with tab_target:
        st.markdown("### Regional Target Baseline Overview")
        df_targets = fetch_targets_from_supabase()
        
        if df_targets.empty:
            st.warning("⚠️ The Targets Database is empty. Please ask a System Admin to sync the database from Google Sheets.")
        else:
            col_map = {"6 - 59 months (Grand Total)": "6-59m_Total", "6 - 12 months": "6-12m_Total", "13 - 23 months": "13-23m_Total", "24 - 59 months": "24-59m_Total"}
            target_col = col_map[age_filter]

            df_view = pd.DataFrame()
            chart_title = ""
            selected_prov = "CAR_Region" # Default export name
            selected_muni = ""
            
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
            else: 
                province_list = df_targets[df_targets['Level'] == 'Province']['Location'].unique().tolist()
                default_prov_idx = province_list.index("Abra") if "Abra" in province_list else 0
                with st.sidebar:
                    selected_prov = st.selectbox("Select Province:", province_list, index=default_prov_idx)
                    muni_list = df_targets[(df_targets['Level'] == 'Municipality') & (df_targets['Parent_Province'] == selected_prov)]['Location'].unique().tolist()
                    default_muni_idx = muni_list.index("Manabo") if "Manabo" in muni_list else 0
                    selected_muni = st.selectbox("Select Municipality:", muni_list, index=default_muni_idx)
                df_view = df_targets[(df_targets['Level'] == 'Barangay') & (df_targets['Parent_Municipality'] == selected_muni)]
                chart_title = f"Barangay Targets for {selected_muni}, {selected_prov}: {age_filter}"

            kpi1, kpi2, kpi3, kpi4 = st.columns(4)
            kpi1.metric("6 - 59 months", f"{df_view['6-59m_Total'].sum():,.0f}")
            kpi2.metric("6 - 12 months", f"{df_view['6-12m_Total'].sum():,.0f}")
            kpi3.metric("13 - 23 months", f"{df_view['13-23m_Total'].sum():,.0f}")
            kpi4.metric("24 - 59 months", f"{df_view['24-59m_Total'].sum():,.0f}")
            st.write("") 

            if not df_view.empty:
                col_chart1, col_chart2 = st.columns([7, 3])
                with col_chart1:
                    df_sorted = df_view.sort_values(target_col, ascending=True) 
                    fig_bar = px.bar(df_sorted, x=target_col, y='Location', orientation='h', title=chart_title, text_auto='.0f', color_discrete_sequence=['#1E88E5'])
                    fig_bar.update_layout(xaxis_title="Eligible Children", yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=500, margin=dict(l=0, r=0, t=40, b=0))
                    st.plotly_chart(fig_bar, use_container_width=True)
                with col_chart2:
                    age_data = pd.DataFrame({'Age Group': ['6-12m', '13-23m', '24-59m'], 'Target': [df_view['6-12m_Total'].sum(), df_view['13-23m_Total'].sum(), df_view['24-59m_Total'].sum()]})
                    fig_donut = px.pie(age_data, names='Age Group', values='Target', hole=0.4, title="Age Distribution", color_discrete_sequence=['#43A047', '#FFB300', '#E53935'])
                    fig_donut.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), height=500, margin=dict(l=0, r=0, t=40, b=0))
                    st.plotly_chart(fig_donut, use_container_width=True)
            else:
                st.warning("No data available.")

            with st.expander("📂 View & Download Target Database"):
                display_df = df_view[['Code', 'Location', 'Level', 'Parent_Province', 'Parent_Municipality', '6-59m_Total', '6-12m_Total', '13-23m_Total', '24-59m_Total']]
                st.dataframe(display_df, use_container_width=True, hide_index=True)
                
                export_location = "CAR_Region" if view_mode == "Region-wide (Compare Provinces)" else selected_prov if view_mode == "Province-wide (Compare Municipalities)" else selected_muni
                
                csv = display_df.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Download Data as CSV", data=csv, file_name=f"SIA_Targets_{export_location}_{datetime.now().strftime('%Y%m%d')}.csv", mime="text/csv", type="primary")

    with tab_mr:
        st.info("🚧 Dashboard framework ready. Awaiting VaccTrack MR export file.")
    with tab_vita:
        st.info("🚧 Dashboard framework ready. Awaiting VaccTrack Vitamin A export file.")
    with tab_total:
        st.info("🚧 Executive Summary will populate once data streams are connected.")

    # ==========================================
    # SECRET ADMIN PANEL
    # ==========================================
    if is_admin:
        with tab_admin:
            # --- THE NEW SUPABASE DATA BRIDGE ---
            st.markdown("### 🔄 Master Database Sync")
            st.write("Pull the latest targets directly from the DOH Google Sheet into the high-speed Supabase Engine.")
            
            if st.button("Sync Targets Database", type="primary", use_container_width=True):
                with st.spinner("Downloading from Google Sheets and writing to Supabase..."):
                    try:
                        conn = st.connection("gsheets", type=GSheetsConnection)
                        col_names = ["Code", "Location", "6-59m_Male", "6-59m_Female", "6-59m_Total", "6-12m_Male", "6-12m_Female", "6-12m_Total", "13-23m_Male", "13-23m_Female", "13-23m_Total", "24-59m_Male", "24-59m_Female", "24-59m_Total"]
                        df_raw = conn.read(spreadsheet=sheet_url, worksheet="Target(CAR)", usecols=list(range(14)), skiprows=2, names=col_names, ttl=0)
                        
                        df_clean = clean_and_process_car_data(df_raw, col_names)
                        df_supabase = df_clean[['Code', 'Location', 'Level', 'Parent_Province', 'Parent_Municipality', '6-59m_Total', '6-12m_Total', '13-23m_Total', '24-59m_Total']].copy()
                        df_supabase.columns = ['code', 'location', 'level', 'parent_province', 'parent_municipality', 'grand_total_6_59m', 'grand_total_6_12m', 'grand_total_13_23m', 'grand_total_24_59m']
                        
                        # Upsert to Supabase
                        data_to_push = df_supabase.to_dict(orient='records')
                        supabase.table('targets').upsert(data_to_push).execute()
                        
                        st.success("✅ Target Database Successfully Synced!")
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"Sync Failed: {e}")
            
            st.divider()
            
            # User Management (Supabase logic)
            st.markdown("### 🔐 User Account Management")
            
            res_users = supabase.table('user_accounts').select('*').execute()
            if res_users.data:
                users_admin_df = pd.DataFrame(res_users.data)
                users_admin_df['contact_info'] = users_admin_df['contact_info'].fillna("").astype(str)
                
                # Order columns logically
                cols = ['username', 'name', 'role', 'account_status', 'contact_info', 'failed_attempts', 'password_hash']
                users_admin_df = users_admin_df[cols]
                
                edited_users = st.data_editor(
                    users_admin_df,
                    column_config={
                        "account_status": st.column_config.SelectboxColumn("Account Status", width="medium", options=["Approved", "Pending", "Pending Reset", "Locked", "Denied", "Revoked"], required=True),
                        "password_hash": None, # Hide Hash
                        "username": st.column_config.TextColumn("Username", disabled=True),
                        "contact_info": st.column_config.TextColumn("Contact Info", width="medium"),
                        "failed_attempts": st.column_config.NumberColumn("Strikes", width="small", disabled=True) 
                    },
                    use_container_width=True,
                    num_rows="dynamic",
                    key="user_editor"
                )
                
                if st.button("💾 Save User Changes", type="secondary"):
                    try:
                        edited_users.loc[edited_users['account_status'] == 'Approved', 'failed_attempts'] = 0
                        updated_records = edited_users.to_dict(orient='records')
                        supabase.table('user_accounts').upsert(updated_records).execute()
                        st.toast("User accounts updated successfully!", icon="✅")
                    except Exception as e:
                        st.error(f"Failed to update users: {e}")
            
            st.divider()

            # Access Logs (Supabase logic)
            st.markdown("### 📋 System Access Logs")
            try:
                res_logs = supabase.table('access_logs').select('*').order('id', desc=True).limit(100).execute()
                if res_logs.data:
                    logs_df = pd.DataFrame(res_logs.data)[['timestamp', 'name', 'role']]
                    st.dataframe(logs_df, use_container_width=True)
                else:
                    st.info("No access logs found yet.")
            except Exception as e:
                st.warning(f"Could not load Access Logs: {e}")

except Exception as e:
    st.error(f"Dashboard Error: {e}")
