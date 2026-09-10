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
import MoveMenu, { type MoveOption } from "../components/ui/MoveMenu";
import RowRail from "../components/ui/RowRail";
import PropertyFormDialog from "../components/PropertyFormDialog";
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
  IconCheckCircle,
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
 *  Sold out is not a fourth state of the same rows: those properties have
 *  been MOVED out of the property table entirely, into `soldout_properties`
 *  (see Backend/Service/WhatsAppDataFetchingService/soldout_property_service.py),
 *  which is what makes them vanish from matching, the landing page, agent
 *  hand-offs and every other list at once. This view therefore reads a
 *  completely different list from the other three — see `soldOutProperties`
 *  below — and shares only the table/card/detail components, because a sold
 *  property is still the same property.
 *
 *  Exported because the Landing Page page renders the shared detail dialog
 *  too; it only ever passes "main"/"outsider". */
export type ViewTab = "main" | "outsider" | "needsReview" | "soldOut";

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

/** Every confirmation this page can raise. A discriminated union rather
 *  than a `type` string plus loose extras, so "move" can no longer be
 *  raised without saying WHERE — which is exactly the bug the old
 *  single-direction Move button had once a third destination existed. */
/** Where the "Move to" menu can send a property. The two review_status
 *  homes, plus the one destination that isn't a status at all — Sold out
 *  moves the row to another table entirely (see ViewTab's own comment). */
type MoveTarget = "accepted" | "outsider" | "soldOut";

type ConfirmAction =
  | { type: "delete"; property: PropertyRecord }
  | { type: "move"; property: PropertyRecord; target: "accepted" | "outsider" }
  | { type: "soldOut"; property: PropertyRecord }
  | { type: "deleteSoldOut"; property: PropertyRecord };

/** Title/label/tone per confirmation, kept as a table so the dialog's JSX
 *  below doesn't nest four ternaries deep just to name itself. */
const CONFIRM_COPY: Record<ConfirmAction["type"], { title: string; confirmLabel: string; tone: "danger" | "default" }> =
  {
    delete: { title: "Delete this property?", confirmLabel: "Delete forever", tone: "danger" },
    move: { title: "Move this property?", confirmLabel: "Move", tone: "default" },
    soldOut: { title: "Mark this property sold out?", confirmLabel: "Move to Sold out", tone: "danger" },
    deleteSoldOut: { title: "Delete this sold-out record?", confirmLabel: "Delete forever", tone: "danger" },
  };

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
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null);
  // Which row's "Move to" menu is open, and the button it hangs off.
  const [moveMenu, setMoveMenu] = useState<{ property: PropertyRecord; anchor: HTMLElement } | null>(null);
  const [actionBusy, setActionBusy] = useState(false);

  // The Sold out view's own list. Deliberately NOT merged into
  // `properties`, and deliberately never written into the shared property
  // caches (lib/propertyListCache.ts / propertyDetailCache.ts): those are
  // read by the Landing Page, Inquiries and Select Property screens, none
  // of which should ever be handed a property whose deal has closed. null
  // means "not fetched yet" (the view is lazy — nothing is loaded until the
  // tab is actually opened).
  const [soldOutProperties, setSoldOutProperties] = useState<SoldOutPropertyRecord[] | null>(null);
  const [soldOutError, setSoldOutError] = useState<string | null>(null);
  const [soldOutRefreshing, setSoldOutRefreshing] = useState(false);
  const [formDialog, setFormDialog] = useState<{ mode: "add" | "edit"; property?: PropertyRecord } | null>(null);
  // Not persisted like `view`/`viewTab` — this is a quick one-off lens on
  // the current list, not a standing preference worth remembering across
  // visits.
  const [reelOnly, setReelOnly] = useState(false);

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

  const loadSoldOut = useCallback(
    async (manual = false) => {
      setSoldOutRefreshing(true);
      try {
        const data = await soldoutPropertyApi.getSoldOutProperties(FETCH_LIMIT);
        setSoldOutProperties(data);
        setSoldOutError(null);
        if (manual) {
          toast.push({ tone: "ok", title: "Refreshed", message: `${data.length} sold-out properties loaded.` });
        }
      } catch (err) {
        const message = friendlyError(err);
        setSoldOutError(message);
        if (manual) toast.push({ tone: "bad", title: "Refresh failed", message });
      } finally {
        setSoldOutRefreshing(false);
      }
    },
    [toast],
  );

  // The Sold out list is fetched lazily — nothing is loaded until the tab
  // is actually opened — and then re-fetched only when the shared status
  // poll's own soldout_property_count changes. Rows in that table are only
  // ever inserted or deleted, never edited, so a count IS its change
  // signal; this needs no version token of its own and adds no poll of its
  // own on top of the one every page already shares (state/StatusProvider).
  const soldOutCount = appStatus?.soldout_property_count ?? null;
  const lastSoldOutCount = useRef<number | null>(null);

  useEffect(() => {
    if (viewTab !== "soldOut") return;
    if (soldOutProperties === null) {
      lastSoldOutCount.current = soldOutCount;
      void loadSoldOut(false);
      return;
    }
    if (soldOutCount === null || lastSoldOutCount.current === soldOutCount) return;
    lastSoldOutCount.current = soldOutCount;
    void loadSoldOut(false);
  }, [viewTab, soldOutCount, soldOutProperties, loadSoldOut]);

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
  const soldOutList = useMemo(() => soldOutProperties ?? [], [soldOutProperties]);

  // Derived from whichever live list the current view reads rather than
  // snapshotted at open time — a background poll landing while the dialog
  // is open keeps it showing current data, and anything that removes the
  // property from that list (Delete, or a move to Sold out) closes the
  // dialog automatically for free, with no extra bookkeeping.
  const detailSource = viewTab === "soldOut" ? soldOutList : allProperties;
  const detailProperty = useMemo(
    () => (detailId ? (detailSource.find((p) => p.record_id === detailId) ?? null) : null),
    [detailId, detailSource],
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
    // Sold out reads a different table entirely (see ViewTab's own comment),
    // so there is nothing to filter out of it — everything in that list is,
    // by definition, exactly what this view is for.
    if (viewTab === "soldOut") return soldOutList;
    if (viewTab === "needsReview") return allProperties.filter((p) => p.needs_review);
    if (viewTab === "outsider") return allProperties.filter((p) => p.review_status === "outsider" && !p.needs_review);
    return allProperties.filter((p) => p.review_status === "accepted" && !p.needs_review);
  }, [allProperties, soldOutList, viewTab]);

  // The set the column filters build their option lists from — "everything
  // seen so far" has to mean the list you're actually looking at, or the
  // Sold out view would be filtered by values drawn from properties it
  // doesn't contain (and vice versa).
  const filterSource = viewTab === "soldOut" ? soldOutList : allProperties;

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

  /* The Sold out view's own equivalents. They pointedly do NOT touch the
     shared caches the two above maintain: a sold-out record must never end
     up in lib/propertyListCache.ts or lib/propertyDetailCache.ts, which the
     Landing Page, Inquiries and Select Property screens all read as "live
     properties". */
  function updateLocalSoldOut(recordId: string, next: SoldOutPropertyRecord) {
    setSoldOutProperties((prev) => (prev ? prev.map((p) => (p.record_id === recordId ? next : p)) : prev));
  }

  function removeLocalSoldOut(recordId: string) {
    setSoldOutProperties((prev) => (prev ? prev.filter((p) => p.record_id !== recordId) : prev));
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
    // Sold out has its own endpoint and its own (uncached) fetch — see
    // updateLocalSoldOut's comment for why the shared property caches are
    // deliberately bypassed here.
    if (viewTab === "soldOut") {
      soldoutPropertyApi
        .getSoldOutProperty(recordId)
        .then((full) => updateLocalSoldOut(recordId, full))
        .catch((err) => {
          toast.push({ tone: "bad", title: "Couldn't load this property's photos", message: friendlyError(err) });
        });
      return;
    }
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

  /** Move between the two homes, Main <-> Outsider. The destination is
   *  whatever the Move menu was told, never inferred from the property's
   *  current home — picking "Move to Main" for a property already in Main
   *  simply isn't offered (see moveOptionsFor), so there is no direction to
   *  guess at. */
  async function confirmMove() {
    if (confirmAction?.type !== "move") return;
    const { property, target } = confirmAction;
    setActionBusy(true);
    try {
      const updated = await propertyApi.updateProperty(property.record_id, { review_status: target });
      updateLocalProperty(property.record_id, updated);
      setDetailId(null);
      toast.push({ tone: "ok", title: "Moved", message: `Moved to ${target === "outsider" ? "Outsider" : "Main"}.` });
      setConfirmAction(null);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't move property", message: friendlyError(err) });
    } finally {
      setActionBusy(false);
    }
  }

  /** "Move to → Sold out": the deal closed. The backend moves the property
   *  out of the property table and into the sold-out one, cancels every
   *  active site visit against it (messaging each agent involved) and drops
   *  the cached matches and hand-picks that pointed at it — see
   *  Backend/Service/WhatsAppDataFetchingService/soldout_property_service.py.
   *
   *  Locally that means exactly what a delete means (the property is gone
   *  from this list and from the shared caches), plus prepending it to the
   *  Sold out list if that view has already been loaded — so switching to
   *  the tab shows it immediately instead of waiting for the next status
   *  tick. The toast reports what actually happened to the agents rather
   *  than just "done": an unreachable agent is a real thing the operator
   *  needs to follow up by phone. */
  async function confirmSoldOut() {
    if (confirmAction?.type !== "soldOut") return;
    const property = confirmAction.property;
    setActionBusy(true);
    try {
      const result = await soldoutPropertyApi.markSoldOut(property.record_id);
      removeLocalProperty(property.record_id);
      setSoldOutProperties((prev) => (prev ? [result.property, ...prev] : prev));
      setDetailId(null);
      const visitNote =
        result.cancelled_visits > 0
          ? ` ${result.cancelled_visits} site visit${result.cancelled_visits === 1 ? "" : "s"} cancelled · ` +
            `${result.agents_notified} agent${result.agents_notified === 1 ? "" : "s"} notified` +
            (result.agents_failed > 0 ? `, ${result.agents_failed} unreachable` : "") +
            "."
          : " No site visits were out for it.";
      toast.push({
        tone: result.agents_failed > 0 ? "warn" : "ok",
        title: "Moved to Sold out",
        message: `Removed from Properties, matches and the landing page.${visitNote}`,
      });
      setConfirmAction(null);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't mark this property sold out", message: friendlyError(err) });
    } finally {
      setActionBusy(false);
    }
  }

  /** Erases one sold-out record for good. The Sold out view's only write
   *  action — there is deliberately no way back to the live table (a
   *  property re-listed later arrives as its own new listing). */
  async function confirmDeleteSoldOut() {
    if (confirmAction?.type !== "deleteSoldOut") return;
    const property = confirmAction.property;
    setActionBusy(true);
    try {
      await soldoutPropertyApi.deleteSoldOutProperty(property.record_id);
      removeLocalSoldOut(property.record_id);
      setDetailId(null);
      toast.push({ tone: "ok", title: "Deleted", message: "Sold-out record removed permanently." });
      setConfirmAction(null);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't delete this record", message: friendlyError(err) });
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

  const loading =
    viewTab === "soldOut"
      ? soldOutProperties === null && soldOutError === null
      : properties === null && error === null;

  /** What the "Move to" menu offers for one property: the home it is NOT
   *  currently in, plus Sold out. A destination a property is already in is
   *  left out rather than shown disabled — there is nothing to explain
   *  about it, and an option that does nothing is just noise.
   *
   *  Needs review is deliberately absent: a property there is resolved by
   *  picking a home in its own dialog (onResolveReview), which also clears
   *  the review flag — a plain move would leave it flagged and it would
   *  stay in the queue looking unmoved. */
  function moveOptionsFor(property: PropertyRecord): MoveOption<MoveTarget>[] {
    const options: MoveOption<MoveTarget>[] = [];
    if (property.review_status !== "accepted") {
      options.push({ value: "accepted", label: "Main", detail: "Inside the tracked areas" });
    }
    if (property.review_status !== "outsider") {
      options.push({ value: "outsider", label: "Outsider", detail: "Outside the tracked areas" });
    }
    options.push({
      value: "soldOut",
      label: "Sold out",
      detail: "Deal done — cancels site visits",
      danger: true,
    });
    return options;
  }

  function pickMoveTarget(property: PropertyRecord, target: MoveTarget) {
    setConfirmAction(
      target === "soldOut" ? { type: "soldOut", property } : { type: "move", property, target },
    );
  }

  /** Clicking the same row's Move button again closes the menu rather than
   *  re-opening it, so the button reads as a toggle (MoveMenu's own
   *  outside-press handler deliberately ignores its trigger for exactly
   *  this reason). */
  function openMoveMenu(property: PropertyRecord, anchor: HTMLElement) {
    setMoveMenu((prev) => (prev?.property.record_id === property.record_id ? null : { property, anchor }));
  }

  /** Delete means two different things depending on which list you're
   *  looking at: removing a live property, or erasing a sold-out record.
   *  Decided here, once, rather than in each of the table/card/detail call
   *  sites. */
  function onRowDelete(property: PropertyRecord) {
    setConfirmAction({ type: viewTab === "soldOut" ? "deleteSoldOut" : "delete", property });
  }

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
            {refreshing || soldOutRefreshing ? (
              <>
                <span className="spinner" style={{ width: 12, height: 12 }} /> Syncing…
              </>
            ) : lastUpdated ? (
              <>
                <span className="badge__dot" style={{ color: "var(--ok)" }} /> Updated {relativeTime(lastUpdated)}
              </>
            ) : null}
          </span>
          {/* Refreshes whichever list is actually on screen — on the Sold
              out view, re-fetching the property list would look like the
              button did nothing. */}
          <Button
            icon={<IconRefresh size={15} />}
            onClick={() => (viewTab === "soldOut" ? loadSoldOut(true) : load(true))}
            busy={viewTab === "soldOut" ? soldOutRefreshing : refreshing}
          >
            Refresh
          </Button>
          <Button variant="primary" icon={<IconPlus size={15} />} onClick={() => setFormDialog({ mode: "add" })}>
            Add property
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
          {/* Not part of "Stored" — a sold property has left the property
              table altogether, so adding it in would make these two tiles
              contradict each other. */}
          <Stat label="Sold out" value={soldOutCount ?? 0} icon={<IconCheckCircle size={13} />} delay={165} />
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
          value={viewTab === "needsReview" || viewTab === "soldOut" ? null : viewTab}
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

        {/* Its own toggle, alongside Needs review rather than inside the
            Main/Outsider capsule: those two are the homes a LIVE property
            picks between, and a sold property is in neither — it is not in
            the property table at all (see ViewTab's own comment). The count
            comes from the shared status poll, so it is right before this
            view has ever been opened. */}
        <Button
          size="sm"
          variant={viewTab === "soldOut" ? "primary" : "ghost"}
          icon={<IconCheckCircle size={14} />}
          onClick={() => setViewTab(viewTab === "soldOut" ? "main" : "soldOut")}
          title="Properties whose deal has closed"
        >
          Sold out{soldOutCount ? ` (${soldOutCount})` : ""}
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
      {view === "cards" && filterSource.length > 0 && (
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

      {(viewTab === "soldOut" ? soldOutError : error) && (
        <Note tone="bad" icon={<IconAlert size={17} />}>
          <strong>Backend unreachable.</strong> {viewTab === "soldOut" ? soldOutError : error} — the last loaded data
          is still shown below, and polling continues in the background.
        </Note>
      )}

      {loading && (
        <Panel>
          <div className="stack stack-3">
            <div className="row-flex faint small">
              <span className="spinner" /> Loading {viewTab === "soldOut" ? "sold-out properties" : "properties"}…
            </div>
            <SkeletonRows rows={6} />
          </div>
        </Panel>
      )}

      {viewTab === "soldOut" && soldOutProperties !== null && soldOutList.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconCheckCircle size={38} />}
            title="No sold-out properties yet"
            body="A property lands here when you close its deal — pick Move to → Sold out on any property. It is then removed from Properties, from client matches and from the landing page, and any agent holding a site visit for it is told the visit is cancelled."
          />
        </Panel>
      )}

      {viewTab !== "soldOut" && properties !== null && allProperties.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconInbox size={38} />}
            title="Nothing captured yet"
            body="Properties appear here automatically once a monitored chat receives a message that looks property-related. Check the Connection page to confirm something is being watched."
          />
        </Panel>
      )}

      {viewTab !== "soldOut" && allProperties.length > 0 && tabFiltered.length === 0 && (
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
              onOpenMoveMenu={openMoveMenu}
              onDelete={onRowDelete}
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
              onOpenMoveMenu={openMoveMenu}
              onDelete={onRowDelete}
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
          properties={filterSource}
          filter={filters[openFilter.key]}
          onChange={(next) => setColumnFilter(openFilter.key, next)}
          onClose={() => setOpenFilter(null)}
          sort={openColumn ? sortControlFor(openColumn) : undefined}
        />
      )}

      {moveMenu && (
        <MoveMenu<MoveTarget>
          anchorEl={moveMenu.anchor}
          options={moveOptionsFor(moveMenu.property)}
          onPick={(target) => pickMoveTarget(moveMenu.property, target)}
          onClose={() => setMoveMenu(null)}
        />
      )}

      {detailProperty && (
        <PropertyDetailDialog
          property={detailProperty}
          viewTab={viewTab}
          onResolveReview={handleResolveReview}
          onMove={(property, target) => setConfirmAction({ type: "move", property, target })}
          onMarkSoldOut={(property) => setConfirmAction({ type: "soldOut", property })}
          onDelete={onRowDelete}
          onEdit={(property) => openEdit(property)}
          onClose={() => setDetailId(null)}
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
          title={CONFIRM_COPY[confirmAction.type].title}
          body={
            <>
              <PropertySummary property={confirmAction.property} />
              {confirmAction.type === "move" ? (
                <p style={{ marginTop: 12 }}>
                  Move it to <strong>{confirmAction.target === "outsider" ? "Outsider" : "Main"}</strong>?
                </p>
              ) : confirmAction.type === "soldOut" ? (
                /* Spelled out rather than summarised: this is the one action
                   on this page with consequences outside it, and an operator
                   who only learns that a site visit was cancelled from the
                   toast afterwards has learned it too late. */
                <div style={{ marginTop: 12 }} className="stack stack-2">
                  <p>
                    Mark this property <strong>sold out</strong>? It will be moved out of Properties into the Sold out
                    view, and will no longer appear anywhere else:
                  </p>
                  <ul className="faint small" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6 }}>
                    <li>removed from every client's matches and hand-picked list</li>
                    <li>removed from the public landing page, if it was published</li>
                    <li>
                      every site visit currently out with an agent for it is cancelled, and each of those agents is
                      messaged on WhatsApp that the property is already sold
                    </li>
                  </ul>
                  <p className="faint small" style={{ margin: 0 }}>
                    Completed visits are kept as history. This cannot be undone.
                  </p>
                </div>
              ) : confirmAction.type === "deleteSoldOut" ? (
                <p style={{ marginTop: 12 }}>
                  This erases the sold-out record <strong>permanently</strong> — the property will not come back to
                  Properties. It cannot be undone.
                </p>
              ) : (
                <p style={{ marginTop: 12 }}>
                  This removes it from your database <strong>permanently</strong> — it cannot be undone.
                </p>
              )}
            </>
          }
          confirmLabel={CONFIRM_COPY[confirmAction.type].confirmLabel}
          tone={CONFIRM_COPY[confirmAction.type].tone}
          busy={actionBusy}
          onConfirm={
            confirmAction.type === "delete"
              ? confirmDelete
              : confirmAction.type === "move"
                ? confirmMove
                : confirmAction.type === "soldOut"
                  ? confirmSoldOut
                  : confirmDeleteSoldOut
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
  /** Opens the "Move to" menu against the button that was clicked — the row
   *  reports WHERE to anchor it, the page decides what it offers. */
  onOpenMoveMenu: (property: PropertyRecord, anchor: HTMLElement) => void;
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
  onOpenMoveMenu,
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
              // In the Sold out view neither tint applies — every row there
              // is sold, so marking some of them "outsider" or "flagged"
              // would highlight a distinction that no longer means anything.
              const flagged = property.needs_review && viewTab !== "soldOut";
              const outsider = property.review_status === "outsider" && viewTab !== "soldOut";
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
                  <td onClick={(event) => event.stopPropagation()}>
                    <RowActions
                      property={property}
                      viewTab={viewTab}
                      onAccept={onAccept}
                      onOpenMoveMenu={onOpenMoveMenu}
                      onDelete={onDelete}
                      onEdit={onEdit}
                    />
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
  onOpenMoveMenu,
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
              <ReviewBadge property={property} soldOut={viewTab === "soldOut"} />
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
                <RowActions
                  property={property}
                  viewTab={viewTab}
                  onAccept={onAccept}
                  onOpenMoveMenu={onOpenMoveMenu}
                  onDelete={onDelete}
                  onEdit={onEdit}
                />
              </div>
            </div>
          </Panel>
        );
      })}
    </div>
  );
}

/* ---------------------------------------------------------------- actions */

/** Edit, Move-to and Delete on a live property; Accept only while looking
 *  at the Needs review queue. Shared between the table's action cell and the
 *  card's action row so the icon set and behaviour never drift apart between
 *  layouts.
 *
 *  The Sold out view gets Delete alone. There is nothing to edit about a
 *  closed deal, and nowhere left to move it to — the deliberate absence of a
 *  route back to the live table (see soldout_property_service.
 *  delete_soldout_property) is why Move isn't offered here rather than
 *  offered and refused. */
function RowActions({
  property,
  viewTab,
  onAccept,
  onOpenMoveMenu,
  onDelete,
  onEdit,
}: {
  property: PropertyRecord;
  viewTab: ViewTab;
  onAccept: (property: PropertyRecord) => void;
  onOpenMoveMenu: (property: PropertyRecord, anchor: HTMLElement) => void;
  onDelete: (property: PropertyRecord) => void;
  onEdit: (property: PropertyRecord) => void;
}) {
  const soldOut = viewTab === "soldOut";
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
      {viewTab !== "needsReview" && !soldOut && (
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
      {!soldOut && (
        <button
          type="button"
          className="row-actions__btn"
          title="Move to…"
          aria-label="Move this property to another tab"
          aria-haspopup="menu"
          onClick={(event) => onOpenMoveMenu(property, event.currentTarget)}
        >
          <IconMove size={15} />
        </button>
      )}
      <button
        type="button"
        className="row-actions__btn row-actions__btn--danger"
        title={soldOut ? "Delete this sold-out record" : "Delete"}
        aria-label={soldOut ? "Delete this sold-out record" : "Delete this property"}
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
  onMarkSoldOut,
  onDelete,
  onEdit,
  onClose,
  selectAction,
}: {
  property: PropertyRecord;
  viewTab: ViewTab;
  /** Only set by the Properties page — backs the Needs review dialog's Move
   *  to Main / Move to Outsider buttons (clears needs_review AND sets
   *  review_status in one request). Undefined on the Landing Page page,
   *  which never shows a property with needs_review=true in the first
   *  place (see its own viewTab prop, always "main"/"outsider" there). */
  onResolveReview?: (property: PropertyRecord, targetStatus: "accepted" | "outsider") => void;
  /** The footer offers ONE move here — to whichever of Main/Outsider this
   *  property isn't in — rather than the row's full menu: a popover opened
   *  from inside this dialog would have to sit above its own scrim, and
   *  Escape would then ambiguously mean "close the menu" or "close the
   *  dialog". Two plain buttons say the same thing with none of that. */
  onMove: (property: PropertyRecord, target: "accepted" | "outsider") => void;
  /** Only set by the Properties page — the "Sold out" footer button. Left
   *  undefined by the Landing Page page, whose job is publishing, so the
   *  button simply doesn't render there (the action is always available from
   *  the Properties page's own Move menu). */
  onMarkSoldOut?: (property: PropertyRecord) => void;
  onDelete: (property: PropertyRecord) => void;
  onEdit: (property: PropertyRecord) => void;
  onClose: () => void;
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
  const photoCount = property.image_urls.length;

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

  const moveTarget: "accepted" | "outsider" = property.review_status === "outsider" ? "accepted" : "outsider";
  const movesTo = moveTarget === "accepted" ? "Main" : "Outsider";
  const soldOut = viewTab === "soldOut";
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
              <ReviewBadge property={property} soldOut={soldOut} />
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
          {photoCount > 0 && (
            <div className="detail__gallery">
              {property.image_urls.map((src, index) => (
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

          {soldOut && (
            <Note tone="ok" icon={<IconCheckCircle size={16} />}>
              <strong>Sold out</strong>
              {"sold_out_at" in property
                ? ` on ${(property as SoldOutPropertyRecord).formatted_sold_out_at}`
                : ""}
              . This property is no longer in your live database, in any client's matches, or on the landing page.
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
            {/* Needs review resolves itself by explicitly picking a home
                tab — no separate Accept step, and no dynamic single
                "Move to X" that only ever offers one direction. */}
            {viewTab === "needsReview" ? (
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
            ) : soldOut ? (
              /* Nothing to edit, and nowhere to move it to — the only thing
                 left to do with a closed deal is erase the record, which the
                 Delete button below already is. */
              null
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
                <Button variant="ghost" icon={<IconMove size={14} />} onClick={() => onMove(property, moveTarget)}>
                  Move to {movesTo}
                </Button>
                {onMarkSoldOut && (
                  <Button
                    variant="ghost"
                    icon={<IconCheckCircle size={14} />}
                    onClick={() => onMarkSoldOut(property)}
                    title="The deal is done — move this out of Properties and cancel any site visits"
                  >
                    Sold out
                  </Button>
                )}
              </>
            )}
            <Button className="btn--danger" icon={<IconTrash size={14} />} onClick={() => onDelete(property)}>
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
          <img src={property.image_urls[lightboxIndex]} alt={`Property photo ${lightboxIndex + 1}`} />
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
function ReviewBadge({ property, soldOut = false }: { property: PropertyRecord; soldOut?: boolean }) {
  // Outranks both badges below: for a sold property the home it used to sit
  // in, and whether it was once flagged for review, are history — "sold" is
  // the only thing about it that still decides anything.
  if (soldOut) {
    return <Badge tone="ok">Sold out</Badge>;
  }
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
