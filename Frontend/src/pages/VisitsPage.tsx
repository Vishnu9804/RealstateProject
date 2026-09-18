import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CLIENT_FETCH_LIMIT as SHARED_CLIENT_FETCH_LIMIT,
  PROPERTY_FETCH_LIMIT,
} from "../lib/fetchLimits";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { agentApi } from "../api/agentApi";
import { inquiryClientApi } from "../api/inquiryClientApi";
import { propertyApi } from "../api/propertyApi";
import type { AgentSummary, InquiryClientRecord, PropertySource } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { useDebounced } from "../hooks/useUi";
import { getCachedAgents, setCachedAgents } from "../lib/agentListCache";
import { friendlyError } from "../lib/apiError";
import { formatVisitTime, relativeTime, toIstFields } from "../lib/formatters";
import { getCachedClients, setCachedClients } from "../lib/inquiryListCache";
import { setCachedPropertyList } from "../lib/propertyListCache";
import ClientDetailDialog, { formatBudgetRange } from "../components/ClientDetailDialog";
import ClientFormDialog from "../components/ClientFormDialog";
import PropertyReadOnlyDialog from "../components/PropertyReadOnlyDialog";
import SourceTag from "../components/ui/SourceTag";
import { useToast } from "../components/ui/Toast";
import {
  Badge,
  Button,
  Copyable,
  EmptyState,
  Highlight,
  Note,
  Panel,
  SearchInput,
  Segmented,
  SkeletonRows,
  Stat,
} from "../components/ui/Primitives";
import {
  IconAlert,
  IconBuilding,
  IconCalendar,
  IconCheck,
  IconChevron,
  IconClock,
  IconInbox,
  IconMessage,
  IconRefresh,
  IconSearch,
  IconUserCheck,
  IconUsers,
  IconX,
} from "../components/ui/Icons";

/**
 * Every site visit, across every agent, in one table — the Agents page shows
 * the same field work agent-by-agent; this reads it visit-by-visit.
 *
 * Two tabs. ASSIGNED is every visit out with an agent right now. COMPLETED
 * is every visit already made. Visits are tied together by client + property:
 * the same client visiting the same property again is a RE-VISIT, and all
 * visits of one pair always sit in ONE combined row —
 *   - on Completed, "Visited 3 times", which drops down into the 3 visits;
 *   - on Assigned, "Re-visit", which drops down into the visits already
 *     completed plus the one out now.
 * A single visit is a plain row that opens its details straight away. The
 * "Re-visits" toggle narrows either tab to just those combined rows.
 *
 * Read-only on purpose: completing, reopening and re-timing a visit stay on
 * the Agents page and in the client matches dialog, which already own those
 * actions. Nothing new is fetched for this page either — it is the same
 * agents (+ active and completed visits) read the Agents page makes, and the
 * same client list the Inquiries page reads, through the same shared caches.
 */

type Tab = "assigned" | "completed";

const REFRESH_INTERVAL_MS = 15_000;
const CLIENT_FETCH_LIMIT = SHARED_CLIENT_FETCH_LIMIT;

/** One visit, active or completed, flattened out of its agent. */
interface VisitEntry {
  id: string;
  kind: Tab;
  /** 1-based position among every visit of this client + property. */
  number: number;
  pairKey: string;
  agentId: string;
  agentName: string;
  agentPhone: string | null;
  clientPhone: string;
  clientName: string | null;
  propertyRecordId: string | null;
  propertyLabel: string;
  propertySource: PropertySource;
  budgetMin: number | null;
  budgetMax: number | null;
  assignedAt: string | null;
  scheduledAt: string | null;
  completedAt: string | null;
  notes: string | null;
}

/** One table row: a single visit, or several visits of one client +
 *  property combined (expandable). `primary` is the visit the row's own
 *  cells describe. */
interface VisitRow {
  key: string;
  primary: VisitEntry;
  /** Every visit the row stands for, oldest first. One entry = plain row. */
  entries: VisitEntry[];
  /** Completed tab only: the re-visit currently out for this pair, if any. */
  activeRevisit: VisitEntry | null;
}

function timeOf(iso: string | null): number {
  if (!iso) return 0;
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? 0 : ms;
}

function shortDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** The table's short form — "Sat, 12 Sept, 8:51 pm", with the year only when
 *  it isn't this year — so a row's dates never push Comments off screen.
 *  The full date is on hover, and in the visit's dialog. */
function compactTime(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("en-IN", {
    weekday: "short",
    day: "numeric",
    month: "short",
    ...(date.getFullYear() !== new Date().getFullYear() ? { year: "numeric" as const } : {}),
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

function pairKeyOf(phone: string, recordId: string | null, label: string | null): string {
  return `${phone}|${recordId ?? `label:${label ?? ""}`}`;
}

/** Every visit in the agent list, numbered per client + property: completed
 *  visits in the order they were completed, then the active one(s). */
function flattenVisits(agents: AgentSummary[]): VisitEntry[] {
  const agentPhone = new Map(agents.map((agent) => [agent.agent_id, agent.phone]));
  const entries: VisitEntry[] = [];
  for (const agent of agents) {
    for (const visit of agent.completed_visits) {
      entries.push({
        id: `visit:${visit.visit_id}`,
        kind: "completed",
        number: 0,
        pairKey: pairKeyOf(visit.client_phone, visit.property_record_id, visit.property_label),
        agentId: visit.agent_id,
        agentName: visit.agent_name,
        agentPhone: agentPhone.get(visit.agent_id) ?? null,
        clientPhone: visit.client_phone,
        clientName: visit.client_name,
        propertyRecordId: visit.property_record_id,
        propertyLabel: visit.property_label ?? "Property",
        propertySource: visit.property_source ?? "property",
        budgetMin: visit.budget_min_inr,
        budgetMax: visit.budget_max_inr,
        assignedAt: null,
        scheduledAt: visit.scheduled_at,
        completedAt: visit.completed_at,
        notes: visit.notes,
      });
    }
    for (const active of agent.active_clients) {
      entries.push({
        id: `active:${agent.agent_id}:${active.phone}:${active.property_record_id}`,
        kind: "assigned",
        number: 0,
        pairKey: pairKeyOf(active.phone, active.property_record_id, active.property_label),
        agentId: agent.agent_id,
        agentName: agent.name,
        agentPhone: agent.phone,
        clientPhone: active.phone,
        clientName: active.name,
        propertyRecordId: active.property_record_id,
        propertyLabel: active.property_label || "Property",
        propertySource: active.property_source ?? "property",
        budgetMin: active.budget_min_inr,
        budgetMax: active.budget_max_inr,
        assignedAt: active.assigned_at,
        scheduledAt: active.scheduled_at,
        completedAt: null,
        notes: null,
      });
    }
  }

  const byPair = new Map<string, VisitEntry[]>();
  for (const entry of entries) {
    const list = byPair.get(entry.pairKey);
    if (list) list.push(entry);
    else byPair.set(entry.pairKey, [entry]);
  }
  for (const list of byPair.values()) {
    list.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "completed" ? -1 : 1;
      return a.kind === "completed"
        ? timeOf(a.completedAt) - timeOf(b.completedAt)
        : timeOf(a.assignedAt) - timeOf(b.assignedAt);
    });
    list.forEach((entry, index) => {
      entry.number = index + 1;
    });
  }
  return entries;
}

function groupByPair(entries: VisitEntry[]): Map<string, VisitEntry[]> {
  const byPair = new Map<string, VisitEntry[]>();
  for (const entry of entries) {
    const list = byPair.get(entry.pairKey);
    if (list) list.push(entry);
    else byPair.set(entry.pairKey, [entry]);
  }
  for (const list of byPair.values()) list.sort((a, b) => a.number - b.number);
  return byPair;
}

/** A visit whose client is no longer in the Inquiries list (deleted since)
 *  still opens the same client dialog, filled from what the visit itself
 *  remembers about them. */
function fallbackClient(entry: VisitEntry): InquiryClientRecord {
  return {
    phone: entry.clientPhone,
    status: "",
    // This record is reconstructed from a visit, not read from a client
    // row, so where that client originally came from genuinely is not known
    // here — "unknown" is the value the backend itself uses for exactly that
    // (see Backend/Model/record_source.py's SOURCE_UNKNOWN), rather than
    // guessing at one that would read as fact.
    source: "unknown",
    name: entry.clientName,
    email: null,
    current_address: null,
    about_loan: null,
    notes: null,
    additional_phones: null,
    last_follow_up_dates: null,
    follow_up_report: null,
    purpose: null,
    property_type: null,
    bhk: null,
    budget_min_inr: entry.budgetMin,
    budget_max_inr: entry.budgetMax,
    preferred_areas: null,
    additional_requirements: null,
    property_sizes: null,
    assigned_agent_id: null,
    handoff_sent_at: null,
    has_photo: false,
    created_at: null,
    updated_at: null,
  };
}

export default function VisitsPage() {
  const toast = useToast();
  const navigate = useNavigate();

  const [agents, setAgents] = useState<AgentSummary[] | null>(() => getCachedAgents());
  const [clients, setClients] = useState<InquiryClientRecord[] | null>(() => getCachedClients()?.data ?? null);
  const lastClientsVersion = useRef<string | null>(getCachedClients()?.version ?? null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [tab, setTab] = useState<Tab>("assigned");
  const [onlyRevisits, setOnlyRevisits] = useState(false);
  const [search, setSearch] = useState("");
  const query = useDebounced(search, 180);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // Dialogs. The visit is held by id so a poll keeps it current, and it
  // closes on its own if the visit disappears (e.g. completed meanwhile —
  // its completed entry then has a different id).
  const [openEntryId, setOpenEntryId] = useState<string | null>(null);
  const [clientPhone, setClientPhone] = useState<string | null>(null);
  const [editingClient, setEditingClient] = useState<InquiryClientRecord | null>(null);
  const [viewingListing, setViewingListing] = useState<{ recordId: string; source: PropertySource } | null>(null);

  const load = useCallback(
    async (manual = false) => {
      setRefreshing(true);
      try {
        const [agentData, statusData] = await Promise.all([agentApi.getAgents(), inquiryClientApi.getStatus()]);
        setAgents(agentData);
        setCachedAgents(agentData);
        // The client list only when it actually changed — same version gate
        // the Inquiries page uses, sharing its cache.
        if (manual || lastClientsVersion.current !== statusData.clients_version) {
          const clientData = await inquiryClientApi.getClients(CLIENT_FETCH_LIMIT);
          setClients(clientData);
          lastClientsVersion.current = statusData.clients_version;
          setCachedClients(clientData, statusData.clients_version);
        }
        setLastUpdated(new Date());
        setError(null);
        if (manual) toast.push({ tone: "ok", title: "Refreshed", message: "Visits are up to date." });
      } catch (err) {
        const message = friendlyError(err);
        setError(message);
        if (manual) toast.push({ tone: "bad", title: "Refresh failed", message });
      } finally {
        setRefreshing(false);
      }
    },
    [toast],
  );

  usePolling(() => load(false), REFRESH_INTERVAL_MS);

  // Warms the shared property-list cache once, exactly as the agent visits
  // dialog does, so opening a property from here is instant.
  useEffect(() => {
    let cancelled = false;
    propertyApi
      .getProperties(PROPERTY_FETCH_LIMIT)
      .then((data) => {
        if (!cancelled) setCachedPropertyList(data, null);
      })
      .catch(() => {
        /* PropertyReadOnlyDialog fetches the one property itself */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const clientsByPhone = useMemo(() => new Map((clients ?? []).map((client) => [client.phone, client])), [clients]);

  const allEntries = useMemo(() => flattenVisits(agents ?? []), [agents]);
  const entriesById = useMemo(() => new Map(allEntries.map((entry) => [entry.id, entry])), [allEntries]);
  const entriesByPair = useMemo(() => groupByPair(allEntries), [allEntries]);

  const assignedRows = useMemo<VisitRow[]>(() => {
    const rows: VisitRow[] = [];
    for (const entry of allEntries) {
      if (entry.kind !== "assigned") continue;
      const pair = entriesByPair.get(entry.pairKey) ?? [entry];
      // This visit plus every visit of the pair already completed before it.
      const entries = pair.filter((other) => other.kind === "completed" || other.id === entry.id);
      rows.push({ key: entry.id, primary: entry, entries, activeRevisit: null });
    }
    // Soonest visit first (a time that has passed sorts to the very top,
    // since it needs attention); visits with no time yet after, newest
    // assignment first.
    return rows.sort((a, b) => {
      const at = timeOf(a.primary.scheduledAt);
      const bt = timeOf(b.primary.scheduledAt);
      if (at && bt) return at - bt;
      if (at || bt) return at ? -1 : 1;
      return timeOf(b.primary.assignedAt) - timeOf(a.primary.assignedAt);
    });
  }, [allEntries, entriesByPair]);

  const completedRows = useMemo<VisitRow[]>(() => {
    const rows: VisitRow[] = [];
    for (const [pairKey, pair] of entriesByPair) {
      const done = pair.filter((entry) => entry.kind === "completed");
      if (done.length === 0) continue;
      rows.push({
        key: `pair:${pairKey}`,
        primary: done[done.length - 1],
        entries: done,
        activeRevisit: pair.find((entry) => entry.kind === "assigned") ?? null,
      });
    }
    return rows.sort((a, b) => timeOf(b.primary.completedAt) - timeOf(a.primary.completedAt));
  }, [entriesByPair]);

  const isRevisitRow = (row: VisitRow) => row.entries.length > 1;

  const displayName = useCallback(
    (entry: VisitEntry) => clientsByPhone.get(entry.clientPhone)?.name || entry.clientName || entry.clientPhone,
    [clientsByPhone],
  );

  const matchesQuery = useCallback(
    (row: VisitRow, needle: string) =>
      row.entries.some((entry) =>
        [displayName(entry), entry.clientPhone, entry.propertyLabel, entry.agentName, entry.notes]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(needle),
      ),
    [displayName],
  );

  const tabRows = tab === "assigned" ? assignedRows : completedRows;
  const revisitCount = useMemo(() => tabRows.filter(isRevisitRow).length, [tabRows]);
  const visibleRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return tabRows.filter((row) => (!onlyRevisits || isRevisitRow(row)) && (!needle || matchesQuery(row, needle)));
  }, [tabRows, onlyRevisits, query, matchesQuery]);

  const stats = useMemo(() => {
    const today = toIstFields(new Date().toISOString())?.date;
    const active = allEntries.filter((entry) => entry.kind === "assigned");
    return {
      assigned: active.length,
      today: active.filter((entry) => entry.scheduledAt && toIstFields(entry.scheduledAt)?.date === today).length,
      completed: allEntries.length - active.length,
      // Every visit to a property the same client had already visited.
      revisits: allEntries.filter((entry) => entry.number > 1).length,
    };
  }, [allEntries]);

  function toggleExpanded(key: string) {
    setExpanded((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  const openEntry = openEntryId ? (entriesById.get(openEntryId) ?? null) : null;
  const openClient = clientPhone ? (clientsByPhone.get(clientPhone) ?? null) : null;
  const openClientFallback = useMemo(() => {
    if (!clientPhone || openClient) return null;
    const entry = allEntries.find((candidate) => candidate.clientPhone === clientPhone);
    return entry ? fallbackClient(entry) : null;
  }, [clientPhone, openClient, allEntries]);

  const loading = agents === null && error === null;
  const hasAnyVisit = allEntries.length > 0;
  const filtersActive = onlyRevisits || query.trim().length > 0;

  return (
    <div className="stack stack-5">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Field team · site visits</div>
          <h1 className="page-title">Visits</h1>
          <p className="section-head__sub">
            Every site visit in one place — out with an agent now, or already done. A client visiting the same
            property again is grouped into one row; click it to see every visit. Refreshes automatically.
          </p>
        </div>
        <div className="row-flex">
          <span className="toolbar__meta">
            {refreshing ? (
              <>
                <span className="spinner" style={{ width: 12, height: 12 }} /> Syncing…
              </>
            ) : lastUpdated ? (
              <>
                <span className="badge__dot" style={{ color: "var(--ok)" }} /> Updated {relativeTime(lastUpdated)}
              </>
            ) : null}
          </span>
          <Button icon={<IconRefresh size={15} />} onClick={() => load(true)} busy={refreshing}>
            Refresh
          </Button>
        </div>
      </header>

      {hasAnyVisit && (
        <div className="stat-grid">
          <Stat label="Assigned" value={stats.assigned} icon={<IconUserCheck size={13} />} tone="warn" delay={0} />
          <Stat
            label="Scheduled today"
            value={stats.today}
            icon={<IconClock size={13} />}
            tone="accent"
            hint="Assigned visits booked for today (IST)"
            delay={60}
          />
          <Stat label="Completed" value={stats.completed} icon={<IconCheck size={13} />} tone="ok" delay={120} />
          <Stat
            label="Re-visits"
            value={stats.revisits}
            icon={<IconRefresh size={13} />}
            hint="Visits to a property the same client had already visited"
            delay={180}
          />
        </div>
      )}

      <div className="toolbar">
        <Segmented<Tab>
          ariaLabel="Assigned or completed visits"
          value={tab}
          onChange={(next) => {
            setTab(next);
            setExpanded(new Set());
          }}
          options={[
            { value: "assigned", label: `Assigned (${assignedRows.length})` },
            { value: "completed", label: `Completed (${completedRows.length})` },
          ]}
        />
        <div className="toolbar__grow">
          <SearchInput
            value={search}
            onChange={setSearch}
            placeholder="Search client, phone, property, agent, comments…"
            ariaLabel="Search visits"
          />
        </div>
        <Button
          size="sm"
          variant={onlyRevisits ? "primary" : undefined}
          icon={<IconRefresh size={14} />}
          onClick={() => setOnlyRevisits((value) => !value)}
          aria-pressed={onlyRevisits}
          title={
            onlyRevisits
              ? "Showing only clients who visited the same property more than once — click to show every visit"
              : "Show only clients who visited the same property more than once"
          }
        >
          Re-visits ({revisitCount})
        </Button>
        {filtersActive && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setSearch("");
              setOnlyRevisits(false);
            }}
          >
            Reset
          </Button>
        )}
      </div>

      {error && (
        <Note tone="bad" icon={<IconAlert size={17} />}>
          <strong>Backend unreachable.</strong> {error} — the last loaded visits are still shown below, and polling
          continues in the background.
        </Note>
      )}

      {loading && (
        <Panel>
          <div className="stack stack-3">
            <div className="row-flex faint small">
              <span className="spinner" /> Loading visits…
            </div>
            <SkeletonRows rows={6} />
          </div>
        </Panel>
      )}

      {!loading && tabRows.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconInbox size={38} />}
            title={tab === "assigned" ? "No visits out with an agent" : "No completed visits yet"}
            body={
              tab === "assigned"
                ? "Assign a property to an agent from a client's matched properties on the Inquiries page, and the visit shows up here."
                : "Once an agent's visit is marked complete on the Agents page, it moves here — with the comments written at the time."
            }
          />
        </Panel>
      )}

      {tabRows.length > 0 && visibleRows.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconSearch size={36} />}
            title="Nothing matches"
            body={
              onlyRevisits && !query.trim()
                ? "No re-visits on this tab yet."
                : "No visit on this tab matches the current search."
            }
            action={
              <Button
                onClick={() => {
                  setSearch("");
                  setOnlyRevisits(false);
                }}
              >
                Show every visit
              </Button>
            }
          />
        </Panel>
      )}

      {visibleRows.length > 0 && (
        <div className="table-frame anim-rise">
          <div className="table-scroll">
            <table className="table visits-table">
              <thead>
                <tr>
                  <th aria-label="Expand" style={{ width: 36 }} />
                  <th>Client</th>
                  <th>Visit</th>
                  <th>Contact</th>
                  <th>Property</th>
                  <th>Agent</th>
                  <th>Visit time</th>
                  <th>{tab === "assigned" ? "Assigned" : "Completed"}</th>
                  <th>Comments</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row) => {
                  const grouped = isRevisitRow(row);
                  const isOpen = grouped && expanded.has(row.key);
                  return (
                    <Fragment key={row.key}>
                      <VisitTableRow
                        entry={row.primary}
                        tab={tab}
                        row={row}
                        grouped={grouped}
                        open={isOpen}
                        query={query}
                        clientName={displayName(row.primary)}
                        onActivate={() => (grouped ? toggleExpanded(row.key) : setOpenEntryId(row.primary.id))}
                        onOpenClient={() => setClientPhone(row.primary.clientPhone)}
                      />
                      {isOpen &&
                        row.entries.map((entry) => (
                          <VisitTableRow
                            key={entry.id}
                            entry={entry}
                            tab={tab}
                            row={null}
                            grouped={false}
                            open={false}
                            query={query}
                            clientName={displayName(entry)}
                            onActivate={() => setOpenEntryId(entry.id)}
                            onOpenClient={() => setClientPhone(entry.clientPhone)}
                          />
                        ))}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {openEntry && (
        <VisitDetailDialog
          entry={openEntry}
          history={entriesByPair.get(openEntry.pairKey) ?? [openEntry]}
          clientName={displayName(openEntry)}
          nestedOpen={clientPhone !== null || editingClient !== null || viewingListing !== null}
          onClose={() => setOpenEntryId(null)}
          onOpenEntry={setOpenEntryId}
          onOpenClient={() => setClientPhone(openEntry.clientPhone)}
          onOpenProperty={
            openEntry.propertyRecordId
              ? () =>
                  setViewingListing({ recordId: openEntry.propertyRecordId as string, source: openEntry.propertySource })
              : undefined
          }
        />
      )}

      {/* The Inquiries page's own client dialog, Edit included. A client no
          longer in that list opens it from the visit's own snapshot, with
          nothing to edit. */}
      {clientPhone && (openClient || openClientFallback) && (
        <ClientDetailDialog
          client={(openClient ?? openClientFallback) as InquiryClientRecord}
          onClose={() => setClientPhone(null)}
          onEdit={
            openClient
              ? (target) => {
                  setClientPhone(null);
                  setEditingClient(target);
                }
              : undefined
          }
        />
      )}

      {editingClient && (
        <ClientFormDialog
          mode="edit"
          client={editingClient}
          assignedCount={
            allEntries.filter((entry) => entry.kind === "assigned" && entry.clientPhone === editingClient.phone).length
          }
          onOpenProperties={() => {
            const phone = editingClient.phone;
            setEditingClient(null);
            // The Inquiries page opens this client's matches dialog from it.
            navigate(`/inquiries?matches=${encodeURIComponent(phone)}`);
          }}
          onClose={() => setEditingClient(null)}
          onSaved={(saved) => {
            // Folded straight in, exactly as the Inquiries page does after
            // its own Edit — the shared cache keeps the version it was
            // fetched at, so the next status tick still re-reads the list.
            const next = (clients ?? []).map((client) => (client.phone === saved.phone ? saved : client));
            setClients(next);
            setCachedClients(next, lastClientsVersion.current ?? "");
            setEditingClient(null);
            toast.push({ tone: "ok", title: "Client saved", message: saved.name ?? saved.phone });
          }}
        />
      )}

      {viewingListing && (
        <PropertyReadOnlyDialog
          recordId={viewingListing.recordId}
          source={viewingListing.source}
          onClose={() => setViewingListing(null)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ row */

function VisitTableRow({
  entry,
  tab,
  row,
  grouped,
  open,
  query,
  clientName,
  onActivate,
  onOpenClient,
}: {
  entry: VisitEntry;
  tab: Tab;
  /** The combined row this is the head of — null for a dropped-down child. */
  row: VisitRow | null;
  grouped: boolean;
  open: boolean;
  query: string;
  clientName: string;
  /** A combined row drops down; any other row opens its visit's details. */
  onActivate: () => void;
  onOpenClient: () => void;
}) {
  const child = row === null;
  const passed = entry.kind === "assigned" && entry.scheduledAt !== null && timeOf(entry.scheduledAt) < Date.now();
  const agentNames = row ? [...new Set(row.entries.map((item) => item.agentName))] : [entry.agentName];

  return (
    <tr
      className={["row", grouped && "row--group", open && "row--open", child && "row--child"].filter(Boolean).join(" ")}
      tabIndex={0}
      role="button"
      aria-expanded={grouped ? open : undefined}
      title={grouped ? (open ? "Hide these visits" : "Show every visit") : "Open this visit"}
      onClick={onActivate}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onActivate();
        }
      }}
    >
      <td className="visits-table__toggle">
        {grouped ? (
          <span className={`visits-chevron${open ? " visits-chevron--open" : ""}`} aria-hidden="true">
            <IconChevron size={15} />
          </span>
        ) : child ? (
          <span className="visits-table__num">{entry.number}</span>
        ) : null}
      </td>

      <td className="cell-strong">
        {child ? (
          <span className="faint small">Visit {entry.number}</span>
        ) : (
          <button
            type="button"
            className="text-link visits-table__client"
            title="Open this client's details"
            onClick={(event) => {
              event.stopPropagation();
              onOpenClient();
            }}
          >
            <Highlight text={clientName} query={query} />
          </button>
        )}
      </td>

      {/* Next to the name, so "Re-visit" / "Visited 3 times" reads with the
          client it belongs to; badges stack rather than widen the column. */}
      <td>
        <div className="visits-table__badges">
          <VisitBadge entry={entry} tab={tab} row={row} />
        </div>
      </td>

      <td onClick={(event) => event.stopPropagation()}>
        {child ? <span className="faint small">—</span> : <Copyable text={entry.clientPhone} />}
      </td>

      <td>
        <div className="row-flex" style={{ gap: 8, flexWrap: "nowrap" }}>
          {!child && <SourceTag source={entry.propertySource} />}
          <span className="cell-truncate" style={{ maxWidth: 220 }} title={entry.propertyLabel}>
            <Highlight text={entry.propertyLabel} query={query} />
          </span>
        </div>
      </td>

      <td>
        <div className="cell-truncate" style={{ maxWidth: 170 }} title={agentNames.join(", ")}>
          {agentNames.length > 1 ? `${entry.agentName} +${agentNames.length - 1}` : entry.agentName}
        </div>
        {entry.agentPhone && <div className="faint small">{entry.agentPhone}</div>}
      </td>

      <td className="cell-num">
        {entry.scheduledAt ? (
          <span
            style={passed ? { color: "var(--warn)" } : undefined}
            title={`${formatVisitTime(entry.scheduledAt)}${passed ? " — this time has passed" : ""}`}
          >
            {compactTime(entry.scheduledAt)}
          </span>
        ) : (
          <span className="faint small">Not set</span>
        )}
      </td>

      <td className="cell-num">
        {entry.kind === "completed" ? (
          <span title={shortDate(entry.completedAt)}>{compactTime(entry.completedAt)}</span>
        ) : entry.assignedAt ? (
          <span title={shortDate(entry.assignedAt)}>
            {Date.now() - timeOf(entry.assignedAt) < 24 * 60 * 60 * 1000
              ? relativeTime(new Date(entry.assignedAt))
              : compactTime(entry.assignedAt)}
          </span>
        ) : (
          "—"
        )}
      </td>

      <td>
        {entry.notes ? (
          <span className="cell-truncate visits-table__notes" title={entry.notes}>
            <Highlight text={entry.notes} query={query} />
          </span>
        ) : (
          <span className="faint small">—</span>
        )}
      </td>
    </tr>
  );
}

function VisitBadge({ entry, tab, row }: { entry: VisitEntry; tab: Tab; row: VisitRow | null }) {
  // A dropped-down child says what that one visit was.
  if (row === null) {
    return entry.kind === "completed" ? (
      <Badge tone="ok">
        <IconCheck size={11} /> Completed
      </Badge>
    ) : (
      <Badge tone="orange">
        <IconUserCheck size={11} /> Assigned now
      </Badge>
    );
  }
  if (tab === "assigned") {
    return row.entries.length > 1 ? (
      <Badge tone="info" title={`Visit #${entry.number} to this property for this client`}>
        <IconRefresh size={11} /> Re-visit
      </Badge>
    ) : (
      <Badge tone="orange">
        <IconUserCheck size={11} /> First visit
      </Badge>
    );
  }
  return (
    <>
      <Badge tone="ok">
        <IconCheck size={11} /> {row.entries.length > 1 ? `Visited ${row.entries.length} times` : "Completed"}
      </Badge>
      {row.activeRevisit && (
        <Badge tone="orange" title={`Re-visit out with ${row.activeRevisit.agentName}`}>
          <IconRefresh size={11} /> Re-visit assigned
        </Badge>
      )}
    </>
  );
}

/* --------------------------------------------------------------- dialog */

function VisitDetailDialog({
  entry,
  history,
  clientName,
  nestedOpen,
  onClose,
  onOpenEntry,
  onOpenClient,
  onOpenProperty,
}: {
  entry: VisitEntry;
  /** Every visit of this client + property, oldest first (this one included). */
  history: VisitEntry[];
  clientName: string;
  /** A dialog opened from this one is on top and owns Escape / outside clicks. */
  nestedOpen: boolean;
  onClose: () => void;
  onOpenEntry: (id: string) => void;
  onOpenClient: () => void;
  /** Absent when the visit no longer knows which listing it was. */
  onOpenProperty?: () => void;
}) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !nestedOpen) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, nestedOpen]);

  const completed = entry.kind === "completed";
  const budget = formatBudgetRange(entry.budgetMin, entry.budgetMax);
  const passed = !completed && entry.scheduledAt !== null && timeOf(entry.scheduledAt) < Date.now();

  return createPortal(
    <div
      className="modal-scrim"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !nestedOpen) onClose();
      }}
    >
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="Visit details">
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">{completed ? "Completed visit" : "Assigned visit"}</div>
            <h2 className="detail-modal__title cell-truncate">{entry.propertyLabel}</h2>
            <div className="detail-modal__sub cell-truncate">
              {clientName} · {entry.clientPhone}
            </div>
            <div className="detail-modal__badges">
              <SourceTag source={entry.propertySource} />
              {completed ? (
                <Badge tone="ok">
                  <IconCheck size={11} /> Completed
                </Badge>
              ) : (
                <Badge tone="orange">
                  <IconUserCheck size={11} /> With {entry.agentName}
                </Badge>
              )}
              {history.length > 1 ? (
                <Badge tone="info">
                  <IconRefresh size={11} /> {entry.number > 1 ? "Re-visit" : "First visit"} · visit {entry.number} of{" "}
                  {history.length}
                </Badge>
              ) : (
                <Badge tone="info">First visit</Badge>
              )}
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          <div className="detail">
            <div className="detail__grid">
              <div className="detail__block">
                <div className="detail__k">
                  <IconUsers size={11} /> Client
                </div>
                <div className="detail__v">
                  <button type="button" className="text-link" style={{ fontSize: 13.5 }} onClick={onOpenClient}>
                    {clientName}
                  </button>
                </div>
                <div className="detail__v" style={{ marginTop: 4 }}>
                  <Copyable text={entry.clientPhone} />
                </div>
                {budget !== "—" && (
                  <div className="faint small" style={{ marginTop: 4 }}>
                    Budget {budget}
                  </div>
                )}
              </div>

              <div className="detail__block">
                <div className="detail__k">
                  <IconUserCheck size={11} /> Agent
                </div>
                <div className="detail__v">{entry.agentName}</div>
                {entry.agentPhone && (
                  <div className="detail__v" style={{ marginTop: 4 }}>
                    <Copyable text={entry.agentPhone} />
                  </div>
                )}
              </div>

              <div className="detail__block">
                <div className="detail__k">
                  <IconBuilding size={11} /> Property
                </div>
                <div className="detail__v">{entry.propertyLabel}</div>
                {onOpenProperty && (
                  <button type="button" className="text-link" style={{ marginTop: 4 }} onClick={onOpenProperty}>
                    View property details
                  </button>
                )}
              </div>

              <div className="detail__block">
                <div className="detail__k">
                  <IconClock size={11} /> Visit time
                </div>
                <div className="detail__v" style={passed ? { color: "var(--warn)" } : undefined}>
                  {entry.scheduledAt ? formatVisitTime(entry.scheduledAt) : "Not set"}
                </div>
                {passed && <div className="faint small">This time has passed</div>}
              </div>

              {!completed && (
                <div className="detail__block">
                  <div className="detail__k">
                    <IconCalendar size={11} /> Assigned
                  </div>
                  <div className="detail__v">{shortDate(entry.assignedAt)}</div>
                  {entry.assignedAt && (
                    <div className="faint small">{relativeTime(new Date(entry.assignedAt))}</div>
                  )}
                </div>
              )}

              {completed && (
                <div className="detail__block">
                  <div className="detail__k">
                    <IconCheck size={11} /> Completed
                  </div>
                  <div className="detail__v">{shortDate(entry.completedAt)}</div>
                </div>
              )}
            </div>

            {completed && (
              <div className="detail__block">
                <div className="detail__k">
                  <IconMessage size={11} /> Comments from the visit
                </div>
                <div className="detail__v" style={{ whiteSpace: "pre-wrap" }}>
                  {entry.notes || <span className="faint">No comments were written when this visit was completed.</span>}
                </div>
              </div>
            )}

            {history.length > 1 && (
              <div className="detail__block">
                <div className="detail__k">
                  <IconRefresh size={11} /> Every visit of this client to this property
                </div>
                <ol className="visit-history" style={{ marginTop: 6 }}>
                  {history.map((item) => (
                    <li key={item.id} className="visit-history__item">
                      <span className="visit-history__num">{item.number}</span>
                      <div className="visit-history__body">
                        <div className="visit-history__when">
                          {item.id === entry.id ? (
                            <strong>This visit</strong>
                          ) : (
                            <button type="button" className="text-link" onClick={() => onOpenEntry(item.id)}>
                              Visit {item.number}
                            </button>
                          )}{" "}
                          ·{" "}
                          {item.kind === "completed"
                            ? `Completed ${shortDate(item.completedAt)}`
                            : `Assigned now${item.scheduledAt ? ` · ${formatVisitTime(item.scheduledAt)}` : ""}`}{" "}
                          · {item.agentName}
                        </div>
                        {item.notes && <div className="visit-history__notes">{item.notes}</div>}
                      </div>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        </div>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <span className="row-flex" style={{ marginLeft: "auto", gap: 10 }}>
            <Button variant="ghost" icon={<IconUsers size={14} />} onClick={onOpenClient}>
              Client details
            </Button>
            {onOpenProperty && (
              <Button icon={<IconBuilding size={14} />} onClick={onOpenProperty}>
                View property
              </Button>
            )}
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
