import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { BUILDER_PROJECT_LIST_LIMIT, builderProjectApi } from "../api/builderProjectApi";
import type { BuilderProjectRecord } from "../api/types";
import { useAppStatus } from "../state/StatusProvider";
import { useDebounced, usePersistentState, useSearchShortcut } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import { formatArea, formatPrice, relativeTime } from "../lib/formatters";
import {
  compileFilters,
  countActiveFilters,
  describeFilter,
  isFilterActive,
  type ColumnFilter,
  type FilterState,
} from "../lib/propertyFilters";
import { BUILDER_PROJECT_FILTER_DEF_BY_KEY, BUILDER_PROJECT_FILTER_DEFS } from "../lib/builderProjectFilters";
import { getCachedBuilderProjectList, setCachedBuilderProjectList } from "../lib/builderProjectListCache";
import { useToast } from "../components/ui/Toast";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import FilterPopover, { type SortControl } from "../components/ui/FilterPopover";
import StickyTableHead from "../components/ui/StickyTableHead";
import RowRail from "../components/ui/RowRail";
import PropertyFormDialog, { type ContentFormApi } from "../components/PropertyFormDialog";
import { ContactPhoneDetails, ContactPhoneSummary } from "../components/ui/ContactPhones";
import { phoneList, phoneSearchText } from "../lib/phone";
import {
  FilterTrigger,
  Pager,
  PropertyIndicators,
  SORT_LABELS,
  compareNullable,
  type SortDir,
  type SortKey,
} from "./DashboardPage";
import {
  Badge,
  Button,
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
  IconImage,
  IconInstagram,
  IconLayers,
  IconList,
  IconPhone,
  IconPin,
  IconPlus,
  IconRefresh,
  IconRuler,
  IconSearch,
  IconTag,
  IconTrash,
  IconX,
} from "../components/ui/Icons";

/**
 * Builder Projects — properties a person adds BY HAND with the Add button,
 * never captured from WhatsApp.
 *
 * Built to read exactly like the Properties page: the same fields under the
 * same names, the same click-a-heading column filters (lib/
 * builderProjectFilters.ts, on the Properties page's own FilterPopover), the
 * same table/cards layouts, search, Sale/Rent capsule, "Has reel" lens,
 * paging and detail dialog, and the very same Add/Edit dialog
 * (components/PropertyFormDialog.tsx, pointed at this page's own endpoints).
 *
 * What it deliberately leaves out is everything tied to the WhatsApp
 * intake: no Main/Outsider split, no Needs review queue, no Sold out tab, no
 * Source column and no original message — a project that was typed in has
 * none of those. And it is kept apart from the property database on purpose
 * (see Backend/Database/builder_project_models.py), so a builder project
 * never shows up on the Properties page, the Landing Page or the public
 * site. It IS matched against client inquiries and broker requirements,
 * alongside properties — the match dialogs label every card "Property" or
 * "Builder project" so the two are never confused.
 */

// The same number the match dialogs ask for, so the page and the dialogs
// share one browser-cached copy of the list (see BUILDER_PROJECT_LIST_LIMIT).
const FETCH_LIMIT = BUILDER_PROJECT_LIST_LIMIT;
const PAGE_SIZE = 20;

type ViewMode = "table" | "cards";

interface Column {
  key: string;
  label: string;
  sort?: SortKey;
  numeric?: boolean;
  /** Set when this column has a filter dialog — the sort, if any, then lives
   *  inside that dialog, exactly as on the Properties page. */
  filterKey?: string;
}

/** The Properties page's own columns, minus Source (a builder project has no
 *  chat it came from), with the timestamp reading as when it was added. */
const COLUMNS: Column[] = [
  { key: "society", label: "Society / Building", sort: "society" },
  { key: "unitNo", label: "Unit / Flat no." },
  { key: "locality", label: "Area", sort: "locality", filterKey: "locality" },
  { key: "address", label: "Address" },
  { key: "bhk", label: "BHK", filterKey: "bhk" },
  { key: "type", label: "Type", filterKey: "type" },
  { key: "listingType", label: "Sale/Rent", filterKey: "listingType" },
  { key: "areaSqft", label: "Area (sqft)", sort: "areaSqft", numeric: true, filterKey: "areaSqft" },
  { key: "areaVaar", label: "Area (vaar)", sort: "areaVaar", numeric: true, filterKey: "areaVaar" },
  { key: "superBuilt", label: "Super built" },
  { key: "furnishing", label: "Furnishing", filterKey: "furnishing" },
  { key: "price", label: "Price", sort: "price", numeric: true, filterKey: "price" },
  { key: "contact", label: "Contact" },
  { key: "time", label: "Added (IST)", sort: "time" },
];

const BUILDER_PROJECT_FORM_API: ContentFormApi<BuilderProjectRecord> = {
  create: builderProjectApi.createBuilderProject,
  update: builderProjectApi.updateBuilderProject,
  getImages: builderProjectApi.getBuilderProjectImages,
};

function projectTitle(project: BuilderProjectRecord): string {
  return project.society_name ?? project.area_name ?? "Unnamed project";
}

/** Sorted as a number, not as the ISO string — a timestamp's text only
 *  compares correctly while every value shares one offset. */
function addedAt(project: BuilderProjectRecord): number | null {
  return project.created_at ? Date.parse(project.created_at) : null;
}

/* ----------------------------------------------------------------- page */

export default function BuilderProjectsPage() {
  const toast = useToast();
  // Seeded from the shared cache so a revisit paints at once rather than
  // from a skeleton; the mount effect below still confirms it.
  const [projects, setProjects] = useState<BuilderProjectRecord[] | null>(
    () => getCachedBuilderProjectList()?.data ?? null,
  );
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(() => {
    const cached = getCachedBuilderProjectList();
    return cached ? new Date(cached.fetchedAt) : null;
  });
  const [refreshing, setRefreshing] = useState(false);

  const [search, setSearch] = useState("");
  const query = useDebounced(search, 180);
  const [filters, setFilters] = useState<FilterState>({});
  const [reelOnly, setReelOnly] = useState(false);
  const activeFilterCount = countActiveFilters(filters);
  const filtersActive = search.trim().length > 0 || activeFilterCount > 0 || reelOnly;
  const [openFilter, setOpenFilter] = useState<{ key: string; anchor: HTMLElement } | null>(null);
  // Remembered like the Properties page's own table/cards choice.
  const [view, setView] = usePersistentState<ViewMode>("builderProjects.view", "table");
  const [sortKey, setSortKey] = useState<SortKey>("time");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [page, setPage] = useState(1);

  const [detailId, setDetailId] = useState<string | null>(null);
  const [formDialog, setFormDialog] = useState<{ mode: "add" | "edit"; project?: BuilderProjectRecord } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<BuilderProjectRecord | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const searchRef = useRef<HTMLInputElement>(null);
  const listTopRef = useRef<HTMLDivElement>(null);
  const firstPaint = useRef(true);
  // Which records existed at the previous load — anything new gets a brief
  // highlight so an arrival (from another tab, say) is noticeable.
  const seenIds = useRef<Set<string> | null>(null);
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());

  const { status: appStatus } = useAppStatus();

  /** Every local change goes through here, so the shared cache can never
   *  hand a later visit a copy already known to be stale. Setting the cache
   *  inside the updater is safe: it is idempotent, so React replaying the
   *  updater changes nothing. */
  const applyProjects = useCallback((update: (prev: BuilderProjectRecord[]) => BuilderProjectRecord[]) => {
    setProjects((prev) => {
      const next = update(prev ?? []);
      setCachedBuilderProjectList(next);
      return next;
    });
  }, []);

  const flashFresh = useCallback((ids: Set<string>) => {
    if (ids.size === 0) return;
    setFreshIds(ids);
    window.setTimeout(() => setFreshIds(new Set()), 2600);
  }, []);

  const load = useCallback(
    async (manual = false) => {
      setRefreshing(true);
      try {
        const data = await builderProjectApi.getBuilderProjects(FETCH_LIMIT);
        setProjects(data);
        setCachedBuilderProjectList(data);
        setLastUpdated(new Date());
        setError(null);

        const incoming = new Set(data.map((p) => p.record_id));
        if (seenIds.current) flashFresh(new Set([...incoming].filter((id) => !seenIds.current!.has(id))));
        seenIds.current = incoming;
        if (manual) toast.push({ tone: "ok", title: "Refreshed", message: `${data.length} builder projects loaded.` });
      } catch (err) {
        const message = friendlyError(err);
        setError(message);
        if (manual) toast.push({ tone: "bad", title: "Refresh failed", message });
      } finally {
        setRefreshing(false);
      }
    },
    [toast, flashFresh],
  );

  // On mount: always ask, cache hit or not. It is a conditional request —
  // the backend answers a bodyless 304 from memory whenever the browser's
  // copy is still current — so confirming the cached paint costs almost
  // nothing, and a list changed from another tab never goes unnoticed.
  useEffect(() => {
    const cached = getCachedBuilderProjectList();
    if (cached) seenIds.current = new Set(cached.data.map((p) => p.record_id));
    void load(false);
    // Mount-only — the version-watch effect below drives every later load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Change-driven, not interval-driven: the shared status poll already runs
  // on every page and carries a token that moves only when a project is
  // actually added, edited or deleted (see the backend's
  // builder_projects_version), so this page never polls on its own.
  const lastVersion = useRef<string | null>(null);
  useEffect(() => {
    const version = appStatus?.builder_projects_version;
    if (version === undefined) return;
    if (lastVersion.current === null) {
      lastVersion.current = version;
      return;
    }
    if (lastVersion.current === version) return;
    lastVersion.current = version;
    void load(false);
  }, [appStatus?.builder_projects_version, load]);

  // "/" jumps to search from anywhere on the page, same as Properties.
  useSearchShortcut(searchRef);

  const allProjects = useMemo(() => projects ?? [], [projects]);

  // Derived from the live list rather than snapshotted at open time — a
  // refresh keeps the open dialog current, and a delete closes it for free.
  const detailProject = useMemo(
    () => (detailId ? (allProjects.find((p) => p.record_id === detailId) ?? null) : null),
    [detailId, allProjects],
  );

  const withPhotos = useMemo(() => allProjects.filter((p) => p.image_count > 0).length, [allProjects]);
  const localities = useMemo(() => {
    // Case-folded, matching how the Area filter groups its options.
    const set = new Set<string>();
    allProjects.forEach((p) => p.area_name?.trim() && set.add(p.area_name.trim().toLowerCase()));
    return set.size;
  }, [allProjects]);

  const passesFilters = useMemo(
    () => compileFilters<BuilderProjectRecord>(filters, BUILDER_PROJECT_FILTER_DEFS),
    [filters],
  );

  const visibleProjects = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = allProjects.filter((project) => {
      if (!passesFilters(project)) return false;
      if (reelOnly && !project.instagram_reel_url) return false;
      if (!needle) return true;
      const haystack = [
        project.society_name,
        project.area_name,
        project.address,
        project.contact_name,
        phoneSearchText(project),
        project.description,
        project.bhk,
        project.property_type,
        project.super_built,
        project.price_text,
        project.unit_no,
        project.furnishing,
        project.extra_notes,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(needle);
    });

    const direction = sortDir === "asc" ? 1 : -1;
    // Records missing the sorted field sink to the bottom either way — see
    // compareNullable.
    return [...filtered].sort((a, b) => {
      switch (sortKey) {
        case "price":
          return compareNullable(a.price_amount_inr, b.price_amount_inr, direction);
        case "areaSqft":
          return compareNullable(a.area_sqft, b.area_sqft, direction);
        case "areaVaar":
          return compareNullable(a.area_vaar, b.area_vaar, direction);
        case "society":
          return compareNullable(a.society_name, b.society_name, direction);
        case "locality":
          return compareNullable(a.area_name, b.area_name, direction);
        case "time":
        default:
          return compareNullable(addedAt(a), addedAt(b), direction);
      }
    });
  }, [allProjects, query, passesFilters, reelOnly, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(visibleProjects.length / PAGE_SIZE));
  const pageItems = useMemo(
    () => visibleProjects.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
    [visibleProjects, page],
  );

  // Any change that narrows the list invalidates the page you were on.
  useEffect(() => setPage(1), [query, filters, sortKey, sortDir, reelOnly]);
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
      applySort(key, key === "society" || key === "locality" ? "asc" : "desc");
    }
  }

  function resetAll() {
    setSearch("");
    setFilters({});
    setReelOnly(false);
  }

  /* The Sale/Rent capsule and the Sale/Rent column dialog are two views of
     one filter — the same pattern the Properties page uses. */
  const listingTypeFilter = filters.listingType;
  const listingTypeSegment: "all" | "Sale" | "Rent" =
    listingTypeFilter?.kind === "values" && listingTypeFilter.selected.length === 1
      ? (listingTypeFilter.selected[0] as "Sale" | "Rent")
      : "all";

  function setListingTypeSegment(value: "all" | "Sale" | "Rent") {
    if (value === "all") return setColumnFilter("listingType", undefined);
    setColumnFilter("listingType", { kind: "values", selected: [value] });
  }

  function handleSaved(saved: BuilderProjectRecord, mode: "add" | "edit") {
    if (mode === "add") {
      applyProjects((prev) => [saved, ...prev.filter((p) => p.record_id !== saved.record_id)]);
      seenIds.current?.add(saved.record_id);
      flashFresh(new Set([saved.record_id]));
    } else {
      applyProjects((prev) => prev.map((p) => (p.record_id === saved.record_id ? saved : p)));
    }
    setFormDialog(null);
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    const recordId = deleteTarget.record_id;
    setDeleteBusy(true);
    try {
      await builderProjectApi.deleteBuilderProject(recordId);
      applyProjects((prev) => prev.filter((p) => p.record_id !== recordId));
      seenIds.current?.delete(recordId);
      toast.push({ tone: "ok", title: "Deleted", message: "Builder project removed permanently." });
      setDeleteTarget(null);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't delete this builder project", message: friendlyError(err) });
    } finally {
      setDeleteBusy(false);
    }
  }

  const loading = projects === null && error === null;

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
  const openFilterDef = openFilter ? BUILDER_PROJECT_FILTER_DEF_BY_KEY[openFilter.key] : undefined;

  return (
    <div className="stack stack-5">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Supply — added by hand</div>
          <h1 className="page-title">Builder Projects</h1>
          <p className="section-head__sub">
            Projects you add yourself with the Add button — never captured from WhatsApp. The same fields as a
            property, and the same filters: click any column heading to filter by the values seen so far.
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
          <Button variant="primary" icon={<IconPlus size={15} />} onClick={() => setFormDialog({ mode: "add" })}>
            Add builder project
          </Button>
        </div>
      </header>

      {allProjects.length > 0 && (
        <div className="stat-grid">
          <Stat label="Stored" value={allProjects.length} icon={<IconLayers size={13} />} delay={0} />
          <Stat label="Showing" value={visibleProjects.length} icon={<IconSearch size={13} />} tone="accent" delay={60} />
          <Stat label="With photos" value={withPhotos} icon={<IconImage size={13} />} delay={120} />
          <Stat label="Localities" value={localities} icon={<IconPin size={13} />} delay={180} />
        </div>
      )}

      <div className="toolbar">
        <div className="toolbar__grow">
          <SearchInput
            inputRef={searchRef}
            value={search}
            onChange={setSearch}
            placeholder="Search society, area, address, contact…"
            shortcutHint="/"
            ariaLabel="Search builder projects"
          />
        </div>

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

        <Button
          size="sm"
          variant={reelOnly ? "primary" : "ghost"}
          icon={<IconInstagram size={14} />}
          onClick={() => setReelOnly((prev) => !prev)}
          title="Show only builder projects with an Instagram reel link"
          aria-pressed={reelOnly}
        >
          Has reel
        </Button>

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
          {BUILDER_PROJECT_FILTER_DEFS.filter((def) => isFilterActive(filters[def.key])).map((def) => (
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
      {view === "cards" && allProjects.length > 0 && (
        <div className="filter-strip">
          <span className="faint small">Filter by</span>
          {BUILDER_PROJECT_FILTER_DEFS.map((def) => (
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
              <span className="spinner" /> Loading builder projects…
            </div>
            <SkeletonRows rows={6} />
          </div>
        </Panel>
      )}

      {projects !== null && allProjects.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconBuilding size={38} />}
            title="No builder projects yet"
            body="Builder projects are added by hand, never captured from WhatsApp. Press Add builder project to create the first one — every field is optional."
            action={
              <Button variant="primary" icon={<IconPlus size={15} />} onClick={() => setFormDialog({ mode: "add" })}>
                Add builder project
              </Button>
            }
          />
        </Panel>
      )}

      {allProjects.length > 0 && visibleProjects.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconSearch size={36} />}
            title="No matches"
            body={`None of the ${allProjects.length} builder projects match the current search and filters.`}
            action={<Button onClick={resetAll}>Clear everything</Button>}
          />
        </Panel>
      )}

      {visibleProjects.length > 0 && (
        <>
          <div ref={listTopRef} className="list-anchor" />
          {view === "table" ? (
            <BuilderProjectTable
              projects={pageItems}
              query={query}
              sortKey={sortKey}
              sortDir={sortDir}
              toggleSort={toggleSort}
              filters={filters}
              openFilterKey={openFilter?.key ?? null}
              onOpenFilter={(key, anchor) => setOpenFilter(openFilter?.key === key ? null : { key, anchor })}
              freshIds={freshIds}
              onOpenDetail={(project) => setDetailId(project.record_id)}
              onEdit={(project) => setFormDialog({ mode: "edit", project })}
              onDelete={setDeleteTarget}
            />
          ) : (
            <BuilderProjectCards
              projects={pageItems}
              query={query}
              freshIds={freshIds}
              onOpenDetail={(project) => setDetailId(project.record_id)}
              onEdit={(project) => setFormDialog({ mode: "edit", project })}
              onDelete={setDeleteTarget}
            />
          )}
          <Pager page={page} pageCount={pageCount} total={visibleProjects.length} onChange={setPage} />
        </>
      )}

      {openFilter && openFilterDef && (
        <FilterPopover<BuilderProjectRecord>
          def={openFilterDef}
          anchorEl={openFilter.anchor}
          properties={allProjects}
          filter={filters[openFilter.key]}
          onChange={(next) => setColumnFilter(openFilter.key, next)}
          onClose={() => setOpenFilter(null)}
          sort={openColumn ? sortControlFor(openColumn) : undefined}
        />
      )}

      {detailProject && (
        <BuilderProjectDetailDialog
          project={detailProject}
          onClose={() => setDetailId(null)}
          onEdit={(project) => setFormDialog({ mode: "edit", project })}
          onDelete={setDeleteTarget}
        />
      )}

      {formDialog && (
        <PropertyFormDialog<BuilderProjectRecord>
          mode={formDialog.mode}
          property={formDialog.project}
          api={BUILDER_PROJECT_FORM_API}
          noun="builder project"
          onClose={() => setFormDialog(null)}
          onSaved={handleSaved}
        />
      )}

      {deleteTarget && (
        <ConfirmDialog
          title="Delete this builder project?"
          tone="danger"
          confirmLabel="Delete forever"
          busy={deleteBusy}
          onClose={() => !deleteBusy && setDeleteTarget(null)}
          onConfirm={confirmDelete}
          body={
            <>
              <p>
                <strong>{projectTitle(deleteTarget)}</strong>
                {deleteTarget.area_name && deleteTarget.society_name ? ` · ${deleteTarget.area_name}` : ""}
              </p>
              <p className="faint small">
                {[deleteTarget.bhk, deleteTarget.property_type, deleteTarget.address].filter(Boolean).join(" · ") ||
                  "No further details"}
              </p>
              <p className="faint small">{formatPrice(deleteTarget.price_text, deleteTarget.price_amount_inr)}</p>
              <p style={{ marginTop: 12 }}>
                This removes it, photos included, <strong>permanently</strong> — it cannot be undone.
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
  projects: BuilderProjectRecord[];
  query: string;
  freshIds: Set<string>;
  onOpenDetail: (project: BuilderProjectRecord) => void;
  onEdit: (project: BuilderProjectRecord) => void;
  onDelete: (project: BuilderProjectRecord) => void;
}

/** Edit and Delete, in the Properties page's own arrangement so the two
 *  pages' action columns line up. Shared between the table cell and the card
 *  footer so the two layouts can never drift apart. */
function RowActions({
  project,
  onEdit,
  onDelete,
}: {
  project: BuilderProjectRecord;
  onEdit: (project: BuilderProjectRecord) => void;
  onDelete: (project: BuilderProjectRecord) => void;
}) {
  return (
    <div className="row-actions">
      <button
        type="button"
        className="row-actions__btn"
        title="Edit"
        aria-label="Edit this builder project"
        onClick={() => onEdit(project)}
      >
        <IconEdit size={15} />
      </button>
      <button
        type="button"
        className="row-actions__btn row-actions__btn--danger"
        title="Delete"
        aria-label="Delete this builder project"
        onClick={() => onDelete(project)}
      >
        <IconTrash size={15} />
      </button>
    </div>
  );
}

/* ----------------------------------------------------------------- table */

function BuilderProjectTable({
  projects,
  query,
  sortKey,
  sortDir,
  toggleSort,
  filters,
  openFilterKey,
  onOpenFilter,
  freshIds,
  onOpenDetail,
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
  const tableWrapRef = useRef<HTMLDivElement>(null);
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
    <div className="table-with-rail" ref={tableWrapRef}>
      {/* The same photo/reel chips the Properties table shows in its gutter. */}
      <RowRail containerRef={tableWrapRef} count={projects.length} className="anim-rise" ariaHidden>
        {(index) => {
          const project = projects[index];
          return project ? <PropertyIndicators property={project} /> : null;
        }}
      </RowRail>
      <div className="table-frame anim-rise">
        <StickyTableHead scrollRef={scrollRef}>{headerRow}</StickyTableHead>
        <div className="table-scroll" ref={scrollRef}>
          <table className="table">
            <thead>{headerRow}</thead>
            <tbody>
              {projects.map((project) => (
                <tr
                  key={project.record_id}
                  data-rail-row=""
                  className={`row${freshIds.has(project.record_id) ? " row--new" : ""}`}
                  tabIndex={0}
                  role="button"
                  onClick={() => onOpenDetail(project)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onOpenDetail(project);
                    }
                  }}
                >
                  <td className="cell-truncate cell-strong" title={project.society_name ?? undefined}>
                    <Highlight text={project.society_name ?? "—"} query={query} />
                  </td>
                  <td className="cell-truncate" title={project.unit_no ?? undefined}>
                    <Highlight text={project.unit_no ?? "—"} query={query} />
                  </td>
                  <td className="cell-truncate" title={project.area_name ?? undefined}>
                    <Highlight text={project.area_name ?? "—"} query={query} />
                  </td>
                  <td className="cell-truncate" title={project.address ?? undefined}>
                    <Highlight text={project.address ?? "—"} query={query} />
                  </td>
                  <td>{project.bhk ?? "—"}</td>
                  <td>{project.property_type ?? "—"}</td>
                  <td>
                    <Badge tone={project.listing_type === "Rent" ? "info" : "ok"}>{project.listing_type}</Badge>
                  </td>
                  <td className="cell-num" style={{ textAlign: "right" }}>
                    {project.area_sqft === null ? "—" : Math.round(project.area_sqft)}
                  </td>
                  <td className="cell-num" style={{ textAlign: "right" }}>
                    {project.area_vaar === null ? "—" : Math.round(project.area_vaar)}
                  </td>
                  <td className="cell-truncate" title={project.super_built ?? undefined}>
                    <Highlight text={project.super_built ?? "—"} query={query} />
                  </td>
                  <td className="cell-truncate">
                    <Highlight text={project.furnishing ?? "—"} query={query} />
                  </td>
                  <td
                    className="cell-num cell-strong"
                    style={{ textAlign: "right" }}
                    title={project.price_text ?? undefined}
                  >
                    {formatPrice(project.price_text, project.price_amount_inr)}
                  </td>
                  <td className="cell-truncate">
                    <Highlight text={project.contact_name ?? "—"} query={query} />
                    {phoneList(project).length > 0 && (
                      <div className="cell-muted">
                        <ContactPhoneSummary record={project} />
                      </div>
                    )}
                  </td>
                  <td className="cell-num" style={{ whiteSpace: "nowrap" }}>
                    {project.formatted_timestamp}
                  </td>
                  <td onClick={(event) => event.stopPropagation()} style={{ textAlign: "right" }}>
                    <RowActions project={project} onEdit={onEdit} onDelete={onDelete} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- cards */

function BuilderProjectCards({ projects, query, freshIds, onOpenDetail, onEdit, onDelete }: ListProps) {
  return (
    <div className="card-grid">
      {projects.map((project, index) => (
        <Panel
          key={project.record_id}
          interactive
          pad={false}
          delay={Math.min(index * 35, 420)}
          className={`pcard${freshIds.has(project.record_id) ? " anim-pop" : ""}`}
          onClick={() => onOpenDetail(project)}
        >
          <div className="pcard__top">
            <div style={{ minWidth: 0 }}>
              <div className="pcard__title cell-truncate" style={{ maxWidth: "100%" }}>
                <Highlight text={projectTitle(project)} query={query} />
              </div>
              <div className="pcard__sub cell-truncate" style={{ maxWidth: "100%" }}>
                <Highlight text={project.address ?? project.area_name ?? "—"} query={query} />
              </div>
            </div>
            <Badge tone={project.listing_type === "Rent" ? "info" : "ok"}>{project.listing_type}</Badge>
          </div>

          <div className="pcard__price" title={project.price_text ?? undefined}>
            {formatPrice(project.price_text, project.price_amount_inr)}
          </div>

          <div className="pcard__facts">
            {project.bhk && (
              <span className="fact">
                <IconBuilding size={12} />
                {project.bhk}
              </span>
            )}
            {project.property_type && (
              <span className="fact">
                <IconTag size={12} />
                {project.property_type}
              </span>
            )}
            {formatArea(project.area_sqft, project.area_vaar) !== "—" && (
              <span className="fact">
                <IconRuler size={12} />
                {formatArea(project.area_sqft, project.area_vaar)}
              </span>
            )}
            {project.super_built && (
              <span className="fact" title="Super built">
                <IconRuler size={12} />
                Super built {project.super_built}
              </span>
            )}
            {project.area_name && (
              <span className="fact">
                <IconPin size={12} />
                <Highlight text={project.area_name} query={query} />
              </span>
            )}
            {project.image_count > 0 && (
              <span className="fact">
                <IconImage size={12} />
                {project.image_count}
              </span>
            )}
          </div>

          {phoneList(project).length > 0 && (
            <div className="fact" style={{ alignSelf: "flex-start" }}>
              <IconPhone size={12} />
              <ContactPhoneSummary record={project}>
                {(primary) => (project.contact_name ? `${project.contact_name} · ${primary}` : primary)}
              </ContactPhoneSummary>
            </div>
          )}

          <div className="pcard__foot">
            <span className="cell-truncate">Added {project.formatted_timestamp}</span>
            <div onClick={(event) => event.stopPropagation()}>
              <RowActions project={project} onEdit={onEdit} onDelete={onDelete} />
            </div>
          </div>
        </Panel>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- detail */

/**
 * The full-detail dialog opened by clicking any row or card — the
 * Properties page's own detail dialog, minus what only a WhatsApp capture
 * has (sender, source, original message, review state). Photos are fetched
 * only when someone asks to see them, and the browser keeps them until the
 * project is edited (see builderProjectApi.getBuilderProjectImages).
 */
function BuilderProjectDetailDialog({
  project,
  onClose,
  onEdit,
  onDelete,
}: {
  project: BuilderProjectRecord;
  onClose: () => void;
  onEdit: (project: BuilderProjectRecord) => void;
  onDelete: (project: BuilderProjectRecord) => void;
}) {
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  const [photos, setPhotos] = useState<string[] | null>(null);
  const [loadingPhotos, setLoadingPhotos] = useState(false);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const photoCount = photos?.length ?? 0;
  const hasUnloadedPhotos = photos === null && project.image_count > 0;

  // A different project — or this one after an edit — must never show the
  // photos loaded for what was open before.
  useEffect(() => {
    setPhotos(null);
    setLoadingPhotos(false);
    setPhotoError(null);
    setLightboxIndex(null);
  }, [project.record_id, project.updated_at]);

  async function loadPhotos() {
    setLoadingPhotos(true);
    setPhotoError(null);
    try {
      const { image_urls } = await builderProjectApi.getBuilderProjectImages(project.record_id);
      setPhotos(image_urls);
    } catch (err) {
      setPhotoError(friendlyError(err));
    } finally {
      setLoadingPhotos(false);
    }
  }

  // Escape backs out of the lightbox first, then closes the dialog.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        if (lightboxIndex !== null) setLightboxIndex(null);
        else onClose();
        return;
      }
      if (lightboxIndex === null || photoCount < 2) return;
      if (event.key === "ArrowRight") setLightboxIndex((i) => (i === null ? i : (i + 1) % photoCount));
      if (event.key === "ArrowLeft") setLightboxIndex((i) => (i === null ? i : (i - 1 + photoCount) % photoCount));
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, lightboxIndex, photoCount]);

  const subtitle = [project.area_name, project.address].filter(Boolean).join(" · ");

  return createPortal(
    <>
      <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
        <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="Builder project details">
          <div className="detail-modal__head">
            <div style={{ minWidth: 0 }}>
              <div className="detail-modal__eyebrow">
                Builder project · {project.property_type ?? "Property"} · {project.listing_type}
              </div>
              <h2 className="detail-modal__title cell-truncate">{projectTitle(project)}</h2>
              {subtitle && <div className="detail-modal__sub cell-truncate">{subtitle}</div>}
              <div className="detail-modal__badges">
                {project.bhk && (
                  <span className="fact">
                    <IconBuilding size={12} />
                    {project.bhk}
                  </span>
                )}
                {formatArea(project.area_sqft, project.area_vaar) !== "—" && (
                  <span className="fact">
                    <IconRuler size={12} />
                    {formatArea(project.area_sqft, project.area_vaar)}
                  </span>
                )}
                {project.super_built && (
                  <span className="fact" title="Super built">
                    <IconRuler size={12} />
                    Super built {project.super_built}
                  </span>
                )}
                {project.furnishing && <span className="fact">{project.furnishing}</span>}
                {!project.is_available && (
                  <span className="fact" title="Marked not available">
                    Not available
                  </span>
                )}
              </div>
            </div>
            <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
              <IconX size={15} />
            </button>
          </div>

          <div className="detail-modal__body">
            {hasUnloadedPhotos && (
              <div style={{ marginBottom: 14 }}>
                <Button variant="ghost" icon={<IconImage size={14} />} busy={loadingPhotos} onClick={loadPhotos}>
                  {`Show ${project.image_count} photo${project.image_count === 1 ? "" : "s"}`}
                </Button>
                {photoError && (
                  <div style={{ marginTop: 8 }}>
                    <Note tone="bad" icon={<IconAlert size={16} />}>
                      {photoError}
                    </Note>
                  </div>
                )}
              </div>
            )}

            {photos && photoCount > 0 && (
              <div className="detail__gallery">
                {photos.map((src, index) => (
                  <button
                    key={index}
                    type="button"
                    className="detail__photo"
                    onClick={() => setLightboxIndex(index)}
                    aria-label={`View photo ${index + 1} of ${photoCount}`}
                  >
                    <img src={src} alt={`Builder project photo ${index + 1}`} />
                  </button>
                ))}
              </div>
            )}

            <div className="detail__grid">
              <div className="detail__block">
                <div className="detail__k">Price as written</div>
                <div className="detail__v">{project.price_text ?? "—"}</div>
                {project.price_amount_inr !== null && (
                  <div className="faint small" style={{ marginTop: 4 }}>
                    Read as {formatPrice(null, project.price_amount_inr)}
                  </div>
                )}
              </div>

              <div className="detail__block">
                <div className="detail__k">Size</div>
                <div className="detail__v">{formatArea(project.area_sqft, project.area_vaar)}</div>
                <div className="faint small" style={{ marginTop: 4 }}>
                  Super built {project.super_built ?? "—"}
                </div>
              </div>

              <div className="detail__block">
                <div className="detail__k">Contact</div>
                <div className="detail__v">{project.contact_name ?? "—"}</div>
                <ContactPhoneDetails record={project} />
              </div>

              <div className="detail__block">
                <div className="detail__k">Added</div>
                <div className="detail__v">{project.formatted_timestamp}</div>
                {project.updated_at && project.updated_at !== project.created_at && (
                  <div className="faint small" style={{ marginTop: 4 }}>
                    Last edited {relativeTime(new Date(project.updated_at))}
                  </div>
                )}
              </div>

              {project.instagram_reel_url && (
                <div className="detail__block">
                  <div className="detail__k">
                    <IconInstagram size={11} /> Instagram reel
                  </div>
                  <div className="detail__v">
                    <a href={project.instagram_reel_url} target="_blank" rel="noreferrer">
                      {project.instagram_reel_url}
                    </a>
                  </div>
                </div>
              )}
            </div>

            {project.description && (
              <div className="detail__block">
                <div className="detail__k">Description</div>
                <div className="detail__v">{project.description}</div>
              </div>
            )}
          </div>

          <div className="detail-modal__foot">
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
            <span className="row-flex" style={{ marginLeft: "auto", gap: 10 }}>
              <Button variant="ghost" icon={<IconEdit size={14} />} onClick={() => onEdit(project)}>
                Edit
              </Button>
              <Button className="btn--danger" icon={<IconTrash size={14} />} onClick={() => onDelete(project)}>
                Delete
              </Button>
            </span>
          </div>
        </div>
      </div>

      {lightboxIndex !== null && (
        <div
          className="modal-scrim lightbox-scrim"
          onMouseDown={(event) => event.target === event.currentTarget && setLightboxIndex(null)}
        >
          <div className="lightbox anim-pop">
            <button type="button" className="lightbox__close" onClick={() => setLightboxIndex(null)} aria-label="Close photo">
              <IconX size={16} />
            </button>
            {photoCount > 1 && (
              <button
                type="button"
                className="lightbox__nav lightbox__nav--prev"
                onClick={() => setLightboxIndex((i) => (i === null ? i : (i - 1 + photoCount) % photoCount))}
                aria-label="Previous photo"
              >
                <IconChevron size={18} />
              </button>
            )}
            <img src={(photos ?? [])[lightboxIndex]} alt={`Builder project photo ${lightboxIndex + 1}`} />
            {photoCount > 1 && (
              <button
                type="button"
                className="lightbox__nav lightbox__nav--next"
                onClick={() => setLightboxIndex((i) => (i === null ? i : (i + 1) % photoCount))}
                aria-label="Next photo"
              >
                <IconChevron size={18} />
              </button>
            )}
            {photoCount > 1 && (
              <div className="lightbox__count">
                {lightboxIndex + 1} / {photoCount}
              </div>
            )}
          </div>
        </div>
      )}
    </>,
    document.body,
  );
}
