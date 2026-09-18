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
import { useAuth } from "../state/AuthProvider";
import { useDebounced } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import { formatIst, fromIstFields, relativeTime, toIstFields } from "../lib/formatters";
import { CLIENT_FETCH_LIMIT } from "../lib/fetchLimits";
import { getCachedClients, setCachedClients } from "../lib/inquiryListCache";
import { useToast } from "../components/ui/Toast";
import ClientMatchesDialog, { type DialogView } from "../components/ClientMatchesDialog";
import ClientFormDialog from "../components/ClientFormDialog";
import ClientDetailDialog, { formatBudgetRange } from "../components/ClientDetailDialog";
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
  SkeletonRows,
  Stat,
} from "../components/ui/Primitives";
import {
  IconAlert,
  IconCheck,
  IconEdit,
  IconImage,
  IconInbox,
  IconMessage,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconTrash,
  IconUsers,
  IconX,
} from "../components/ui/Icons";

/** What the Status column says about one client. "partial" is the case
 *  that used to be invisible: some of this client's properties are out
 *  with an agent and some are still sitting here waiting to be handed
 *  off — a half-finished round reads as done under a flat "Assigned". */
type PipelineStatus =
  | { kind: "loading" }
  | { kind: "partial"; assigned: number; remaining: number }
  | { kind: "assigned"; assigned: number }
  | { kind: "remaining"; remaining: number }
  | { kind: "completed" }
  | { kind: "matched" }
  | { kind: "new" };

/** Read off the live counts alone — never client.assigned_agent_id, which
 *  is only a "was ever handed off" flag the backend never clears: it kept
 *  this badge on "Assigned" after every visit was completed, and flashed
 *  "Assigned" right after a hand-off while the counts were still catching
 *  up. `remaining` is always the Matches total minus what is out with an
 *  agent right now, so the badge and the Matches pill never disagree. */
function pipelineStatus(counts: ClientPropertyCounts | null): PipelineStatus {
  if (counts === null) return { kind: "loading" };
  const remaining = Math.max(counts.total - counts.assigned, 0);
  if (counts.assigned > 0) {
    if (remaining > 0) return { kind: "partial", assigned: counts.assigned, remaining };
    return { kind: "assigned", assigned: counts.assigned };
  }
  // Nothing out with an agent now, but visits have happened — whatever is
  // left in Matches is what still waits to be handed off.
  if (counts.completed > 0) return remaining > 0 ? { kind: "remaining", remaining } : { kind: "completed" };
  if (counts.total > 0) return { kind: "matched" };
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
const FETCH_LIMIT = CLIENT_FETCH_LIMIT;
/** How often the match-count map is re-read even when no version signal
 *  moved — the backstop that catches a count changed somewhere this page
 *  cannot observe (see the counts effect). Deliberately well above the
 *  8-second poll: it is a safety net, not the refresh mechanism, and the
 *  response it usually gets is a bodyless 304. */
const COUNTS_HEARTBEAT_MS = 60000;

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
  const [expandedPhone, setExpandedPhone] = useState<string | null>(null);

  const searchRef = useRef<HTMLInputElement>(null);
  const seenPhones = useRef<Set<string> | null>(null);
  const [freshPhones, setFreshPhones] = useState<Set<string>>(new Set());
  // Last clients_version this page actually fetched a list for — see
  // load() below. null means "never fetched yet", which always forces a
  // fetch regardless of what the version says.
  const lastClientsVersion = useRef<string | null>(null);
  // AgentManagement feature: every client's match counts (the Matches pill,
  // the Completed pill and the pipeline Status badge), fetched as ONE map —
  // matchingApi.getAllMatchCounts, not getMatchCounts per row.
  //
  // This used to be one request per visible client, gated on that client's
  // own updated_at. The gate worked; the shape did not. Any tick that had
  // to re-read them (a first load, a new registration, a website enquiry)
  // opened one request per row, the browser ran six at a time, and every
  // one of those requests loaded that client's entire cached match set
  // server-side to produce three integers. With five hundred clients and
  // three hundred thousand cached match rows behind them, that is what the
  // spinners in those three columns actually were: a queue draining.
  //
  // One request now answers for every row, from one in-memory map on the
  // backend (see Backend/Service/ClientPropertyMatchingService/
  // match_counts_service.py). `countsVersion` remembers the
  // (clients_version, leads_version) pair the held map was fetched for, so
  // an ordinary poll that changed neither doesn't re-fetch at all — exactly
  // the gate the per-row version did, applied once instead of per row.
  const [matchCounts, setMatchCounts] = useState<Record<string, MatchCounts> | null>(null);
  const countsSignature = useRef<string | null>(null);
  const countsFetchedAt = useRef(0);
  const countsInFlight = useRef(false);
  // Bumped to force the counts effect below to run again, AND to ask the
  // backend for a freshly rebuilt map rather than its held one — adding a
  // property by hand or handing one to an agent changes a client's counts
  // without touching clients_version, so the version gate alone would keep
  // serving the stale number.
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

  // Per-row actions. Delete keeps its confirmation dialog; Add and Edit
  // open the client form dialog below.
  const [deleteTarget, setDeleteTarget] = useState<InquiryClientRecord | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  // "Add" and "Edit" — one tall dialog on this page (components/
  // ClientFormDialog.tsx), filled in right here. Both used to open the
  // public site's requirements form in a new tab through a freshly minted
  // form link; now nothing leaves this page and nothing is sent to the
  // client. Add covers a walk-in, a phone call or a referral; Edit opens
  // pre-filled with everything on file, photo included.
  const [clientForm, setClientForm] = useState<
    { mode: "add" } | { mode: "edit"; client: InquiryClientRecord } | null
  >(null);

  /**
   * Edit = the client form dialog, pre-filled with what we hold. Saving a
   * changed requirement re-runs matching through the one code path that
   * has always done that (client_store.upsert_client's auto-recompute).
   *
   * While ANY of this client's properties is still out with an agent, the
   * requirement fields open read-only: re-scoring rewrites the High/Medium/
   * Low buckets underneath visits already booked in someone's calendar, so
   * those assignments have to be cleared (and the agents told) first. Name,
   * email and photo never re-run matching, so they stay editable — and the
   * backend holds the same line on save whatever this page believed.
   */
  function handleEdit(client: InquiryClientRecord) {
    setClientForm({ mode: "edit", client });
  }

  /** Folds a saved client straight into the list — the backend already
   *  returned the record as stored, so there is nothing to refetch. The
   *  shared list cache keeps the version it was last fetched at, so the next
   *  status tick still re-reads the list once and confirms it; the record's
   *  new updated_at is also what makes the counts effect below re-read its
   *  Matches. */
  function applySavedClient(saved: InquiryClientRecord, mode: "add" | "edit") {
    const list = clients ?? [];
    const next =
      mode === "add"
        ? [saved, ...list.filter((c) => c.phone !== saved.phone)]
        : list.map((c) => (c.phone === saved.phone ? saved : c));
    setClients(next);
    setCachedClients(next, lastClientsVersion.current ?? "");
    if (mode === "add") {
      seenPhones.current?.add(saved.phone);
      setFreshPhones(new Set([saved.phone]));
      window.setTimeout(() => setFreshPhones(new Set()), 2600);
    }
    setClientForm(null);
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

        // clients_version and leads_version are both carried on the status
        // response and both feed the counts effect below (see
        // countsVersion) — a new website enquiry can change an EXISTING
        // client's Matches count without that client's own updated_at
        // moving at all, so the counts have to watch both.

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

  const visibleClients = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return allClients.filter((client) => {
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
        ...Object.values(client.property_sizes ?? {}),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });
  }, [allClients, query]);

  // AgentManagement feature: every client's match counts, in ONE request
  // (see matchingApi.getAllMatchCounts for what this replaced and why).
  //
  // Gated exactly as the per-row version was, just once instead of per row:
  // clients_version moves whenever any client is added, edited or removed,
  // and leads_version whenever a website enquiry lands — between the two,
  // every change the old per-client updated_at gate could notice is still
  // noticed here. An ordinary poll that moves neither does not fetch at
  // all, and a poll that does fetch revalidates against the backend's own
  // content hash, so an unchanged answer costs a bodyless 304.
  //
  // countsNonce is the third trigger: an operator action (a hand-off, a
  // hand-picked property) changes a count without moving either version, so
  // it both re-runs this and asks the backend to rebuild rather than serve
  // the map it is holding.
  //
  // And a slow heartbeat on top of those three, because some things that
  // change a count move none of them: the nightly rescore, an agent
  // completing a visit from the Visits page, another operator working in
  // their own browser. The per-row version gate could not see any of those
  // either — a number changed elsewhere simply stayed wrong on screen until
  // something unrelated happened to that client. A re-read this
  // infrequent, against a validator that is a hash of the counts
  // themselves, costs a bodyless 304 whenever nothing has in fact changed.
  useEffect(() => {
    if (!inquiryStatus) return;
    // One at a time. `inquiryStatus` is a new object on every status tick,
    // so this effect runs every few seconds; without this a request slower
    // than the poll interval would have a second one started on top of it.
    if (countsInFlight.current) return;
    const signature = `${inquiryStatus.clients_version}|${inquiryStatus.leads_version}|${countsNonce}`;
    const changed = countsSignature.current !== signature;
    const heartbeatDue = Date.now() - countsFetchedAt.current >= COUNTS_HEARTBEAT_MS;
    if (!changed && !heartbeatDue) return;

    const previous = countsSignature.current;
    countsSignature.current = signature;
    countsFetchedAt.current = Date.now();
    countsInFlight.current = true;
    matchingApi
      // A real change is worth making the backend rebuild for, so the new
      // number is on screen on this very response rather than whenever its
      // held map next expires. The heartbeat deliberately does not: it is
      // the backstop, and the backend's own expiry is what paces it.
      .getAllMatchCounts(changed && previous !== null)
      .then(setMatchCounts)
      .catch(() => {
        // Put the clock and the signature back so the next tick retries,
        // rather than latching this one as "already fetched" — otherwise a
        // single failed request would freeze the three columns.
        countsSignature.current = previous;
        countsFetchedAt.current = 0;
      })
      .finally(() => {
        countsInFlight.current = false;
      });
  }, [inquiryStatus, countsNonce]);

  /** Re-read the counts on the next tick, from a freshly rebuilt map — see
   *  countsNonce above for why a version change alone isn't enough. Takes
   *  the phone that changed for call-site readability; the map is fetched
   *  whole either way, which is the entire point of it. */
  const invalidateCounts = useCallback((_phone: string) => {
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
      // null (the map itself, or this phone within it) means "not fetched
      // yet" and renders as a spinner, exactly as it did when each row was
      // fetched separately. Every client in the backend's list is in the
      // map, so the only phone missing from a loaded map is one this page
      // added locally a moment ago and the next fetch has yet to cover.
      const counts = matchCounts?.[phone];
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

  const filtersActive = query.trim().length > 0;
  function resetAll() {
    setSearch("");
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
          <Button icon={<IconPlus size={15} />} variant="primary" onClick={() => setClientForm({ mode: "add" })}>
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
              {inquiryStatus && (
                <Stat
                  label="Property inquiries seen"
                  value={inquiryStatus.property_inquiry_count}
                  icon={<IconMessage size={13} />}
                  tone="accent"
                  delay={60}
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
                body={`None of the ${allClients.length} clients match the current search.`}
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
              // The same fold-back an Edit save uses: the endpoint returns
              // the updated record, so the cell and the shared list cache
              // both show the new date without re-fetching the whole list.
              onFollowUpSaved={(saved) => applySavedClient(saved, "edit")}
            />
          )}

      {clientForm && (
        <ClientFormDialog
          mode={clientForm.mode}
          client={clientForm.mode === "edit" ? clientForm.client : undefined}
          assignedCount={clientForm.mode === "edit" ? (countsFor(clientForm.client.phone)?.assigned ?? 0) : 0}
          onOpenProperties={
            clientForm.mode === "edit"
              ? () => {
                  const phone = clientForm.client.phone;
                  setClientForm(null);
                  setMatchesInitialView("main");
                  setMatchesPhone(phone);
                }
              : undefined
          }
          onClose={() => setClientForm(null)}
          onSaved={applySavedClient}
        />
      )}

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
          onChanged={(hint) => {
            // A hand-off only moves `assigned` (total and completed stay
            // put), and the dialog already knows by how much — so the
            // Status badge reads "4 assigned · 11 remaining" the moment the
            // hand-off lands, with no extra request. invalidateCounts just
            // below still re-reads the server's figure, which replaces this.
            const newlyAssigned = hint?.newlyAssigned ?? 0;
            if (newlyAssigned > 0) {
              const phone = matchesPhone;
              setMatchCounts((prev) => {
                const current = prev?.[phone];
                if (!prev || !current) return prev;
                return {
                  ...prev,
                  [phone]: { ...current, assigned: Math.min(current.assigned + newlyAssigned, current.total) },
                };
              });
            }
            invalidateCounts(matchesPhone);
          }}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------- last follow-up */

/**
 * The editable "Last follow-up" cell.
 *
 * The value is normally written for you: the post-site-visit follow-up
 * message goes out 24 hours after a visit is marked complete, and stamps
 * this with the moment it actually sent (Backend/Service/
 * AgentManagementService/visit_reminder_service.py). This cell exists for
 * the other half — a follow-up that happened by phone, in person, or on a
 * day the automatic one didn't cover — so the date can be corrected by hand.
 *
 * Everything shown and typed here is IST, converted at the edge (see
 * lib/formatters.ts). The backend stores a UTC instant, so the automatic
 * stamp and a hand-set one are directly comparable.
 */
function FollowUpPopover({
  client,
  anchorEl,
  onClose,
  onSaved,
}: {
  client: InquiryClientRecord;
  anchorEl: HTMLElement;
  onClose: () => void;
  onSaved: (saved: InquiryClientRecord) => void;
}) {
  const toast = useToast();
  const popoverRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const stored = toIstFields(client.last_follow_up_dates);
  const [date, setDate] = useState(stored?.date ?? "");
  const [time, setTime] = useState(stored?.time ?? "");
  const storedReport = client.follow_up_report ?? "";
  const [report, setReport] = useState(storedReport);

  // Same placement and dismissal rules as components/ui/FilterPopover.tsx —
  // re-measured on scroll/resize so it tracks its cell when the table scrolls
  // sideways instead of drifting away from it.
  useEffect(() => {
    const place = () => {
      const rect = anchorEl.getBoundingClientRect();
      const width = 300;
      // Date/time row + the report box below it — kept in step with what
      // this popover actually renders, since it decides whether there is
      // room to open downwards or it has to flip above the cell.
      const height = 360;
      const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
      const below = rect.bottom + 8;
      const top = below + height > window.innerHeight - 8 ? Math.max(8, rect.top - height - 8) : below;
      setPosition({ left, top });
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [anchorEl]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (popoverRef.current?.contains(target) || anchorEl.contains(target)) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [anchorEl, onClose]);

  /** Fills both boxes with the current moment in IST — the common case by
   *  far, since the reason someone opens this is usually "I just called
   *  them". Only fills the inputs; nothing is stored until Save. */
  function setNow() {
    const now = toIstFields(new Date().toISOString());
    if (!now) return;
    setDate(now.date);
    setTime(now.time);
  }

  async function save(at: string | null, note: string | null) {
    setSaving(true);
    try {
      onSaved(await inquiryClientApi.setFollowUp(client.phone, at, note));
      toast.push({
        tone: "ok",
        title: at || note ? "Follow-up saved" : "Follow-up cleared",
        message: at
          ? formatIst(at)
          : note
            ? "Report saved without a date."
            : "This client now reads as never followed up.",
      });
      onClose();
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't save the follow-up", message: friendlyError(err) });
    } finally {
      setSaving(false);
    }
  }

  // A date with no time means midnight IST (see fromIstFields). Both are
  // saved together, so a report can be written on its own and a date picked
  // on its own — Save is offered as soon as either is there or either has
  // been changed, rather than only for a date as it used to be.
  const iso = date ? fromIstFields(date, time) : null;
  const trimmedReport = report.trim();
  const canSave = Boolean(iso || trimmedReport) || trimmedReport !== storedReport.trim();

  function submit() {
    if (saving || !canSave) return;
    void save(iso, trimmedReport || null);
  }

  /** Enter saves from any box in here (the popover has no form to submit).
   *  In the report box, Shift+Enter still starts a new line — a report is
   *  free text and often more than one. */
  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    submit();
  }

  return createPortal(
    <div
      ref={popoverRef}
      className="popover"
      role="dialog"
      aria-label="Last follow-up"
      style={{
        left: position?.left ?? -9999,
        top: position?.top ?? -9999,
        width: 300,
        visibility: position ? "visible" : "hidden",
      }}
    >
      <div className="popover__head">
        <span className="popover__title">Last follow-up</span>
        <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
          <IconX size={13} />
        </button>
      </div>

      <div className="stack stack-3" style={{ padding: 12 }}>
        <div className="row-flex" style={{ gap: 8 }}>
          <input
            className="input"
            type="date"
            aria-label="Follow-up date (IST)"
            value={date}
            onChange={(event) => setDate(event.target.value)}
            onKeyDown={onKeyDown}
            disabled={saving}
          />
          <input
            className="input"
            type="time"
            aria-label="Follow-up time (IST)"
            value={time}
            onChange={(event) => setTime(event.target.value)}
            onKeyDown={onKeyDown}
            disabled={saving}
          />
        </div>
        <span className="field__hint">
          Times are IST. {iso ? formatIst(iso) : "Pick a date, or use Now."}
        </span>

        <div className="field">
          <label className="field__hint" htmlFor="follow-up-report">
            Follow-up report
          </label>
          <textarea
            id="follow-up-report"
            className="input"
            rows={4}
            style={{ resize: "vertical", minHeight: 76 }}
            placeholder="What was said on this follow-up?"
            value={report}
            onChange={(event) => setReport(event.target.value)}
            onKeyDown={onKeyDown}
            disabled={saving}
          />
          <span className="field__hint">Enter saves · Shift + Enter for a new line</span>
        </div>

        <div className="row-flex" style={{ gap: 8, flexWrap: "wrap" }}>
          <Button size="sm" variant="ghost" onClick={setNow} disabled={saving}>
            Now
          </Button>
          <Button size="sm" variant="primary" busy={saving} disabled={!canSave} onClick={submit}>
            Save
          </Button>
          {(client.last_follow_up_dates || client.follow_up_report) && (
            <Button size="sm" variant="ghost" disabled={saving} onClick={() => save(null, null)}>
              Clear
            </Button>
          )}
        </div>
      </div>
    </div>,
    document.body,
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
  onFollowUpSaved,
}: {
  clients: InquiryClientRecord[];
  query: string;
  expandedPhone: string | null;
  setExpandedPhone: (phone: string | null) => void;
  freshPhones: Set<string>;
  onViewMatches: (phone: string, view: DialogView) => void;
  countsFor: (phone: string) => ClientPropertyCounts | null;
  /** Opens this client's pre-filled Edit dialog — requirements read-only
   *  while any of their properties is still out with an agent. */
  onEdit: (client: InquiryClientRecord) => void;
  /** Raises the delete confirmation; the caller owns the actual delete. */
  onDelete: (client: InquiryClientRecord) => void;
  /** Folds the record the follow-up endpoint returned back into the list. */
  onFollowUpSaved: (saved: InquiryClientRecord) => void;
}) {
  const [followUp, setFollowUp] = useState<{ phone: string; anchor: HTMLElement } | null>(null);
  const followUpClient = followUp ? clients.find((c) => c.phone === followUp.phone) : undefined;
  // Delete is admin-only — Backend/Controller/WhatsAppInquiryHandlingController/
  // whatsapp_inquiry_controller.py's DELETE /clients/{phone} requires it
  // server-side regardless; hiding the button here is purely so an
  // employee never sees one that would fail with a 403.
  const { isAdmin } = useAuth();
  return (
    <div className="table-frame anim-rise">
      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Phone</th>
              <th>Purpose</th>
              <th>Type</th>
              <th>BHK</th>
              <th style={{ textAlign: "right" }}>Budget</th>
              <th>Areas</th>
              <th>Last follow-up</th>
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
                  <td
                    className="cell-truncate cell-strong"
                    title={client.name ?? undefined}
                  >
                    <Highlight text={client.name ?? "—"} query={query} />
                    {/* A marker, never the photo itself — the list carries
                        only has_photo, so this page never moves image data
                        until a client is opened. */}
                    {client.has_photo && (
                      <span className="client-photo-mark" title="Has a photo">
                        <IconImage size={12} />
                      </span>
                    )}
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
                  {/* Owns its own clicks — opening the picker is not the
                      same as opening the client's detail dialog, which is
                      what the row itself does. */}
                  <td onClick={(event) => event.stopPropagation()} style={{ whiteSpace: "nowrap" }}>
                    <button
                      type="button"
                      className="text-link"
                      title={
                        client.last_follow_up_dates
                          ? `Last followed up ${formatIst(client.last_follow_up_dates)} — click to change`
                          : "Not followed up yet — click to set a date"
                      }
                      onClick={(event) => {
                        // Read synchronously: React runs a functional state
                        // updater during the NEXT render, by which point the
                        // synthetic event's currentTarget is already null.
                        const anchor = event.currentTarget;
                        setFollowUp((open) =>
                          open?.phone === client.phone ? null : { phone: client.phone, anchor },
                        );
                      }}
                    >
                      {client.last_follow_up_dates ? formatIst(client.last_follow_up_dates) : "Set date"}
                    </button>
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
                    <PipelineStatusBadge status={pipelineStatus(countsFor(client.phone))} />
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
                        onClick={() => onEdit(client)}
                        title="Edit this client's requirements"
                      >
                        Edit
                      </Button>
                      {isAdmin && (
                        <Button
                          size="sm"
                          variant="ghost"
                          icon={<IconTrash size={14} />}
                          onClick={() => onDelete(client)}
                          title="Delete this inquiry"
                        >
                          Delete
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Anchored to the cell that opened it. Keyed off the live client from
          `clients`, so the value it shows follows a poll that changed it —
          and it closes on its own if that client leaves the filtered list. */}
      {followUp && followUpClient && (
        <FollowUpPopover
          client={followUpClient}
          anchorEl={followUp.anchor}
          onClose={() => setFollowUp(null)}
          onSaved={onFollowUpSaved}
        />
      )}

      {expandedPhone && (() => {
        const client = clients.find((c) => c.phone === expandedPhone);
        return client ? (
          <ClientDetailDialog
            client={client}
            onClose={() => setExpandedPhone(null)}
            onEdit={(target) => {
              setExpandedPhone(null);
              onEdit(target);
            }}
          />
        ) : null;
      })()}
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
  // Same "still loading" spinner the Matches/Completed cells show.
  if (status.kind === "loading")
    return <span className="spinner" style={{ width: 12, height: 12, verticalAlign: "middle" }} />;
  if (status.kind === "partial")
    return (
      <Badge tone="warn" title="Some of this client's properties are still waiting to be handed off">
        {status.assigned} assigned · {status.remaining} remaining
      </Badge>
    );
  if (status.kind === "assigned") return <Badge tone="ok">Assigned</Badge>;
  if (status.kind === "remaining")
    return (
      <Badge tone="warn" title="Nothing is out with an agent right now — these properties are still waiting to be handed off">
        {status.remaining} remaining
      </Badge>
    );
  if (status.kind === "completed") return <Badge tone="ok">All visited</Badge>;
  if (status.kind === "matched") return <Badge tone="accent">Matched</Badge>;
  return <Badge tone="info">New</Badge>;
}
