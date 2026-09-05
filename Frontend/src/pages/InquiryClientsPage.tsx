import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { inquiryClientApi } from "../api/inquiryClientApi";
import { landingLeadApi } from "../api/landingLeadApi";
import { propertyApi } from "../api/propertyApi";
import type { InquiryClientRecord, InquiryStatusResponse, LandingLeadRecord, PropertyRecord } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { useDebounced } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import { formatCarpetArea, formatCompactInr, formatPrice, relativeTime } from "../lib/formatters";
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
const QR_POLL_INTERVAL_MS = 3000;
const FETCH_LIMIT = 500;

type StatusFilter = "all" | "registered" | "pending_registration";
/** Which of the two enquiry sources is showing — see the Segmented tab
 *  right below the page header. "form" is the original WhatsApp
 *  registration-form flow this page has always shown; "property" is new —
 *  leads from the public landing page's own enquiry form (LandingPage/). */
type Source = "form" | "property";

export default function InquiryClientsPage() {
  const toast = useToast();
  const [clients, setClients] = useState<InquiryClientRecord[] | null>(null);
  const [inquiryStatus, setInquiryStatus] = useState<InquiryStatusResponse | null>(null);
  const [leads, setLeads] = useState<LandingLeadRecord[] | null>(null);
  const [properties, setProperties] = useState<PropertyRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [source, setSource] = useState<Source>("form");
  const [search, setSearch] = useState("");
  const query = useDebounced(search, 180);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [expandedPhone, setExpandedPhone] = useState<string | null>(null);
  const [expandedLeadId, setExpandedLeadId] = useState<string | null>(null);

  const searchRef = useRef<HTMLInputElement>(null);
  const seenPhones = useRef<Set<string> | null>(null);
  const [freshPhones, setFreshPhones] = useState<Set<string>>(new Set());

  const [qrTick, setQrTick] = useState(0);
  const [qrLoadFailed, setQrLoadFailed] = useState(false);
  const waitingForQr = inquiryStatus?.status === "waiting_for_qr_scan";

  // Only polled while a scan is actually being waited on — pointless to
  // keep refreshing a QR image once pairing is done or hasn't started yet.
  usePolling(
    () => {
      setQrTick((t) => t + 1);
      setQrLoadFailed(false);
    },
    QR_POLL_INTERVAL_MS,
    waitingForQr,
  );

  const load = useCallback(
    async (manual = false) => {
      setRefreshing(true);
      try {
        // Fetched together, on the same poll, regardless of which tab is
        // showing — same pattern the Dashboard and Landing Page screens
        // already use for their own property list, and it means switching
        // tabs never shows a stale load spinner for data that's actually
        // sitting there ready.
        const [clientData, statusData, leadData, propertyData] = await Promise.all([
          inquiryClientApi.getClients(FETCH_LIMIT),
          inquiryClientApi.getStatus(),
          landingLeadApi.getLeads(FETCH_LIMIT),
          propertyApi.getProperties(FETCH_LIMIT),
        ]);
        setClients(clientData);
        setInquiryStatus(statusData);
        setLeads(leadData);
        setProperties(propertyData);
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

  const allLeads = useMemo(() => leads ?? [], [leads]);
  const allProperties = useMemo(() => properties ?? [], [properties]);
  const propertyLeadCount = useMemo(() => allLeads.filter((lead) => lead.property_record_id !== null).length, [allLeads]);

  // Leaving a tab collapses whatever row was open in it — returning later
  // shouldn't dump a visitor straight into detail they already closed.
  useEffect(() => {
    setExpandedPhone(null);
    setExpandedLeadId(null);
  }, [source]);

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
            Everyone who's reached out about a property — through the WhatsApp registration form, or by leaving
            their name and number on the public website. Refreshes automatically.
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

      <Segmented<Source>
        ariaLabel="Enquiry source"
        value={source}
        onChange={setSource}
        options={[
          { value: "form", label: `Form Enquiries${allClients.length ? ` (${allClients.length})` : ""}` },
          { value: "property", label: `Property Interest${allLeads.length ? ` (${allLeads.length})` : ""}` },
        ]}
      />

      {source === "form" && (
        <>
          {waitingForQr ? (
            <Panel className="stack stack-4">
              <div className="section-head__eyebrow" style={{ marginBottom: 0 }}>
                Pair the inquiry-handling WhatsApp account
              </div>
              <div className="stack stack-4" style={{ alignItems: "center" }}>
                {!qrLoadFailed ? (
                  <div className="qr">
                    <img
                      src={inquiryClientApi.getQrCodeUrl(qrTick)}
                      alt="WhatsApp pairing QR code for inquiry handling"
                      onError={() => setQrLoadFailed(true)}
                    />
                    <span className="qr__corner qr__corner--tl" />
                    <span className="qr__corner qr__corner--tr" />
                    <span className="qr__corner qr__corner--bl" />
                    <span className="qr__corner qr__corner--br" />
                  </div>
                ) : (
                  <div className="qr-skeleton">
                    <span className="spinner" style={{ width: 22, height: 22 }} />
                    <span>Waiting for WhatsApp to generate a code…</span>
                  </div>
                )}
                <ol className="stack stack-2 small muted" style={{ margin: 0, paddingLeft: 18 }}>
                  <li>Open WhatsApp on the phone that should handle inquiries.</li>
                  <li>
                    Go to <strong>Settings → Linked devices</strong>.
                  </li>
                  <li>
                    Tap <strong>Link a device</strong> and scan the code.
                  </li>
                </ol>
                <p className="faint small" style={{ textAlign: "center" }}>
                  This pairs a second, independent linked device from the Connection page's WhatsApp account —
                  pairing one never affects the other. The code refreshes automatically; you never need to reload
                  the page.
                </p>
              </div>
            </Panel>
          ) : (
            statusDisplay && (
              <Note tone={statusNoteTone} icon={<IconMessage size={16} />}>
                <strong>Inquiry bot: {statusDisplay.label}.</strong> {statusDisplay.hint}
                {inquiryStatus && !inquiryStatus.client_database_configured && (
                  <>
                    {" "}
                    <strong>CLIENT_DATABASE_URL is not set</strong> — client records are in-memory only and will be
                    lost on restart.
                  </>
                )}
              </Note>
            )
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
        </>
      )}

      {source === "property" && (
        <>
          {allLeads.length > 0 && (
            <div className="stat-grid">
              <Stat label="Total enquiries" value={allLeads.length} icon={<IconUsers size={13} />} delay={0} />
              <Stat
                label="About a specific property"
                value={propertyLeadCount}
                icon={<IconBuilding size={13} />}
                tone="ok"
                delay={60}
              />
              {propertyLeadCount < allLeads.length && (
                <Stat
                  label="General (Contact section)"
                  value={allLeads.length - propertyLeadCount}
                  icon={<IconMessage size={13} />}
                  delay={120}
                />
              )}
            </div>
          )}

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
                  <span className="spinner" /> Loading website enquiries…
                </div>
                <SkeletonRows rows={6} />
              </div>
            </Panel>
          )}

          {leads !== null && allLeads.length === 0 && (
            <Panel>
              <EmptyState
                icon={<IconInbox size={38} />}
                title="No website enquiries yet"
                body="Leads appear here the moment someone leaves their name and WhatsApp number on the public landing page — either from a property's own page, or the home page's Contact section."
              />
            </Panel>
          )}

          {allLeads.length > 0 && (
            <LeadTable
              leads={allLeads}
              properties={allProperties}
              expandedLeadId={expandedLeadId}
              setExpandedLeadId={setExpandedLeadId}
            />
          )}
        </>
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

/* ------------------------------------------------------------ lead table */

/**
 * The "Property Interest" tab's table — leads from the public landing
 * page's own enquiry form (LandingPage/), NOT the WhatsApp registration
 * flow above. Someone here didn't state open requirements the way a
 * whatsapp-inquiry client does; they looked at one specific listing (or
 * the home page) and liked it enough to leave their number, so the point
 * of this table is simply: who, and about what.
 */
function LeadTable({
  leads,
  properties,
  expandedLeadId,
  setExpandedLeadId,
}: {
  leads: LandingLeadRecord[];
  properties: PropertyRecord[];
  expandedLeadId: string | null;
  setExpandedLeadId: (leadId: string | null) => void;
}) {
  return (
    <div className="table-frame anim-rise">
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>WhatsApp</th>
              <th>Property</th>
              <th>Submitted</th>
            </tr>
          </thead>
          <tbody>
            {leads.map((lead) => {
              const isExpanded = expandedLeadId === lead.lead_id;
              const property = lead.property_record_id
                ? (properties.find((p) => p.record_id === lead.property_record_id) ?? null)
                : null;
              return (
                <Fragment key={lead.lead_id}>
                  <tr
                    className={["row", isExpanded && "row--open"].filter(Boolean).join(" ")}
                    tabIndex={0}
                    role="button"
                    aria-expanded={isExpanded}
                    onClick={() => setExpandedLeadId(isExpanded ? null : lead.lead_id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setExpandedLeadId(isExpanded ? null : lead.lead_id);
                      }
                    }}
                  >
                    <td className="cell-truncate cell-strong">{lead.name}</td>
                    <td className="cell-truncate">
                      <Copyable text={lead.whatsapp_number} />
                    </td>
                    <td className="cell-truncate" title={lead.property_label ?? undefined}>
                      {lead.property_label ?? <span className="faint">General enquiry</span>}
                    </td>
                    <td className="cell-num" style={{ whiteSpace: "nowrap" }}>
                      {lead.created_at ? relativeTime(new Date(lead.created_at)) : "—"}
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr>
                      <td className="detail-cell" colSpan={4}>
                        <LeadDetail lead={lead} property={property} />
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

/**
 * The "division" a row expands into: client info first, full property
 * information underneath — exactly the order a human reading this would
 * want it (who is this, then what are they asking about).
 */
function LeadDetail({ lead, property }: { lead: LandingLeadRecord; property: PropertyRecord | null }) {
  return (
    <div className="detail stack stack-4">
      <div className="detail__grid">
        <div className="detail__block">
          <div className="detail__k">Contact</div>
          <div className="detail__v">{lead.name}</div>
          <div className="detail__v" style={{ marginTop: 4 }}>
            <Copyable text={lead.whatsapp_number} />
          </div>
        </div>

        <div className="detail__block">
          <div className="detail__k">Submitted</div>
          <div className="detail__v">{lead.created_at ? relativeTime(new Date(lead.created_at)) : "—"}</div>
          <div className="faint small" style={{ marginTop: 4 }}>
            From the public website — {lead.property_record_id ? "a property page" : "the Contact section"}
          </div>
        </div>
      </div>

      {lead.property_record_id ? (
        property ? (
          <PropertySummaryBlock property={property} />
        ) : (
          <Note tone="warn" icon={<IconAlert size={16} />}>
            <strong>That property is no longer available.</strong> They enquired about "
            {lead.property_label ?? "a property"}", which has since been edited, unpublished, or deleted.
          </Note>
        )
      ) : (
        <Note tone="info" icon={<IconMessage size={16} />}>
          Submitted from the site's general Contact section — not tied to any one listing.
        </Note>
      )}
    </div>
  );
}

/**
 * Full property information for a lead that named one — everything this
 * internal tool itself knows about the listing (unlike the public landing
 * page, this can show the address and the owner/broker's contact, since
 * it's the client's own team looking, not a stranger).
 */
function PropertySummaryBlock({ property }: { property: PropertyRecord }) {
  const location = [property.area_name, property.address].filter(Boolean).join(" · ");

  return (
    <div className="stack stack-3">
      <div className="detail__k">Property they're interested in</div>

      {property.image_urls.length > 0 && (
        <div className="detail__gallery">
          {property.image_urls.map((src, index) => (
            <div key={index} className="detail__photo">
              <img src={src} alt={`${property.society_name ?? "Property"} photo ${index + 1}`} />
            </div>
          ))}
        </div>
      )}

      <div className="detail__grid">
        <div className="detail__block">
          <div className="detail__k">Listing</div>
          <div className="detail__v">{[property.bhk, property.property_type].filter(Boolean).join(" ") || "—"}</div>
          <div className="faint small" style={{ marginTop: 4 }}>
            {property.society_name ?? property.area_name ?? "—"}
          </div>
        </div>

        <div className="detail__block">
          <div className="detail__k">Price</div>
          <div className="detail__v">{formatPrice(property.price_text, property.price_amount_inr)}</div>
          <div style={{ marginTop: 6 }}>
            <Badge tone={property.listing_type === "Rent" ? "info" : "ok"}>{property.listing_type}</Badge>
          </div>
        </div>

        <div className="detail__block">
          <div className="detail__k">Carpet area</div>
          <div className="detail__v">{formatCarpetArea(property.carpet_area_sqft, property.carpet_area_unit)}</div>
        </div>

        {location && (
          <div className="detail__block">
            <div className="detail__k">
              <IconPin size={11} /> Location
            </div>
            <div className="detail__v">{location}</div>
          </div>
        )}

        {(property.contact_name || property.contact_phone) && (
          <div className="detail__block">
            <div className="detail__k">Owner / broker contact</div>
            <div className="detail__v">{property.contact_name ?? "—"}</div>
            {property.contact_phone && (
              <div className="faint small" style={{ marginTop: 4 }}>
                <Copyable text={property.contact_phone} />
              </div>
            )}
          </div>
        )}
      </div>

      {property.description && (
        <div className="detail__block">
          <div className="detail__k">Description</div>
          <div className="detail__msg">{property.description}</div>
        </div>
      )}
    </div>
  );
}
