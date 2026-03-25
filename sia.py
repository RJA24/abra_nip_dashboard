import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import numpy as np
import plotly.express as px
from datetime import datetime
import pytz
import time
import hashlib

# 1. Page Configuration
st.set_page_config(page_title="CAR SIA 2026 Tracker", page_icon="💉", layout="wide")

# 2. Security Functions
def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    if make_hashes(password) == hashed_text:
        return True
    return False

# 3. Setup Session State
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
    st.session_state['user_name'] = ""
    st.session_state['user_role'] = ""

sheet_url = "https://docs.google.com/spreadsheets/d/1hM0yhzLY5uCh-bxFRPV7u6MYAzimfG0f4uluUGkLogU"

# ==========================================
# 4. THE GATEWAY (Login, Registration & Recovery)
# ==========================================
if not st.session_state['logged_in']:
    st.title("🔒 CAR SIA 2026 Tracker - Secure Access")
    
    tab_login, tab_signup, tab_forgot = st.tabs(["🔑 Log In", "📝 Request Account", "❓ Forgot Password"])
    
    # --- LOGIN TAB ---
    with tab_login:
        with st.form("login_form"):
            st.markdown("### Access Command Center")
            input_username = st.text_input("Username")
            input_password = st.text_input("Password", type="password")
            submit_login = st.form_submit_button("Log In", type="primary")
            
            if submit_login:
                if input_username.strip() == "" or input_password.strip() == "":
                    st.warning("Please enter both username and password.")
                else:
                    try:
                        conn = st.connection("gsheets", type=GSheetsConnection)
                        users_df = conn.read(spreadsheet=sheet_url, worksheet="User_Accounts", ttl=0)
                        users_df['Username'] = users_df['Username'].astype(str).str.strip()
                        
                        user_record = users_df[users_df['Username'] == input_username.strip()]
                        
                        if not user_record.empty:
                            stored_hash = str(user_record.iloc[0]['Password_Hash']).strip()
                            account_status = str(user_record.iloc[0].get('Account_Status', 'Pending')).strip()
                            
                            if check_hashes(input_password, stored_hash):
                                if account_status == "Approved":
                                    db_name = user_record.iloc[0]['Name']
                                    db_role = user_record.iloc[0]['Role']
                                    
                                    existing_logs = conn.read(spreadsheet=sheet_url, worksheet="Access_Logs", ttl=0)
                                    manila_tz = pytz.timezone('Asia/Manila')
                                    current_time = datetime.now(manila_tz).strftime("%Y-%m-%d %I:%M:%S %p")
                                    new_log = pd.DataFrame([{"Timestamp": current_time, "Name": db_name, "Role": db_role}])
                                    updated_logs = pd.concat([existing_logs, new_log], ignore_index=True)
                                    conn.update(spreadsheet=sheet_url, worksheet="Access_Logs", data=updated_logs)
                                    
                                    st.session_state['logged_in'] = True
                                    st.session_state['user_name'] = db_name
                                    st.session_state['user_role'] = db_role
                                    st.success("Authentication Successful! Loading...")
                                    time.sleep(1)
                                    st.rerun()
                                    
                                elif account_status == "Pending":
                                    st.warning("⏳ Your account request is still pending admin approval.")
                                elif account_status == "Pending Reset":
                                    st.warning("🔄 Your password reset request is pending admin approval.")
                                else:
                                    st.error("🚫 Your account access has been denied or revoked.")
                            else:
                                st.error("❌ Incorrect Password.")
                        else:
                            st.error("❌ Username not found.")
                    except Exception as e:
                        st.error(f"System Error: {e}")

    # --- SIGN UP TAB ---
    with tab_signup:
        with st.form("signup_form"):
            st.markdown("### Request Access")
            st.info("Submitted requests will be reviewed by a System Admin before access is granted.")
            
            new_name = st.text_input("Full Name")
            new_role = st.selectbox("Designation / Role", ["DOH Regional Office", "Provincial Health Office", "Municipal Health Office", "Data Encoder", "Guest / Viewer"])
            new_contact = st.text_input("Official Contact (Email or Viber Number)", placeholder="Used for account verification")
            new_username = st.text_input("Desired Username")
            new_password = st.text_input("Create Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            
            submit_signup = st.form_submit_button("Submit Request", type="primary")
            
            if submit_signup:
                if not all([new_name, new_role, new_contact, new_username, new_password, confirm_password]):
                    st.warning("Please fill out all fields.")
                elif new_password != confirm_password:
                    st.error("Passwords do not match!")
                else:
                    try:
                        conn = st.connection("gsheets", type=GSheetsConnection)
                        users_df = conn.read(spreadsheet=sheet_url, worksheet="User_Accounts", ttl=0)
                        
                        if new_username.strip() in users_df['Username'].astype(str).str.strip().values:
                            st.error("⚠️ That username is already taken. Please choose another.")
                        else:
                            hashed_pw = make_hashes(new_password)
                            new_account = pd.DataFrame([{
                                "Username": new_username.strip(),
                                "Password_Hash": hashed_pw,
                                "Name": new_name.strip(),
                                "Role": new_role,
                                "Account_Status": "Pending",
                                "Contact_Info": new_contact.strip()
                            }])
                            
                            updated_users = pd.concat([users_df, new_account], ignore_index=True)
                            conn.update(spreadsheet=sheet_url, worksheet="User_Accounts", data=updated_users)
                            st.success("✅ Request submitted successfully! Please wait for admin approval.")
                    except Exception as e:
                        st.error(f"Registration Error: {e}")

    # --- FORGOT PASSWORD TAB ---
    with tab_forgot:
        with st.form("forgot_password_form"):
            st.markdown("### Password Reset Request")
            st.info("Submit your username and a new password. An Admin must approve this reset before you can log in.")
            
            reset_username = st.text_input("Your Username")
            reset_new_password = st.text_input("New Password", type="password")
            reset_confirm_password = st.text_input("Confirm New Password", type="password")
            
            submit_reset = st.form_submit_button("Request Password Reset")
            
            if submit_reset:
                if not all([reset_username, reset_new_password, reset_confirm_password]):
                    st.warning("Please fill out all fields.")
                elif reset_new_password != reset_confirm_password:
                    st.error("Passwords do not match!")
                else:
                    try:
                        conn = st.connection("gsheets", type=GSheetsConnection)
                        users_df = conn.read(spreadsheet=sheet_url, worksheet="User_Accounts", ttl=0)
                        users_df['Username'] = users_df['Username'].astype(str).str.strip()
                        
                        user_idx = users_df.index[users_df['Username'] == reset_username.strip()].tolist()
                        
                        if user_idx:
                            idx = user_idx[0]
                            users_df.at[idx, 'Password_Hash'] = make_hashes(reset_new_password)
                            users_df.at[idx, 'Account_Status'] = "Pending Reset"
                            
                            conn.update(spreadsheet=sheet_url, worksheet="User_Accounts", data=users_df)
                            st.success("✅ Reset request sent! Your account is temporarily locked until Admin approval.")
                        else:
                            st.error("⚠️ Username not found.")
                    except Exception as e:
                        st.error(f"Reset Error: {e}")

    st.stop()

# ==========================================
# MAIN DASHBOARD CODE (Only runs if logged in)
# ==========================================

st.title("Cordillera Administrative Region (CAR) SIA 2026")

@st.cache_data(ttl="10m")
def get_last_updated_time():
    tz = pytz.timezone('Asia/Manila')
    return datetime.now(tz).strftime("%B %d, %Y | %I:%M %p")

last_updated = get_last_updated_time()

with st.sidebar:
    st.header("⚙️ Dashboard Controls")
    st.success(f"👤 **{st.session_state['user_name']}**\n\n*({st.session_state['user_role']})*")
    
    if st.button("🚪 Logout", use_container_width=True):
        st.session_state['logged_in'] = False
        st.session_state['user_name'] = ""
        st.session_state['user_role'] = ""
        st.rerun()
        
    st.divider()
    st.caption(f"🕒 Last Data Sync: {last_updated}")
    
    if st.button("🔄 Force Refresh Data", use_container_width=True):
        st.cache_data.clear()
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

try:
    conn = st.connection("gsheets", type=GSheetsConnection)
    col_names = ["Code", "Location", "6-59m_Male", "6-59m_Female", "6-59m_Total", "6-12m_Male", "6-12m_Female", "6-12m_Total", "13-23m_Male", "13-23m_Female", "13-23m_Total", "24-59m_Male", "24-59m_Female", "24-59m_Total"]
    
    with tab_target:
        st.markdown("### Regional Target Baseline Overview")
        df_targets_raw = conn.read(spreadsheet=sheet_url, worksheet="Target(CAR)", usecols=list(range(14)), skiprows=2, names=col_names, ttl="10m")
        df_targets = clean_and_process_car_data(df_targets_raw, col_names)
        
        col_map = {"6 - 59 months (Grand Total)": "6-59m_Total", "6 - 12 months": "6-12m_Total", "13 - 23 months": "13-23m_Total", "24 - 59 months": "24-59m_Total"}
        target_col = col_map[age_filter]

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
        st.divider()

        if not df_view.empty:
            col_chart1, col_chart2 = st.columns([7, 3])
            with col_chart1:
                df_sorted = df_view.sort_values(target_col, ascending=True) 
                fig_bar = px.bar(df_sorted, x=target_col, y='Location', orientation='h', title=chart_title, text_auto='.0f', color_discrete_sequence=['#1E88E5'])
                fig_bar.update_layout(xaxis_title="Eligible Children", yaxis_title="", plot_bgcolor='rgba(0,0,0,0)', height=500)
                st.plotly_chart(fig_bar, use_container_width=True)
            with col_chart2:
                age_data = pd.DataFrame({'Age Group': ['6-12m', '13-23m', '24-59m'], 'Target': [df_view['6-12m_Total'].sum(), df_view['13-23m_Total'].sum(), df_view['24-59m_Total'].sum()]})
                fig_donut = px.pie(age_data, names='Age Group', values='Target', hole=0.4, title="Age Distribution", color_discrete_sequence=['#43A047', '#FFB300', '#E53935'])
                fig_donut.update_layout(legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5), height=500)
                st.plotly_chart(fig_donut, use_container_width=True)
        else:
            st.warning("No data available.")

        with st.expander("View Cleaned Regional Target Database"):
            st.dataframe(df_targets[['Code', 'Location', 'Level', 'Parent_Province', 'Parent_Municipality', '6-59m_Total', '6-12m_Total', '13-23m_Total', '24-59m_Total']], use_container_width=True, hide_index=True)

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
            st.markdown("### 🔐 User Account Management")
            st.write("Approve or deny pending access and password reset requests. Use the Contact Info to verify user identities out-of-band.")
            
            users_admin_df = conn.read(spreadsheet=sheet_url, worksheet="User_Accounts", ttl=0)
            
            # --- THE FIX: Force the column to be text so Streamlit doesn't crash ---
            if 'Contact_Info' not in users_admin_df.columns:
                users_admin_df['Contact_Info'] = ""
            users_admin_df['Contact_Info'] = users_admin_df['Contact_Info'].fillna("").astype(str)
            # ------------------------------------------------------------------------
            
            edited_users = st.data_editor(
                users_admin_df,
                column_config={
                }
# ... (the rest of your column config stays exactly the same) ...# ==========================================
    # SECRET ADMIN PANEL
    # ==========================================
    if is_admin:
        with tab_admin:
            st.markdown("### 🔐 User Account Management")
            st.write("Approve or deny pending access and password reset requests. Use the Contact Info to verify user identities out-of-band.")
            
            users_admin_df = conn.read(spreadsheet=sheet_url, worksheet="User_Accounts", ttl=0)
            
            # --- THE FIX: Force the column to be text so Streamlit doesn't crash ---
            if 'Contact_Info' not in users_admin_df.columns:
                users_admin_df['Contact_Info'] = ""
            users_admin_df['Contact_Info'] = users_admin_df['Contact_Info'].fillna("").astype(str)
            # ------------------------------------------------------------------------
            
            edited_users = st.data_editor(
                users_admin_df,
                column_config={
# ... (the rest of your column config stays exactly the same) ...
                    "Account_Status": st.column_config.SelectboxColumn(
                        "Account Status",
                        help="Select the approval status",
                        width="medium",
                        options=["Approved", "Pending", "Pending Reset", "Denied", "Revoked"],
                        required=True,
                    ),
                    "Password_Hash": None, # Hide the hash column
                    "Contact_Info": st.column_config.TextColumn("Contact Info", width="medium") # Display the new contact column
                },
                use_container_width=True,
                num_rows="dynamic"
            )
            
            if st.button("💾 Save User Changes", type="primary"):
                try:
                    conn.update(spreadsheet=sheet_url, worksheet="User_Accounts", data=edited_users)
                    st.success("User accounts updated successfully!")
                    st.cache_data.clear()
                except Exception as e:
                    st.error(f"Failed to update database: {e}")

            st.divider()

            st.markdown("### 📋 System Access Logs")
            st.write("Real-time audit trail of all successful logins.")
            
            try:
                logs_df = conn.read(spreadsheet=sheet_url, worksheet="Access_Logs", ttl=0)
                if not logs_df.empty:
                    logs_df = logs_df.sort_values(by="Timestamp", ascending=False).reset_index(drop=True)
                    st.dataframe(logs_df, use_container_width=True)
                else:
                    st.info("No access logs found yet.")
            except Exception as e:
                st.warning(f"Could not load Access Logs: {e}")

except Exception as e:
    st.error(f"Error loading data: {e}")
