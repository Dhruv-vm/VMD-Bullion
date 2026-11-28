from decimal import Decimal
import pytest

from app.services.bullion_service import get_bullion_summary


def test_derived_24k_parity_formula():
    mjdta_22k = Decimal("11600.00")
    purity_22k = Decimal("0.9160")
    derived_24k = (mjdta_22k / purity_22k).quantize(Decimal("0.01"))
    
    assert derived_24k == Decimal("12663.76")
    # Verify standard float round (11600 / 0.9160 = 12663.755458... -> 12663.76)
    assert round(float(mjdta_22k / purity_22k), 2) == 12663.76


def test_bullion_summary_includes_derived_and_canonical_benchmarks():
    mock_rates = [
        {
            "city_code": "CHENNAI",
            "product_id": "MJDTA_GOLD_22K",
            "product_name": "MJDTA Gold 22K Benchmark (Excl. GST)",
            "metal": "GOLD",
            "purity": 0.9160,
            "unit": "1g",
            "latest_price": 11600.0,
            "per_gram_price": 11600.0,
            "change_inr": 60.0,
            "change_pct": 0.52,
            "source_updated_time": "Time : 09:33 am",
            "freshness": "FRESH",
            "validation_status": "VALID",
        },
        {
            "city_code": "CHENNAI",
            "product_id": "MJDTA_GOLD_22K_GST",
            "product_name": "MJDTA Gold 22K Benchmark (With 3% GST)",
            "metal": "GOLD",
            "purity": 0.9160,
            "unit": "1g",
            "latest_price": 11948.0,
            "per_gram_price": 11948.0,
            "change_inr": 61.8,
            "change_pct": 0.52,
            "source_updated_time": "Time : 09:33 am",
            "freshness": "FRESH",
            "validation_status": "VALID",
        },
        {
            "city_code": "CHENNAI",
            "product_id": "MJDTA_SILVER",
            "metal": "SILVER",
            "purity": 0.9990,
            "unit": "1g",
            "latest_price": 170.0,
            "per_gram_price": 170.0,
            "change_inr": 0.0,
            "change_pct": 0.0,
            "freshness": "FRESH",
            "validation_status": "VALID",
        },
        {
            "city_code": "CHENNAI",
            "product_id": "KJPL_SPOT_GOLD_USD",
            "latest_price": 3900.0,
            "freshness": "FRESH",
        },
        {
            "city_code": "CHENNAI",
            "product_id": "KJPL_SPOT_USDINR",
            "latest_price": 84.50,
            "freshness": "FRESH",
        },
    ]

    summary = get_bullion_summary(all_rates=mock_rates)

    # 1. Canonical 22K
    assert summary["benchmark_gold_22k"] is not None
    assert summary["benchmark_gold_22k"]["product_id"] == "MJDTA_GOLD_22K"
    assert summary["benchmark_gold_22k"]["latest_price"] == 11600.0

    # 2. Derived 24K
    assert summary["derived_gold_24k"] is not None
    assert summary["derived_gold_24k"]["is_derived"] is True
    assert summary["derived_gold_24k"]["latest_price"] == 12663.76
    assert summary["derived_gold_24k"]["purity"] == 0.9990
    assert "0.9160" in summary["derived_gold_24k"]["derivation_formula"]

    # 3. Legacy compatibility alias
    assert summary["benchmark_gold_24k"] is not None
    assert summary["benchmark_gold_24k"]["is_derived"] is True
    assert summary["benchmark_gold_24k"]["legacy_alias"] is True
    assert summary["benchmark_gold_24k"]["latest_price"] == 12663.76

    # 4. Parity info compares like-for-like 24K equivalent vs 24K spot
    assert summary["parity"] is not None
    assert summary["parity"]["raw_spot_inr_per_gram"] == 10595.28  # (3900 * 84.50) / 31.1034768
    assert summary["parity"]["derived_retail_parity_spread_inr"] == 2068.48  # 12663.76 - 10595.28
    assert summary["parity"]["derived_retail_parity_spread_pct"] == 19.52
