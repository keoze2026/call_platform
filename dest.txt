/**
 * Destinations service — /api/destinations/*.
 *
 * Endpoints (confirmed by backend dev, June 2026):
 *   GET    /api/destinations/?page=&page_size=&status=&buyer_id=&search=
 *   GET    /api/destinations/stats/   → header roll-ups across the org
 *   GET    /api/destinations/{id}/
 *   POST   /api/destinations/
 *   PATCH  /api/destinations/{id}/    (also toggles enabled via { enabled })
 *   DELETE /api/destinations/{id}/
 *
 * A Destination is a buyer-owned dial target with name, TFN/SIP URI, caps,
 * filters, business hours, and live usage counters. The wire shape mirrors
 * the frontend `Destination` type via the case-adapter; usage counters and
 * `buyer_name` are read-only echoes.
 */

import { http } from "@/lib/api/http";
import type { Paginated } from "@/lib/api/types";
import type {
  BusinessHourSlot,
  Destination,
  DestinationForwardType,
  FilterGroup,
} from "@/lib/types";

/* ─── Frontend shapes (in addition to the canonical Destination type) ─── */

export interface DestinationStats {
  activeLive: number;
  totalLive: number;
  totalCC: number;
  activeTfns: number;
  vacantCC: number;
}

export interface DestinationListQuery {
  page?: number;
  pageSize?: number;
  status?: "all" | "enabled" | "disabled";
  buyerId?: string;
  search?: string;
}

/* ─── Wire shapes ─────────────────────────────────────────────────────── */

interface DestinationWire {
  id: string;
  buyerId: string;
  buyerName?: string;
  tfn: string;
  name: string;
  /**
   * Canonical wire field is `routing_type` (camelCased to `routingType` by
   * the http layer). Backend enum: `external` | `sip`.
   *
   * We also tolerate legacy `forward_type` echoes from older responses —
   * either field is read on the way in and mapped to the FE's
   * `DestinationForwardType` (`number` | `sip`) below.
   */
  routingType?: string;
  forwardType?: string;
  concurrencyCap: number;
  hourlyCap?: number;
  dailyCap: number;
  monthlyCap: number;
  globalCap?: number;
  enabled: boolean;
  ringDurationSec?: number;
  timezone?: string | null;
  liveCalls?: number;
  hourlyCalls?: number;
  /** Canonical per-row counter: `calls_today` on the wire. */
  callsToday?: number;
  /** Older spelling of the same counter — read as a fallback. */
  dailyCalls?: number;
  monthlyCalls?: number;
  globalCalls?: number;
  filterEnabled?: boolean;
  filterGroups?: FilterGroup[];
  businessHoursEnabled?: boolean;
  businessHourSlots?: BusinessHourSlot[];
  createdAt?: string;
  updatedAt?: string;
}

interface DestinationStatsWire {
  activeLive: number;
  totalLive: number;
  totalCc: number;
  activeTfns: number;
  vacantCc: number;
}

/* ─── Mappers ─────────────────────────────────────────────────────────── */

/**
 * The backend enum is `external` | `sip`; the FE type is the older
 * `number` | `sip`. Map at the wire boundary so the rest of the app
 * doesn't need to learn the new vocabulary.
 */
function normalizeForwardType(raw: string | null | undefined): DestinationForwardType {
  const v = (raw ?? "").toLowerCase();
  return v === "sip" ? "sip" : "number";
}

/** FE forward type → wire `routing_type` enum (`external` | `sip`). */
function forwardTypeToWire(t: DestinationForwardType | undefined): "external" | "sip" | undefined {
  if (t === "sip") return "sip";
  if (t === "number") return "external";
  return undefined; // unknown / empty → omit so backend default kicks in
}

function wireToDestination(w: DestinationWire): Destination {
  return {
    id: w.id,
    buyerId: w.buyerId,
    tfn: w.tfn,
    name: w.name,
    // Prefer the canonical `routing_type` field; fall back to legacy
    // `forward_type` if the backend ever echoes it instead.
    forwardType: normalizeForwardType(w.routingType ?? w.forwardType),
    concurrencyCap: w.concurrencyCap ?? 0,
    dailyCap: w.dailyCap ?? 0,
    monthlyCap: w.monthlyCap ?? 0,
    enabled: !!w.enabled,
    ringDurationSec: w.ringDurationSec ?? 25,
    filterEnabled: !!w.filterEnabled,
    filterGroups: Array.isArray(w.filterGroups) ? w.filterGroups : [],
    businessHoursEnabled: !!w.businessHoursEnabled,
    businessHourSlots: Array.isArray(w.businessHourSlots) ? w.businessHourSlots : [],
    timezone: w.timezone ?? undefined,
    // These were already declared on `DestinationWire` above but never
    // actually read here — the Destinations table's LIVE/HOURLY/DAILY/
    // MONTHLY/GLOBAL columns were instead deriving their own counts
    // client-side from the calls cache, which structurally can't contain
    // ringing/in-progress rows (that cache is a completed-call log). Wiring
    // these through is what actually fixes the LIVE column.
    liveCalls: w.liveCalls ?? 0,
    hourlyCalls: w.hourlyCalls ?? 0,
    // The dashboard's Destinations table shows this for "today" directly
    // (no client-side tally of the calls cache, which holds completed
    // calls only). `calls_today` is the field the backend documents;
    // `daily_calls` is tolerated from older responses.
    dailyCalls: w.callsToday ?? w.dailyCalls ?? 0,
    monthlyCalls: w.monthlyCalls ?? 0,
    globalCalls: w.globalCalls ?? 0,
  };
}

/** Build a writable subset of the wire shape from a Destination patch.
 *  Skips read-only / computed fields (live counters, buyer_name, timestamps). */
function destinationToWire(patch: Partial<Destination>): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  // buyerId is optional — empty string means "no buyer assigned yet", send
  // null so the backend stores an unassigned destination.
  if (patch.buyerId !== undefined) {
    body.buyerId = patch.buyerId === "" ? null : patch.buyerId;
  }
  if (patch.tfn !== undefined) body.tfn = patch.tfn;
  if (patch.name !== undefined) body.name = patch.name;
  // Backend schema: `routing_type` is enum (`external` | `sip`). Empty
  // string fails validation, so we omit the field entirely when the FE
  // value is missing / unknown and let the backend's `external` default
  // kick in.
  if (patch.forwardType !== undefined) {
    const mapped = forwardTypeToWire(patch.forwardType);
    if (mapped !== undefined) body.routingType = mapped;
  }
  if (patch.concurrencyCap !== undefined) body.concurrencyCap = patch.concurrencyCap;
  if (patch.dailyCap !== undefined) body.dailyCap = patch.dailyCap;
  if (patch.monthlyCap !== undefined) body.monthlyCap = patch.monthlyCap;
  if (patch.enabled !== undefined) body.enabled = patch.enabled;
  if (patch.ringDurationSec !== undefined) body.ringDurationSec = patch.ringDurationSec;
  if (patch.timezone !== undefined) body.timezone = patch.timezone ?? null;
  if (patch.filterEnabled !== undefined) body.filterEnabled = patch.filterEnabled;
  if (patch.filterGroups !== undefined) body.filterGroups = patch.filterGroups;
  if (patch.businessHoursEnabled !== undefined) body.businessHoursEnabled = patch.businessHoursEnabled;
  if (patch.businessHourSlots !== undefined) body.businessHourSlots = patch.businessHourSlots;
  return body;
}

/* ─── Public service ──────────────────────────────────────────────────── */

export const destinationsService = {
  async list(query: DestinationListQuery = {}): Promise<Paginated<Destination>> {
    const res = await http.get<Paginated<DestinationWire>>("/api/destinations/", { query });
    return { ...res, items: res.items.map(wireToDestination) };
  },

  async get(id: string): Promise<Destination> {
    return wireToDestination(await http.get<DestinationWire>(`/api/destinations/${id}/`));
  },

  async create(input: Omit<Destination, "id">): Promise<Destination> {
    return wireToDestination(
      await http.post<DestinationWire>("/api/destinations/", { body: destinationToWire(input) }),
    );
  },

  async update(id: string, patch: Partial<Destination>): Promise<Destination> {
    return wireToDestination(
      await http.patch<DestinationWire>(`/api/destinations/${id}/`, {
        body: destinationToWire(patch),
      }),
    );
  },

  async remove(id: string): Promise<void> {
    await http.delete(`/api/destinations/${id}/`);
  },

  async setEnabled(id: string, enabled: boolean): Promise<Destination> {
    return wireToDestination(
      await http.patch<DestinationWire>(`/api/destinations/${id}/`, { body: { enabled } }),
    );
  },

  async stats(): Promise<DestinationStats> {
    const w = await http.get<DestinationStatsWire>("/api/destinations/stats/");
    return {
      activeLive: w.activeLive ?? 0,
      totalLive: w.totalLive ?? 0,
      totalCC: w.totalCc ?? 0,
      activeTfns: w.activeTfns ?? 0,
      vacantCC: w.vacantCc ?? 0,
    };
  },
};
