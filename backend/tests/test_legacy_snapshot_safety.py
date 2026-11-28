from __future__ import annotations

from unittest.mock import MagicMock
from decimal import Decimal
from datetime import datetime, timezone
import pytest
from app.services.bullion_service import get_latest_city_rates, get_bullion_summary, _serialize_latest_rate
from app.models.bullion import BullionLatestRate, BullionProduct, BullionCity


def test_legacy_snapshot_filtered_by_default():
    """Verify that get_latest_city_rates excludes soft-deprecated legacy products (is_active=False) by default."""
    mock_db = MagicMock()

    # Create active 22K product and inactive 24K product
    prod_22k = BullionProduct(
        product_id="MJDTA_GOLD_22K",
        product_name="MJDTA Gold 22K Benchmark (Excl. GST)",
        metal="GOLD",
        purity=Decimal("0.9160"),
        weight=Decimal("1.0"),
        unit="1g",
        rate_type="MJDTA_BENCHMARK",
        is_active=True,
    )
    prod_24k_legacy = BullionProduct(
        product_id="MJDTA_GOLD_24K",
        product_name="[DEPRECATED] MJDTA Gold 24K Benchmark (Excl. GST)",
        metal="GOLD",
        purity=Decimal("0.9990"),
        weight=Decimal("1.0"),
        unit="1g",
        rate_type="MJDTA_BENCHMARK",
        is_active=False,
    )
    city = BullionCity(city_code="CHENNAI", display_name="Chennai", sort_order=1)

    now_utc = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
    rate_22k = BullionLatestRate(
        city_code="CHENNAI",
        product_id="MJDTA_GOLD_22K",
        latest_price=Decimal("11540.00"),
        change_inr=Decimal("0.0"),
        change_pct=Decimal("0.0"),
        currency="INR",
        unit="1g",
        rate_type="MJDTA_BENCHMARK",
        validation_status="VALID",
        last_collected_at=now_utc,
        last_verified_at=now_utc,
    )
    rate_24k = BullionLatestRate(
        city_code="CHENNAI",
        product_id="MJDTA_GOLD_24K",
        latest_price=Decimal("11540.00"),
        change_inr=Decimal("0.0"),
        change_pct=Decimal("0.0"),
        currency="INR",
        unit="1g",
        rate_type="MJDTA_BENCHMARK",
        validation_status="VALID",
        last_collected_at=now_utc,
        last_verified_at=now_utc,
    )

    # When include_deprecated=False, database returns active rows only
    mock_db.execute.return_value.all.return_value = [(rate_22k, prod_22k, city)]
    active_rates = get_latest_city_rates(city_code="CHENNAI", include_deprecated=False, db=mock_db)

    prod_ids = [r["product_id"] for r in active_rates]
    assert "MJDTA_GOLD_22K" in prod_ids
    assert "MJDTA_GOLD_24K" not in prod_ids
    assert all(r["is_active"] is True for r in active_rates)


def test_legacy_snapshot_explicit_alias_flags():
    """Verify that when deprecated rows are inspected, they carry explicit deprecation notices and legacy flags."""
    prod_24k_legacy = BullionProduct(
        product_id="MJDTA_GOLD_24K",
        product_name="[DEPRECATED] MJDTA Gold 24K Benchmark (Excl. GST)",
        metal="GOLD",
        purity=Decimal("0.9990"),
        weight=Decimal("1.0"),
        unit="1g",
        rate_type="MJDTA_BENCHMARK",
        is_active=False,
    )
    city = BullionCity(city_code="CHENNAI", display_name="Chennai", sort_order=1)
    now_utc = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
    rate_24k = BullionLatestRate(
        city_code="CHENNAI",
        product_id="MJDTA_GOLD_24K",
        latest_price=Decimal("11540.00"),
        change_inr=Decimal("0.0"),
        change_pct=Decimal("0.0"),
        currency="INR",
        unit="1g",
        rate_type="MJDTA_BENCHMARK",
        validation_status="VALID",
        last_collected_at=now_utc,
        last_verified_at=now_utc,
    )

    serialized = _serialize_latest_rate(rate_24k, prod_24k_legacy, city)
    assert serialized["is_active"] is False
    assert serialized["is_legacy_alias"] is True
    assert "Legacy product identifier" in serialized["deprecation_notice"]
    assert "DERIVED_MJDTA_GOLD_24K" in serialized["deprecation_notice"]


def test_bullion_summary_never_represents_22k_rate_as_observed_24k():
    """Verify that get_bullion_summary provides derived parity for benchmark_gold_24k rather than observed 22K rate."""
    all_rates = [
        {
            "city_code": "CHENNAI",
            "city_display_name": "Chennai",
            "product_id": "MJDTA_GOLD_22K",
            "product_name": "MJDTA Gold 22K Benchmark",
            "latest_price": 11600.0,
            "per_gram_price": 11600.0,
            "unit": "1g",
            "purity": 0.9160,
            "change_inr": 0.0,
            "change_pct": 0.0,
            "validation_status": "VALID",
            "rate_type": "MJDTA_BENCHMARK",
        }
    ]

    summary = get_bullion_summary(all_rates=all_rates)

    # 1. Observed 22K benchmark is ₹11,600/g
    assert summary["benchmark_gold_22k"]["latest_price"] == 11600.0
    assert summary["benchmark_gold_22k"]["purity"] == 0.9160

    # 2. Derived 24K parity is ₹12,663.76/g (11600 / 0.9160)
    assert summary["derived_gold_24k"]["latest_price"] == 12663.76
    assert summary["derived_gold_24k"]["is_derived"] is True

    # 3. Backward-compatible benchmark_gold_24k is derived parity with legacy notice, NEVER 11600.0
    assert summary["benchmark_gold_24k"]["latest_price"] == 12663.76
    assert summary["benchmark_gold_24k"]["legacy_alias"] is True
    assert "deprecation_notice" in summary["benchmark_gold_24k"]
