"""Tests for analysis/sector_mapper.py."""

import sqlite3


from analysis.sector_mapper import (
    map_event_to_sectors,
    tag_all_untagged,
    tag_event,
)


def _seed_sector_keywords(conn: sqlite3.Connection):
    """Insert sector keyword mappings for testing."""
    keywords = [
        ("Defense", "defense"),
        ("Defense", "military"),
        ("Defense", "procurement"),
        ("Defense", "pentagon"),
        ("Healthcare", "drug"),
        ("Healthcare", "pharmaceutical"),
        ("Healthcare", "fda"),
        ("Healthcare", "clinical trial"),
        ("Energy", "oil"),
        ("Energy", "pipeline"),
        ("Energy", "lng"),
        ("Technology", "artificial intelligence"),
        ("Technology", "semiconductor"),
        ("Technology", "cybersecurity"),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO sector_keyword_map (sector, keyword) VALUES (?, ?)",
        keywords,
    )
    conn.commit()


class TestMapEventToSectors:
    """Test map_event_to_sectors() sector and ticker detection."""

    def test_matches_defense_sector_by_keyword(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        sector_scores, tickers = map_event_to_sectors(
            title="New defense procurement regulation",
            summary="Pentagon announces military equipment standards",
            agency="Department of Defense",
            conn=conn,
        )
        conn.close()

        assert "Defense" in sector_scores
        # Should match: defense, procurement, pentagon, military = 4 keywords
        assert sector_scores["Defense"] >= 3

    def test_matches_healthcare_sector_by_keyword(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        sector_scores, tickers = map_event_to_sectors(
            title="FDA approves new drug application",
            summary="Pharmaceutical company receives clinical trial results",
            agency="FDA",
            conn=conn,
        )
        conn.close()

        assert "Healthcare" in sector_scores
        assert sector_scores["Healthcare"] >= 2

    def test_returns_empty_when_no_keyword_match(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        sector_scores, tickers = map_event_to_sectors(
            title="Routine administrative update",
            summary="Minor formatting change to existing regulations",
            agency="Office of Personnel Management",
            conn=conn,
        )
        conn.close()

        assert len(sector_scores) == 0

    def test_returns_tickers_from_matched_sector(self, db_path):
        """If Defense sector matches, LMT should appear (seeded in conftest watchlist)."""
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        sector_scores, tickers = map_event_to_sectors(
            title="Military defense budget increase",
            summary="Pentagon procurement expansion",
            agency="Department of Defense",
            conn=conn,
        )
        conn.close()

        assert "LMT" in tickers

    def test_returns_healthcare_tickers(self, db_path):
        """If Healthcare sector matches, PFE should appear (seeded in conftest watchlist)."""
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        sector_scores, tickers = map_event_to_sectors(
            title="FDA drug approval process",
            summary="Pharmaceutical regulations update",
            agency="FDA",
            conn=conn,
        )
        conn.close()

        assert "PFE" in tickers

    def test_multiple_sectors_can_match(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        sector_scores, tickers = map_event_to_sectors(
            title="Defense cybersecurity and semiconductor requirements",
            summary="Military artificial intelligence systems",
            agency="Department of Defense",
            conn=conn,
        )
        conn.close()

        assert "Defense" in sector_scores
        assert "Technology" in sector_scores

    def test_tickers_not_duplicated(self, db_path):
        """Same ticker should not appear twice even if matched by sector and agency."""
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        # Add a watchlist entry with key_agencies that will also match
        conn.execute(
            """UPDATE watchlist SET key_agencies = 'Department of Defense'
               WHERE ticker = 'LMT'"""
        )
        conn.commit()

        sector_scores, tickers = map_event_to_sectors(
            title="Defense procurement update",
            summary="Pentagon military standards",
            agency="Department of Defense",
            conn=conn,
        )
        conn.close()

        assert tickers.count("LMT") == 1

    def test_ticker_matched_by_agency_when_sector_not_matched(self, db_path):
        """Watchlist entries can match by agency even if their sector doesn't match."""
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        # Add a watchlist entry with key_agencies set
        conn.execute(
            """INSERT OR IGNORE INTO watchlist (ticker, company_name, sector, key_agencies, active)
               VALUES (?, ?, ?, ?, ?)""",
            ("BA", "Boeing", "Aerospace", "FAA", 1),
        )
        conn.commit()

        sector_scores, tickers = map_event_to_sectors(
            title="Routine airworthiness directive",
            summary="Standard maintenance bulletin",
            agency="FAA",
            conn=conn,
        )
        conn.close()

        # "Aerospace" has no keyword mappings, so sector won't match,
        # but BA should appear because agency matches "FAA"
        assert "BA" in tickers

    def test_keyword_matching_is_case_insensitive(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        sector_scores, tickers = map_event_to_sectors(
            title="DEFENSE PROCUREMENT STANDARDS",
            summary="PENTAGON MILITARY SPENDING",
            agency="DOD",
            conn=conn,
        )
        conn.close()

        assert "Defense" in sector_scores

    def test_uses_db_path_fallback_when_no_conn(self, db_path):
        """Verify map_event_to_sectors falls back to DB_PATH when conn=None."""
        # Seed keyword data directly
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)
        conn.close()

        from unittest.mock import patch

        with patch("analysis.sector_mapper.DB_PATH", db_path):
            sector_scores, tickers = map_event_to_sectors(
                title="Defense military procurement",
                summary="Pentagon standards",
                agency="DOD",
            )

        assert "Defense" in sector_scores


class TestTagEvent:
    """Test tag_event() database writes."""

    def test_tags_event_with_sectors_and_tickers(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        # Insert an untagged event
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, summary, agency, sectors, tickers, impact_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "test", "tag-test-1", "final_rule",
                "Defense procurement regulation update",
                "Military spending standards from the Pentagon",
                "Department of Defense",
                "", "", 4,
            ),
        )
        conn.commit()

        event_id = conn.execute(
            "SELECT id FROM regulatory_events WHERE source_id = 'tag-test-1'"
        ).fetchone()[0]

        tag_event(event_id, conn)

        row = conn.execute(
            "SELECT sectors, tickers FROM regulatory_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        conn.close()

        sectors_str, tickers_str = row
        assert "Defense" in sectors_str
        assert "LMT" in tickers_str

    def test_tag_event_nonexistent_id_does_not_raise(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)
        # Should silently return without error
        tag_event(99999, conn)
        conn.close()

    def test_tag_event_updates_sectors_as_comma_separated_sorted(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, summary, agency, sectors, tickers, impact_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "test", "tag-multi-sector", "final_rule",
                "Defense cybersecurity semiconductor standards",
                "Military artificial intelligence systems",
                "DOD",
                "", "", 4,
            ),
        )
        conn.commit()

        event_id = conn.execute(
            "SELECT id FROM regulatory_events WHERE source_id = 'tag-multi-sector'"
        ).fetchone()[0]

        tag_event(event_id, conn)

        row = conn.execute(
            "SELECT sectors FROM regulatory_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        conn.close()

        sectors = row[0].split(",")
        assert sectors == sorted(sectors)
        assert "Defense" in sectors
        assert "Technology" in sectors


class TestTagAllUntagged:
    """Test tag_all_untagged() batch processing."""

    def test_tags_untagged_events_and_returns_count(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        # Insert untagged events (sectors is empty)
        for i in range(3):
            conn.execute(
                """INSERT INTO regulatory_events
                   (source, source_id, event_type, title, summary, agency,
                    sectors, tickers, impact_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "test", f"untagged-{i}", "final_rule",
                    "Defense military regulation",
                    "Pentagon procurement update",
                    "DOD", "", "", 4,
                ),
            )
        conn.commit()

        count = tag_all_untagged(conn)
        assert count == 3

        # Verify they now have sectors
        for i in range(3):
            row = conn.execute(
                "SELECT sectors FROM regulatory_events WHERE source_id = ?",
                (f"untagged-{i}",),
            ).fetchone()
            assert row[0] != ""
            assert "Defense" in row[0]

        conn.close()

    def test_does_not_retag_already_tagged(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        # The conftest seeds events with sectors='Defense', so they should not be retagged
        already_tagged = conn.execute(
            "SELECT COUNT(*) FROM regulatory_events WHERE sectors IS NOT NULL AND sectors != ''"
        ).fetchone()[0]
        assert already_tagged > 0

        count = tag_all_untagged(conn)
        # Only NULL or empty-string sectors get retagged
        assert count == 0
        conn.close()

    def test_handles_null_sectors(self, db_path):
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        # Insert an event with NULL sectors
        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, summary, agency,
                sectors, tickers, impact_score)
               VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
            ("test", "null-sector", "final_rule", "Defense rule", "Military", "DOD", "", 4),
        )
        conn.commit()

        count = tag_all_untagged(conn)
        assert count == 1

        row = conn.execute(
            "SELECT sectors FROM regulatory_events WHERE source_id = 'null-sector'"
        ).fetchone()
        assert row[0] is not None
        conn.close()

    def test_uses_db_path_fallback_when_no_conn(self, db_path):
        """Verify tag_all_untagged falls back to DB_PATH when conn=None."""
        conn = sqlite3.connect(db_path)
        _seed_sector_keywords(conn)

        conn.execute(
            """INSERT INTO regulatory_events
               (source, source_id, event_type, title, summary, agency,
                sectors, tickers, impact_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("test", "fallback-tag", "notice", "Defense update", "Military", "DOD", "", "", 1),
        )
        conn.commit()
        conn.close()

        from unittest.mock import patch

        with patch("analysis.sector_mapper.DB_PATH", db_path):
            count = tag_all_untagged()  # No conn argument
        assert count == 1
