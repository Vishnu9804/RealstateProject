import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { propertyApi } from "../api/propertyApi";
import type { PropertyRecord } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { useDebounced, usePersistentState } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import { formatCarpetArea, formatPrice, formatPricePerUnit, relativeTime } from "../lib/formatters";
import {
  compileFilters,
  countActiveFilters,
  describeFilter,
  FILTER_DEF_BY_KEY,
  FILTER_DEFS,
  isFilterActive,
  sourceDetail,
  sourceLabel,
  type ColumnFilter,
  type FilterState,
} from "../lib/propertyFilters";
import { useToast } from "../components/ui/Toast";
import FilterPopover, { type SortControl } from "../components/ui/FilterPopover";
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
  IconChevron,
  IconGrid,
  IconInbox,
  IconList,
  IconMessage,
  IconMove,
  IconPhone,
  IconPin,
  IconRefresh,
  IconRuler,
  IconSearch,
  IconTag,
  IconTrash,
  IconUsers,
  IconX,
} from "../components/ui/Icons";

/** The three mutually-exclusive views: Main and Outsider are the property's
 *  permanent home (review_status), toggled via the Main/Outsider capsule;
 *  Needs review is an orthogonal queue (needs_review=true, from either
 *  home) opened via its own button and left via Accept. */
type ViewTab = "main" | "outsider" | "needsReview";

const REFRESH_INTERVAL_MS = 8000;
const FETCH_LIMIT = 500;

/**
 * Rows shown at once.
 *
 * The whole matching set is still fetched in one request — the endpoint
 * takes only a `limit`, and more importantly the column filters are built
 * from every value seen so far, which is impossible if only the current
 * page is in memory. What pagination buys here is rendering: 20 rows
 * instead of several hundred, which is what the page actually pays for on
 * every poll, sort and keystroke.
 */
const PAGE_SIZE = 20;

type ViewMode = "table" | "cards";
type SortKey = "time" | "price" | "priceUnit" | "area" | "society" | "locality";
type SortDir = "asc" | "desc";

interface Column {
  key: string;
  label: string;
  sort?: SortKey;
  numeric?: boolean;
  /** Set when this column has a filter dialog. Society, address and contact
   *  deliberately have none — they are near-unique free text, so a value
   *  picker would list one option per row and a range means nothing. The
   *  search box already searches all three. */
  filterKey?: string;
}

const COLUMNS: Column[] = [
  { key: "society", label: "Society", sort: "society" },
  { key: "locality", label: "Area", sort: "locality", filterKey: "locality" },
  { key: "address", label: "Address" },
  { key: "bhk", label: "BHK", filterKey: "bhk" },
  { key: "type", label: "Type", filterKey: "type" },
  { key: "listingType", label: "Sale/Rent", filterKey: "listingType" },
  { key: "carpet", label: "Carpet area", sort: "area", numeric: true, filterKey: "carpet" },
  { key: "price", label: "Price", sort: "price", numeric: true, filterKey: "price" },
  { key: "priceUnit", label: "Price/unit", sort: "priceUnit", numeric: true, filterKey: "priceUnit" },
  { key: "contact", label: "Contact" },
  { key: "source", label: "Source", filterKey: "source" },
  { key: "time", label: "Received (IST)", sort: "time" },
];

/** Ascending/descending read differently per column type — "A → Z" for a
 *  name, "Low → High" for money — and a generic label makes the reader
 *  translate before they can choose. */
const SORT_LABELS: Record<SortKey, { asc: string; desc: string }> = {
  time: { asc: "Oldest first", desc: "Newest first" },
  price: { asc: "Low → High", desc: "High → Low" },
  priceUnit: { asc: "Low → High", desc: "High → Low" },
  area: { asc: "Small → Large", desc: "Large → Small" },
  society: { asc: "A → Z", desc: "Z → A" },
  locality: { asc: "A → Z", desc: "Z → A" },
};

export default function DashboardPage() {
  const toast = useToast();
  const [properties, setProperties] = useState<PropertyRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [search, setSearch] = useState("");
  const query = useDebounced(search, 180);
  const [filters, setFilters] = useState<FilterState>({});
  const [openFilter, setOpenFilter] = useState<{ key: string; anchor: HTMLElement } | null>(null);
  // The chosen layout is remembered: on a wide screen the table wins, on a
  // laptop people often prefer cards, and re-picking it on every visit is a
  // small annoyance that repeats forever.
  const [view, setView] = usePersistentState<ViewMode>("dashboard.view", "table");
  const [sortKey, setSortKey] = useState<SortKey>("time");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [detailId, setDetailId] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  // Which of the three views is open. Persisted like the table/cards choice
  // — re-picking "Main" on every visit would be a small annoyance that
  // repeats forever.
  const [viewTab, setViewTab] = usePersistentState<ViewTab>("dashboard.tab", "main");
  const [confirmAction, setConfirmAction] = useState<{ type: "delete" | "move"; property: PropertyRecord } | null>(
    null,
  );
  const [actionBusy, setActionBusy] = useState(false);

  const searchRef = useRef<HTMLInputElement>(null);
  const listTopRef = useRef<HTMLDivElement>(null);
  const firstPaint = useRef(true);
  // Which records existed at the previous poll — anything new gets a brief
  // highlight so an arrival is noticeable without stealing focus or moving
  // anything the user is currently reading.
  const seenIds = useRef<Set<string> | null>(null);
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());

  const load = useCallback(
    async (manual = false) => {
      setRefreshing(true);
      try {
        const data = await propertyApi.getProperties(FETCH_LIMIT);
        setProperties(data);
        setLastUpdated(new Date());
        setError(null);

        const incoming = new Set(data.map((p) => p.record_id));
        if (seenIds.current) {
          const added = new Set([...incoming].filter((id) => !seenIds.current!.has(id)));
          if (added.size > 0) {
            setFreshIds(added);
            window.setTimeout(() => setFreshIds(new Set()), 2600);
          }
        }
        seenIds.current = incoming;
        if (manual) toast.push({ tone: "ok", title: "Refreshed", message: `${data.length} properties loaded.` });
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

  // "/" jumps to search from anywhere on the page — the single most-used
  // control should never require aiming at it.
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

  const allProperties = useMemo(() => properties ?? [], [properties]);

  // Derived from the live list rather than snapshotted at open time — a
  // background poll landing while the dialog is open keeps it showing
  // current data, and a delete (which removes the property from this list
  // entirely) closes it automatically for free, with no extra bookkeeping.
  const detailProperty = useMemo(
    () => (detailId ? (allProperties.find((p) => p.record_id === detailId) ?? null) : null),
    [detailId, allProperties],
  );

  const needsReviewCount = useMemo(() => allProperties.filter((p) => p.needs_review).length, [allProperties]);

  const outsiderCount = useMemo(
    () => allProperties.filter((p) => p.review_status === "outsider").length,
    [allProperties],
  );

  // The base set for whichever of the three views is open. Main/Outsider
  // each exclude anything still pending review — a flagged property lives
  // only in the Needs review queue until a human accepts it, at which point
  // it simply reappears here under whichever review_status it already has.
  const tabFiltered = useMemo(() => {
    if (viewTab === "needsReview") return allProperties.filter((p) => p.needs_review);
    if (viewTab === "outsider") return allProperties.filter((p) => p.review_status === "outsider" && !p.needs_review);
    return allProperties.filter((p) => p.review_status === "accepted" && !p.needs_review);
  }, [allProperties, viewTab]);

  const localities = useMemo(() => {
    // Case-folded to match how the Area filter groups its options —
    // otherwise this tile claims more localities than that picker lists.
    const set = new Set<string>();
    allProperties.forEach((p) => p.area_name?.trim() && set.add(p.area_name.trim().toLowerCase()));
    return set.size;
  }, [allProperties]);

  const passesFilters = useMemo(() => compileFilters(filters), [filters]);

  const visibleProperties = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = tabFiltered.filter((property) => {
      if (!passesFilters(property)) return false;
      if (!needle) return true;
      const haystack = [
        property.society_name,
        property.area_name,
        property.address,
        property.contact_name,
        property.contact_phone,
        property.description,
        sourceLabel(property),
        property.sender_name,
        property.sender_saved_name,
        property.bhk,
        property.property_type,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });

    const direction = sortDir === "asc" ? 1 : -1;
    // Records missing the sorted field always sink to the bottom regardless
    // of direction — a column of dashes at the top is never what someone
    // sorting by price wanted to see.
    return [...filtered].sort((a, b) => {
      switch (sortKey) {
        case "price":
          return compareNullable(a.price_amount_inr, b.price_amount_inr, direction);
        case "priceUnit":
          return compareNullable(a.price_per_unit_amount_inr, b.price_per_unit_amount_inr, direction);
        case "area":
          return compareNullable(a.carpet_area_sqft, b.carpet_area_sqft, direction);
        case "society":
          return compareNullable(a.society_name, b.society_name, direction);
        case "locality":
          return compareNullable(a.area_name, b.area_name, direction);
        case "time":
        default:
          return compareNullable(a.message_timestamp, b.message_timestamp, direction);
      }
    });
  }, [tabFiltered, query, passesFilters, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(visibleProperties.length / PAGE_SIZE));
  const pageItems = useMemo(
    () => visibleProperties.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
    [visibleProperties, page],
  );

  // Any change that narrows the list invalidates the page you were on —
  // page 7 of a 3-page result is a blank screen that reads as a bug.
  useEffect(() => setPage(1), [query, filters, sortKey, sortDir, viewTab]);
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  // Turning the page should put you at the top of the new rows, not
  // wherever the bottom of the old ones happened to leave you.
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
      // Time and money read newest/highest-first; names read A→Z.
      applySort(key, key === "society" || key === "locality" ? "asc" : "desc");
    }
  }

  const activeFilterCount = countActiveFilters(filters);
  const filtersActive = query.trim().length > 0 || activeFilterCount > 0;

  function resetAll() {
    setSearch("");
    setFilters({});
  }

  /* The Sale/Rent capsule and the Sale/Rent column dialog are two views of
     one filter, not two competing ones — with only two possible values,
     selecting both and selecting neither mean the same thing, so they map
     onto each other exactly (same pattern the old Status segment used). */
  const listingTypeFilter = filters.listingType;
  const listingTypeSegment: "all" | "Sale" | "Rent" =
    listingTypeFilter?.kind === "values" && listingTypeFilter.selected.length === 1
      ? (listingTypeFilter.selected[0] as "Sale" | "Rent")
      : "all";

  function setListingTypeSegment(value: "all" | "Sale" | "Rent") {
    if (value === "all") return setColumnFilter("listingType", undefined);
    setColumnFilter("listingType", { kind: "values", selected: [value] });
  }

  function updateLocalProperty(recordId: string, next: PropertyRecord) {
    setProperties((prev) => (prev ? prev.map((p) => (p.record_id === recordId ? next : p)) : prev));
  }

  function removeLocalProperty(recordId: string) {
    setProperties((prev) => (prev ? prev.filter((p) => p.record_id !== recordId) : prev));
  }

  async function handleAccept(property: PropertyRecord) {
    try {
      const updated = await propertyApi.updateProperty(property.record_id, { needs_review: false });
      updateLocalProperty(property.record_id, updated);
      setDetailId(null);
      toast.push({
        tone: "ok",
        title: "Accepted",
        message: `Moved into ${updated.review_status === "outsider" ? "Outsider" : "Main"}.`,
      });
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't accept property", message: friendlyError(err) });
    }
  }

  async function confirmMove() {
    if (!confirmAction || confirmAction.type !== "move") return;
    const property = confirmAction.property;
    const nextStatus = property.review_status === "outsider" ? "accepted" : "outsider";
    setActionBusy(true);
    try {
      const updated = await propertyApi.updateProperty(property.record_id, { review_status: nextStatus });
      updateLocalProperty(property.record_id, updated);
      setDetailId(null);
      toast.push({ tone: "ok", title: "Moved", message: `Moved to ${nextStatus === "outsider" ? "Outsider" : "Main"}.` });
      setConfirmAction(null);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't move property", message: friendlyError(err) });
    } finally {
      setActionBusy(false);
    }
  }

  async function confirmDelete() {
    if (!confirmAction || confirmAction.type !== "delete") return;
    const property = confirmAction.property;
    setActionBusy(true);
    try {
      await propertyApi.deleteProperty(property.record_id);
      removeLocalProperty(property.record_id);
      toast.push({ tone: "ok", title: "Deleted", message: "Property removed from your database permanently." });
      setConfirmAction(null);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't delete property", message: friendlyError(err) });
    } finally {
      setActionBusy(false);
    }
  }

  const loading = properties === null && error === null;

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

  return (
    <div className="stack stack-5">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Step 2 — Intake</div>
          <h1 className="page-title">Properties</h1>
          <p className="section-head__sub">
            Every qualified WhatsApp message, structured into columns and de-duplicated. Click any column heading to
            filter by the values seen so far. The list refreshes itself every few seconds.
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

      {allProperties.length > 0 && (
        <div className="stat-grid">
          <Stat label="Stored" value={allProperties.length} icon={<IconBuilding size={13} />} delay={0} />
          <Stat label="Showing" value={visibleProperties.length} icon={<IconSearch size={13} />} tone="accent" delay={60} />
          <Stat
            label="Need review"
            value={needsReviewCount}
            icon={<IconAlert size={13} />}
            tone={needsReviewCount > 0 ? "warn" : undefined}
            delay={120}
          />
          <Stat
            label="Outsider"
            value={outsiderCount}
            icon={<IconPin size={13} />}
            tone={outsiderCount > 0 ? "accent" : undefined}
            delay={150}
          />
          <Stat label="Localities" value={localities} icon={<IconPin size={13} />} delay={180} />
        </div>
      )}

      <div className="toolbar">
        <div className="toolbar__grow">
          <SearchInput
            inputRef={searchRef}
            value={search}
            onChange={setSearch}
            placeholder="Search society, area, address, contact…  (press / )"
            ariaLabel="Search properties"
          />
        </div>

        <Segmented<"main" | "outsider">
          ariaLabel="Main or Outsider"
          value={viewTab === "needsReview" ? null : viewTab}
          onChange={setViewTab}
          options={[
            { value: "main", label: "Main" },
            { value: "outsider", label: `Outsider${outsiderCount ? ` (${outsiderCount})` : ""}` },
          ]}
        />

        <Button
          size="sm"
          variant={viewTab === "needsReview" ? "primary" : "ghost"}
          icon={<IconAlert size={14} />}
          onClick={() => setViewTab(viewTab === "needsReview" ? "main" : "needsReview")}
        >
          Needs review{needsReviewCount ? ` (${needsReviewCount})` : ""}
        </Button>

        <Segmented<"all" | "Sale" | "Rent">
          ariaLabel="Sale or Rent"
          value={listingTypeSegment}
          onChange={setListingTypeSegment}
          options={[
            { value: "all", label: "All" },
            { value: "Sale", label: "Sale" },
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

      {/* Applied filters stay visible after the popover closes. A filter you
          cannot see is a filter you forget you set, and then the table looks
          like it is missing data. */}
      {activeFilterCount > 0 && (
        <div className="filter-strip">
          {FILTER_DEFS.filter((def) => isFilterActive(filters[def.key])).map((def) => (
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

      {/* In card view there are no column headings to click, so the same
          dialogs get an explicit row of triggers rather than disappearing. */}
      {view === "cards" && allProperties.length > 0 && (
        <div className="filter-strip">
          <span className="faint small">Filter by</span>
          {FILTER_DEFS.map((def) => (
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
          <strong>Backend unreachable.</strong> {error} — the last loaded data is still shown below, and polling
          continues in the background.
        </Note>
      )}

      {loading && (
        <Panel>
          <div className="stack stack-3">
            <div className="row-flex faint small">
              <span className="spinner" /> Loading properties…
            </div>
            <SkeletonRows rows={6} />
          </div>
        </Panel>
      )}

      {properties !== null && allProperties.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconInbox size={38} />}
            title="Nothing captured yet"
            body="Properties appear here automatically once a monitored chat receives a message that looks property-related. Check the Connection page to confirm something is being watched."
          />
        </Panel>
      )}

      {allProperties.length > 0 && tabFiltered.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconInbox size={36} />}
            title={viewTab === "needsReview" ? "Nothing needs review" : `No ${viewTab === "outsider" ? "outsider" : "Main"} properties`}
            body={
              viewTab === "needsReview"
                ? "Every stored property has been reviewed — new arrivals land here only when duplicate detection is unsure."
                : `No properties currently sit in ${viewTab === "outsider" ? "Outsider" : "Main"}. Switch tabs to see the rest.`
            }
          />
        </Panel>
      )}

      {tabFiltered.length > 0 && visibleProperties.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconSearch size={36} />}
            title="No matches"
            body={`None of the ${tabFiltered.length} properties in this view match the current search and filters.`}
            action={<Button onClick={resetAll}>Clear everything</Button>}
          />
        </Panel>
      )}

      {visibleProperties.length > 0 && (
        <>
          <div ref={listTopRef} className="list-anchor" />
          {view === "table" ? (
            <PropertyTable
              properties={pageItems}
              query={query}
              onOpenDetail={(property) => setDetailId(property.record_id)}
              sortKey={sortKey}
              sortDir={sortDir}
              toggleSort={toggleSort}
              freshIds={freshIds}
              filters={filters}
              openFilterKey={openFilter?.key ?? null}
              onOpenFilter={(key, anchor) => setOpenFilter(openFilter?.key === key ? null : { key, anchor })}
              viewTab={viewTab}
              onAccept={handleAccept}
              onMove={(property) => setConfirmAction({ type: "move", property })}
              onDelete={(property) => setConfirmAction({ type: "delete", property })}
            />
          ) : (
            <PropertyCards
              properties={pageItems}
              query={query}
              onOpenDetail={(property) => setDetailId(property.record_id)}
              freshIds={freshIds}
              viewTab={viewTab}
              onAccept={handleAccept}
              onMove={(property) => setConfirmAction({ type: "move", property })}
              onDelete={(property) => setConfirmAction({ type: "delete", property })}
            />
          )}
          <Pager page={page} pageCount={pageCount} total={visibleProperties.length} onChange={setPage} />
        </>
      )}

      {openFilter && (
        <FilterPopover
          def={FILTER_DEF_BY_KEY[openFilter.key]}
          anchorEl={openFilter.anchor}
          properties={allProperties}
          filter={filters[openFilter.key]}
          onChange={(next) => setColumnFilter(openFilter.key, next)}
          onClose={() => setOpenFilter(null)}
          sort={openColumn ? sortControlFor(openColumn) : undefined}
        />
      )}

      {detailProperty && (
        <PropertyDetailDialog
          property={detailProperty}
          viewTab={viewTab}
          onAccept={handleAccept}
          onMove={(property) => setConfirmAction({ type: "move", property })}
          onDelete={(property) => setConfirmAction({ type: "delete", property })}
          onClose={() => setDetailId(null)}
        />
      )}

      {confirmAction && (
        <ConfirmDialog
          title={confirmAction.type === "delete" ? "Delete this property?" : "Move this property?"}
          body={
            confirmAction.type === "delete" ? (
              <>
                <PropertySummary property={confirmAction.property} />
                <p style={{ marginTop: 12 }}>
                  This removes it from your database <strong>permanently</strong> — it cannot be undone.
                </p>
              </>
            ) : (
              <>
                <PropertySummary property={confirmAction.property} />
                <p style={{ marginTop: 12 }}>
                  Move it to{" "}
                  <strong>{confirmAction.property.review_status === "outsider" ? "Main" : "Outsider"}</strong>?
                </p>
              </>
            )
          }
          confirmLabel={confirmAction.type === "delete" ? "Delete forever" : "Move"}
          tone={confirmAction.type === "delete" ? "danger" : "default"}
          busy={actionBusy}
          onConfirm={confirmAction.type === "delete" ? confirmDelete : confirmMove}
          onClose={() => !actionBusy && setConfirmAction(null)}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------ confirmations */

function PropertySummary({ property }: { property: PropertyRecord }) {
  return (
    <>
      <p>
        <strong>{property.society_name ?? property.area_name ?? "Unnamed property"}</strong>
        {property.area_name && property.society_name ? ` · ${property.area_name}` : ""}
      </p>
      <p className="faint small">
        {[property.bhk, property.property_type, property.address].filter(Boolean).join(" · ") ||
          "No further address details"}
      </p>
      <p className="faint small">
        {formatPrice(property.price_text, property.price_amount_inr)}
        {property.contact_phone ? ` · ${property.contact_phone}` : ""}
      </p>
    </>
  );
}

/* ----------------------------------------------------------------- pager */

/** First, last and a window around the current page, with gaps collapsed.
 *  Rendering every number is unusable past a handful of pages. */
function pageNumbers(page: number, pageCount: number): (number | "gap")[] {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, index) => index + 1);
  const wanted = [1, pageCount, page, page - 1, page + 1].filter((n) => n >= 1 && n <= pageCount);
  const unique = [...new Set(wanted)].sort((a, b) => a - b);
  const out: (number | "gap")[] = [];
  let previous = 0;
  for (const number of unique) {
    if (previous && number - previous > 1) out.push("gap");
    out.push(number);
    previous = number;
  }
  return out;
}

function Pager({
  page,
  pageCount,
  total,
  onChange,
}: {
  page: number;
  pageCount: number;
  total: number;
  onChange: (page: number) => void;
}) {
  const from = (page - 1) * PAGE_SIZE + 1;
  const to = Math.min(page * PAGE_SIZE, total);

  return (
    <nav className="pager" aria-label="Property pages">
      <span className="pager__info">
        Showing{" "}
        <strong className="tnum">
          {from}&ndash;{to}
        </strong>{" "}
        of <strong className="tnum">{total}</strong>
      </span>

      {pageCount > 1 && (
        <div className="pager__controls">
          <Button size="sm" onClick={() => onChange(page - 1)} disabled={page === 1}>
            Previous
          </Button>
          {pageNumbers(page, pageCount).map((entry, index) =>
            entry === "gap" ? (
              <span key={`gap-${index}`} className="pager__gap" aria-hidden="true">
                &hellip;
              </span>
            ) : (
              <button
                key={entry}
                type="button"
                className={`pager__num${entry === page ? " pager__num--on" : ""}`}
                aria-current={entry === page ? "page" : undefined}
                aria-label={`Page ${entry}`}
                onClick={() => onChange(entry)}
              >
                {entry}
              </button>
            ),
          )}
          <Button size="sm" onClick={() => onChange(page + 1)} disabled={page === pageCount}>
            Next
          </Button>
        </div>
      )}
    </nav>
  );
}

/* ---------------------------------------------------------------- trigger */

function FilterTrigger({
  label,
  filter,
  expanded,
  onOpen,
  className = "th-trigger",
}: {
  label: string;
  filter: ColumnFilter | undefined;
  expanded: boolean;
  onOpen: (anchor: HTMLElement) => void;
  className?: string;
}) {
  const active = isFilterActive(filter);
  const count = filter?.kind === "values" ? filter.selected.length : active ? 1 : 0;
  return (
    <button
      type="button"
      className={`${className}${active ? " th-trigger--filtered" : ""}`}
      aria-expanded={expanded}
      aria-haspopup="dialog"
      title={`Filter by ${label}`}
      onClick={(event) => onOpen(event.currentTarget)}
    >
      {label}
      {count > 0 && <span className="th-badge">{count}</span>}
      <IconChevron size={12} className="th-caret" />
    </button>
  );
}

/* ------------------------------------------------------------------ table */

interface ListProps {
  properties: PropertyRecord[];
  query: string;
  onOpenDetail: (property: PropertyRecord) => void;
  freshIds: Set<string>;
  viewTab: ViewTab;
  onAccept: (property: PropertyRecord) => void;
  onMove: (property: PropertyRecord) => void;
  onDelete: (property: PropertyRecord) => void;
}

function PropertyTable({
  properties,
  query,
  onOpenDetail,
  sortKey,
  sortDir,
  toggleSort,
  freshIds,
  filters,
  openFilterKey,
  onOpenFilter,
  viewTab,
  onAccept,
  onMove,
  onDelete,
}: ListProps & {
  sortKey: SortKey;
  sortDir: SortDir;
  toggleSort: (key: SortKey) => void;
  filters: FilterState;
  openFilterKey: string | null;
  onOpenFilter: (key: string, anchor: HTMLElement) => void;
}) {
  return (
    <div className="table-frame anim-rise">
      <div className="table-scroll">
        <table className="table">
          <thead>
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
                      // sort lives inside it, so one heading never has to
                      // mean two different things depending on where you hit.
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
          </thead>
          <tbody>
            {properties.map((property) => {
              const flagged = property.needs_review;
              const outsider = property.review_status === "outsider";
              return (
                <tr
                  key={property.record_id}
                  className={[
                    "row",
                    flagged && "row--flagged",
                    outsider && "row--outsider",
                    freshIds.has(property.record_id) && "row--new",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  // Rows are reachable and openable from the keyboard, not
                  // just by clicking — the detail dialog holds the original
                  // message, which is the whole point of an audit trail.
                  tabIndex={0}
                  role="button"
                  onClick={() => onOpenDetail(property)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onOpenDetail(property);
                    }
                  }}
                >
                    <td className="cell-truncate cell-strong" title={property.society_name ?? undefined}>
                      <Highlight text={property.society_name ?? "—"} query={query} />
                    </td>
                    <td className="cell-truncate" title={property.area_name ?? undefined}>
                      <Highlight text={property.area_name ?? "—"} query={query} />
                    </td>
                    <td className="cell-truncate" title={property.address ?? undefined}>
                      <Highlight text={property.address ?? "—"} query={query} />
                    </td>
                    <td>{property.bhk ?? "—"}</td>
                    <td>{property.property_type ?? "—"}</td>
                    <td>
                      <Badge tone={property.listing_type === "Rent" ? "info" : "ok"}>{property.listing_type}</Badge>
                    </td>
                    <td className="cell-num" style={{ textAlign: "right" }}>
                      {formatCarpetArea(property.carpet_area_sqft, property.carpet_area_unit)}
                    </td>
                    <td
                      className="cell-num cell-strong"
                      style={{ textAlign: "right" }}
                      // The broker's own wording is one hover away, so
                      // normalising the display never hides the source.
                      title={property.price_text ?? undefined}
                    >
                      {formatPrice(property.price_text, property.price_amount_inr)}
                    </td>
                    <td
                      className="cell-num"
                      style={{ textAlign: "right" }}
                      title={property.price_per_unit_text ?? undefined}
                    >
                      {formatPricePerUnit(property.price_per_unit_text, property.price_per_unit_amount_inr)}
                    </td>
                    <td className="cell-truncate">
                      <Highlight text={property.contact_name ?? "—"} query={query} />
                      {property.contact_phone && (
                        <div className="cell-muted">
                          <Copyable text={property.contact_phone} />
                        </div>
                      )}
                    </td>
                    <td className="cell-truncate" title={sourceDetail(property)}>
                      <span className="faint small" style={{ display: "block" }}>
                        {property.chat_type === "group" ? "Group" : "Personal"}
                      </span>
                      <Highlight text={sourceLabel(property)} query={query} />
                    </td>
                    <td className="cell-num" style={{ whiteSpace: "nowrap" }}>
                      {property.formatted_timestamp}
                    </td>
                  <td onClick={(event) => event.stopPropagation()}>
                    <RowActions
                      property={property}
                      viewTab={viewTab}
                      onAccept={onAccept}
                      onMove={onMove}
                      onDelete={onDelete}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ cards */

function PropertyCards({
  properties,
  query,
  onOpenDetail,
  freshIds,
  viewTab,
  onAccept,
  onMove,
  onDelete,
}: ListProps) {
  return (
    <div className="card-grid">
      {properties.map((property, index) => {
        return (
          <Panel
            key={property.record_id}
            interactive
            pad={false}
            delay={Math.min(index * 35, 420)}
            className={`pcard${freshIds.has(property.record_id) ? " anim-pop" : ""}`}
            onClick={() => onOpenDetail(property)}
          >
            <div className="pcard__top">
              <div style={{ minWidth: 0 }}>
                <div className="pcard__title cell-truncate" style={{ maxWidth: "100%" }}>
                  <Highlight text={property.society_name ?? property.area_name ?? "Unnamed property"} query={query} />
                </div>
                <div className="pcard__sub cell-truncate" style={{ maxWidth: "100%" }}>
                  <Highlight text={property.address ?? property.area_name ?? "—"} query={query} />
                </div>
              </div>
              <ReviewBadge property={property} />
            </div>

            <div className="pcard__price" title={property.price_text ?? undefined}>
              {formatPrice(property.price_text, property.price_amount_inr)}
            </div>
            {(property.price_per_unit_text !== null || property.price_per_unit_amount_inr !== null) && (
              <div className="faint small" title={property.price_per_unit_text ?? undefined}>
                {formatPricePerUnit(property.price_per_unit_text, property.price_per_unit_amount_inr)} / unit
              </div>
            )}

            <div className="pcard__facts">
              {property.bhk && (
                <span className="fact">
                  <IconBuilding size={12} />
                  {property.bhk}
                </span>
              )}
              {property.property_type && (
                <span className="fact">
                  <IconTag size={12} />
                  {property.property_type}
                </span>
              )}
              <span className="fact">
                <IconTag size={12} />
                {property.listing_type}
              </span>
              {property.carpet_area_sqft !== null && (
                <span className="fact">
                  <IconRuler size={12} />
                  {formatCarpetArea(property.carpet_area_sqft, property.carpet_area_unit)}
                </span>
              )}
              {property.area_name && (
                <span className="fact">
                  <IconPin size={12} />
                  <Highlight text={property.area_name} query={query} />
                </span>
              )}
            </div>

            {property.contact_phone && (
              <div className="fact" style={{ alignSelf: "flex-start" }}>
                <IconPhone size={12} />
                <Copyable text={property.contact_phone}>
                  {property.contact_name ? `${property.contact_name} · ${property.contact_phone}` : property.contact_phone}
                </Copyable>
              </div>
            )}

            <div className="pcard__foot">
              <span className="cell-truncate" title={sourceDetail(property)}>
                <IconUsers size={11} /> {sourceLabel(property)} · {property.formatted_timestamp}
              </span>
              <div onClick={(event) => event.stopPropagation()}>
                <RowActions property={property} viewTab={viewTab} onAccept={onAccept} onMove={onMove} onDelete={onDelete} />
              </div>
            </div>
          </Panel>
        );
      })}
    </div>
  );
}

/* ---------------------------------------------------------------- actions */

/** Delete and Move-to are available on every property in every view; Accept
 *  only makes sense while looking at the Needs review queue. Shared between
 *  the table's action cell and the card's action row so the icon set and
 *  behaviour never drift apart between layouts. */
function RowActions({
  property,
  viewTab,
  onAccept,
  onMove,
  onDelete,
}: {
  property: PropertyRecord;
  viewTab: ViewTab;
  onAccept: (property: PropertyRecord) => void;
  onMove: (property: PropertyRecord) => void;
  onDelete: (property: PropertyRecord) => void;
}) {
  const movesTo = property.review_status === "outsider" ? "Main" : "Outsider";
  return (
    <div className="row-actions">
      {viewTab === "needsReview" && (
        <button
          type="button"
          className="row-actions__btn row-actions__btn--accept"
          title="Accept"
          aria-label="Accept this property"
          onClick={() => onAccept(property)}
        >
          <IconCheck size={15} />
        </button>
      )}
      <button
        type="button"
        className="row-actions__btn"
        title={`Move to ${movesTo}`}
        aria-label={`Move to ${movesTo}`}
        onClick={() => onMove(property)}
      >
        <IconMove size={15} />
      </button>
      <button
        type="button"
        className="row-actions__btn row-actions__btn--danger"
        title="Delete"
        aria-label="Delete this property"
        onClick={() => onDelete(property)}
      >
        <IconTrash size={15} />
      </button>
    </div>
  );
}

/* ----------------------------------------------------------------- detail */

/**
 * The full-detail dialog opened by clicking any row or card, in any of the
 * three views. Everything the table/card layouts show only a slice of
 * (facts, price, contact, source, sender, description, the original
 * message) is shown here at once — the point of a dedicated dialog is that
 * nothing about the property is left behind the click, since the table/card
 * behind it is now covered rather than expanded in place. The same Accept /
 * Move / Delete actions available inline are repeated here too, so acting
 * on a property never requires closing the dialog first to reach them.
 */
function PropertyDetailDialog({
  property,
  viewTab,
  onAccept,
  onMove,
  onDelete,
  onClose,
}: {
  property: PropertyRecord;
  viewTab: ViewTab;
  onAccept: (property: PropertyRecord) => void;
  onMove: (property: PropertyRecord) => void;
  onDelete: (property: PropertyRecord) => void;
  onClose: () => void;
}) {
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

  const movesTo = property.review_status === "outsider" ? "Main" : "Outsider";
  const subtitle = [property.area_name, property.address].filter(Boolean).join(" · ");

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="Property details">
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">
              {property.property_type ?? "Property"} · {property.listing_type}
            </div>
            <h2 className="detail-modal__title cell-truncate">
              {property.society_name ?? property.area_name ?? "Unnamed property"}
            </h2>
            {subtitle && <div className="detail-modal__sub cell-truncate">{subtitle}</div>}
            <div className="detail-modal__badges">
              <ReviewBadge property={property} />
              {property.bhk && (
                <span className="fact">
                  <IconBuilding size={12} />
                  {property.bhk}
                </span>
              )}
              {property.carpet_area_sqft !== null && (
                <span className="fact">
                  <IconRuler size={12} />
                  {formatCarpetArea(property.carpet_area_sqft, property.carpet_area_unit)}
                </span>
              )}
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          {property.needs_review && property.review_notes && (
            <Note tone="warn" icon={<IconAlert size={16} />}>
              <strong>Flagged for review:</strong> {property.review_notes}
            </Note>
          )}

          {property.review_status === "outsider" && (
            <Note tone="info" icon={<IconPin size={16} />}>
              <strong>Outsider:</strong> {property.review_notes ?? "Outside the client's selected areas."}
            </Note>
          )}

          <div className="detail__grid">
            <div className="detail__block">
              <div className="detail__k">Price as written</div>
              <div className="detail__v">{property.price_text ?? "—"}</div>
              {property.price_amount_inr !== null && (
                <div className="faint small" style={{ marginTop: 4 }}>
                  Read as {formatPrice(null, property.price_amount_inr)}
                </div>
              )}
            </div>

            <div className="detail__block">
              <div className="detail__k">Price per unit</div>
              <div className="detail__v">
                {property.price_per_unit_text ?? formatPricePerUnit(null, property.price_per_unit_amount_inr)}
              </div>
              {property.price_per_unit_amount_inr !== null && (
                <div className="faint small" style={{ marginTop: 4 }}>
                  Read as {formatPricePerUnit(null, property.price_per_unit_amount_inr)}
                </div>
              )}
            </div>

            <div className="detail__block">
              <div className="detail__k">Contact</div>
              <div className="detail__v">{property.contact_name ?? "—"}</div>
              {property.contact_phone && (
                <div className="detail__v" style={{ marginTop: 4 }}>
                  <Copyable text={property.contact_phone} />
                </div>
              )}
            </div>

            <div className="detail__block">
              <div className="detail__k">Sender</div>
              <div className="detail__v">
                {property.sender_name}
                {property.sender_saved_name && property.sender_saved_name !== property.sender_name && (
                  <span className="faint"> · saved as {property.sender_saved_name}</span>
                )}
              </div>
              <div className="detail__v" style={{ marginTop: 4 }}>
                <Copyable text={property.sender_phone} />
              </div>
            </div>

            <div className="detail__block">
              <div className="detail__k">Source</div>
              <div className="detail__v">{sourceLabel(property)}</div>
              <div className="faint small" style={{ marginTop: 4 }}>
                {sourceDetail(property)} · {property.formatted_timestamp}
              </div>
            </div>
          </div>

          {property.description && (
            <div className="detail__block">
              <div className="detail__k">Description</div>
              <div className="detail__v">{property.description}</div>
            </div>
          )}

          <div className="detail__block">
            <div className="detail__k">
              <IconMessage size={11} /> Original message
            </div>
            <div className="detail__msg">{property.message_text}</div>
          </div>
        </div>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <span className="row-flex" style={{ marginLeft: "auto", gap: 10 }}>
            {viewTab === "needsReview" && (
              <Button icon={<IconCheck size={14} />} onClick={() => onAccept(property)}>
                Accept
              </Button>
            )}
            <Button variant="ghost" icon={<IconMove size={14} />} onClick={() => onMove(property)}>
              Move to {movesTo}
            </Button>
            <Button className="btn--danger" icon={<IconTrash size={14} />} onClick={() => onDelete(property)}>
              Delete
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Shows nothing for a plain, reviewed Main property — that's the default,
 *  unmarked state of the tab you're already looking at. Outsider and Needs
 *  review are each worth a badge because either can be true regardless of
 *  which tab a card is shown in (a card inside Needs review can belong to
 *  either Main or Outsider underneath, which the badge spells out). */
function ReviewBadge({ property }: { property: PropertyRecord }) {
  if (property.needs_review) {
    return (
      <Badge tone="warn" title={property.review_notes ?? undefined}>
        Needs review · {property.review_status === "outsider" ? "Outsider" : "Main"}
      </Badge>
    );
  }
  if (property.review_status === "outsider") {
    return (
      <Badge tone="info" title={property.review_notes ?? undefined}>
        Outsider
      </Badge>
    );
  }
  return null;
}

/** Nulls sort last in both directions; everything else compares naturally. */
function compareNullable(a: string | number | null, b: string | number | null, direction: number): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  if (typeof a === "number" && typeof b === "number") return (a - b) * direction;
  return String(a).localeCompare(String(b), "en-IN") * direction;
}
