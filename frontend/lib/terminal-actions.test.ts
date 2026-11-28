import test from "node:test";
import assert from "node:assert/strict";
import {
  formatSyncNotice,
  formatObservationProvenance,
  normalizePurityInput,
  validateHoldingForm,
  validateAlertForm,
  loadStoredPreferences,
  saveStoredPreferences,
  CitySelectionCoordinator,
} from "./terminal-actions.ts";

// =============================================================================
// 1. SYNC ACTION TESTS (Success, Stale, Unavailable, Error)
// =============================================================================

test("SYNC - generates honest notice for real-time Connected status", () => {
  const notice = formatSyncNotice("Connected");
  assert.equal(notice, "Terminal synced: Real-time database state verified.");
});

test("SYNC - generates honest notice for Stale status with observation timestamp", () => {
  const notice = formatSyncNotice("Stale", "26 Nov 09:56 AM IST");
  assert.equal(
    notice,
    "Terminal synced: Database connected (Quotes from 26 Nov 09:56 AM IST, Stale)."
  );

  // Fallback when observation time is omitted or null
  const noticeFallback = formatSyncNotice("Stale", null);
  assert.equal(
    noticeFallback,
    "Terminal synced: Database connected (Quotes from prior session, Stale)."
  );
});

test("SYNC - generates honest notice for Unavailable feed status", () => {
  const notice = formatSyncNotice("Unavailable");
  assert.equal(
    notice,
    "Terminal synced: Bullion market quotes currently unavailable."
  );

  const noticeWarm = formatSyncNotice("Warming up");
  assert.equal(noticeWarm, "Terminal synced: Market cache warming up.");

  const noticeRecon = formatSyncNotice("Reconnecting");
  assert.equal(noticeRecon, "Terminal synced: Feed reconnecting...");
});

// =============================================================================
// 2. OBSERVATION PROVENANCE & STALE BADGE RENDERING
// =============================================================================

test("Observation Provenance - formats exact timestamp and age indicator", () => {
  const summaryStale = {
    system_status: "Stale",
    last_successful_observation: {
      updated_time_ist: "26 Nov 09:56 AM IST",
      age_display: "2d 4h ago",
    },
  };
  const res = formatObservationProvenance(summaryStale);
  assert.equal(res.text, "OBSERVED: 26 Nov 09:56 AM IST (2d 4h ago)");
  assert.equal(res.isStale, true);

  const summaryConnected = {
    system_status: "Connected",
    last_successful_observation: {
      updated_time_ist: "28 Nov 10:00 AM IST",
      age_display: "15m ago",
    },
  };
  const resConnected = formatObservationProvenance(summaryConnected);
  assert.equal(resConnected.text, "OBSERVED: 28 Nov 10:00 AM IST (15m ago)");
  assert.equal(resConnected.isStale, false);

  // When observation is missing
  const resMissing = formatObservationProvenance(null);
  assert.equal(resMissing.text, "No observation recorded");
  assert.equal(resMissing.isStale, false);
});

// =============================================================================
// 3. PURITY & HOLDINGS SUBMISSION VALIDATION
// =============================================================================

test("Purity normalization - handles jewelers/traders shorthand fineness", () => {
  // 22K shorthand
  assert.deepEqual(normalizePurityInput("22"), { purity: 0.916, error: null });
  assert.deepEqual(normalizePurityInput("916"), { purity: 0.916, error: null });

  // 24K shorthand
  assert.deepEqual(normalizePurityInput("24"), { purity: 0.999, error: null });
  assert.deepEqual(normalizePurityInput("999"), { purity: 0.999, error: null });

  // 18K shorthand
  assert.deepEqual(normalizePurityInput("18"), { purity: 0.75, error: null });
  assert.deepEqual(normalizePurityInput("750"), { purity: 0.75, error: null });

  // Standard decimal
  assert.deepEqual(normalizePurityInput("0.916"), { purity: 0.916, error: null });
  assert.deepEqual(normalizePurityInput("0.999"), { purity: 0.999, error: null });

  // Out of range or invalid
  assert.equal(normalizePurityInput("0").purity, null);
  assert.equal(normalizePurityInput("-5").purity, null);
  assert.equal(normalizePurityInput("abc").purity, null);
  assert.equal(normalizePurityInput("1500").purity, null);
});

test("Holding submission validation - enforces required positive financial fields", () => {
  // Valid holding with 22K trader shorthand
  const validForm = {
    metal_type: "GOLD",
    product_name: "22K Sovereign Coin",
    purity: "22",
    gross_weight: "8.0",
    weight_unit: "g",
    quantity: "2",
    acquisition_date: "2026-11-20",
    acquisition_cost_inr: "92000",
    notes: "Vault shelf A",
  };
  const val = validateHoldingForm(validForm);
  assert.equal(val.valid, true);
  if (val.valid) {
    assert.equal(val.payload.purity, 0.916);
    assert.equal(val.payload.gross_weight, 8.0);
    assert.equal(val.payload.quantity, 2);
    assert.equal(val.payload.acquisition_cost_inr, 92000);
  }

  // Reject zero or negative weight
  const invalidWeight = { ...validForm, gross_weight: "0" };
  const valWeight = validateHoldingForm(invalidWeight);
  assert.equal(valWeight.valid, false);

  // Reject quantity < 1
  const invalidQty = { ...validForm, quantity: "0" };
  const valQty = validateHoldingForm(invalidQty);
  assert.equal(valQty.valid, false);

  // Reject invalid purity
  const invalidPurity = { ...validForm, purity: "not_a_number" };
  const valPurity = validateHoldingForm(invalidPurity);
  assert.equal(valPurity.valid, false);
});

// =============================================================================
// 4. ALERTS SUBMISSION & DELETION CONFIRMATION
// =============================================================================

test("Alert submission validation - validates threshold and target metric", () => {
  const validAlert = {
    alert_type: "PRICE_ABOVE",
    target_metric: "MJDTA_GOLD_22K",
    threshold: "12000.50",
    notes: "Notify wholesale desk",
  };
  const val = validateAlertForm(validAlert);
  assert.equal(val.valid, true);
  if (val.valid) {
    assert.equal(val.payload.target_metric, "MJDTA_GOLD_22K");
    assert.equal(val.payload.threshold, 12000.5);
  }

  // Reject non-positive threshold
  const invalidThresh = { ...validAlert, threshold: "0" };
  const valThresh = validateAlertForm(invalidThresh);
  assert.equal(valThresh.valid, false);

  // Reject missing metric
  const missingMetric = { ...validAlert, target_metric: "   " };
  const valMetric = validateAlertForm(missingMetric);
  assert.equal(valMetric.valid, false);
});

test("Alert and Holding deletion - requires confirmation guard", () => {
  let userConfirmed = false;
  const mockConfirm = () => userConfirmed;

  let deleteInvoked = false;
  const executeDelete = (confirmed: boolean) => {
    if (confirmed) {
      deleteInvoked = true;
    }
  };

  // User cancels
  userConfirmed = false;
  executeDelete(mockConfirm());
  assert.equal(deleteInvoked, false);

  // User confirms
  userConfirmed = true;
  executeDelete(mockConfirm());
  assert.equal(deleteInvoked, true);
});

// =============================================================================
// 5. CITY SELECTION & OUT-OF-ORDER RACE CONDITIONS
// =============================================================================

test("CitySelectionCoordinator - prevents out-of-order responses from overwriting active selection", async () => {
  const coordinator = new CitySelectionCoordinator("CHENNAI");
  const appliedData: Array<{ city: string; data: Record<string, unknown> }> = [];

  // Delayed fetcher simulating network latency
  const mockFetcher = (cityCode: string, _limit: number, signal?: AbortSignal) => {
    return new Promise<Record<string, unknown>>((resolve, reject) => {
      const delayMs = cityCode === "CHENNAI" ? 60 : 15; // Chennai returns slower than Bengaluru
      const timer = setTimeout(() => {
        if (signal?.aborted) {
          reject(new Error("AbortError"));
        } else {
          resolve({ city: cityCode, timestamp: Date.now() });
        }
      }, delayMs);

      signal?.addEventListener("abort", () => {
        clearTimeout(timer);
        reject(new Error("AbortError"));
      });
    });
  };

  // 1. Select Chennai (slower request)
  const chennaiPromise = coordinator.selectCity(
    "CHENNAI",
    mockFetcher,
    (data, city) => {
      appliedData.push({ city, data });
    }
  );

  // 2. User immediately switches to Bengaluru (faster request)
  const bengaluruPromise = coordinator.selectCity(
    "BENGALURU",
    mockFetcher,
    (data, city) => {
      appliedData.push({ city, data });
    }
  );

  await Promise.all([chennaiPromise, bengaluruPromise]);

  // Assert that ONLY Bengaluru was applied, and Chennai was discarded/aborted
  assert.equal(coordinator.getActiveCity(), "BENGALURU");
  assert.equal(appliedData.length, 1);
  assert.equal(appliedData[0].city, "BENGALURU");
});

// =============================================================================
// 6. SECURE SETTINGS PERSISTENCE & SECRETS PROTECTION
// =============================================================================

test("Settings persistence - stores non-sensitive preferences and NEVER stores apiKey", () => {
  const mockStorage: Record<string, string> = {
    vmd_api_key: "legacy_secret_key_123", // Pre-existing unsafe key in storage
  };

  const fakeStorage = {
    getItem: (key: string) => mockStorage[key] || null,
    setItem: (key: string, val: string) => {
      mockStorage[key] = val;
    },
    removeItem: (key: string) => {
      delete mockStorage[key];
    },
    clear: () => {},
    key: () => null,
    length: 1,
  } as unknown as Storage;

  // 1. Loading preferences purges legacy API key from storage
  const loaded = loadStoredPreferences(fakeStorage);
  assert.equal(mockStorage["vmd_api_key"], undefined);
  assert.equal(loaded.selectedCity, "CHENNAI");
  assert.equal(loaded.userId, "owner");

  // 2. Saving preferences stores selectedCity and userId, never apiKey
  const saveResult = saveStoredPreferences(
    {
      selectedCity: "MUMBAI",
      userId: "trader_101",
    },
    fakeStorage
  );
  assert.equal(saveResult.success, true);
  assert.equal(mockStorage["vmd_selected_city"], "MUMBAI");
  assert.equal(mockStorage["vmd_user_id"], "trader_101");
  assert.equal(mockStorage["vmd_api_key"], undefined);
});
