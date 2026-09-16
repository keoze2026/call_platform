/**
 * Campaigns service — talks to /api/campaigns/*.
 *
 * Backend schema (confirmed by backend dev, June 2026):
 *   id, name, description, status (active|paused|archived),
 *   routing_type (priority|round_robin|weighted|rtb),
 *   payout_amount, revenue_amount, payout_model (per_call|per_qualified|per_minute),
 *   vertical (health|auto|home|finance|legal|insurance|other),
 *   min_call_duration, duplicate_call_block, duplicate_call_block_hours,
 *   bid_floor, rtb_timeout_seconds,
 *   cap: { max_calls_daily, max_calls_monthly, max_calls_global, max_concurrency },
 *   schedules: [{ day_of_week (0=Mon..6=Sun), open_time, close_time, is_closed }],
 *   greeting_*, whisper_*, auto_sms_*, recording_enabled, queue_*
 *
 * Frontend keeps the existing `Campaign` shape (kebab-case enums, 0=Sun
 * weekday convention, single schedule with start/end hour) — the wire
 * mappers convert at this boundary so the rest of the app doesn't change.
 */

import { http } from "@/lib/api/http";
import type { Paginated } from "@/lib/api/types";
import type { Campaign, CampaignStatus, PayoutModel, Weekday } from "@/lib/types";

/* ─── Wire shapes ─────────────────────────────────────────────────────── */

interface CampaignCapWire {
  id?: string;
  maxCallsDaily?: number;
  maxCallsMonthly?: number;
  maxCallsGlobal?: number;
  maxConcurrency?: number;
}

interface CampaignScheduleWire {
  id?: string;
  dayOfWeek: number;
  openTime: string;
  closeTime: string;
  isClosed: boolean;
}

interface CampaignListWire {
  id: string;
  name: string;
  status: string;
  routingType: string;
  payoutAmount: string;
  revenueAmount: string;
  createdAt: string;
  vertical?: string;
  payoutModel?: string;
  /** Per BACKEND-CONTRACT.md §3.8 "Live counter fields" — read-only
   *  aggregates the backend computes from the Call table. Optional here
   *  since older/unmigrated rows may not carry them yet; the mapper below
   *  falls back to 0 rather than showing `undefined`. `callsToday` was
   *  already being sent (confirmed in the contract) — this interface just
   *  never declared it, so `listWireToCampaign` hardcoded 0 instead of
   *  reading the real value. */
  callsToday?: number;
  liveCalls?: number;
  callsHour?: number;
  callsMonth?: number;
  callsGlobal?: number;
  /** Present on the list payload too when the backend serialises the full
   *  campaign; read here so the table's "live / cap" cell doesn't show
   *  "N / 0" for every row until the detail page is opened. */
  cap?: CampaignCapWire;
}

interface CampaignWire extends CampaignListWire {
  description?: string;
  minCallDuration?: number;
  duplicateCallBlock?: boolean;
  duplicateCallBlockHours?: number;
  bidFloor?: string;
  rtbTimeoutSeconds?: number;
  organizationId?: string;
  createdById?: string | null;
  cap?: CampaignCapWire;
  schedules?: CampaignScheduleWire[];
  recordingEnabled?: boolean;
  greetingEnabled?: boolean;
  greetingMessage?: string;
  whisperEnabled?: boolean;
  whisperMessage?: string;
  /** Backend persists this verbatim as a JSON blob. Schema is FE-owned. */
  advancedSettings?: Record<string, unknown>;
}

/* ─── Enum maps ───────────────────────────────────────────────────────── */

const PAYOUT_MODEL_TO_WIRE: Record<PayoutModel, string> = {
  "per-call": "per_call",
  "per-qualified": "per_qualified",
  "per-minute": "per_minute",
};
const PAYOUT_MODEL_FROM_WIRE: Record<string, PayoutModel> = {
  per_call: "per-call",
  per_qualified: "per-qualified",
  per_minute: "per-minute",
};

/** Frontend uses long human-readable vertical labels; backend enum is short
 *  slugs. Map both ways so the wizard's existing options keep working. */
const VERTICAL_TO_WIRE: Record<string, string> = {
  "Health Insurance": "health",
  "Auto Insurance": "insurance",
  Automotive: "auto",
  "Home Services": "home",
  Finance: "finance",
  Legal: "legal",
  Insurance: "insurance",
  Other: "other",
};
const VERTICAL_FROM_WIRE: Record<string, string> = {
  health: "Health Insurance",
  auto: "Automotive",
  home: "Home Services",
  finance: "Finance",
  legal: "Legal",
  insurance: "Insurance",
  other: "Other",
};

/** Weekday convention conversion.
 *  Frontend: 0=Sun, 1=Mon, ..., 6=Sat
 *  Backend:  0=Mon, 1=Tue, ..., 6=Sun                                    */
function feDayToBeDay(fe: Weekday): number {
  return fe === 0 ? 6 : fe - 1;
}
function beDayToFeDay(be: number): Weekday {
  const v = be === 6 ? 0 : be + 1;
  return v as Weekday;
}

/** "08:00:00" → 8 ; "20:30:00" → 20 (we ignore minutes — wizard is hour-grain) */
function timeToHour(t: string | null | undefined): number {
  if (!t) return 0;
  const m = /^(\d{1,2})/.exec(t);
  return m ? Math.max(0, Math.min(23, Number(m[1]))) : 0;
}
function hourToTime(h: number): string {
  // Backend's GET returns "HH:MM:SS" but the POST validator only accepts
  // "HH:MM" — confirmed by the "Time must be in HH:MM format" 422 error.
  const safe = Math.max(0, Math.min(23, Math.round(h)));
  return `${String(safe).padStart(2, "0")}:00`;
}

/* ─── Mappers ─────────────────────────────────────────────────────────── */

function normalizeStatus(raw: string | null | undefined): CampaignStatus {
  const s = (raw ?? "").toLowerCase();
  if (s === "paused" || s === "draft" || s === "archived") return s;
  return "active";
}

function toNum(s: string | number | undefined, fallback = 0): number {
  if (typeof s === "number") return s;
  if (typeof s === "string") {
    const n = Number(s);
    return Number.isFinite(n) ? n : fallback;
  }
  return fallback;
}

function defaultSchedule(): Campaign["schedule"] {
  return { days: [0, 1, 2, 3, 4, 5, 6], startHour: 0, endHour: 24, timezone: "auto" };
}

function payoutModelFromWire(raw?: string): PayoutModel {
  if (!raw) return "per-call";
  return PAYOUT_MODEL_FROM_WIRE[raw.toLowerCase()] ?? "per-call";
}

function verticalFromWire(raw?: string): string {
  if (!raw) return "Other";
  return VERTICAL_FROM_WIRE[raw.toLowerCase()] ?? "Other";
}

/** Collapse the backend's per-day schedule array into the frontend's
 *  single-block representation: pick the first open day's open/close as the
 *  uniform start/end, and union all open days. Days the backend marks
 *  `is_closed: true` are excluded. */
function schedulesFromWire(wires: CampaignScheduleWire[] | undefined): Campaign["schedule"] {
  if (!wires || wires.length === 0) return defaultSchedule();
  const open = wires.filter((w) => !w.isClosed);
  if (open.length === 0) {
    return { days: [], startHour: 0, endHour: 24, timezone: "auto" };
  }
  const first = open[0];
  return {
    days: open.map((w) => beDayToFeDay(w.dayOfWeek)).sort((a, b) => a - b) as Weekday[],
    startHour: timeToHour(first.openTime),
    endHour: timeToHour(first.closeTime) || 24,
    // Backend keeps timezone on the org, not the schedule — we surface "auto"
    // here so the UI doesn't lie about per-schedule timezone control.
    timezone: "auto",
  };
}

/** Inverse: collapse the frontend's single block into 7 backend entries
 *  (one per day-of-week), marking unselected days as closed. */
function schedulesToWire(s: Campaign["schedule"]): CampaignScheduleWire[] {
  const open = new Set<number>(s.days.map((d) => feDayToBeDay(d as Weekday)));
  const openTime = hourToTime(s.startHour);
  const closeTime = hourToTime(s.endHour === 24 ? 23 : s.endHour);
  return Array.from({ length: 7 }, (_, beDay) => ({
    dayOfWeek: beDay,
    openTime,
    closeTime,
    isClosed: !open.has(beDay),
  }));
}

function listWireToCampaign(w: CampaignListWire): Campaign {
  return {
    id: w.id,
    name: w.name,
    vertical: verticalFromWire(w.vertical),
    status: normalizeStatus(w.status),
    payout: toNum(w.payoutAmount),
    payoutModel: payoutModelFromWire(w.payoutModel),
    qualifyDurationSec: 0,
    dailyCap: w.cap?.maxCallsDaily ?? 0,
    monthlyCap: w.cap?.maxCallsMonthly ?? 0,
    schedule: defaultSchedule(),
    numbersCount: 0,
    buyersCount: 0,
    publishersCount: 0,
    callsToday: w.callsToday ?? 0,
    revenueToday: 0,
    conversionRate: 0,
    liveCalls: w.liveCalls ?? 0,
    callsHour: w.callsHour ?? 0,
    callsMonth: w.callsMonth ?? 0,
    callsGlobal: w.callsGlobal ?? 0,
    createdAt: Date.parse(w.createdAt) || Date.now(),
  };
}

function detailWireToCampaign(w: CampaignWire): Campaign {
  return {
    ...listWireToCampaign(w),
    description: w.description,
    qualifyDurationSec: w.minCallDuration ?? 0,
    dailyCap: w.cap?.maxCallsDaily ?? 0,
    monthlyCap: w.cap?.maxCallsMonthly ?? 0,
    schedule: schedulesFromWire(w.schedules),
    // Audio + duplicate-handling fields — backend now persists these, so we
    // round-trip them on every read.
    recordingEnabled: w.recordingEnabled,
    greetingEnabled: w.greetingEnabled,
    greetingMessage: w.greetingMessage,
    whisperEnabled: w.whisperEnabled,
    whisperMessage: w.whisperMessage,
    duplicateCallBlock: w.duplicateCallBlock,
    duplicateCallBlockHours: w.duplicateCallBlockHours,
    // Advanced-settings JSON blob — backend ships back what we sent.
    // Schema lives in `CampaignAdvancedSettings`; we treat the wire as opaque.
    advancedSettings: w.advancedSettings,
  };
}

/* ─── Public service ──────────────────────────────────────────────────── */

export const campaignsService = {
  async list(query: { page?: number; pageSize?: number } = {}): Promise<Paginated<Campaign>> {
    const res = await http.get<Paginated<CampaignListWire>>("/api/campaigns/", { query });
    return { ...res, items: res.items.map(listWireToCampaign) };
  },

  async get(id: string): Promise<Campaign> {
    const wire = await http.get<CampaignWire>(`/api/campaigns/${id}`);
    return detailWireToCampaign(wire);
  },

  async create(input: Omit<Campaign, "id" | "createdAt">): Promise<Campaign> {
    // Backend defaults new campaigns to `active`. We always send `paused` so
    // the operator can wire up numbers/buyers before traffic flows — matches
    // the wizard's "Draft, then activate" UX.
    const body: Record<string, unknown> = {
      name: input.name,
      description: input.description ?? "",
      status: "paused",
      routingType: "priority",
      vertical: VERTICAL_TO_WIRE[input.vertical] ?? "other",
      payoutModel: PAYOUT_MODEL_TO_WIRE[input.payoutModel],
      payoutAmount: String(input.payout ?? 0),
      minCallDuration: input.qualifyDurationSec || 0,
      cap: {
        maxCallsDaily: input.dailyCap || 0,
        maxCallsMonthly: input.monthlyCap || 0,
      },
      schedules: schedulesToWire(input.schedule),
    };
    const wire = await http.post<CampaignWire>("/api/campaigns/", { body });
    return detailWireToCampaign(wire);
  },

  async update(id: string, patch: Partial<Campaign>): Promise<Campaign> {
    const body: Record<string, unknown> = {};
    if (patch.name !== undefined) body.name = patch.name;
    if (patch.description !== undefined) body.description = patch.description;
    if (patch.vertical !== undefined) {
      body.vertical = VERTICAL_TO_WIRE[patch.vertical] ?? "other";
    }
    if (patch.payoutModel !== undefined) {
      body.payoutModel = PAYOUT_MODEL_TO_WIRE[patch.payoutModel];
    }
    if (patch.payout !== undefined) body.payoutAmount = String(patch.payout);
    if (patch.qualifyDurationSec !== undefined) body.minCallDuration = patch.qualifyDurationSec;
    if (patch.dailyCap !== undefined || patch.monthlyCap !== undefined) {
      body.cap = {
        ...(patch.dailyCap !== undefined ? { maxCallsDaily: patch.dailyCap } : {}),
        ...(patch.monthlyCap !== undefined ? { maxCallsMonthly: patch.monthlyCap } : {}),
      };
    }
    if (patch.schedule !== undefined) body.schedules = schedulesToWire(patch.schedule);
    // Call audio + duplicate handling — backend's PATCH allowlist now
    // accepts these. Sending only the fields the caller actually patched
    // keeps the request minimal and avoids accidentally clearing untouched
    // fields with `undefined`.
    if (patch.recordingEnabled !== undefined) body.recordingEnabled = patch.recordingEnabled;
    if (patch.greetingEnabled !== undefined) body.greetingEnabled = patch.greetingEnabled;
    if (patch.greetingMessage !== undefined) body.greetingMessage = patch.greetingMessage;
    if (patch.whisperEnabled !== undefined) body.whisperEnabled = patch.whisperEnabled;
    if (patch.whisperMessage !== undefined) body.whisperMessage = patch.whisperMessage;
    if (patch.duplicateCallBlock !== undefined) body.duplicateCallBlock = patch.duplicateCallBlock;
    if (patch.duplicateCallBlockHours !== undefined) body.duplicateCallBlockHours = patch.duplicateCallBlockHours;
    if (patch.advancedSettings !== undefined) body.advancedSettings = patch.advancedSettings;
    const wire = await http.patch<CampaignWire>(`/api/campaigns/${id}`, { body });
    return detailWireToCampaign(wire);
  },

  async remove(id: string): Promise<void> {
    // NOTE: backend canonical URL is WITHOUT trailing slash (confirmed by
    // 404 response on slash-suffixed URL). However, this DELETE has been
    // observed to silently no-op without actually removing the row — see
    // BACKEND-CONTRACT.md §2.12. Backend dev needs to investigate why
    // DELETE returns 2xx but doesn't delete.
    await http.delete(`/api/campaigns/${id}`);
  },

  async setStatus(id: string, status: CampaignStatus): Promise<void> {
    if (status === "active") {
      await http.post(`/api/campaigns/${id}/activate`);
      return;
    }
    if (status === "paused") {
      await http.post(`/api/campaigns/${id}/pause`);
      return;
    }
    await http.patch(`/api/campaigns/${id}`, { body: { status } });
  },

  async updateCap(id: string, cap: { daily?: number; monthly?: number }): Promise<void> {
    await http.patch(`/api/campaigns/${id}/cap`, {
      body: {
        ...(cap.daily !== undefined ? { maxCallsDaily: cap.daily } : {}),
        ...(cap.monthly !== undefined ? { maxCallsMonthly: cap.monthly } : {}),
      },
    });
  },

};
