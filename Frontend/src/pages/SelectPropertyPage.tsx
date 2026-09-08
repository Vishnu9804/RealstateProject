import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { inquiryClientApi } from "../api/inquiryClientApi";
import { propertyApi } from "../api/propertyApi";
import type { PropertyRecord } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { useDebounced } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import { formatCarpetArea, formatPrice, formatPricePerUnit, relativeTime } from "../lib/formatters";
import {
  compileFilters,
  countActiveFilters,
  FILTER_DEF_BY_KEY,
  sourceDetail,
  sourceLabel,
  type ColumnFilter,
  type FilterState,
} from "../lib/propertyFilters";
import { COLUMNS, FilterTrigger, Pager } from "./DashboardPage";
import { useToast } from "../components/ui/Toast";
import FilterPopover from "../components/ui/FilterPopover";
import { Badge, Button, Copyable, EmptyState, Highlight, Note, Panel, SearchInput, Segmented, SkeletonRows, Stat } from "../components/ui/Primitives";
import { IconAlert, IconBuilding, IconCheck, IconInbox, IconSearch } from "../components/ui/Icons";

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
  const backHref = `/inquiries/${encodeURIComponent(clientPhone)}/matches`;

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

  // The saved baseline (what this client's manual list actually contains
  // right now) vs. the working set the operator is editing on this visit —
  // kept separate so "Save selection" can diff the two and only touch
  // what actually changed, rather than blindly re-adding everything.
  const [savedIds, setSavedIds] = useState<Set<string> | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  const load = useCallback(
    async (manual = false) => {
      setRefreshing(true);
      try {
        const data = await propertyApi.getProperties(FETCH_LIMIT);
        setProperties(data);
        setLastUpdated(new Date());
        setError(null);
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

  const allProperties = useMemo(() => properties ?? [], [properties]);
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
  const visibleProperties = useMemo(() => searched.filter(passesFilters), [searched, passesFilters]);

  const pageCount = Math.max(1, Math.ceil(visibleProperties.length / PAGE_SIZE));
  const pageItems = useMemo(() => visibleProperties.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [visibleProperties, page]);
  useEffect(() => setPage(1), [reviewTab, query, filters]);
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
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(recordId)) next.delete(recordId);
      else next.add(recordId);
      return next;
    });
  }

  const dirty = savedIds !== null && (selected.size !== savedIds.size || [...selected].some((id) => !savedIds.has(id)));

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
          <Link to={backHref} className="faint small" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            ← Back to {clientName}'s matches
          </Link>
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
        </div>
      </header>

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
          <div className="table-with-rail">
            <div className="row-icon-rail" aria-hidden="true">
              {pageItems.map((property) => (
                <div key={property.record_id} className="row-icon-slot">
                  <button
                    type="button"
                    className={`select-toggle${selected.has(property.record_id) ? " select-toggle--add" : ""}`}
                    onClick={() => toggleSelect(property.record_id)}
                    aria-pressed={selected.has(property.record_id)}
                    aria-label={selected.has(property.record_id) ? "Remove from selection" : "Add to selection"}
                  >
                    <IconCheck size={14} strokeWidth={2.4} />
                  </button>
                </div>
              ))}
            </div>

            <div className="table-frame anim-rise">
              <div className="table-scroll">
                <table className="table">
                  <thead>
                    <tr>
                      {COLUMNS.map((column) => (
                        <th key={column.key} style={column.numeric ? { textAlign: "right" } : undefined}>
                          {column.filterKey ? (
                            <FilterTrigger
                              label={column.label}
                              filter={filters[column.filterKey]}
                              expanded={openFilter?.key === column.filterKey}
                              onOpen={(anchor) => setOpenFilter(openFilter?.key === column.filterKey ? null : { key: column.filterKey!, anchor })}
                            />
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
                          className={`row${isSelected ? " row--open" : ""}`}
                          tabIndex={0}
                          role="button"
                          aria-pressed={isSelected}
                          onClick={() => toggleSelect(property.record_id)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              toggleSelect(property.record_id);
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
    </div>
  );
}
