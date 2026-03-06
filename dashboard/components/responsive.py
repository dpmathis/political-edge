"""Responsive CSS media queries for tablet/mobile support."""

import streamlit as st

_RESPONSIVE_CSS = """
<style>
/* ── Tablet (768px - 1024px) ─────────────────────────── */
@media (max-width: 1024px) {
    /* Reduce large font sizes in custom HTML cards */
    div[data-testid="stMarkdownContainer"] div[style*="font-size:48px"] {
        font-size: 32px !important;
    }
    div[data-testid="stMarkdownContainer"] div[style*="font-size:64px"] {
        font-size: 40px !important;
    }
    div[data-testid="stMarkdownContainer"] div[style*="font-size:28px"] {
        font-size: 22px !important;
    }
}

/* ── Mobile (< 768px) ────────────────────────────────── */
@media (max-width: 768px) {
    /* Stack flex layouts vertically */
    div[data-testid="stMarkdownContainer"] div[style*="display:flex"] {
        flex-direction: column !important;
        gap: 8px !important;
    }
    /* Reduce card padding */
    div[data-testid="stMarkdownContainer"] div[style*="padding:24px"] {
        padding: 12px !important;
    }
    div[data-testid="stMarkdownContainer"] div[style*="padding:16px"] {
        padding: 10px !important;
    }
    /* Auto min-height on cards */
    div[data-testid="stMarkdownContainer"] div[style*="min-height:180px"] {
        min-height: auto !important;
    }
    /* Scale down banner text */
    div[data-testid="stMarkdownContainer"] div[style*="font-size:48px"] {
        font-size: 24px !important;
    }
    div[data-testid="stMarkdownContainer"] div[style*="font-size:64px"] {
        font-size: 32px !important;
    }
    div[data-testid="stMarkdownContainer"] div[style*="font-size:28px"] {
        font-size: 18px !important;
    }
    div[data-testid="stMarkdownContainer"] div[style*="font-size:24px"] {
        font-size: 16px !important;
    }
    div[data-testid="stMarkdownContainer"] div[style*="font-size:20px"] {
        font-size: 16px !important;
    }
}

/* ── General responsiveness ──────────────────────────── */
/* Prevent horizontal overflow in card text */
div[data-testid="stMarkdownContainer"] div {
    overflow-wrap: break-word;
    word-wrap: break-word;
}
/* Horizontal scroll on dataframes for small screens */
div[data-testid="stDataFrame"] {
    max-width: 100%;
    overflow-x: auto;
}
</style>
"""

_injected = False


def inject_responsive_css():
    """Inject responsive CSS media queries. Call once in the main app."""
    global _injected
    if not _injected:
        st.markdown(_RESPONSIVE_CSS, unsafe_allow_html=True)
        _injected = True
