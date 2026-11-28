from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
import pytest

from app.services.data_service import get_freshness_details
from app.services.bullion_service import (
    get_latest_city_rates,
    get_bullion_summary,
    _serialize_latest_rate,
)
from app.services.kjpl.live_rate_service import CityLiveRateCache
from app.models.bullion import BullionLatestRate, BullionProduct, BullionCity


def test_freshness_evaluation_matrix():
    """Verify freshness state evaluation across feed cadences and edge states."""
    now = datetime.now(timezone.utc)

    # 1. Fresh MJDTA observation (<16h)
    fresh_obs = now - timedelta(hours=2)
    res = get_freshness_details(fresh_obs, rate_type="MJDTA_BENCHMARK")
    assert res["status"] == "FRESH"
    assert res["age_hours"] < 16.0

    # 2. Delayed MJDTA observation (16h - 36h)
    delayed_obs = now - timedelta(hours=20)
    res = get_freshness_details(delayed_obs, rate_type="MJDTA_BENCHMARK")
    assert res["status"] == "DELAYED"

    # 3. Stale MJDTA observation (>36h)
    stale_obs = now - timedelta(hours=48)
    res = get_freshness_details(stale_obs, rate_type="MJDTA_BENCHMARK")
    assert res["status"] == "STALE"
    assert res["age_hours"] >= 36.0

    # 4. Live feed quote (<120s)
    live_feed_obs = now - timedelta(seconds=45)
    res = get_freshness_details(live_feed_obs, rate_type="RETAIL")
    assert res["status"] == "LIVE"

    # 5. Stale live feed quote (>24h)
    stale_feed_obs = now - timedelta(hours=26)
    res = get_freshness_details(stale_feed_obs, rate_type="RETAIL")
    assert res["status"] == "STALE"

    # 6. Missing observation timestamp
    res = get_freshness_details(None, rate_type="MJDTA_BENCHMARK")
    assert res["status"] == "STALE"
    assert res["age_hours"] is None
    assert res["age_display"] == "Unknown timestamp"

    # 7. Explicit Source Unavailable
    res = get_freshness_details(now, source_unavailable=True)
    assert res["status"] == "SOURCE_UNAVAILABLE"
    assert res["age_display"] == "Source unavailable"

    # 8. Explicit Warming Up
    res = get_freshness_details(now, is_warming_up=True)
    assert res["status"] == "WARMING_UP"
    assert res["age_display"] == "Warming up"

    # 9. Explicit Reconnecting
    res = get_freshness_details(now, is_reconnecting=True)
    assert res["status"] == "RECONNECTING"
    assert res["age_display"] == "Reconnecting"


def test_freshness_boundary_values_and_cadence_intervals():
    """Verify exact boundary values and interval behavior for benchmark and live feeds without clock jitter."""
    fixed_now = datetime(2026, 11, 28, 12, 0, 0, tzinfo=timezone.utc)

    with patch("app.services.data_service.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)

        # --- BENCHMARK CADENCE (Twice-daily, 16h / 36h) ---
        # Boundary: Exactly 16.0 hours -> FRESH
        at_16h = fixed_now - timedelta(hours=16.0)
        res = get_freshness_details(at_16h, rate_type="MJDTA_BENCHMARK")
        assert res["status"] == "FRESH"
        assert res["age_hours"] == 16.0

        # Intermediate interval: 16.01 hours -> DELAYED (Never Connected)
        at_16_01h = fixed_now - timedelta(hours=16.01)
        res = get_freshness_details(at_16_01h, rate_type="MJDTA_BENCHMARK")
        assert res["status"] == "DELAYED"
        assert res["age_hours"] == 16.01

        # Boundary: Exactly 36.0 hours -> DELAYED
        at_36h = fixed_now - timedelta(hours=36.0)
        res = get_freshness_details(at_36h, rate_type="MJDTA_BENCHMARK")
        assert res["status"] == "DELAYED"
        assert res["age_hours"] == 36.0

        # Beyond 36 hours: 36.01 hours -> STALE
        at_36_01h = fixed_now - timedelta(hours=36.01)
        res = get_freshness_details(at_36_01h, rate_type="MJDTA_BENCHMARK")
        assert res["status"] == "STALE"
        assert res["age_hours"] == 36.01

        # --- LIVE FEED CADENCE (Streaming tick 120s, intraday 4h / 24h) ---
        # Boundary: Exactly 120 seconds -> LIVE
        at_120s = fixed_now - timedelta(seconds=120)
        res = get_freshness_details(at_120s, rate_type="RETAIL")
        assert res["status"] == "LIVE"

        # Beyond 120s: 121 seconds -> FRESH
        at_121s = fixed_now - timedelta(seconds=121)
        res = get_freshness_details(at_121s, rate_type="RETAIL")
        assert res["status"] == "FRESH"

        # Boundary: Exactly 4.0 hours -> FRESH
        at_4h = fixed_now - timedelta(hours=4.0)
        res = get_freshness_details(at_4h, rate_type="RETAIL")
        assert res["status"] == "FRESH"
        assert res["age_hours"] == 4.0

        # Intermediate interval: 4.01 hours -> DELAYED (Never Connected)
        at_4_01h = fixed_now - timedelta(hours=4.01)
        res = get_freshness_details(at_4_01h, rate_type="RETAIL")
        assert res["status"] == "DELAYED"
        assert res["age_hours"] == 4.01

        # Boundary: Exactly 24.0 hours -> DELAYED
        at_24h = fixed_now - timedelta(hours=24.0)
        res = get_freshness_details(at_24h, rate_type="RETAIL")
        assert res["status"] == "DELAYED"
        assert res["age_hours"] == 24.0

        # Beyond 24 hours: 24.01 hours -> STALE
        at_24_01h = fixed_now - timedelta(hours=24.01)
        res = get_freshness_details(at_24_01h, rate_type="RETAIL")
        assert res["status"] == "STALE"
        assert res["age_hours"] == 24.01

        # --- MISSING TIMESTAMP ---
        res_none = get_freshness_details(None, rate_type="MJDTA_BENCHMARK")
        assert res_none["status"] == "STALE"
        assert res_none["age_display"] == "Unknown timestamp"

    # --- SYSTEM STATUS INTERMEDIATE SAFEGUARDS ---
    # Ensure DELAYED observations are NEVER classified as Connected in system_status
    now = datetime.now(timezone.utc)
    delayed_benchmark_rate = [
        {
            "city_code": "CHENNAI",
            "city_display_name": "Chennai",
            "product_id": "MJDTA_GOLD_22K",
            "product_name": "MJDTA Gold 22K Benchmark",
            "latest_price": 11540.0,
            "per_gram_price": 11540.0,
            "unit": "1g",
            "purity": 0.9160,
            "change_inr": 0.0,
            "change_pct": 0.0,
            "validation_status": "VALID",
            "rate_type": "MJDTA_BENCHMARK",
            "freshness": "DELAYED",
            "age_display": "22h ago",
            "source_published_at": (now - timedelta(hours=22)).isoformat(),
            "source_updated_time": "27 Nov 03:00 PM IST",
            "observed_at": (now - timedelta(hours=22)).isoformat(),
            "last_collected_at": (now - timedelta(hours=22)).isoformat(),
        }
    ]
    summary_delayed = get_bullion_summary(all_rates=delayed_benchmark_rate)
    assert summary_delayed["system_status"] == "Stale"
    assert summary_delayed["system_status"] != "Connected"


def test_no_timestamp_fabrication_in_serialization():
    """Verify that _serialize_latest_rate never uses current time as a substitute for observation time."""
    prod = BullionProduct(
        product_id="MJDTA_GOLD_22K",
        product_name="MJDTA Gold 22K (1g)",
        metal="GOLD",
        purity=Decimal("0.9160"),
        weight=Decimal("1.0"),
        unit="1g",
        rate_type="MJDTA_BENCHMARK",
        is_active=True,
    )
    city = BullionCity(city_code="CHENNAI", display_name="Chennai", sort_order=1)

    # Historical collection date
    hist_coll = datetime(2026, 11, 26, 10, 20, tzinfo=timezone.utc)
    hist_pub = datetime(2026, 11, 26, 4, 26, tzinfo=timezone.utc)

    rate = BullionLatestRate(
        city_code="CHENNAI",
        product_id="MJDTA_GOLD_22K",
        latest_price=Decimal("11540.00"),
        change_inr=Decimal("0.0"),
        change_pct=Decimal("0.0"),
        currency="INR",
        unit="1g",
        rate_type="MJDTA_BENCHMARK",
        validation_status="VALID",
        source_published_at=hist_pub,
        last_collected_at=hist_coll,
        last_verified_at=datetime.now(timezone.utc),  # Verified just now
    )

    serialized = _serialize_latest_rate(rate, prod, city)

    # 1. observed_at matches exact collection date, NOT current time
    assert serialized["observed_at"] == hist_coll.isoformat()
    assert serialized["source_published_at"] == hist_pub.isoformat()
    assert serialized["last_collected_at"] == hist_coll.isoformat()

    # 2. Freshness is evaluated against published/collected time, NOT last_verified_at
    # Since published date is Nov 26 and current time is Nov 28 (>36h), freshness MUST be STALE
    assert serialized["freshness"] == "STALE"


def test_missing_timestamp_never_substitutes_now():
    """Verify that when last_collected_at and source_published_at are None, observed_at is None."""
    prod = BullionProduct(
        product_id="MJDTA_GOLD_22K",
        product_name="MJDTA Gold 22K (1g)",
        metal="GOLD",
        purity=Decimal("0.9160"),
        weight=Decimal("1.0"),
        unit="1g",
        rate_type="MJDTA_BENCHMARK",
        is_active=True,
    )
    city = BullionCity(city_code="CHENNAI", display_name="Chennai", sort_order=1)

    rate = BullionLatestRate(
        city_code="CHENNAI",
        product_id="MJDTA_GOLD_22K",
        latest_price=Decimal("11540.00"),
        change_inr=Decimal("0.0"),
        change_pct=Decimal("0.0"),
        currency="INR",
        unit="1g",
        rate_type="MJDTA_BENCHMARK",
        validation_status="VALID",
        source_published_at=None,
        last_collected_at=None,
        last_verified_at=None,
    )

    serialized = _serialize_latest_rate(rate, prod, city)
    assert serialized["observed_at"] is None
    assert serialized["last_collected_at"] is None
    assert serialized["freshness"] == "STALE"


def test_system_status_and_observation_provenance():
    """Verify get_bullion_summary exposes the 5 explicit system states and observation provenance."""
    # 1. Stale state when benchmark observation is older than 36h
    stale_rates = [
        {
            "city_code": "CHENNAI",
            "city_display_name": "Chennai",
            "product_id": "MJDTA_GOLD_22K",
            "product_name": "MJDTA Gold 22K Benchmark",
            "latest_price": 11540.0,
            "per_gram_price": 11540.0,
            "unit": "1g",
            "purity": 0.9160,
            "change_inr": 0.0,
            "change_pct": 0.0,
            "validation_status": "VALID",
            "rate_type": "MJDTA_BENCHMARK",
            "freshness": "STALE",
            "age_display": "2d 3h ago",
            "source_published_at": "2026-11-26T04:26:14+00:00",
            "source_updated_time": "26 Nov 09:56 AM IST",
            "observed_at": "2026-11-26T10:20:39.652053+00:00",
            "last_collected_at": "2026-11-26T10:20:39.652053+00:00",
        }
    ]

    summary_stale = get_bullion_summary(all_rates=stale_rates)
    assert summary_stale["system_status"] == "Stale"
    assert summary_stale["last_successful_update_ist"] == "26 Nov 09:56 AM IST"
    obs = summary_stale["last_successful_observation"]
    assert obs is not None
    assert obs["published_at"] == "2026-11-26T04:26:14+00:00"
    assert obs["updated_time_ist"] == "26 Nov 09:56 AM IST"
    assert obs["age_display"] == "2d 3h ago"
    assert obs["system_status"] == "Stale"

    # 2. Connected state when benchmark observation is fresh (<16h)
    fresh_rates = [
        {
            "city_code": "CHENNAI",
            "city_display_name": "Chennai",
            "product_id": "MJDTA_GOLD_22K",
            "product_name": "MJDTA Gold 22K Benchmark",
            "latest_price": 11800.0,
            "per_gram_price": 11800.0,
            "unit": "1g",
            "purity": 0.9160,
            "change_inr": 15.0,
            "change_pct": 0.12,
            "validation_status": "VALID",
            "rate_type": "MJDTA_BENCHMARK",
            "freshness": "FRESH",
            "age_display": "1h 10m ago",
            "source_published_at": datetime.now(timezone.utc).isoformat(),
            "source_updated_time": "28 Nov 10:00 AM IST",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "last_collected_at": datetime.now(timezone.utc).isoformat(),
        }
    ]

    summary_fresh = get_bullion_summary(all_rates=fresh_rates)
    assert summary_fresh["system_status"] == "Connected"
    assert summary_fresh["last_successful_observation"]["system_status"] == "Connected"


def test_live_city_cache_warming_and_expiry():
    """Verify in-memory cache warming, TTL freshness expiry, and cold-state handling."""
    cache = CityLiveRateCache(ttl_seconds=2.0)
    assert not cache.is_warm()
    assert not cache.is_fresh()

    # Update valid rate for Chennai
    ok, changed = cache.update_rate(
        "CHENNAI",
        price=12500.0,
        source_product_id="48",
        product_id="GOLD_100G_999",
        validation_status="VALID",
    )
    assert ok is True
    assert changed is True
    assert cache.is_warm() is True
    cache.mark_fetched()
    assert cache.is_fresh() is True

    # Rate retrieval preserves last_collected_at
    rate = cache.get_rate("CHENNAI")
    assert rate is not None
    assert rate["latest_price"] == 12500.0
    assert rate["last_collected_at"] is not None

    # Clear resets cache to cold state without fabricating historical rates
    cache.clear()
    assert not cache.is_warm()
    assert not cache.is_fresh()
    assert cache.get_all_rates() == []
