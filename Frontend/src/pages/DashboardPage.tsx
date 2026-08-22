import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { propertyApi } from "../api/propertyApi";
import type { PropertyRecord } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { useDebounced, usePersistentState } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import { formatCarpetArea, formatPrice, relativeTime } from "../lib/formatters";
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
  IconGrid,
  IconInbox,
  IconList,
  IconMessage,
  IconPhone,
  IconPin,
  IconRefresh,
  IconRuler,
  IconSearch,
  IconTag,
  IconUsers,
  IconX,
} from "../components/ui/Icons";

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
type SortKey = "time" | "price" | "area" | "society" | "locality";
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
  { key: "status", label: "Status", filterKey: "status" },
  { key: "society", label: "Society", sort: "society" },
  { key: "locality", label: "Area", sort: "locality", filterKey: "locality" },
  { key: "address", label: "Address" },
  { key: "bhk", label: "BHK", filterKey: "bhk" },
  { key: "type", label: "Type", filterKey: "type" },
  { key: "carpet", label: "Carpet area", sort: "area", numeric: true, filterKey: "carpet" },
  { key: "price", label: "Price", sort: "price", numeric: true, filterKey: "price" },
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
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [page, setPage] = useState(1);

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

        const incoming = new Set(data.map((p) => p.source_message_id));
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

  const needsReviewCount = useMemo(
    () => allProperties.filter((p) => p.review_status === "needs_review").length,
    [allProperties],
  );

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
    const filtered = allProperties.filter((property) => {
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
  }, [allProperties, query, passesFilters, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(visibleProperties.length / PAGE_SIZE));
  const pageItems = useMemo(
    () => visibleProperties.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
    [visibleProperties, page],
  );

  // Any change that narrows the list invalidates the page you were on —
  // page 7 of a 3-page result is a blank screen that reads as a bug.
  useEffect(() => setPage(1), [query, filters, sortKey, sortDir]);
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

  /* The Status segmented control and the Status column dialog are two views
     of one filter, not two competing ones — with only two possible values,
     "both selected" and "no filter" mean the same thing, so they map onto
     each other exactly. */
  const statusFilter = filters.status;
  const statusSegment: "all" | "accepted" | "needs_review" =
    statusFilter?.kind === "values" && statusFilter.selected.length === 1
      ? statusFilter.selected[0] === "Needs review"
        ? "needs_review"
        : "accepted"
      : "all";

  function setStatusSegment(value: "all" | "accepted" | "needs_review") {
    if (value === "all") return setColumnFilter("status", undefined);
    setColumnFilter("status", {
      kind: "values",
      selected: [value === "needs_review" ? "Needs review" : "Accepted"],
    });
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

        <Segmented<"all" | "accepted" | "needs_review">
          ariaLabel="Filter by review status"
          value={statusSegment}
          onChange={setStatusSegment}
          options={[
            { value: "all", label: "All" },
            { value: "accepted", label: "Accepted" },
            { value: "needs_review", label: `Review${needsReviewCount ? ` (${needsReviewCount})` : ""}` },
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
            body="Properties appear here automatically once a monitored chat receives a message that mentions one of your area keywords. Check the Connection page to confirm something is being watched."
          />
        </Panel>
      )}

      {allProperties.length > 0 && visibleProperties.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconSearch size={36} />}
            title="No matches"
            body={`None of the ${allProperties.length} stored properties match the current search and filters.`}
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
              expandedId={expandedId}
              setExpandedId={setExpandedId}
              sortKey={sortKey}
              sortDir={sortDir}
              toggleSort={toggleSort}
              freshIds={freshIds}
              filters={filters}
              openFilterKey={openFilter?.key ?? null}
              onOpenFilter={(key, anchor) => setOpenFilter(openFilter?.key === key ? null : { key, anchor })}
            />
          ) : (
            <PropertyCards
              properties={pageItems}
              query={query}
              expandedId={expandedId}
              setExpandedId={setExpandedId}
              freshIds={freshIds}
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
    </div>
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
  expandedId: string | null;
  setExpandedId: (id: string | null) => void;
  freshIds: Set<string>;
}

function PropertyTable({
  properties,
  query,
  expandedId,
  setExpandedId,
  sortKey,
  sortDir,
  toggleSort,
  freshIds,
  filters,
  openFilterKey,
  onOpenFilter,
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
            </tr>
          </thead>
          <tbody>
            {properties.map((property) => {
              const isExpanded = expandedId === property.source_message_id;
              const flagged = property.review_status === "needs_review";
              return (
                <Fragment key={property.source_message_id}>
                  <tr
                    className={[
                      "row",
                      isExpanded && "row--open",
                      flagged && "row--flagged",
                      freshIds.has(property.source_message_id) && "row--new",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    // Rows are reachable and openable from the keyboard, not
                    // just by clicking — the detail panel holds the original
                    // message, which is the whole point of an audit trail.
                    tabIndex={0}
                    role="button"
                    aria-expanded={isExpanded}
                    onClick={() => setExpandedId(isExpanded ? null : property.source_message_id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setExpandedId(isExpanded ? null : property.source_message_id);
                      }
                    }}
                  >
                    <td>
                      <ReviewBadge status={property.review_status} notes={property.review_notes} />
                    </td>
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
                    <td className="cell-num" style={{ textAlign: "right" }}>
                      {formatCarpetArea(property.carpet_area_sqft)}
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
                  </tr>
                  {isExpanded && (
                    <tr>
                      <td className="detail-cell" colSpan={COLUMNS.length}>
                        <PropertyDetail property={property} />
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

/* ------------------------------------------------------------------ cards */

function PropertyCards({ properties, query, expandedId, setExpandedId, freshIds }: ListProps) {
  return (
    <div className="card-grid">
      {properties.map((property, index) => {
        const isExpanded = expandedId === property.source_message_id;
        return (
          <Panel
            key={property.source_message_id}
            interactive
            selected={isExpanded}
            pad={false}
            delay={Math.min(index * 35, 420)}
            className={`pcard${freshIds.has(property.source_message_id) ? " anim-pop" : ""}`}
            onClick={() => setExpandedId(isExpanded ? null : property.source_message_id)}
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
              <ReviewBadge status={property.review_status} notes={property.review_notes} />
            </div>

            <div className="pcard__price" title={property.price_text ?? undefined}>
              {formatPrice(property.price_text, property.price_amount_inr)}
            </div>

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
              {property.carpet_area_sqft !== null && (
                <span className="fact">
                  <IconRuler size={12} />
                  {formatCarpetArea(property.carpet_area_sqft)}
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
              <span className="cell-truncate" title={sourceDetail(property)} style={{ maxWidth: "60%" }}>
                <IconUsers size={11} /> {sourceLabel(property)}
              </span>
              <span>{property.formatted_timestamp}</span>
            </div>

            {isExpanded && <PropertyDetail property={property} embedded />}
          </Panel>
        );
      })}
    </div>
  );
}

/* ----------------------------------------------------------------- detail */

function PropertyDetail({ property, embedded = false }: { property: PropertyRecord; embedded?: boolean }) {
  return (
    <div className="detail" style={embedded ? { padding: "14px 0 0", borderBottom: 0 } : undefined}>
      {property.review_status === "needs_review" && property.review_notes && (
        <Note tone="warn" icon={<IconAlert size={16} />}>
          <strong>Flagged for review:</strong> {property.review_notes}
        </Note>
      )}

      <div className="detail__grid">
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

        {property.price_text && (
          <div className="detail__block">
            <div className="detail__k">Price as written</div>
            <div className="detail__v">{property.price_text}</div>
            {property.price_amount_inr !== null && (
              <div className="faint small" style={{ marginTop: 4 }}>
                Read as {formatPrice(null, property.price_amount_inr)}
              </div>
            )}
          </div>
        )}

        {property.contact_phone && (
          <div className="detail__block">
            <div className="detail__k">Contact</div>
            <div className="detail__v">{property.contact_name ?? "—"}</div>
            <div className="detail__v" style={{ marginTop: 4 }}>
              <Copyable text={property.contact_phone} />
            </div>
          </div>
        )}
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
  );
}

function ReviewBadge({ status, notes }: { status: PropertyRecord["review_status"]; notes: string | null }) {
  const flagged = status === "needs_review";
  return (
    <Badge tone={flagged ? "warn" : "ok"} title={notes ?? undefined}>
      {flagged ? "Needs review" : "Accepted"}
    </Badge>
  );
}

/** Nulls sort last in both directions; everything else compares naturally. */
function compareNullable(a: string | number | null, b: string | number | null, direction: number): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  if (typeof a === "number" && typeof b === "number") return (a - b) * direction;
  return String(a).localeCompare(String(b), "en-IN") * direction;
}
