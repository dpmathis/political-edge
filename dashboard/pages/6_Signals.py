"""Signals & Paper Trading Dashboard."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import DB_PATH

st.title("Signals & Paper Trading")
st.caption("Trading signal management and paper portfolio tracking")

from dashboard.components.freshness import render_freshness
render_freshness("trading_signals", "signal_date", "Trading Signals")


@st.cache_data(ttl=60)
def load_signals(status_filter: str | None = None) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    query = """SELECT id, signal_date, ticker, signal_type, direction, conviction,
                      position_size_modifier, status, entry_price, entry_date,
                      exit_price, exit_date, pnl_dollars, pnl_percent,
                      holding_days, rationale, macro_regime_at_signal,
                      stop_loss_price, take_profit_price, suggested_position_size,
                      time_horizon_days, expected_car,
                      historical_win_rate, historical_p_value, historical_n_events
               FROM trading_signals"""
    if status_filter and status_filter != "All":
        query += f" WHERE status = '{status_filter}'"
    query += " ORDER BY signal_date DESC"
    try:
        df = pd.read_sql_query(query, conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=60)
def load_paper_trades() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            """SELECT pt.*, ts.signal_type, ts.direction AS signal_direction
               FROM paper_trades pt
               LEFT JOIN trading_signals ts ON pt.signal_id = ts.id
               ORDER BY pt.created_at DESC LIMIT 50""",
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


def get_alpaca_account():
    """Try to get Alpaca account info."""
    try:
        from execution.paper_trader import PaperTrader
        trader = PaperTrader()
        if trader.is_configured:
            return trader.get_account(), trader.get_positions()
    except Exception:
        pass
    return None, []


# ── Row 1: Portfolio Summary ──────────────────────────────────────────
account_info, positions = get_alpaca_account()

if account_info:
    st.subheader("Portfolio Summary")
    port_cols = st.columns(5)
    with port_cols[0]:
        st.metric("Account Equity", f"${account_info['equity']:,.2f}")
    with port_cols[1]:
        st.metric("Portfolio Value", f"${account_info['portfolio_value']:,.2f}")
    with port_cols[2]:
        st.metric("Cash", f"${account_info['cash']:,.2f}")
    with port_cols[3]:
        st.metric("Buying Power", f"${account_info['buying_power']:,.2f}")
    with port_cols[4]:
        exposure_pct = (account_info['portfolio_value'] - account_info['cash']) / max(account_info['equity'], 1)
        st.metric("Exposure", f"{exposure_pct:.1%}")

    if positions:
        st.markdown("**Open Positions**")
        pos_df = pd.DataFrame(positions)
        pos_df["pnl_pct"] = pos_df["pnl_pct"].apply(lambda x: f"{x:+.2%}")
        pos_df["pnl"] = pos_df["pnl"].apply(lambda x: f"${x:+,.2f}")
        pos_df["market_value"] = pos_df["market_value"].apply(lambda x: f"${x:,.2f}")
        st.dataframe(pos_df, use_container_width=True, hide_index=True)

    st.markdown("---")
else:
    st.info("Alpaca paper trading not configured. Add `alpaca_key_id` and `alpaca_secret_key` to config.yaml to enable portfolio tracking.")

# ── Signal Generation Controls ────────────────────────────────────────
st.subheader("Signal Management")

ctrl_col1, ctrl_col2, ctrl_col3 = st.columns(3)

with ctrl_col1:
    if st.button("Generate Signals", type="primary"):
        with st.spinner("Generating signals..."):
            from analysis.signal_generator import generate_signals
            new_signals = generate_signals()
            st.success(f"Generated {len(new_signals)} new signals")
            st.cache_data.clear()

with ctrl_col2:
    if st.button("Review Active Signals"):
        with st.spinner("Reviewing active signals..."):
            from analysis.signal_generator import review_active_signals
            closed = review_active_signals()
            st.success(f"Closed {closed} signals (exit conditions met)")
            st.cache_data.clear()

with ctrl_col3:
    status_filter = st.selectbox(
        "Filter by status",
        ["All", "pending", "active", "closed", "expired", "skipped"],
    )

# ── Row 2: Signals Table ─────────────────────────────────────────────
signals_df = load_signals(status_filter)

if signals_df.empty:
    st.info("No trading signals yet. Click 'Generate Signals' to evaluate all signal rules.")
else:
    st.markdown(f"**{len(signals_df)} signals**")

    # Format for display
    display_df = signals_df.copy()

    for col in ["pnl_percent"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(
                lambda x: f"{x:+.2%}" if pd.notna(x) else ""
            )
    for col in ["pnl_dollars"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(
                lambda x: f"${x:+,.2f}" if pd.notna(x) else ""
            )
    if "position_size_modifier" in display_df.columns:
        display_df["position_size_modifier"] = display_df["position_size_modifier"].apply(
            lambda x: f"{x:.1f}x" if pd.notna(x) else ""
        )

    # Format new trade parameter columns
    for col in ["expected_car", "historical_win_rate"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(
                lambda x: f"{x:+.2%}" if pd.notna(x) else ""
            )
    if "suggested_position_size" in display_df.columns:
        display_df["suggested_position_size"] = display_df["suggested_position_size"].apply(
            lambda x: f"{x:.1%}" if pd.notna(x) else ""
        )
    for col in ["stop_loss_price", "take_profit_price"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(
                lambda x: f"${x:,.2f}" if pd.notna(x) else ""
            )
    if "historical_p_value" in display_df.columns:
        display_df["historical_p_value"] = display_df["historical_p_value"].apply(
            lambda x: f"{x:.3f}" if pd.notna(x) else ""
        )

    show_cols = [
        "signal_date", "ticker", "signal_type", "direction", "conviction",
        "position_size_modifier", "status", "expected_car", "entry_price",
        "stop_loss_price", "take_profit_price", "pnl_percent",
        "holding_days", "rationale",
    ]
    available = [c for c in show_cols if c in display_df.columns]

    rename = {
        "signal_date": "Date",
        "ticker": "Ticker",
        "signal_type": "Type",
        "direction": "Direction",
        "conviction": "Conviction",
        "position_size_modifier": "Macro Mod",
        "status": "Status",
        "expected_car": "Exp CAR",
        "entry_price": "Entry $",
        "stop_loss_price": "Stop $",
        "take_profit_price": "TP $",
        "pnl_percent": "PnL %",
        "holding_days": "Days",
        "rationale": "Rationale",
    }
    display_df = display_df[available].rename(columns=rename)
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # Historical performance context
    with st.expander("Signal Historical Performance"):
        perf_cols_show = [
            "signal_type", "expected_car", "historical_win_rate",
            "historical_p_value", "historical_n_events",
            "suggested_position_size", "time_horizon_days",
        ]
        perf_available = [c for c in perf_cols_show if c in signals_df.columns]
        if perf_available:
            perf_df = signals_df[perf_available].drop_duplicates(subset=["signal_type"])
            perf_rename = {
                "signal_type": "Signal Type",
                "expected_car": "Expected CAR",
                "historical_win_rate": "Win Rate",
                "historical_p_value": "p-value",
                "historical_n_events": "N Events",
                "suggested_position_size": "Position Size",
                "time_horizon_days": "Horizon (days)",
            }
            for col in ["expected_car", "historical_win_rate"]:
                if col in perf_df.columns:
                    perf_df[col] = perf_df[col].apply(
                        lambda x: f"{x:+.2%}" if pd.notna(x) else "N/A"
                    )
            if "historical_p_value" in perf_df.columns:
                perf_df["historical_p_value"] = perf_df["historical_p_value"].apply(
                    lambda x: f"{x:.3f}" if pd.notna(x) else "N/A"
                )
            if "suggested_position_size" in perf_df.columns:
                perf_df["suggested_position_size"] = perf_df["suggested_position_size"].apply(
                    lambda x: f"{x:.1%}" if pd.notna(x) else "N/A"
                )
            perf_df = perf_df.rename(columns=perf_rename)
            st.dataframe(perf_df, use_container_width=True, hide_index=True)

    # ── Prediction Market Context ────────────────────────────────────
    with st.expander("Prediction Market Context"):
        try:
            pred_conn = sqlite3.connect(DB_PATH)
            pred_df = pd.read_sql_query(
                """SELECT question_text, current_price, volume, category, related_ticker
                   FROM prediction_markets
                   WHERE current_price IS NOT NULL
                   ORDER BY volume DESC LIMIT 10""",
                pred_conn,
            )
            pred_conn.close()

            if not pred_df.empty:
                pred_df["current_price"] = pred_df["current_price"].apply(lambda x: f"{x:.1%}")
                pred_df["volume"] = pred_df["volume"].apply(lambda x: f"${x:,.0f}")
                pred_df = pred_df.rename(columns={
                    "question_text": "Market",
                    "current_price": "Probability",
                    "volume": "Volume",
                    "category": "Category",
                    "related_ticker": "Ticker",
                })
                st.dataframe(pred_df, use_container_width=True, hide_index=True)
            else:
                st.caption("No prediction market data. Run Polymarket collector from Settings.")
        except Exception:
            st.caption("Prediction markets table not available.")

    # ── Signal Actions ────────────────────────────────────────────────
    pending_signals = signals_df[signals_df["status"] == "pending"]
    if not pending_signals.empty and account_info:
        st.markdown("**Execute Pending Signals**")
        selected_id = st.selectbox(
            "Select signal to execute",
            pending_signals["id"].tolist(),
            format_func=lambda x: f"#{x} — {pending_signals[pending_signals['id']==x].iloc[0]['ticker']} ({pending_signals[pending_signals['id']==x].iloc[0]['signal_type']})",
        )

        exec_col1, exec_col2, exec_col3 = st.columns(3)
        with exec_col1:
            if st.button("Execute Trade"):
                with st.spinner("Executing..."):
                    from execution.paper_trader import PaperTrader
                    trader = PaperTrader()
                    sig_row = pending_signals[pending_signals["id"] == selected_id].iloc[0]
                    result = trader.execute_signal(sig_row.to_dict(), account_info["equity"])
                    if result["status"] in ("submitted", "filled"):
                        st.success(f"Order submitted: {result.get('shares', 0)} shares")
                    else:
                        st.warning(f"Order {result['status']}: {result.get('reason', '')}")
                    st.cache_data.clear()

        with exec_col2:
            if st.button("Skip Signal"):
                conn = sqlite3.connect(DB_PATH)
                conn.execute("UPDATE trading_signals SET status = 'skipped' WHERE id = ?", (selected_id,))
                conn.commit()
                conn.close()
                st.success("Signal skipped")
                st.cache_data.clear()

# ── Row 3: Signal Performance ─────────────────────────────────────────
st.markdown("---")
st.subheader("Signal Performance")

closed_signals = signals_df[signals_df["status"] == "closed"] if not signals_df.empty else pd.DataFrame()

if closed_signals.empty:
    st.info("No closed signals yet. Performance metrics will appear after signals are executed and closed.")
else:
    perf_cols = st.columns(4)

    with perf_cols[0]:
        win_rate = (closed_signals["pnl_percent"] > 0).mean() if "pnl_percent" in closed_signals.columns else 0
        st.metric("Win Rate", f"{win_rate:.1%}")

    with perf_cols[1]:
        avg_pnl = closed_signals["pnl_percent"].mean() if "pnl_percent" in closed_signals.columns else 0
        st.metric("Avg PnL", f"{avg_pnl:+.2%}" if pd.notna(avg_pnl) else "N/A")

    with perf_cols[2]:
        total_pnl = closed_signals["pnl_dollars"].sum() if "pnl_dollars" in closed_signals.columns else 0
        st.metric("Total PnL", f"${total_pnl:+,.2f}" if pd.notna(total_pnl) else "N/A")

    with perf_cols[3]:
        avg_hold = closed_signals["holding_days"].mean() if "holding_days" in closed_signals.columns else 0
        st.metric("Avg Hold Days", f"{avg_hold:.0f}" if pd.notna(avg_hold) else "N/A")

    # Performance by signal type
    perf_chart_col1, perf_chart_col2 = st.columns(2)

    with perf_chart_col1:
        if "signal_type" in closed_signals.columns and "pnl_percent" in closed_signals.columns:
            by_type = closed_signals.groupby("signal_type").agg(
                avg_pnl=("pnl_percent", "mean"),
                count=("id", "count"),
                win_rate=("pnl_percent", lambda x: (x > 0).mean()),
            ).reset_index()

            if not by_type.empty:
                fig = px.bar(
                    by_type, x="signal_type", y="avg_pnl",
                    color="avg_pnl",
                    color_continuous_scale=["red", "gray", "green"],
                    title="Avg PnL by Signal Type",
                    text="count",
                )
                fig.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0))
                st.plotly_chart(fig, use_container_width=True)

    with perf_chart_col2:
        if "signal_date" in closed_signals.columns and "pnl_dollars" in closed_signals.columns:
            cumulative = closed_signals.sort_values("exit_date").copy()
            cumulative["cum_pnl"] = cumulative["pnl_dollars"].cumsum()

            if not cumulative.empty:
                fig = px.line(
                    cumulative, x="exit_date", y="cum_pnl",
                    title="Cumulative PnL Over Time",
                )
                fig.add_hline(y=0, line_dash="dash", line_color="gray")
                fig.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0), yaxis_title="Cumulative PnL ($)")
                st.plotly_chart(fig, use_container_width=True)

# ── Row 4: Recent Trades ─────────────────────────────────────────────
trades_df = load_paper_trades()

if not trades_df.empty:
    st.markdown("---")
    st.subheader("Recent Paper Trades")

    trade_cols = ["created_at", "ticker", "side", "quantity", "price", "filled_price", "status", "signal_type"]
    available = [c for c in trade_cols if c in trades_df.columns]
    display_trades = trades_df[available].copy()

    rename = {
        "created_at": "Date",
        "ticker": "Ticker",
        "side": "Side",
        "quantity": "Qty",
        "price": "Price",
        "filled_price": "Filled",
        "status": "Status",
        "signal_type": "Signal Type",
    }
    display_trades = display_trades.rename(columns=rename)
    st.dataframe(display_trades, use_container_width=True, hide_index=True)
