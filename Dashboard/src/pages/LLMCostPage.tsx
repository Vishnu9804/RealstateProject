import { useCallback, useEffect, useMemo, useState } from "react";
import { llmUsageApi } from "../api/llmUsageApi";
import type { FeedResponse, LLMHourlyItem, LLMUsageMetrics, LLMUsageOverview } from "../api/types";
import { useFeed } from "../hooks/useFeed";
import { formatInt } from "../lib/formatters";
import { groupByHour, HourlyCards } from "../components/HourlyCards";
import { Badge, Button, EmptyState, Panel, SkeletonRows } from "../components/Primitives";
import { IconCpu, IconGrid, IconInbox, IconInfo, IconRefresh, IconUsers } from "../components/Icons";

interface SiteTab {
  id: string;
  label: string;
  icon: (props: { size?: number }) => JSX.Element;
  blurb: string;
}

/** Fixed to the three call sites that exist today (see
 *  Backend/Service/LLMUsageService/llm_usage_service.py's own docstring).
 *  Purely a label/icon/blurb lookup — every NUMBER on this page still comes
 *  straight from the API, so a site that starts pointing at a different model
 *  tomorrow needs no change here at all. */
const SITE_TABS: SiteTab[] = [
  {
    id: "property",
    label: "Property LLM",
    icon: IconGrid,
    blurb: "Structures WhatsApp messages into property listings — one call per batch of up to 10 messages.",
  },
  {
    id: "requirement",
    label: "Requirement LLM",
    icon: IconInbox,
    blurb: "Structures WhatsApp messages into broker requirements (demand) — one call per batch of up to 10 messages.",
  },
  {
    id: "intent",
    label: "Client Inquiry Identification LLM",
    icon: IconUsers,
    blurb: "Classifies whether an inbound client message is a genuine property inquiry — one call per debounced burst from one number.",
  },
];

const METRIC_LABELS: { key: keyof LLMUsageMetrics; label: string; group: "totals" | "per-call" }[] = [
  { key: "calls", label: "LLM calls", group: "totals" },
  { key: "input_tokens", label: "Input tokens", group: "totals" },
  { key: "output_tokens", label: "Output tokens", group: "totals" },
  { key: "total_tokens", label: "Total tokens", group: "totals" },
  { key: "avg_input_tokens_per_call", label: "Input tokens / call", group: "per-call" },
  { key: "avg_output_tokens_per_call", label: "Output tokens / call", group: "per-call" },
  { key: "avg_total_tokens_per_call", label: "Total tokens / call", group: "per-call" },
];

function formatMetric(key: keyof LLMUsageMetrics, value: number): string {
  if (key.startsWith("avg_")) return value.toLocaleString("en-IN", { maximumFractionDigits: 1 });
  return value.toLocaleString("en-IN");
}

function MetricsGrid({ metrics }: { metrics: LLMUsageMetrics }) {
  return (
    <div className="metrics-grid">
      {METRIC_LABELS.map((entry) => (
        <div key={entry.key} className={`metric-tile metric-tile--${entry.group}`}>
          <span className="metric-tile__label">{entry.label}</span>
          <span className="metric-tile__value">{formatMetric(entry.key, metrics[entry.key])}</span>
        </div>
      ))}
    </div>
  );
}

/** Totals over a set of hourly rows — the same arithmetic as the backend's
 *  own per-site totals, so an hour, a model and the 48-hour panel always add
 *  up exactly. */
function metricsOf(rows: LLMHourlyItem[]): LLMUsageMetrics {
  let calls = 0;
  let input = 0;
  let output = 0;
  for (const row of rows) {
    calls += row.calls;
    input += row.input_tokens;
    output += row.output_tokens;
  }
  const total = input + output;
  const average = (value: number) => (calls ? Math.round((value / calls) * 10) / 10 : 0);
  return {
    calls,
    input_tokens: input,
    output_tokens: output,
    total_tokens: total,
    avg_input_tokens_per_call: average(input),
    avg_output_tokens_per_call: average(output),
    avg_total_tokens_per_call: average(total),
  };
}

function modelsOf(rows: LLMHourlyItem[]): { model: string; metrics: LLMUsageMetrics }[] {
  const byModel = new Map<string, LLMHourlyItem[]>();
  for (const row of rows) {
    const list = byModel.get(row.model);
    if (list) list.push(row);
    else byModel.set(row.model, [row]);
  }
  return Array.from(byModel, ([model, list]) => ({ model, metrics: metricsOf(list) })).sort(
    (a, b) => b.metrics.calls - a.metrics.calls || a.model.localeCompare(b.model),
  );
}

export default function LLMCostPage() {
  const [activeSite, setActiveSite] = useState(SITE_TABS[0].id);

  const feed = useFeed<LLMHourlyItem, FeedResponse<LLMHourlyItem>>({
    storageKey: "llm-hourly-v1",
    fetchPage: llmUsageApi.getHourly,
    keyOf: (item) => item.k,
    timeOf: (item) => item.hour,
    // LLM calls happen at batch/debounce cadence, and a poll with nothing new
    // is a few dozen bytes — slow and cheap is plenty.
    intervalMs: 20_000,
  });

  const allHours = useMemo(() => groupByHour(feed.items, (item) => item.hour), [feed.items]);
  const callsBySite = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const group of allHours) {
      for (const item of group.items) counts[item.site] = (counts[item.site] ?? 0) + item.calls;
    }
    return counts;
  }, [allHours]);
  const siteGroups = useMemo(
    () =>
      allHours
        .map((group) => ({ start: group.start, items: group.items.filter((item) => item.site === activeSite) }))
        .filter((group) => group.items.length > 0),
    [allHours, activeSite],
  );
  const siteTotals = useMemo(() => metricsOf(siteGroups.flatMap((group) => group.items)), [siteGroups]);

  // The all-time counters (one small response) are re-read only when the
  // 48-hour call count actually moves — i.e. when a new call happened.
  const [overview, setOverview] = useState<LLMUsageOverview | null>(null);
  const loadOverview = useCallback(async () => {
    try {
      setOverview(await llmUsageApi.getOverview());
    } catch {
      // The hourly view does not depend on it.
    }
  }, []);
  const callsIn48h = Object.values(callsBySite).reduce((sum, value) => sum + value, 0);
  useEffect(() => {
    void loadOverview();
  }, [loadOverview, callsIn48h]);

  const currentTab = SITE_TABS.find((tab) => tab.id === activeSite) ?? SITE_TABS[0];
  const allTime = overview?.sites[activeSite]?.totals;
  const showData = feed.ready || feed.items.length > 0;

  return (
    <div className="stack stack-6">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Cost &amp; usage</div>
          <h1 className="page-title">LLM Cost</h1>
          <p className="section-head__sub">
            Every LLM call this app makes, by pipeline stage and by the model that actually answered — hour by hour
            (IST) for the last 48 hours. Click an hour to see exactly which models it used and what they cost.
          </p>
        </div>
        {showData && (
          <div className="row-flex">
            <Badge tone="info" live title="Updated automatically every 20 seconds">
              Live
            </Badge>
            <Button
              size="sm"
              icon={<IconRefresh size={15} />}
              onClick={() => {
                feed.refresh();
                void loadOverview();
              }}
            >
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
          <nav className="capsule-tabs" aria-label="LLM call site">
            {SITE_TABS.map((tab) => {
              const Icon = tab.icon;
              const active = tab.id === activeSite;
              return (
                <button
                  key={tab.id}
                  type="button"
                  className={`capsule-tab${active ? " capsule-tab--active" : ""}`}
                  aria-current={active ? "page" : undefined}
                  onClick={() => setActiveSite(tab.id)}
                  title="Calls in the last 48 hours"
                >
                  <Icon size={15} />
                  <span>{tab.label}</span>
                  <span className="capsule-tab__count">{formatInt(callsBySite[tab.id] ?? 0)}</span>
                </button>
              );
            })}
          </nav>

          <Panel className="stack stack-4">
            <div>
              <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                <IconCpu size={12} /> {currentTab.label} — last 48 hours
              </div>
              <h2>Every model combined</h2>
              <p className="section-head__sub" style={{ marginTop: 6 }}>
                {currentTab.blurb}
              </p>
            </div>
            <MetricsGrid metrics={siteTotals} />
            {allTime && (
              <p className="muted" style={{ fontSize: 12, margin: 0 }}>
                All time, since tracking began: <strong>{formatInt(allTime.calls)}</strong> call(s) ·{" "}
                <strong>{formatInt(allTime.input_tokens)}</strong> input · <strong>{formatInt(allTime.output_tokens)}</strong>{" "}
                output · <strong>{formatInt(allTime.total_tokens)}</strong> total tokens.
              </p>
            )}
          </Panel>

          <Panel className="stack stack-4">
            <div>
              <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                <IconInfo size={12} /> Hour by hour (IST)
              </div>
              <h2>One card per hour — click it for the models that answered</h2>
              <p className="section-head__sub" style={{ marginTop: 6 }}>
                Whichever model <code>{activeSite === "intent" ? "GEMINI_MODEL" : "ZAI_MODEL"}</code> pointed at when
                each call was made. The model cards inside an hour add up to that hour's totals, and the hours add up to
                the 48-hour totals above — exactly.
              </p>
            </div>

            <HourlyCards
              groups={siteGroups}
              stats={(group) => {
                const metrics = metricsOf(group.items);
                return [
                  { label: "Calls", value: formatInt(metrics.calls) },
                  { label: "Input tokens", value: formatInt(metrics.input_tokens) },
                  { label: "Output tokens", value: formatInt(metrics.output_tokens) },
                  { label: "Total tokens", value: formatInt(metrics.total_tokens), accent: true },
                ];
              }}
              renderBody={(group) =>
                modelsOf(group.items).map(({ model, metrics }) => (
                  <div key={model} className="model-card">
                    <div className="model-card__header">
                      <span className="model-card__badge">{model}</span>
                      <span className="muted">{formatInt(metrics.calls)} call(s)</span>
                    </div>
                    <MetricsGrid metrics={metrics} />
                  </div>
                ))
              }
              emptyState={
                <EmptyState
                  icon={<IconCpu size={34} />}
                  title="No calls in the last 48 hours"
                  body="An hour card appears the moment this pipeline stage makes an LLM call — nothing to configure here."
                />
              }
            />
          </Panel>
        </>
      )}
    </div>
  );
}
