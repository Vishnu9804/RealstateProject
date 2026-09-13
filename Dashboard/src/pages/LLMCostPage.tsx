import { useCallback, useState } from "react";
import { llmUsageApi } from "../api/llmUsageApi";
import type { LLMUsageMetrics, LLMUsageOverview, LLMUsageSite } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { usePolling } from "../hooks/usePolling";
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
 *  straight from the API's `sites` map, so a site that starts pointing at a
 *  different model tomorrow needs no change here at all. */
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
  if (key === "calls") return value.toLocaleString("en-IN");
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

export default function LLMCostPage() {
  const [data, setData] = useState<LLMUsageOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeSite, setActiveSite] = useState(SITE_TABS[0].id);

  const load = useCallback(async () => {
    try {
      setData(await llmUsageApi.getOverview());
      setError(null);
    } catch (err) {
      setError(friendlyError(err));
    }
  }, []);

  // LLM calls happen at message-batch/debounce cadence (minutes for
  // property/requirement, seconds for intent bursts) — a slow poll is
  // plenty and keeps a tab left open all day off the backend's back.
  usePolling(load, 15_000);

  const currentTab = SITE_TABS.find((tab) => tab.id === activeSite) ?? SITE_TABS[0];
  const site: LLMUsageSite | undefined = data?.sites[activeSite];

  return (
    <div className="stack stack-6">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Cost &amp; usage</div>
          <h1 className="page-title">LLM Cost</h1>
          <p className="section-head__sub">
            Every LLM call this app makes, broken down by which pipeline stage made it and which model actually
            answered — so switching models later shows up here automatically instead of needing a code change.
          </p>
        </div>
        {data && (
          <div className="row-flex">
            <Badge tone="info" live title="Updated automatically every 15 seconds">
              Live
            </Badge>
            <Button size="sm" icon={<IconRefresh size={15} />} onClick={() => void load()}>
              Refresh
            </Button>
          </div>
        )}
      </header>

      {error && (
        <Panel>
          <p style={{ color: "var(--bad)", margin: 0 }}>{error}</p>
        </Panel>
      )}

      {!data && !error && <SkeletonRows rows={4} />}

      {data && (
        <>
          <nav className="capsule-tabs" aria-label="LLM call site">
            {SITE_TABS.map((tab) => {
              const Icon = tab.icon;
              const active = tab.id === activeSite;
              const calls = data.sites[tab.id]?.totals.calls ?? 0;
              return (
                <button
                  key={tab.id}
                  type="button"
                  className={`capsule-tab${active ? " capsule-tab--active" : ""}`}
                  aria-current={active ? "page" : undefined}
                  onClick={() => setActiveSite(tab.id)}
                >
                  <Icon size={15} />
                  <span>{tab.label}</span>
                  <span className="capsule-tab__count">{calls.toLocaleString("en-IN")}</span>
                </button>
              );
            })}
          </nav>

          <Panel className="stack stack-4">
            <div>
              <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                <IconCpu size={12} /> {currentTab.label} — overall
              </div>
              <h2>Every model combined</h2>
              <p className="section-head__sub" style={{ marginTop: 6 }}>
                {currentTab.blurb}
              </p>
            </div>
            <MetricsGrid metrics={site?.totals ?? EMPTY_METRICS} />
          </Panel>

          <Panel className="stack stack-4">
            <div>
              <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                <IconInfo size={12} /> Breakdown by model
              </div>
              <h2>One row per model that has actually answered a {currentTab.label.replace(" LLM", "").toLowerCase()} call</h2>
              <p className="section-head__sub" style={{ marginTop: 6 }}>
                Whichever model <code>{activeSite === "intent" ? "GEMINI_MODEL" : "ZAI_MODEL"}</code> pointed at when
                each call was made — every card below adds up to the overall numbers above, exactly.
              </p>
            </div>

            {!site || site.models.length === 0 ? (
              <EmptyState
                icon={<IconCpu size={34} />}
                title="No calls recorded yet"
                body="This fills in the moment this pipeline stage makes its first LLM call — nothing to configure here."
              />
            ) : (
              <div className="stack stack-4">
                {site.models.map((model) => (
                  <div key={model.model} className="model-card">
                    <div className="model-card__header">
                      <span className="model-card__badge">{model.model}</span>
                      <span className="muted">{model.calls.toLocaleString("en-IN")} call(s)</span>
                    </div>
                    <MetricsGrid metrics={model} />
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </>
      )}
    </div>
  );
}

const EMPTY_METRICS: LLMUsageMetrics = {
  calls: 0,
  input_tokens: 0,
  output_tokens: 0,
  total_tokens: 0,
  avg_input_tokens_per_call: 0,
  avg_output_tokens_per_call: 0,
  avg_total_tokens_per_call: 0,
};
