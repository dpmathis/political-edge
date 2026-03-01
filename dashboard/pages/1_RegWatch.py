"""RegWatch — Regulatory event feed with sector mapping and price overlays."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
import pandas as pd
import plotly.express as px
import streamlit as st

from config import DB_PATH
from dashboard.components.filters import render_sidebar_filters
from dashboard.components.event_card import (
    render_impact_badge,
    format_event_type,
    TRADE_ACTION_OPTIONS,
)
from dashboard.components.price_chart import render_price_chart

st.title("RegWatch")
st.caption("Regulatory & political event feed with sector mapping")

from dashboard.components.freshness import render_freshness
render_freshness("regulatory_events", "publication_date", "Regulatory Events")

# Sidebar filters
filters = render_sidebar_filters()


@st.cache_data(ttl=300)
def load_events(start_date: str, end_date: str) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """SELECT id, source, event_type, title, summary, agency,
                  publication_date, effective_date, comment_deadline,
                  url, sectors, tickers, impact_score, user_notes, trade_action
           FROM regulatory_events
           WHERE publication_date >= ? AND publication_date <= ?
           ORDER BY publication_date DESC""",
        conn,
        params=(start_date, end_date),
    )
    conn.close()
    return df


# Load data
events_df = load_events(filters["start_date"], filters["end_date"])

# Apply filters
if filters["sectors"]:
    mask = events_df["sectors"].fillna("").apply(
        lambda s: any(sec in s for sec in filters["sectors"])
    )
    events_df = events_df[mask]

if filters["event_types"]:
    events_df = events_df[events_df["event_type"].isin(filters["event_types"])]

min_impact, max_impact = filters["impact_range"]
events_df = events_df[
    (events_df["impact_score"] >= min_impact) & (events_df["impact_score"] <= max_impact)
]

if filters["tickers"]:
    mask = events_df["tickers"].fillna("").apply(
        lambda t: any(ticker in t for ticker in filters["tickers"])
    )
    events_df = events_df[mask]

# --- KPI ROW ---
st.markdown("---")
kpi_cols = st.columns(4)
with kpi_cols[0]:
    st.metric("Total Events", len(events_df))
with kpi_cols[1]:
    high_impact = len(events_df[events_df["impact_score"] >= 4])
    st.metric("High Impact (4+)", high_impact)
with kpi_cols[2]:
    unique_sectors = set()
    for s in events_df["sectors"].dropna():
        for sector in s.split(","):
            if sector.strip():
                unique_sectors.add(sector.strip())
    st.metric("Sectors Affected", len(unique_sectors))
with kpi_cols[3]:
    unique_tickers = set()
    for t in events_df["tickers"].dropna():
        for ticker in t.split(","):
            if ticker.strip():
                unique_tickers.add(ticker.strip())
    st.metric("Tickers Affected", len(unique_tickers))

# --- SECTOR BREAKDOWN CHART ---
if not events_df.empty:
    sector_counts = {}
    for s in events_df["sectors"].dropna():
        for sector in s.split(","):
            sector = sector.strip()
            if sector:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1

    if sector_counts:
        sector_df = pd.DataFrame(
            sorted(sector_counts.items(), key=lambda x: x[1], reverse=True),
            columns=["Sector", "Count"],
        )
        fig = px.bar(
            sector_df,
            x="Sector",
            y="Count",
            title="Events by Sector",
            color="Count",
            color_continuous_scale="Blues",
        )
        fig.update_layout(height=300, margin=dict(l=40, r=40, t=50, b=40))
        st.plotly_chart(fig, use_container_width=True)

# --- EVENT TABLE ---
st.markdown("---")
st.subheader(f"Regulatory Events ({len(events_df)})")

if events_df.empty:
    st.info("No events match your filters. Try widening the date range or removing filters.")
else:
    # Display table
    display_df = events_df[
        ["publication_date", "event_type", "agency", "title", "sectors", "tickers", "impact_score", "trade_action"]
    ].copy()
    display_df["event_type"] = display_df["event_type"].apply(format_event_type)
    display_df["title"] = display_df["title"].apply(lambda x: str(x)[:100] if x else "")
    display_df.columns = ["Date", "Type", "Agency", "Title", "Sectors", "Tickers", "Impact", "Action"]

    # Sortable table
    st.dataframe(
        display_df,
        use_container_width=True,
        height=400,
        column_config={
            "Impact": st.column_config.NumberColumn(format="%d/5"),
        },
    )

    # --- EVENT DETAIL EXPANDER ---
    st.markdown("---")
    st.subheader("Event Detail")

    event_options = [
        f"{row['publication_date']} | {str(row.get('agency', ''))[:30]} | {str(row.get('title', ''))[:60]}..."
        for _, row in events_df.head(50).iterrows()
    ]

    if event_options:
        selected_idx = st.selectbox(
            "Select an event to view details",
            range(len(event_options)),
            format_func=lambda i: event_options[i],
        )

        event = events_df.iloc[selected_idx]

        col_left, col_right = st.columns([2, 1])

        with col_left:
            st.markdown(f"### {event['title']}")
            st.markdown(f"**Type:** {format_event_type(event['event_type'])} | **Agency:** {event['agency']}")
            st.markdown(f"**Published:** {event['publication_date']} | **Impact:** {render_impact_badge(event['impact_score'])}")

            if event["effective_date"]:
                st.markdown(f"**Effective Date:** {event['effective_date']}")
            if event["comment_deadline"]:
                st.markdown(f"**Comment Deadline:** {event['comment_deadline']}")

            st.markdown(f"**Sectors:** {event['sectors'] or 'None detected'}")
            st.markdown(f"**Tickers:** {event['tickers'] or 'None detected'}")

            if event["summary"]:
                with st.expander("Full Summary"):
                    st.write(event["summary"])

            if event["url"]:
                st.markdown(f"[View Source Document]({event['url']})")

        with col_right:
            st.markdown("#### Your Analysis")

            # Trade action selector
            current_action = event["trade_action"] or "none"
            action_idx = TRADE_ACTION_OPTIONS.index(current_action) if current_action in TRADE_ACTION_OPTIONS else 0
            new_action = st.selectbox(
                "Trade Action",
                TRADE_ACTION_OPTIONS,
                index=action_idx,
                key=f"action_{event['id']}",
            )

            # User notes
            notes = st.text_area(
                "Notes",
                value=event["user_notes"] or "",
                key=f"notes_{event['id']}",
                height=150,
            )

            if st.button("Save", key=f"save_{event['id']}"):
                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    """UPDATE regulatory_events
                       SET trade_action = ?, user_notes = ?, updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (new_action, notes, event["id"]),
                )
                conn.commit()
                conn.close()
                st.success("Saved!")
                st.cache_data.clear()

    # --- PRICE CHART ---
    st.markdown("---")
    st.subheader("Price Chart with Event Overlay")

    # Get tickers from the current filtered events
    all_tickers = set()
    for t in events_df["tickers"].dropna():
        for ticker in t.split(","):
            if ticker.strip():
                all_tickers.add(ticker.strip())

    # Also include watchlist tickers
    conn = sqlite3.connect(DB_PATH)
    watchlist_tickers = [
        r[0] for r in conn.execute("SELECT ticker FROM watchlist WHERE active = 1 ORDER BY ticker").fetchall()
    ]
    conn.close()

    chart_tickers = sorted(all_tickers.union(set(watchlist_tickers)))

    if chart_tickers:
        selected_ticker = st.selectbox("Select Ticker", chart_tickers)
        if selected_ticker:
            render_price_chart(selected_ticker, filters["start_date"], filters["end_date"])
