"use client";

import { useMemo } from "react";
import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

import { CallWaveform } from "@/components/live/call-waveform";
import { Card } from "@/components/ui/card";
import { useTranslation } from "@/hooks/use-translation";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ROUTES } from "@/lib/constants";
import { useBuyersStore } from "@/lib/store/buyers-store";
import { useCallsStore } from "@/lib/store/calls-store";
import { useDestinationsStore } from "@/lib/store/destinations-store";
import { formatCurrency, formatNumber, toE164 } from "@/lib/format";
import type { Buyer, Call, Destination } from "@/lib/types";
import { cn } from "@/lib/utils";

interface DestinationSummaryTableProps {
  /** When set, only render the destination matching this TFN. */
  destinationFilter?: string;
  limit?: number;
}

interface Row {
  destination: Destination;
  buyer: Buyer | undefined;
  cc: number;
  callsToday: number;
  revenueToday: number;
  capPct: number;
}

/** Small green pill — every row in this table is, by filter, active. */
function ActiveBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-[oklch(0.78_0.18_155)]/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-[oklch(0.5_0.18_155)] dark:text-[oklch(0.78_0.18_155)]">
      <span aria-hidden className="h-1 w-1 rounded-full bg-current" />
      Active
    </span>
  );
}

function buildRows(
  destinations: Destination[],
  filter: string | undefined,
  limit: number,
  recentCalls: Call[],
  buyers: Buyer[],
): Row[] {
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const startMs = startOfToday.getTime();

  // Pre-compute per-destination (keyed by TFN) call aggregates from the
  // shared calls cache. Pass in from the component so the table reactively
  // updates when fresh calls land.
  //
  // `cc` (concurrent/live calls) is NOT derived from this cache — it used to
  // count `ringing`/`in-progress` rows here, but the cache is backed by
  // `/api/analytics/calls`, a completed-call log that structurally can't
  // contain one (the backend's CDR `status` column only ever holds
  // `no_answer` | `completed` | `failed`). It reads `destination.liveCalls`
  // instead, straight off the Destination record.
  const callsByTfn = new Map<string, number>();
  const revenueByTfn = new Map<string, number>();
  for (const c of recentCalls) {
    if (c.startedAt >= startMs) {
      callsByTfn.set(c.destinationNumber, (callsByTfn.get(c.destinationNumber) ?? 0) + 1);
      revenueByTfn.set(
        c.destinationNumber,
        (revenueByTfn.get(c.destinationNumber) ?? 0) + c.revenue,
      );
    }
  }

  const buyerById = new Map<string, Buyer>();
  for (const b of buyers) buyerById.set(b.id, b);

  return destinations
    .filter((d) => !filter || d.tfn === filter)
    // Only active destinations attached to an active buyer surface here.
    // A paused destination (`enabled === false`) or one whose buyer is paused
    // / capped / pending is hidden so the operator sees live inventory only.
    .filter((d) => {
      if (!d.enabled) return false;
      const buyer = buyerById.get(d.buyerId);
      return buyer?.status === "active";
    })
    .map<Row>((destination) => {
      const callsToday = callsByTfn.get(destination.tfn) ?? 0;
      const cap = destination.dailyCap;
      return {
        destination,
        buyer: buyerById.get(destination.buyerId),
        cc: destination.liveCalls,
        callsToday,
        revenueToday: revenueByTfn.get(destination.tfn) ?? 0,
        capPct: cap > 0 ? Math.min(100, (callsToday / cap) * 100) : 0,
      };
    })
    .sort((a, b) => b.revenueToday - a.revenueToday)
    .slice(0, limit);
}

export function DestinationSummaryTable({
  destinationFilter,
  limit = 12,
}: DestinationSummaryTableProps) {
  const { t } = useTranslation();
  const destinations = useDestinationsStore((s) => s.destinations);
  const recentCalls = useCallsStore((s) => s.recent);
  const buyers = useBuyersStore((s) => s.buyers);
  const rows = useMemo(
    () => buildRows(destinations, destinationFilter, limit, recentCalls, buyers),
    [destinations, destinationFilter, limit, recentCalls, buyers],
  );

  return (
    <Card className="overflow-hidden p-0">
      <div className="flex items-center justify-between gap-2 border-b border-border px-6 py-4">
        <div>
          <h3 className="text-[13px] font-semibold uppercase tracking-wider">{t("dashboard.destinations")}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t("dashboard.destinationsHint")}
          </p>
        </div>
        <Link
          href={ROUTES.buyers}
          className="inline-flex items-center gap-0.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          {t("nav.buyers")} <ArrowUpRight className="h-3 w-3" />
        </Link>
      </div>

      <div className="overflow-x-auto">
        <Table className="min-w-[980px] [&_tr]:border-b-0 [&_td]:py-2 [&_th]:h-8 [&_th]:py-1.5 [&_th]:text-[10px] [&_th]:font-semibold [&_th]:uppercase [&_th]:tracking-wider">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-[220px] pl-6 text-left">{t("dashboard.columns.destination")}</TableHead>
              <TableHead className="w-[160px] !text-left">{t("dashboard.columns.status")}</TableHead>
              <TableHead className="text-left">{t("dashboard.columns.buyer")}</TableHead>
              <TableHead className="text-center">{t("dashboard.columns.live")}</TableHead>
              <TableHead className="text-right">{t("dashboard.columns.capToday")}</TableHead>
              <TableHead className="text-center">{t("dashboard.columns.callsToday")}</TableHead>
              <TableHead className="pr-6 text-center">{t("dashboard.columns.revenueToday")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <TableRow className="hover:bg-transparent">
                <TableCell colSpan={7} className="pl-6 py-8 text-center text-sm text-muted-foreground">
                  No destinations.
                </TableCell>
              </TableRow>
            ) : (
              rows.map(({ destination, buyer, cc, callsToday, revenueToday, capPct }) => {
                const ccPct = destination.concurrencyCap > 0
                  ? (cc / destination.concurrencyCap) * 100
                  : 0;
                // Live concurrency color ramp:
                //   0–69%   bright Won-green (healthy)
                //   70–89%  amber (heads-up)
                //   90%+    red (over the ceiling)
                const ccColor =
                  ccPct >= 90 ? "text-destructive" :
                  ccPct >= 70 ? "text-[color:var(--warning)]" :
                  "text-[oklch(0.5_0.18_155)] dark:text-[oklch(0.78_0.18_155)]";
                const capColor =
                  capPct >= 90 ? "bg-destructive" :
                  capPct >= 70 ? "bg-[color:var(--warning)]" :
                  "bg-accent";
                return (
                  <TableRow key={destination.id}>
                    <TableCell className="w-[220px] pl-6 text-left">
                      <div className="text-[13px] font-medium leading-tight">
                        {destination.name}
                      </div>
                      <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                        {toE164(destination.tfn)}
                      </div>
                    </TableCell>
                    <TableCell className="w-[160px] !text-left">
                      <ActiveBadge />
                    </TableCell>
                    <TableCell className="text-left">
                      {buyer ? (
                        <Link
                          href={`${ROUTES.buyers}/${buyer.id}`}
                          className="text-xs transition-colors hover:text-accent"
                        >
                          {buyer.name}
                        </Link>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs tabular-nums">
                      {/* Four fixed slots — waveform · current · "/ cap" —
                          so every row's slash and total line up regardless of
                          whether the row has a live waveform or 1-vs-2-digit
                          numbers. The waveform color tracks the same green /
                          amber / red ramp as the current count. */}
                      <div className="flex items-center justify-center gap-1.5">
                        <span
                          className={cn(
                            "inline-flex w-4 shrink-0 justify-center",
                            cc > 0 ? ccColor : "opacity-0",
                          )}
                        >
                          {cc > 0 && (
                            <CallWaveform
                              size="sm"
                              bars={4}
                              label={`${cc} live ${cc === 1 ? "call" : "calls"}`}
                            />
                          )}
                        </span>
                        <span
                          className={cn(
                            "w-4 text-right font-medium",
                            cc > 0 ? ccColor : "text-muted-foreground",
                          )}
                        >
                          {cc}
                        </span>
                        <span className="text-muted-foreground">/</span>
                        <span className="w-6 text-left text-muted-foreground">
                          {destination.concurrencyCap}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      {destination.dailyCap > 0 ? (
                        <div className="ml-auto flex w-fit flex-col gap-0.5">
                          <div className="flex items-baseline justify-end gap-2 text-[11px] tabular-nums">
                            <span className="font-medium">
                              {formatNumber(callsToday)} / {formatNumber(destination.dailyCap)}
                            </span>
                            <span className="text-muted-foreground">{Math.round(capPct)}%</span>
                          </div>
                          <div className="h-0.5 w-28 overflow-hidden rounded-full bg-muted">
                            <div
                              className={cn("h-full rounded-full transition-[width]", capColor)}
                              style={{ width: `${capPct}%` }}
                            />
                          </div>
                        </div>
                      ) : (
                        <span className="text-[11px] text-muted-foreground">Unlimited</span>
                      )}
                    </TableCell>
                    <TableCell className="text-center text-xs tabular-nums">
                      {formatNumber(callsToday)}
                    </TableCell>
                    <TableCell className="pr-6 text-center text-xs font-medium tabular-nums">
                      {formatCurrency(revenueToday)}
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>
    </Card>
  );
}
