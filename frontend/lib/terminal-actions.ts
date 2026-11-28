/**
 * Terminal Action Coordinators, Validation Helpers, and State Protections
 * for VMD Bullion Institutional Terminal.
 */

// =============================================================================
// 1. SETTINGS PERSISTENCE & SECRETS PROTECTION
// =============================================================================

export interface TerminalPreferences {
  selectedCity: string;
  userId: string;
  // Note: apiKey is strictly session-only and never stored in localStorage
}

const STORAGE_KEYS = {
  SELECTED_CITY: "vmd_selected_city",
  USER_ID: "vmd_user_id",
  LEGACY_API_KEY: "vmd_api_key", // Purged on load to eliminate security risk
};

function getStorage(): Storage | null {
  if (typeof window !== "undefined" && window.localStorage) {
    return window.localStorage;
  }
  return null;
}

export function loadStoredPreferences(storage: Storage | null = getStorage()): TerminalPreferences {
  if (!storage) {
    return { selectedCity: "CHENNAI", userId: "owner" };
  }
  try {
    // Purge any previously stored API key from localStorage to prevent XSS exfiltration
    if (storage.getItem(STORAGE_KEYS.LEGACY_API_KEY)) {
      storage.removeItem(STORAGE_KEYS.LEGACY_API_KEY);
    }
    const selectedCity = storage.getItem(STORAGE_KEYS.SELECTED_CITY) || "CHENNAI";
    const userId = storage.getItem(STORAGE_KEYS.USER_ID) || "owner";
    return { selectedCity, userId };
  } catch {
    return { selectedCity: "CHENNAI", userId: "owner" };
  }
}

export function saveStoredPreferences(
  prefs: { selectedCity: string; userId: string },
  storage: Storage | null = getStorage()
): { success: boolean; message: string } {
  if (!storage) {
    return { success: false, message: "Storage unavailable" };
  }
  try {
    storage.setItem(STORAGE_KEYS.SELECTED_CITY, prefs.selectedCity);
    storage.setItem(STORAGE_KEYS.USER_ID, prefs.userId);
    // Explicitly enforce that apiKey is never stored
    if (storage.getItem(STORAGE_KEYS.LEGACY_API_KEY)) {
      storage.removeItem(STORAGE_KEYS.LEGACY_API_KEY);
    }
    return {
      success: true,
      message: "Preferences saved (API Key kept in memory only for security).",
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Failed to access storage";
    return { success: false, message: `Save error: ${msg}` };
  }
}

// =============================================================================
// 2. CITY SELECTION REQUEST SEQUENCER (RACE CONDITION DEFENSE)
// =============================================================================

export interface CitySelectionPayload {
  summary?: Record<string, unknown>;
  [key: string]: unknown;
}

export type DashboardFetcher = (
  cityCode: string,
  historyLimit: number,
  signal?: AbortSignal
) => Promise<CitySelectionPayload>;

export class CitySelectionCoordinator {
  private activeRequestId = 0;
  private activeCityCode = "CHENNAI";
  private activeAbortController: AbortController | null = null;

  constructor(initialCity: string = "CHENNAI") {
    this.activeCityCode = initialCity;
  }

  public getActiveCity(): string {
    return this.activeCityCode;
  }

  public getActiveRequestId(): number {
    return this.activeRequestId;
  }

  /**
   * Dispatches a city switch request, aborting any prior in-flight request
   * and discarding out-of-order responses.
   */
  public async selectCity(
    cityCode: string,
    fetcher: DashboardFetcher,
    onSuccess: (payload: CitySelectionPayload, city: string) => void,
    onError?: (error: Error, city: string) => void
  ): Promise<boolean> {
    this.activeCityCode = cityCode;

    // Abort previous in-flight request
    if (this.activeAbortController) {
      try {
        this.activeAbortController.abort();
      } catch {
        // Ignore abort errors
      }
    }

    const controller = new AbortController();
    this.activeAbortController = controller;
    const currentRequestId = ++this.activeRequestId;

    try {
      const data = await fetcher(cityCode, 30, controller.signal);

      // Race guard: Only apply if this request is still the latest active one and was not aborted
      if (currentRequestId === this.activeRequestId && !controller.signal.aborted) {
        onSuccess(data, cityCode);
        return true;
      }
      return false; // Discarded as stale out-of-order response
    } catch (err: unknown) {
      if (controller.signal.aborted || (err as { name?: string })?.name === "AbortError") {
        return false; // Expected abort, swallow
      }
      if (currentRequestId === this.activeRequestId && onError) {
        onError(err instanceof Error ? err : new Error(String(err)), cityCode);
      }
      return false;
    }
  }
}

// =============================================================================
// 3. PURITY & FINANCIAL INPUT NORMALIZERS
// =============================================================================

export interface PurityNormalizationResult {
  purity: number | null;
  error: string | null;
}

export function normalizePurityInput(val: string): PurityNormalizationResult {
  const num = parseFloat(val);
  if (isNaN(num) || num <= 0) {
    return { purity: null, error: "Purity must be a positive number." };
  }
  let normalized = num;
  if (num === 22 || num === 916) normalized = 0.916;
  else if (num === 24 || num === 999) normalized = 0.999;
  else if (num === 18 || num === 750) normalized = 0.750;
  else if (num > 1.0 && num <= 1000) normalized = Math.round((num / 1000) * 10000) / 10000;

  if (normalized <= 0 || normalized > 1.0) {
    return {
      purity: null,
      error: "Purity must be between 0.0 and 1.0 (e.g. 0.916 for 22K, 0.999 for 24K).",
    };
  }
  return { purity: normalized, error: null };
}

// =============================================================================
// 4. PORTFOLIO & ALERTS FORM VALIDATORS
// =============================================================================

export interface HoldingFormData {
  metal_type: string;
  product_name: string;
  purity: string;
  gross_weight: string;
  weight_unit: string;
  quantity: string;
  acquisition_date?: string;
  acquisition_cost_inr?: string;
  notes?: string;
}

export interface ValidHoldingPayload {
  metal_type: string;
  product_name: string;
  purity: number;
  gross_weight: number;
  weight_unit: string;
  quantity: number;
  acquisition_date: string | null;
  acquisition_cost_inr: number | null;
  notes: string | null;
}

export function validateHoldingForm(
  form: HoldingFormData
): { valid: true; payload: ValidHoldingPayload } | { valid: false; error: string } {
  const { purity, error: purityErr } = normalizePurityInput(form.purity);
  if (purityErr || purity === null) {
    return { valid: false, error: purityErr || "Invalid purity." };
  }
  const grossWeight = parseFloat(form.gross_weight);
  if (isNaN(grossWeight) || grossWeight <= 0) {
    return { valid: false, error: "Gross weight must be greater than 0." };
  }
  const qty = parseInt(form.quantity, 10);
  if (isNaN(qty) || qty < 1) {
    return { valid: false, error: "Quantity must be at least 1." };
  }
  return {
    valid: true,
    payload: {
      metal_type: form.metal_type,
      product_name: form.product_name.trim(),
      purity,
      gross_weight: grossWeight,
      weight_unit: form.weight_unit || "g",
      quantity: qty,
      acquisition_date: form.acquisition_date || null,
      acquisition_cost_inr: form.acquisition_cost_inr ? parseFloat(form.acquisition_cost_inr) : null,
      notes: form.notes?.trim() || null,
    },
  };
}

export interface AlertFormData {
  alert_type: string;
  target_metric: string;
  threshold: string;
  notes?: string;
}

export interface ValidAlertPayload {
  alert_type: string;
  target_metric: string;
  threshold: number;
  notes: string | null;
}

export function validateAlertForm(
  form: AlertFormData
): { valid: true; payload: ValidAlertPayload } | { valid: false; error: string } {
  const thresh = parseFloat(form.threshold);
  if (isNaN(thresh) || thresh <= 0) {
    return { valid: false, error: "Threshold must be a positive number." };
  }
  const metric = form.target_metric.trim();
  if (!metric) {
    return { valid: false, error: "Target metric is required." };
  }
  return {
    valid: true,
    payload: {
      alert_type: form.alert_type,
      target_metric: metric,
      threshold: thresh,
      notes: form.notes?.trim() || null,
    },
  };
}

// =============================================================================
// 5. SYNC NOTICE & OBSERVATION PROVENANCE FORMATTERS
// =============================================================================

export function formatSyncNotice(
  systemStatus: string,
  lastObsIst?: string | null
): string {
  if (systemStatus === "Stale") {
    return `Market quotes are stale (Last observed: ${lastObsIst || "prior session"}). Active session quote pending.`;
  }
  if (systemStatus === "Connected") {
    return "Market quotes refreshed (Live feed verified).";
  }
  if (systemStatus === "Unavailable") {
    return "Bullion market quotes currently unavailable. Upstream feed offline.";
  }
  if (systemStatus === "Warming up") {
    return "Market feed warming up...";
  }
  if (systemStatus === "Reconnecting") {
    return "Feed reconnecting...";
  }
  return `Market feed status: ${systemStatus}.`;
}

export function formatObservationProvenance(summary: {
  system_status?: string;
  last_successful_observation?: {
    updated_time_ist?: string;
    age_display?: string;
  } | null;
} | null): { text: string; isStale: boolean } {
  if (!summary?.last_successful_observation?.updated_time_ist) {
    return { text: "No observation recorded", isStale: false };
  }
  const timeStr = summary.last_successful_observation.updated_time_ist;
  const ageStr = summary.last_successful_observation.age_display || "";
  const isStale = summary.system_status === "Stale";
  return {
    text: `OBSERVED: ${timeStr} (${ageStr})`,
    isStale,
  };
}
