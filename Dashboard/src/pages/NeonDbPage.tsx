import { useMemo, useState } from "react";
import { neonUsageApi } from "../api/neonUsageApi";
import type { NeonAssumptions, NeonTransferRow, NeonWindowItem, NeonWindowsResponse } from "../api/types";
import { useFeed } from "../hooks/useFeed";
import { useTabWatermarks } from "../hooks/useTabWatermarks";
import { formatBytes, formatInt } from "../lib/formatters";
import { formatIstTime, isoToSeconds, nowSeconds } from "../lib/ist";
import { useToast } from "../components/Toast";
import { groupByHour, HourlyCards } from "../components/HourlyCards";
import { Badge, Button, EmptyState, Note, Panel, SkeletonRows } from "../components/Primitives";
import { IconCheck, IconInfo, IconLayers, IconRefresh, IconServer, IconTrash, IconZap } from "../components/Icons";

type Capsule = "compute" | "transfer";

const CAPSULES: { id: Capsule; label: string; icon: (props: { size?: number }) => JSX.Element }[] = [
  { id: "compute", label: "CU Hours", icon: IconZap },
  { id: "transfer", label: "Network Transfer", icon: IconLayers },
];

/** Until the first response names them — Neon's free-tier defaults, the same
 *  constants the backend starts with. */
const DEFAULT_ASSUMPTIONS: NeonAssumptions = { compute_units: 0.25, autosuspend_seconds: 300, tracking_since: null };

function TriggerBadge({ kind }: { kind: string }) {
  const tone = kind === "API request" ? "info" : kind === "Background job" ? "warn" : "neutral";
  return <Badge tone={tone}>{kind}</Badge>;
}

/** The thin bar showing how much of the 48-hour total one row is responsible
 *  for — the fastest way to spot the expensive one. */
function ShareBar({ percent }: { percent: number }) {
  return (
    <div className="event__bar" title={`${percent}% of the last 48 hours`}>
      <span style={{ width: `${Math.min(100, Math.max(2, percent))}%` }} />
    </div>
  );
}

function share(value: number, total: number): number {
  return total > 0 ? Math.round((value / total) * 1000) / 10 : 0;
}

export default function NeonDbPage() {
  const toast = useToast();
  const [capsule, setCapsule] = useState<Capsule>("compute");
  const { watermarkFor, clear, version } = useTabWatermarks("neon-clear");

  // Reads the backend's in-memory counters only — this poll never reaches
  // Neon, so watching the cost cannot add to the cost.
  const feed = useFeed<NeonWindowItem, NeonWindowsResponse>({
    storageKey: "neon-windows-v1",
    fetchPage: neonUsageApi.getWindows,
    keyOf: (item) => item.k,
    timeOf: (item) => isoToSeconds(item.compute.ended_at),
    intervalMs: 20_000,
  });
  const assumptions = feed.page?.assumptions ?? DEFAULT_ASSUMPTIONS;
  const idleMinutes = Math.round(assumptions.autosuspend_seconds / 60);

  // CU Hours and Network Transfer are two views of the SAME windows, but
  // each is filtered against its OWN cutoff — clearing one can never affect
  // the other, even though both are read off feed.items.
  const computeGroups = useMemo(
    () =>
      groupByHour(
        feed.items.filter((item) => isoToSeconds(item.compute.at) >= watermarkFor("compute")),
        (item) => isoToSeconds(item.compute.at),
      ).map((group) => ({
        start: group.start,
        items: [...group.items].sort((a, b) => isoToSeconds(b.compute.at) - isoToSeconds(a.compute.at)),
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [feed.items, watermarkFor, version],
  );
  const transferGroups = useMemo(
    () =>
      groupByHour(
        feed.items.flatMap((item) => item.transfer).filter((row) => isoToSeconds(row.at) >= watermarkFor("transfer")),
        (row: NeonTransferRow) => isoToSeconds(row.at),
      ).map((group) => ({
        start: group.start,
        items: [...group.items].sort((a, b) => isoToSeconds(b.at) - isoToSeconds(a.at) || b.bytes - a.bytes),
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [feed.items, watermarkFor, version],
  );

  const totals = useMemo(() => {
    let cuHours = 0;
    let queries = 0;
    let wakeups = 0;
    for (const group of computeGroups) {
      for (const item of group.items) {
        cuHours += item.compute.cu_hours;
        queries += item.compute.queries;
        wakeups += 1;
      }
    }
    let bytes = 0;
    let transferRows = 0;
    for (const group of transferGroups) {
      for (const row of group.items) {
        bytes += row.bytes;
        transferRows += 1;
      }
    }
    return { cuHours, queries, wakeups, bytes, transferRows };
  }, [computeGroups, transferGroups]);

  const showData = feed.ready || feed.items.length > 0;

  function clearCompute() {
    clear("compute");
    toast.push({ tone: "ok", title: "Cleared", message: "CU Hours is back to 0. Network Transfer is untouched." });
  }

  function clearTransfer() {
    clear("transfer");
    toast.push({ tone: "ok", title: "Cleared", message: "Network Transfer is back to 0. CU Hours is untouched." });
  }

  return (
    <div className="stack stack-6">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Neon · what spends the quota</div>
          <h1 className="page-title">Neon DB</h1>
          <p className="section-head__sub">
            Neon's own dashboard tells you the totals but never what caused them. This does: every database operation
            this app runs is measured, grouped hour by hour (IST) for the last 48 hours — click an hour to see exactly
            which operation spent the CU-hours and which one moved the data.
          </p>
        </div>
        {showData && (
          <div className="row-flex">
            <Badge tone="info" live title="Updated automatically every 20 seconds">
              Live
            </Badge>
            <Button size="sm" icon={<IconRefresh size={15} />} onClick={feed.refresh}>
              Refresh
            </Button>
          </div>
        )}
      </header>

      {feed.error && (
        <Panel>
          <p style={{ color: "var(--bad)", margin: 0 }}>{feed.error}</p>
        </Panel>
      )}

      {!showData && !feed.error && <SkeletonRows rows={4} />}

      {showData && (
        <>
          <Note tone="info" icon={<IconCheck size={17} />}>
            <strong>This page is free to watch.</strong> Every number below is measured inside the backend as your
            app's own queries run, kept in memory and in a local file — opening or refreshing this tab makes{" "}
            <strong>no database call at all</strong>, so it can never add to the CU-hours or the transfer it is
            reporting on. Each poll only downloads what changed since the last one.
          </Note>

          <div className="metrics-grid">
            <div className="metric-tile metric-tile--totals">
              <span className="metric-tile__label">CU-hours · 48 h (estimated)</span>
              <span className="metric-tile__value">{totals.cuHours.toFixed(4)}</span>
            </div>
            <div className="metric-tile metric-tile--totals">
              <span className="metric-tile__label">Network transfer · 48 h</span>
              <span className="metric-tile__value">{formatBytes(totals.bytes)}</span>
            </div>
            <div className="metric-tile metric-tile--per-call">
              <span className="metric-tile__label">Wake-ups · 48 h</span>
              <span className="metric-tile__value">{formatInt(totals.wakeups)}</span>
            </div>
            <div className="metric-tile metric-tile--per-call">
              <span className="metric-tile__label">Queries · 48 h</span>
              <span className="metric-tile__value">{formatInt(totals.queries)}</span>
            </div>
          </div>

          <nav className="capsule-tabs" aria-label="Neon cost type">
            {CAPSULES.map((tab) => {
              const Icon = tab.icon;
              const active = tab.id === capsule;
              const count = tab.id === "compute" ? totals.wakeups : totals.transferRows;
              return (
                <button
                  key={tab.id}
                  type="button"
                  className={`capsule-tab${active ? " capsule-tab--active" : ""}`}
                  aria-current={active ? "page" : undefined}
                  onClick={() => setCapsule(tab.id)}
                >
                  <Icon size={15} />
                  <span>{tab.label}</span>
                  <span className="capsule-tab__count">{count}</span>
                </button>
              );
            })}
          </nav>

          {capsule === "compute" ? (
            <Panel className="stack stack-4">
              <div className="row-between">
                <div>
                  <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                    <IconZap size={12} /> What spent CU-hours
                  </div>
                  <h2>Every time something woke the database, hour by hour</h2>
                  <p className="section-head__sub" style={{ marginTop: 6 }}>
                    Neon charges for the time the compute is <strong>awake</strong>, not the time it spends working. It
                    only sleeps after {idleMinutes} idle minutes — so one stray query at a quiet moment costs the same as
                    a busy {idleMinutes} minutes. Each card inside an hour is one wake-up that started in that hour.
                  </p>
                </div>
                <Button size="sm" tone="danger" onClick={clearCompute} icon={<IconTrash size={15} />}>
                  Clear this tab
                </Button>
              </div>

              <HourlyCards
                groups={computeGroups}
                stats={(group) => {
                  let cuHours = 0;
                  let queries = 0;
                  for (const item of group.items) {
                    cuHours += item.compute.cu_hours;
                    queries += item.compute.queries;
                  }
                  return [
                    { label: "CU-hours", value: cuHours.toFixed(4), accent: true },
                    { label: "Wake-ups", value: formatInt(group.items.length) },
                    { label: "Queries", value: formatInt(queries) },
                  ];
                }}
                renderBody={(group) =>
                  group.items.map((item) => {
                    const row = item.compute;
                    const percent = share(row.cu_hours, totals.cuHours);
                    const stillAwake = nowSeconds() - isoToSeconds(row.ended_at) <= assumptions.autosuspend_seconds;
                    return (
                      <div key={item.k} className="event">
                        <div className="event__head">
                          <div className="event__title">
                            <span className="event__time">{formatIstTime(isoToSeconds(row.at))}</span>
                            <strong>{row.trigger}</strong>
                            <TriggerBadge kind={row.trigger_kind} />
                            {stillAwake && <Badge tone="ok">Still awake</Badge>}
                          </div>
                          <div className="event__value">
                            <span className="event__number">{row.cu_hours.toFixed(4)}</span>
                            <span className="muted">CU-hours · {percent}%</span>
                          </div>
                        </div>
                        <p className="event__summary">{row.summary}</p>
                        <ShareBar percent={percent} />
                        <div className="event__sub">
                          {row.operations.map((op) => (
                            <div key={op.label} className="event__sub-row">
                              <span className="chip">{op.area}</span>
                              <span>{op.summary}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })
                }
                emptyState={
                  <EmptyState
                    icon={<IconServer size={34} />}
                    title="No database activity in the last 48 hours"
                    body="An hour card appears the moment anything queries Neon — a page load, a pipeline batch, or the startup schema check."
                  />
                }
              />
            </Panel>
          ) : (
            <Panel className="stack stack-4">
              <div className="row-between">
                <div>
                  <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                    <IconLayers size={12} /> What spent Network Transfer
                  </div>
                  <h2>Every operation that pulled data out of Neon, hour by hour</h2>
                  <p className="section-head__sub" style={{ marginTop: 6 }}>
                    Measured from the real size of each result. Operations too small to be worth their own line are
                    never dropped — they are added together into one &ldquo;smaller operations&rdquo; row, which is how
                    you catch the quiet ones that add up.
                  </p>
                </div>
                <Button size="sm" tone="danger" onClick={clearTransfer} icon={<IconTrash size={15} />}>
                  Clear this tab
                </Button>
              </div>

              <HourlyCards
                groups={transferGroups}
                stats={(group) => {
                  let bytes = 0;
                  let queries = 0;
                  for (const row of group.items) {
                    bytes += row.bytes;
                    queries += row.queries;
                  }
                  return [
                    { label: "Transferred", value: formatBytes(bytes), accent: true },
                    { label: "Operations", value: formatInt(group.items.length) },
                    { label: "Queries", value: formatInt(queries) },
                  ];
                }}
                renderBody={(group) =>
                  group.items.map((row, index) => {
                    const percent = share(row.bytes, totals.bytes);
                    return (
                      <div key={`${row.at}-${row.label}-${index}`} className="event">
                        <div className="event__head">
                          <div className="event__title">
                            <span className="event__time">{formatIstTime(isoToSeconds(row.at))}</span>
                            <strong>{row.label}</strong>
                            <span className="chip">{row.area}</span>
                          </div>
                          <div className="event__value">
                            <span className="event__number">{formatBytes(row.bytes)}</span>
                            <span className="muted">{percent}% of 48 h transfer</span>
                          </div>
                        </div>
                        <p className="event__summary">{row.summary}</p>
                        <ShareBar percent={percent} />
                        <div className="event__sub">
                          <div className="event__sub-row">
                            <span className="muted">
                              Set off by <strong>{row.trigger}</strong>
                            </span>
                            <TriggerBadge kind={row.trigger_kind} />
                          </div>
                        </div>
                      </div>
                    );
                  })
                }
                emptyState={
                  <EmptyState
                    icon={<IconLayers size={34} />}
                    title="No data pulled from Neon in the last 48 hours"
                    body="Rows appear as soon as a query actually returns data — writes and schema checks move almost nothing, so they may never show here."
                  />
                }
              />
            </Panel>
          )}

          <Note tone="warn" icon={<IconInfo size={17} />}>
            CU-hours are an estimate, worked out as <strong>awake time × {assumptions.compute_units} CU</strong>{" "}
            assuming Neon's default {idleMinutes}-minute scale-to-zero delay. If your Neon project uses a different
            compute size or autosuspend setting, change the two constants at the top of{" "}
            <code>Backend/Service/NeonUsageService/neon_usage_service.py</code> and these figures line up again. Network
            transfer is not an estimate — it is the real byte size of what came back.
          </Note>
        </>
      )}
    </div>
  );
}
