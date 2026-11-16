BEGIN;
SET TRANSACTION ISOLATION LEVEL SERIALIZABLE;

DO $$
DECLARE
    v_tx_now timestamptz := CURRENT_TIMESTAMP;
    v_manifest_match_count integer;
    v_manifest_mismatch integer;
    v_lr_keys_before integer;
    v_lr_dup_before integer;
    v_lr_invalid_count integer;
    v_target_superseding_total integer;
    v_rogue_non_corr integer;
    v_bad_revision_corr integer;
    v_inserted_corr integer := 0;
    v_updated_latest integer := 0;
    v_corr RECORD;
    v_target_corr_count integer;
    v_lr_multi_corr integer;
    v_lr RECORD;
    v_future_corr_count integer;
    v_orig_id integer;
    v_single_corr_count integer;
BEGIN
    -- 1. Advisory Lock & Row-Level Concurrency Locks
    PERFORM pg_advisory_xact_lock(918273645);
    PERFORM 1 FROM public.bullion_rate_observations WHERE id BETWEEN 1 AND 28 ORDER BY id FOR SHARE;
    PERFORM 1 FROM public.bullion_latest_rates ORDER BY id FOR UPDATE;

    -- 2. Temporary Expected Manifest for Original Observations 7-28
    -- Exhaustively specifies all business, provenance, and timestamp attributes.
    CREATE TEMP TABLE _expected_manifest (
        id integer PRIMARY KEY,
        city_code text NOT NULL,
        product_id text NOT NULL,
        source_product_id text,
        source_name text NOT NULL,
        raw_source_name text NOT NULL,
        price numeric(12, 4) NOT NULL,
        buy_price numeric(12, 4),
        currency text NOT NULL,
        unit text NOT NULL,
        product_weight_grams numeric(10, 4) NOT NULL,
        rate_type text NOT NULL,
        raw_source_published_time text NOT NULL,
        expected_erroneous_source_published_at timestamptz NOT NULL,
        expected_source_published_at timestamptz NOT NULL,
        run_name text NOT NULL
    ) ON COMMIT DROP;

    INSERT INTO _expected_manifest VALUES
    (7, 'ALL_INDIA', 'GOLD_1KG_9999', '47', 'KJPL', 'GOLD 1KG 99.99', 14241.70, NULL, 'INR', '1g', 1.0000, 'WHOLESALE', '11/12/2026 01:44:14 PM', TIMESTAMPTZ '2026-12-11 08:14:14+00', TIMESTAMPTZ '2026-11-12 08:14:14+00', 'Run 2'),
    (8, 'CHENNAI', 'GOLD_100G_999', '48', 'KJPL', 'GOLD 100 GMS 999 Chennai', 14224.70, NULL, 'INR', '1g', 1.0000, 'RETAIL', '11/12/2026 01:44:14 PM', TIMESTAMPTZ '2026-12-11 08:14:14+00', TIMESTAMPTZ '2026-11-12 08:14:14+00', 'Run 2'),
    (9, 'BENGALURU', 'GOLD_100G_999', '49', 'KJPL', 'Gold 100 GMS 999 Bangalore', 14224.70, NULL, 'INR', '1g', 1.0000, 'RETAIL', '11/12/2026 01:44:14 PM', TIMESTAMPTZ '2026-12-11 08:14:14+00', TIMESTAMPTZ '2026-11-12 08:14:14+00', 'Run 2'),
    (10, 'MUMBAI', 'GOLD_100G_999', '50', 'KJPL', 'Gold 100 GMS 999 Mumbai', 14224.70, NULL, 'INR', '1g', 1.0000, 'RETAIL', '11/12/2026 01:44:14 PM', TIMESTAMPTZ '2026-12-11 08:14:14+00', TIMESTAMPTZ '2026-11-12 08:14:14+00', 'Run 2'),
    (11, 'COIMBATORE', 'GOLD_100G_999', '51', 'KJPL', 'Gold 100 GMS 999 Coimbatore', 14224.70, NULL, 'INR', '1g', 1.0000, 'RETAIL', '11/12/2026 01:44:14 PM', TIMESTAMPTZ '2026-12-11 08:14:14+00', TIMESTAMPTZ '2026-11-12 08:14:14+00', 'Run 2'),
    (12, 'HYDERABAD', 'GOLD_100G_999', '52', 'KJPL', 'GOLD 100 GMS 999 Hyderabad', 14224.70, NULL, 'INR', '1g', 1.0000, 'RETAIL', '11/12/2026 01:44:14 PM', TIMESTAMPTZ '2026-12-11 08:14:14+00', TIMESTAMPTZ '2026-11-12 08:14:14+00', 'Run 2'),
    (13, 'ALL_INDIA', 'KJPL_GOLDDEC_INR_10G', 'GOLDDEC', 'KJPL', 'GOLDDEC', 138500.00, 138468.00, 'INR', '10g', 10.0000, 'KJPL_LIVE', '11/12/2026 01:44:14 PM', TIMESTAMPTZ '2026-12-11 08:14:14+00', TIMESTAMPTZ '2026-11-12 08:14:14+00', 'Run 2'),
    (14, 'ALL_INDIA', 'KJPL_SILVERDEC_INR_1KG', 'SILVERDEC', 'KJPL', 'SILVERDEC', 182700.00, 182658.00, 'INR', '1kg', 1000.0000, 'KJPL_LIVE', '11/12/2026 01:44:14 PM', TIMESTAMPTZ '2026-12-11 08:14:14+00', TIMESTAMPTZ '2026-11-12 08:14:14+00', 'Run 2'),
    (15, 'ALL_INDIA', 'KJPL_SPOT_GOLD_USD', 'SPOT-GOLD', 'KJPL', 'SPOT-GOLD', 3958.01, 3957.76, 'USD', 'oz', 31.1035, 'KJPL_LIVE', '11/12/2026 01:44:14 PM', TIMESTAMPTZ '2026-12-11 08:14:14+00', TIMESTAMPTZ '2026-11-12 08:14:14+00', 'Run 2'),
    (16, 'ALL_INDIA', 'KJPL_SPOT_SILVER_USD', 'SPOT-SILVER', 'KJPL', 'SPOT-SILVER', 51.50, 51.48, 'USD', 'oz', 31.1035, 'KJPL_LIVE', '11/12/2026 01:44:14 PM', TIMESTAMPTZ '2026-12-11 08:14:14+00', TIMESTAMPTZ '2026-11-12 08:14:14+00', 'Run 2'),
    (17, 'ALL_INDIA', 'KJPL_SPOT_USDINR', 'SPOT-INR', 'KJPL', 'SPOT-INR', 93.94, 93.93, 'INR', 'USD', 1.0000, 'KJPL_LIVE', '11/12/2026 01:44:14 PM', TIMESTAMPTZ '2026-12-11 08:14:14+00', TIMESTAMPTZ '2026-11-12 08:14:14+00', 'Run 2'),
    (18, 'ALL_INDIA', 'GOLD_1KG_9999', '47', 'KJPL', 'GOLD 1KG 99.99', 14183.30, NULL, 'INR', '1g', 1.0000, 'WHOLESALE', '11/12/2026 02:18:44 PM', TIMESTAMPTZ '2026-12-11 08:48:44+00', TIMESTAMPTZ '2026-11-12 08:48:44+00', 'Run 4'),
    (19, 'CHENNAI', 'GOLD_100G_999', '48', 'KJPL', 'GOLD 100 GMS 999 Chennai', 14166.30, NULL, 'INR', '1g', 1.0000, 'RETAIL', '11/12/2026 02:18:44 PM', TIMESTAMPTZ '2026-12-11 08:48:44+00', TIMESTAMPTZ '2026-11-12 08:48:44+00', 'Run 4'),
    (20, 'BENGALURU', 'GOLD_100G_999', '49', 'KJPL', 'Gold 100 GMS 999 Bangalore', 14166.30, NULL, 'INR', '1g', 1.0000, 'RETAIL', '11/12/2026 02:18:44 PM', TIMESTAMPTZ '2026-12-11 08:48:44+00', TIMESTAMPTZ '2026-11-12 08:48:44+00', 'Run 4'),
    (21, 'MUMBAI', 'GOLD_100G_999', '50', 'KJPL', 'Gold 100 GMS 999 Mumbai', 14166.30, NULL, 'INR', '1g', 1.0000, 'RETAIL', '11/12/2026 02:18:44 PM', TIMESTAMPTZ '2026-12-11 08:48:44+00', TIMESTAMPTZ '2026-11-12 08:48:44+00', 'Run 4'),
    (22, 'COIMBATORE', 'GOLD_100G_999', '51', 'KJPL', 'Gold 100 GMS 999 Coimbatore', 14166.30, NULL, 'INR', '1g', 1.0000, 'RETAIL', '11/12/2026 02:18:44 PM', TIMESTAMPTZ '2026-12-11 08:48:44+00', TIMESTAMPTZ '2026-11-12 08:48:44+00', 'Run 4'),
    (23, 'HYDERABAD', 'GOLD_100G_999', '52', 'KJPL', 'GOLD 100 GMS 999 Hyderabad', 14166.30, NULL, 'INR', '1g', 1.0000, 'RETAIL', '11/12/2026 02:18:44 PM', TIMESTAMPTZ '2026-12-11 08:48:44+00', TIMESTAMPTZ '2026-11-12 08:48:44+00', 'Run 4'),
    (24, 'ALL_INDIA', 'KJPL_GOLDDEC_INR_10G', 'GOLDDEC', 'KJPL', 'GOLDDEC', 137913.00, 137881.00, 'INR', '10g', 10.0000, 'KJPL_LIVE', '11/12/2026 02:18:44 PM', TIMESTAMPTZ '2026-12-11 08:48:44+00', TIMESTAMPTZ '2026-11-12 08:48:44+00', 'Run 4'),
    (25, 'ALL_INDIA', 'KJPL_SILVERDEC_INR_1KG', 'SILVERDEC', 'KJPL', 'SILVERDEC', 181622.00, 181555.00, 'INR', '1kg', 1000.0000, 'KJPL_LIVE', '11/12/2026 02:18:44 PM', TIMESTAMPTZ '2026-12-11 08:48:44+00', TIMESTAMPTZ '2026-11-12 08:48:44+00', 'Run 4'),
    (26, 'ALL_INDIA', 'KJPL_SPOT_GOLD_USD', 'SPOT-GOLD', 'KJPL', 'SPOT-GOLD', 3939.06, 3938.75, 'USD', 'oz', 31.1035, 'KJPL_LIVE', '11/12/2026 02:18:44 PM', TIMESTAMPTZ '2026-12-11 08:48:44+00', TIMESTAMPTZ '2026-11-12 08:48:44+00', 'Run 4'),
    (27, 'ALL_INDIA', 'KJPL_SPOT_SILVER_USD', 'SPOT-SILVER', 'KJPL', 'SPOT-SILVER', 51.05, 51.04, 'USD', 'oz', 31.1035, 'KJPL_LIVE', '11/12/2026 02:18:44 PM', TIMESTAMPTZ '2026-12-11 08:48:44+00', TIMESTAMPTZ '2026-11-12 08:48:44+00', 'Run 4'),
    (28, 'ALL_INDIA', 'KJPL_SPOT_USDINR', 'SPOT-INR', 'KJPL', 'SPOT-INR', 93.99, 93.98, 'INR', 'USD', 1.0000, 'KJPL_LIVE', '11/12/2026 02:18:44 PM', TIMESTAMPTZ '2026-12-11 08:48:44+00', TIMESTAMPTZ '2026-11-12 08:48:44+00', 'Run 4');

    -- 3. Assert All 22 Original Records Match Manifest Individually
    -- Uses NULL-safe IS DISTINCT FROM across all fields
    SELECT COUNT(*) INTO v_manifest_match_count
    FROM _expected_manifest m
    JOIN public.bullion_rate_observations o ON m.id = o.id;

    IF v_manifest_match_count <> 22 THEN
        RAISE EXCEPTION 'Manifest check failed: expected 22 matching original records, found %', v_manifest_match_count;
    END IF;

    SELECT COUNT(*) INTO v_manifest_mismatch
    FROM _expected_manifest m
    JOIN public.bullion_rate_observations o ON m.id = o.id
    WHERE o.city_code IS DISTINCT FROM m.city_code
       OR o.product_id IS DISTINCT FROM m.product_id
       OR o.source_product_id IS DISTINCT FROM m.source_product_id
       OR o.source_name IS DISTINCT FROM m.source_name
       OR o.raw_source_name IS DISTINCT FROM m.raw_source_name
       OR o.price IS DISTINCT FROM m.price
       OR o.buy_price IS DISTINCT FROM m.buy_price
       OR o.currency IS DISTINCT FROM m.currency
       OR o.unit IS DISTINCT FROM m.unit
       OR o.product_weight_grams IS DISTINCT FROM m.product_weight_grams
       OR o.rate_type IS DISTINCT FROM m.rate_type
       OR o.raw_source_published_time IS DISTINCT FROM m.raw_source_published_time
       OR o.source_published_at IS DISTINCT FROM m.expected_erroneous_source_published_at
       OR o.observation_kind IS DISTINCT FROM 'ORIGINAL'
       OR o.revision_number IS DISTINCT FROM 1;

    IF v_manifest_mismatch > 0 THEN
        RAISE EXCEPTION 'Manifest assertion failed: % original records do not match expected manifest!', v_manifest_mismatch;
    END IF;

    -- 4. Pre-Remediation Snapshots for Protected MJDTA/LBMA Records (IDs 1-6) using JSONB
    CREATE TEMP TABLE _pre_mjdta_obs ON COMMIT DROP AS
    SELECT id, to_jsonb(o.*) AS row_data
    FROM public.bullion_rate_observations o
    WHERE id BETWEEN 1 AND 6;

    CREATE TEMP TABLE _pre_mjdta_latest ON COMMIT DROP AS
    SELECT lr.city_code, lr.product_id, to_jsonb(lr.*) AS row_data
    FROM public.bullion_latest_rates lr
    WHERE (lr.city_code, lr.product_id) IN (
        SELECT DISTINCT city_code, product_id FROM public.bullion_rate_observations WHERE id BETWEEN 1 AND 6
    );

    IF (SELECT COUNT(*) FROM _pre_mjdta_obs) <> 6 THEN
        RAISE EXCEPTION 'Precondition failed: expected 6 protected MJDTA observations, found %', (SELECT COUNT(*) FROM _pre_mjdta_obs);
    END IF;

    IF (SELECT COUNT(*) FROM _pre_mjdta_latest) <> 6 THEN
        RAISE EXCEPTION 'Precondition failed: expected 6 protected MJDTA latest rates, found %', (SELECT COUNT(*) FROM _pre_mjdta_latest);
    END IF;

    -- 5. Strengthened Preconditions on 11 Snapshot Latest-Rate Rows Before Update
    SELECT COUNT(DISTINCT (lr.city_code, lr.product_id)) INTO v_lr_keys_before
    FROM public.bullion_latest_rates lr
    JOIN (SELECT DISTINCT city_code, product_id FROM _expected_manifest WHERE id BETWEEN 18 AND 28) k
      ON lr.city_code = k.city_code AND lr.product_id = k.product_id;

    IF v_lr_keys_before <> 11 THEN
        RAISE EXCEPTION 'Precondition failed: expected exactly 11 latest_rate keys matching manifest, found %', v_lr_keys_before;
    END IF;

    SELECT COUNT(*) INTO v_lr_dup_before
    FROM (
        SELECT city_code, product_id, COUNT(*) as cnt
        FROM public.bullion_latest_rates
        WHERE (city_code, product_id) IN (SELECT DISTINCT city_code, product_id FROM _expected_manifest WHERE id BETWEEN 18 AND 28)
        GROUP BY city_code, product_id
        HAVING COUNT(*) > 1
    ) sub;

    IF v_lr_dup_before > 0 THEN
        RAISE EXCEPTION 'Precondition failed: duplicate latest_rates rows found for manifest keys!';
    END IF;

    -- Assert all 11 target latest_rates currently have validation_status = 'VALID' (NULL-safe IS DISTINCT FROM)
    SELECT COUNT(*) INTO v_lr_invalid_count
    FROM public.bullion_latest_rates lr
    JOIN (SELECT DISTINCT city_code, product_id FROM _expected_manifest WHERE id BETWEEN 18 AND 28) k
      ON lr.city_code = k.city_code AND lr.product_id = k.product_id
    WHERE lr.validation_status IS DISTINCT FROM 'VALID';

    IF v_lr_invalid_count > 0 THEN
        RAISE EXCEPTION 'Precondition failed: % target latest_rate rows have invalid or NULL validation_status!', v_lr_invalid_count;
    END IF;

    -- 6. Check Target Superseding Records State (Scoped Idempotency & Rogue Lineage Guard)
    -- Inspects ALL records referencing target original IDs 7..28, regardless of observation_kind!
    SELECT COUNT(*) INTO v_target_superseding_total
    FROM public.bullion_rate_observations
    WHERE supersedes_observation_id BETWEEN 7 AND 28;

    SELECT COUNT(*) INTO v_rogue_non_corr
    FROM public.bullion_rate_observations
    WHERE supersedes_observation_id BETWEEN 7 AND 28
      AND observation_kind IS DISTINCT FROM 'CORRECTION';

    IF v_rogue_non_corr > 0 THEN
        RAISE EXCEPTION 'Precondition failed: found % rogue non-CORRECTION records superseding target originals!', v_rogue_non_corr;
    END IF;

    SELECT COUNT(*) INTO v_bad_revision_corr
    FROM public.bullion_rate_observations
    WHERE supersedes_observation_id BETWEEN 7 AND 28
      AND revision_number IS DISTINCT FROM 2;

    IF v_bad_revision_corr > 0 THEN
        RAISE EXCEPTION 'Precondition failed: found % records superseding target originals with revision <> 2!', v_bad_revision_corr;
    END IF;

    IF v_target_superseding_total NOT IN (0, 22) THEN
        RAISE EXCEPTION 'Precondition failed: unexpected target superseding records count (%)', v_target_superseding_total;
    END IF;

    -- 7. Insert 22 CORRECTION Observations (if not already inserted)
    IF v_target_superseding_total = 0 THEN
        INSERT INTO public.bullion_rate_observations (
            city_code, product_id, price, buy_price, currency, rate_type, unit,
            source_name, source_product_id, raw_source_name, product_weight_grams,
            original_price, original_unit, normalized_price_per_gram, source_url,
            source_published_at, raw_source_published_time, source_timestamp_precision,
            is_timestamp_inferred, observed_at, observation_kind, revision_number,
            supersedes_observation_id, correction_reason, is_changed, freshness_status,
            validation_status, quarantine_reason, raw_payload_reference
        )
        SELECT
            o.city_code, o.product_id, o.price, o.buy_price, o.currency, o.rate_type, o.unit,
            o.source_name, o.source_product_id, o.raw_source_name, o.product_weight_grams,
            o.original_price, o.original_unit, o.normalized_price_per_gram, o.source_url,
            m.expected_source_published_at,
            o.raw_source_published_time, 'FULL', false, o.observed_at,
            'CORRECTION', 2, o.id,
            'Stage 19.5 remediation: KJPL Type-4 MM/DD/YYYY timestamp correction',
            o.is_changed,
            CASE
                WHEN v_tx_now - m.expected_source_published_at <= INTERVAL '4 hours' THEN 'FRESH'
                WHEN v_tx_now - m.expected_source_published_at <= INTERVAL '24 hours' THEN 'DELAYED'
                ELSE 'STALE'
            END,
            'VALID', NULL, o.raw_payload_reference
        FROM public.bullion_rate_observations o
        JOIN _expected_manifest m ON o.id = m.id
        ORDER BY o.id;

        GET DIAGNOSTICS v_inserted_corr = ROW_COUNT;
        IF v_inserted_corr <> 22 THEN
            RAISE EXCEPTION 'Post-insert assertion failed: expected 22 inserted corrections, got %', v_inserted_corr;
        END IF;
    END IF;

    -- 8. Explicit Per-ID Lineage & Scoped Postconditions on Target Corrections
    SELECT COUNT(*) INTO v_target_corr_count
    FROM public.bullion_rate_observations
    WHERE supersedes_observation_id BETWEEN 7 AND 28 AND observation_kind = 'CORRECTION';

    IF v_target_corr_count <> 22 THEN
        RAISE EXCEPTION 'Verification failed: expected exactly 22 target corrections, found %', v_target_corr_count;
    END IF;

    FOR v_orig_id IN 7..28 LOOP
        SELECT COUNT(*) INTO v_single_corr_count
        FROM public.bullion_rate_observations
        WHERE supersedes_observation_id = v_orig_id AND observation_kind = 'CORRECTION';

        IF v_single_corr_count <> 1 THEN
            RAISE EXCEPTION 'Per-ID cardinality failed for target original ID %: expected exactly 1 correction, found %',
                v_orig_id, v_single_corr_count;
        END IF;
    END LOOP;

    FOR v_corr IN (
        SELECT c.*,
               m.expected_source_published_at,
               m.price AS m_price,
               m.buy_price AS m_buy_price,
               m.city_code AS m_city,
               m.product_id AS m_prod,
               m.source_product_id AS m_sprod,
               m.currency AS m_curr,
               m.unit AS m_unit,
               m.product_weight_grams AS m_weight,
               m.rate_type AS m_rtype,
               m.raw_source_published_time AS m_raw_time
        FROM public.bullion_rate_observations c
        JOIN _expected_manifest m ON c.supersedes_observation_id = m.id
        WHERE c.supersedes_observation_id BETWEEN 7 AND 28
    ) LOOP
        IF v_corr.source_published_at IS DISTINCT FROM v_corr.expected_source_published_at THEN
            RAISE EXCEPTION 'Correction timestamp mismatch for superseded ID %: expected %, got %',
                v_corr.supersedes_observation_id, v_corr.expected_source_published_at, v_corr.source_published_at;
        END IF;
        IF v_corr.source_published_at > v_tx_now THEN
            RAISE EXCEPTION 'Correction timestamp is in the future for superseded ID %: % > %',
                v_corr.supersedes_observation_id, v_corr.source_published_at, v_tx_now;
        END IF;
        IF v_corr.source_timestamp_precision IS DISTINCT FROM 'FULL' THEN
            RAISE EXCEPTION 'Correction precision not FULL for superseded ID %', v_corr.supersedes_observation_id;
        END IF;
        IF v_corr.is_timestamp_inferred IS DISTINCT FROM false THEN
            RAISE EXCEPTION 'Correction inferred flag not false for superseded ID %', v_corr.supersedes_observation_id;
        END IF;
        IF v_corr.validation_status IS DISTINCT FROM 'VALID' THEN
            RAISE EXCEPTION 'Correction validation status not VALID for superseded ID %', v_corr.supersedes_observation_id;
        END IF;
        IF v_corr.revision_number IS DISTINCT FROM 2 THEN
            RAISE EXCEPTION 'Correction revision number not 2 for superseded ID %', v_corr.supersedes_observation_id;
        END IF;
        IF v_corr.observation_kind IS DISTINCT FROM 'CORRECTION' THEN
            RAISE EXCEPTION 'Correction kind not CORRECTION for superseded ID %', v_corr.supersedes_observation_id;
        END IF;
        IF v_corr.price IS DISTINCT FROM v_corr.m_price THEN
            RAISE EXCEPTION 'Correction price mismatch for superseded ID %: expected %, got %',
                v_corr.supersedes_observation_id, v_corr.m_price, v_corr.price;
        END IF;
        IF v_corr.buy_price IS DISTINCT FROM v_corr.m_buy_price THEN
            RAISE EXCEPTION 'Correction buy_price mismatch for superseded ID %', v_corr.supersedes_observation_id;
        END IF;
        IF v_corr.city_code IS DISTINCT FROM v_corr.m_city OR v_corr.product_id IS DISTINCT FROM v_corr.m_prod OR v_corr.source_product_id IS DISTINCT FROM v_corr.m_sprod THEN
            RAISE EXCEPTION 'Correction source identity mismatch for superseded ID %', v_corr.supersedes_observation_id;
        END IF;
        IF v_corr.currency IS DISTINCT FROM v_corr.m_curr OR v_corr.unit IS DISTINCT FROM v_corr.m_unit OR v_corr.product_weight_grams IS DISTINCT FROM v_corr.m_weight THEN
            RAISE EXCEPTION 'Correction unit/weight mismatch for superseded ID %', v_corr.supersedes_observation_id;
        END IF;
        IF v_corr.rate_type IS DISTINCT FROM v_corr.m_rtype OR v_corr.raw_source_published_time IS DISTINCT FROM v_corr.m_raw_time THEN
            RAISE EXCEPTION 'Correction rate_type or raw_time mismatch for superseded ID %', v_corr.supersedes_observation_id;
        END IF;
        IF v_corr.quarantine_reason IS NOT NULL THEN
            RAISE EXCEPTION 'Correction quarantine_reason is not NULL for superseded ID %', v_corr.supersedes_observation_id;
        END IF;
    END LOOP;

    -- 9. Check Run-4 Latest-Rate Cardinality Preconditions Before Update
    SELECT COUNT(*) INTO v_lr_multi_corr
    FROM (
        SELECT city_code, product_id, COUNT(*) as cnt
        FROM public.bullion_rate_observations
        WHERE supersedes_observation_id BETWEEN 18 AND 28
          AND observation_kind = 'CORRECTION'
        GROUP BY city_code, product_id
        HAVING COUNT(*) > 1
    ) sub;

    IF v_lr_multi_corr > 0 THEN
        RAISE EXCEPTION 'Run-4 latest-rate rebuild failed: duplicate correction candidates for key!';
    END IF;

    -- 10. Rebuild the 11 Affected Latest-Rate Rows from Run-4 Corrections (IDs 18-28)
    -- Explicitly sets validation_status = 'VALID'
    WITH latest_corr AS (
        SELECT c.city_code, c.product_id, c.source_published_at, c.price, c.freshness_status
        FROM public.bullion_rate_observations c
        WHERE c.supersedes_observation_id BETWEEN 18 AND 28
          AND c.observation_kind = 'CORRECTION'
          AND c.revision_number = 2
          AND c.validation_status = 'VALID'
    )
    UPDATE public.bullion_latest_rates lr
    SET source_published_at = lc.source_published_at,
        latest_price = lc.price,
        freshness_status = lc.freshness_status,
        validation_status = 'VALID',
        last_verified_at = GREATEST(lr.last_verified_at, lc.source_published_at)
    FROM latest_corr lc
    WHERE lr.city_code = lc.city_code AND lr.product_id = lc.product_id;

    GET DIAGNOSTICS v_updated_latest = ROW_COUNT;
    IF v_updated_latest <> 11 THEN
        RAISE EXCEPTION 'Post-update assertion failed: expected 11 updated latest_rates, got %', v_updated_latest;
    END IF;

    -- 11. Strengthened Postconditions for the 11 Latest-Rate Rows
    FOR v_lr IN (
        SELECT lr.city_code, lr.product_id, lr.latest_price, lr.source_published_at,
               lr.freshness_status, lr.validation_status,
               c.price AS corr_price, c.source_published_at AS corr_ts,
               c.freshness_status AS corr_freshness, c.validation_status AS corr_validation
        FROM public.bullion_latest_rates lr
        JOIN public.bullion_rate_observations c
          ON lr.city_code = c.city_code AND lr.product_id = c.product_id
        WHERE c.supersedes_observation_id BETWEEN 18 AND 28
          AND c.observation_kind = 'CORRECTION'
    ) LOOP
        IF v_lr.latest_price IS DISTINCT FROM v_lr.corr_price THEN
            RAISE EXCEPTION 'Postcondition failed: latest_price mismatch for key (%, %): lr=%, corr=%',
                v_lr.city_code, v_lr.product_id, v_lr.latest_price, v_lr.corr_price;
        END IF;
        IF v_lr.source_published_at IS DISTINCT FROM v_lr.corr_ts THEN
            RAISE EXCEPTION 'Postcondition failed: timestamp mismatch for key (%, %): lr=%, corr=%',
                v_lr.city_code, v_lr.product_id, v_lr.source_published_at, v_lr.corr_ts;
        END IF;
        IF v_lr.freshness_status IS DISTINCT FROM v_lr.corr_freshness THEN
            RAISE EXCEPTION 'Postcondition failed: freshness mismatch for key (%, %): lr=%, corr=%',
                v_lr.city_code, v_lr.product_id, v_lr.freshness_status, v_lr.corr_freshness;
        END IF;
        IF v_lr.validation_status IS DISTINCT FROM 'VALID' THEN
            RAISE EXCEPTION 'Postcondition failed: validation_status not VALID for key (%, %)',
                v_lr.city_code, v_lr.product_id;
        END IF;
        IF v_lr.source_published_at > v_tx_now THEN
            RAISE EXCEPTION 'Postcondition failed: latest_rate timestamp is in future for key (%, %)',
                v_lr.city_code, v_lr.product_id;
        END IF;
    END LOOP;

    -- 12. Complete Comparison of Protected MJDTA/LBMA Rows (Observations & Latest Rates) via JSONB
    IF EXISTS (
        SELECT 1 FROM _pre_mjdta_obs p
        FULL OUTER JOIN (
            SELECT id, to_jsonb(o.*) AS row_data
            FROM public.bullion_rate_observations o
            WHERE id BETWEEN 1 AND 6
        ) c ON p.id = c.id
        WHERE p.row_data IS DISTINCT FROM c.row_data
           OR p.id IS NULL OR c.id IS NULL
    ) THEN
        RAISE EXCEPTION 'MJDTA/LBMA observation preservation check failed: protected rows modified or missing!';
    END IF;

    IF EXISTS (
        SELECT 1 FROM _pre_mjdta_latest p
        FULL OUTER JOIN (
            SELECT lr.city_code, lr.product_id, to_jsonb(lr.*) AS row_data
            FROM public.bullion_latest_rates lr
            WHERE (lr.city_code, lr.product_id) IN (
                SELECT DISTINCT city_code, product_id FROM public.bullion_rate_observations WHERE id BETWEEN 1 AND 6
            )
        ) c ON p.city_code = c.city_code AND p.product_id = c.product_id
        WHERE p.row_data IS DISTINCT FROM c.row_data
           OR p.city_code IS NULL OR c.city_code IS NULL
    ) THEN
        RAISE EXCEPTION 'MJDTA/LBMA latest-rates preservation check failed: protected rows modified or missing!';
    END IF;

    -- 13. Sanity Check: All target CORRECTION rows must have source_published_at <= v_tx_now
    SELECT COUNT(*) INTO v_future_corr_count
    FROM public.bullion_rate_observations
    WHERE source_published_at > v_tx_now
      AND observation_kind = 'CORRECTION'
      AND supersedes_observation_id BETWEEN 7 AND 28;

    IF v_future_corr_count > 0 THEN
        RAISE EXCEPTION 'Postcondition failed: % CORRECTION records have future timestamps!', v_future_corr_count;
    END IF;

    RAISE NOTICE 'Stage 19.5 remediation executed and validated successfully. Inserted corrections: %, Updated latest rates: %',
        v_inserted_corr, v_updated_latest;
END $$;

COMMIT;
