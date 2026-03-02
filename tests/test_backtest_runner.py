"""Tests for analysis/backtest_runner.py."""

import pytest


class TestBacktestRunnerListStudies:
    """Test study listing."""

    def test_list_studies_returns_10(self, db_path):
        from analysis.backtest_runner import BacktestRunner
        runner = BacktestRunner(db_path=db_path)
        studies = runner.list_studies()
        assert len(studies) == 10

    def test_list_studies_contains_core_studies(self, db_path):
        from analysis.backtest_runner import BacktestRunner
        runner = BacktestRunner(db_path=db_path)
        studies = runner.list_studies()
        assert "tariff_sectors" in studies
        assert "fda_adcom" in studies
        assert "fomc_drift" in studies
        assert "high_impact_regulatory" in studies

    def test_list_studies_contains_report_studies(self, db_path):
        from analysis.backtest_runner import BacktestRunner
        runner = BacktestRunner(db_path=db_path)
        studies = runner.list_studies()
        for i in range(1, 6):
            report_prefix = f"report{i}_"
            assert any(s.startswith(report_prefix) for s in studies), f"Missing report{i}"


class TestBacktestRunnerRunStudy:
    """Test individual study execution."""

    def test_unknown_study_raises(self, db_path):
        from analysis.backtest_runner import BacktestRunner
        runner = BacktestRunner(db_path=db_path)
        with pytest.raises(ValueError, match="Unknown study"):
            runner.run_study("nonexistent_study")

    def test_high_impact_regulatory_returns_results(self, db_path):
        from analysis.backtest_runner import BacktestRunner
        from analysis.event_study import EventStudyResults
        runner = BacktestRunner(db_path=db_path)
        result = runner.backtest_high_impact_regulatory()
        assert isinstance(result, EventStudyResults)
        assert result.study_name == "high_impact_regulatory"

    def test_fomc_drift_returns_results(self, db_path):
        from analysis.backtest_runner import BacktestRunner
        from analysis.event_study import EventStudyResults
        runner = BacktestRunner(db_path=db_path)
        result = runner.backtest_fomc_drift()
        assert isinstance(result, EventStudyResults)
        assert result.study_name == "fomc_drift"

    def test_fda_adcom_handles_empty(self, db_path):
        from analysis.backtest_runner import BacktestRunner
        from analysis.event_study import EventStudyResults
        runner = BacktestRunner(db_path=db_path)
        # No FDA events seeded, should return empty result
        result = runner.backtest_fda_adcom()
        assert isinstance(result, EventStudyResults)
        assert result.num_events == 0


class TestBacktestRunnerEmptyResult:
    """Test _empty_result() returns valid defaults."""

    def test_empty_result_fields(self, db_path):
        from analysis.backtest_runner import BacktestRunner
        runner = BacktestRunner(db_path=db_path)
        result = runner._empty_result("test_study")
        assert result.study_name == "test_study"
        assert result.num_events == 0
        assert result.mean_car == 0.0
        assert result.p_value == 1.0
        assert result.win_rate == 0.0


class TestBacktestRunnerRunAll:
    """Test run_all() error handling."""

    def test_run_all_returns_dict(self, db_path):
        from analysis.backtest_runner import BacktestRunner
        runner = BacktestRunner(db_path=db_path)
        results = runner.run_all()
        assert isinstance(results, dict)
        # Some may fail but the dict should have entries
        assert len(results) > 0
