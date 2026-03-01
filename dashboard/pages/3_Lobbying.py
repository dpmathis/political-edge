"""Lobbying Activity — Lobbying disclosure filings with QoQ spending analysis."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import DB_PATH

st.title("Lobbying Activity")
st.caption("Lobbying disclosure filings with QoQ spending analysis")

conn = sqlite3.connect(DB_PATH)

# --- KPI ROW ---
total_filings = conn.execute("SELECT COUNT(*) FROM lobbying_filings").fetchone()[0]
total_spend = conn.execute(
    "SELECT COALESCE(SUM(amount), 0) FROM lobbying_filings WHERE client_ticker IS NOT NULL"
).fetchone()[0]
tickers_with_filings = conn.execute(
    "SELECT COUNT(DISTINCT client_ticker) FROM lobbying_filings WHERE client_ticker IS NOT NULL"
).fetchone()[0]

# Calculate average spend per company
avg_spend = total_spend / tickers_with_filings if tickers_with_filings > 0 else 0

kpi_cols = st.columns(4)
with kpi_cols[0]:
    st.metric("Total Filings", f"{total_filings:,}")
with kpi_cols[1]:
    st.metric("Watchlist Spend", f"${total_spend:,.0f}")
with kpi_cols[2]:
    st.metric("Companies Tracked", tickers_with_filings)
with kpi_cols[3]:
    st.metric("Avg Spend/Company", f"${avg_spend:,.0f}")

# --- SPEND OVER TIME + QOQ HEATMAP ---
st.markdown("---")

spend_df = pd.read_sql_query(
    """SELECT client_ticker, client_name, filing_year, filing_period, SUM(amount) as total_spend
       FROM lobbying_filings
       WHERE client_ticker IS NOT NULL AND amount IS NOT NULL
       GROUP BY client_ticker, filing_year, filing_period
       ORDER BY filing_year, filing_period""",
    conn,
)

if not spend_df.empty:
    spend_df["period_label"] = spend_df["filing_year"].astype(str) + " " + spend_df["filing_period"].fillna("")

    col_chart, col_heat = st.columns(2)

    with col_chart:
        st.subheader("Spending Over Time")
        tickers_available = sorted(spend_df["client_ticker"].unique().tolist())
        selected_tickers = st.multiselect(
            "Select companies", tickers_available, default=tickers_available[:5], key="lobby_tickers"
        )
        if selected_tickers:
            chart_df = spend_df[spend_df["client_ticker"].isin(selected_tickers)]
            fig = px.line(
                chart_df,
                x="period_label",
                y="total_spend",
                color="client_ticker",
                markers=True,
            )
            fig.update_layout(
                height=350,
                margin=dict(l=40, r=40, t=10, b=40),
                yaxis_title="Total Spend ($)",
                xaxis_title="Period",
            )
            st.plotly_chart(fig, use_container_width=True)

    with col_heat:
        st.subheader("QoQ Change Heatmap")

        # Calculate QoQ changes
        pivot = spend_df.pivot_table(index="client_ticker", columns="period_label", values="total_spend")
        if pivot.shape[1] >= 2:
            pct_change = pivot.pct_change(axis=1) * 100
            pct_change = pct_change.iloc[:, 1:]  # Drop first column (NaN)

            if not pct_change.empty:
                fig = go.Figure(
                    data=go.Heatmap(
                        z=pct_change.values,
                        x=pct_change.columns.tolist(),
                        y=pct_change.index.tolist(),
                        colorscale=[[0, "#22c55e"], [0.5, "#f5f5f5"], [1, "#ef4444"]],
                        zmid=0,
                        text=[[f"{v:.0f}%" if pd.notna(v) else "" for v in row] for row in pct_change.values],
                        texttemplate="%{text}",
                        hovertemplate="Ticker: %{y}<br>Period: %{x}<br>Change: %{z:.1f}%<extra></extra>",
                    )
                )
                fig.update_layout(height=350, margin=dict(l=40, r=40, t=10, b=40))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Not enough data for QoQ comparison.")
        else:
            st.info("Need at least 2 periods for QoQ comparison.")
else:
    st.info("No lobbying filings with ticker mappings found. Run collectors to populate.")

# --- FILINGS TABLE ---
st.markdown("---")
st.subheader("Lobbying Filings")

filings_df = pd.read_sql_query(
    """SELECT client_name, client_ticker, registrant_name, amount,
              filing_year, filing_period, specific_issues, government_entities
       FROM lobbying_filings
       ORDER BY filing_year DESC, filing_period DESC, amount DESC
       LIMIT 500""",
    conn,
)

if not filings_df.empty:
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        ticker_filter = st.multiselect(
            "Filter by Ticker",
            sorted(filings_df["client_ticker"].dropna().unique().tolist()),
            key="lobby_ticker_filter",
        )
    with col_f2:
        issue_search = st.text_input("Search issues", key="lobby_issue_search")

    display_df = filings_df.copy()
    if ticker_filter:
        display_df = display_df[display_df["client_ticker"].isin(ticker_filter)]
    if issue_search:
        display_df = display_df[
            display_df["specific_issues"].fillna("").str.contains(issue_search, case=False)
        ]

    # Format amount
    display_df["amount"] = display_df["amount"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "")
    display_df["period"] = display_df["filing_year"].astype(str) + " " + display_df["filing_period"].fillna("")

    # Truncate issues for display
    display_df["specific_issues"] = display_df["specific_issues"].apply(
        lambda x: x[:150] + "..." if isinstance(x, str) and len(x) > 150 else x
    )

    st.dataframe(
        display_df[["client_name", "client_ticker", "amount", "period", "specific_issues", "government_entities"]],
        column_config={
            "client_name": "Client",
            "client_ticker": "Ticker",
            "amount": "Amount",
            "period": "Period",
            "specific_issues": "Issues",
            "government_entities": "Gov Entities",
        },
        use_container_width=True,
        height=400,
    )
else:
    st.info("No lobbying filings found. Run collectors to populate.")

# --- CROSS-REFERENCE ---
st.markdown("---")
st.subheader("Cross-Reference: Lobbying + Regulatory Events")

matched_tickers = pd.read_sql_query(
    "SELECT DISTINCT client_ticker FROM lobbying_filings WHERE client_ticker IS NOT NULL ORDER BY client_ticker",
    conn,
)

if not matched_tickers.empty:
    selected_company = st.selectbox(
        "Select a company to cross-reference",
        matched_tickers["client_ticker"].tolist(),
        key="lobby_xref_company",
    )

    if selected_company:
        # Get lobbying issues for this company
        company_issues = conn.execute(
            """SELECT specific_issues, filing_year, filing_period
               FROM lobbying_filings
               WHERE client_ticker = ? AND specific_issues IS NOT NULL
               ORDER BY filing_year DESC, filing_period DESC LIMIT 5""",
            (selected_company,),
        ).fetchall()

        if company_issues:
            # Extract keywords from issues
            all_issues_text = " ".join(row[0] for row in company_issues if row[0])
            # Use important words (>4 chars) as search terms
            words = set(
                w.strip(".,;:()")
                for w in all_issues_text.split()
                if len(w.strip(".,;:()")) > 4 and w[0].isalpha()
            )
            # Take top 10 most common-ish keywords
            search_terms = list(words)[:10]

            if search_terms:
                # Build LIKE conditions for matching
                like_conditions = " OR ".join(
                    f"title LIKE '%{term}%'" for term in search_terms[:5]
                )
                matching_events = pd.read_sql_query(
                    f"""SELECT publication_date, source, event_type, title, impact_score, sectors
                        FROM regulatory_events
                        WHERE ({like_conditions})
                          AND tickers LIKE ?
                        ORDER BY publication_date DESC
                        LIMIT 20""",
                    conn,
                    params=(f"%{selected_company}%",),
                )

                if not matching_events.empty:
                    st.dataframe(matching_events, use_container_width=True)
                else:
                    st.info(f"No regulatory events found matching {selected_company}'s lobbying issues.")
            else:
                st.info("No lobbying issue keywords to cross-reference.")
        else:
            st.info(f"No lobbying issues found for {selected_company}.")
else:
    st.info("No companies with ticker mappings in lobbying data.")

conn.close()
