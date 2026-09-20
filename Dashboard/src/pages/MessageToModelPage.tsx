import { Fragment, useCallback, useMemo, useState } from "react";
import { llmUsageApi } from "../api/llmUsageApi";
import type { FeedResponse, MessageModelEntry } from "../api/types";
import { useFeed } from "../hooks/useFeed";
import { useTabWatermarks } from "../hooks/useTabWatermarks";
import { formatInt } from "../lib/formatters";
import { formatIstTime, isoToSeconds } from "../lib/ist";
import { useToast } from "../components/Toast";
import { groupByHour, HourlyCards } from "../components/HourlyCards";
import { Badge, Button, EmptyState, Note, Panel, SkeletonRows } from "../components/Primitives";
import { IconGrid, IconInbox, IconInfo, IconMessage, IconRefresh, IconTrash } from "../components/Icons";

type Filter = "all" | "property" | "requirement";

const FILTERS: { id: Filter; label: string; icon: (props: { size?: number }) => JSX.Element }[] = [
  { id: "all", label: "All messages", icon: IconMessage },
  { id: "property", label: "Property LLM", icon: IconGrid },
  { id: "requirement", label: "Requirement LLM", icon: IconInbox },
];

/** Display order and labels for a property model's fields (the backend sends
 *  only the ones that have a value). listing_type is shown as a badge. */
const PROPERTY_FIELDS: [string, string][] = [
  ["property_type", "Type"],
  ["bhk", "BHK"],
  ["society_name", "Society / building"],
  ["area_name", "Area"],
  ["address", "Address"],
  ["unit_no", "Unit / flat no."],
  ["area_sqft", "Area (sq ft)"],
  ["area_vaar", "Area (vaar)"],
  ["furnishing", "Furnishing"],
  ["price_text", "Price"],
  ["price_amount_inr", "Price (₹)"],
  ["contact_name", "Contact name"],
  ["contact_phones", "Contact numbers"],
  ["review_status", "Tab"],
  ["needs_review", "Needs review"],
  ["review_notes", "Review notes"],
  ["description", "Description"],
  ["record_id", "Record id"],
];

const REQUIREMENT_FIELDS: [string, string][] = [
  ["requirement_type", "Type"],
  ["bhk", "BHK"],
  ["area_name", "Primary area"],
  ["preferred_areas", "Preferred areas"],
  ["society_name", "Society"],
  ["furnishing", "Furnishing"],
  ["budget_text", "Budget"],
  ["budget_min_inr", "Budget min (₹)"],
  ["budget_max_inr", "Budget max (₹)"],
  ["contact_name", "Contact name"],
  ["contact_phones", "Contact numbers"],
  ["description", "Description"],
  ["record_id", "Record id"],
];

function isBlank(value: unknown): boolean {
  return value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0);
}

function formatValue(key: string, value: unknown): string {
  if (Array.isArray(value)) return value.map((part) => String(part)).join(", ");
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    return key.endsWith("_inr") ? `₹${value.toLocaleString("en-IN")}` : value.toLocaleString("en-IN");
  }
  if (key === "review_status") return value === "outsider" ? "Outsider" : value === "accepted" ? "Main" : String(value);
  return String(value);
}

function ModelCard({
  model,
  index,
  total,
  site,
}: {
  model: Record<string, unknown>;
  index: number;
  total: number;
  site: MessageModelEntry["site"];
}) {
  const fields = site === "property" ? PROPERTY_FIELDS : REQUIREMENT_FIELDS;
  const known = new Set([...fields.map(([key]) => key), "listing_type"]);
  const rows: [string, string][] = [];
  for (const [key, label] of fields) {
    if (!isBlank(model[key])) rows.push([label, formatValue(key, model[key])]);
  }
  // Anything the backend adds later still shows up, just unlabelled.
  for (const [key, value] of Object.entries(model)) {
    if (!known.has(key) && !isBlank(value)) rows.push([key, formatValue(key, value)]);
  }
  const listingType = typeof model.listing_type === "string" ? model.listing_type : null;
  const noun = site === "property" ? "Property" : "Requirement";
  return (
    <div className="model-kv">
      <div className="model-kv__title">
        <span>
          {noun} {index + 1} of {total}
        </span>
        {listingType && (
          <Badge tone={listingType === "Rent" ? "warn" : "info"}>
            {site === "requirement" && listingType === "Sale" ? "Buy" : listingType}
          </Badge>
        )}
      </div>
      <dl>
        {rows.map(([label, value]) => (
          <Fragment key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </Fragment>
        ))}
      </dl>
    </div>
  );
}

function OutcomeBadge({ entry }: { entry: MessageModelEntry }) {
  switch (entry.outcome) {
    case "converted": {
      const count = entry.models.length;
      const noun =
        entry.site === "property" ? (count === 1 ? "property" : "properties") : count === 1 ? "requirement" : "requirements";
      return (
        <Badge tone="ok">
          → {count} {noun}
        </Badge>
      );
    }
    case "rerouted":
      // Each LLM stage hands a message it reads as the OTHER kind to the
      // other stage — a demand from the property LLM, a listing from the
      // requirement LLM — so the destination is always the other site.
      return (
        <Badge tone="warn">{entry.site === "property" ? "Re-routed to requirements" : "Re-routed to properties"}</Badge>
      );
    case "skipped":
      return <Badge tone="neutral">{entry.site === "property" ? "Not a listing" : "Not a requirement"}</Badge>;
    default:
      return <Badge tone="bad">No answer for this message</Badge>;
  }
}

function batchText(entry: MessageModelEntry): string {
  const { batch } = entry;
  const reask = batch.retry ? `, plus ${batch.calls - 1} corrective re-ask` : "";
  if (batch.messages === 1) {
    return `This message had the LLM call${batch.retry ? "s" : ""} to itself${reask} — these are exactly the tokens billed.`;
  }
  return (
    `Sent in one LLM call together with ${batch.messages - 1} other message${batch.messages === 2 ? "" : "s"}${reask}; ` +
    `the batch used ${formatInt(batch.input)} input + ${formatInt(batch.output)} output tokens. This message's share ` +
    `is split by how much of the prompt and of the reply were its own — the shares of one batch add up to it exactly.`
  );
}

function MessageCard({ entry }: { entry: MessageModelEntry }) {
  const sender = entry.sender_saved && entry.sender_saved !== entry.sender ? `${entry.sender} (${entry.sender_saved})` : entry.sender;
  const receivedAt = isoToSeconds(entry.received_at);
  return (
    <div className="message-card">
      <div className="message-card__head">
        <div className="message-card__meta">
          <span className="event__time" title="When the LLM answered (IST)">
            {formatIstTime(entry.at)}
          </span>
          <Badge tone={entry.site === "property" ? "info" : "warn"}>
            {entry.site === "property" ? "Property" : "Broker requirement"}
          </Badge>
          <OutcomeBadge entry={entry} />
          <span className="chip">{entry.group}</span>
          <span className="muted">
            {sender}
            {entry.sender_phone ? ` · ${entry.sender_phone}` : ""}
          </span>
        </div>
        <div className="token-pills">
          <span className="token-pill">
            In <strong>{formatInt(entry.tokens.input)}</strong>
          </span>
          <span className="token-pill">
            Out <strong>{formatInt(entry.tokens.output)}</strong>
          </span>
          <span className="token-pill token-pill--total">
            Total <strong>{formatInt(entry.tokens.total)}</strong>
          </span>
        </div>
      </div>

      {entry.note && <p className="event__summary">{entry.note}</p>}
      <p className="muted" style={{ fontSize: 12, margin: 0 }}>
        {batchText(entry)} Model: <code>{entry.model}</code>
        {receivedAt > 0 ? ` · message received ${formatIstTime(receivedAt)} IST` : ""}.
      </p>

      <details className="message-card__text">
        <summary>
          Original message ({formatInt(entry.text.length)} characters{entry.text_truncated ? ", shortened" : ""})
        </summary>
        <pre>{entry.text}</pre>
      </details>

      {entry.models.length > 0 && (
        <div className="model-grid">
          {entry.models.map((model, index) => (
            <ModelCard
              key={typeof model.record_id === "string" ? model.record_id : index}
              model={model}
              index={index}
              total={entry.models.length}
              site={entry.site}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function sumOf(entries: MessageModelEntry[]) {
  let models = 0;
  let input = 0;
  let output = 0;
  for (const entry of entries) {
    models += entry.models.length;
    input += entry.tokens.input;
    output += entry.tokens.output;
  }
  return { messages: entries.length, models, input, output, total: input + output };
}

export default function MessageToModelPage() {
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("all");
  const { watermarkFor, clear, version } = useTabWatermarks("message-model-clear");

  const feed = useFeed<MessageModelEntry, FeedResponse<MessageModelEntry>>({
    storageKey: "message-models-v1",
    fetchPage: llmUsageApi.getMessages,
    keyOf: (entry) => entry.id,
    timeOf: (entry) => entry.at,
    intervalMs: 30_000,
  });

  // Three FULLY independent cutoffs — "All messages" is its own watermark,
  // not a combination of the other two, so clearing any one tab can never
  // change what another tab shows. An entry cleared from "Property LLM" can
  // still show up under "All messages" (and vice versa): each tab's view
  // only ever depends on its own cutoff.
  const entriesFor = useCallback(
    (tab: Filter): MessageModelEntry[] =>
      feed.items.filter(
        (entry) => (tab === "all" || entry.site === tab) && entry.at >= watermarkFor(tab),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [feed.items, watermarkFor, version],
  );

  const counts = useMemo(
    (): Record<Filter, number> => ({
      all: entriesFor("all").length,
      property: entriesFor("property").length,
      requirement: entriesFor("requirement").length,
    }),
    [entriesFor],
  );

  const groups = useMemo(
    () =>
      groupByHour(entriesFor(filter), (entry) => entry.at).map((group) => ({
        start: group.start,
        items: [...group.items].sort((a, b) => b.at - a.at),
      })),
    [entriesFor, filter],
  );
  const totals = useMemo(() => sumOf(groups.flatMap((group) => group.items)), [groups]);
  const showData = feed.ready || feed.items.length > 0;

  function clearActiveFilter() {
    clear(filter);
    const label = FILTERS.find((tab) => tab.id === filter)?.label ?? filter;
    toast.push({
      tone: "ok",
      title: "Cleared",
      message: `${label}'s count is back to 0. Nothing was deleted — every real message → model record is still stored, and the other tabs are untouched.`,
    });
  }

  return (
    <div className="stack stack-6">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">LLM · message → model</div>
          <h1 className="page-title">Message to Model</h1>
          <p className="section-head__sub">
            Every WhatsApp message the property or broker-requirement LLM read, and every model it turned that message
            into — a message carrying 10 properties shows all 10 in its one card — with the input, output and total
            tokens that message used. Hour by hour (IST), last 48 hours.
          </p>
        </div>
        {showData && (
          <div className="row-flex">
            <Badge tone="info" live title="Updated automatically every 30 seconds">
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
          <Note tone="info" icon={<IconInfo size={17} />}>
            <strong>How the tokens per message are worked out.</strong> The LLM is called once per batch of up to 10
            messages and reports tokens per call, so each call's tokens are split across its messages — input by how
            much of the prompt each message's own text is (the shared instructions divided evenly), output by how much
            of the reply is each message's own extraction. The split is exact in total: one batch's messages always add
            up to what that call was billed, which is also what the LLM Cost tab counts. Stored in this browser once
            fetched; nothing here touches the database.
            <br />
            <strong>Clear never deletes a record.</strong> Every row here is the actual property/requirement a
            message produced, so Clear only moves that tab's own "count from here" line forward — the real
            message → model record stays stored either way, and clearing one tab never changes what another tab
            shows.
          </Note>

          <nav className="capsule-tabs" aria-label="Which LLM">
            {FILTERS.map((tab) => {
              const Icon = tab.icon;
              const active = tab.id === filter;
              return (
                <button
                  key={tab.id}
                  type="button"
                  className={`capsule-tab${active ? " capsule-tab--active" : ""}`}
                  aria-current={active ? "page" : undefined}
                  onClick={() => setFilter(tab.id)}
                  title="Messages in the last 48 hours"
                >
                  <Icon size={15} />
                  <span>{tab.label}</span>
                  <span className="capsule-tab__count">{formatInt(counts[tab.id])}</span>
                </button>
              );
            })}
          </nav>

          <div className="row-flex" style={{ justifyContent: "flex-end" }}>
            <Button size="sm" tone="danger" onClick={clearActiveFilter} icon={<IconTrash size={15} />}>
              Clear this tab
            </Button>
          </div>

          <div className="metrics-grid">
            <div className="metric-tile metric-tile--totals">
              <span className="metric-tile__label">Messages · 48 h</span>
              <span className="metric-tile__value">{formatInt(totals.messages)}</span>
            </div>
            <div className="metric-tile metric-tile--totals">
              <span className="metric-tile__label">Models produced</span>
              <span className="metric-tile__value">{formatInt(totals.models)}</span>
            </div>
            <div className="metric-tile metric-tile--per-call">
              <span className="metric-tile__label">Input tokens</span>
              <span className="metric-tile__value">{formatInt(totals.input)}</span>
            </div>
            <div className="metric-tile metric-tile--per-call">
              <span className="metric-tile__label">Output tokens</span>
              <span className="metric-tile__value">{formatInt(totals.output)}</span>
            </div>
            <div className="metric-tile metric-tile--per-call">
              <span className="metric-tile__label">Total tokens</span>
              <span className="metric-tile__value">{formatInt(totals.total)}</span>
            </div>
          </div>

          <Panel className="stack stack-4">
            <div>
              <div className="section-head__eyebrow" style={{ marginBottom: 6 }}>
                <IconMessage size={12} /> Hour by hour (IST)
              </div>
              <h2>One card per hour — click it for every message and its models</h2>
            </div>
            <HourlyCards
              groups={groups}
              stats={(group) => {
                const sums = sumOf(group.items);
                return [
                  { label: "Messages", value: formatInt(sums.messages) },
                  { label: "Models", value: formatInt(sums.models) },
                  { label: "Input", value: formatInt(sums.input) },
                  { label: "Output", value: formatInt(sums.output) },
                  { label: "Total tokens", value: formatInt(sums.total), accent: true },
                ];
              }}
              renderBody={(group) => group.items.map((entry) => <MessageCard key={entry.id} entry={entry} />)}
              emptyState={
                <EmptyState
                  icon={<IconMessage size={34} />}
                  title="No messages converted in the last 48 hours"
                  body="A card appears as soon as the next WhatsApp batch goes through the property or requirement LLM."
                />
              }
            />
          </Panel>
        </>
      )}
    </div>
  );
}
