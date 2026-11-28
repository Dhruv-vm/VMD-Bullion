from __future__ import annotations

import os
from unittest.mock import MagicMock
from app.services.kjpl.live_rate_service import live_city_cache
from app.services.bullion_service import get_latest_city_rates, get_bullion_summary


def test_mjdta_and_wholesale_recovered_with_cached_city_feeds(monkeypatch):
    """
    Regression test for Stage 32:
    Ensures that when the database returns 0 rows (e.g. fresh/unpopulated dev DB),
    the presence of in-memory non-persistent city cache entries (IDs 48-52) does NOT
    suppress CSV fallback, allowing MJDTA_GOLD_24K and wholesale benchmarks to be populated.
    """
    # 1. Warm the live city cache with non-persistent feeds
    live_city_cache.clear()
    live_city_cache.update_rate(
        "CHENNAI",
        price=13850.0,
        source_product_id="48",
        product_id="GOLD_100G_999",
        validation_status="VALID",
    )
    live_city_cache.update_rate(
        "MUMBAI",
        price=13850.0,
        source_product_id="50",
        product_id="GOLD_100G_999",
        validation_status="VALID",
    )

    # 2. Mock DB session returning 0 rows
    mock_db = MagicMock()
    mock_db.execute.return_value.all.return_value = []
    mock_db.execute.return_value.scalars.return_value.all.return_value = []

    monkeypatch.setenv("ENVIRONMENT", "development")

    # 3. Retrieve latest city rates
    rates = get_latest_city_rates(db=mock_db)
    prod_ids = {r["product_id"] for r in rates}

    # Verify both MJDTA benchmarks (from CSV) and cached city rates coexist
    assert "MJDTA_GOLD_24K" in prod_ids, "MJDTA_GOLD_24K must be present via fallback"
    assert "GOLD_100G_999" in prod_ids, "GOLD_100G_999 must be present from cache"

    # 4. Generate summary and verify benchmark_gold_24k is not None (derived or legacy)
    summary = get_bullion_summary(all_rates=rates, db=mock_db)
    assert summary["benchmark_gold_24k"] is not None
    assert summary["benchmark_gold_24k"]["product_id"] in ("MJDTA_GOLD_24K", "DERIVED_MJDTA_GOLD_24K")
    assert summary["benchmark_gold_24k"]["latest_price"] > 0
    assert len(summary["city_tape"]) >= 2
