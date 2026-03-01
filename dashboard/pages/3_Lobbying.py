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
    "SELECT COALESCE(SUM(amount), 0) FROM lobbying_filings"
).fetchone()[0]
unique_clients = conn.execute(
    "SELECT COUNT(DISTINCT client_name) FROM lobbying_filings"
).fetchone()[0]
unique_registrants = conn.execute(
    "SELECT COUNT(DISTINCT registrant_name) FROM lobbying_filings"
).fetchone()[0]

kpi_cols = st.columns(4)
with kpi_cols[0]:
    st.metric("Total Filings", f"{total_filings:,}")
with kpi_cols[1]:
    st.metric("Total Spend", f"${total_spend:,.0f}")
with kpi_cols[2]:
    st.metric("Unique Clients", f"{unique_clients:,}")
with kpi_cols[3]:
    st.metric("Lobbying Firms", f"{unique_registrants:,}")

# --- SPEND OVER TIME + QOQ HEATMAP ---
st.markdown("---")

# Top spenders
top_spenders = pd.read_sql_query(
    """SELECT client_name, SUM(amount) as total_spend, COUNT(*) as filings
       FROM lobbying_filings
       WHERE amount IS NOT NULL AND amount > 0
       GROUP BY client_name
       ORDER BY total_spend DESC
       LIMIT 20""",
    conn,
)

if not top_spenders.empty:
    col_chart, col_period = st.columns(2)

    with col_chart:
        st.subheader("Top Spenders")
        fig = px.bar(
            top_spenders.head(15),
            x="total_spend",
            y="client_name",
            orientation="h",
            color="total_spend",
            color_continuous_scale="Blues",
        )
        fig.update_layout(
            height=400,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis_title="Total Spend ($)",
            yaxis_title="",
            yaxis=dict(autorange="reversed"),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

    with col_period:
        st.subheader("Spending by Period")
        period_spend = pd.read_sql_query(
            """SELECT filing_year, filing_period, SUM(amount) as total_spend, COUNT(*) as filings
               FROM lobbying_filings
               WHERE amount IS NOT NULL
               GROUP BY filing_year, filing_period
               ORDER BY filing_year, filing_period""",
            conn,
        )
        if not period_spend.empty:
            period_spend["period_label"] = (
                period_spend["filing_year"].astype(str) + " " + period_spend["filing_period"].fillna("")
            )
            fig = px.bar(
                period_spend,
                x="period_label",
                y="total_spend",
                text="filings",
                color="total_spend",
                color_continuous_scale="Blues",
            )
            fig.update_layout(
                height=400,
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis_title="Period",
                yaxis_title="Total Spend ($)",
                showlegend=False,
            )
            fig.update_traces(texttemplate="%{text} filings", textposition="outside")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No spending data by period.")
else:
    st.info("No lobbying filings found. Run collectors to populate.")

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
