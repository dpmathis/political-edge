"""Stock price chart with MAs, volume, benchmark overlay, and event markers."""

import sqlite3

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from config import DB_PATH


def render_price_chart(
    ticker: str,
    start_date: str,
    end_date: str,
    show_mas: bool = True,
    show_volume: bool = True,
    benchmark: str | None = None,
    event_windows: bool = False,
    window_pre: int = 5,
    window_post: int = 10,
):
    """Render a stock price chart with event markers, MAs, volume, and benchmark.

    Args:
        ticker: Stock ticker symbol.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        show_mas: Show 20-day and 50-day moving averages.
        show_volume: Show volume bars on secondary y-axis.
        benchmark: Optional benchmark ticker to overlay (e.g., "SPY", "XBI").
        event_windows: Show shaded pre/post event study windows.
        window_pre: Days before event for window shading.
        window_post: Days after event for window shading.
    """
    conn = sqlite3.connect(DB_PATH)

    # Fetch price data
    try:
        prices = pd.read_sql_query(
            """SELECT date, close, volume FROM market_data
               WHERE ticker = ? AND date >= ? AND date <= ?
               ORDER BY date""",
            conn,
            params=(ticker, start_date, end_date),
        )
    except Exception:
        prices = pd.DataFrame()

    if prices.empty:
        st.info(f"No price data for {ticker} in this date range.")
        conn.close()
        return

    prices["date"] = pd.to_datetime(prices["date"])

    # Compute moving averages
    if show_mas and len(prices) >= 20:
        prices["ma20"] = prices["close"].rolling(20).mean()
        prices["ma50"] = prices["close"].rolling(50).mean()

    # Fetch benchmark data if requested
    bench_df = None
    if benchmark and benchmark != ticker:
        try:
            bench_df = pd.read_sql_query(
                """SELECT date, close FROM market_data
                   WHERE ticker = ? AND date >= ? AND date <= ?
                   ORDER BY date""",
                conn,
                params=(benchmark, start_date, end_date),
            )
        except Exception:
            bench_df = pd.DataFrame()
        if not bench_df.empty:
            bench_df["date"] = pd.to_datetime(bench_df["date"])
            # Normalize to percentage change from first day
            bench_df["pct"] = (bench_df["close"] / bench_df["close"].iloc[0] - 1) * 100
            prices["pct"] = (prices["close"] / prices["close"].iloc[0] - 1) * 100

    # Fetch regulatory events
    try:
        events = pd.read_sql_query(
            """SELECT publication_date, title, event_type, impact_score, agency
               FROM regulatory_events
               WHERE tickers LIKE ? AND publication_date >= ? AND publication_date <= ?
               ORDER BY publication_date""",
            conn,
            params=(f"%{ticker}%", start_date, end_date),
        )
    except Exception:
        events = pd.DataFrame()
    conn.close()

    # Build chart with subplots if volume is shown
    if show_volume:
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.8, 0.2], vertical_spacing=0.02,
        )
    else:
        fig = make_subplots(rows=1, cols=1)

    # Use performance comparison mode if benchmark is active
    if bench_df is not None and not bench_df.empty:
        # Relative performance chart
        fig.add_trace(
            go.Scatter(
                x=prices["date"], y=prices["pct"],
                mode="lines", name=ticker,
                line=dict(color="#2563eb", width=2),
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=bench_df["date"], y=bench_df["pct"],
                mode="lines", name=benchmark,
                line=dict(color="#94a3b8", width=1.5, dash="dash"),
            ),
            row=1, col=1,
        )
        y_title = "Relative Performance (%)"
    else:
        # Standard price chart
        fig.add_trace(
            go.Scatter(
                x=prices["date"], y=prices["close"],
                mode="lines", name=f"{ticker} Close",
                line=dict(color="#2563eb", width=2),
            ),
            row=1, col=1,
        )
        y_title = "Close Price ($)"

        # Moving averages
        if show_mas and "ma20" in prices.columns:
            fig.add_trace(
                go.Scatter(
                    x=prices["date"], y=prices["ma20"],
                    mode="lines", name="20-day MA",
                    line=dict(color="#f59e0b", width=1),
                ),
                row=1, col=1,
            )
        if show_mas and "ma50" in prices.columns:
            fig.add_trace(
                go.Scatter(
                    x=prices["date"], y=prices["ma50"],
                    mode="lines", name="50-day MA",
                    line=dict(color="#ef4444", width=1),
                ),
                row=1, col=1,
            )

    # Volume bars
    if show_volume and "volume" in prices.columns:
        fig.add_trace(
            go.Bar(
                x=prices["date"], y=prices["volume"],
                name="Volume",
                marker_color="#cbd5e1",
                opacity=0.5,
            ),
            row=2, col=1,
        )

    # Event markers and windows
    if not events.empty:
        events["publication_date"] = pd.to_datetime(events["publication_date"])
        impact_colors = {1: "#94a3b8", 2: "#60a5fa", 3: "#fbbf24", 4: "#f97316", 5: "#ef4444"}

        y_col = "pct" if (bench_df is not None and not bench_df.empty) else "close"
        y_min = prices[y_col].min()
        y_max = prices[y_col].max()
        y_range = y_max - y_min

        for _, event in events.iterrows():
            evt_date = event["publication_date"]
            impact = event["impact_score"]
            color = impact_colors.get(impact, "#94a3b8")

            # Vertical line at event date
            fig.add_trace(
                go.Scatter(
                    x=[evt_date, evt_date],
                    y=[y_min - y_range * 0.02, y_max + y_range * 0.02],
                    mode="lines",
                    line=dict(color=color, width=1, dash="dot"),
                    showlegend=False,
                    hoverinfo="text",
                    hovertext=(
                        f"{event['event_type']}: {event['title'][:80]}...<br>"
                        f"Impact: {impact}<br>Agency: {event['agency']}"
                    ),
                ),
                row=1, col=1,
            )

            # Event study window shading
            if event_windows and impact >= 3:
                pre_date = evt_date - pd.Timedelta(days=int(window_pre * 1.5))  # rough trading days
                post_date = evt_date + pd.Timedelta(days=int(window_post * 1.5))

                fig.add_vrect(
                    x0=pre_date, x1=post_date,
                    fillcolor=color, opacity=0.06,
                    line_width=0,
                    row=1, col=1,
                )

    fig.update_layout(
        title=f"{ticker} — Price with Regulatory Events",
        height=500 if show_volume else 400,
        margin=dict(l=40, r=40, t=50, b=40),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        xaxis_rangeslider_visible=False,
    )

    fig.update_yaxes(title_text=y_title, row=1, col=1)
    if show_volume:
        fig.update_yaxes(title_text="Volume", row=2, col=1, showgrid=False)
        fig.update_xaxes(title_text="Date", row=2, col=1)
    else:
        fig.update_xaxes(title_text="Date", row=1, col=1)

    st.plotly_chart(fig, use_container_width=True)
