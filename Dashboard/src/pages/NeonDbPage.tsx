import { useCallback, useState } from "react";
import { neonUsageApi } from "../api/neonUsageApi";
import type { NeonUsageOverview } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { formatWhen } from "../lib/formatters";
import { usePolling } from "../hooks/usePolling";
import { Badge, Button, EmptyState, Note, Panel, SkeletonRows } from "../components/Primitives";
import { IconCheck, IconInfo, IconLayers, IconRefresh, IconServer, IconZap } from "../components/Icons";

type Capsule = "compute" | "transfer";

const CAPSULES: { id: Capsule; label: string; icon: (props: { size?: number }) => JSX.Element }[] = [
  { id: "compute", label: "CU Hours", icon: IconZap },
  { id: "transfer", label: "Network Transfer", icon: IconLayers },
];

function TriggerBadge({ kind }: { kind: string }) {
  const tone = kind === "API request" ? "info" : kind === "Background job" ? "warn" : "neutral";
  return <Badge tone={tone}>{kind}</Badge>;
}

/** The thin bar showing how much of the running total one row is
 *  responsible for — the fastest way to spot the expensive one. */
function ShareBar({ percent }: { percent: number }) {
  return (
    <div className="event__bar" title={`${percent}% of everything recorded`}>
      <span style={{ width: `${Math.min(100, Math.max(2, percent))}%` }} />
    </div>
  );
}

export default function NeonDbPage() {
  const [data, setData] = useState<NeonUsageOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [capsule, setCapsule] = useState<Capsule>("compute");

  const load = useCallback(async () => {
    try {
      setData(await neonUsageApi.getOverview());
      setError(null);
    } catch (err) {
      setError(friendlyError(err));
    }
  }, []);

  // Reads the backend's in-memory counters only — this poll never reaches
  // Neon, so watching the cost cannot add to the cost.
  usePolling(load, 20_000);

  const totals = data?.totals;
  const assumptions = data?.assumptions;

  return (
    <div className="stack stack-6">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Neon · what spends the quota</div>
          <h1 className="page-title">Neon DB</h1>
          <p className="section-head__sub">
            Neon's own dashboard tells you the totals but never what caused them. This does: every database
            operation this app runs is measured here, newest first, so you can see exactly which one spent the
            CU-hours and which one moved the data.
          </p>
        </div>
        {data && (
          <div className="row-flex">
            <Badge tone="info" live title="Updated automatically every 20 seconds">
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

      {data && totals && assumptions && (
        <>
          <Note tone="info" icon={<IconCheck size={17} />}>
            <strong>This page is free to watch.</strong> Every number below is measured inside the backend as your
            app's own queries run, kept in memory and in a local file — opening or refreshing this tab makes{" "}
            <strong>no database call at all</strong>, so it can never add to the CU-hours or the transfer it is
            reporting on.
          </Note>

          <div className="metrics-grid">
            <div className="metric-tile metric-tile--totals">
              <span className="metric-tile__label">CU-hours (estimated)</span>
              <span className="metric-tile__value">{totals.cu_hours.toFixed(4)}</span>
            </div>
            <div className="metric-tile metric-tile--totals">
              <span className="metric-tile__label">Network transfer</span>
              <span className="metric-tile__value">{totals.bytes_text}</span>
            </div>
            <div className="metric-tile metric-tile--per-call">
              <span className="metric-tile__label">Wake-ups</span>
              <span className="metric-tile__value">{totals.wakeups.toLocaleString("en-IN")}</span>
            </div>
            <div className="metric-tile metric-tile--per-call">
              <span className="metric-tile__label">Queries</span>
              <span className="metric-tile__value">{totals.queries.toLocaleString("en-IN")}</span>
            </div>
          </div>

          <nav className="capsule-tabs" aria-label="Neon cost type">
            {CAPSULES.map((tab) => {
              const Icon = tab.icon;
              const active = tab.id === capsule;
              const count = tab.id === "compute" ? data.compute.length : data.transfer.length;
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
              <div>
                <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                  <IconZap size={12} /> What spent CU-hours
                </div>
                <h2>Every time something woke the database</h2>
                <p className="section-head__sub" style={{ marginTop: 6 }}>
                  Neon charges for the time the compute is <strong>awake</strong>, not the time it spends working.
                  It only sleeps after {Math.round(assumptions.autosuspend_seconds / 60)} idle minutes — so one
                  stray query at a quiet moment costs the same as a busy{" "}
                  {Math.round(assumptions.autosuspend_seconds / 60)} minutes. Each row below is one wake-up.
                </p>
              </div>

              {data.compute.length === 0 ? (
                <EmptyState
                  icon={<IconServer size={34} />}
                  title="No database activity recorded yet"
                  body="Rows appear the moment anything queries Neon — a page load, a pipeline batch, or the startup schema check."
                />
              ) : (
                <div className="event-list">
                  {data.compute.map((window) => (
                    <div key={`${window.at}-${window.ended_at}`} className="event">
                      <div className="event__head">
                        <div className="event__title">
                          <span className="event__time">{formatWhen(window.at)}</span>
                          <strong>{window.trigger}</strong>
                          <TriggerBadge kind={window.trigger_kind} />
                          {window.still_awake && <Badge tone="ok">Still awake</Badge>}
                        </div>
                        <div className="event__value">
                          <span className="event__number">{window.cu_hours.toFixed(4)}</span>
                          <span className="muted">CU-hours · {window.share_percent}%</span>
                        </div>
                      </div>
                      <p className="event__summary">{window.summary}</p>
                      <ShareBar percent={window.share_percent} />
                      <div className="event__sub">
                        {window.operations.map((op) => (
                          <div key={op.label} className="event__sub-row">
                            <span className="chip">{op.area}</span>
                            <span>{op.summary}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          ) : (
            <Panel className="stack stack-4">
              <div>
                <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                  <IconLayers size={12} /> What spent Network Transfer
                </div>
                <h2>Every operation that pulled data out of Neon</h2>
                <p className="section-head__sub" style={{ marginTop: 6 }}>
                  Newest first, measured from the real size of each result. Operations too small to be worth their
                  own line are never dropped — they are added together into one &ldquo;smaller operations&rdquo;
                  row, which is how you catch the quiet ones that add up.
                </p>
              </div>

              {data.transfer.length === 0 ? (
                <EmptyState
                  icon={<IconLayers size={34} />}
                  title="No data pulled from Neon yet"
                  body="Rows appear as soon as a query actually returns data — writes and schema checks move almost nothing, so they may never show here."
                />
              ) : (
                <div className="event-list">
                  {data.transfer.map((entry, index) => (
                    <div key={`${entry.at}-${entry.label}-${index}`} className="event">
                      <div className="event__head">
                        <div className="event__title">
                          <span className="event__time">{formatWhen(entry.at)}</span>
                          <strong>{entry.label}</strong>
                          <span className="chip">{entry.area}</span>
                        </div>
                        <div className="event__value">
                          <span className="event__number">{entry.share_percent}%</span>
                          <span className="muted">of all transfer</span>
                        </div>
                      </div>
                      <p className="event__summary">{entry.summary}</p>
                      <ShareBar percent={entry.share_percent} />
                      <div className="event__sub">
                        <div className="event__sub-row">
                          <span className="muted">
                            Set off by <strong>{entry.trigger}</strong>
                          </span>
                          <TriggerBadge kind={entry.trigger_kind} />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          )}

          <Note tone="warn" icon={<IconInfo size={17} />}>
            CU-hours are an estimate, worked out as{" "}
            <strong>awake time × {assumptions.compute_units} CU</strong> assuming Neon's default{" "}
            {Math.round(assumptions.autosuspend_seconds / 60)}-minute scale-to-zero delay. If your Neon project
            uses a different compute size or autosuspend setting, change the two constants at the top of{" "}
            <code>Backend/Service/NeonUsageService/neon_usage_service.py</code> and these figures line up again.
            Network transfer is not an estimate — it is the real byte size of what came back.
          </Note>
        </>
      )}
    </div>
  );
}
