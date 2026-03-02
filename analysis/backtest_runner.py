"""Hypothesis Backtest Runner.

Pre-configured backtests for validated hypotheses. Each backtest prepares
an event list from the database and runs it through the EventStudy framework.
"""

import logging
import sqlite3

from config import DB_PATH, load_tariff_events
from analysis.event_study import EventStudy, EventStudyResults

logger = logging.getLogger(__name__)


class BacktestRunner:
    """Runs predefined hypothesis backtests using the EventStudy framework."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DB_PATH
        self.event_study = EventStudy(self.db_path)

    def list_studies(self) -> list[str]:
        """List available backtest study names."""
        return ["tariff_sectors", "contract_awards", "fda_adcom",
                "high_impact_regulatory", "fomc_drift",
                "report1_reg_shocks", "report2_eo_impact",
                "report3_reg_pipeline", "report4_tariff_asymmetry",
                "report5_macro_conditional"]

    def run_all(self) -> dict[str, EventStudyResults]:
        """Run all hypothesis backtests. Returns dict of name → results."""
        results = {}
        for study_name in self.list_studies():
            try:
                logger.info("Running backtest: %s", study_name)
                results[study_name] = self.run_study(study_name)
            except Exception as e:
                logger.error("Backtest '%s' failed: %s", study_name, e, exc_info=True)
        return results

    def run_study(self, name: str) -> EventStudyResults:
        """Run a specific backtest by name."""
        method_map = {
            "tariff_sectors": self.backtest_tariff_sectors,
            "contract_awards": self.backtest_contract_awards,
            "fda_adcom": self.backtest_fda_adcom,
            "high_impact_regulatory": self.backtest_high_impact_regulatory,
            "fomc_drift": self.backtest_fomc_drift,
            "report1_reg_shocks": self.backtest_report1,
            "report2_eo_impact": self.backtest_report2,
            "report3_reg_pipeline": self.backtest_report3,
            "report4_tariff_asymmetry": self.backtest_report4,
            "report5_macro_conditional": self.backtest_report5,
        }
        func = method_map.get(name)
        if not func:
            raise ValueError(f"Unknown study: {name}. Available: {list(method_map.keys())}")
        return func()

    def backtest_tariff_sectors(self) -> EventStudyResults:
        """Tariff announcements → sector ETF dispersion vs SPY."""
        tariff_events = load_tariff_events()

        # Build events: for each tariff date, use affected sector ETFs
        events = []
        for evt in tariff_events:
            for sector_etf in evt.get("affected_sectors", []):
                events.append({
                    "date": evt["date"],
                    "ticker": sector_etf,
                    "label": f"{evt['description']} ({sector_etf})",
                })

        return self.event_study.run(
            events=events,
            study_name="tariff_sectors",
            hypothesis="Tariff announcements cause 5-17% sector dispersion over 5 days",
            window_pre=1,
            window_post=5,
            benchmark="SPY",
            method="market_adjusted",
        )

    def backtest_contract_awards(self) -> EventStudyResults:
        """Large DOD contracts >$100M → positive CAR for winning firm."""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            """SELECT award_date, recipient_ticker, description
               FROM contract_awards
               WHERE award_amount >= 100000000
                 AND awarding_agency LIKE '%Defense%'
                 AND recipient_ticker IS NOT NULL
                 AND award_date IS NOT NULL
               ORDER BY award_date"""
        ).fetchall()
        conn.close()

        events = [
            {"date": r[0], "ticker": r[1], "label": (r[2] or "")[:100]}
            for r in rows
        ]

        if not events:
            logger.warning("No contract awards found for backtest. Using regulatory events as proxy.")
            return self._backtest_defense_regulatory()

        return self.event_study.run(
            events=events,
            study_name="contract_awards",
            hypothesis="Large DOD contract awards (>$100M) → positive CAR for winning firm",
            window_pre=0,
            window_post=10,
            benchmark="SPY",
            method="market_adjusted",
        )

    def _backtest_defense_regulatory(self) -> EventStudyResults:
        """Fallback: high-impact defense regulatory events → defense stock CAR."""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            """SELECT publication_date, tickers, title
               FROM regulatory_events
               WHERE sectors LIKE '%Defense%'
                 AND impact_score >= 4
                 AND tickers IS NOT NULL AND tickers != ''
               ORDER BY publication_date"""
        ).fetchall()
        conn.close()

        events = []
        seen = set()
        for pub_date, tickers, title in rows:
            for ticker in tickers.split(","):
                ticker = ticker.strip()
                if ticker:
                    key = (pub_date, ticker)
                    if key not in seen:
                        seen.add(key)
                        events.append({"date": pub_date, "ticker": ticker, "label": title[:100]})

        return self.event_study.run(
            events=events,
            study_name="contract_awards",
            hypothesis="High-impact defense regulatory events → defense stock CAR (proxy for contracts)",
            window_pre=0,
            window_post=10,
            benchmark="SPY",
            method="market_adjusted",
        )

    def backtest_fda_adcom(self) -> EventStudyResults:
        """FDA AdCom votes → CAR for affected company."""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            """SELECT event_date, ticker, details
               FROM fda_events
               WHERE event_type = 'adcom_vote'
                 AND ticker IS NOT NULL
                 AND event_date IS NOT NULL
               ORDER BY event_date"""
        ).fetchall()
        conn.close()

        events = [
            {"date": r[0], "ticker": r[1], "label": (r[2] or "")[:100]}
            for r in rows
        ]

        if not events:
            logger.warning("No FDA AdCom events with tickers found for backtest")
            return EventStudyResults(
                study_name="fda_adcom",
                hypothesis="FDA AdCom positive votes → +5-20% CAR over 5 days",
                method="market_adjusted", benchmark="XBI",
                window_pre=1, window_post=5, num_events=0,
                mean_car=0.0, median_car=0.0, t_statistic=0.0,
                p_value=1.0, win_rate=0.0, sharpe_ratio=0.0,
            )

        return self.event_study.run(
            events=events,
            study_name="fda_adcom",
            hypothesis="FDA AdCom positive votes → +5-20% CAR over 5 days",
            window_pre=1,
            window_post=5,
            benchmark="SPY",
            method="market_adjusted",
        )

    def backtest_high_impact_regulatory(self) -> EventStudyResults:
        """High-impact regulatory events (score >= 4) → affected ticker CAR."""
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            """SELECT publication_date, tickers, title
               FROM regulatory_events
               WHERE impact_score >= 4
                 AND tickers IS NOT NULL AND tickers != ''
               ORDER BY publication_date
               LIMIT 200"""
        ).fetchall()
        conn.close()

        events = []
        for pub_date, tickers, title in rows:
            for ticker in tickers.split(","):
                ticker = ticker.strip()
                if ticker:
                    events.append({"date": pub_date, "ticker": ticker, "label": title[:100]})

        # Deduplicate on (date, ticker) — multiple events on same day with same ticker
        seen = set()
        unique_events = []
        for e in events:
            key = (e["date"], e["ticker"])
            if key not in seen:
                seen.add(key)
                unique_events.append(e)
        events = unique_events

        return self.event_study.run(
            events=events,
            study_name="high_impact_regulatory",
            hypothesis="High-impact regulatory events (score >= 4) → measurable abnormal returns",
            window_pre=1,
            window_post=5,
            benchmark="SPY",
            method="market_adjusted",
        )

    def backtest_fomc_drift(self) -> EventStudyResults:
        """Pre-FOMC drift: SPY tends to rise in the 5 days before FOMC meetings.

        Literature documents a ~0.49% average drift in the 5 trading days
        before FOMC announcement days.
        """
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            """SELECT event_date FROM fomc_events
               WHERE event_type = 'meeting'
                 AND event_date <= date('now')
               ORDER BY event_date"""
        ).fetchall()
        conn.close()

        events = [
            {"date": r[0], "ticker": "SPY", "label": f"FOMC Meeting {r[0]}"}
            for r in rows
        ]

        # Deduplicate
        seen = set()
        unique_events = []
        for e in events:
            key = (e["date"], e["ticker"])
            if key not in seen:
                seen.add(key)
                unique_events.append(e)
        events = unique_events

        return self.event_study.run(
            events=events,
            study_name="fomc_drift",
            hypothesis="SPY exhibits positive drift (+0.49% avg) in the 5 days preceding FOMC meetings",
            window_pre=5,
            window_post=2,
            benchmark="SPY",
            method="raw_returns",  # Use raw returns since SPY is the benchmark
        )

    # ── Research Report Backtests ──────────────────────────────────

    def backtest_report1(self) -> EventStudyResults:
        """Report 1: Regulatory Intensity Shocks."""
        from analysis.research.report1_reg_shocks import run_report
        result = run_report(self.db_path)
        return result.event_studies[0] if result.event_studies else self._empty_result("report1_reg_shocks")

    def backtest_report2(self) -> EventStudyResults:
        """Report 2: Executive Order Market Impact."""
        from analysis.research.report2_eo_impact import run_report
        result = run_report(self.db_path)
        return result.event_studies[0] if result.event_studies else self._empty_result("report2_eo_impact")

    def backtest_report3(self) -> EventStudyResults:
        """Report 3: Regulatory Pipeline Rotation."""
        from analysis.research.report3_reg_pipeline import run_report
        result = run_report(self.db_path)
        return result.event_studies[0] if result.event_studies else self._empty_result("report3_reg_pipeline")

    def backtest_report4(self) -> EventStudyResults:
        """Report 4: Tariff Announcement Asymmetry."""
        from analysis.research.report4_tariff_asymmetry import run_report
        result = run_report(self.db_path)
        return result.event_studies[0] if result.event_studies else self._empty_result("report4_tariff_asymmetry")

    def backtest_report5(self) -> EventStudyResults:
        """Report 5: Macro Regime-Conditional Signal Returns."""
        from analysis.research.report5_macro_conditional import run_report
        result = run_report(db_path=self.db_path)
        return result.event_studies[0] if result.event_studies else self._empty_result("report5_macro_conditional")

    def _empty_result(self, name: str) -> EventStudyResults:
        """Return a placeholder result when a report has no event studies."""
        return EventStudyResults(
            study_name=name, hypothesis="", method="market_adjusted",
            benchmark="SPY", window_pre=1, window_post=5, num_events=0,
            mean_car=0.0, median_car=0.0, t_statistic=0.0,
            p_value=1.0, win_rate=0.0, sharpe_ratio=0.0,
        )
