"""add_canonical_mjdta_22k_products

P0.1 Remediation: Correct MJDTA product identity from 24K (0.9990) to 22K (0.9160).
- Inserts canonical products MJDTA_GOLD_22K and MJDTA_GOLD_22K_GST into bullion_products.
- Soft-deprecates legacy misclassified products MJDTA_GOLD_24K and MJDTA_GOLD_24K_GST.
- Seeds bullion_latest_rates for CHENNAI MJDTA_GOLD_22K / MJDTA_GOLD_22K_GST from legacy snapshots.
- Updates prospective hindsight & forecast evaluation database triggers to support canonical 22K observations (and legacy 24K provenance).
- Provides non-destructive downgrade that soft-deactivates 22K products without violating FK constraints or immutable observation triggers.

Revision ID: c1f8a3d5e7b2
Revises: 9b4e1d8c3a02
Create Date: 2026-11-28 12:50:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1f8a3d5e7b2"
down_revision: Union[str, Sequence[str], None] = "9b4e1d8c3a02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # 1. Insert canonical 22K product definitions with exact schema columns
    op.execute("""
        INSERT INTO bullion_products (
            product_id, product_name, metal, purity, weight, unit, 
            rate_type, source_product_id, is_active
        ) VALUES 
        (
            'MJDTA_GOLD_22K', 
            'MJDTA Gold 22K Benchmark (Excl. GST)', 
            'GOLD', 
            0.9160, 
            1.0000, 
            '1g', 
            'MJDTA_BENCHMARK', 
            'MJDTA_GOLD', 
            TRUE
        ),
        (
            'MJDTA_GOLD_22K_GST', 
            'MJDTA Gold 22K Benchmark (With 3% GST)', 
            'GOLD', 
            0.9160, 
            1.0000, 
            '1g', 
            'MJDTA_BENCHMARK', 
            'MJDTA_GOLD_GST', 
            TRUE
        )
        ON CONFLICT (product_id) DO UPDATE SET
            product_name = EXCLUDED.product_name,
            purity = 0.9160,
            metal = 'GOLD',
            weight = 1.0000,
            unit = '1g',
            rate_type = 'MJDTA_BENCHMARK',
            source_product_id = EXCLUDED.source_product_id,
            is_active = TRUE;
    """)

    # 2. Soft-deprecate legacy misclassified 24K products
    op.execute("""
        UPDATE bullion_products 
        SET is_active = FALSE,
            product_name = CASE 
                WHEN product_name NOT LIKE '[DEPRECATED]%' THEN '[DEPRECATED] ' || product_name
                ELSE product_name 
            END
        WHERE product_id IN ('MJDTA_GOLD_24K', 'MJDTA_GOLD_24K_GST')
          AND is_active = TRUE;
    """)

    # 3. Synchronize bullion_latest_rates for CHENNAI
    op.execute("""
        INSERT INTO bullion_latest_rates (
            city_code, product_id, latest_price, previous_price, 
            change_inr, change_pct, currency, unit, rate_type, 
            source_published_at, last_collected_at, last_verified_at, 
            freshness_status, source_url, buy_price, normalized_price_per_gram, 
            product_weight_grams, source_product_id, validation_status
        )
        SELECT 
            city_code,
            CASE WHEN product_id = 'MJDTA_GOLD_24K' THEN 'MJDTA_GOLD_22K'
                 ELSE 'MJDTA_GOLD_22K_GST' END,
            latest_price,
            previous_price,
            change_inr,
            change_pct,
            currency,
            unit,
            rate_type,
            source_published_at,
            last_collected_at,
            last_verified_at,
            freshness_status,
            source_url,
            buy_price,
            normalized_price_per_gram,
            product_weight_grams,
            CASE WHEN product_id = 'MJDTA_GOLD_24K' THEN 'MJDTA_GOLD'
                 ELSE 'MJDTA_GOLD_GST' END,
            validation_status
        FROM bullion_latest_rates
        WHERE city_code = 'CHENNAI' 
          AND product_id IN ('MJDTA_GOLD_24K', 'MJDTA_GOLD_24K_GST')
        ON CONFLICT (city_code, product_id) DO UPDATE SET
            latest_price = EXCLUDED.latest_price,
            previous_price = EXCLUDED.previous_price,
            change_inr = EXCLUDED.change_inr,
            change_pct = EXCLUDED.change_pct,
            source_published_at = EXCLUDED.source_published_at,
            last_collected_at = EXCLUDED.last_collected_at,
            last_verified_at = EXCLUDED.last_verified_at,
            freshness_status = EXCLUDED.freshness_status,
            validation_status = EXCLUDED.validation_status;
    """)

    # 4. Update database triggers to accept canonical 22K (and legacy 24K) observations
    if dialect == "postgresql":
        op.execute("""
        CREATE OR REPLACE FUNCTION fn_guard_prospective_no_hindsight()
        RETURNS trigger AS $$
        DECLARE
            post_count integer;
            origin_pub timestamptz;
        BEGIN
            IF NEW.generation_mode = 'PROSPECTIVE' THEN
                IF NEW.target_observation_id IS NOT NULL OR NEW.target_observed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'DB Hindsight Violation: prospective forecast cannot pre-populate target observation';
                END IF;
                IF NEW.generated_at < NEW.origin_observed_at THEN
                    RAISE EXCEPTION 'DB Hindsight Violation: generated_at (%) precedes origin_observed_at (%)', NEW.generated_at, NEW.origin_observed_at;
                END IF;
                IF NEW.origin_observation_id IS NOT NULL THEN
                    SELECT source_published_at INTO origin_pub
                    FROM bullion_rate_observations
                    WHERE id = NEW.origin_observation_id;
                END IF;
                origin_pub := COALESCE(origin_pub, NEW.origin_source_published_at, NEW.origin_observed_at);

                SELECT COUNT(DISTINCT o.source_published_at) INTO post_count
                FROM bullion_rate_observations o
                JOIN bullion_products p ON p.product_id = o.product_id
                WHERE o.product_id IN ('MJDTA_GOLD_22K', 'MJDTA_GOLD_24K')
                  AND o.city_code = 'CHENNAI'
                  AND o.source_product_id = 'MJDTA_GOLD'
                  AND o.validation_status = 'VALID'
                  AND o.unit = '1g'
                  AND o.rate_type = 'MJDTA_BENCHMARK'
                  AND o.source_published_at IS NOT NULL
                  AND o.observation_kind = 'ORIGINAL'
                  AND COALESCE(o.normalized_price_per_gram, o.price) > 0
                  AND p.purity IN (0.9160, 0.9990)
                  AND p.unit = '1g'
                  AND p.rate_type = 'MJDTA_BENCHMARK'
                  AND o.observed_at > NEW.origin_observed_at
                  AND o.source_published_at > origin_pub;
                IF post_count >= NEW.horizon_publications THEN
                    RAISE EXCEPTION 'DB Hindsight Violation: % post-origin MJDTA observation(s) already exist for h=%', post_count, NEW.horizon_publications;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """)

        op.execute("""
        CREATE OR REPLACE FUNCTION fn_guard_forecast_evaluation_v2()
        RETURNS trigger AS $$
        DECLARE
            fc RECORD;
            tgt RECORD;
            origin_pub timestamptz;
            post_pub_count integer;
        BEGIN
            SELECT generation_mode, origin_observed_at, origin_source_published_at,
                   origin_observation_id, generated_at, horizon_publications,
                   model_version, protocol_version, target_instrument
            INTO fc
            FROM forecast_runs
            WHERE forecast_id = NEW.forecast_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'DB Evaluation Violation: forecast_id % not found in forecast_runs', NEW.forecast_id;
            END IF;

            IF fc.generation_mode = 'PROSPECTIVE' THEN
                IF NEW.evaluation_mode <> 'PROSPECTIVE' THEN
                    RAISE EXCEPTION 'DB Evaluation Violation: evaluation_mode mismatch for prospective forecast (%)', NEW.evaluation_mode;
                END IF;
                IF NEW.model_version <> fc.model_version OR NEW.protocol_version <> fc.protocol_version THEN
                    RAISE EXCEPTION 'DB Evaluation Violation: model/protocol version mismatch';
                END IF;

                IF NEW.target_observation_id IS NULL THEN
                    RAISE EXCEPTION 'DB Evaluation Violation: target_observation_id cannot be null for prospective evaluation';
                END IF;

                SELECT o.*, p.purity AS prod_purity, p.unit AS prod_unit, p.rate_type AS prod_rate_type
                INTO tgt
                FROM bullion_rate_observations o
                JOIN bullion_products p ON p.product_id = o.product_id
                WHERE o.id = NEW.target_observation_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'DB Evaluation Violation: target_observation_id % not found', NEW.target_observation_id;
                END IF;

                IF tgt.product_id NOT IN ('MJDTA_GOLD_22K', 'MJDTA_GOLD_24K')
                   OR tgt.city_code <> 'CHENNAI'
                   OR tgt.source_product_id <> 'MJDTA_GOLD'
                   OR tgt.validation_status <> 'VALID'
                   OR tgt.unit <> '1g'
                   OR tgt.rate_type <> 'MJDTA_BENCHMARK'
                   OR tgt.source_published_at IS NULL
                   OR tgt.observation_kind <> 'ORIGINAL'
                   OR tgt.prod_purity NOT IN (0.9160, 0.9990)
                   OR tgt.prod_unit <> '1g'
                   OR tgt.prod_rate_type <> 'MJDTA_BENCHMARK' THEN
                    RAISE EXCEPTION 'DB Evaluation Violation: target_observation_id % is not an authoritative MJDTA observation', NEW.target_observation_id;
                END IF;

                IF tgt.observed_at <= fc.origin_observed_at THEN
                    RAISE EXCEPTION 'DB Evaluation Violation: target observation (%) must be strictly after origin (%)', tgt.observed_at, fc.origin_observed_at;
                END IF;
                IF tgt.observed_at <= fc.generated_at THEN
                    RAISE EXCEPTION 'DB Evaluation Violation: target observation (%) predates forecast generation (%)', tgt.observed_at, fc.generated_at;
                END IF;
                IF NEW.matured_observed_at <> tgt.observed_at THEN
                    RAISE EXCEPTION 'DB Evaluation Violation: fabricated matured_observed_at (% <> %)', NEW.matured_observed_at, tgt.observed_at;
                END IF;
                IF NEW.evaluated_at < tgt.observed_at OR NEW.evaluated_at < fc.generated_at THEN
                    RAISE EXCEPTION 'DB Evaluation Violation: evaluated_at (%) precedes target maturity or forecast generation', NEW.evaluated_at;
                END IF;

                IF fc.origin_observation_id IS NOT NULL THEN
                    SELECT source_published_at INTO origin_pub FROM bullion_rate_observations WHERE id = fc.origin_observation_id;
                END IF;
                origin_pub := COALESCE(origin_pub, fc.origin_source_published_at, fc.origin_observed_at);

                SELECT COUNT(DISTINCT source_published_at) INTO post_pub_count
                FROM bullion_rate_observations
                WHERE product_id IN ('MJDTA_GOLD_22K', 'MJDTA_GOLD_24K')
                  AND city_code = 'CHENNAI'
                  AND source_product_id = 'MJDTA_GOLD'
                  AND validation_status = 'VALID'
                  AND unit = '1g'
                  AND rate_type = 'MJDTA_BENCHMARK'
                  AND source_published_at IS NOT NULL
                  AND observation_kind = 'ORIGINAL'
                  AND observed_at > fc.origin_observed_at
                  AND source_published_at > origin_pub
                  AND source_published_at <= tgt.source_published_at;
                IF post_pub_count <> fc.horizon_publications THEN
                    RAISE EXCEPTION 'DB Evaluation Violation: post_pub_count (%) does not match horizon_publications (%)', post_pub_count, fc.horizon_publications;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """)


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # 1. Reactivate legacy products and restore original names
    op.execute("""
        UPDATE bullion_products 
        SET is_active = TRUE,
            product_name = REPLACE(product_name, '[DEPRECATED] ', '')
        WHERE product_id IN ('MJDTA_GOLD_24K', 'MJDTA_GOLD_24K_GST');
    """)

    # 2. Synchronize latest price back to legacy snapshot
    op.execute("""
        UPDATE bullion_latest_rates target
        SET latest_price = src.latest_price,
            source_published_at = src.source_published_at,
            last_collected_at = src.last_collected_at,
            last_verified_at = src.last_verified_at,
            freshness_status = src.freshness_status
        FROM bullion_latest_rates src
        WHERE target.city_code = 'CHENNAI'
          AND target.product_id = 'MJDTA_GOLD_24K'
          AND src.city_code = 'CHENNAI'
          AND src.product_id = 'MJDTA_GOLD_22K';
    """)

    # 3. Soft-deactivate 22K products (never delete, preserving any FK references from observations)
    op.execute("""
        UPDATE bullion_products 
        SET is_active = FALSE 
        WHERE product_id IN ('MJDTA_GOLD_22K', 'MJDTA_GOLD_22K_GST');
    """)

    # 4. Remove 22K snapshots from bullion_latest_rates (safe because no FK references latest_rates)
    op.execute("""
        DELETE FROM bullion_latest_rates 
        WHERE city_code = 'CHENNAI' 
          AND product_id IN ('MJDTA_GOLD_22K', 'MJDTA_GOLD_22K_GST');
    """)
