import { Fragment, useCallback, useMemo, useState } from "react";
import { areaKnowledgeApi } from "../api/areaKnowledgeApi";
import type { AreaKnowledgeArea, AreaKnowledgeBreakdown, AreaKnowledgeOverview } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { relativeTime } from "../lib/formatters";
import { usePolling } from "../hooks/usePolling";
import { useToast } from "../components/ui/Toast";
import { Badge, Button, EmptyState, Note, Panel, SkeletonRows, Stat } from "../components/ui/Primitives";
import {
  IconCheck,
  IconDatabase,
  IconInfo,
  IconLayers,
  IconPin,
  IconPlus,
  IconRefresh,
  IconSparkle,
  IconTag,
  IconZap,
} from "../components/ui/Icons";

/**
 * TEMPORARY — the read-out for the internal area knowledge base.
 *
 * The knowledge base itself is grown entirely in the backend, as a
 * by-product of the property pipeline: every property the LLM structures is
 * shown to it once, and each place string on that property (its area, the
 * segments of its address, its society name) is either recognised (a HIT) or
 * added (a WRITE). See Backend/Service/WhatsAppDataFetchingService/
 * area_knowledge_service.py — the LLM stage is untouched and nothing here
 * feeds back into it.
 *
 * This page exists to answer one question with evidence rather than a
 * feeling: is the knowledge base getting good enough that the LLM would not
 * need to re-derive this area knowledge on every single call? A hit rate
 * climbing towards 100% while writes flatten out is that answer.
 *
 * Everything on it is read-only except "Reset analysis", which zeroes the
 * counters and deliberately KEEPS the learned strings — so the next
 * measurement is of today's knowledge base against fresh traffic, not of an
 * empty one against everything.
 */
export default function TemporaryPage() {
  const toast = useToast();
  const [data, setData] = useState<AreaKnowledgeOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await areaKnowledgeApi.getOverview());
      setError(null);
    } catch (err) {
      setError(friendlyError(err));
    }
  }, []);

  // The pipeline only writes here when a batch lands (10 messages, or the
  // batch window elapsing), so this is minutes-scale data at best — a slow
  // poll is plenty and keeps a tab left open all day off the backend's back.
  usePolling(load, 10_000);

  async function reset() {
    setResetting(true);
    try {
      const result = await areaKnowledgeApi.resetStats();
      setData(result);
      toast.push({
        tone: "ok",
        title: "Analysis reset",
        message: `Counters back to zero. All ${result.totals.place_count} learned place string(s) were kept.`,
      });
    } catch (err) {
      const message = friendlyError(err);
      setError(message);
      toast.push({ tone: "bad", title: "Could not reset the analysis", message });
    } finally {
      setResetting(false);
    }
  }

  const totals = data?.totals;

  return (
    <div className="stack stack-6">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Experiment — Area knowledge</div>
          <h1 className="page-title">Temporary</h1>
          <p className="section-head__sub">
            Every property the LLM structures is shown once to an internal area knowledge base, which files that
            property's place strings under its area. Nothing about the pipeline changed — this only watches it and
            writes down what it saw. The numbers below say how often the knowledge base already knew a place (a{" "}
            <strong>hit</strong>) versus had to learn it (a <strong>write</strong>).
          </p>
        </div>
        {data && (
          <div className="row-flex">
            <Badge tone="info" live title="Updated automatically every 10 seconds">
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

      {data && totals && (
        <>
          <div className="stat-grid">
            <Stat
              label="Knowledge base visits"
              value={totals.properties_seen}
              icon={<IconDatabase size={13} />}
              hint="One per property the LLM produced — every single one goes through here"
              delay={0}
            />
            <Stat
              label="Place strings checked"
              value={totals.place_lookups}
              icon={<IconLayers size={13} />}
              hint="One visit checks several: the area, each address segment, the society name"
              delay={60}
            />
            <Stat
              label="Already known (hits)"
              value={totals.place_hits}
              icon={<IconCheck size={13} />}
              tone="ok"
              hint="The string was already in that area's list — nothing to write"
              delay={120}
            />
            <Stat
              label="Newly written"
              value={totals.place_writes}
              icon={<IconPlus size={13} />}
              tone="warn"
              hint="The string was missing, so the knowledge base learned it"
              delay={180}
            />
            <Stat
              label="Hit rate"
              value={`${totals.hit_rate}%`}
              icon={<IconZap size={13} />}
              tone="accent"
              hint="Hits ÷ strings checked. This is the number that should climb over time"
              delay={240}
            />
          </div>

          <div className="stat-grid">
            <Stat
              label="Areas known"
              value={totals.area_count}
              icon={<IconPin size={13} />}
              hint="Keys in the knowledge base file"
              delay={0}
            />
            <Stat
              label="Place strings known"
              value={totals.place_count}
              icon={<IconTag size={13} />}
              hint="Total entries across every area"
              delay={60}
            />
            <Stat
              label="Fully recognised properties"
              value={totals.properties_all_known}
              icon={<IconSparkle size={13} />}
              tone="ok"
              hint="Every place string on the property was already known — nothing new to learn from it"
              delay={120}
            />
            <Stat
              label="Properties that taught it something"
              value={totals.properties_with_new_places}
              icon={<IconPlus size={13} />}
              hint="At least one string on the property was new"
              delay={180}
            />
            <Stat
              label="Skipped — no area"
              value={totals.properties_skipped_no_area}
              icon={<IconInfo size={13} />}
              tone={totals.properties_skipped_no_area > 0 ? "warn" : undefined}
              hint="The LLM returned no area_name, so there was nothing to file the places under"
              delay={240}
            />
          </div>

          <Panel className="stack stack-4">
            <div className="row-between">
              <div>
                <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                  <IconInfo size={12} /> How to read this
                </div>
                <h2>The measurement</h2>
              </div>
              <Button size="sm" busy={resetting} onClick={() => void reset()} icon={<IconRefresh size={15} />}>
                {resetting ? "Resetting…" : "Reset analysis"}
              </Button>
            </div>
            <Note tone="info" icon={<IconInfo size={17} />}>
              <strong>{totals.properties_seen}</strong> propert{totals.properties_seen === 1 ? "y has" : "ies have"}{" "}
              gone to the knowledge base across <strong>{totals.batches_observed}</strong> LLM batch
              {totals.batches_observed === 1 ? "" : "es"}. Between them they raised{" "}
              <strong>{totals.place_lookups}</strong> place-string checks:{" "}
              <strong>{totals.place_hits}</strong> were already there and needed no write, and{" "}
              <strong>{totals.place_writes}</strong> were missing and were added. That is a{" "}
              <strong>{totals.hit_rate}%</strong> hit rate over{" "}
              <strong>{totals.place_count}</strong> known place string
              {totals.place_count === 1 ? "" : "s"} in <strong>{totals.area_count}</strong> area
              {totals.area_count === 1 ? "" : "s"}.
              {totals.cross_area_collisions > 0 && (
                <>
                  {" "}
                  <strong>{totals.cross_area_collisions}</strong> string
                  {totals.cross_area_collisions === 1 ? " was" : "s were"} claimed by a second area as well — worth a
                  look in the activity feed below, since it is either a genuinely shared name or a bad area call.
                </>
              )}
            </Note>
            <div className="row-flex muted" style={{ fontSize: 12, gap: 18 }}>
              <span>
                Counting since <strong>{formatWhen(totals.stats_since)}</strong>
              </span>
              <span>
                Last property seen <strong>{formatWhen(totals.last_observed_at)}</strong>
              </span>
            </div>
            <hr className="rule" />
            <div className="muted" style={{ fontSize: 12, wordBreak: "break-all" }}>
              Knowledge base file: <code>{data.file_path}</code> — a plain Python dict you can open, read and edit by
              hand. It sits outside <code>Backend/</code> on purpose: the dev server's auto-reload restarts on any{" "}
              <code>.py</code> write inside <code>Backend/</code>, which would kill the live WhatsApp connections
              every time a new place was learned.
            </div>
          </Panel>

          <BreakdownPanel
            title="Where the place strings came from"
            eyebrow="Breakdown by field"
            note="The property's own area_name is nearly always a hit once the area is known. The address is where roads and landmarks live — that is the row worth watching. Society names are close to unique per listing, so a low hit rate there is expected, not a fault."
            rows={data.by_source}
            labels={{ area: "area_name", address: "address", society: "society_name" }}
          />

          <BreakdownPanel
            title="Main vs Outsider properties"
            eyebrow="Breakdown by tab"
            note="Outsider properties are recorded too — their localities are real places, just outside the client's selected areas, and knowing them is what lets an outsider be recognised as one."
            rows={data.by_status}
            labels={{ accepted: "Main (accepted)", outsider: "Outsider" }}
          />

          <AreasPanel areas={data.areas} />

          <ActivityPanel overview={data} />
        </>
      )}
    </div>
  );
}

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return relativeTime(date);
}

/* --------------------------------------------------------- breakdowns */

function BreakdownPanel({
  title,
  eyebrow,
  note,
  rows,
  labels,
}: {
  title: string;
  eyebrow: string;
  note: string;
  rows: AreaKnowledgeBreakdown[];
  labels: Record<string, string>;
}) {
  return (
    <Panel className="stack stack-4">
      <div>
        <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
          <IconLayers size={12} /> {eyebrow}
        </div>
        <h2>{title}</h2>
        <p className="section-head__sub" style={{ marginTop: 6 }}>
          {note}
        </p>
      </div>
      <div className="table-frame anim-rise">
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>Source</th>
                <th style={{ textAlign: "right" }}>Checked</th>
                <th style={{ textAlign: "right" }}>Hits</th>
                <th style={{ textAlign: "right" }}>Writes</th>
                <th style={{ textAlign: "right" }}>Hit rate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.name}>
                  <td>{labels[row.name] ?? row.name}</td>
                  <td style={{ textAlign: "right" }}>{row.lookups}</td>
                  <td style={{ textAlign: "right", color: "var(--ok)" }}>{row.hits}</td>
                  <td style={{ textAlign: "right", color: "var(--warn)" }}>{row.writes}</td>
                  <td style={{ textAlign: "right" }}>{row.hit_rate}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Panel>
  );
}

/* -------------------------------------------------------------- areas */

function AreasPanel({ areas }: { areas: AreaKnowledgeArea[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const total = useMemo(() => areas.reduce((sum, area) => sum + area.place_count, 0), [areas]);

  return (
    <Panel className="stack stack-4">
      <div>
        <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
          <IconPin size={12} /> The knowledge base itself
        </div>
        <h2>
          {areas.length} area{areas.length === 1 ? "" : "s"} · {total} place string{total === 1 ? "" : "s"}
        </h2>
        <p className="section-head__sub" style={{ marginTop: 6 }}>
          Exactly what is in the file right now. Click a row to see every string recorded under that area — one real
          place often has several names (&ldquo;University Road&rdquo; and &ldquo;VNSGU Road&rdquo; are the same
          road) and all of them are kept on purpose, because the point is to recognise whatever a broker actually
          typed.
        </p>
      </div>

      {areas.length === 0 ? (
        <EmptyState
          icon={<IconPin size={34} />}
          title="Nothing learned yet"
          body="The knowledge base fills itself the next time a batch of WhatsApp messages goes through the LLM. Nothing to do here — it is a by-product of the pipeline, not a setting."
        />
      ) : (
        <div className="table-frame anim-rise">
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Area</th>
                  <th style={{ textAlign: "right" }}>Places</th>
                  <th style={{ textAlign: "right" }}>Properties</th>
                  <th style={{ textAlign: "right" }}>Checked</th>
                  <th style={{ textAlign: "right" }}>Hits</th>
                  <th style={{ textAlign: "right" }}>Writes</th>
                  <th style={{ textAlign: "right" }}>Hit rate</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {areas.map((area) => (
                  <Fragment key={area.area}>
                    <tr
                      onClick={() => setExpanded((current) => (current === area.area ? null : area.area))}
                      style={{ cursor: "pointer" }}
                      title="Show every place string recorded under this area"
                    >
                      <td>
                        <strong>{area.area}</strong>
                      </td>
                      <td style={{ textAlign: "right" }}>{area.place_count}</td>
                      <td style={{ textAlign: "right" }}>{area.properties}</td>
                      <td style={{ textAlign: "right" }}>{area.lookups}</td>
                      <td style={{ textAlign: "right", color: "var(--ok)" }}>{area.hits}</td>
                      <td style={{ textAlign: "right", color: "var(--warn)" }}>{area.writes}</td>
                      <td style={{ textAlign: "right" }}>{area.hit_rate}%</td>
                      <td className="muted">{formatWhen(area.last_updated)}</td>
                    </tr>
                    {expanded === area.area && (
                      <tr>
                        <td colSpan={8}>
                          <div className="row-flex" style={{ gap: 8, padding: "4px 0 8px" }}>
                            {area.places.map((place) => (
                              <span key={place} className="chip">
                                <IconTag size={12} />
                                {place}
                              </span>
                            ))}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Panel>
  );
}

/* ----------------------------------------------------------- activity */

function ActivityPanel({ overview }: { overview: AreaKnowledgeOverview }) {
  return (
    <Panel className="stack stack-4">
      <div>
        <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
          <IconZap size={12} /> Raw activity
        </div>
        <h2>Last {overview.events.length} propert{overview.events.length === 1 ? "y" : "ies"}</h2>
        <p className="section-head__sub" style={{ marginTop: 6 }}>
          One row per visit, newest first — so the totals above can be checked against what actually happened rather
          than taken on trust.
        </p>
      </div>

      {overview.events.length === 0 ? (
        <EmptyState
          icon={<IconZap size={34} />}
          title="No activity yet"
          body="Rows appear here as soon as the next batch of messages is structured by the LLM."
        />
      ) : (
        <div className="table-frame anim-rise">
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Area</th>
                  <th style={{ textAlign: "right" }}>Checked</th>
                  <th style={{ textAlign: "right" }}>Hits</th>
                  <th style={{ textAlign: "right" }}>Writes</th>
                  <th>What happened</th>
                </tr>
              </thead>
              <tbody>
                {overview.events.map((event, index) => (
                  <tr key={`${event.record_id ?? event.source_message_id ?? "event"}-${event.at}-${index}`}>
                    <td className="muted">{formatWhen(event.at)}</td>
                    <td>
                      {event.area ? <strong>{event.area}</strong> : <span className="muted">—</span>}
                      {event.review_status === "outsider" && (
                        <>
                          {" "}
                          <Badge tone="warn">Outsider</Badge>
                        </>
                      )}
                    </td>
                    <td style={{ textAlign: "right" }}>{event.lookups}</td>
                    <td style={{ textAlign: "right", color: "var(--ok)" }}>{event.hits}</td>
                    <td style={{ textAlign: "right", color: "var(--warn)" }}>{event.writes}</td>
                    <td>
                      {event.skipped ? (
                        <span className="muted">{event.skip_reason}</span>
                      ) : (
                        <div className="row-flex" style={{ gap: 6 }}>
                          {event.hit_places.map((place) => (
                            <span key={`hit-${place}`} className="chip" title="Already in the knowledge base">
                              <IconCheck size={12} />
                              {place}
                            </span>
                          ))}
                          {event.new_places.map((place) => (
                            <span key={`new-${place}`} className="chip" title="Was missing — written to the knowledge base">
                              <IconPlus size={12} />
                              {place}
                            </span>
                          ))}
                          {event.collisions.map((collision) => (
                            <span
                              key={`clash-${collision}`}
                              className="chip"
                              style={{ color: "var(--warn)" }}
                              title="This string is already recorded under a different area"
                            >
                              <IconInfo size={12} />
                              {collision}
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Panel>
  );
}
