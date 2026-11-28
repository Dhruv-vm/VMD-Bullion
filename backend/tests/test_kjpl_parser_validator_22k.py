from decimal import Decimal
import pytest

from app.services.kjpl.validator import validate_bullion_observation, PRODUCT_SPECS
from app.services.kjpl.city_parser import parse_kjpl_all_city_rates


SAMPLE_KJPL_HTML = """
<html>
<body>
<div class="chennairate_table">
  <table>
    <tr><th>MJDTA RATE (WITHOUT GST)</th><th>RATE</th></tr>
    <tr><td>Gold 1GRM 22CT</td><td>11600</td></tr>
    <tr><td>Silver 1GRM</td><td>170</td></tr>
  </table>
  <table>
    <tr><th>MJDTA RATE (WITH GST)</th><th>RATE</th></tr>
    <tr><td>Gold 22CT With 3% GST</td><td>11948</td></tr>
    <tr><td>Silver With 3% GST</td><td>175.10</td></tr>
  </table>
</div>
<span class="mjdma_data">Time : 09:33 am</span>
</body>
</html>
"""


def test_validator_accepts_canonical_22k():
    res = validate_bullion_observation(
        product_id="MJDTA_GOLD_22K",
        metal="GOLD",
        currency="INR",
        unit="1g",
        weight_value=1.0,
        price=11600.0,
        purity=0.9160,
    )
    assert res.validation_status == "VALID"
    assert res.normalized_price_per_gram == 11600.0


def test_validator_quarantines_legacy_24k_classification():
    res = validate_bullion_observation(
        product_id="MJDTA_GOLD_24K",
        metal="GOLD",
        currency="INR",
        unit="1g",
        weight_value=1.0,
        price=11600.0,
        purity=0.9990,
    )
    assert res.validation_status == "QUARANTINED"
    assert "Deprecated 24K classification" in (res.reason or "")


def test_validator_quarantines_purity_mismatch():
    res = validate_bullion_observation(
        product_id="MJDTA_GOLD_22K",
        metal="GOLD",
        currency="INR",
        unit="1g",
        weight_value=1.0,
        price=11600.0,
        purity=0.9990,  # Wrong purity for 22K
    )
    assert res.validation_status == "QUARANTINED"
    assert "Purity mismatch" in (res.reason or "")


def test_city_parser_extracts_canonical_22k():
    payload = parse_kjpl_all_city_rates(SAMPLE_KJPL_HTML)
    
    # Assert MJDTA gold rates are present
    assert payload.mjdta_gold == 11600.0
    assert payload.mjdta_gold_with_gst == 11948.0

    # Assert city rates contain MJDTA_GOLD_22K
    rates_by_pid = {r.product_id: r for r in payload.city_rates}
    assert "MJDTA_GOLD_22K" in rates_by_pid
    assert "MJDTA_GOLD_22K_GST" in rates_by_pid
    assert "MJDTA_GOLD_24K" not in rates_by_pid
    assert "MJDTA_GOLD_24K_GST" not in rates_by_pid

    item_22k = rates_by_pid["MJDTA_GOLD_22K"]
    assert item_22k.purity == 0.9160
    assert item_22k.price == 11600.0
    assert item_22k.validation_status == "VALID"
    assert item_22k.city_code == "CHENNAI"

    item_22k_gst = rates_by_pid["MJDTA_GOLD_22K_GST"]
    assert item_22k_gst.purity == 0.9160
    assert item_22k_gst.price == 11948.0
    assert item_22k_gst.validation_status == "VALID"
