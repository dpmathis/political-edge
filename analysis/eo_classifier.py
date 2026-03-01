"""Executive Order topic classifier for trading signal generation.

Classifies EOs by topic using keyword matching on titles and returns
trading signal metadata based on the EO Market Impact Research Report.

Research findings (522 observations, Jan 2024 - Feb 2026):
- Tariff/Trade EOs: +0.98% CAR (p=0.004) — HIGHLY SIGNIFICANT
- Defense EOs: +0.74% CAR (p=0.008) — HIGHLY SIGNIFICANT
- Sanctions EOs: +0.88% CAR (p=0.033) — SIGNIFICANT
- Healthcare EOs: -1.08% CAR (p=0.205) — NOT SIGNIFICANT
- Energy EOs: +0.66% CAR (p=0.379) — NOT SIGNIFICANT
- Technology EOs: +0.54% CAR (p=0.375) — NOT SIGNIFICANT
"""

TOPIC_KEYWORDS = {
    "tariff_trade": ["tariff", "trade", "import", "duty", "customs", "surcharge", "de minimis"],
    "sanctions": [
        "sanction", "russia", "china", "iran", "venezuela", "cuba", "libya",
        "assets control", "foreign assets",
    ],
    "defense": ["defense", "national security", "military", "armed forces", "defense production"],
    "energy": ["energy", "coal", "oil", "gas", "nuclear", "renewable", "petroleum",
               "phosphorus", "clean coal"],
    "healthcare": ["health", "drug", "pharma", "medicare", "medicaid", "fentanyl"],
    "technology": ["technolog", "cyber", "artificial intelligence", "data", "spectrum"],
}

TOPIC_TICKERS = {
    "tariff_trade": ["XOM", "BA", "LMT", "GOOGL"],
    "sanctions": ["XOM", "LMT"],
    "defense": ["LMT", "RTX", "GD", "NOC", "BA"],
    "energy": ["XOM", "NEE"],
    "healthcare": ["UNH", "HUM", "PFE", "LLY"],
    "technology": ["GOOGL", "META"],
}

TOPIC_DIRECTION = {
    "tariff_trade": "long",
    "sanctions": "long",
    "defense": "long",
    "energy": "long",
    "healthcare": "short",
    "technology": "long",
}

TOPIC_EXPECTED_CAR = {
    "tariff_trade": 0.0098,
    "defense": 0.0074,
    "sanctions": 0.0088,
    "energy": 0.0066,
    "healthcare": -0.0108,
    "technology": 0.0054,
}

TOPIC_CONFIDENCE = {
    "tariff_trade": "high",   # p = 0.004
    "defense": "high",        # p = 0.008
    "sanctions": "medium",    # p = 0.033
    "energy": "low",          # p = 0.379
    "healthcare": "low",      # p = 0.205
    "technology": "low",      # p = 0.375
}

# Sample sizes from the research report
TOPIC_SAMPLE_SIZE = {
    "tariff_trade": 147,
    "defense": 158,
    "sanctions": 57,
    "healthcare": 108,
    "energy": 23,
    "technology": 29,
}

IMPOSITION_KEYWORDS = ["imposing", "increasing", "surcharge", "restricting", "modifying duties"]
RELIEF_KEYWORDS = ["ending", "reducing", "deal", "pause", "exemption", "waiver", "suspension"]


def classify_eo(title: str) -> dict:
    """Classify an executive order by topic and return trading signal metadata.

    Args:
        title: The executive order title from the Federal Register.

    Returns:
        dict with keys: topic, tickers, direction, expected_car, confidence,
                        is_tradeable, tariff_direction, sample_size
    """
    title_lower = title.lower()

    topic = "other"
    for t, keywords in TOPIC_KEYWORDS.items():
        if any(k in title_lower for k in keywords):
            topic = t
            break

    if topic == "other":
        return {
            "topic": "other",
            "tickers": [],
            "direction": None,
            "expected_car": None,
            "confidence": None,
            "is_tradeable": False,
            "tariff_direction": None,
            "sample_size": None,
        }

    # For tariff EOs, determine imposition vs relief
    tariff_dir = None
    if topic == "tariff_trade":
        if any(k in title_lower for k in RELIEF_KEYWORDS):
            tariff_dir = "relief"
        elif any(k in title_lower for k in IMPOSITION_KEYWORDS):
            tariff_dir = "imposition"
        else:
            tariff_dir = "neutral"

    return {
        "topic": topic,
        "tickers": TOPIC_TICKERS.get(topic, []),
        "direction": TOPIC_DIRECTION.get(topic, "long"),
        "expected_car": TOPIC_EXPECTED_CAR.get(topic),
        "confidence": TOPIC_CONFIDENCE.get(topic, "low"),
        "is_tradeable": TOPIC_CONFIDENCE.get(topic) in ("high", "medium"),
        "tariff_direction": tariff_dir,
        "sample_size": TOPIC_SAMPLE_SIZE.get(topic),
    }
