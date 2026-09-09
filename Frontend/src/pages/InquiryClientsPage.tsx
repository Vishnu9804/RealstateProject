import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { inquiryClientApi } from "../api/inquiryClientApi";
import { matchingApi } from "../api/matchingApi";
import type {
  InquiryClientRecord,
  InquiryStatusResponse,
  MatchCounts,
} from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { useDebounced } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import { formatCompactInr, relativeTime } from "../lib/formatters";
import { getCachedClients, setCachedClients } from "../lib/inquiryListCache";
import { useToast } from "../components/ui/Toast";
import ClientMatchesDialog, { type DialogView } from "../components/ClientMatchesDialog";
import ConfirmDialog from "../components/ui/ConfirmDialog";
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
  IconCheck,
  IconClock,
  IconEdit,
  IconInbox,
  IconMessage,
  IconPin,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconTag,
  IconTrash,
  IconUsers,
  IconX,
} from "../components/ui/Icons";

/** What the Status column says about one client. "partial" is the case
 *  that used to be invisible: some of this client's properties are out
 *  with an agent and some are still sitting here waiting to be handed
 *  off — a half-finished round reads as done under a flat "Assigned". */
type PipelineStatus =
  | { kind: "partial"; assigned: number; remaining: number }
  | { kind: "assigned"; assigned: number }
  | { kind: "matched" }
  | { kind: "new" };

function pipelineStatus(client: InquiryClientRecord, counts: ClientPropertyCounts | null): PipelineStatus {
  if (counts !== null && counts.assigned > 0) {
    const remaining = Math.max(counts.total - counts.assigned, 0);
    if (remaining > 0) return { kind: "partial", assigned: counts.assigned, remaining };
    return { kind: "assigned", assigned: counts.assigned };
  }
  if (client.assigned_agent_id) return { kind: "assigned", assigned: counts?.assigned ?? 0 };
  if (counts !== null && counts.total > 0) return { kind: "matched" };
  return { kind: "new" };
}

/** The Matches/Completed/Status columns' whole input, straight off
 *  matchingApi.getMatchCounts — `total` is scored matches PLUS properties
 *  the operator added by hand MINUS whichever of those already have a
 *  completed visit, since to the person reading this table those are all
 *  just "properties still outstanding for this client", and a property
 *  that's already been shown and visited isn't outstanding anymore. */
export interface ClientPropertyCounts {
  total: number;
  assigned: number;
  completed: number;
}

const REFRESH_INTERVAL_MS = 8000;
const FETCH_LIMIT = 500;

type StatusFilter = "all" | "registered" | "pending_registration";

export default function InquiryClientsPage() {
  const toast = useToast();
  const navigate = useNavigate();
  const location = useLocation();
  const [clients, setClients] = useState<InquiryClientRecord[] | null>(null);
  const [inquiryStatus, setInquiryStatus] =
    useState<InquiryStatusResponse | null>(null);
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
  // Last clients_version this page actually fetched a list for — see
  // load() below. null means "never fetched yet", which always forces a
  // fetch regardless of what the version says.
  const lastClientsVersion = useRef<string | null>(null);
  // Purely a signal, never used to fetch a leads list any more (there
  // isn't one on this page) — see load() below, where a change here
  // forces every client's match counts to be re-fetched. A website
  // enquiry about an existing, already-registered client's ALREADY-known
  // requirements never touches that client's own updated_at (see Backend/
  // Service/LandingPageService/landing_page_service.py's
  // _sync_to_inquiries), so the per-client updated_at gate a few lines
  // down would otherwise never notice that client's MatchCounts.website_only
  // just changed.
  const lastLeadsVersion = useRef<string | null>(null);

  // AgentManagement feature: the field team (for the Agent column) and, per
  // client, the cheap per-bucket match count (for the Matches pill + the
  // pipeline Status badge) — matchingApi.getMatchCounts, NOT getMatches,
  // since this runs for every visible client and the full match result
  // enriches every match with its property's current display fields (an
  // expensive join this column has no use for). matchResultsUpdatedAt is a
  // ref, not state — it just remembers which client.updated_at each cached
  // count was fetched for, so a client whose requirements haven't changed
  // since is never re-fetched on every 8s poll.
  const [matchCounts, setMatchCounts] = useState<Record<string, MatchCounts>>({});
  const matchCountsUpdatedAt = useRef<Record<string, string>>({});
  // Bumped to force the counts effect below to run again — adding a
  // property by hand or handing one to an agent changes this client's
  // counts without touching client.updated_at, so the version gate alone
  // would keep serving the stale number.
  const [countsNonce, setCountsNonce] = useState(0);

  // AgentManagement feature: "N properties" opens the matches dialog over
  // this table (components/ClientMatchesDialog.tsx) rather than navigating
  // to a page of its own, so the operator keeps their place in the list.
  const [matchesPhone, setMatchesPhone] = useState<string | null>(null);
  // Which tab that dialog opens on — "main" from the Matches pill, or
  // "completed" from the new green Completed pill, so a click on either
  // one lands straight where it says it will instead of always opening to
  // Main and making the operator switch tabs themselves.
  const [matchesInitialView, setMatchesInitialView] = useState<DialogView>("main");

  // Per-row actions. `editBlocked` holds the phone whose Edit was refused
  // because that client still has live assignments — cleared when the
  // explanatory dialog is dismissed.
  const [editBlocked, setEditBlocked] = useState<string | null>(null);
  const [editBusyPhone, setEditBusyPhone] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<InquiryClientRecord | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  // "+ Add" — manually register a client who hasn't messaged in yet (a
  // walk-in, a phone call, a referral). Mints the exact same token-
  // authenticated registration form link the WhatsApp welcome message
  // sends, and opens it in a new tab — from there it's indistinguishable
  // from the normal flow: fill it in on the client's behalf, or hand/send
  // that link to the client so they fill it in themselves.
  const [addOpen, setAddOpen] = useState(false);
  const [addPhone, setAddPhone] = useState("");
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  function openAddDialog() {
    setAddPhone("");
    setAddError(null);
    setAddOpen(true);
  }

  async function handleCreateManualLink() {
    if (!addPhone.trim()) {
      setAddError("Enter a phone number.");
      return;
    }
    setAddBusy(true);
    setAddError(null);
    try {
      const result = await inquiryClientApi.createManualLink(addPhone.trim());
      window.open(result.url, "_blank", "noopener,noreferrer");
      setAddOpen(false);
      toast.push({
        tone: "ok",
        title: "Form opened",
        message: `Registration form opened for ${result.phone} in a new tab.`,
      });
    } catch (err) {
      setAddError(friendlyError(err));
    } finally {
      setAddBusy(false);
    }
  }

  /**
   * Edit = the same token-authenticated requirements form the "+ Add"
   * button and every WhatsApp welcome message open, for a client who
   * already exists — so it arrives pre-filled with what we have on file,
   * and submitting it re-runs matching through the one code path that has
   * always done that (client_store.upsert_client's auto-recompute).
   *
   * Refused while ANY of this client's properties is still out with an
   * agent: re-scoring rewrites the High/Medium/Low buckets underneath
   * visits that are already booked in someone's calendar, so the
   * assignments have to be cleared (and those agents told) first.
   * Manually-added and website-enquiry properties are never touched by a
   * recompute either way — only the scored buckets change.
   */
  async function handleEdit(client: InquiryClientRecord) {
    const counts = countsFor(client.phone);
    if (counts !== null && counts.assigned > 0) {
      setEditBlocked(client.phone);
      return;
    }
    setEditBusyPhone(client.phone);
    try {
      const result = await inquiryClientApi.createManualLink(client.phone);
      window.open(result.url, "_blank", "noopener,noreferrer");
      toast.push({
        tone: "ok",
        title: "Form opened",
        message: `${client.name || client.phone}'s requirements form opened in a new tab.`,
      });
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not open the form", message: friendlyError(err) });
    } finally {
      setEditBusyPhone(null);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    try {
      const result = await inquiryClientApi.deleteClient(deleteTarget.phone);
      const name = deleteTarget.name || deleteTarget.phone;
      toast.push({
        tone: "ok",
        title: "Inquiry deleted",
        message:
          result.cleared > 0
            ? `${name} removed. ${result.cleared} site visit${result.cleared === 1 ? "" : "s"} cancelled, ${result.agents_notified} agent${result.agents_notified === 1 ? "" : "s"} notified.`
            : `${name} removed. Completed visits are kept.`,
      });
      setDeleteTarget(null);
      // The row is gone server-side; force the list (not just the counts)
      // to re-read rather than waiting for the next poll.
      await load(true);
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not delete this inquiry", message: friendlyError(err) });
    } finally {
      setDeleteBusy(false);
    }
  }

  const load = useCallback(
    async (manual = false) => {
      setRefreshing(true);
      try {
        // The status call is cheap (mostly in-memory counters plus a small
        // aggregate query) and runs every tick. clients_version on it is
        // what decides whether the one heavy list fetch below (up to 500
        // rows) actually fires — not unconditionally on every tick
        // regardless of whether anything changed.
        //
        // Every website enquiry now folds into this SAME client list (see
        // Backend/Service/LandingPageService/landing_page_service.py's
        // _sync_to_inquiries) — there is no second list to poll here any
        // more, only the one Inquiries table this page has always shown.
        const statusData = await inquiryClientApi.getStatus();
        setInquiryStatus(statusData);
        setError(null);

        // A new website enquiry can change an EXISTING client's Matches
        // count (MatchCounts.website_only) without that client's own
        // updated_at moving at all — see lastLeadsVersion's own comment.
        // Clearing every cached entry forces the counts effect below to
        // re-fetch for every visible client on its very next pass, exactly
        // as invalidateCounts already does for one phone at a time.
        if (lastLeadsVersion.current !== null && lastLeadsVersion.current !== statusData.leads_version) {
          matchCountsUpdatedAt.current = {};
          setCountsNonce((n) => n + 1);
        }
        lastLeadsVersion.current = statusData.leads_version;

        const needsClients =
          manual ||
          lastClientsVersion.current === null ||
          lastClientsVersion.current !== statusData.clients_version;

        const clientData = needsClients ? await inquiryClientApi.getClients(FETCH_LIMIT) : null;

        if (clientData !== null) {
          setClients(clientData);
          lastClientsVersion.current = statusData.clients_version;
          setCachedClients(clientData, statusData.clients_version);

          const incoming = new Set(clientData.map((c) => c.phone));
          if (seenPhones.current) {
            const added = new Set(
              [...incoming].filter((phone) => !seenPhones.current!.has(phone)),
            );
            if (added.size > 0) {
              setFreshPhones(added);
              window.setTimeout(() => setFreshPhones(new Set()), 2600);
            }
          }
          seenPhones.current = incoming;
        }

        setLastUpdated(new Date());
        if (manual)
          toast.push({
            tone: "ok",
            title: "Refreshed",
            message: `${(clientData ?? []).length} client(s) loaded.`,
          });
      } catch (err) {
        const message = friendlyError(err);
        setError(message);
        if (manual)
          toast.push({ tone: "bad", title: "Refresh failed", message });
      } finally {
        setRefreshing(false);
      }
    },
    [toast],
  );

  // A cache hit paints both lists instantly on mount (e.g. returning to this
  // page shortly after leaving it) with no network request — usePolling's
  // own first tick just below still runs immediately either way, but its
  // status check (cheap) is now what decides whether the two heavy list
  // fetches are actually needed, exactly like every other tick.
  useEffect(() => {
    const cachedClients = getCachedClients();
    if (cachedClients) {
      setClients(cachedClients.data);
      lastClientsVersion.current = cachedClients.version;
      seenPhones.current = new Set(cachedClients.data.map((c) => c.phone));
      setLastUpdated(new Date());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
  const registeredCount = useMemo(
    () => allClients.filter((c) => c.status === "registered").length,
    [allClients],
  );
  const pendingCount = allClients.length - registeredCount;

  const visibleClients = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return allClients.filter((client) => {
      // "registered" is an exact match; "pending_registration" instead
      // means "anything that ISN'T registered yet" — the backend can also
      // hand back "website_lead" (a landing-site enquiry that folded into
      // this table without ever completing WhatsApp registration — see
      // Backend/Service/LandingPageService/landing_page_service.py's
      // _sync_to_inquiries), and that belongs on this same Pending tab,
      // not hidden from both filters.
      if (statusFilter === "registered" && client.status !== "registered") return false;
      if (statusFilter === "pending_registration" && client.status === "registered") return false;
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

  // AgentManagement feature: fetch (cheap, count-only — see
  // matchingApi.getMatchCounts) the match count for any client whose
  // updated_at has moved past what was last fetched for it. Runs once per
  // load(), not on every render, since matchCountsUpdatedAt is a ref.
  useEffect(() => {
    const stale = allClients.filter((c) => matchCountsUpdatedAt.current[c.phone] !== (c.updated_at ?? ""));
    // Referenced only to keep it an honest dependency: invalidateCounts
    // clears the ref entry above and bumps this, and re-running is the
    // entire effect it is asking for.
    void countsNonce;
    if (stale.length === 0) return;
    let cancelled = false;
    Promise.all(
      stale.map((c) =>
        matchingApi
          .getMatchCounts(c.phone)
          .then((counts) => ({ phone: c.phone, updatedAt: c.updated_at ?? "", counts }))
          .catch(() => null),
      ),
    ).then((fetched) => {
      if (cancelled) return;
      setMatchCounts((prev) => {
        const next = { ...prev };
        for (const item of fetched) if (item) next[item.phone] = item.counts;
        return next;
      });
      for (const item of fetched) if (item) matchCountsUpdatedAt.current[item.phone] = item.updatedAt;
    });
    return () => {
      cancelled = true;
    };
  }, [allClients, countsNonce]);

  /** Re-read one client's property counts on the next tick — see
   *  countsNonce above for why updated_at alone isn't enough. */
  const invalidateCounts = useCallback((phone: string) => {
    delete matchCountsUpdatedAt.current[phone];
    setCountsNonce((n) => n + 1);
  }, []);

  // "Add property" inside the matches dialog is a full page of its own
  // (SelectPropertyPage), so it leaves this one. It comes back with
  // ?matches=<phone>, which re-opens the dialog exactly where it was and
  // re-reads that client's counts — the newly-added properties are part
  // of the total the moment the operator lands back here.
  useEffect(() => {
    const phone = new URLSearchParams(location.search).get("matches");
    if (!phone) return;
    setMatchesInitialView("main");
    setMatchesPhone(phone);
    invalidateCounts(phone);
    navigate("/inquiries", { replace: true });
  }, [location.search, navigate, invalidateCounts]);

  const countsFor = useCallback(
    (phone: string): ClientPropertyCounts | null => {
      const counts = matchCounts[phone];
      if (!counts) return null;
      // Both figures come straight from the server, which computes them
      // as SETS (see MatchCounts.total). They used to be added up here
      // from the per-source counts, which silently double-counted every
      // property that was both scored and hand-picked, and over-subtracted
      // every completed visit whose property had since dropped out of the
      // matched set — so a client with real properties could read "No
      // matches" while the dialog behind that very button listed them.
      return { total: counts.total, assigned: counts.assigned, completed: counts.completed };
    },
    [matchCounts],
  );

  const filtersActive = query.trim().length > 0 || statusFilter !== "all";
  function resetAll() {
    setSearch("");
    setStatusFilter("all");
  }

  const loading = clients === null && error === null;

  return (
    <div className="stack stack-5">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">whatsappInquiryHandling</div>
          <h1 className="page-title">Inquiries</h1>
          <p className="section-head__sub">
            Everyone who's reached out about a property — through the WhatsApp
            registration form, or by leaving their name and number on the public
            website — in one list. Refreshes automatically.
          </p>
        </div>
        <div className="row-flex">
          <span className="toolbar__meta">
            {refreshing ? (
              <>
                <span className="spinner" style={{ width: 12, height: 12 }} />{" "}
                Syncing…
              </>
            ) : lastUpdated ? (
              <>
                <span className="badge__dot" style={{ color: "var(--ok)" }} />{" "}
                Updated {relativeTime(lastUpdated)}
              </>
            ) : null}
          </span>
          <Button icon={<IconPlus size={15} />} variant="primary" onClick={openAddDialog}>
            Add
          </Button>
          <Button
            icon={<IconRefresh size={15} />}
            onClick={() => load(true)}
            busy={refreshing}
          >
            Refresh
          </Button>
        </div>
      </header>

      {inquiryStatus && !inquiryStatus.client_database_configured && (
            <Note tone="warn" icon={<IconAlert size={16} />}>
              <strong>CLIENT_DATABASE_URL is not set</strong> — client records are in-memory only and will be lost on
              restart.
            </Note>
          )}

          {inquiryStatus && inquiryStatus.status !== "listening" && (
            <Note tone="info" icon={<IconMessage size={16} />}>
              No number is currently watching for client inquiries. Assign one on the{" "}
              <Link to="/">Connection page</Link> under "Number for Client Inquiry".
            </Note>
          )}

          {allClients.length > 0 && (
            <div className="stat-grid">
              <Stat
                label="Total clients"
                value={allClients.length}
                icon={<IconUsers size={13} />}
                delay={0}
              />
              <Stat
                label="Registered"
                value={registeredCount}
                icon={<IconBuilding size={13} />}
                tone="ok"
                delay={60}
              />
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
                {
                  value: "pending_registration",
                  label: `Pending${pendingCount ? ` (${pendingCount})` : ""}`,
                },
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
              <strong>Backend unreachable.</strong> {error} — the last loaded
              data is still shown below, and polling continues in the
              background.
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
              onViewMatches={(phone, view) => {
                setMatchesInitialView(view);
                setMatchesPhone(phone);
              }}
              countsFor={countsFor}
              onEdit={handleEdit}
              onDelete={setDeleteTarget}
              editBusyPhone={editBusyPhone}
            />
          )}

      {addOpen && (
        <ConfirmDialog
          title="Add a client manually"
          confirmLabel="Open form"
          busy={addBusy}
          onConfirm={handleCreateManualLink}
          onClose={() => !addBusy && setAddOpen(false)}
          body={
            <div className="stack stack-3">
              <p className="section-head__sub" style={{ margin: 0 }}>
                For a walk-in, phone call, or referral who hasn't messaged the
                inquiry WhatsApp number yet. This opens the exact same
                registration form a WhatsApp welcome message links to, in a
                new tab — fill it in yourself, or send that link to the
                client so they can fill it in.
              </p>
              <div className="field">
                <label className="field__label" htmlFor="manual-add-phone">
                  Client's WhatsApp number
                </label>
                <input
                  id="manual-add-phone"
                  type="tel"
                  className="input"
                  autoFocus
                  value={addPhone}
                  onChange={(e) => setAddPhone(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void handleCreateManualLink();
                    }
                  }}
                  placeholder="e.g. 9876543210"
                  disabled={addBusy}
                />
              </div>
              {addError && (
                <Note tone="bad" icon={<IconAlert size={16} />}>
                  {addError}
                </Note>
              )}
            </div>
          }
        />
      )}

      {editBlocked && (() => {
        const client = allClients.find((c) => c.phone === editBlocked);
        const counts = countsFor(editBlocked);
        return (
          <ConfirmDialog
            title="Clear the current assignments first"
            confirmLabel="Open properties"
            cancelLabel="Close"
            onConfirm={() => {
              const phone = editBlocked;
              setEditBlocked(null);
              setMatchesInitialView("main");
              setMatchesPhone(phone);
            }}
            onClose={() => setEditBlocked(null)}
            body={
              <p className="section-head__sub" style={{ margin: 0 }}>
                {client?.name || editBlocked} still has{" "}
                <strong>
                  {counts?.assigned ?? 0} propert{(counts?.assigned ?? 0) === 1 ? "y" : "ies"}
                </strong>{" "}
                out with an agent for a site visit. Editing the requirements re-runs matching, which would
                rewrite the property list underneath visits that are already booked — clear those assignments
                from the properties dialog first, then edit.
              </p>
            }
          />
        );
      })()}

      {deleteTarget && (
        <ConfirmDialog
          title="Delete this inquiry?"
          confirmLabel="Delete inquiry"
          tone="danger"
          busy={deleteBusy}
          onConfirm={handleDelete}
          onClose={() => !deleteBusy && setDeleteTarget(null)}
          body={
            <div className="stack stack-3">
              <p className="section-head__sub" style={{ margin: 0 }}>
                Removes <strong>{deleteTarget.name || deleteTarget.phone}</strong> ({deleteTarget.phone}) — their
                requirements, matches and hand-picked properties.
              </p>
              <p className="section-head__sub" style={{ margin: 0 }}>
                Any site visit still out with an agent is cancelled and those agents are messaged on WhatsApp.
                <strong> Completed visits are kept</strong>, so if this number ever enquires again, the properties
                they have already been shown still read as completed.
              </p>
            </div>
          }
        />
      )}

      {matchesPhone && (
        <ClientMatchesDialog
          phone={matchesPhone}
          clientName={allClients.find((c) => c.phone === matchesPhone)?.name ?? null}
          initialView={matchesInitialView}
          onClose={() => setMatchesPhone(null)}
          onChanged={() => invalidateCounts(matchesPhone)}
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
  onViewMatches,
  countsFor,
  onEdit,
  onDelete,
  editBusyPhone,
}: {
  clients: InquiryClientRecord[];
  query: string;
  expandedPhone: string | null;
  setExpandedPhone: (phone: string | null) => void;
  freshPhones: Set<string>;
  onViewMatches: (phone: string, view: DialogView) => void;
  countsFor: (phone: string) => ClientPropertyCounts | null;
  /** Opens this client's pre-filled requirements form — refused by the
   *  caller while any of their properties is still out with an agent. */
  onEdit: (client: InquiryClientRecord) => void;
  /** Raises the delete confirmation; the caller owns the actual delete. */
  onDelete: (client: InquiryClientRecord) => void;
  editBusyPhone: string | null;
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
              <th>Matches</th>
              <th>Completed</th>
              <th className="cell-pipeline">Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {clients.map((client) => {
              const isOpen = expandedPhone === client.phone;
              return (
                <tr
                  key={client.phone}
                  className={[
                    "row",
                    isOpen && "row--open",
                    freshPhones.has(client.phone) && "row--new",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  tabIndex={0}
                  role="button"
                  onClick={() => setExpandedPhone(client.phone)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setExpandedPhone(client.phone);
                    }
                  }}
                >
                  <td>
                    <ClientStatusBadge client={client} />
                  </td>
                  <td
                    className="cell-truncate cell-strong"
                    title={client.name ?? undefined}
                  >
                    <Highlight text={client.name ?? "—"} query={query} />
                  </td>
                  <td className="cell-truncate">
                    <Copyable text={client.phone} />
                  </td>
                  <td>{client.purpose ?? "—"}</td>
                  <td>{client.property_type ?? "—"}</td>
                  <td>{client.bhk ?? "—"}</td>
                  <td className="cell-num" style={{ textAlign: "right" }}>
                    {formatBudgetRange(
                      client.budget_min_inr,
                      client.budget_max_inr,
                    )}
                  </td>
                  <td
                    className="cell-truncate"
                    title={client.preferred_areas ?? undefined}
                  >
                    <Highlight
                      text={client.preferred_areas ?? "—"}
                      query={query}
                    />
                  </td>
                  <td className="cell-num" style={{ whiteSpace: "nowrap" }}>
                    {client.updated_at
                      ? relativeTime(new Date(client.updated_at))
                      : "—"}
                  </td>
                  <td onClick={(event) => event.stopPropagation()}>
                    <MatchesCell
                      counts={countsFor(client.phone)}
                      onOpen={() => onViewMatches(client.phone, "main")}
                    />
                  </td>
                  <td onClick={(event) => event.stopPropagation()}>
                    <CompletedCell
                      counts={countsFor(client.phone)}
                      onOpen={() => onViewMatches(client.phone, "completed")}
                    />
                  </td>
                  <td className="cell-pipeline">
                    <PipelineStatusBadge status={pipelineStatus(client, countsFor(client.phone))} />
                  </td>
                  {/* Owns its own clicks — the row itself opens the client
                      detail dialog, which is not what either of these
                      means. */}
                  <td onClick={(event) => event.stopPropagation()}>
                    <div className="row-flex" style={{ gap: 6, flexWrap: "nowrap" }}>
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={<IconEdit size={14} />}
                        busy={editBusyPhone === client.phone}
                        onClick={() => onEdit(client)}
                        title="Edit this client's requirements"
                      >
                        Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={<IconTrash size={14} />}
                        onClick={() => onDelete(client)}
                        title="Delete this inquiry"
                      >
                        Delete
                      </Button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {expandedPhone && (() => {
        const client = clients.find((c) => c.phone === expandedPhone);
        return client ? (
          <ClientDetailDialog client={client} onClose={() => setExpandedPhone(null)} />
        ) : null;
      })()}
    </div>
  );
}

/* ------------------------------------------------------------- dialogs */

function ClientDetailDialog({ client, onClose }: { client: InquiryClientRecord; onClose: () => void }) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="Client details">
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">Client</div>
            <h2 className="detail-modal__title cell-truncate">{client.name ?? client.phone}</h2>
            <div className="detail-modal__sub cell-truncate">{client.phone}</div>
            <div className="detail-modal__badges">
              <ClientStatusBadge client={client} />
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>
        <div className="detail-modal__body">
          <ClientDetail client={client} />
        </div>
        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ----------------------------------------------------------------- detail */

function ClientDetail({ client }: { client: InquiryClientRecord }) {
  return (
    <div className="detail">
      {client.pending_action && (
        <Note tone="info" icon={<IconClock size={16} />}>
          Waiting on this client:{" "}
          <strong>{client.pending_action.replace(/_/g, " ")}</strong>
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
            First contacted{" "}
            {client.created_at
              ? relativeTime(new Date(client.created_at))
              : "—"}
          </div>
          <div className="faint small" style={{ marginTop: 4 }}>
            Last updated{" "}
            {client.updated_at
              ? relativeTime(new Date(client.updated_at))
              : "—"}
          </div>
        </div>

        {(client.budget_min_inr !== null || client.budget_max_inr !== null) && (
          <div className="detail__block">
            <div className="detail__k">
              <IconPin size={11} /> Budget
            </div>
            <div className="detail__v">
              {formatBudgetRange(client.budget_min_inr, client.budget_max_inr)}
            </div>
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

/* ---------------------------------------------------- AgentManagement UI */

/** Scored matches AND hand-picked properties, as one number — they are
 *  all just "properties lined up for this client" from here, and the
 *  dialog this opens shows them together anyway. */
function MatchesCell({
  counts,
  onOpen,
  emptyLabel = "No matches",
}: {
  counts: ClientPropertyCounts | null;
  onOpen: () => void;
  /** What an empty cell reads. The Property Interest tab overrides it:
   *  nothing there was ever matched, so "No matches" would describe a
   *  process that doesn't run on those rows. */
  emptyLabel?: string;
}) {
  // null means "not fetched yet, still loading" (see matchCounts' own
  // comment) — a tiny spinner reads as "still working" rather than the
  // dash it briefly used to show, which looked identical to "no matches".
  if (counts === null) return <span className="spinner" style={{ width: 12, height: 12, verticalAlign: "middle" }} />;
  if (counts.total === 0) return <span className="faint small">{emptyLabel}</span>;
  return (
    <button type="button" className="pill-accent" onClick={onOpen}>
      {counts.total} {counts.total === 1 ? "property" : "properties"}
    </button>
  );
}

/** Same button as MatchesCell above, green instead of accent (see
 *  styles/controls.css's own comment on .pill-ok) — opens the same dialog
 *  straight to its Completed tab. Properties counted here are exactly the
 *  ones MatchesCell's own total just subtracted out. */
function CompletedCell({ counts, onOpen }: { counts: ClientPropertyCounts | null; onOpen: () => void }) {
  if (counts === null) return <span className="spinner" style={{ width: 12, height: 12, verticalAlign: "middle" }} />;
  if (counts.completed === 0) return <span className="faint small">—</span>;
  return (
    <button type="button" className="pill-ok" onClick={onOpen}>
      <IconCheck size={12} strokeWidth={2.4} />
      {counts.completed} completed
    </button>
  );
}

function PipelineStatusBadge({ status }: { status: PipelineStatus }) {
  if (status.kind === "partial")
    return (
      <Badge tone="warn" title="Some of this client's properties are still waiting to be handed off">
        {status.assigned} assigned · {status.remaining} remaining
      </Badge>
    );
  if (status.kind === "assigned") return <Badge tone="ok">Assigned</Badge>;
  if (status.kind === "matched") return <Badge tone="accent">Matched</Badge>;
  return <Badge tone="info">New</Badge>;
}

function ClientStatusBadge({ client }: { client: InquiryClientRecord }) {
  const registered = client.status === "registered";
  return (
    <Badge
      tone={registered ? "ok" : "warn"}
      title={client.pending_action ?? undefined}
    >
      {registered ? "Registered" : "Pending"}
    </Badge>
  );
}

function formatBudgetRange(min: number | null, max: number | null): string {
  if (min === null && max === null) return "—";
  if (min !== null && max !== null)
    return `${formatCompactInr(min)} – ${formatCompactInr(max)}`;
  if (min !== null) return `${formatCompactInr(min)}+`;
  return `Up to ${formatCompactInr(max as number)}`;
}
