"""Skeleton loading states for database-querying pages."""

import streamlit as st

_SKELETON_CSS = """
<style>
@keyframes skeleton-pulse {
    0% { opacity: 0.4; }
    50% { opacity: 0.7; }
    100% { opacity: 0.4; }
}
.skeleton-card {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 16px;
    animation: skeleton-pulse 1.5s ease-in-out infinite;
    margin-bottom: 12px;
}
.skeleton-line {
    background: #334155;
    border-radius: 4px;
    animation: skeleton-pulse 1.5s ease-in-out infinite;
    margin-bottom: 8px;
}
</style>
"""

_css_injected = False


def _inject_css():
    global _css_injected
    if not _css_injected:
        st.markdown(_SKELETON_CSS, unsafe_allow_html=True)
        _css_injected = True


def render_skeleton_card(height: int = 120):
    """Render a placeholder card skeleton."""
    _inject_css()
    st.markdown(
        f'<div class="skeleton-card" style="min-height:{height}px;"></div>',
        unsafe_allow_html=True,
    )


def render_skeleton_metric_row(count: int = 4):
    """Render placeholder metric cards in a row."""
    _inject_css()
    cols = st.columns(count)
    for col in cols:
        with col:
            st.markdown(
                '<div class="skeleton-line" style="width:60%; height:12px; margin-bottom:6px;"></div>'
                '<div class="skeleton-line" style="width:40%; height:24px;"></div>',
                unsafe_allow_html=True,
            )
