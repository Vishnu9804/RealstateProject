import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { inquiryClientApi } from "../api/inquiryClientApi";
import type { InquiryClientRecord, InquiryStatusResponse } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { useDebounced } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import { formatCompactInr, relativeTime } from "../lib/formatters";
import { describeInquiryStatus } from "../lib/inquiryStatus";
import { statusTone } from "../lib/whatsappStatus";
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
  IconClock,
  IconInbox,
  IconMessage,
  IconPin,
  IconRefresh,
  IconSearch,
  IconTag,
  IconUsers,
} from "../components/ui/Icons";

const REFRESH_INTERVAL_MS = 8000;
const FETCH_LIMIT = 500;

type StatusFilter = "all" | "registered" | "pending_registration";

export default function InquiryClientsPage() {
  const toast = useToast();
  const [clients, setClients] = useState<InquiryClientRecord[] | null>(null);
  const [inquiryStatus, setInquiryStatus] = useState<InquiryStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [search, setSearch] = useState("");
  const query = useDebounced(search, 180);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [expandedPhone, setExpandedPhone] = useState<string | null>(null);

  const searchRef = useRef<HTMLInputElement>(null);
  const seenPhones = useRef<Set<string> | null>(null);
  const [freshPhones, setFreshPhones] = useState<Set<string>>(new Set());

  const load = useCallback(
    async (manual = false) => {
      setRefreshing(true);
      try {
        const [clientData, statusData] = await Promise.all([
          inquiryClientApi.getClients(FETCH_LIMIT),
          inquiryClientApi.getStatus(),
        ]);
        setClients(clientData);
        setInquiryStatus(statusData);
        setLastUpdated(new Date());
        setError(null);

        const incoming = new Set(clientData.map((c) => c.phone));
        if (seenPhones.current) {
          const added = new Set([...incoming].filter((phone) => !seenPhones.current!.has(phone)));
          if (added.size > 0) {
            setFreshPhones(added);
            window.setTimeout(() => setFreshPhones(new Set()), 2600);
          }
        }
        seenPhones.current = incoming;
        if (manual) toast.push({ tone: "ok", title: "Refreshed", message: `${clientData.length} client(s) loaded.` });
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

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName);
      if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const allClients = useMemo(() => clients ?? [], [clients]);
  const registeredCount = useMemo(() => allClients.filter((c) => c.status === "registered").length, [allClients]);
  const pendingCount = allClients.length - registeredCount;

  const visibleClients = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return allClients.filter((client) => {
      if (statusFilter !== "all" && client.status !== statusFilter) return false;
      if (!needle) return true;
      const haystack = [
        client.name,
        client.phone,
        client.email,
        client.purpose,
        client.property_type,
        client.bhk,
        client.preferred_areas,
        client.additional_requirements,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [allClients, query, statusFilter]);

  const filtersActive = query.trim().length > 0 || statusFilter !== "all";
  function resetAll() {
    setSearch("");
    setStatusFilter("all");
  }

  const loading = clients === null && error === null;
  const statusDisplay = inquiryStatus ? describeInquiryStatus(inquiryStatus.status) : null;
  const rawStatusNoteTone = statusDisplay ? statusTone(statusDisplay.tone) : "info";
  const statusNoteTone = rawStatusNoteTone === "neutral" ? "info" : rawStatusNoteTone;

  return (
    <div className="stack stack-5">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">whatsappInquiryHandling</div>
          <h1 className="page-title">Inquiries</h1>
          <p className="section-head__sub">
            Every WhatsApp client who's messaged in about a property, with their requirements as submitted through
            the registration/update form. Refreshes automatically.
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

      {statusDisplay && (
        <Note tone={statusNoteTone} icon={<IconMessage size={16} />}>
          <strong>Inquiry bot: {statusDisplay.label}.</strong> {statusDisplay.hint}
          {inquiryStatus && !inquiryStatus.client_database_configured && (
            <>
              {" "}
              <strong>CLIENT_DATABASE_URL is not set</strong> — client records are in-memory only and will be lost
              on restart.
            </>
          )}
        </Note>
      )}

      {allClients.length > 0 && (
        <div className="stat-grid">
          <Stat label="Total clients" value={allClients.length} icon={<IconUsers size={13} />} delay={0} />
          <Stat label="Registered" value={registeredCount} icon={<IconBuilding size={13} />} tone="ok" delay={60} />
          <Stat
            label="Pending registration"
            value={pendingCount}
            icon={<IconClock size={13} />}
            tone={pendingCount > 0 ? "warn" : undefined}
            delay={120}
          />
          {inquiryStatus && (
            <Stat
              label="Property inquiries seen"
              value={inquiryStatus.property_inquiry_count}
              icon={<IconMessage size={13} />}
              tone="accent"
              delay={180}
            />
          )}
        </div>
      )}

      <div className="toolbar">
        <div className="toolbar__grow">
          <SearchInput
            inputRef={searchRef}
            value={search}
            onChange={setSearch}
            placeholder="Search name, phone, area, requirements…  (press / )"
            ariaLabel="Search clients"
          />
        </div>

        <Segmented<StatusFilter>
          ariaLabel="Filter by registration status"
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { value: "all", label: "All" },
            { value: "registered", label: "Registered" },
            { value: "pending_registration", label: `Pending${pendingCount ? ` (${pendingCount})` : ""}` },
          ]}
        />

        {filtersActive && (
          <Button size="sm" variant="ghost" onClick={resetAll}>
            Reset all
          </Button>
        )}
      </div>

      {error && (
        <Note tone="bad" icon={<IconAlert size={17} />}>
          <strong>Backend unreachable.</strong> {error} — the last loaded data is still shown below, and polling
          continues in the background.
        </Note>
      )}

      {loading && (
        <Panel>
          <div className="stack stack-3">
            <div className="row-flex faint small">
              <span className="spinner" /> Loading clients…
            </div>
            <SkeletonRows rows={6} />
          </div>
        </Panel>
      )}

      {clients !== null && allClients.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconInbox size={38} />}
            title="No inquiries yet"
            body="Clients appear here once someone messages the inquiry-handling WhatsApp number about a property and completes the registration form."
          />
        </Panel>
      )}

      {allClients.length > 0 && visibleClients.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconSearch size={36} />}
            title="No matches"
            body={`None of the ${allClients.length} clients match the current search and filters.`}
            action={<Button onClick={resetAll}>Clear everything</Button>}
          />
        </Panel>
      )}

      {visibleClients.length > 0 && (
        <ClientTable
          clients={visibleClients}
          query={query}
          expandedPhone={expandedPhone}
          setExpandedPhone={setExpandedPhone}
          freshPhones={freshPhones}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ table */

function ClientTable({
  clients,
  query,
  expandedPhone,
  setExpandedPhone,
  freshPhones,
}: {
  clients: InquiryClientRecord[];
  query: string;
  expandedPhone: string | null;
  setExpandedPhone: (phone: string | null) => void;
  freshPhones: Set<string>;
}) {
  return (
    <div className="table-frame anim-rise">
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Name</th>
              <th>Phone</th>
              <th>Purpose</th>
              <th>Type</th>
              <th>BHK</th>
              <th style={{ textAlign: "right" }}>Budget</th>
              <th>Areas</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {clients.map((client) => {
              const isExpanded = expandedPhone === client.phone;
              return (
                <Fragment key={client.phone}>
                  <tr
                    className={["row", isExpanded && "row--open", freshPhones.has(client.phone) && "row--new"]
                      .filter(Boolean)
                      .join(" ")}
                    tabIndex={0}
                    role="button"
                    aria-expanded={isExpanded}
                    onClick={() => setExpandedPhone(isExpanded ? null : client.phone)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setExpandedPhone(isExpanded ? null : client.phone);
                      }
                    }}
                  >
                    <td>
                      <ClientStatusBadge client={client} />
                    </td>
                    <td className="cell-truncate cell-strong" title={client.name ?? undefined}>
                      <Highlight text={client.name ?? "—"} query={query} />
                    </td>
                    <td className="cell-truncate">
                      <Copyable text={client.phone} />
                    </td>
                    <td>{client.purpose ?? "—"}</td>
                    <td>{client.property_type ?? "—"}</td>
                    <td>{client.bhk ?? "—"}</td>
                    <td className="cell-num" style={{ textAlign: "right" }}>
                      {formatBudgetRange(client.budget_min_inr, client.budget_max_inr)}
                    </td>
                    <td className="cell-truncate" title={client.preferred_areas ?? undefined}>
                      <Highlight text={client.preferred_areas ?? "—"} query={query} />
                    </td>
                    <td className="cell-num" style={{ whiteSpace: "nowrap" }}>
                      {client.updated_at ? relativeTime(new Date(client.updated_at)) : "—"}
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr>
                      <td className="detail-cell" colSpan={9}>
                        <ClientDetail client={client} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- detail */

function ClientDetail({ client }: { client: InquiryClientRecord }) {
  return (
    <div className="detail">
      {client.pending_action && (
        <Note tone="info" icon={<IconClock size={16} />}>
          Waiting on this client: <strong>{client.pending_action.replace(/_/g, " ")}</strong>
        </Note>
      )}

      <div className="detail__grid">
        <div className="detail__block">
          <div className="detail__k">Contact</div>
          <div className="detail__v">{client.name ?? "—"}</div>
          <div className="detail__v" style={{ marginTop: 4 }}>
            <Copyable text={client.phone} />
          </div>
          {client.email && (
            <div className="faint small" style={{ marginTop: 4 }}>
              {client.email}
            </div>
          )}
        </div>

        <div className="detail__block">
          <div className="detail__k">Timeline</div>
          <div className="detail__v">
            First contacted {client.created_at ? relativeTime(new Date(client.created_at)) : "—"}
          </div>
          <div className="faint small" style={{ marginTop: 4 }}>
            Last updated {client.updated_at ? relativeTime(new Date(client.updated_at)) : "—"}
          </div>
        </div>

        {(client.budget_min_inr !== null || client.budget_max_inr !== null) && (
          <div className="detail__block">
            <div className="detail__k">
              <IconPin size={11} /> Budget
            </div>
            <div className="detail__v">{formatBudgetRange(client.budget_min_inr, client.budget_max_inr)}</div>
          </div>
        )}

        {client.preferred_areas && (
          <div className="detail__block">
            <div className="detail__k">
              <IconTag size={11} /> Preferred areas
            </div>
            <div className="detail__v">{client.preferred_areas}</div>
          </div>
        )}
      </div>

      {client.additional_requirements && (
        <div className="detail__block">
          <div className="detail__k">Additional requirements</div>
          <div className="detail__v">{client.additional_requirements}</div>
        </div>
      )}
    </div>
  );
}

function ClientStatusBadge({ client }: { client: InquiryClientRecord }) {
  const registered = client.status === "registered";
  return (
    <Badge tone={registered ? "ok" : "warn"} title={client.pending_action ?? undefined}>
      {registered ? "Registered" : "Pending"}
    </Badge>
  );
}

function formatBudgetRange(min: number | null, max: number | null): string {
  if (min === null && max === null) return "—";
  if (min !== null && max !== null) return `${formatCompactInr(min)} – ${formatCompactInr(max)}`;
  if (min !== null) return `${formatCompactInr(min)}+`;
  return `Up to ${formatCompactInr(max as number)}`;
}
