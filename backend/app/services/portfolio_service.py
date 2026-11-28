from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from sqlalchemy import select, delete

from app.db.session import SessionLocal, engine
from app.db.base import Base
from app.models.portfolio import PortfolioHolding
from app.models.bullion import BullionLatestRate
from app.services.data_service import get_latest_kjpl_from_csv, init_database


def get_all_benchmark_rates() -> dict[str, float | None]:
    """Retrieve verified precious metal benchmark rates in INR per gram."""
    rates = {
        "mjdta_22k": None,
        "derived_24k": None,
        "wholesale_1kg": None,
        "silver_999": None,
    }
    with SessionLocal() as db:
        try:
            mjdta_row = db.execute(
                select(BullionLatestRate)
                .where(
                    BullionLatestRate.city_code == "CHENNAI",
                    BullionLatestRate.product_id.in_(["MJDTA_GOLD_22K", "MJDTA_GOLD_24K"]),
                    BullionLatestRate.validation_status.in_(["VALID", "SUSPECT"]),
                )
                .order_by(BullionLatestRate.product_id == "MJDTA_GOLD_22K")
            ).scalars().first()
            if mjdta_row and mjdta_row.latest_price:
                rates["mjdta_22k"] = float(mjdta_row.latest_price)
                rates["derived_24k"] = round(float(Decimal(str(mjdta_row.latest_price)) / Decimal("0.9160")), 2)

            ws_row = db.execute(
                select(BullionLatestRate)
                .where(
                    BullionLatestRate.product_id == "GOLD_1KG_9999",
                    BullionLatestRate.validation_status.in_(["VALID", "SUSPECT"]),
                )
            ).scalars().first()
            if ws_row and ws_row.latest_price:
                rates["wholesale_1kg"] = float(ws_row.latest_price)

            silver_row = db.execute(
                select(BullionLatestRate)
                .where(
                    BullionLatestRate.product_id == "MJDTA_SILVER",
                    BullionLatestRate.validation_status.in_(["VALID", "SUSPECT"]),
                )
            ).scalars().first()
            if silver_row and silver_row.latest_price:
                rates["silver_999"] = float(silver_row.latest_price)
        except Exception:
            pass

    # Fallback to CSV if database snapshot unavailable
    if rates["mjdta_22k"] is None:
        kjpl = get_latest_kjpl_from_csv()
        if kjpl and kjpl.get("gold_mjdta"):
            rates["mjdta_22k"] = float(kjpl["gold_mjdta"])
            rates["derived_24k"] = round(float(Decimal(str(kjpl["gold_mjdta"])) / Decimal("0.9160")), 2)
            if kjpl.get("silver_mjdta"):
                rates["silver_999"] = float(kjpl["silver_mjdta"])

    return rates


def get_current_benchmark_rates() -> tuple[float | None, float | None]:
    """Retrieve current verified KJPL gold and silver benchmark rates (tuple for backward compatibility)."""
    rates = get_all_benchmark_rates()
    return rates["mjdta_22k"], rates["silver_999"]


def calculate_holding_valuation(
    metal_type: str,
    product_name: str,
    gross_weight_grams: float | Decimal,
    purity: float | Decimal,
    quantity: int,
    rates: dict[str, float | None],
) -> tuple[float, str, float | None, float]:
    """
    Computes holding valuation according to explicit asset benchmark rules.
    Section 6:
      - 22K jewellery -> Observed MJDTA 22K (IS 1417:2019 fineness 0.9160)
      - 24K retail coins/bars -> Derived 24K retail equivalent (0.9990 fineness)
      - Institutional 1KG bars -> Wholesale 1KG 99.99 rate (0.9999 fineness)
      - Silver items -> Observed 999 silver benchmark (0.9990 fineness)
    Returns: (current_valuation_inr, valuation_basis, rate_per_gram_used, benchmark_purity_used)
    """
    metal = metal_type.upper()
    gross_dec = Decimal(str(gross_weight_grams))
    purity_dec = Decimal(str(purity))
    qty = quantity

    if metal == "SILVER":
        silver_rate = rates.get("silver_999")
        if silver_rate is None:
            return 0.0, "MJDTA_SILVER_999", None, 0.9990
        bench_rate_dec = Decimal(str(silver_rate))
        bench_purity = Decimal("0.9990")
        val = (gross_dec * (purity_dec / bench_purity) * bench_rate_dec * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(val), "MJDTA_SILVER_999", silver_rate, 0.9990

    # Metal is GOLD
    pname_upper = (product_name or "").upper()
    is_institutional_bar = ("1KG" in pname_upper or "WHOLESALE" in pname_upper or "INSTITUTIONAL" in pname_upper) and gross_dec >= Decimal("900.0")

    if is_institutional_bar and rates.get("wholesale_1kg"):
        ws_rate = rates["wholesale_1kg"]
        bench_rate_dec = Decimal(str(ws_rate))
        bench_purity = Decimal("0.9999")
        val = (gross_dec * (purity_dec / bench_purity) * bench_rate_dec * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(val), "WHOLESALE_1KG_9999", ws_rate, 0.9999

    if purity_dec <= Decimal("0.9200"):
        # 22K jewellery, 18K, 14K -> benchmark against observed MJDTA 22K
        mjdta_rate = rates.get("mjdta_22k")
        if mjdta_rate is None:
            return 0.0, "MJDTA_GOLD_22K", None, 0.9160
        bench_rate_dec = Decimal(str(mjdta_rate))
        bench_purity = Decimal("0.9160")
        val = (gross_dec * (purity_dec / bench_purity) * bench_rate_dec * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(val), "MJDTA_GOLD_22K", mjdta_rate, 0.9160

    # 24K retail coins, bars, fine gold (purity > 0.9200) -> benchmark against Derived 24K Retail Equivalent
    derived_rate = rates.get("derived_24k")
    if derived_rate is None:
        return 0.0, "DERIVED_24K_RETAIL", None, 0.9990
    bench_rate_dec = Decimal(str(derived_rate))
    bench_purity = Decimal("0.9990")
    val = (gross_dec * (purity_dec / bench_purity) * bench_rate_dec * qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(val), "DERIVED_24K_RETAIL", derived_rate, 0.9990


class PortfolioService:
    @staticmethod
    def list_holdings(user_id: str = "default_user") -> list[dict[str, Any]]:
        init_database()
        benchmark_rates = get_all_benchmark_rates()

        with SessionLocal() as session:
            stmt = (
                select(PortfolioHolding)
                .where(PortfolioHolding.user_id == user_id)
                .order_by(PortfolioHolding.created_at.desc())
            )
            holdings = session.scalars(stmt).all()

            results = []
            for h in holdings:
                purity_f = float(h.purity)
                gross_f = float(h.gross_weight_grams)
                fine_f = float(h.fine_weight_grams)
                acq_cost_f = float(h.acquisition_cost_inr) if h.acquisition_cost_inr is not None else None

                current_valuation, basis, rate_used, bench_purity = calculate_holding_valuation(
                    metal_type=h.metal_type,
                    product_name=h.product_name,
                    gross_weight_grams=h.gross_weight_grams,
                    purity=h.purity,
                    quantity=h.quantity,
                    rates=benchmark_rates,
                )

                gain_loss_inr = None
                gain_loss_pct = None
                if acq_cost_f is not None and acq_cost_f > 0 and rate_used is not None:
                    total_cost = acq_cost_f * h.quantity
                    gain_loss_inr = round(current_valuation - total_cost, 2)
                    gain_loss_pct = round((gain_loss_inr / total_cost) * 100, 2)

                results.append({
                    "id": h.id,
                    "metal_type": h.metal_type,
                    "product_name": h.product_name,
                    "purity": purity_f,
                    "gross_weight_grams": gross_f,
                    "fine_weight_grams": fine_f,
                    "fine_weight_kg": round(fine_f / 1000.0, 4),
                    "quantity": h.quantity,
                    "acquisition_date": h.acquisition_date,
                    "acquisition_cost_inr": acq_cost_f,
                    "current_valuation_inr": current_valuation,
                    "valuation_basis": basis,
                    "benchmark_rate_used": rate_used,
                    "benchmark_purity_used": bench_purity,
                    "valuation_available": rate_used is not None,
                    "unrealized_gain_loss_inr": gain_loss_inr,
                    "unrealized_gain_loss_pct": gain_loss_pct,
                    "notes": h.notes,
                    "created_at": h.created_at.isoformat() if h.created_at else None,
                })
            return results

    @staticmethod
    def create_holding(data: dict[str, Any], user_id: str = "default_user") -> dict[str, Any]:
        init_database()
        metal = str(data.get("metal_type", "GOLD")).upper()
        if metal not in ("GOLD", "SILVER"):
            raise ValueError(f"Invalid metal_type: {metal}. Must be 'GOLD' or 'SILVER'.")

        product_name = str(data.get("product_name", "Bullion Item")).strip()
        purity = float(data.get("purity", 0.999))
        if purity > 1.0:
            purity = round(purity / 1000.0, 5)
        if purity <= 0.0 or purity > 1.0:
            raise ValueError(f"Invalid purity: {purity}. Must be between 0.0 and 1.0 (or 0 to 1000 millesimal).")

        unit = str(data.get("weight_unit", "g")).lower()
        raw_weight = float(data.get("gross_weight", 0.0))
        if raw_weight <= 0.0:
            raise ValueError("Gross weight must be greater than 0.")

        # Unit conversion: support grams and kilograms
        gross_grams = raw_weight * 1000.0 if unit in ("kg", "kilogram", "kilograms") else raw_weight
        fine_grams = round(gross_grams * purity, 4)
        quantity = int(data.get("quantity", 1))
        if quantity < 1:
            quantity = 1

        acq_cost = data.get("acquisition_cost_inr")
        acq_cost_val = float(acq_cost) if acq_cost is not None and str(acq_cost).strip() != "" else None

        holding = PortfolioHolding(
            user_id=user_id,
            metal_type=metal,
            product_name=product_name,
            purity=purity,
            gross_weight_grams=gross_grams,
            fine_weight_grams=fine_grams,
            quantity=quantity,
            acquisition_date=data.get("acquisition_date"),
            acquisition_cost_inr=acq_cost_val,
            notes=data.get("notes"),
        )

        with SessionLocal() as session:
            session.add(holding)
            session.commit()
            session.refresh(holding)
            holding_id = holding.id

        return PortfolioService.get_holding_by_id(holding_id, user_id=user_id)

    @staticmethod
    def get_holding_by_id(holding_id: int, user_id: str = "default_user") -> dict[str, Any] | None:
        holdings = PortfolioService.list_holdings(user_id=user_id)
        for h in holdings:
            if h["id"] == holding_id:
                return h
        return None

    @staticmethod
    def update_holding(holding_id: int, data: dict[str, Any], user_id: str = "default_user") -> dict[str, Any] | None:
        init_database()
        with SessionLocal() as session:
            stmt = (
                select(PortfolioHolding)
                .where(PortfolioHolding.id == holding_id, PortfolioHolding.user_id == user_id)
            )
            holding = session.scalar(stmt)
            if not holding:
                return None

            if "metal_type" in data:
                m = str(data["metal_type"]).upper()
                if m in ("GOLD", "SILVER"):
                    holding.metal_type = m
            if "product_name" in data:
                holding.product_name = str(data["product_name"]).strip()
            if "purity" in data:
                p = float(data["purity"])
                if p > 1.0:
                    p = round(p / 1000.0, 5)
                if 0.0 < p <= 1.0:
                    holding.purity = p
            if "gross_weight" in data:
                gw = float(data["gross_weight"])
                unit = str(data.get("weight_unit", "g")).lower()
                gross_grams = gw * 1000.0 if unit in ("kg", "kilogram") else gw
                if gross_grams > 0:
                    holding.gross_weight_grams = gross_grams

            # Recalculate fine weight
            holding.fine_weight_grams = round(float(holding.gross_weight_grams) * float(holding.purity), 4)

            if "quantity" in data:
                q = int(data["quantity"])
                if q >= 1:
                    holding.quantity = q
            if "acquisition_date" in data:
                holding.acquisition_date = data["acquisition_date"]
            if "acquisition_cost_inr" in data:
                val = data["acquisition_cost_inr"]
                holding.acquisition_cost_inr = float(val) if val is not None and str(val).strip() != "" else None
            if "notes" in data:
                holding.notes = data["notes"]

            holding.updated_at = datetime.now(timezone.utc)
            session.commit()

        return PortfolioService.get_holding_by_id(holding_id, user_id=user_id)

    @staticmethod
    def delete_holding(holding_id: int, user_id: str = "default_user") -> bool:
        init_database()
        with SessionLocal() as session:
            stmt = (
                delete(PortfolioHolding)
                .where(PortfolioHolding.id == holding_id, PortfolioHolding.user_id == user_id)
            )
            res = session.execute(stmt)
            session.commit()
            return res.rowcount > 0

    @staticmethod
    def get_historical_valuation(user_id: str = "default_user") -> list[dict[str, Any]]:
        """
        Evaluate user's current holdings across historical KJPL rate events.
        Produces an auditable time-series of historical portfolio valuations using
        asset-specific benchmark fineness standards.
        """
        init_database()
        holdings = PortfolioService.list_holdings(user_id=user_id)
        if not holdings:
            return []

        from app.services.data_service import KJPL_CSV
        if not KJPL_CSV.exists():
            return []

        import csv
        with KJPL_CSV.open("r", encoding="utf-8") as f:
            kjpl_records = list(csv.DictReader(f))

        history = []
        for row in kjpl_records:
            try:
                mjdta_22k_rate = float(row["gold_mjdta"])
                derived_24k_rate = round(float(Decimal(str(mjdta_22k_rate)) / Decimal("0.9160")), 2)
                silver_rate = float(row["silver_mjdta"]) if row.get("silver_mjdta") else None
                obs_date = row.get("observed_at", "")[:10]

                rates = {
                    "mjdta_22k": mjdta_22k_rate,
                    "derived_24k": derived_24k_rate,
                    "wholesale_1kg": None,
                    "silver_999": silver_rate,
                }

                total_val = 0.0
                gold_val = 0.0
                silver_val = 0.0

                for h in holdings:
                    val, basis, rate_used, _ = calculate_holding_valuation(
                        metal_type=h["metal_type"],
                        product_name=h["product_name"],
                        gross_weight_grams=h["gross_weight_grams"],
                        purity=h["purity"],
                        quantity=h["quantity"],
                        rates=rates,
                    )
                    total_val += val
                    if h["metal_type"] == "GOLD":
                        gold_val += val
                    else:
                        silver_val += val

                history.append({
                    "date": obs_date,
                    "observed_at": row.get("observed_at"),
                    "gold_rate_inr": mjdta_22k_rate,
                    "derived_24k_inr": derived_24k_rate,
                    "silver_rate_inr": silver_rate,
                    "total_valuation_inr": round(total_val, 2),
                    "gold_valuation_inr": round(gold_val, 2),
                    "silver_valuation_inr": round(silver_val, 2),
                })
            except (ValueError, KeyError):
                continue

        return history

    @staticmethod
    def get_portfolio_summary(user_id: str = "default_user") -> dict[str, Any]:
        holdings = PortfolioService.list_holdings(user_id=user_id)
        benchmark_rates = get_all_benchmark_rates()

        gold_gross = 0.0
        gold_fine = 0.0
        silver_gross = 0.0
        silver_fine = 0.0

        total_valuation = 0.0
        total_acq_cost = 0.0
        has_known_cost = False

        gold_valuation = 0.0
        silver_valuation = 0.0

        for h in holdings:
            qty = h["quantity"]
            val = h["current_valuation_inr"]
            total_valuation += val

            if h["metal_type"] == "GOLD":
                gold_gross += h["gross_weight_grams"] * qty
                gold_fine += h["fine_weight_grams"] * qty
                gold_valuation += val
            else:
                silver_gross += h["gross_weight_grams"] * qty
                silver_fine += h["fine_weight_grams"] * qty
                silver_valuation += val

            if h["acquisition_cost_inr"] is not None:
                total_acq_cost += h["acquisition_cost_inr"] * qty
                has_known_cost = True

        total_gain_loss = None
        total_gain_loss_pct = None
        if has_known_cost and total_acq_cost > 0:
            total_gain_loss = round(total_valuation - total_acq_cost, 2)
            total_gain_loss_pct = round((total_gain_loss / total_acq_cost) * 100, 2)

        gold_alloc_pct = round((gold_valuation / total_valuation) * 100, 2) if total_valuation > 0 else 0.0
        silver_alloc_pct = round((silver_valuation / total_valuation) * 100, 2) if total_valuation > 0 else 0.0

        return {
            "total_items": sum(h["quantity"] for h in holdings),
            "unique_holdings": len(holdings),
            "gold": {
                "gross_weight_grams": round(gold_gross, 2),
                "fine_weight_grams": round(gold_fine, 2),
                "fine_weight_kg": round(gold_fine / 1000.0, 4),
                "estimated_valuation_inr": round(gold_valuation, 2),
                "allocation_pct": gold_alloc_pct,
            },
            "silver": {
                "gross_weight_grams": round(silver_gross, 2),
                "fine_weight_grams": round(silver_fine, 2),
                "fine_weight_kg": round(silver_fine / 1000.0, 4),
                "estimated_valuation_inr": round(silver_valuation, 2),
                "allocation_pct": silver_alloc_pct,
            },
            "total_estimated_valuation_inr": round(total_valuation, 2),
            "total_recorded_acquisition_cost_inr": round(total_acq_cost, 2) if has_known_cost else None,
            "unrealized_gain_loss_inr": total_gain_loss,
            "unrealized_gain_loss_pct": total_gain_loss_pct,
            "benchmark_rates": {
                "gold_mjdta_22k_inr_per_gram": benchmark_rates.get("mjdta_22k"),
                "derived_gold_24k_inr_per_gram": benchmark_rates.get("derived_24k"),
                "wholesale_gold_1kg_inr_per_gram": benchmark_rates.get("wholesale_1kg"),
                "silver_mjdta_inr_per_gram": benchmark_rates.get("silver_999"),
                "gold_mjdta_inr_per_gram": benchmark_rates.get("mjdta_22k"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "disclaimer": "Valuation is estimated using Madras Jewellers and Diamond Traders Association (MJDTA) 22K benchmark rates and derived 24K retail parity. Physical bullion buyback rates typically trade at a local dealer spread.",
            },
        }
