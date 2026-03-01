"""Stock price chart with regulatory event overlay markers."""

import sqlite3

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import DB_PATH


def render_price_chart(ticker: str, start_date: str, end_date: str):
    """Render a stock price chart with event markers for a given ticker."""
    conn = sqlite3.connect(DB_PATH)

    # Fetch price data
    prices = pd.read_sql_query(
        """SELECT date, close, volume FROM market_data
           WHERE ticker = ? AND date >= ? AND date <= ?
           ORDER BY date""",
        conn,
        params=(ticker, start_date, end_date),
    )

    if prices.empty:
        st.info(f"No price data for {ticker} in this date range.")
        conn.close()
        return

    prices["date"] = pd.to_datetime(prices["date"])

    # Fetch regulatory events that mention this ticker
    events = pd.read_sql_query(
        """SELECT publication_date, title, event_type, impact_score, agency
           FROM regulatory_events
           WHERE tickers LIKE ? AND publication_date >= ? AND publication_date <= ?
           ORDER BY publication_date""",
        conn,
        params=(f"%{ticker}%", start_date, end_date),
    )
    conn.close()

    # Build the chart
    fig = go.Figure()

    # Price line
    fig.add_trace(
        go.Scatter(
            x=prices["date"],
            y=prices["close"],
            mode="lines",
            name=f"{ticker} Close",
            line=dict(color="#2563eb", width=2),
        )
    )

    # Event markers as vertical lines
    if not events.empty:
        events["publication_date"] = pd.to_datetime(events["publication_date"])

        impact_colors = {1: "#94a3b8", 2: "#60a5fa", 3: "#fbbf24", 4: "#f97316", 5: "#ef4444"}

        for _, event in events.iterrows():
            evt_date = event["publication_date"]
            impact = event["impact_score"]
            color = impact_colors.get(impact, "#94a3b8")

            # Find the price on that date (or nearest)
            price_on_date = prices.loc[
                (prices["date"] - evt_date).abs().idxmin(), "close"
            ]

            fig.add_trace(
                go.Scatter(
                    x=[evt_date, evt_date],
                    y=[prices["close"].min() * 0.98, prices["close"].max() * 1.02],
                    mode="lines",
                    line=dict(color=color, width=1, dash="dot"),
                    showlegend=False,
                    hoverinfo="text",
                    hovertext=f"{event['event_type']}: {event['title'][:80]}...<br>Impact: {impact}<br>Agency: {event['agency']}",
                )
            )

    fig.update_layout(
        title=f"{ticker} — Price with Regulatory Events",
        xaxis_title="Date",
        yaxis_title="Close Price ($)",
        height=400,
        margin=dict(l=40, r=40, t=50, b=40),
        hovermode="x unified",
    )

    st.plotly_chart(fig, use_container_width=True)
