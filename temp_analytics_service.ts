/**
 * Analytics service — talks to /api/analytics/*.
 *
 * Wraps the dashboard KPI block, time-series, paginated Call Log, per-entity
 * performance views, live snapshot, caller profile, and CSV export.
 *
 * Note: the Call Log endpoint uses offset/limit pagination (not page/page_size
 * like the rest of the API). This service hides that quirk behind a uniform
 * `{ page, pageSize }` input.
 */

import { http } from "@/lib/api/http";
import type { Call, CallStatus } from "@/lib/types";

/* ─── Wire shapes (post case-adapter) ─────────────────────────────────── */

interface DashboardWire {
  totalCalls: number;
  callsToday: number;
  liveCalls: number;
  completedCalls: number;
  convertedCalls: number;
  conversionRate: number;
  totalRevenue: string;
  totalPayout: string;
  totalProfit: string;
  avgCallDuration: number;
  spamBlocked: number;
  duplicateBlocked: number;
}

interface TimeSeriesPointWire {
  period: string;
  calls: number;
  converted: number;
  revenue: string;
  payout: string;
  profit: string;
  avgDuration: number;
}

/** `/api/analytics/time-series` ships either a bare array (legacy) or the
 *  standard paginated envelope (`{ items: [...] }`) — see contract §1.3.
 *  Both shapes must map to the same point list or the chart silently
 *  renders empty. */
type TimeSeriesResponseWire =
  | TimeSeriesPointWire[]
  | { items?: TimeSeriesPointWire[] | null };

interface CallRecordWire {
  id: string;
  callerNumber: string;
  calledNumber?: string;
  destinationNumber: string;
  status: string;
  /** Backend's own qualification verdict — the same field `is_qualified=true`
   *  filters by on this endpoint. Absent on records from backends that don't
   *  send it yet; callers fall back to a local heuristic in that case (see
   *  matchesCallStatusFilter in lib/call-status.ts). */
  isQualified?: boolean;
  /* Call length. The contract (§3.13 Call Record) names this `duration_sec`;
     some CDR rows still ship the legacy `duration` / `duration_seconds`.
     Read all three — picking only one leaves the column at 00:00:00. */
  duration?: number;
  durationSec?: number;
  durationSeconds?: number;
  callerAreaCode?: string;
  callerState?: string;
  callerCountry?: string;
  campaignId?: string | null;
  campaignName?: string | null;
  buyerId?: string | null;
  buyerName?: string | null;
  publisherId?: string | null;
  publisherName?: string | null;
  revenue: string;
  payout?: string;
  buyerPayout?: string;
  publisherPayout?: string;
  recordingUrl?: string;
  recordingUri?: string;
  /* §3.13 calls this `started_at`; the CDR list endpoint has historically
     sent `created_at`. Either one is the call's start instant. */
  startedAt?: string;
  createdAt?: string;
  endedAt?: string;
  updatedAt?: string;
  tags?: unknown[];
  notes?: string;
}

interface CallLogListWire {
  total: number;
  offset: number;
  limit: number;
  items: CallRecordWire[];
}

/* ─── Frontend shapes ─────────────────────────────────────────────────── */

export interface DashboardKpis {
  totalCalls: number;
  callsToday: number;
  liveCalls: number;
  completedCalls: number;
  convertedCalls: number;
  conversionRate: number;
  totalRevenue: number;
  totalPayout: number;
  totalProfit: number;
  avgCallDurationSec: number;
  spamBlocked: number;
  duplicateBlocked: number;
}

export interface TimeSeriesPoint {
  period: string;
  calls: number;
  converted: number;
  revenue: number;
  payout: number;
  profit: number;
  avgDurationSec: number;
}

export interface CallLogPage {
  total: number;
  page: number;
  pageSize: number;
  items: Call[];
}

export interface CallLogQuery {
  page?: number;
  pageSize?: number;
  dateFrom?: string;
  dateTo?: string;
  /**
   * Raw backend status value (e.g. "completed") — NOT the normalized
   * frontend `CallStatus`. The backend's CDR `status` column only ever
   * holds `no_answer` | `completed` | `failed`, and the outbound filter
   * matches that same vocabulary — `normalizeStatus()` below just handles
   * translating those (plus a few defensive synonyms) into the frontend's
   * richer `CallStatus` enum for *inbound* records.
   */
  status?: string;
  /** Wire key: `is_qualified` (case-adapted automatically by http.ts). */
  isQualified?: boolean;
  campaignId?: string;
  buyerId?: string;
  publisherId?: string;
}

export type Granularity = "hour" | "day" | "week" | "month";

/* ─── Helpers ─────────────────────────────────────────────────────────── */

function toNum(s: string | number | undefined, fallback = 0): number {
  if (typeof s === "number") return s;
  if (typeof s === "string") {
    const n = Number(s);
    return Number.isFinite(n) ? n : fallback;
  }
  return fallback;
}

/** First candidate that is actually a finite number (or numeric string).
 *  `0` is a legitimate value, so this can't be a chain of `??`/`||`. */
function firstNum(...candidates: Array<string | number | null | undefined>): number {
  for (const c of candidates) {
    if (typeof c === "number" && Number.isFinite(c)) return c;
    if (typeof c === "string" && c.trim() !== "") {
      const n = Number(c);
      if (Number.isFinite(n)) return n;
    }
  }
  return 0;
}

function toTs(s: string | number | undefined): number {
  if (typeof s === "number") return s;
  if (typeof s === "string") {
    const t = Date.parse(s);
    return Number.isFinite(t) ? t : Date.now();
  }
  return Date.now();
}

function normalizeStatus(raw: string | null | undefined): CallStatus {
  const s = (raw ?? "").toLowerCase().replace(/_/g, "-");
  if (s === "ringing" || s === "in-progress" || s === "completed" ||
      s === "missed" || s === "rejected" || s === "failed") return s;
  if (s === "queued") return "ringing";
  if (s === "connected") return "in-progress";
  // Defensive synonyms — not values this backend actually sends (its CDR
  // `status` column is exactly `no_answer` | `completed` | `failed`, per
  // the backend team), but harmless to keep mapped in case that ever
  // changes or another backend integration uses these terms.
  if (s === "ended" || s === "answered") return "completed";
  if (s === "spam-blocked" || s === "blocked") return "rejected";
  // Never-connected outcomes the backend sends outside the §3.13 enum
  // (ringing/in-progress/completed/missed/rejected/failed) — these used to
  // fall through to the "completed" default below, which is how a
  // `no_answer` CDR ended up rendering as "Connected" in both the Call Log
  // and the Call Summary totals.
  if (s === "no-answer" || s === "unanswered" || s === "not-answered" || s === "busy") return "missed";
  if (s === "canceled" || s === "cancelled") return "rejected";
  // A genuinely unrecognized status used to default to "completed", which
  // quietly counted calls of unknown outcome as successful connections.
  // "failed" is the safer default — visibly wrong instead of confidently
  // wrong — and it's logged so a new raw value gets a real mapping added
  // above instead of silently miscategorizing calls again.
  if (raw) console.warn(`[analytics] Unrecognized call status "${raw}" — defaulting to "failed". Add a mapping in normalizeStatus().`);
  return "failed";
}

function callRecordToCall(w: CallRecordWire): Call {
  return {
    id: w.id,
    campaignId: w.campaignId ?? "",
    campaignName: w.campaignName ?? "—",
    // `||`, not `??` — the backend sends `""` for "no publisher/buyer" on
    // some rows rather than `null`, and `??` only replaces null/undefined.
    // That let an empty string reach every `c.publisherName ?? "—"` fallback
    // downstream, which also only catches nullish values — so the cell
    // rendered as a blank instead of the intended "—".
    buyerId: w.buyerId || undefined,
    buyerName: w.buyerName || undefined,
    publisherId: w.publisherId || undefined,
    publisherName: w.publisherName || undefined,
    callerNumber: w.callerNumber,
    // The wire record can name this either way (`calledNumber`/`called_number`
    // is the contract's own term for "the dialed destination"; some CDR rows
    // still ship `destinationNumber`). Reading only one silently dropped the
    // called number whenever the backend sent the other — try both, in the
    // order the contract names them.
    destinationNumber: w.calledNumber || w.destinationNumber || "",
    startedAt: toTs(w.startedAt ?? w.createdAt),
    durationSec: firstNum(w.durationSec, w.durationSeconds, w.duration),
    status: normalizeStatus(w.status),
    isQualified: w.isQualified,
    payout: firstNum(w.payout, w.buyerPayout),
    revenue: toNum(w.revenue),
    geo: {
      country: w.callerCountry ?? "",
      state: w.callerState ?? undefined,
    },
    recordingUrl: w.recordingUrl || w.recordingUri || undefined,
  };
}

function dashboardWireToKpis(w: DashboardWire): DashboardKpis {
  return {
    totalCalls: w.totalCalls,
    callsToday: w.callsToday,
    liveCalls: w.liveCalls,
    completedCalls: w.completedCalls,
    convertedCalls: w.convertedCalls,
    conversionRate: w.conversionRate,
    totalRevenue: toNum(w.totalRevenue),
    totalPayout: toNum(w.totalPayout),
    totalProfit: toNum(w.totalProfit),
    avgCallDurationSec: w.avgCallDuration,
    spamBlocked: w.spamBlocked,
    duplicateBlocked: w.duplicateBlocked,
  };
}

function timeSeriesPointToPoint(w: TimeSeriesPointWire): TimeSeriesPoint {
  return {
    period: w.period,
    calls: w.calls,
    converted: w.converted,
    revenue: toNum(w.revenue),
    payout: toNum(w.payout),
    profit: toNum(w.profit),
    avgDurationSec: w.avgDuration,
  };
}

/* ─── Public service ──────────────────────────────────────────────────── */

export const analyticsService = {
  async dashboard(): Promise<DashboardKpis> {
    const wire = await http.get<DashboardWire>("/api/analytics/dashboard");
    return dashboardWireToKpis(wire);
  },

  async timeSeries(query: {
    dateFrom?: string;
    dateTo?: string;
    granularity?: Granularity;
  } = {}): Promise<TimeSeriesPoint[]> {
    const wire = await http.get<TimeSeriesResponseWire>("/api/analytics/time-series", {
      query: {
        dateFrom: query.dateFrom,
        dateTo: query.dateTo,
        granularity: query.granularity,
      },
    });
    const items = Array.isArray(wire) ? wire : (wire?.items ?? []);
    return items.map(timeSeriesPointToPoint);
  },

  /**
   * Paginated call log. Translates page/pageSize → offset/limit on the wire.
   */
  async calls(query: CallLogQuery = {}): Promise<CallLogPage> {
    const page = query.page ?? 1;
    const pageSize = query.pageSize ?? 50;
    const offset = (page - 1) * pageSize;
    const wire = await http.get<CallLogListWire | CallRecordWire[]>("/api/analytics/calls", {
      query: {
        offset,
        limit: pageSize,
        dateFrom: query.dateFrom,
        dateTo: query.dateTo,
        status: query.status,
        isQualified: query.isQualified,
        campaignId: query.campaignId,
        buyerId: query.buyerId,
        publisherId: query.publisherId,
      },
    });
    // Same defensive unwrap as time-series — a bare array is a legacy shape.
    const rows = Array.isArray(wire) ? wire : (wire?.items ?? []);
    return {
      total: Array.isArray(wire) ? rows.length : (wire?.total ?? rows.length),
      page,
      pageSize,
      items: rows.map(callRecordToCall),
    };
  },

  /** Live snapshot — used by Live Monitor as the initial state before the
   *  WebSocket takes over. Returns whatever calls are currently in-flight. */
  async live(): Promise<Call[]> {
    const wire = await http.get<CallRecordWire[]>("/api/analytics/live");
    return wire.map(callRecordToCall);
  },

  async campaigns(): Promise<unknown> {
    return http.get("/api/analytics/campaigns");
  },

  async buyers(): Promise<unknown> {
    return http.get("/api/analytics/buyers");
  },

  async publishers(): Promise<unknown> {
    return http.get("/api/analytics/publishers");
  },

  async callerProfile(callerNumber: string): Promise<unknown> {
    return http.get(`/api/analytics/caller-profile/${encodeURIComponent(callerNumber)}`);
  },

  async recordingUrl(callId: string): Promise<{ url: string } | { recordingUrl: string }> {
    return http.get<{ url: string } | { recordingUrl: string }>(
      `/api/analytics/calls/${callId}/recording`,
    );
  },

  /** Build a CSV export URL that can be opened directly (auth header still applies). */
  async exportCallsCsv(query: Omit<CallLogQuery, "page" | "pageSize">): Promise<Blob> {
    // The backend streams CSV; use the raw http wrapper but with rawResponse.
    const res = await http.get<string>("/api/analytics/calls/export", {
      query: {
        dateFrom: query.dateFrom,
        dateTo: query.dateTo,
        status: query.status,
        campaignId: query.campaignId,
        buyerId: query.buyerId,
        publisherId: query.publisherId,
      },
      rawResponse: true,
    });
    return new Blob([typeof res === "string" ? res : JSON.stringify(res)], { type: "text/csv" });
  },
};

/* ─── Shared types re-exported for socket / call detail ──────────────── */

export type { CallRecordWire };
export { callRecordToCall, normalizeStatus, toNum, toTs };
