import { useCallback, useMemo, useState } from "react";
import { backendUsageApi } from "../api/backendUsageApi";
import { isProcessItem, type CpuItem, type CpuResponse, type MemoryItem, type MemorySnapshot } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { formatBytes, formatCpu, formatInt } from "../lib/formatters";
import { nowSeconds } from "../lib/ist";
import {
  cpuCost,
  formatUsd,
  INCLUDED_USAGE_USD,
  ramCost,
  SECONDS_PER_MONTH,
  TRIAL_RAM_BYTES,
} from "../lib/railway";
import { useFeed } from "../hooks/useFeed";
import { usePolling } from "../hooks/usePolling";
import { useTabWatermarks } from "../hooks/useTabWatermarks";
import { useToast } from "../components/Toast";
import { groupByHour, HourlyCards } from "../components/HourlyCards";
import { Badge, Button, EmptyState, Note, Panel, SkeletonRows } from "../components/Primitives";
import {
  IconActivity,
  IconCheck,
  IconCpu,
  IconInfo,
  IconMemory,
  IconRefresh,
  IconServer,
  IconTrash,
} from "../components/Icons";

type Capsule = "ram" | "cpu";

const CAPSULES: { id: Capsule; label: string; icon: (props: { size?: number }) => JSX.Element }[] = [
  { id: "ram", label: "RAM", icon: IconMemory },
  { id: "cpu", label: "vCPU", icon: IconCpu },
];

export default function BackendPage() {
  const [capsule, setCapsule] = useState<Capsule>("ram");

  return (
    <div className="stack stack-6">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Railway · what the backend spends</div>
          <h1 className="page-title">Backend</h1>
          <p className="section-head__sub">
            Railway bills this backend for the memory it holds and the CPU time it uses, every second.{" "}
            <strong>RAM</strong> shows what that memory is made of right now — every cache by name, how many items it
            holds and how big it is. <strong>vCPU</strong> shows, hour by hour (IST), which requests and background
            jobs used the CPU, and what that cost.
          </p>
        </div>
      </header>

      <nav className="capsule-tabs" aria-label="Backend resource">
        {CAPSULES.map((tab) => {
          const Icon = tab.icon;
          const active = tab.id === capsule;
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
            </button>
          );
        })}
      </nav>

      {/* Only the open capsule polls — the RAM snapshot costs the backend a
          measurement, so it is never taken for a hidden panel. */}
      {capsule === "ram" ? <RamPanel /> : <CpuPanel />}
    </div>
  );
}

/* ------------------------------------------------------------------- shared */

function ShareBar({ percent }: { percent: number }) {
  return (
    <div className="event__bar" title={`${percent}%`}>
      <span style={{ width: `${Math.min(100, Math.max(2, percent))}%` }} />
    </div>
  );
}

function share(value: number, total: number | null): number {
  return total && total > 0 ? Math.round((value / total) * 1000) / 10 : 0;
}

/* ---------------------------------------------------------------------- RAM */

function RamPanel() {
  const [data, setData] = useState<MemorySnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await backendUsageApi.getMemory());
      setError(null);
    } catch (err) {
      setError(friendlyError(err));
    }
  }, []);

  // A live snapshot — once a minute is plenty, and the backend shares one
  // measurement between callers for 20 seconds anyway.
  usePolling(load, 60_000);

  if (!data) {
    return error ? (
      <Panel>
        <p style={{ color: "var(--bad)", margin: 0 }}>{error}</p>
      </Panel>
    ) : (
      <SkeletonRows rows={4} />
    );
  }

  const billable = data.billable_bytes;
  const limit = data.process.container_limit_bytes;
  const meterBase = limit ?? TRIAL_RAM_BYTES;
  const usedPercent = billable ? share(billable, meterBase) : 0;
  const perHour = billable ? ramCost(billable * 3600) : 0;
  const perMonth = billable ? ramCost(billable * SECONDS_PER_MONTH) : 0;
  const hideEmptyFallback = (item: MemoryItem) => data.database_mode === true && item.fallback_only && item.count === 0;
  const hiddenCount = data.groups.flatMap((group) => group.items).filter(hideEmptyFallback).length;

  return (
    <>
      {error && (
        <Panel>
          <p style={{ color: "var(--bad)", margin: 0 }}>{error}</p>
        </Panel>
      )}

      <div className="row-flex">
        <Badge tone="info" live title="Re-measured every minute">
          Live snapshot
        </Badge>
        <Button size="sm" icon={<IconRefresh size={15} />} onClick={() => void load()}>
          Refresh
        </Button>
      </div>

      <Note tone="info" icon={<IconCheck size={17} />}>
        <strong>A live snapshot, not a history.</strong> Measured inside the backend from its own objects when this tab
        asks (this measurement took {data.took_ms} ms), shared by every open dashboard for 20 seconds, and refreshed
        once a minute — no database, no disk. Sizes marked <strong>≈ sampled</strong> were measured on an evenly spaced
        sample of the cache and scaled up. No Clear button here: there is nothing accumulated to reset, only what's
        actually in memory right now.
      </Note>

      <div className="metrics-grid">
        <div className="metric-tile metric-tile--totals">
          <span className="metric-tile__label">
            {data.billable_source === "container" ? "Container memory (billed)" : "Process memory (RSS)"}
          </span>
          <span className="metric-tile__value">{billable !== null ? formatBytes(billable) : "—"}</span>
        </div>
        <div className="metric-tile metric-tile--totals">
          <span className="metric-tile__label">Peak process memory</span>
          <span className="metric-tile__value">
            {data.process.peak_rss_bytes !== null ? formatBytes(data.process.peak_rss_bytes) : "—"}
          </span>
        </div>
        <div className="metric-tile metric-tile--per-call">
          <span className="metric-tile__label">Itemised below</span>
          <span className="metric-tile__value">{formatBytes(data.measured_bytes)}</span>
        </div>
        <div className="metric-tile metric-tile--per-call">
          <span className="metric-tile__label">Runtime &amp; libraries</span>
          <span className="metric-tile__value">
            {data.unaccounted_bytes !== null ? formatBytes(data.unaccounted_bytes) : "—"}
          </span>
        </div>
        <div className="metric-tile metric-tile--per-call">
          <span className="metric-tile__label">RAM cost / hour</span>
          <span className="metric-tile__value">{formatUsd(perHour)}</span>
        </div>
        <div className="metric-tile metric-tile--per-call">
          <span className="metric-tile__label">RAM cost / 30 days</span>
          <span className="metric-tile__value">{formatUsd(perMonth)}</span>
        </div>
      </div>

      {billable !== null && (
        <Panel className="stack stack-4">
          <div className="row-between">
            <div>
              <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                <IconMemory size={12} /> Railway memory
              </div>
              <h2>
                {formatBytes(billable)} of {formatBytes(meterBase)}{" "}
                {limit ? "(this container's limit)" : "(the Trial plan's 1 GB per service)"}
              </h2>
            </div>
            <Badge tone={usedPercent >= 85 ? "bad" : usedPercent >= 60 ? "warn" : "ok"}>{usedPercent}% used</Badge>
          </div>
          <div className="usage-meter">
            <span style={{ width: `${Math.min(100, usedPercent)}%` }} />
          </div>
          <p className="section-head__sub" style={{ margin: 0 }}>
            Held for a whole month at this level, memory alone costs about <strong>{formatUsd(perMonth)}</strong> at
            Railway's $10 / GB-month — {perMonth > INCLUDED_USAGE_USD ? "more than" : "within"} the $
            {INCLUDED_USAGE_USD} of usage the Hobby plan includes each month (and the Trial's one-time $
            {INCLUDED_USAGE_USD} credit), before any CPU. Memory is billed on what is actually held, so a smaller cache
            is a smaller bill.
          </p>
        </Panel>
      )}

      {data.groups.map((group) => {
        const items = group.items.filter((item) => !hideEmptyFallback(item));
        if (items.length === 0) return null;
        const groupBytes = items.reduce((sum, item) => sum + item.bytes, 0);
        return (
          <Panel key={group.id} className="stack stack-4">
            <div>
              <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                <IconServer size={12} /> {group.label}
              </div>
              <h2>
                {formatBytes(groupBytes)} · {share(groupBytes, billable)}% of memory
              </h2>
            </div>
            <div className="event-list">
              {items.map((item) => (
                <CacheCard key={item.id} item={item} billable={billable} />
              ))}
            </div>
          </Panel>
        );
      })}

      {data.unaccounted_bytes !== null && (
        <Panel className="stack stack-4">
          <div className="event">
            <div className="event__head">
              <div className="event__title">
                <strong>Python runtime, libraries &amp; everything not itemised</strong>
              </div>
              <div className="event__value">
                <span className="event__number">{formatBytes(data.unaccounted_bytes)}</span>
                <span className="muted">{share(data.unaccounted_bytes, billable)}% of memory</span>
              </div>
            </div>
            <p className="event__summary">
              The interpreter itself; PyTorch and transformers (loaded together with the embedding model, and usually
              the biggest single item on a server like this); NumPy, SQLAlchemy and psycopg; the WhatsApp
              (neonize/whatsmeow) Go runtime with its session caches; thread stacks; and allocator overhead. A Python
              process can't itemise these from the inside, so this row is the remainder — the total minus everything
              listed above.
            </p>
            <ShareBar percent={share(data.unaccounted_bytes, billable)} />
          </div>
        </Panel>
      )}

      {hiddenCount > 0 && (
        <Note tone="info" icon={<IconInfo size={17} />}>
          {hiddenCount} in-memory fallback store{hiddenCount === 1 ? " is" : "s are"} hidden: they only fill up when{" "}
          <code>DATABASE_URL</code> is unset, and the database is configured, so they hold nothing and cost nothing.
        </Note>
      )}
    </>
  );
}

function CacheCard({ item, billable }: { item: MemoryItem; billable: number | null }) {
  const percent = share(item.bytes, billable);
  const empty = !item.loaded || item.count === 0;
  return (
    <div className={`event${empty ? " event--dim" : ""}`}>
      <div className="event__head">
        <div className="event__title">
          <strong>{item.label}</strong>
          <span className="chip">{item.loaded ? `${formatInt(item.count)} ${item.unit}` : "not loaded"}</span>
          {!item.exact && (
            <Badge tone="neutral" title="Measured on an evenly spaced sample and scaled up">
              ≈ sampled
            </Badge>
          )}
        </div>
        <div className="event__value">
          <span className="event__number">{formatBytes(item.bytes)}</span>
          <span className="muted">
            {percent}% of memory
            {item.count > 1 ? ` · ≈ ${formatBytes(Math.round(item.bytes / item.count))} each` : ""}
          </span>
        </div>
      </div>
      <p className="event__summary">{item.detail}</p>
      {item.bytes > 0 && <ShareBar percent={percent} />}
      {item.breakdown.length > 0 && (
        <div className="event__sub">
          <div className="breakdown-chips">
            {item.breakdown.map((part) => (
              <span key={part.label} className="chip">
                {part.label}: <strong>{formatBytes(part.bytes)}</strong>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* --------------------------------------------------------------------- vCPU */

interface EntryTotals {
  label: string;
  kind: string;
  area: string;
  count: number;
  cpu: number;
  wall: number;
  maxCpu: number;
}

interface HourTotals {
  /** CPU the process used this hour — never less than what the rows add up to. */
  billedCpu: number;
  byteSeconds: number;
  coveredSeconds: number;
  peakBytes: number;
  entries: EntryTotals[];
  everythingElse: number;
  requests: number;
}

function hourTotals(items: CpuItem[]): HourTotals {
  let processCpu = 0;
  let byteSeconds = 0;
  let coveredSeconds = 0;
  let peakBytes = 0;
  const entries = new Map<string, EntryTotals>();
  for (const item of items) {
    if (isProcessItem(item)) {
      processCpu += item.cpu;
      byteSeconds += item.rss_byte_seconds;
      coveredSeconds += item.seconds;
      peakBytes = Math.max(peakBytes, item.rss_peak);
      continue;
    }
    // Same label from two backend runs (before/after a restart) adds up.
    const entry = entries.get(item.label);
    if (entry) {
      entry.count += item.count;
      entry.cpu += item.cpu;
      entry.wall += item.wall;
      entry.maxCpu = Math.max(entry.maxCpu, item.max_cpu);
    } else {
      entries.set(item.label, {
        label: item.label,
        kind: item.kind,
        area: item.area,
        count: item.count,
        cpu: item.cpu,
        wall: item.wall,
        maxCpu: item.max_cpu,
      });
    }
  }
  const list = Array.from(entries.values()).sort((a, b) => b.cpu - a.cpu);
  const trackedCpu = list.reduce((sum, entry) => sum + entry.cpu, 0);
  const billedCpu = Math.max(processCpu, trackedCpu);
  return {
    billedCpu,
    byteSeconds,
    coveredSeconds,
    peakBytes,
    entries: list,
    everythingElse: Math.max(0, billedCpu - trackedCpu),
    requests: list.filter((entry) => entry.kind === "API request").reduce((sum, entry) => sum + entry.count, 0),
  };
}

function vcpuPercent(cpuSeconds: number, seconds: number): string {
  const percent = seconds > 0 ? (cpuSeconds / seconds) * 100 : 0;
  return `${percent.toFixed(percent < 1 ? 2 : 1)}%`;
}

function KindBadge({ kind }: { kind: string }) {
  const tone = kind === "API request" ? "info" : kind === "Background job" ? "warn" : "neutral";
  return <Badge tone={tone}>{kind}</Badge>;
}

function CpuPanel() {
  const toast = useToast();
  const { watermarkFor, clear, version } = useTabWatermarks("backend-clear");
  const feed = useFeed<CpuItem, CpuResponse>({
    storageKey: "cpu-hourly-v1",
    fetchPage: backendUsageApi.getCpu,
    keyOf: (item) => item.k,
    timeOf: (item) => item.hour,
    intervalMs: 30_000,
  });

  function clearCpu() {
    clear("cpu");
    toast.push({ tone: "ok", title: "Cleared", message: "The vCPU view is back to 0." });
  }

  const visibleItems = useMemo(
    () => feed.items.filter((item) => item.hour >= watermarkFor("cpu")),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [feed.items, watermarkFor, version],
  );
  const hours = useMemo(
    () => groupByHour(visibleItems, (item) => item.hour).map((group) => ({ ...group, totals: hourTotals(group.items) })),
    [visibleItems],
  );
  const totalsByHour = useMemo(() => new Map(hours.map((hour) => [hour.start, hour.totals])), [hours]);

  const overall = useMemo(() => {
    let cpu = 0;
    let byteSeconds = 0;
    let covered = 0;
    for (const hour of hours) {
      cpu += hour.totals.billedCpu;
      byteSeconds += hour.totals.byteSeconds;
      covered += hour.totals.coveredSeconds;
    }
    if (covered <= 0 && hours.length > 0) covered = Math.max(1, nowSeconds() - hours[hours.length - 1].start);
    const cpuUsd = cpuCost(cpu);
    const ramUsd = ramCost(byteSeconds);
    const perSecond = covered > 0 ? (cpuUsd + ramUsd) / covered : 0;
    return { cpu, covered, cpuUsd, ramUsd, total: cpuUsd + ramUsd, perMonth: perSecond * SECONDS_PER_MONTH, perSecond };
  }, [hours]);

  const showData = feed.ready || feed.items.length > 0;
  if (!showData) {
    return feed.error ? (
      <Panel>
        <p style={{ color: "var(--bad)", margin: 0 }}>{feed.error}</p>
      </Panel>
    ) : (
      <SkeletonRows rows={4} />
    );
  }

  const trialDays = overall.perSecond > 0 ? INCLUDED_USAGE_USD / (overall.perSecond * 86400) : null;

  return (
    <>
      {feed.error && (
        <Panel>
          <p style={{ color: "var(--bad)", margin: 0 }}>{feed.error}</p>
        </Panel>
      )}

      <div className="row-flex">
        <Badge tone="info" live title="Updated automatically every 30 seconds">
          Live
        </Badge>
        <Button size="sm" icon={<IconRefresh size={15} />} onClick={feed.refresh}>
          Refresh
        </Button>
        {feed.page && (
          <span className="muted" style={{ fontSize: 12 }}>
            {feed.page.python_threads} Python threads · up {formatCpu(feed.page.uptime_seconds)}
          </span>
        )}
      </div>

      <div className="metrics-grid">
        <div className="metric-tile metric-tile--totals">
          <span className="metric-tile__label">CPU time · 48 h</span>
          <span className="metric-tile__value">{formatCpu(overall.cpu)}</span>
        </div>
        <div className="metric-tile metric-tile--totals">
          <span className="metric-tile__label">Average vCPU used</span>
          <span className="metric-tile__value">{vcpuPercent(overall.cpu, overall.covered)}</span>
        </div>
        <div className="metric-tile metric-tile--per-call">
          <span className="metric-tile__label">CPU cost · 48 h</span>
          <span className="metric-tile__value">{formatUsd(overall.cpuUsd)}</span>
        </div>
        <div className="metric-tile metric-tile--per-call">
          <span className="metric-tile__label">RAM cost · 48 h</span>
          <span className="metric-tile__value">{formatUsd(overall.ramUsd)}</span>
        </div>
        <div className="metric-tile metric-tile--per-call">
          <span className="metric-tile__label">Railway total · 48 h</span>
          <span className="metric-tile__value">{formatUsd(overall.total)}</span>
        </div>
        <div className="metric-tile metric-tile--per-call">
          <span className="metric-tile__label">At this rate / 30 days</span>
          <span className="metric-tile__value">{formatUsd(overall.perMonth)}</span>
        </div>
      </div>

      {overall.perMonth > 0 && (
        <Note tone={overall.perMonth > INCLUDED_USAGE_USD ? "warn" : "info"} icon={<IconInfo size={17} />}>
          At the rate measured here, this backend costs about <strong>{formatUsd(overall.perMonth)}</strong> per 30
          days on Railway (CPU and RAM together) —{" "}
          {overall.perMonth > INCLUDED_USAGE_USD ? "more than" : "within"} the ${INCLUDED_USAGE_USD} of usage the Hobby
          plan includes each month.
          {trialDays !== null && (
            <>
              {" "}
              The Trial's one-time ${INCLUDED_USAGE_USD} credit would last about{" "}
              <strong>{trialDays >= 30 ? "the full 30 days" : `${Math.max(0, Math.floor(trialDays))} days`}</strong>.
            </>
          )}
        </Note>
      )}

      <Panel className="stack stack-4">
        <div className="row-between">
          <div>
            <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
              <IconActivity size={12} /> Hour by hour (IST)
            </div>
            <h2>Which request or job used the CPU — click an hour</h2>
            <p className="section-head__sub" style={{ marginTop: 6 }}>
              Each card is one hour: the whole process's CPU time, its average vCPU use, how many API requests it
              served, and what the hour cost on Railway (CPU + RAM). Inside, every endpoint and background job that
              ran, most expensive first, and the untracked remainder.
            </p>
          </div>
          <Button size="sm" tone="danger" onClick={clearCpu} icon={<IconTrash size={15} />}>
            Clear this tab
          </Button>
        </div>

        <HourlyCards
          groups={hours}
          stats={(group) => {
            const totals = totalsByHour.get(group.start);
            if (!totals) return [];
            const seconds = totals.coveredSeconds > 0 ? totals.coveredSeconds : 3600;
            return [
              { label: "CPU time", value: formatCpu(totals.billedCpu), accent: true },
              { label: "Avg vCPU", value: vcpuPercent(totals.billedCpu, seconds) },
              { label: "Requests", value: formatInt(totals.requests) },
              { label: "Railway cost", value: formatUsd(cpuCost(totals.billedCpu) + ramCost(totals.byteSeconds)) },
            ];
          }}
          renderBody={(group) => {
            const totals = totalsByHour.get(group.start);
            if (!totals) return null;
            const averageMemory = totals.coveredSeconds > 0 ? totals.byteSeconds / totals.coveredSeconds : 0;
            return (
              <>
                <p className="muted" style={{ fontSize: 12, margin: 0 }}>
                  Whole process this hour: <strong>{formatCpu(totals.billedCpu)}</strong> of CPU (
                  {formatUsd(cpuCost(totals.billedCpu))})
                  {averageMemory > 0 && (
                    <>
                      {" "}
                      · average memory <strong>{formatBytes(Math.round(averageMemory))}</strong>, peak{" "}
                      {formatBytes(totals.peakBytes)} ({formatUsd(ramCost(totals.byteSeconds))})
                    </>
                  )}
                  .
                </p>
                <div className="event-list">
                  {totals.entries.map((entry) => (
                    <CpuCard key={entry.label} entry={entry} hourCpu={totals.billedCpu} />
                  ))}
                  {totals.everythingElse > 0 && (
                    <CpuCard
                      entry={{
                        label: "Everything else (not tracked individually)",
                        kind: "Process",
                        area: "Runtime",
                        count: 0,
                        cpu: totals.everythingElse,
                        wall: 0,
                        maxCpu: 0,
                      }}
                      hourCpu={totals.billedCpu}
                      summary="The WhatsApp (neonize/whatsmeow) Go client keeping its connections alive and decrypting messages, Python's garbage collector, PyTorch's own threads, idle event-loop work, and any work not wrapped in a measured request or job. The process total is exact; this is what remains after the rows above."
                    />
                  )}
                </div>
              </>
            );
          }}
          emptyState={
            <EmptyState
              icon={<IconCpu size={34} />}
              title="Nothing measured in the last 48 hours yet"
              body="Hour cards appear as soon as the backend serves a request or runs a background job."
            />
          }
        />
      </Panel>

      <Note tone="warn" icon={<IconInfo size={17} />}>
        <strong>How this is measured.</strong> The process total per hour is exact — the kernel's CPU clock for every
        thread. A request's CPU is its share of the event-loop thread (split between requests running at the same
        moment) plus the worker thread that ran its endpoint; a background job's CPU is its own thread's clock. Waiting
        on the LLM, WhatsApp or Neon uses no CPU, so it isn't counted as CPU (it is in the wall-clock time). Costs use
        Railway's $20 / vCPU-month and $10 / GB-month.
      </Note>
    </>
  );
}

function CpuCard({ entry, hourCpu, summary }: { entry: EntryTotals; hourCpu: number; summary?: string }) {
  const percent = share(entry.cpu, hourCpu);
  const noun =
    entry.kind === "API request" ? (entry.count === 1 ? "request" : "requests") : entry.count === 1 ? "run" : "runs";
  return (
    <div className="event">
      <div className="event__head">
        <div className="event__title">
          <strong>{entry.label}</strong>
          <KindBadge kind={entry.kind} />
          <span className="chip">{entry.area}</span>
        </div>
        <div className="event__value">
          <span className="event__number">{formatCpu(entry.cpu)}</span>
          <span className="muted">
            CPU · {percent}% of this hour · {formatUsd(cpuCost(entry.cpu))}
          </span>
        </div>
      </div>
      <p className="event__summary">
        {summary ??
          `${formatInt(entry.count)} ${noun} — ${formatCpu(entry.count ? entry.cpu / entry.count : 0)} of CPU each on average (heaviest ${formatCpu(entry.maxCpu)}), ${formatCpu(entry.wall)} of wall-clock time in total.`}
      </p>
      <ShareBar percent={percent} />
    </div>
  );
}
