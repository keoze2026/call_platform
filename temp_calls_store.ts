/**
 * Calls store — backed by /api/analytics/calls (paginated) and
 * /api/analytics/dashboard for the headline KPIs.
 *
 * Keeps a rolling cache of recent calls (last `pageSize` rows) so the
 * dashboard, reports page, and topbar counters can read synchronously.
 * Heavy filtering / pagination is delegated to the backend via the
 * `fetchPage()` method which the Call Log table uses directly.
 */

"use client";

import { create } from "zustand";

import {
  analyticsService,
  type CallLogPage,
  type CallLogQuery,
  type DashboardKpis,
  type TimeSeriesPoint,
} from "@/lib/api/services/analytics.service";
import type { Call } from "@/lib/types";

interface CallsState {
  /** Most recent N calls — used by the dashboard's chart components. */
  recent: Call[];
  /** Headline KPIs from /api/analytics/dashboard. */
  kpis: DashboardKpis | null;
  /** Cached time-series for the dashboard hourly/day chart. */
  timeSeries: TimeSeriesPoint[];
  /** Real-time in-flight call count, written by useLiveSocket on every
   *  WebSocket event. Topbar reads this instead of the stale kpis.liveCalls. */
  liveCount: number;

  loading: boolean;
  error: string | null;
  hydrated: boolean;

  fetchRecent: (pageSize?: number) => Promise<void>;
  fetchKpis: () => Promise<void>;
  fetchTimeSeries: (query?: { dateFrom?: string; dateTo?: string; granularity?: "hour" | "day" | "week" | "month" }) => Promise<void>;
  fetchPage: (query: CallLogQuery) => Promise<CallLogPage>;
  setLiveCount: (n: number) => void;
}

const RECENT_DEFAULT = 200;

export const useCallsStore = create<CallsState>()((set) => ({
  recent: [],
  kpis: null,
  timeSeries: [],
  liveCount: 0,
  loading: false,
  error: null,
  hydrated: false,

  fetchRecent: async (pageSize = RECENT_DEFAULT) => {
    set({ loading: true, error: null });
    try {
      const page = await analyticsService.calls({ page: 1, pageSize });
      set({ recent: page.items, loading: false, hydrated: true });
    } catch (e) {
      set({ loading: false, error: messageFromError(e) });
    }
  },

  fetchKpis: async () => {
    try {
      const kpis = await analyticsService.dashboard();
      set({ kpis });
    } catch (e) {
      set({ error: messageFromError(e) });
    }
  },

  fetchTimeSeries: async (query = {}) => {
    try {
      const timeSeries = await analyticsService.timeSeries(query);
      set({ timeSeries });
    } catch (e) {
      set({ error: messageFromError(e) });
    }
  },

  // Pass-through to the analytics service; callers manage their own paging UI.
  fetchPage: (query) => analyticsService.calls(query),

  setLiveCount: (n) => set({ liveCount: n }),
}));

function messageFromError(e: unknown): string {
  if (e instanceof Error) return e.message;
  return "Calls request failed";
}
