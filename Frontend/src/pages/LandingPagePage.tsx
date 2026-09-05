import { useCallback, useEffect, useMemo, useState } from "react";
import { propertyApi } from "../api/propertyApi";
import type { PropertyRecord } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { usePersistentState } from "../hooks/useUi";
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
import { useToast } from "../components/ui/Toast";
import FilterPopover from "../components/ui/FilterPopover";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import PropertyFormDialog from "../components/PropertyFormDialog";
import { COLUMNS, FilterTrigger, Pager, PropertyDetailDialog } from "./DashboardPage";
import { Badge, Button, Copyable, EmptyState, Note, Panel, Segmented, SkeletonRows } from "../components/ui/Primitives";
import { IconAlert, IconCheck, IconImage, IconInbox, IconInstagram, IconRefresh } from "../components/ui/Icons";

/**
 * Controls which properties are published to the public landing page (a
 * separate site, designed later — this page only manages the on/off switch
 * and the ordering logic feeding it).
 *
 * Deliberately narrow: only properties with at least one photo and/or an
 * Instagram reel are shown at all — a landing page needs something to
 * display, and text-only listings have nothing to show. Everything else
 * (the filters, the Main/Outsider split) mirrors the Properties page on
 * purpose, so switching between the two never feels like a different tool.
 */

const REFRESH_INTERVAL_MS = 8000;
const FETCH_LIMIT = 500;
const PAGE_SIZE = 20;

type PageTab = "ready" | "live";
type Category = "image" | "reel" | "both";

function categoryOf(property: PropertyRecord): Category {
  const hasImages = property.image_urls.length > 0;
  const hasReel = Boolean(property.instagram_reel_url);
  return hasImages && hasReel ? "both" : hasImages ? "image" : "reel";
}

function timeOf(value: string | null): number {
  return value ? new Date(value).getTime() : 0;
}

export default function LandingPagePage() {
  const toast = useToast();
  const [properties, setProperties] = useState<PropertyRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const [tab, setTab] = usePersistentState<PageTab>("landingPage.tab", "ready");
  const [reviewTab, setReviewTab] = usePersistentState<"main" | "outsider">("landingPage.reviewTab", "main");
  const [category, setCategory] = useState<Category | null>(null);
  const [filters, setFilters] = useState<FilterState>({});
  const [openFilter, setOpenFilter] = useState<{ key: string; anchor: HTMLElement } | null>(null);
  const [page, setPage] = useState(1);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmKind, setConfirmKind] = useState<"send" | "remove" | null>(null);
  const [busy, setBusy] = useState(false);

  const [detailId, setDetailId] = useState<string | null>(null);
  const [formDialog, setFormDialog] = useState<{ property: PropertyRecord } | null>(null);
  const [rowConfirm, setRowConfirm] = useState<{ type: "move" | "delete"; property: PropertyRecord } | null>(null);
  const [rowBusy, setRowBusy] = useState(false);

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

  // Switching tabs starts a fresh selection — "selected" means something
  // different on each one (about to send vs. about to remove), so carrying
  // it across would silently reinterpret it.
  useEffect(() => setSelected(new Set()), [tab]);

  const allProperties = useMemo(() => properties ?? [], [properties]);

  // The whole point of this page: only a property with something to show on
  // a landing page belongs here at all, and an unreviewed (needs_review)
  // property has no business going live before a human has looked at it.
  const qualifying = useMemo(
    () => allProperties.filter((p) => (p.image_urls.length > 0 || p.instagram_reel_url) && !p.needs_review),
    [allProperties],
  );

  const outsiderCount = useMemo(() => qualifying.filter((p) => p.review_status === "outsider").length, [qualifying]);

  const reviewFiltered = useMemo(
    () =>
      qualifying.filter((p) =>
        reviewTab === "outsider" ? p.review_status === "outsider" : p.review_status === "accepted",
      ),
    [qualifying, reviewTab],
  );

  const passesFilters = useMemo(() => compileFilters(filters), [filters]);

  const categoryFiltered = useMemo(
    () => reviewFiltered.filter((p) => passesFilters(p) && (category === null || categoryOf(p) === category)),
    [reviewFiltered, passesFilters, category],
  );

  // Ready to Add: properties with BOTH a photo and a reel lead, since those
  // make the strongest landing-page listing; within the rest, whichever
  // just gained a photo/reel (qualified_at) rises to the top. Removed
  // properties land back here without qualified_at being touched by the
  // remove itself, so they resume wherever their own qualifying moment
  // already put them rather than jumping to the top.
  const readyList = useMemo(() => {
    const list = categoryFiltered.filter((p) => !p.on_landing_page);
    return [...list].sort((a, b) => {
      const bothA = categoryOf(a) === "both" ? 1 : 0;
      const bothB = categoryOf(b) === "both" ? 1 : 0;
      if (bothA !== bothB) return bothB - bothA;
      const qa = timeOf(a.qualified_at) || timeOf(a.message_timestamp);
      const qb = timeOf(b.qualified_at) || timeOf(b.message_timestamp);
      return qb - qa;
    });
  }, [categoryFiltered]);

  // Live: most recently sent to the landing page first, oldest at the
  // bottom.
  const liveList = useMemo(() => {
    const list = categoryFiltered.filter((p) => p.on_landing_page);
    return [...list].sort((a, b) => timeOf(b.landing_page_updated_at) - timeOf(a.landing_page_updated_at));
  }, [categoryFiltered]);

  const activeList = tab === "ready" ? readyList : liveList;

  const pageCount = Math.max(1, Math.ceil(activeList.length / PAGE_SIZE));
  const pageItems = useMemo(() => activeList.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [activeList, page]);

  useEffect(() => setPage(1), [tab, reviewTab, category, filters]);
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

  function updateLocalProperty(recordId: string, next: PropertyRecord) {
    setProperties((prev) => (prev ? prev.map((p) => (p.record_id === recordId ? next : p)) : prev));
  }
  function removeLocalProperty(recordId: string) {
    setProperties((prev) => (prev ? prev.filter((p) => p.record_id !== recordId) : prev));
  }

  async function commitSelection() {
    if (!confirmKind || selected.size === 0) return;
    const wantsLive = confirmKind === "send";
    const ids = [...selected];
    setBusy(true);
    try {
      const results = await Promise.allSettled(ids.map((id) => propertyApi.updateProperty(id, { landing_page: wantsLive })));
      let okCount = 0;
      results.forEach((result, index) => {
        if (result.status === "fulfilled") {
          updateLocalProperty(ids[index], result.value);
          okCount += 1;
        }
      });
      const failedCount = ids.length - okCount;
      if (okCount > 0) {
        toast.push({
          tone: failedCount > 0 ? "warn" : "ok",
          title: wantsLive ? "Sent to the landing page" : "Removed from the landing page",
          message: `${okCount} propert${okCount === 1 ? "y" : "ies"} updated${failedCount ? `, ${failedCount} failed` : ""}.`,
        });
      } else {
        toast.push({ tone: "bad", title: "Couldn't update those properties", message: "Please try again." });
      }
      setSelected(new Set());
      setConfirmKind(null);
    } finally {
      setBusy(false);
    }
  }

  async function confirmRowMove() {
    if (!rowConfirm || rowConfirm.type !== "move") return;
    const property = rowConfirm.property;
    const nextStatus = property.review_status === "outsider" ? "accepted" : "outsider";
    setRowBusy(true);
    try {
      const updated = await propertyApi.updateProperty(property.record_id, { review_status: nextStatus });
      updateLocalProperty(property.record_id, updated);
      setDetailId(null);
      toast.push({ tone: "ok", title: "Moved", message: `Moved to ${nextStatus === "outsider" ? "Outsider" : "Main"}.` });
      setRowConfirm(null);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't move property", message: friendlyError(err) });
    } finally {
      setRowBusy(false);
    }
  }

  async function confirmRowDelete() {
    if (!rowConfirm || rowConfirm.type !== "delete") return;
    const property = rowConfirm.property;
    setRowBusy(true);
    try {
      await propertyApi.deleteProperty(property.record_id);
      removeLocalProperty(property.record_id);
      toast.push({ tone: "ok", title: "Deleted", message: "Property removed from your database permanently." });
      setRowConfirm(null);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't delete property", message: friendlyError(err) });
    } finally {
      setRowBusy(false);
    }
  }

  const detailProperty = useMemo(
    () => (detailId ? (allProperties.find((p) => p.record_id === detailId) ?? null) : null),
    [detailId, allProperties],
  );

  const loading = properties === null && error === null;

  return (
    <div className="stack stack-5">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Landing Page</div>
          <h1 className="page-title">Landing Page</h1>
          <p className="section-head__sub">
            Choose which properties show up on the public landing page. Only properties with at least one photo or an
            Instagram reel appear here — those are the only ones with something to show.
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

      <Segmented<PageTab>
        ariaLabel="Ready to Add or Live"
        value={tab}
        onChange={setTab}
        options={[
          { value: "ready", label: `Ready to Add${readyList.length ? ` (${readyList.length})` : ""}` },
          { value: "live", label: `Live${liveList.length ? ` (${liveList.length})` : ""}` },
        ]}
      />

      <div className="toolbar">
        <Segmented<"main" | "outsider">
          ariaLabel="Main or Outsider"
          value={reviewTab}
          onChange={setReviewTab}
          options={[
            { value: "main", label: "Main" },
            { value: "outsider", label: `Outsider${outsiderCount ? ` (${outsiderCount})` : ""}` },
          ]}
        />

        <Segmented<Category | "all">
          ariaLabel="Photos, Instagram, or both"
          value={category ?? "all"}
          onChange={(value) => setCategory(value === "all" ? null : value)}
          options={[
            { value: "all", label: "All" },
            { value: "image", label: "Photos", icon: <IconImage size={14} /> },
            { value: "reel", label: "Instagram", icon: <IconInstagram size={14} /> },
            { value: "both", label: "Both" },
          ]}
        />

        {activeFilterCount > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setFilters({})}>
            Reset filters
          </Button>
        )}

        <span style={{ marginLeft: "auto" }} />

        {selected.size > 0 && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() =>
              setSelected((prev) => (prev.size === activeList.length ? new Set() : new Set(activeList.map((p) => p.record_id))))
            }
          >
            {selected.size === activeList.length ? "Clear selection" : `Select all (${activeList.length})`}
          </Button>
        )}

        {tab === "ready" ? (
          <Button variant="primary" disabled={selected.size === 0} onClick={() => setConfirmKind("send")}>
            Send to landing page{selected.size ? ` (${selected.size})` : ""}
          </Button>
        ) : (
          <Button className="btn--danger" disabled={selected.size === 0} onClick={() => setConfirmKind("remove")}>
            Remove from landing page{selected.size ? ` (${selected.size})` : ""}
          </Button>
        )}
      </div>

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

      {!loading && qualifying.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconInbox size={38} />}
            title="Nothing with a photo or reel yet"
            body="Add photos or an Instagram reel link to a property from its Edit dialog on the Properties page, and it will show up here."
          />
        </Panel>
      )}

      {!loading && qualifying.length > 0 && activeList.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconInbox size={38} />}
            title={tab === "ready" ? "Nothing waiting to be added" : "Nothing live yet"}
            body={
              tab === "ready"
                ? "Every qualifying property is already on the landing page, or the current filters are hiding them."
                : "Nothing has been sent to the landing page yet — switch to Ready to Add to send some."
            }
          />
        </Panel>
      )}

      {activeList.length > 0 && (
        <>
          <div className="table-with-rail">
            <div className="row-icon-rail">
              {pageItems.map((property) => (
                <div key={property.record_id} className="row-icon-slot">
                  <button
                    type="button"
                    className={`select-toggle${selected.has(property.record_id) ? ` select-toggle--${tab === "ready" ? "add" : "remove"}` : ""}`}
                    onClick={() => toggleSelect(property.record_id)}
                    aria-pressed={selected.has(property.record_id)}
                    aria-label={selected.has(property.record_id) ? "Deselect this property" : "Select this property"}
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
                              onOpen={(anchor) =>
                                setOpenFilter(openFilter?.key === column.filterKey ? null : { key: column.filterKey!, anchor })
                              }
                            />
                          ) : (
                            column.label
                          )}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pageItems.map((property) => (
                      <tr
                        key={property.record_id}
                        className="row"
                        tabIndex={0}
                        role="button"
                        onClick={() => setDetailId(property.record_id)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            setDetailId(property.record_id);
                          }
                        }}
                      >
                        <td className="cell-truncate cell-strong" title={property.society_name ?? undefined}>
                          {property.society_name ?? "—"}
                        </td>
                        <td className="cell-truncate" title={property.area_name ?? undefined}>
                          {property.area_name ?? "—"}
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
                        <td
                          className="cell-num cell-strong"
                          style={{ textAlign: "right" }}
                          title={property.price_text ?? undefined}
                        >
                          {formatPrice(property.price_text, property.price_amount_inr)}
                        </td>
                        <td className="cell-num" style={{ textAlign: "right" }} title={property.price_per_unit_text ?? undefined}>
                          {formatPricePerUnit(property.price_per_unit_text, property.price_per_unit_amount_inr)}
                        </td>
                        <td className="cell-truncate">
                          {property.contact_name ?? "—"}
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
                          {sourceLabel(property)}
                        </td>
                        <td className="cell-num" style={{ whiteSpace: "nowrap" }}>
                          {property.formatted_timestamp}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
          <Pager page={page} pageCount={pageCount} total={activeList.length} onChange={setPage} />
        </>
      )}

      {detailProperty && (
        <PropertyDetailDialog
          property={detailProperty}
          viewTab={detailProperty.review_status === "outsider" ? "outsider" : "main"}
          onAccept={() => {}}
          onMove={(property) => setRowConfirm({ type: "move", property })}
          onDelete={(property) => setRowConfirm({ type: "delete", property })}
          onEdit={(property) => setFormDialog({ property })}
          onClose={() => setDetailId(null)}
          selectAction={{
            selected: selected.has(detailProperty.record_id),
            tone: tab === "ready" ? "add" : "remove",
            onToggle: () => toggleSelect(detailProperty.record_id),
          }}
        />
      )}

      {formDialog && (
        <PropertyFormDialog
          mode="edit"
          property={formDialog.property}
          onClose={() => setFormDialog(null)}
          onSaved={(saved) => {
            updateLocalProperty(saved.record_id, saved);
            setFormDialog(null);
          }}
        />
      )}

      {rowConfirm?.type === "move" && (
        <ConfirmDialog
          title={`Move to ${rowConfirm.property.review_status === "outsider" ? "Main" : "Outsider"}`}
          body={
            <p>
              Move "{rowConfirm.property.society_name ?? rowConfirm.property.area_name ?? "this property"}" to{" "}
              {rowConfirm.property.review_status === "outsider" ? "Main" : "Outsider"}?
            </p>
          }
          confirmLabel="Move"
          busy={rowBusy}
          onConfirm={confirmRowMove}
          onClose={() => setRowConfirm(null)}
        />
      )}

      {rowConfirm?.type === "delete" && (
        <ConfirmDialog
          title="Delete property"
          body={
            <p>
              Permanently delete "{rowConfirm.property.society_name ?? rowConfirm.property.area_name ?? "this property"}"?
              This cannot be undone.
            </p>
          }
          confirmLabel="Delete"
          tone="danger"
          busy={rowBusy}
          onConfirm={confirmRowDelete}
          onClose={() => setRowConfirm(null)}
        />
      )}

      {openFilter && (
        <FilterPopover
          def={FILTER_DEF_BY_KEY[openFilter.key]}
          anchorEl={openFilter.anchor}
          properties={qualifying}
          filter={filters[openFilter.key]}
          onChange={(next) => setColumnFilter(openFilter.key, next)}
          onClose={() => setOpenFilter(null)}
        />
      )}

      {confirmKind && (
        <ConfirmDialog
          title={confirmKind === "send" ? "Send to the landing page" : "Remove from the landing page"}
          body={
            <p>
              {confirmKind === "send"
                ? `This will send ${selected.size} selected propert${selected.size === 1 ? "y" : "ies"} to the landing page.`
                : `This will remove ${selected.size} selected propert${selected.size === 1 ? "y" : "ies"} from the landing page.`}
            </p>
          }
          confirmLabel={confirmKind === "send" ? "Send" : "Remove"}
          tone={confirmKind === "remove" ? "danger" : "default"}
          busy={busy}
          onConfirm={commitSelection}
          onClose={() => setConfirmKind(null)}
        />
      )}
    </div>
  );
}
