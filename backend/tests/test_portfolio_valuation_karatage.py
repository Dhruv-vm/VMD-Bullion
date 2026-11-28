from decimal import Decimal
import pytest

from app.services.portfolio_service import calculate_holding_valuation


SAMPLE_RATES = {
    "mjdta_22k": 11600.0,
    "derived_24k": 12663.75,  # 11600 / 0.9160
    "wholesale_1kg": 13014.80,
    "silver_999": 170.0,
}


def test_22k_jewellery_valuation():
    # 10g of 22K (0.9160 purity) chain
    val, basis, rate_used, bench_purity = calculate_holding_valuation(
        metal_type="GOLD",
        product_name="22K Gold Chain",
        gross_weight_grams=10.0,
        purity=0.9160,
        quantity=1,
        rates=SAMPLE_RATES,
    )
    # Expected: 10 * (0.9160 / 0.9160) * 11600 = 116,000.00
    assert basis == "MJDTA_GOLD_22K"
    assert rate_used == 11600.0
    assert bench_purity == 0.9160
    assert val == 116000.00


def test_24k_retail_coin_valuation():
    # 10g of 24K (0.9990 purity) retail coin
    val, basis, rate_used, bench_purity = calculate_holding_valuation(
        metal_type="GOLD",
        product_name="24K Sovereign Coin",
        gross_weight_grams=10.0,
        purity=0.9990,
        quantity=1,
        rates=SAMPLE_RATES,
    )
    # Expected: 10 * (0.9990 / 0.9990) * 12663.75 = 126,637.50
    assert basis == "DERIVED_24K_RETAIL"
    assert rate_used == 12663.75
    assert bench_purity == 0.9990
    assert val == 126637.50


def test_institutional_1kg_bar_valuation():
    # 1000g of 24K 99.99 institutional bar
    val, basis, rate_used, bench_purity = calculate_holding_valuation(
        metal_type="GOLD",
        product_name="KJPL 1KG 99.99 Wholesale Bar",
        gross_weight_grams=1000.0,
        purity=0.9999,
        quantity=1,
        rates=SAMPLE_RATES,
    )
    # Expected: 1000 * (0.9999 / 0.9999) * 13014.80 = 13,014,800.00
    assert basis == "WHOLESALE_1KG_9999"
    assert rate_used == 13014.80
    assert bench_purity == 0.9999
    assert val == 13014800.00


def test_silver_item_valuation():
    # 100g of 999 silver bar
    val, basis, rate_used, bench_purity = calculate_holding_valuation(
        metal_type="SILVER",
        product_name="999 Silver Coin",
        gross_weight_grams=100.0,
        purity=0.9990,
        quantity=1,
        rates=SAMPLE_RATES,
    )
    # Expected: 100 * (0.9990 / 0.9990) * 170.0 = 17,000.00
    assert basis == "MJDTA_SILVER_999"
    assert rate_used == 170.0
    assert bench_purity == 0.9990
    assert val == 17000.00
