import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { agentApi } from "../api/agentApi";
import { inquiryClientApi } from "../api/inquiryClientApi";
import { matchingApi } from "../api/matchingApi";
import { propertyApi } from "../api/propertyApi";
import type { AgentSummary, PropertyRecord, VisitRecord } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { useDebounced } from "../hooks/useUi";
import { getCachedAgents, setCachedAgents } from "../lib/agentListCache";
import { friendlyError } from "../lib/apiError";
import { getCachedCompletedVisits, setCachedCompletedVisits } from "../lib/clientMatchCache";
import { formatCarpetArea, formatPrice, formatPricePerUnit, relativeTime } from "../lib/formatters";
import { setCachedPropertyList } from "../lib/propertyListCache";
import {
  compileFilters,
  countActiveFilters,
  FILTER_DEF_BY_KEY,
  sourceDetail,
  sourceLabel,
  type ColumnFilter,
  type FilterState,
} from "../lib/propertyFilters";
import { COLUMNS, compareNullable, FilterTrigger, Pager, type SortDir } from "./DashboardPage";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import PropertyReadOnlyDialog from "../components/PropertyReadOnlyDialog";
import { useToast } from "../components/ui/Toast";
import FilterPopover from "../components/ui/FilterPopover";
import RowRail from "../components/ui/RowRail";
import { Badge, Button, Copyable, EmptyState, Highlight, Note, Panel, SearchInput, Segmented, SkeletonRows, Stat } from "../components/ui/Primitives";
import { IconAlert, IconArrowRight, IconBuilding, IconCheck, IconChevron, IconInbox, IconSearch } from "../components/ui/Icons";

const REFRESH_INTERVAL_MS = 8000;
const FETCH_LIMIT = 500;
const PAGE_SIZE = 20;

/**
 * AgentManagement feature: "Add property" on ClientMatchesPage.tsx sends
 * the operator here — a dedicated copy of the Properties page (same
 * columns, search, filters, Main/Outsider split, stats) rather than a
 * "selection mode" bolted onto DashboardPage.tsx itself, so the two pages
 * can evolve independently and Properties never has to know this feature
 * exists. The one addition on top of everything Properties already does:
 * a checkbox per row, and a save action that syncs the checked set to
 * this client's manually-added properties (Backend/Database/
 * manual_property_models.py) — entirely separate from, and never
 * affecting, Client-Property Matching's own scored results.
 */
export default function SelectPropertyPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const toast = useToast();

  const params = new URLSearchParams(location.search);
  const clientPhone = params.get("forClient") ?? "";
  const clientName = params.get("clientName") || clientPhone;
  // Where to go when this page is done. The matches view is a dialog over
  // the Inquiries table now, not a page, so coming from there means going
  // back to /inquiries with a marker that re-opens it (see
  // InquiryClientsPage's own effect on ?matches=). The old
  // /inquiries/:phone/matches route still works, and anything that
  // arrived without the marker is sent back to it unchanged.
  const backHref =
    params.get("from") === "inquiries"
      ? `/inquiries?matches=${encodeURIComponent(clientPhone)}`
      : `/inquiries/${encodeURIComponent(clientPhone)}/matches`;

  const [properties, setProperties] = useState<PropertyRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const [search, setSearch] = useState("");
  const query = useDebounced(search, 180);
  const [reviewTab, setReviewTab] = useState<"main" | "outsider">("main");
  const [filters, setFilters] = useState<FilterState>({});
  const [openFilter, setOpenFilter] = useState<{ key: string; anchor: HTMLElement } | null>(null);
  const [page, setPage] = useState(1);
  // Received (IST) sort — newest first by default, same as the actual
  // Properties page's own "time" column (see DashboardPage's SORT_LABELS).
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const tableWrapRef = useRef<HTMLDivElement>(null);
  // Row click now opens a read-only dialog (property info + Select/
  // Deselect) instead of toggling selection directly — the rail's own
  // circle button (below) still toggles instantly without opening it.
  const [detailId, setDetailId] = useState<string | null>(null);

  // The saved baseline (what this client's manual list actually contains
  // right now) vs. the working set the operator is editing on this visit —
  // kept separate so "Save selection" can diff the two and only touch
  // what actually changed, rather than blindly re-adding everything.
  const [savedIds, setSavedIds] = useState<Set<string> | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  // Which of this client's properties can't be freely unselected — already
  // out with an agent for a visit, or already visited — seeded from the
  // same shared caches ClientMatchesDialog uses (this page is almost
  // always opened right from there) and always re-fetched fresh below, so
  // a property assigned or completed moments ago is never missed.
  const [agents, setAgents] = useState<AgentSummary[] | null>(() => getCachedAgents());
  const [completedVisits, setCompletedVisits] = useState<VisitRecord[] | null>(() =>
    clientPhone ? getCachedCompletedVisits(clientPhone) : null,
  );
  // Guards the two "leave this page" affordances (the header link and the
  // toolbar's own Back button below) — clicking either with unsaved
  // changes pending asks first, rather than silently discarding them.
  const [showLeaveConfirm, setShowLeaveConfirm] = useState(false);

  const load = useCallback(
    async (manual = false) => {
      setRefreshing(true);
      try {
        const data = await propertyApi.getProperties(FETCH_LIMIT);
        setProperties(data);
        setLastUpdated(new Date());
        setError(null);
        // Warms the shared property-list cache, same as ClientMatchesDialog
        // and AgentVisitsDialog — the read-only detail dialog opened by a
        // row click below checks that cache first, so it paints instantly
        // instead of paying its own ~1-2s single-property fetch.
        setCachedPropertyList(data, null);
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

  useEffect(() => {
    if (!clientPhone) return;
    let cancelled = false;
    inquiryClientApi
      .getManualProperties(clientPhone)
      .then((ids) => {
        if (cancelled) return;
        setSavedIds(new Set(ids));
        setSelected(new Set(ids));
      })
      .catch((err) => !cancelled && setError(friendlyError(err)));
    return () => {
      cancelled = true;
    };
  }, [clientPhone]);

  // Same "seed from cache, always refetch" contract as ClientMatchesDialog
  // (see lib/agentListCache.ts / lib/clientMatchCache.ts) — both failing
  // quietly here just means the unselect guard below falls back to
  // allowing everything, same as before this existed, rather than
  // blocking the page on a request neither is essential for.
  useEffect(() => {
    if (!clientPhone) return;
    let cancelled = false;
    agentApi
      .getAgents()
      .then((data) => {
        if (cancelled) return;
        setAgents(data);
        setCachedAgents(data);
      })
      .catch(() => {});
    matchingApi
      .getCompletedVisits(clientPhone)
      .then((visits) => {
        if (cancelled) return;
        setCompletedVisits(visits);
        setCachedCompletedVisits(clientPhone, visits);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [clientPhone]);

  const allProperties = useMemo(() => properties ?? [], [properties]);

  /** property_record_id → why it can't be unselected, for the toast below.
   *  Checked in this order so an agent-visit reason wins over a
   *  completed-visit one when (rare, but possible after a "Mark as still
   *  active") both are somehow true at once. */
  const lockReasonById = useMemo(() => {
    const reasons = new Map<string, string>();
    for (const agent of agents ?? []) {
      for (const active of agent.active_clients) {
        if (active.phone === clientPhone) reasons.set(active.property_record_id, "it's already assigned to an agent for a site visit");
      }
    }
    for (const visit of completedVisits ?? []) {
      if (visit.property_record_id && !reasons.has(visit.property_record_id)) {
        reasons.set(visit.property_record_id, "its visit has already been completed");
      }
    }
    return reasons;
  }, [agents, completedVisits, clientPhone]);
  const outsiderCount = useMemo(() => allProperties.filter((p) => p.review_status === "outsider").length, [allProperties]);
  const reviewFiltered = useMemo(
    () => allProperties.filter((p) => !p.needs_review && (reviewTab === "outsider" ? p.review_status === "outsider" : p.review_status === "accepted")),
    [allProperties, reviewTab],
  );

  const passesFilters = useMemo(() => compileFilters(filters), [filters]);
  const searched = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return reviewFiltered;
    return reviewFiltered.filter((p) =>
      [p.society_name, p.area_name, p.address, p.contact_name, p.contact_phone].filter(Boolean).join(" ").toLowerCase().includes(needle),
    );
  }, [reviewFiltered, query]);
  const visibleProperties = useMemo(() => {
    const filtered = searched.filter(passesFilters);
    const direction = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => compareNullable(a.message_timestamp, b.message_timestamp, direction));
  }, [searched, passesFilters, sortDir]);

  const pageCount = Math.max(1, Math.ceil(visibleProperties.length / PAGE_SIZE));
  const pageItems = useMemo(() => visibleProperties.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [visibleProperties, page]);
  useEffect(() => setPage(1), [reviewTab, query, filters, sortDir]);
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const setColumnFilter = useCallback((key: string, next: ColumnFilter | undefined) => {
    setFilters((prev) => {
      const merged = { ...prev };
      if (next === undefined) delete merged[key];
      else merged[key] = next;
      return merged;
    });
  }, []);
  const activeFilterCount = countActiveFilters(filters);

  function toggleSelect(recordId: string) {
    // Only unselecting is guarded — ticking a new property, even a locked
    // one, has no downside: it just joins the same list an assigned or
    // completed property is already on.
    if (selected.has(recordId)) {
      const reason = lockReasonById.get(recordId);
      if (reason) {
        const property = allProperties.find((p) => p.record_id === recordId);
        const label = property?.society_name || property?.area_name || "This property";
        toast.push({
          tone: "warn",
          title: "Can't remove this property",
          message: `${label} can't be unselected — ${reason}. Only properties that haven't been assigned yet can be removed here.`,
        });
        return;
      }
    }
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(recordId)) next.delete(recordId);
      else next.add(recordId);
      return next;
    });
  }

  const dirty = savedIds !== null && (selected.size !== savedIds.size || [...selected].some((id) => !savedIds.has(id)));

  /** Both "leave this page" affordances (the header link, the toolbar's
   *  own Back button) route through this — unsaved changes get a chance
   *  to be kept before they're discarded, saved changes never do. */
  function handleBackClick() {
    if (dirty) setShowLeaveConfirm(true);
    else navigate(backHref);
  }

  async function handleSave() {
    if (!savedIds || !clientPhone) return;
    setSaving(true);
    try {
      const toAdd = [...selected].filter((id) => !savedIds.has(id));
      const toRemove = [...savedIds].filter((id) => !selected.has(id));
      await Promise.all([
        ...toAdd.map((id) => inquiryClientApi.addManualProperty(clientPhone, id)),
        ...toRemove.map((id) => inquiryClientApi.removeManualProperty(clientPhone, id)),
      ]);
      toast.push({ tone: "ok", title: "Selection saved", message: `${selected.size} propert${selected.size === 1 ? "y" : "ies"} for ${clientName}.` });
      navigate(backHref);
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not save selection", message: friendlyError(err) });
    } finally {
      setSaving(false);
    }
  }

  const loading = properties === null && error === null;

  if (!clientPhone) {
    return (
      <div className="stack stack-5">
        <Panel>
          <EmptyState icon={<IconAlert size={38} />} title="No client specified" body="Open this page from a client's matches — use the Add property button there." />
        </Panel>
      </div>
    );
  }

  return (
    <div className="stack stack-5">
      <header className="section-head">
        <div>
          <button
            type="button"
            className="faint small"
            style={{ display: "inline-flex", alignItems: "center", gap: 4, background: "none", border: "none", padding: 0, font: "inherit", cursor: "pointer" }}
            onClick={handleBackClick}
          >
            ← Back to {clientName}'s matches
          </button>
          <div className="section-head__eyebrow">Step 4 — Manual selection</div>
          <h1 className="page-title">Select a property for {clientName}</h1>
          <p className="section-head__sub">
            The same list as Properties, with a checkbox — tick anything worth showing this client, matched or not, then save.
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
          <Button
            variant="primary"
            icon={<IconCheck size={15} />}
            onClick={handleSave}
            busy={saving}
            disabled={!dirty || savedIds === null}
          >
            Save selection{selected.size ? ` (${selected.size})` : ""}
          </Button>
          <Button variant="ghost" icon={<IconArrowRight size={15} className="icon-flip-x" />} onClick={handleBackClick}>
            Back
          </Button>
        </div>
      </header>

      {showLeaveConfirm && (
        <ConfirmDialog
          title="Discard unsaved changes?"
          body={
            <>
              Your selection changes for <strong>{clientName}</strong> haven't been saved yet — leaving now will discard
              them.
            </>
          }
          confirmLabel="Go back anyway"
          cancelLabel="Cancel"
          tone="danger"
          onConfirm={() => {
            setShowLeaveConfirm(false);
            navigate(backHref);
          }}
          onClose={() => setShowLeaveConfirm(false)}
        />
      )}

      {allProperties.length > 0 && (
        <div className="stat-grid">
          <Stat label="Stored" value={allProperties.length} icon={<IconBuilding size={13} />} delay={0} />
          <Stat label="Showing" value={visibleProperties.length} icon={<IconSearch size={13} />} tone="accent" delay={60} />
          <Stat label="Selected" value={selected.size} icon={<IconCheck size={13} />} tone={selected.size > 0 ? "ok" : undefined} delay={120} />
        </div>
      )}

      <div className="toolbar">
        <div className="toolbar__grow">
          <SearchInput value={search} onChange={setSearch} placeholder="Search society, area, address, contact…" ariaLabel="Search properties" />
        </div>

        <Segmented<"main" | "outsider">
          ariaLabel="Main or Outsider"
          value={reviewTab}
          onChange={setReviewTab}
          options={[
            { value: "main", label: "Main" },
            { value: "outsider", label: `Outsider${outsiderCount ? ` (${outsiderCount})` : ""}` },
          ]}
        />

        {activeFilterCount > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setFilters({})}>
            Reset filters
          </Button>
        )}
      </div>

      {error && (
        <Note tone="bad" icon={<IconAlert size={17} />}>
          <strong>Backend unreachable.</strong> {error} — the last loaded data is still shown below.
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
          <EmptyState icon={<IconInbox size={38} />} title="Nothing captured yet" body="Properties appear here once the WhatsApp pipeline picks something up." />
        </Panel>
      )}

      {allProperties.length > 0 && visibleProperties.length === 0 && (
        <Panel>
          <EmptyState icon={<IconSearch size={36} />} title="No matches" body="Nothing in this view matches the current search and filters." />
        </Panel>
      )}

      {visibleProperties.length > 0 && (
        <>
          <div className="table-with-rail" ref={tableWrapRef}>
            <RowRail containerRef={tableWrapRef} count={pageItems.length}>
              {(index) => {
                const property = pageItems[index];
                if (!property) return null;
                const isSelected = selected.has(property.record_id);
                return (
                  <button
                    type="button"
                    className={`select-toggle${isSelected ? " select-toggle--add" : ""}`}
                    onClick={() => toggleSelect(property.record_id)}
                    aria-pressed={isSelected}
                    aria-label={isSelected ? "Remove from selection" : "Add to selection"}
                  >
                    <IconCheck size={12} strokeWidth={2.4} />
                  </button>
                );
              }}
            </RowRail>

            <div className="table-frame anim-rise">
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      {COLUMNS.map((column) => (
                        <th
                          key={column.key}
                          aria-sort={
                            column.sort === "time" ? (sortDir === "asc" ? "ascending" : "descending") : undefined
                          }
                          style={column.numeric ? { textAlign: "right" } : undefined}
                        >
                          {column.filterKey ? (
                            <FilterTrigger
                              label={column.label}
                              filter={filters[column.filterKey]}
                              expanded={openFilter?.key === column.filterKey}
                              onOpen={(anchor) => setOpenFilter(openFilter?.key === column.filterKey ? null : { key: column.filterKey!, anchor })}
                            />
                          ) : column.sort === "time" ? (
                            <button
                              type="button"
                              onClick={() => setSortDir((dir) => (dir === "asc" ? "desc" : "asc"))}
                              title={sortDir === "asc" ? "Showing oldest first — click for newest first" : "Showing newest first — click for oldest first"}
                            >
                              {column.label}
                              <IconChevron size={12} className="sort-caret" />
                            </button>
                          ) : (
                            column.label
                          )}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pageItems.map((property) => {
                      const isSelected = selected.has(property.record_id);
                      return (
                        <tr
                          key={property.record_id}
                          data-rail-row=""
                          className={`row${isSelected ? " row--open" : ""}`}
                          tabIndex={0}
                          role="button"
                          aria-pressed={isSelected}
                          onClick={() => setDetailId(property.record_id)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              setDetailId(property.record_id);
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
                            {property.address ?? "—"}
                          </td>
                          <td>{property.bhk ?? "—"}</td>
                          <td>{property.property_type ?? "—"}</td>
                          <td>
                            <Badge tone={property.listing_type === "Rent" ? "info" : "ok"}>{property.listing_type}</Badge>
                          </td>
                          <td className="cell-num" style={{ textAlign: "right" }}>
                            {formatCarpetArea(property.carpet_area_sqft, property.carpet_area_unit)}
                          </td>
                          <td className="cell-num cell-strong" style={{ textAlign: "right" }} title={property.price_text ?? undefined}>
                            {formatPrice(property.price_text, property.price_amount_inr)}
                          </td>
                          <td className="cell-num" style={{ textAlign: "right" }} title={property.price_per_unit_text ?? undefined}>
                            {formatPricePerUnit(property.price_per_unit_text, property.price_per_unit_amount_inr)}
                          </td>
                          <td className="cell-truncate">
                            <Highlight text={property.contact_name ?? "—"} query={query} />
                            {property.contact_phone && (
                              <div className="cell-muted" onClick={(event) => event.stopPropagation()}>
                                <Copyable text={property.contact_phone} />
                              </div>
                            )}
                          </td>
                          <td className="cell-truncate" title={sourceDetail(property)}>
                            <span className="faint small" style={{ display: "block" }}>
                              {property.chat_type === "group" ? "Group" : "Personal"}
                            </span>
                            {sourceLabel(property)}
                          </td>
                          <td className="cell-num" style={{ whiteSpace: "nowrap" }}>
                            {property.formatted_timestamp}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          <Pager page={page} pageCount={pageCount} total={visibleProperties.length} onChange={setPage} />
        </>
      )}

      {openFilter && (
        <FilterPopover
          def={FILTER_DEF_BY_KEY[openFilter.key]}
          anchorEl={openFilter.anchor}
          properties={reviewFiltered}
          filter={filters[openFilter.key]}
          onChange={(next) => setColumnFilter(openFilter.key, next)}
          onClose={() => setOpenFilter(null)}
        />
      )}

      {detailId && (
        <PropertyReadOnlyDialog
          recordId={detailId}
          onClose={() => setDetailId(null)}
          selectAction={{
            selected: selected.has(detailId),
            locked: selected.has(detailId) ? (lockReasonById.get(detailId) ?? null) : null,
            onToggle: () => toggleSelect(detailId),
          }}
        />
      )}
    </div>
  );
}
