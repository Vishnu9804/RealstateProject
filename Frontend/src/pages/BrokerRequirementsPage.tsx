import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { matchingApi } from "../api/matchingApi";
import { requirementApi } from "../api/requirementApi";
import type { BrokerRequirementRecord } from "../api/types";
import { useAppStatus } from "../state/StatusProvider";
import { useAuth } from "../state/AuthProvider";
import { useDebounced, usePersistentState, useSearchShortcut } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import { formatCompactInr, relativeTime } from "../lib/formatters";
import {
  compileFilters,
  countActiveFilters,
  describeFilter,
  isFilterActive,
  type ColumnFilter,
  type FilterState,
} from "../lib/propertyFilters";
import {
  REQUIREMENT_FILTER_DEF_BY_KEY,
  REQUIREMENT_FILTER_DEFS,
  requirementSourceLabel,
} from "../lib/requirementFilters";
import { useToast } from "../components/ui/Toast";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import FilterPopover, { type SortControl } from "../components/ui/FilterPopover";
import StickyTableHead from "../components/ui/StickyTableHead";
import RequirementFormDialog from "../components/RequirementFormDialog";
import RequirementMatchesDialog from "../components/RequirementMatchesDialog";
import { FilterTrigger, Pager, compareNullable } from "./DashboardPage";
import { ContactPhoneDetails, ContactPhoneSummary } from "../components/ui/ContactPhones";
import { phoneList, phoneSearchText } from "../lib/phone";
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
  IconChevron,
  IconEdit,
  IconGrid,
  IconInbox,
  IconList,
  IconMessage,
  IconPhone,
  IconPin,
  IconPlus,
  IconRefresh,
  IconSearch,
  IconTag,
  IconTrash,
  IconUsers,
  IconX,
} from "../components/ui/Icons";

/**
 * The demand side of the pipeline: every broker REQUIREMENT captured from a
 * chat selected under "Requirement monitoring" on the Connection page.
 *
 * Built to read exactly like the Properties page — same row cards, same
 * click-a-row-to-open-the-dialog, same action buttons, and the same
 * click-a-column-heading filter dialogs (lib/requirementFilters.ts, on the
 * Properties page's own FilterPopover) — because it is the same job from the
 * other direction, and an operator switching between the two should not have
 * to learn a second set of habits — Add included: a requirement heard on a
 * call, or in a chat this app does not monitor, is typed in here the same way
 * a property is. What it deliberately does NOT carry over is everything that
 * only makes sense for supply: there is no Main/Outsider split, no
 * Needs-review queue and no photos.
 *
 * The columns are exactly the fields matching and sharing use. Everything
 * else a broker wrote (furnishing, size, who it is for, urgency, ...) is in
 * the requirement's description, shown in its detail dialog.
 */

const FETCH_LIMIT = 500;
const PAGE_SIZE = 20;

type ViewMode = "table" | "cards";
type SortKey = "time" | "budget" | "area";
type SortDir = "asc" | "desc";

interface Column {
  key: string;
  label: string;
  sort?: SortKey;
  numeric?: boolean;
  /** Set when this column has a filter dialog — the sort, if any, then lives
   *  inside that dialog, exactly as on the Properties page. */
  filterKey?: string;
}

const COLUMNS: Column[] = [
  { key: "type", label: "Type", filterKey: "type" },
  { key: "bhk", label: "BHK", filterKey: "bhk" },
  { key: "areas", label: "Areas", sort: "area", filterKey: "areas" },
  { key: "listingType", label: "Buy/Rent", filterKey: "listingType" },
  { key: "budget", label: "Budget", sort: "budget", numeric: true, filterKey: "budget" },
  { key: "contact", label: "Contact" },
  { key: "source", label: "Source", filterKey: "source" },
  { key: "time", label: "Received (IST)", sort: "time" },
  // How many properties this requirement's matches dialog lists — the same
  // "N properties" button the Inquiries table's Matches column uses.
  { key: "matches", label: "Matches" },
];

/** Filters that have no column of their own in the table — offered as an
 *  explicit row of triggers instead, so they are never unreachable in table
 *  view. (Every current filter has a column; this keeps it that way if one
 *  is added without one.) */
const COLUMN_FILTER_KEYS = new Set(COLUMNS.map((column) => column.filterKey).filter(Boolean));

const SORT_LABELS: Record<SortKey, { asc: string; desc: string }> = {
  time: { asc: "Oldest first", desc: "Newest first" },
  budget: { asc: "Low → High", desc: "High → Low" },
  area: { asc: "A → Z", desc: "Z → A" },
};

/* ------------------------------------------------------------ formatting */

/** A range reads as one value when both ends are the same ("45L"), as a
 *  span when they differ ("80L – 1cr"), and as an open bound when only one
 *  end is known ("80L+" / "up to 1cr") — a requirement genuinely has all
 *  four shapes, and collapsing them all to "80L – 80L" would be noise. */
function formatRange(min: number | null, max: number | null, render: (value: number) => string): string | null {
  if (min === null && max === null) return null;
  if (min !== null && max !== null) return min === max ? render(min) : `${render(min)} – ${render(max)}`;
  if (min !== null) return `${render(min)}+`;
  return `up to ${render(max as number)}`;
}

function formatBudget(requirement: BrokerRequirementRecord): string {
  const fromAmounts = formatRange(requirement.budget_min_inr, requirement.budget_max_inr, formatCompactInr);
  // The parsed amounts win over the broker's own wording for the same
  // reason formatPrice does it on the Properties page: a column of free
  // text can't be scanned or compared. The wording is one hover away.
  if (fromAmounts) return fromAmounts;
  return requirement.budget_text ?? "—";
}

function areasLabel(requirement: BrokerRequirementRecord): string {
  if (requirement.preferred_areas.length > 0) return requirement.preferred_areas.join(", ");
  return requirement.area_name ?? "—";
}

function requirementTitle(requirement: BrokerRequirementRecord): string {
  const parts = [requirement.bhk, requirement.requirement_type].filter(Boolean).join(" ");
  return parts || requirement.society_name || areasLabel(requirement) || "Requirement";
}

function sourceDetail(requirement: BrokerRequirementRecord): string {
  return `${requirement.sender_name} · ${requirement.sender_phone}`;
}

/* ----------------------------------------------------------------- page */

export default function BrokerRequirementsPage() {
  const toast = useToast();
  const [requirements, setRequirements] = useState<BrokerRequirementRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [search, setSearch] = useState("");
  const query = useDebounced(search, 180);
  const [filters, setFilters] = useState<FilterState>({});
  const activeFilterCount = countActiveFilters(filters);
  const filtersActive = search.trim().length > 0 || activeFilterCount > 0;
  const [openFilter, setOpenFilter] = useState<{ key: string; anchor: HTMLElement } | null>(null);
  const [view, setView] = usePersistentState<ViewMode>("requirements.view", "table");
  const [sortKey, setSortKey] = useState<SortKey>("time");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [page, setPage] = useState(1);

  const [detailId, setDetailId] = useState<string | null>(null);
  // Add and Edit are one dialog (see RequirementFormDialog), held as one bit
  // of state exactly like the Properties page's own formDialog.
  const [formDialog, setFormDialog] = useState<{ mode: "add" | "edit"; requirement?: BrokerRequirementRecord } | null>(
    null,
  );
  const [deleteTarget, setDeleteTarget] = useState<BrokerRequirementRecord | null>(null);
  // Which requirement's matched-properties dialog is open. Held as the
  // whole record, not an id: the dialog needs the requirement's own fields
  // (budget, areas, sender) to render its header and its share message, and
  // the row that opened it already has all of them in hand.
  const [matchesFor, setMatchesFor] = useState<BrokerRequirementRecord | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const searchRef = useRef<HTMLInputElement>(null);
  const listTopRef = useRef<HTMLDivElement>(null);
  const firstPaint = useRef(true);
  // Which records existed at the previous refresh — anything new gets a
  // brief highlight so an arrival is noticeable without stealing focus.
  const seenIds = useRef<Set<string> | null>(null);
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());

  const { status: appStatus } = useAppStatus();

  // The Matches column: record_id -> how many properties that requirement's
  // matches dialog lists. null until the first answer lands. Fetched only
  // when the list itself is (re)loaded — one small aggregate query per load
  // (see matchingApi.getRequirementMatchCounts), never on a timer — and
  // patched per row from the dialog's own result, which costs no request.
  const [matchCounts, setMatchCounts] = useState<Record<string, number> | null>(null);
  const [countsFailed, setCountsFailed] = useState(false);
  // True while the one delayed retry below is pending, so rows still being
  // scored show a spinner rather than flicking through "View matches".
  const [countsRetrying, setCountsRetrying] = useState(false);
  const countsSeq = useRef(0);
  const countsRetryTimer = useRef<number | null>(null);

  const loadCounts = useCallback(async (recordIds: string[], allowRetry: boolean) => {
    const seq = ++countsSeq.current;
    if (countsRetryTimer.current !== null) {
      window.clearTimeout(countsRetryTimer.current);
      countsRetryTimer.current = null;
    }
    try {
      const counts = await matchingApi.getRequirementMatchCounts(FETCH_LIMIT);
      // A newer load has started since — its answer is the one to keep.
      if (seq !== countsSeq.current) return;
      setMatchCounts(counts);
      setCountsFailed(false);
      // A requirement that has just arrived is scored a moment AFTER it is
      // stored, so the list can briefly be ahead of its counts. One delayed
      // re-ask covers that; a requirement still missing afterwards simply
      // offers "View matches", which scores it on open.
      const missing = allowRetry && recordIds.some((id) => !(id in counts));
      setCountsRetrying(missing);
      if (missing) {
        countsRetryTimer.current = window.setTimeout(() => {
          countsRetryTimer.current = null;
          void loadCounts(recordIds, false);
        }, 6000);
      }
    } catch {
      if (seq !== countsSeq.current) return;
      setCountsFailed(true);
      setCountsRetrying(false);
    }
  }, []);

  useEffect(
    () => () => {
      if (countsRetryTimer.current !== null) window.clearTimeout(countsRetryTimer.current);
    },
    [],
  );

  /** The dialog's result is the freshest count there is (its open catches up
   *  on properties added since the requirement was scored). Only patched in
   *  once the column has loaded, so a partial map never replaces a spinner. */
  const handleMatchesLoaded = useCallback((recordId: string, total: number) => {
    setMatchCounts((prev) => (prev === null || prev[recordId] === total ? prev : { ...prev, [recordId]: total }));
  }, []);

  /** undefined = still loading, null = no count to show (never scored, or
   *  the counts could not be read), a number = the count. */
  const countFor = useCallback(
    (recordId: string): number | null | undefined => {
      if (matchCounts === null) return countsFailed ? null : undefined;
      const count = matchCounts[recordId];
      if (typeof count === "number") return count;
      return countsRetrying ? undefined : null;
    },
    [matchCounts, countsFailed, countsRetrying],
  );

  const load = useCallback(
    async (manual = false) => {
      setRefreshing(true);
      try {
        const data = await requirementApi.getRequirements(FETCH_LIMIT);
        setRequirements(data);
        setLastUpdated(new Date());
        setError(null);
        void loadCounts(
          data.map((r) => r.record_id),
          true,
        );

        const incoming = new Set(data.map((r) => r.record_id));
        if (seenIds.current) {
          const added = new Set([...incoming].filter((id) => !seenIds.current!.has(id)));
          if (added.size > 0) {
            setFreshIds(added);
            window.setTimeout(() => setFreshIds(new Set()), 2600);
          }
        }
        seenIds.current = incoming;
        if (manual) toast.push({ tone: "ok", title: "Refreshed", message: `${data.length} requirements loaded.` });
      } catch (err) {
        const message = friendlyError(err);
        setError(message);
        if (manual) toast.push({ tone: "bad", title: "Refresh failed", message });
      } finally {
        setRefreshing(false);
      }
    },
    [toast, loadCounts],
  );

  useEffect(() => {
    void load(false);
    // Mount-only — the version-watch effect below drives every later load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Change-driven rather than interval-driven: the shared status poll
  // already runs on every page, and it carries a token that only moves when
  // a requirement is actually added/edited/deleted (see the backend's
  // requirements_version). Re-fetching the whole list on a timer instead
  // would spend a request every few seconds to usually learn nothing.
  const lastVersion = useRef<string | null>(null);
  useEffect(() => {
    const version = appStatus?.requirements_version;
    if (version === undefined) return;
    if (lastVersion.current === null) {
      lastVersion.current = version;
      return;
    }
    if (lastVersion.current === version) return;
    lastVersion.current = version;
    void load(false);
  }, [appStatus?.requirements_version, load]);

  // "/" jumps to search from anywhere on the page, same as Properties.
  useSearchShortcut(searchRef);

  const allRequirements = useMemo(() => requirements ?? [], [requirements]);

  // Derived from the live list rather than snapshotted at open time — a
  // refresh landing while the dialog is open keeps it current, and a delete
  // closes it automatically for free.
  const detailRequirement = useMemo(
    () => (detailId ? (allRequirements.find((r) => r.record_id === detailId) ?? null) : null),
    [detailId, allRequirements],
  );

  const rentCount = useMemo(
    () => allRequirements.filter((r) => r.listing_type === "Rent").length,
    [allRequirements],
  );
  const localities = useMemo(() => {
    const set = new Set<string>();
    allRequirements.forEach((r) =>
      r.preferred_areas.forEach((area) => area.trim() && set.add(area.trim().toLowerCase())),
    );
    return set.size;
  }, [allRequirements]);

  const passesFilters = useMemo(
    () => compileFilters<BrokerRequirementRecord>(filters, REQUIREMENT_FILTER_DEFS),
    [filters],
  );

  const visibleRequirements = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = allRequirements.filter((requirement) => {
      if (!passesFilters(requirement)) return false;
      if (!needle) return true;
      const haystack = [
        requirement.requirement_type,
        requirement.bhk,
        requirement.society_name,
        requirement.furnishing,
        areasLabel(requirement),
        requirement.budget_text,
        requirement.contact_name,
        phoneSearchText(requirement),
        // Carries every stated detail without a field of its own, so
        // searching "furnished" or "veg" still finds those requirements.
        requirement.description,
        requirementSourceLabel(requirement),
        requirement.sender_name,
        requirement.sender_saved_name,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });

    const direction = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      switch (sortKey) {
        case "budget":
          // Sorted on the lower bound, falling back to the upper one for a
          // "up to 50L" requirement that has no lower bound at all —
          // otherwise every ceiling-only requirement sinks to the bottom
          // regardless of how big its ceiling is.
          return compareNullable(a.budget_min_inr ?? a.budget_max_inr, b.budget_min_inr ?? b.budget_max_inr, direction);
        case "area":
          return compareNullable(a.area_name, b.area_name, direction);
        case "time":
        default:
          return compareNullable(a.message_timestamp, b.message_timestamp, direction);
      }
    });
  }, [allRequirements, query, passesFilters, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(visibleRequirements.length / PAGE_SIZE));
  const pageItems = useMemo(
    () => visibleRequirements.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
    [visibleRequirements, page],
  );

  useEffect(() => setPage(1), [query, filters, sortKey, sortDir]);
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  useEffect(() => {
    if (firstPaint.current) {
      firstPaint.current = false;
      return;
    }
    listTopRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [page]);

  const setColumnFilter = useCallback((key: string, next: ColumnFilter | undefined) => {
    setFilters((prev) => {
      const merged = { ...prev };
      if (next === undefined) delete merged[key];
      else merged[key] = next;
      return merged;
    });
  }, []);

  function applySort(key: SortKey, dir: SortDir) {
    setSortKey(key);
    setSortDir(dir);
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((dir) => (dir === "asc" ? "desc" : "asc"));
    } else {
      applySort(key, key === "area" ? "asc" : "desc");
    }
  }

  function resetAll() {
    setSearch("");
    setFilters({});
  }

  /* The Buy/Rent capsule and the Buy/Rent column dialog are two views of one
     filter, not two competing ones — the same pattern the Properties page
     uses for its Sale/Rent capsule. */
  const listingTypeFilter = filters.listingType;
  const listingTypeSegment: "all" | "Sale" | "Rent" =
    listingTypeFilter?.kind === "values" && listingTypeFilter.selected.length === 1
      ? listingTypeFilter.selected[0].toLowerCase() === "rent"
        ? "Rent"
        : "Sale"
      : "all";

  function setListingTypeSegment(value: "all" | "Sale" | "Rent") {
    if (value === "all") return setColumnFilter("listingType", undefined);
    setColumnFilter("listingType", { kind: "values", selected: [value === "Rent" ? "Rent" : "Buy"] });
  }

  /** Patched into the list in place rather than re-fetching it: the saved
   *  record the backend just returned IS the current one, so a reload would
   *  only re-download every other row to learn nothing. */
  function applySaved(saved: BrokerRequirementRecord, mode: "add" | "edit") {
    if (mode === "add") {
      // De-duplicated by record_id, not a bare append: create_requirement
      // scores the new requirement against every stored property before its
      // response comes back, which can take long enough for the background
      // status poll (StatusProvider, every 7s) to notice requirements_version
      // move and trigger this page's own load() first. If that happens, the
      // fetched list already contains this record — appending it again would
      // leave two rows sharing one record_id, and since confirmDelete removes
      // by record_id, deleting either would silently delete both.
      setRequirements((prev) =>
        prev ? [...prev.filter((r) => r.record_id !== saved.record_id), saved] : [saved],
      );
      // Marked as already seen so the new-arrival highlight doesn't fire for
      // a row this operator just typed themselves.
      seenIds.current?.add(saved.record_id);
    } else {
      setRequirements((prev) => (prev ? prev.map((r) => (r.record_id === saved.record_id ? saved : r)) : prev));
    }
    setFormDialog(null);
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    try {
      await requirementApi.deleteRequirement(deleteTarget.record_id);
      setRequirements((prev) => (prev ? prev.filter((r) => r.record_id !== deleteTarget.record_id) : prev));
      seenIds.current?.delete(deleteTarget.record_id);
      toast.push({ tone: "ok", title: "Deleted", message: "Requirement removed from your database permanently." });
      setDeleteTarget(null);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't delete requirement", message: friendlyError(err) });
    } finally {
      setDeleteBusy(false);
    }
  }

  const loading = requirements === null && error === null;

  const sortControlFor = (column: Column): SortControl | undefined =>
    column.sort
      ? {
          active: sortKey === column.sort,
          dir: sortDir,
          ascLabel: SORT_LABELS[column.sort].asc,
          descLabel: SORT_LABELS[column.sort].desc,
          onSort: (dir) => applySort(column.sort!, dir),
        }
      : undefined;

  const openColumn = openFilter ? COLUMNS.find((column) => column.filterKey === openFilter.key) : undefined;
  const openFilterDef = openFilter ? REQUIREMENT_FILTER_DEF_BY_KEY[openFilter.key] : undefined;
  // In card view there are no column headings to click, so every filter
  // gets a trigger; in table view only the ones without a column do.
  const stripFilterDefs =
    view === "cards" ? REQUIREMENT_FILTER_DEFS : REQUIREMENT_FILTER_DEFS.filter((def) => !COLUMN_FILTER_KEYS.has(def.key));

  return (
    <div className="stack stack-5">
      <header className="section-head">
        <div>
          <h1 className="page-title">Broker Requirements</h1>
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
          <Button variant="primary" icon={<IconPlus size={15} />} onClick={() => setFormDialog({ mode: "add" })}>
            Add requirement
          </Button>
        </div>
      </header>

      {allRequirements.length > 0 && (
        <div className="stat-grid">
          <Stat label="Stored" value={allRequirements.length} icon={<IconInbox size={13} />} delay={0} />
          <Stat label="Showing" value={visibleRequirements.length} icon={<IconSearch size={13} />} tone="accent" delay={60} />
          <Stat label="To buy" value={allRequirements.length - rentCount} icon={<IconBuilding size={13} />} delay={120} />
          <Stat label="To rent" value={rentCount} icon={<IconTag size={13} />} delay={150} />
          <Stat label="Localities asked for" value={localities} icon={<IconPin size={13} />} delay={180} />
        </div>
      )}

      <div className="toolbar">
        <div className="toolbar__grow">
          <SearchInput
            inputRef={searchRef}
            value={search}
            onChange={setSearch}
            placeholder="Search area, BHK, budget, contact, details…"
            shortcutHint="/"
            ariaLabel="Search requirements"
          />
        </div>

        <Segmented<"all" | "Sale" | "Rent">
          ariaLabel="Buy or Rent"
          value={listingTypeSegment}
          onChange={setListingTypeSegment}
          options={[
            { value: "all", label: "All" },
            { value: "Sale", label: "Buy" },
            { value: "Rent", label: "Rent" },
          ]}
        />

        <Segmented<ViewMode>
          ariaLabel="Layout"
          value={view}
          onChange={setView}
          options={[
            { value: "table", label: "Table", icon: <IconList size={14} /> },
            { value: "cards", label: "Cards", icon: <IconGrid size={14} /> },
          ]}
        />

        {filtersActive && (
          <Button size="sm" variant="ghost" onClick={resetAll}>
            Reset all
          </Button>
        )}
      </div>

      {/* Applied filters stay visible after the popover closes — a filter you
          cannot see is a filter you forget you set. */}
      {activeFilterCount > 0 && (
        <div className="filter-strip">
          {REQUIREMENT_FILTER_DEFS.filter((def) => isFilterActive(filters[def.key])).map((def) => (
            <span key={def.key} className="chip chip--filter">
              <strong>{def.label}:</strong> <span>{describeFilter(def, filters[def.key]!)}</span>
              <button
                type="button"
                className="chip__x"
                onClick={() => setColumnFilter(def.key, undefined)}
                aria-label={`Remove the ${def.label} filter`}
              >
                <IconX size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      {allRequirements.length > 0 && stripFilterDefs.length > 0 && (
        <div className="filter-strip">
          <span className="faint small">{view === "cards" ? "Filter by" : "More filters"}</span>
          {stripFilterDefs.map((def) => (
            <FilterTrigger
              key={def.key}
              label={def.label}
              filter={filters[def.key]}
              expanded={openFilter?.key === def.key}
              onOpen={(anchor) => setOpenFilter(openFilter?.key === def.key ? null : { key: def.key, anchor })}
              className="btn btn--sm"
            />
          ))}
        </div>
      )}

      {error && (
        <Note tone="bad" icon={<IconAlert size={17} />}>
          <strong>Backend unreachable.</strong> {error} — the last loaded data is still shown below, and this page
          recovers on its own once the backend is back.
        </Note>
      )}

      {loading && (
        <Panel>
          <div className="stack stack-3">
            <div className="row-flex faint small">
              <span className="spinner" /> Loading requirements…
            </div>
            <SkeletonRows rows={6} />
          </div>
        </Panel>
      )}

      {requirements !== null && allRequirements.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconInbox size={38} />}
            title="Nothing captured yet"
            body="Requirements appear here automatically once a chat selected under Requirement monitoring receives a message asking for a property. Check the Connection page to confirm something is selected there — or add one by hand if you heard it somewhere this app doesn't watch."
            action={
              <Button variant="primary" icon={<IconPlus size={15} />} onClick={() => setFormDialog({ mode: "add" })}>
                Add requirement
              </Button>
            }
          />
        </Panel>
      )}

      {allRequirements.length > 0 && visibleRequirements.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconSearch size={36} />}
            title="No matches"
            body={`None of the ${allRequirements.length} stored requirements match the current search and filters.`}
            action={<Button onClick={resetAll}>Clear everything</Button>}
          />
        </Panel>
      )}

      {visibleRequirements.length > 0 && (
        <>
          <div ref={listTopRef} className="list-anchor" />
          {view === "table" ? (
            <RequirementTable
              requirements={pageItems}
              query={query}
              sortKey={sortKey}
              sortDir={sortDir}
              toggleSort={toggleSort}
              filters={filters}
              openFilterKey={openFilter?.key ?? null}
              onOpenFilter={(key, anchor) => setOpenFilter(openFilter?.key === key ? null : { key, anchor })}
              freshIds={freshIds}
              countFor={countFor}
              onOpenDetail={(requirement) => setDetailId(requirement.record_id)}
              onMatch={setMatchesFor}
              onEdit={(requirement) => setFormDialog({ mode: "edit", requirement })}
              onDelete={setDeleteTarget}
            />
          ) : (
            <RequirementCards
              requirements={pageItems}
              query={query}
              freshIds={freshIds}
              countFor={countFor}
              onOpenDetail={(requirement) => setDetailId(requirement.record_id)}
              onMatch={setMatchesFor}
              onEdit={(requirement) => setFormDialog({ mode: "edit", requirement })}
              onDelete={setDeleteTarget}
            />
          )}
          <Pager page={page} pageCount={pageCount} total={visibleRequirements.length} onChange={setPage} />
        </>
      )}

      {openFilter && openFilterDef && (
        <FilterPopover<BrokerRequirementRecord>
          def={openFilterDef}
          anchorEl={openFilter.anchor}
          properties={allRequirements}
          filter={filters[openFilter.key]}
          onChange={(next) => setColumnFilter(openFilter.key, next)}
          onClose={() => setOpenFilter(null)}
          sort={openColumn ? sortControlFor(openColumn) : undefined}
        />
      )}

      {detailRequirement && (
        <RequirementDetailDialog
          requirement={detailRequirement}
          onClose={() => setDetailId(null)}
          onMatch={(requirement) => setMatchesFor(requirement)}
          onEdit={(requirement) => setFormDialog({ mode: "edit", requirement })}
          onDelete={(requirement) => setDeleteTarget(requirement)}
        />
      )}

      {/* Deliberately independent of the detail dialog: matching is a
          separate errand, and closing the shortlist should put the operator
          back exactly where they were rather than unwinding a stack. */}
      {matchesFor && (
        <RequirementMatchesDialog
          requirement={matchesFor}
          onClose={() => setMatchesFor(null)}
          onMatchesLoaded={handleMatchesLoaded}
        />
      )}

      {formDialog && (
        <RequirementFormDialog
          mode={formDialog.mode}
          requirement={formDialog.requirement}
          onClose={() => setFormDialog(null)}
          onSaved={applySaved}
        />
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="Delete this requirement?"
          tone="danger"
          confirmLabel="Delete forever"
          busy={deleteBusy}
          onClose={() => !deleteBusy && setDeleteTarget(null)}
          onConfirm={confirmDelete}
          body={
            <>
              <p>
                <strong>{requirementTitle(deleteTarget)}</strong>
                {deleteTarget.preferred_areas.length > 0 ? ` · ${areasLabel(deleteTarget)}` : ""}
              </p>
              <p className="faint small">
                {formatBudget(deleteTarget)} · {deleteTarget.listing_type === "Rent" ? "Rent" : "Buy"}
              </p>
              <p style={{ marginTop: 12 }}>
                This removes it from your database <strong>permanently</strong> — it cannot be undone.
              </p>
            </>
          }
        />
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- shared */

interface ListProps {
  requirements: BrokerRequirementRecord[];
  query: string;
  freshIds: Set<string>;
  /** See the page's own countFor: undefined = loading, null = no count. */
  countFor: (recordId: string) => number | null | undefined;
  onOpenDetail: (requirement: BrokerRequirementRecord) => void;
  onMatch: (requirement: BrokerRequirementRecord) => void;
  onEdit: (requirement: BrokerRequirementRecord) => void;
  onDelete: (requirement: BrokerRequirementRecord) => void;
}

/** The Matches column, built like the Inquiries table's own MatchesCell:
 *  the accent "N properties" pill opens this requirement's matches dialog.
 *  Unlike there, the empty states stay clickable — opening the dialog is
 *  what scores properties that arrived since the requirement was last
 *  scored (and scores a never-scored one), so "No matches" must never be a
 *  dead end. */
function MatchesCell({ count, onOpen }: { count: number | null | undefined; onOpen: () => void }) {
  if (count === undefined) return <span className="spinner" style={{ width: 12, height: 12, verticalAlign: "middle" }} />;
  if (count === null || count === 0) {
    return (
      <button
        type="button"
        className="pill-faint"
        onClick={onOpen}
        title="Open to check this requirement against the latest properties"
      >
        {count === 0 ? "No matches" : "View matches"}
      </button>
    );
  }
  return (
    <button type="button" className="pill-accent" onClick={onOpen}>
      {count} {count === 1 ? "property" : "properties"}
    </button>
  );
}

/** Edit and Delete, in the Properties page's own arrangement so the two
 *  tables' action columns line up and muscle-memory carries across. Matching
 *  has its own Matches column (see MatchesCell above), exactly as on the
 *  Inquiries table. Shared between the table cell and the card footer so the
 *  two layouts can never drift apart. */
function RowActions({
  requirement,
  onEdit,
  onDelete,
}: {
  requirement: BrokerRequirementRecord;
  onEdit: (requirement: BrokerRequirementRecord) => void;
  onDelete: (requirement: BrokerRequirementRecord) => void;
}) {
  // Delete is admin-only — Backend/Controller/BrokerRequirementController/
  // broker_requirement_controller.py's DELETE route requires it
  // server-side regardless; hiding the button here is purely so an
  // employee never sees one that would fail with a 403.
  const { isAdmin } = useAuth();
  return (
    <div className="row-actions">
      <button
        type="button"
        className="row-actions__btn"
        title="Edit"
        aria-label="Edit this requirement"
        onClick={() => onEdit(requirement)}
      >
        <IconEdit size={15} />
      </button>
      {isAdmin && (
        <button
          type="button"
          className="row-actions__btn row-actions__btn--danger"
          title="Delete"
          aria-label="Delete this requirement"
          onClick={() => onDelete(requirement)}
        >
          <IconTrash size={15} />
        </button>
      )}
    </div>
  );
}

/* ----------------------------------------------------------------- table */

function RequirementTable({
  requirements,
  query,
  sortKey,
  sortDir,
  toggleSort,
  filters,
  openFilterKey,
  onOpenFilter,
  freshIds,
  countFor,
  onOpenDetail,
  onMatch,
  onEdit,
  onDelete,
}: ListProps & {
  sortKey: SortKey;
  sortDir: SortDir;
  toggleSort: (key: SortKey) => void;
  filters: FilterState;
  openFilterKey: string | null;
  onOpenFilter: (key: string, anchor: HTMLElement) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // One definition, rendered both in the table and in the copy pinned under
  // the top bar — see components/ui/StickyTableHead.tsx.
  const headerRow = (
    <tr>
      {COLUMNS.map((column) => {
        const sorted = column.sort && column.sort === sortKey;
        return (
          <th
            key={column.key}
            aria-sort={sorted ? (sortDir === "asc" ? "ascending" : "descending") : undefined}
            style={column.numeric ? { textAlign: "right" } : undefined}
          >
            {column.filterKey ? (
              // Filterable columns open their dialog on click; the
              // sort lives inside it, same as the Properties page.
              <FilterTrigger
                label={column.label}
                filter={filters[column.filterKey]}
                expanded={openFilterKey === column.filterKey}
                onOpen={(anchor) => onOpenFilter(column.filterKey!, anchor)}
              />
            ) : column.sort ? (
              <button type="button" onClick={() => toggleSort(column.sort!)} title={`Sort by ${column.label}`}>
                {column.label}
                <IconChevron size={12} className="sort-caret" />
              </button>
            ) : (
              column.label
            )}
          </th>
        );
      })}
      <th style={{ textAlign: "right" }}>Actions</th>
    </tr>
  );
  return (
    <div className="table-frame anim-rise">
      <StickyTableHead scrollRef={scrollRef}>{headerRow}</StickyTableHead>
      <div className="table-scroll" ref={scrollRef}>
        <table className="table">
          <thead>{headerRow}</thead>
          <tbody>
            {requirements.map((requirement) => (
              <tr
                key={requirement.record_id}
                className={`row${freshIds.has(requirement.record_id) ? " row--new" : ""}`}
                // Rows are openable from the keyboard, not just by clicking
                // — the dialog holds the original message, which is the
                // whole point of an audit trail.
                tabIndex={0}
                role="button"
                onClick={() => onOpenDetail(requirement)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onOpenDetail(requirement);
                  }
                }}
              >
                <td className="cell-truncate cell-strong" title={requirement.requirement_type ?? undefined}>
                  <Highlight text={requirement.requirement_type ?? "—"} query={query} />
                </td>
                <td>{requirement.bhk ?? "—"}</td>
                <td className="cell-truncate" title={areasLabel(requirement)}>
                  <Highlight text={areasLabel(requirement)} query={query} />
                  {requirement.society_name && (
                    <div className="cell-muted cell-truncate" title={requirement.society_name}>
                      <Highlight text={requirement.society_name} query={query} />
                    </div>
                  )}
                </td>
                <td>
                  <Badge tone={requirement.listing_type === "Rent" ? "info" : "ok"}>
                    {requirement.listing_type === "Rent" ? "Rent" : "Buy"}
                  </Badge>
                </td>
                <td
                  className="cell-num cell-strong"
                  style={{ textAlign: "right" }}
                  // The broker's own wording is one hover away, so
                  // normalising the display never hides the source.
                  title={requirement.budget_text ?? undefined}
                >
                  {formatBudget(requirement)}
                </td>
                <td className="cell-truncate">
                  <Highlight text={requirement.contact_name ?? "—"} query={query} />
                  {phoneList(requirement).length > 0 && (
                    <div className="cell-muted">
                      <ContactPhoneSummary record={requirement} />
                    </div>
                  )}
                </td>
                <td className="cell-truncate" title={sourceDetail(requirement)}>
                  <span className="faint small" style={{ display: "block" }}>
                    {requirement.chat_type === "group" ? "Group" : "Personal"}
                  </span>
                  <Highlight text={requirementSourceLabel(requirement)} query={query} />
                </td>
                <td className="cell-num" style={{ whiteSpace: "nowrap" }}>
                  {requirement.formatted_timestamp}
                </td>
                {/* Owns its clicks and keys — the row itself opens the
                    requirement's detail dialog, which is not what this means. */}
                <td onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
                  <MatchesCell count={countFor(requirement.record_id)} onOpen={() => onMatch(requirement)} />
                </td>
                <td onClick={(event) => event.stopPropagation()}>
                  <RowActions requirement={requirement} onEdit={onEdit} onDelete={onDelete} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- cards */

function RequirementCards({
  requirements,
  query,
  freshIds,
  countFor,
  onOpenDetail,
  onMatch,
  onEdit,
  onDelete,
}: ListProps) {
  return (
    <div className="card-grid">
      {requirements.map((requirement, index) => (
        <Panel
          key={requirement.record_id}
          interactive
          pad={false}
          delay={Math.min(index * 35, 420)}
          className={`pcard${freshIds.has(requirement.record_id) ? " anim-pop" : ""}`}
          onClick={() => onOpenDetail(requirement)}
        >
          <div className="pcard__top">
            <div style={{ minWidth: 0 }}>
              <div className="pcard__title cell-truncate" style={{ maxWidth: "100%" }}>
                <Highlight text={requirementTitle(requirement)} query={query} />
              </div>
              <div className="pcard__sub cell-truncate" style={{ maxWidth: "100%" }}>
                <Highlight text={areasLabel(requirement)} query={query} />
              </div>
            </div>
            <Badge tone={requirement.listing_type === "Rent" ? "info" : "ok"}>
              {requirement.listing_type === "Rent" ? "Rent" : "Buy"}
            </Badge>
          </div>

          <div className="pcard__price" title={requirement.budget_text ?? undefined}>
            {formatBudget(requirement)}
          </div>

          <div className="pcard__facts">
            {requirement.bhk && (
              <span className="fact">
                <IconBuilding size={12} />
                {requirement.bhk}
              </span>
            )}
            {requirement.requirement_type && (
              <span className="fact">
                <IconTag size={12} />
                {requirement.requirement_type}
              </span>
            )}
          </div>

          {/* Card view has no columns, so the Matches column's button lives
              here. Owns its clicks and keys, like the table cell. */}
          <div
            className="row-flex"
            style={{ gap: 8, alignSelf: "flex-start" }}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            <span className="faint small">Matches</span>
            <MatchesCell count={countFor(requirement.record_id)} onOpen={() => onMatch(requirement)} />
          </div>

          {phoneList(requirement).length > 0 && (
            <div className="fact" style={{ alignSelf: "flex-start" }}>
              <IconPhone size={12} />
              <ContactPhoneSummary record={requirement}>
                {(primary) => (requirement.contact_name ? `${requirement.contact_name} · ${primary}` : primary)}
              </ContactPhoneSummary>
            </div>
          )}

          <div className="pcard__foot">
            <span className="cell-truncate" title={sourceDetail(requirement)}>
              <IconUsers size={11} /> {requirementSourceLabel(requirement)} · {requirement.formatted_timestamp}
            </span>
            <div onClick={(event) => event.stopPropagation()}>
              <RowActions requirement={requirement} onEdit={onEdit} onDelete={onDelete} />
            </div>
          </div>
        </Panel>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- detail */

/**
 * The full-detail dialog opened by clicking any row or card. Everything the
 * table/card layouts show only a slice of is shown here at once, including
 * the description (which carries every stated detail without a field of its
 * own) and the original WhatsApp message — the point of a dedicated dialog
 * is that nothing about the requirement is left behind the click. The same
 * Edit and Delete actions available inline are repeated in the footer
 * alongside Cancel, so acting on a requirement never requires closing the
 * dialog first to reach them.
 */
function RequirementDetailDialog({
  requirement,
  onClose,
  onMatch,
  onEdit,
  onDelete,
}: {
  requirement: BrokerRequirementRecord;
  onClose: () => void;
  onMatch: (requirement: BrokerRequirementRecord) => void;
  onEdit: (requirement: BrokerRequirementRecord) => void;
  onDelete: (requirement: BrokerRequirementRecord) => void;
}) {
  // Delete is admin-only — see RowActions' own comment on the same check.
  const { isAdmin } = useAuth();
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
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="Requirement details">
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">
              Requirement · {requirement.listing_type === "Rent" ? "Rent" : "Buy"}
            </div>
            <h2 className="detail-modal__title cell-truncate">{requirementTitle(requirement)}</h2>
            <div className="detail-modal__sub cell-truncate">{areasLabel(requirement)}</div>
            <div className="detail-modal__badges">
              <Badge tone={requirement.listing_type === "Rent" ? "info" : "ok"}>
                {requirement.listing_type === "Rent" ? "Rent" : "Buy"}
              </Badge>
              {requirement.requirement_type && (
                <span className="fact">
                  <IconTag size={12} />
                  {requirement.requirement_type}
                </span>
              )}
              {requirement.bhk && (
                <span className="fact">
                  <IconBuilding size={12} />
                  {requirement.bhk}
                </span>
              )}
              {/* A field of its own again now that matching scores it — the
                  broker's own wording is still in the description below. */}
              {requirement.furnishing && <span className="fact">{requirement.furnishing}</span>}
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          <div className="detail__grid">
            <div className="detail__block">
              <div className="detail__k">Budget</div>
              <div className="detail__v">{formatBudget(requirement)}</div>
              {requirement.budget_text && (
                <div className="faint small" style={{ marginTop: 4 }}>
                  As written: {requirement.budget_text}
                </div>
              )}
            </div>

            <div className="detail__block">
              <div className="detail__k">Areas asked for</div>
              <div className="detail__v">{areasLabel(requirement)}</div>
              {requirement.society_name && (
                <div className="faint small" style={{ marginTop: 4 }}>
                  Society: {requirement.society_name}
                </div>
              )}
            </div>

            {/* The size wanted per type, only when one was stated — every
                other detail a broker wrote about size still lives in the
                description below, in their own words. */}
            {requirement.property_sizes && Object.keys(requirement.property_sizes).length > 0 && (
              <div className="detail__block">
                <div className="detail__k">Size wanted</div>
                <div className="detail__v">
                  {Object.entries(requirement.property_sizes)
                    .map(([type, size]) => `${type}: ${size}`)
                    .join(" · ")}
                </div>
              </div>
            )}

            <div className="detail__block">
              <div className="detail__k">Contact</div>
              <div className="detail__v">{requirement.contact_name ?? "—"}</div>
              <ContactPhoneDetails record={requirement} />
            </div>

            <div className="detail__block">
              <div className="detail__k">Sender</div>
              <div className="detail__v">
                {requirement.sender_name}
                {requirement.sender_saved_name && requirement.sender_saved_name !== requirement.sender_name && (
                  <span className="faint"> · saved as {requirement.sender_saved_name}</span>
                )}
              </div>
              <div className="detail__v" style={{ marginTop: 4 }}>
                <Copyable text={requirement.sender_phone} />
              </div>
            </div>

            <div className="detail__block">
              <div className="detail__k">Source</div>
              <div className="detail__v">{requirementSourceLabel(requirement)}</div>
              <div className="faint small" style={{ marginTop: 4 }}>
                {requirement.chat_type === "group" ? "Group" : "Personal"} · {requirement.formatted_timestamp}
              </div>
            </div>
          </div>

          {requirement.description && (
            <div className="detail__block">
              <div className="detail__k">Description &amp; other details</div>
              <div className="detail__v">{requirement.description}</div>
            </div>
          )}

          <div className="detail__block">
            <div className="detail__k">
              <IconMessage size={11} /> Original message
            </div>
            <div className="detail__msg">{requirement.message_text}</div>
          </div>
        </div>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <span className="row-flex" style={{ marginLeft: "auto", gap: 10 }}>
            {/* The primary action on a requirement: what have we got that
                fits it. Placed before Edit/Delete because it is what an
                operator opens this dialog to do. */}
            <Button variant="primary" icon={<IconBuilding size={14} />} onClick={() => onMatch(requirement)}>
              Match properties
            </Button>
            <Button variant="ghost" icon={<IconEdit size={14} />} onClick={() => onEdit(requirement)}>
              Edit
            </Button>
            {isAdmin && (
              <Button className="btn--danger" icon={<IconTrash size={14} />} onClick={() => onDelete(requirement)}>
                Delete
              </Button>
            )}
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
