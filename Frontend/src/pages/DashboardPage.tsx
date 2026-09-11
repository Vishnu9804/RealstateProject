import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { propertyApi } from "../api/propertyApi";
import { soldoutPropertyApi } from "../api/soldoutPropertyApi";
import type { PropertyRecord, SoldOutPropertyRecord } from "../api/types";
import { useAppStatus } from "../state/StatusProvider";
import { useDebounced, usePersistentState } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import { formatCarpetArea, formatPrice, formatPricePerUnit, relativeTime } from "../lib/formatters";
import {
  addCachedProperty,
  getCachedPropertyList,
  patchCachedProperty,
  removeCachedProperty,
  setCachedPropertyList,
} from "../lib/propertyListCache";
import {
  getCachedPropertyDetail,
  invalidateCachedPropertyDetail,
  setCachedPropertyDetail,
} from "../lib/propertyDetailCache";
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
import RowRail from "../components/ui/RowRail";
import PropertyFormDialog from "../components/PropertyFormDialog";
import MoveMenu from "../components/ui/MoveMenu";
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
  IconEdit,
  IconGrid,
  IconImage,
  IconInbox,
  IconInstagram,
  IconList,
  IconMessage,
  IconMove,
  IconPhone,
  IconPin,
  IconPlus,
  IconRefresh,
  IconRuler,
  IconSearch,
  IconTag,
  IconTrash,
  IconUsers,
  IconX,
} from "../components/ui/Icons";

/** The four mutually-exclusive views. Main and Outsider are the property's
 *  permanent home (review_status), toggled via the Main/Outsider capsule;
 *  Needs review is an orthogonal queue (needs_review=true, from either
 *  home) opened via its own button. A property is in that queue because
 *  almost nothing could be extracted from its message, and it leaves by a
 *  human completing it and picking Main or Outsider — this is the only
 *  page that shows the queue at all (the matching dialogs never do, since
 *  a flagged property isn't matched).
 *
 *  "soldout" is different in kind from the other three: those are three
 *  views of the SAME list (the property database), filtered by the flags on
 *  each row. Sold out reads a different list entirely — properties whose
 *  deal is done no longer exist in the property database at all (see
 *  Backend/Database/soldout_property_models.py on why that is a separate
 *  table rather than a flag), so this tab fetches its own data and shows it
 *  read-only: there is nothing left to edit, move or assign. */
export type ViewTab = "main" | "outsider" | "needsReview" | "soldout";

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
export type SortKey = "time" | "price" | "priceUnit" | "area" | "society" | "locality";
export type SortDir = "asc" | "desc";

export interface Column {
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

/** Exported so the Landing Page page's table can share the exact same
 *  column set/labels/filter keys as this one, per its own requirement to
 *  filter identically to the Properties page. */
export const COLUMNS: Column[] = [
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
  // `move` carries its destination explicitly rather than deriving it from
  // the property: with three destinations to choose from, "the other one"
  // is no longer a meaningful answer (see MoveMenu.tsx).
  const [confirmAction, setConfirmAction] = useState<
    | { type: "delete"; property: PropertyRecord }
    | { type: "move"; property: PropertyRecord; target: "accepted" | "outsider" }
    | { type: "soldout"; property: PropertyRecord }
    | null
  >(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [formDialog, setFormDialog] = useState<{ mode: "add" | "edit"; property?: PropertyRecord } | null>(null);
  // Not persisted like `view`/`viewTab` — this is a quick one-off lens on
  // the current list, not a standing preference worth remembering across
  // visits.
  const [reelOnly, setReelOnly] = useState(false);

  // The Sold out tab's own list, fetched from its own endpoint (see the
  // ViewTab comment on why it is not a filtered view of `properties`).
  // `null` means "not loaded yet" — deliberately not loaded on mount: most
  // visits to this page never open that tab, and a request nobody asked for
  // is a request worth not making. The effect below loads it the first time
  // the tab is actually opened, and again only when the backend's sold-out
  // change token says a new sale has been recorded.
  const [soldout, setSoldout] = useState<SoldOutPropertyRecord[] | null>(null);
  const [soldoutError, setSoldoutError] = useState<string | null>(null);
  const lastSoldoutVersion = useRef<string | null>(null);
  // Deliberately a ref, not derived from `soldout` being non-null: the
  // effect below must not have the state it writes among its dependencies,
  // or every completed load would re-trigger it (and, while the status poll
  // has not answered yet and there is no version to compare against, that
  // would be an unbounded fetch loop rather than one extra request).
  const soldoutLoaded = useRef(false);

  const searchRef = useRef<HTMLInputElement>(null);
  const listTopRef = useRef<HTMLDivElement>(null);
  const firstPaint = useRef(true);
  // Which records existed at the previous poll — anything new gets a brief
  // highlight so an arrival is noticeable without stealing focus or moving
  // anything the user is currently reading.
  const seenIds = useRef<Set<string> | null>(null);
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());

  // Mirrors the latest status context value without needing it as a `load`
  // dependency — load() reads whatever version is current at the moment its
  // fetch resolves, purely to label the cache entry it writes; it never
  // needs to re-run just because a render happened.
  const { status: appStatus } = useAppStatus();
  const appStatusRef = useRef(appStatus);
  appStatusRef.current = appStatus;

  const load = useCallback(
    async (manual = false) => {
      setRefreshing(true);
      try {
        const data = await propertyApi.getProperties(FETCH_LIMIT);
        setProperties(data);
        setLastUpdated(new Date());
        setError(null);
        setCachedPropertyList(data, appStatusRef.current?.properties_version ?? null);

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

  // On mount: an in-memory cache hit (left behind by this same page, or by
  // the Landing Page page — both read the identical list, see
  // lib/propertyListCache.ts) paints instantly with zero network request.
  // The version-watch effect below then either confirms it's still current
  // (no fetch at all) or fetches once if it turns out stale. No cache hit
  // falls back to the unconditional fetch this always did.
  const lastPropertiesVersion = useRef<string | null>(null);

  useEffect(() => {
    const cached = getCachedPropertyList();
    if (cached) {
      setProperties(cached.data);
      setLastUpdated(new Date(cached.fetchedAt));
      seenIds.current = new Set(cached.data.map((p) => p.record_id));
      lastPropertiesVersion.current = cached.version;
      return;
    }
    void load(false);
    // Intentionally mount-only — the effect below drives every subsequent load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const version = appStatus?.properties_version;
    if (version === undefined) return;
    if (lastPropertiesVersion.current === null) {
      // First observation with no prior cache — the mount effect above
      // already fetched current data at roughly the same time, so just
      // start tracking from here rather than triggering a redundant fetch.
      lastPropertiesVersion.current = version;
      return;
    }
    if (lastPropertiesVersion.current === version) return;
    lastPropertiesVersion.current = version;
    void load(false);
  }, [appStatus?.properties_version, load]);

  const loadSoldout = useCallback(async () => {
    try {
      const data = await soldoutPropertyApi.getSoldOutProperties(FETCH_LIMIT);
      setSoldout(data);
      setSoldoutError(null);
    } catch (err) {
      setSoldoutError(friendlyError(err));
      // So leaving this tab and coming back retries, instead of the first
      // failed attempt latching the error in place for the whole session.
      soldoutLoaded.current = false;
    }
  }, []);

  // Loads the sold-out list the first time its tab is opened, and re-loads
  // it only when the backend's own change token moves — i.e. when a sale
  // has actually been recorded, by this tab or anywhere else. A sold-out
  // record is never edited, so between sales there is nothing to re-fetch,
  // and the request that proves it costs no database work on either side
  // (see soldoutPropertyApi's own comment).
  const soldoutVersion = appStatus?.soldout_version;
  useEffect(() => {
    if (viewTab !== "soldout") return;
    // Already holding this exact version — nothing to do. `undefined` means
    // the status poll has not answered yet, which is not a new version and
    // must not count as one.
    if (soldoutLoaded.current && (soldoutVersion === undefined || lastSoldoutVersion.current === soldoutVersion)) {
      return;
    }
    soldoutLoaded.current = true;
    lastSoldoutVersion.current = soldoutVersion ?? null;
    void loadSoldout();
  }, [viewTab, soldoutVersion, loadSoldout]);

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
  const onSoldOutTab = viewTab === "soldout";
  const soldOutList = useMemo(() => soldout ?? [], [soldout]);
  /** The pool the open tab draws from: the property database for the three
   *  views of it, the sold-out list for its own tab. Everything downstream
   *  (search, column filters, sorting, paging, the empty states) works off
   *  this, which is what lets one page serve both without a second copy of
   *  any of it. */
  const tabSource: PropertyRecord[] = onSoldOutTab ? soldOutList : allProperties;

  // Derived from the live list rather than snapshotted at open time — a
  // background poll landing while the dialog is open keeps it showing
  // current data, and a delete (which removes the property from this list
  // entirely) closes it automatically for free, with no extra bookkeeping.
  const detailProperty = useMemo(
    () => (detailId ? (tabSource.find((p) => p.record_id === detailId) ?? null) : null),
    [detailId, tabSource],
  );
  /** The sale date, when the open dialog is showing a sold-out record —
   *  which is also what puts that dialog into its read-only mode (see
   *  PropertyDetailDialog's own `soldOutAt` prop). */
  const detailSoldOutAt = detailProperty ? soldOutStamp(detailProperty) : null;

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
    // The sold-out list has no sub-division: every record in it is sold, so
    // there is no flag left to filter it by.
    if (viewTab === "soldout") return soldOutList;
    if (viewTab === "needsReview") return allProperties.filter((p) => p.needs_review);
    if (viewTab === "outsider") return allProperties.filter((p) => p.review_status === "outsider" && !p.needs_review);
    return allProperties.filter((p) => p.review_status === "accepted" && !p.needs_review);
  }, [allProperties, soldOutList, viewTab]);

  const localities = useMemo(() => {
    // Case-folded to match how the Area filter groups its options —
    // otherwise this tile claims more localities than that picker lists.
    const set = new Set<string>();
    allProperties.forEach((p) => p.area_name?.trim() && set.add(p.area_name.trim().toLowerCase()));
    return set.size;
  }, [allProperties]);

  const soldOutCount = appStatus?.soldout_property_count ?? soldOutList.length;

  const passesFilters = useMemo(() => compileFilters(filters), [filters]);

  const visibleProperties = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = tabFiltered.filter((property) => {
      if (!passesFilters(property)) return false;
      if (reelOnly && !property.instagram_reel_url) return false;
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
  }, [tabFiltered, query, passesFilters, sortKey, sortDir, reelOnly]);

  const pageCount = Math.max(1, Math.ceil(visibleProperties.length / PAGE_SIZE));
  const pageItems = useMemo(
    () => visibleProperties.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
    [visibleProperties, page],
  );

  // Any change that narrows the list invalidates the page you were on —
  // page 7 of a 3-page result is a blank screen that reads as a bug.
  useEffect(() => setPage(1), [query, filters, sortKey, sortDir, viewTab, reelOnly]);
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

  // Every mutation in this page (Accept/Move/Edit save/Delete/Add) funnels
  // through updateLocalProperty/removeLocalProperty below — patching the
  // shared list cache (lib/propertyListCache.ts) right here, once, means
  // every call site gets it for free instead of remembering it individually.
  function updateLocalProperty(recordId: string, next: PropertyRecord) {
    setProperties((prev) => (prev ? prev.map((p) => (p.record_id === recordId ? next : p)) : prev));
    patchCachedProperty(recordId, next);
    setCachedPropertyDetail(next);
  }

  function removeLocalProperty(recordId: string) {
    setProperties((prev) => (prev ? prev.filter((p) => p.record_id !== recordId) : prev));
    removeCachedProperty(recordId);
    invalidateCachedPropertyDetail(recordId);
  }

  // The polled list (load, above) never carries real photos — see
  // propertyApi.getProperties's own comment — so opening a property's
  // detail or Edit dialog needs the one full record first. lib/
  // propertyDetailCache.ts is checked before hitting the network — it's
  // shared with the Landing Page and Inquiries pages, so reopening a
  // property recently viewed from any of them skips the fetch entirely (and
  // with it, re-downloading that property's photos). A poll landing later
  // just overwrites `properties` back to the photo-less summary, which is
  // fine: reopening re-fetches (or re-reads the cache) in a moment, and
  // while it's open the poll never replaces `detailProperty`/`formDialog`'s
  // own already-fetched object out from under it.
  // Opens immediately using whatever this row already has (the polled
  // summary — everything except real photos, see propertyApi.getProperties'
  // own comment) rather than waiting on the fetch below first — a dialog
  // that only appears once its data has fully loaded reads as broken (a
  // click that visibly does nothing for a second), where every other
  // detail dialog in the app (ClientMatchesDialog, PropertyReadOnlyDialog,
  // SelectPropertyPage) opens at once and fills in as data arrives. The
  // background fetch below only exists to backfill photos.
  function openDetail(recordId: string) {
    setDetailId(recordId);
    // A sold-out record is already complete in the list this tab fetched
    // (everything except photos, which come from its own endpoint on
    // demand), and it no longer exists in the property database — so the
    // backfill below would only produce a 404 and an error toast for a
    // dialog that in fact has everything it needs.
    if (onSoldOutTab) return;
    const cached = getCachedPropertyDetail(recordId);
    if (cached) {
      updateLocalProperty(recordId, cached);
      return;
    }
    propertyApi
      .getProperty(recordId)
      .then((full) => updateLocalProperty(recordId, full))
      .catch((err) => {
        toast.push({ tone: "bad", title: "Couldn't load this property's photos", message: friendlyError(err) });
      });
  }

  async function openEdit(property: PropertyRecord) {
    const cached = getCachedPropertyDetail(property.record_id);
    if (cached) {
      updateLocalProperty(property.record_id, cached);
      setFormDialog({ mode: "edit", property: cached });
      return;
    }
    try {
      const full = await propertyApi.getProperty(property.record_id);
      updateLocalProperty(property.record_id, full);
      setFormDialog({ mode: "edit", property: full });
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't open this property", message: friendlyError(err) });
    }
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

  /** Needs review's own resolution step: unlike the plain Accept above
   *  (which clears needs_review but leaves review_status untouched), this
   *  both clears the review flag AND places the property into whichever of
   *  Main/Outsider the reviewer picked, in one request — see the Needs
   *  review dialog's Move to Main / Move to Outsider buttons. */
  async function handleResolveReview(property: PropertyRecord, targetStatus: "accepted" | "outsider") {
    try {
      const updated = await propertyApi.updateProperty(property.record_id, {
        review_status: targetStatus,
        needs_review: false,
      });
      updateLocalProperty(property.record_id, updated);
      setDetailId(null);
      toast.push({
        tone: "ok",
        title: "Moved",
        message: `Moved into ${targetStatus === "outsider" ? "Outsider" : "Main"}.`,
      });
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't move property", message: friendlyError(err) });
    }
  }

  async function confirmMove() {
    if (!confirmAction || confirmAction.type !== "move") return;
    const property = confirmAction.property;
    const nextStatus = confirmAction.target;
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

  /** Moving a property to Sold out is not a status change like the other
   *  two destinations — it takes the property out of the property database
   *  altogether, so locally it is a removal, exactly like a delete. The
   *  sold-out list then reloads (the record has to come back with the
   *  `sold_out_at` the backend assigned), and the toast reports what the
   *  one request actually did on the other side: the visits it cancelled
   *  and the agents it reached. */
  async function confirmSoldOut() {
    if (!confirmAction || confirmAction.type !== "soldout") return;
    const property = confirmAction.property;
    setActionBusy(true);
    try {
      const result = await soldoutPropertyApi.markSoldOut(property.record_id);
      removeLocalProperty(property.record_id);
      setDetailId(null);
      // Folded in locally as well as reloaded, so switching to the Sold out
      // tab right now shows it even before the status poll's next tick.
      setSoldout((prev) => (prev ? [result.property, ...prev.filter((p) => p.record_id !== property.record_id)] : prev));
      lastSoldoutVersion.current = null;
      const cancelled =
        result.visits_cancelled > 0
          ? ` ${result.visits_cancelled} pending visit${result.visits_cancelled === 1 ? "" : "s"} cancelled, ` +
            `${result.agents_notified} agent${result.agents_notified === 1 ? "" : "s"} notified on WhatsApp` +
            (result.agents_failed > 0 ? ` (${result.agents_failed} could not be reached — tell them by hand).` : ".")
          : "";
      toast.push({
        tone: result.agents_failed > 0 ? "warn" : "ok",
        title: "Marked sold out",
        message: `It now appears only in the Sold out tab.${cancelled}`,
      });
      setConfirmAction(null);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't mark this property sold out", message: friendlyError(err) });
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
          <Button variant="primary" icon={<IconPlus size={15} />} onClick={() => setFormDialog({ mode: "add" })}>
            Add property
          </Button>
        </div>
      </header>

      {/* Hidden on the Sold out tab: every tile here counts something about
          the live property database (stored, needing review, outsider,
          localities), and showing those above a list of closed deals reads
          as if they described it. */}
      {allProperties.length > 0 && !onSoldOutTab && (
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

        <Segmented<"main" | "outsider" | "soldout">
          ariaLabel="Main, Outsider or Sold out"
          value={viewTab === "needsReview" ? null : viewTab}
          onChange={setViewTab}
          options={[
            { value: "main", label: "Main" },
            { value: "outsider", label: `Outsider${outsiderCount ? ` (${outsiderCount})` : ""}` },
            // The count comes from the status poll (already running for
            // every page), so the tab can show how many sales there are
            // without this page fetching the list first.
            { value: "soldout", label: `Sold out${soldOutCount ? ` (${soldOutCount})` : ""}` },
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

        <Button
          size="sm"
          variant={reelOnly ? "primary" : "ghost"}
          icon={<IconInstagram size={14} />}
          onClick={() => setReelOnly((prev) => !prev)}
          title="Show only properties with an Instagram reel link"
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
      {view === "cards" && tabSource.length > 0 && (
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

      {(loading || (onSoldOutTab && soldout === null && soldoutError === null)) && (
        <Panel>
          <div className="stack stack-3">
            <div className="row-flex faint small">
              <span className="spinner" /> Loading {onSoldOutTab ? "sold-out properties" : "properties"}…
            </div>
            <SkeletonRows rows={6} />
          </div>
        </Panel>
      )}

      {onSoldOutTab && soldoutError && (
        <Note tone="bad" icon={<IconAlert size={17} />}>
          <strong>Couldn't load the sold-out properties.</strong> {soldoutError}
        </Note>
      )}

      {!onSoldOutTab && properties !== null && allProperties.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconInbox size={38} />}
            title="Nothing captured yet"
            body="Properties appear here automatically once a monitored chat receives a message that looks property-related. Check the Connection page to confirm something is being watched."
          />
        </Panel>
      )}

      {onSoldOutTab && soldout !== null && soldOutList.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconTag size={36} />}
            title="No sold-out properties yet"
            body="A property lands here when you move it to Sold out from the Properties list. It then leaves the property database entirely — off this page's other tabs, off the Landing Page, off the public website, and out of every client's matches — and lives only here."
          />
        </Panel>
      )}

      {!onSoldOutTab && allProperties.length > 0 && tabFiltered.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconInbox size={36} />}
            title={viewTab === "needsReview" ? "Nothing needs review" : `No ${viewTab === "outsider" ? "outsider" : "Main"} properties`}
            body={
              viewTab === "needsReview"
                ? "Nothing is waiting to be completed by hand — a property only lands here when almost nothing could be extracted from its message."
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
              onOpenDetail={(property) => openDetail(property.record_id)}
              sortKey={sortKey}
              sortDir={sortDir}
              toggleSort={toggleSort}
              freshIds={freshIds}
              filters={filters}
              openFilterKey={openFilter?.key ?? null}
              onOpenFilter={(key, anchor) => setOpenFilter(openFilter?.key === key ? null : { key, anchor })}
              viewTab={viewTab}
              onAccept={handleAccept}
              onMove={(property, target) => setConfirmAction({ type: "move", property, target })}
              onSoldOut={(property) => setConfirmAction({ type: "soldout", property })}
              onDelete={(property) => setConfirmAction({ type: "delete", property })}
              onEdit={(property) => openEdit(property)}
            />
          ) : (
            <PropertyCards
              properties={pageItems}
              query={query}
              onOpenDetail={(property) => openDetail(property.record_id)}
              freshIds={freshIds}
              viewTab={viewTab}
              onAccept={handleAccept}
              onMove={(property, target) => setConfirmAction({ type: "move", property, target })}
              onSoldOut={(property) => setConfirmAction({ type: "soldout", property })}
              onDelete={(property) => setConfirmAction({ type: "delete", property })}
              onEdit={(property) => openEdit(property)}
            />
          )}
          <Pager page={page} pageCount={pageCount} total={visibleProperties.length} onChange={setPage} />
        </>
      )}

      {openFilter && (
        <FilterPopover
          def={FILTER_DEF_BY_KEY[openFilter.key]}
          anchorEl={openFilter.anchor}
          /* The open tab's own pool, so on the Sold out tab the value
             pickers list the areas/BHKs/types that actually appear among
             sold properties rather than among live ones. */
          properties={tabSource}
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
          onResolveReview={handleResolveReview}
          onMove={(property) =>
            setConfirmAction({
              type: "move",
              property,
              target: property.review_status === "outsider" ? "accepted" : "outsider",
            })
          }
          onSoldOut={(property) => setConfirmAction({ type: "soldout", property })}
          onDelete={(property) => setConfirmAction({ type: "delete", property })}
          onEdit={(property) => openEdit(property)}
          onClose={() => setDetailId(null)}
          soldOutAt={detailSoldOutAt}
          /* Sold-out photos come from their own endpoint — the property they
             belonged to is no longer in the property database, so the usual
             one would 404. */
          loadImages={onSoldOutTab ? soldoutPropertyApi.getSoldOutPropertyImages : undefined}
        />
      )}

      {formDialog && (
        <PropertyFormDialog
          mode={formDialog.mode}
          property={formDialog.property}
          onClose={() => setFormDialog(null)}
          onSaved={(saved, mode) => {
            if (mode === "add") {
              setProperties((prev) => (prev ? [...prev, saved] : [saved]));
              seenIds.current?.add(saved.record_id);
              addCachedProperty(saved);
              setCachedPropertyDetail(saved);
            } else {
              updateLocalProperty(saved.record_id, saved);
            }
            setFormDialog(null);
          }}
        />
      )}

      {confirmAction && (
        <ConfirmDialog
          title={
            confirmAction.type === "delete"
              ? "Delete this property?"
              : confirmAction.type === "soldout"
                ? "Mark this property sold out?"
                : "Move this property?"
          }
          body={
            confirmAction.type === "delete" ? (
              <>
                <PropertySummary property={confirmAction.property} />
                <p style={{ marginTop: 12 }}>
                  This removes it from your database <strong>permanently</strong> — it cannot be undone.
                </p>
              </>
            ) : confirmAction.type === "soldout" ? (
              <>
                <PropertySummary property={confirmAction.property} />
                {/* Spelled out in full because this is not reversible from
                    the UI, and because most of what it does happens
                    somewhere the operator isn't looking: other clients'
                    matches, the public website, and agents' phones. */}
                <p style={{ marginTop: 12 }}>
                  It moves into the <strong>Sold out</strong> tab and leaves your property database, so it
                  disappears from Main/Outsider, the Landing Page, the public website, every client's matches and
                  every hand-picked list.
                </p>
                <p style={{ marginTop: 8 }}>
                  Every <strong>pending site visit</strong> for it is cancelled, and each agent who had one is
                  told on WhatsApp — once, however many visits they were holding. Visits already completed are
                  kept as history.
                </p>
                <p className="faint small" style={{ marginTop: 8 }}>
                  This cannot be undone from here.
                </p>
              </>
            ) : (
              <>
                <PropertySummary property={confirmAction.property} />
                <p style={{ marginTop: 12 }}>
                  Move it to <strong>{confirmAction.target === "outsider" ? "Outsider" : "Main"}</strong>?
                </p>
              </>
            )
          }
          confirmLabel={
            confirmAction.type === "delete"
              ? "Delete forever"
              : confirmAction.type === "soldout"
                ? "Mark sold out"
                : "Move"
          }
          tone={confirmAction.type === "move" ? "default" : "danger"}
          busy={actionBusy}
          onConfirm={
            confirmAction.type === "delete"
              ? confirmDelete
              : confirmAction.type === "soldout"
                ? confirmSoldOut
                : confirmMove
          }
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

export function Pager({
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

export function FilterTrigger({
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
  /** The destination is passed explicitly — see the `confirmAction` state's
   *  own comment on why "the other one" stopped being an answer. */
  onMove: (property: PropertyRecord, target: "accepted" | "outsider") => void;
  onSoldOut: (property: PropertyRecord) => void;
  onDelete: (property: PropertyRecord) => void;
  onEdit: (property: PropertyRecord) => void;
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
  onSoldOut,
  onDelete,
  onEdit,
}: ListProps & {
  sortKey: SortKey;
  sortDir: SortDir;
  toggleSort: (key: SortKey) => void;
  filters: FilterState;
  openFilterKey: string | null;
  onOpenFilter: (key: string, anchor: HTMLElement) => void;
}) {
  const tableWrapRef = useRef<HTMLDivElement>(null);
  return (
    <div className="table-with-rail" ref={tableWrapRef}>
      <RowRail containerRef={tableWrapRef} count={properties.length} className="anim-rise" ariaHidden>
        {(index) => {
          const property = properties[index];
          return property ? <PropertyIndicators property={property} /> : null;
        }}
      </RowRail>
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
                  data-rail-row=""
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
                  <td onClick={(event) => event.stopPropagation()} style={{ textAlign: "right" }}>
                    {viewTab === "soldout" ? (
                      <SoldOutStamp property={property} />
                    ) : (
                      <RowActions
                        property={property}
                        viewTab={viewTab}
                        onAccept={onAccept}
                        onMove={onMove}
                        onSoldOut={onSoldOut}
                        onDelete={onDelete}
                        onEdit={onEdit}
                      />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
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
  onSoldOut,
  onDelete,
  onEdit,
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
                {viewTab === "soldout" ? (
                  <SoldOutStamp property={property} />
                ) : (
                  <RowActions
                    property={property}
                    viewTab={viewTab}
                    onAccept={onAccept}
                    onMove={onMove}
                    onSoldOut={onSoldOut}
                    onDelete={onDelete}
                    onEdit={onEdit}
                  />
                )}
              </div>
            </div>
          </Panel>
        );
      })}
    </div>
  );
}

/* ---------------------------------------------------------------- actions */

/** The sale date, for a record that has one — which is exactly the records
 *  in the Sold out tab. Read this way rather than from a flag so that
 *  anywhere handling a plain PropertyRecord can still recognise a sold-out
 *  one without every component learning about a second type. */
export function soldOutStamp(property: PropertyRecord): string | null {
  return (property as Partial<SoldOutPropertyRecord>).formatted_sold_out_at ?? null;
}

/** What the Sold out tab shows where the Edit/Move/Delete buttons would
 *  otherwise be. That tab is read-only — the property has left the database
 *  and there is nothing left to edit, move or assign — so the cell carries
 *  the one fact those buttons no longer can. */
function SoldOutStamp({ property }: { property: PropertyRecord }) {
  const stamp = soldOutStamp(property);
  if (!stamp) return null;
  return (
    <span className="soldout-stamp" title={`Marked sold out on ${stamp}`}>
      <IconTag size={12} /> Sold {stamp}
    </span>
  );
}

/** Delete and Move-to are available on every property in every view; Accept
 *  only makes sense while looking at the Needs review queue. Shared between
 *  the table's action cell and the card's action row so the icon set and
 *  behaviour never drift apart between layouts.
 *
 *  Not rendered at all in the Sold out tab (its callers show SoldOutStamp
 *  instead): every action here either edits the property or moves it
 *  somewhere, and a sold property is no longer in the database to do either
 *  to. */
function RowActions({
  property,
  viewTab,
  onAccept,
  onMove,
  onSoldOut,
  onDelete,
  onEdit,
}: {
  property: PropertyRecord;
  viewTab: ViewTab;
  onAccept: (property: PropertyRecord) => void;
  onMove: (property: PropertyRecord, target: "accepted" | "outsider") => void;
  onSoldOut: (property: PropertyRecord) => void;
  onDelete: (property: PropertyRecord) => void;
  onEdit: (property: PropertyRecord) => void;
}) {
  const isOutsider = property.review_status === "outsider";
  // The home it is already in is left out — offering "Move to Main" on a
  // property that is in Main is an option that does nothing.
  const moveTargets = [
    isOutsider
      ? { key: "main", label: "Move to Main", onSelect: () => onMove(property, "accepted") }
      : { key: "outsider", label: "Move to Outsider", onSelect: () => onMove(property, "outsider") },
    {
      key: "soldout",
      label: "Move to Sold out",
      hint: "Removes it everywhere, cancels visits",
      danger: true,
      onSelect: () => onSoldOut(property),
    },
  ];
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
      {/* Edit only makes sense once a property has left the review queue —
          Needs review already has its own resolution step (Accept), and
          editing a not-yet-reviewed property here would let its content
          change before anyone has actually looked at it. */}
      {viewTab !== "needsReview" && (
        <button
          type="button"
          className="row-actions__btn"
          title="Edit"
          aria-label="Edit this property"
          onClick={() => onEdit(property)}
        >
          <IconEdit size={15} />
        </button>
      )}
      <MoveMenu targets={moveTargets} />
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
export function PropertyDetailDialog({
  property,
  viewTab,
  onResolveReview,
  onMove,
  onSoldOut,
  onDelete,
  onEdit,
  onClose,
  selectAction,
  soldOutAt,
  loadImages,
}: {
  property: PropertyRecord;
  viewTab: ViewTab;
  /** Only set by the Properties page — backs the Needs review dialog's Move
   *  to Main / Move to Outsider buttons (clears needs_review AND sets
   *  review_status in one request). Undefined on the Landing Page page,
   *  which never shows a property with needs_review=true in the first
   *  place (see its own viewTab prop, always "main"/"outsider" there). */
  onResolveReview?: (property: PropertyRecord, targetStatus: "accepted" | "outsider") => void;
  onMove: (property: PropertyRecord) => void;
  /** Only set by the Properties page — adds "Mark sold out" to the footer.
   *  Undefined on the Landing Page page, so the button simply doesn't
   *  render there (a property is sold out from the Properties list, which
   *  is where the whole move lives). */
  onSoldOut?: (property: PropertyRecord) => void;
  onDelete: (property: PropertyRecord) => void;
  onEdit: (property: PropertyRecord) => void;
  onClose: () => void;
  /** The sale date, set only when this dialog is showing a SOLD-OUT record.
   *  It is what puts the dialog into read-only mode: no Edit, no Move, no
   *  Delete — the property has left the database, and every one of those
   *  actions would have nothing to act on. */
  soldOutAt?: string | null;
  /** Where to fetch this record's photos from. Defaults to the properties
   *  endpoint; the Sold out tab passes its own, because the property that
   *  owned these photos no longer exists in the property database. */
  loadImages?: (recordId: string) => Promise<{ image_urls: string[] }>;
  /** Only set by the Landing Page page — an extra footer button, just left
   *  of Edit, that marks this property selected (green on Ready to Add, red
   *  on Live) and closes the dialog, same as clicking its row's own select
   *  button. Undefined everywhere else (the Properties page), so the button
   *  simply doesn't render there. */
  selectAction?: { selected: boolean; tone: "add" | "remove"; onToggle: () => void };
}) {
  // The photo lightbox lives inside this same dialog rather than as a
  // sibling — Escape backs out of it first (one Escape, one step back) and
  // only closes the whole detail dialog once no photo is open.
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);

  // Photos are NOT part of opening a property any more. The record arrives
  // with image_urls: [] and the real number in image_count (see
  // propertyApi.getProperty), so this dialog opens instantly out of the
  // backend's in-memory snapshot and only fetches the actual image data —
  // base64, often megabytes — if someone asks to see it.
  //
  // `property.image_urls` is still honoured when it IS populated: the
  // record handed back by a save carries the photos already, and there is
  // no reason to re-fetch what we were just given.
  const [fetchedPhotos, setFetchedPhotos] = useState<string[] | null>(null);
  const [loadingPhotos, setLoadingPhotos] = useState(false);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const photos = fetchedPhotos ?? (property.image_urls.length > 0 ? property.image_urls : null);
  const photoCount = photos?.length ?? 0;
  const hasUnloadedPhotos = photos === null && property.image_count > 0;

  // A different property in the same mounted dialog must not show the
  // previous one's photos.
  useEffect(() => {
    setFetchedPhotos(null);
    setLoadingPhotos(false);
    setPhotoError(null);
    setLightboxIndex(null);
  }, [property.record_id]);

  async function loadPhotos() {
    setLoadingPhotos(true);
    setPhotoError(null);
    try {
      const { image_urls } = await (loadImages ?? propertyApi.getPropertyImages)(property.record_id);
      setFetchedPhotos(image_urls);
    } catch (err) {
      setPhotoError(friendlyError(err));
    } finally {
      setLoadingPhotos(false);
    }
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        if (lightboxIndex !== null) {
          setLightboxIndex(null);
        } else {
          onClose();
        }
        return;
      }
      if (lightboxIndex === null || photoCount < 2) return;
      if (event.key === "ArrowRight") setLightboxIndex((i) => (i === null ? i : (i + 1) % photoCount));
      if (event.key === "ArrowLeft") setLightboxIndex((i) => (i === null ? i : (i - 1 + photoCount) % photoCount));
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, lightboxIndex, photoCount]);

  const movesTo = property.review_status === "outsider" ? "Main" : "Outsider";
  const subtitle = [property.area_name, property.address].filter(Boolean).join(" · ");

  return createPortal(
    <>
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
          {hasUnloadedPhotos && (
            <div style={{ marginBottom: 14 }}>
              <Button variant="ghost" icon={<IconImage size={14} />} busy={loadingPhotos} onClick={loadPhotos}>
                {`Show ${property.image_count} photo${property.image_count === 1 ? "" : "s"}`}
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
                  <img src={src} alt={`Property photo ${index + 1}`} />
                </button>
              ))}
            </div>
          )}

          {soldOutAt && (
            <Note tone="info" icon={<IconTag size={16} />}>
              <strong>Sold out on {soldOutAt}.</strong> This is a record of a completed deal — it is no longer in
              your property database, so it does not appear on the Landing Page, the public website, or in any
              client's matches, and every pending site visit for it was cancelled when it was marked sold out.
            </Note>
          )}

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

            {property.instagram_reel_url && (
              <div className="detail__block">
                <div className="detail__k">
                  <IconInstagram size={11} /> Instagram reel
                </div>
                <div className="detail__v">
                  <a href={property.instagram_reel_url} target="_blank" rel="noreferrer">
                    {property.instagram_reel_url}
                  </a>
                </div>
              </div>
            )}
          </div>

          {/* For a flagged property this holds the extracted excerpt of the
              original message that refers to THIS property specifically —
              a message can list ten, and reading all ten to complete one
              of them is the problem the excerpt solves. Labelled so it's
              obvious that's what you're looking at, rather than a summary
              someone wrote. The full message is still below, unchanged. */}
          {property.description && (
            <div className="detail__block">
              <div className="detail__k">
                {property.needs_review ? "The part of the message about this property" : "Description"}
              </div>
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
            {/* A sold-out record has no actions at all: it is not in the
                property database, so there is nothing to edit, move,
                publish or delete. Close is the whole footer. */}
            {soldOutAt ? null : viewTab === "needsReview" ? (
              <>
                <Button icon={<IconCheck size={14} />} onClick={() => onResolveReview?.(property, "accepted")}>
                  Move to Main
                </Button>
                <Button
                  variant="ghost"
                  icon={<IconMove size={14} />}
                  onClick={() => onResolveReview?.(property, "outsider")}
                >
                  Move to Outsider
                </Button>
              </>
            ) : (
              <>
                {selectAction && (
                  <Button
                    variant="ghost"
                    className={`select-toggle-btn${selectAction.selected ? ` select-toggle-btn--${selectAction.tone}` : ""}`}
                    icon={<IconCheck size={14} />}
                    onClick={() => {
                      selectAction.onToggle();
                      onClose();
                    }}
                  >
                    {selectAction.selected ? "Selected" : "Select"}
                  </Button>
                )}
                <Button variant="ghost" icon={<IconEdit size={14} />} onClick={() => onEdit(property)}>
                  Edit
                </Button>
                <Button variant="ghost" icon={<IconMove size={14} />} onClick={() => onMove(property)}>
                  Move to {movesTo}
                </Button>
                {onSoldOut && (
                  <Button variant="ghost" icon={<IconTag size={14} />} onClick={() => onSoldOut(property)}>
                    Mark sold out
                  </Button>
                )}
              </>
            )}
            {!soldOutAt && (
              <Button className="btn--danger" icon={<IconTrash size={14} />} onClick={() => onDelete(property)}>
                Delete
              </Button>
            )}
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
          <img src={(photos ?? [])[lightboxIndex]} alt={`Property photo ${lightboxIndex + 1}`} />
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

/** One compact chip marking "this property has photos" / "has an Instagram
 *  reel" / has both — rendered in the table's icon rail (a plain column of
 *  divs floating in the page's own left gutter, not inside the table, see
 *  PropertyTable), one slot per row in the same order, so it just scrolls
 *  with the page like everything else — no JS position syncing. */
function PropertyIndicators({ property }: { property: PropertyRecord }) {
  // image_count, not image_urls.length — this list's properties never carry
  // real photos (see propertyApi.getProperties), but the count is always
  // accurate.
  const hasImages = property.image_count > 0;
  const hasReel = Boolean(property.instagram_reel_url);
  if (!hasImages && !hasReel) return null;
  const label = [
    hasImages && `${property.image_count} photo${property.image_count === 1 ? "" : "s"}`,
    hasReel && "Has an Instagram reel",
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <span className={`row-indicator-chip${hasReel ? " row-indicator-chip--reel" : ""}`} title={label}>
      {hasImages && <IconImage size={14} />}
      {hasReel && <IconInstagram size={14} />}
    </span>
  );
}

/** Nulls sort last in both directions; everything else compares naturally. */
export function compareNullable(a: string | number | null, b: string | number | null, direction: number): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  if (typeof a === "number" && typeof b === "number") return (a - b) * direction;
  return String(a).localeCompare(String(b), "en-IN") * direction;
}
