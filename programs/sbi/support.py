from __future__ import annotations

from datetime import datetime

import pytz
import streamlit as st


MANILA_TZ = pytz.timezone("Asia/Manila")
FEEDBACK_TABLE = "sbi_user_feedback"
APP_VERSION = "v5.20.1"


def feedback_schema_available(supabase) -> bool:
    try:
        supabase.table(FEEDBACK_TABLE).select("id").limit(1).execute()
        return True
    except Exception:
        return False


def render_feedback_form(
    supabase,
    municipality: str,
    username: str,
    role: str = "RHU Encoder",
) -> None:
    with st.expander("Send Feedback / Report a Problem", expanded=False):
        st.caption(
            "Use this while testing the dashboard. Please do not include learner names, LRN, or other identifying information."
        )

        if not feedback_schema_available(supabase):
            st.info("Feedback collection will be available after the v5.20 database update is applied.")
            return

        with st.form("sbi_feedback_form", clear_on_submit=True):
            category = st.selectbox(
                "What is this about?",
                [
                    "Upload / Validation",
                    "Follow-up Activity",
                    "Correction / Revision",
                    "VaccTrack Encoding",
                    "VaccTrack Check",
                    "Guide / Instructions",
                    "Login / Account",
                    "Performance / Mobile View",
                    "Suggestion",
                    "Other",
                ],
            )
            page = st.selectbox(
                "Where did you encounter it?",
                [
                    "SBI - Upload Learner Records",
                    "SBI - VaccTrack Encoding",
                    "SBI - VaccTrack Check",
                    "SBI - Corrections / History",
                    "SBI - Training / Practice",
                    "Main Menu / Account",
                    "Other",
                ],
            )
            message = st.text_area(
                "Describe what happened or what you suggest",
                height=130,
                placeholder="Example: I uploaded a follow-up file and I was unsure which status to use for MR.",
            )
            submit = st.form_submit_button("Send Feedback", type="primary", width="stretch")

        if not submit:
            return

        clean_message = message.strip()
        if len(clean_message) < 8:
            st.error("Please add a little more detail before sending.")
            return

        try:
            supabase.table(FEEDBACK_TABLE).insert(
                {
                    "submitted_at": datetime.now(MANILA_TZ).isoformat(),
                    "username": username or None,
                    "role": role or None,
                    "municipality": municipality or None,
                    "category": category,
                    "page": page,
                    "app_version": APP_VERSION,
                    "message": clean_message,
                    "status": "Open",
                }
            ).execute()
            st.success("Thank you. Your feedback was sent to the NIP dashboard administrator.")
        except Exception:
            st.error("Your feedback could not be sent right now. Please try again later.")
