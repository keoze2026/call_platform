"use client";

import { useMemo, useState } from "react";
import type { DateRange } from "react-day-picker";
import { Download } from "lucide-react";
import { toast } from "sonner";

import { DestinationSummaryTable } from "@/components/dashboard/destination-summary-table";
import { RevenueChart } from "@/components/dashboard/revenue-chart";
import { TopCampaignsBars } from "@/components/dashboard/top-campaigns-bars";
import { VerticalDonut } from "@/components/dashboard/vertical-donut";
import { CallPerfCard } from "@/components/reports/call-perf-card";
import { HourlyDistribution } from "@/components/reports/hourly-distribution";
import { DateRangePicker } from "@/components/shared/date-range-picker";
import { ExportMenu } from "@/components/shared/export-menu";
import { PageHeader } from "@/components/shared/page-header";
import { TimezonePicker } from "@/components/shared/timezone-picker";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/hooks/use-translation";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { dateStamped, downloadRows, type ExportColumn, type ExportFormat } from "@/lib/export";
import { useBuyersStore } from "@/lib/store/buyers-store";
import { useCallsStore } from "@/lib/store/calls-store";
import { useDestinationsStore } from "@/lib/store/destinations-store";

const ALL_DEST = "all";

function startOfDay(d: Date) {
  const x = new Date(d);
  x.setHours(0, 0, 0, 0);
  return x;
}

function endOfDay(d: Date) {
  const x = new Date(d);
  x.setHours(23, 59, 59, 999);
  return x;
}

export default function DashboardPage() {
  const { t } = useTranslation();
  const destinations = useDestinationsStore((s) => s.destinations);
  // Calls now stream in from the backend via the calls store. The dashboard's
  // chart components still aggregate client-side off this list, so we pull a
  // generous slice (200 by default; tune via fetchRecent) at app mount.
  const recentCalls = useCallsStore((s) => s.recent);
  // Buyers (live) — for the destination-dropdown label "buyer name" column.
  const buyers = useBuyersStore((s) => s.buyers);
  const buyerById = useMemo(() => new Map(buyers.map((b) => [b.id, b])), [buyers]);
  const [destinationTfn, setDestinationTfn] = useState<string>(ALL_DEST);
  const allSelected = destinationTfn === ALL_DEST;
  // Date-range filter — same shape and default as the Reports page so the
  // two surfaces feel consistent. Default = today only; the picker offers
  // presets (Yesterday, Last 7, Last 14, This/Last month) for quick jumps.
  const [dateRange, setDateRange] = useState<DateRange | undefined>(() => {
    const today = new Date();
    return { from: today, to: today };
  });

  // Calls-today count per destination TFN — used inside the destination
  // dropdown's secondary label so the operator can see at a glance which
  // TFNs are hot today. Independent of the chosen date range above.
  const callsTodayByTfn = useMemo(() => {
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    const map = new Map<string, number>();
    for (const c of recentCalls) {
      if (c.startedAt < start.getTime()) continue;
      map.set(c.destinationNumber, (map.get(c.destinationNumber) ?? 0) + 1);
    }
    return map;
  }, [recentCalls]);

  // Apply the date-range filter first, then the destination filter, then
  // hand the result to the charts. Mirrors the Reports page exactly.
  const dateFilteredCalls = useMemo(() => {
    const start = dateRange?.from ? startOfDay(dateRange.from).getTime() : -Infinity;
    const end = dateRange?.from
      ? endOfDay(dateRange.to ?? dateRange.from).getTime()
      : Infinity;
    return recentCalls.filter((c) => c.startedAt >= start && c.startedAt <= end);
  }, [recentCalls, dateRange]);

  // When a destination is selected, scope everything to just its calls.
  const scopedCalls = useMemo(() => {
    if (allSelected) return dateFilteredCalls;
    return dateFilteredCalls.filter((c) => c.destinationNumber === destinationTfn);
  }, [destinationTfn, allSelected, dateFilteredCalls]);

  const summary = useMemo(() => ({
    revenue: scopedCalls.reduce((s, c) => s + c.revenue, 0),
    payout: scopedCalls.reduce((s, c) => s + c.payout, 0),
  }), [scopedCalls]);

  const onExport = (format: ExportFormat) => {
    const rows = buildDestinationExportRows(
      destinations,
      allSelected ? undefined : destinationTfn,
    );
    const stem = dateStamped(
      allSelected ? "vortyx-dashboard" : `vortyx-dashboard-${destinationTfn.replace(/\D/g, "")}`,
    );
    downloadRows(format, DASHBOARD_EXPORT_COLUMNS, rows, stem, "Destinations");
    toast.success(`Exported ${rows.length} destinations to ${format.toUpperCase()}`);
  };

  return (
    <>
      <PageHeader
        title={t("page.dashboard.title")}
        description={t("page.dashboard.description")}
        actions={
          <>
            <TimezonePicker />
            <Select value={destinationTfn} onValueChange={setDestinationTfn}>
              <SelectTrigger size="sm" className="w-[20rem]">
                <SelectValue placeholder={t("dashboard.allDestinations")} />
              </SelectTrigger>
              <SelectContent align="end" className="max-h-80">
                <SelectItem value={ALL_DEST}>{t("dashboard.allDestinations")}</SelectItem>
                {destinations.map((d) => {
                  const buyer = buyerById.get(d.buyerId);
                  const calls = callsTodayByTfn.get(d.tfn) ?? 0;
                  return (
                    <SelectItem key={d.id} value={d.tfn}>
                      <span className="flex items-center gap-2">
                        <span className="font-medium">{d.name}</span>
                        <span className="font-mono text-[10px] text-muted-foreground">
                          {d.tfn}
                        </span>
                        <span className="text-[10px] text-muted-foreground">
                          {buyer?.name ?? "—"} · {calls} {t("dashboard.callsToday")}
                        </span>
                      </span>
                    </SelectItem>
                  );
                })}
              </SelectContent>
            </Select>
            <DateRangePicker value={dateRange} onChange={setDateRange} />
            <ExportMenu onExport={onExport}>
              <Button variant="outline" size="sm">
                <Download className="h-4 w-4" /> {t("common.export")}
              </Button>
            </ExportMenu>
          </>
        }
      />

      {/* Row 1 — Hourly CALLS chart (primary) + donut on the right.
          Uses the same composed-chart component as the Reports page so the
          two surfaces share an identical visual language. */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* h-full on both wrapper and card so the chart matches the height of
            the two stacked cards beside it; the chart then centres in the
            extra space rather than leaving a gap at the bottom. */}
        <div className="h-full lg:col-span-2">
          <HourlyDistribution calls={scopedCalls} className="h-full" />
        </div>
        <div className="flex h-full min-w-0 flex-col gap-4">
          <CallPerfCard revenue={summary.revenue} payout={summary.payout} />
          <div className="min-h-0 flex-1">
            <VerticalDonut calls={scopedCalls} />
          </div>
        </div>
      </div>

      {/* Row 2 — Top campaigns + Revenue trend (secondary) */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <TopCampaignsBars calls={scopedCalls} />
        <RevenueChart calls={scopedCalls} />
      </div>

      {/* Row 3 — Destinations table (each TFN with its own CC and Cap) */}
      <DestinationSummaryTable
        destinationFilter={allSelected ? undefined : destinationTfn}
      />
    </>
  );
}

/* ─── Export support ─── */

interface DestinationExportRow {
  destination: string;
  tfn: string;
  buyer: string;
  callsToday: number;
  revenueToday: number;
  concurrent: number;
  dailyCap: number;
  capPct: number;
}

const DASHBOARD_EXPORT_COLUMNS: ExportColumn<DestinationExportRow>[] = [
  { label: "Destination", value: (r) => r.destination },
  { label: "TFN", value: (r) => r.tfn },
  { label: "Buyer", value: (r) => r.buyer },
  { label: "Calls today", value: (r) => r.callsToday },
  { label: "Revenue today", value: (r) => Number(r.revenueToday.toFixed(2)) },
  { label: "Concurrent", value: (r) => r.concurrent },
  { label: "Daily cap", value: (r) => r.dailyCap },
  { label: "Cap %", value: (r) => Number(r.capPct.toFixed(1)) },
];

/** Mirror the on-screen Destinations card, scoped to the selected TFN if any. */
function buildDestinationExportRows(
  destinations: ReturnType<typeof useDestinationsStore.getState>["destinations"],
  filter: string | undefined,
): DestinationExportRow[] {
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const startMs = startOfToday.getTime();

  // Pull the cached calls directly from the store — no React hook here since
  // this builder runs during the export click handler, not in render.
  const recentCalls = useCallsStore.getState().recent;

  const callsByTfn = new Map<string, number>();
  const revenueByTfn = new Map<string, number>();
  const ccByTfn = new Map<string, number>();
  for (const c of recentCalls) {
    if (c.startedAt >= startMs) {
      callsByTfn.set(c.destinationNumber, (callsByTfn.get(c.destinationNumber) ?? 0) + 1);
      revenueByTfn.set(
        c.destinationNumber,
        (revenueByTfn.get(c.destinationNumber) ?? 0) + c.revenue,
      );
    }
    if (c.status === "ringing" || c.status === "in-progress") {
      ccByTfn.set(c.destinationNumber, (ccByTfn.get(c.destinationNumber) ?? 0) + 1);
    }
  }

  // Buyers are pulled non-hook from the store since this runs at click time.
  const buyerById = new Map(
    useBuyersStore.getState().buyers.map((b) => [b.id, b]),
  );

  return destinations
    .filter((d) => !filter || d.tfn === filter)
    .map<DestinationExportRow>((d) => {
      const callsToday = callsByTfn.get(d.tfn) ?? 0;
      return {
        destination: d.name,
        tfn: d.tfn,
        buyer: buyerById.get(d.buyerId)?.name ?? "—",
        callsToday,
        revenueToday: revenueByTfn.get(d.tfn) ?? 0,
        concurrent: ccByTfn.get(d.tfn) ?? 0,
        dailyCap: d.dailyCap,
        capPct: d.dailyCap > 0 ? Math.min(100, (callsToday / d.dailyCap) * 100) : 0,
      };
    })
    .sort((a, b) => b.revenueToday - a.revenueToday);
}
