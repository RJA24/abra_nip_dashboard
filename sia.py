"""Compatibility entry point.

The canonical Streamlit entry point is now app.py. Keep this file temporarily
so existing Streamlit Cloud deployments configured for sia.py continue to run.
"""

from app import *  # noqa: F401,F403
