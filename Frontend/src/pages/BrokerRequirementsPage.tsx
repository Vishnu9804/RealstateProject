import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { requirementApi } from "../api/requirementApi";
import type { BrokerRequirementRecord } from "../api/types";
import { useAppStatus } from "../state/StatusProvider";
import { useDebounced, usePersistentState } from "../hooks/useUi";
import { friendlyError } from "../lib/apiError";
import { formatCompactInr, relativeTime } from "../lib/formatters";
import { useToast } from "../components/ui/Toast";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import RequirementFormDialog from "../components/RequirementFormDialog";
import { Pager, compareNullable } from "./DashboardPage";
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
  IconRefresh,
  IconRuler,
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
 * click-a-row-to-open-the-dialog, same action buttons — because it is the
 * same job from the other direction, and an operator switching between the
 * two should not have to learn a second set of habits. What it deliberately
 * does NOT carry over is everything that only makes sense for supply: there
 * is no Main/Outsider split, no Needs-review queue, no duplicate comparison,
 * no photos and no Add button (a requirement only exists because someone
 * asked for it in a monitored chat).
 */

const FETCH_LIMIT = 500;
const PAGE_SIZE = 20;

type ViewMode = "table" | "cards";
type SortKey = "time" | "budget" | "size" | "area";
type SortDir = "asc" | "desc";

interface Column {
  key: string;
  label: string;
  sort?: SortKey;
  numeric?: boolean;
}

const COLUMNS: Column[] = [
  { key: "type", label: "Wanted" },
  { key: "bhk", label: "BHK" },
  { key: "areas", label: "Areas", sort: "area" },
  { key: "listingType", label: "Buy/Rent" },
  { key: "size", label: "Size", sort: "size", numeric: true },
  { key: "budget", label: "Budget", sort: "budget", numeric: true },
  { key: "contact", label: "Contact" },
  { key: "source", label: "Source" },
  { key: "time", label: "Received (IST)", sort: "time" },
];

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

function formatSize(requirement: BrokerRequirementRecord): string {
  const unit = requirement.carpet_area_unit ?? "sqft";
  const range = formatRange(requirement.carpet_area_min, requirement.carpet_area_max, (value) =>
    String(Math.round(value)),
  );
  return range ? `${range} ${unit}` : "—";
}

function areasLabel(requirement: BrokerRequirementRecord): string {
  if (requirement.preferred_areas.length > 0) return requirement.preferred_areas.join(", ");
  return requirement.area_name ?? "—";
}

function requirementTitle(requirement: BrokerRequirementRecord): string {
  const parts = [requirement.bhk, requirement.requirement_type].filter(Boolean).join(" ");
  return parts || requirement.society_name || areasLabel(requirement) || "Requirement";
}

function sourceLabel(requirement: BrokerRequirementRecord): string {
  return requirement.chat_type === "group" ? requirement.group_name : requirement.sender_saved_name || requirement.sender_name;
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
  const [view, setView] = usePersistentState<ViewMode>("requirements.view", "table");
  const [listingFilter, setListingFilter] = useState<"all" | "Sale" | "Rent">("all");
  const [sortKey, setSortKey] = useState<SortKey>("time");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [page, setPage] = useState(1);

  const [detailId, setDetailId] = useState<string | null>(null);
  const [editing, setEditing] = useState<BrokerRequirementRecord | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<BrokerRequirementRecord | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  const searchRef = useRef<HTMLInputElement>(null);
  const listTopRef = useRef<HTMLDivElement>(null);
  const firstPaint = useRef(true);
  // Which records existed at the previous refresh — anything new gets a
  // brief highlight so an arrival is noticeable without stealing focus.
  const seenIds = useRef<Set<string> | null>(null);
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());

  const { status: appStatus } = useAppStatus();

  const load = useCallback(
    async (manual = false) => {
      setRefreshing(true);
      try {
        const data = await requirementApi.getRequirements(FETCH_LIMIT);
        setRequirements(data);
        setLastUpdated(new Date());
        setError(null);

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
    [toast],
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

  const visibleRequirements = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = allRequirements.filter((requirement) => {
      if (listingFilter !== "all" && requirement.listing_type !== listingFilter) return false;
      if (!needle) return true;
      const haystack = [
        requirement.requirement_type,
        requirement.bhk,
        requirement.society_name,
        requirement.address,
        areasLabel(requirement),
        requirement.budget_text,
        requirement.furnishing,
        requirement.contact_name,
        requirement.contact_phone,
        requirement.description,
        sourceLabel(requirement),
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
        case "size":
          return compareNullable(a.carpet_area_min ?? a.carpet_area_max, b.carpet_area_min ?? b.carpet_area_max, direction);
        case "area":
          return compareNullable(a.area_name, b.area_name, direction);
        case "time":
        default:
          return compareNullable(a.message_timestamp, b.message_timestamp, direction);
      }
    });
  }, [allRequirements, query, listingFilter, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(visibleRequirements.length / PAGE_SIZE));
  const pageItems = useMemo(
    () => visibleRequirements.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE),
    [visibleRequirements, page],
  );

  useEffect(() => setPage(1), [query, listingFilter, sortKey, sortDir]);
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

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((dir) => (dir === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "area" ? "asc" : "desc");
    }
  }

  function applySaved(saved: BrokerRequirementRecord) {
    setRequirements((prev) => (prev ? prev.map((r) => (r.record_id === saved.record_id ? saved : r)) : prev));
    setEditing(null);
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

  return (
    <div className="stack stack-5">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Step 2 — Demand</div>
          <h1 className="page-title">Broker Requirements</h1>
          <p className="section-head__sub">
            What brokers are <strong>looking for</strong>, structured from the chats selected under Requirement
            monitoring on the Connection page. A message that reads as a requirement never becomes a property, and one
            message can produce several requirements — each gets its own row here.
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
            placeholder="Search area, BHK, budget, contact…  (press / )"
            ariaLabel="Search requirements"
          />
        </div>

        <Segmented<"all" | "Sale" | "Rent">
          ariaLabel="Buy or Rent"
          value={listingFilter}
          onChange={setListingFilter}
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

        {(search.trim() || listingFilter !== "all") && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setSearch("");
              setListingFilter("all");
            }}
          >
            Reset all
          </Button>
        )}
      </div>

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
            body="Requirements appear here automatically once a chat selected under Requirement monitoring receives a message asking for a property. Check the Connection page to confirm something is selected there."
          />
        </Panel>
      )}

      {allRequirements.length > 0 && visibleRequirements.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconSearch size={36} />}
            title="No matches"
            body={`None of the ${allRequirements.length} stored requirements match the current search and filter.`}
            action={
              <Button
                onClick={() => {
                  setSearch("");
                  setListingFilter("all");
                }}
              >
                Clear everything
              </Button>
            }
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
              freshIds={freshIds}
              onOpenDetail={(requirement) => setDetailId(requirement.record_id)}
              onEdit={setEditing}
              onDelete={setDeleteTarget}
            />
          ) : (
            <RequirementCards
              requirements={pageItems}
              query={query}
              freshIds={freshIds}
              onOpenDetail={(requirement) => setDetailId(requirement.record_id)}
              onEdit={setEditing}
              onDelete={setDeleteTarget}
            />
          )}
          <Pager page={page} pageCount={pageCount} total={visibleRequirements.length} onChange={setPage} />
        </>
      )}

      {detailRequirement && (
        <RequirementDetailDialog
          requirement={detailRequirement}
          onClose={() => setDetailId(null)}
          onEdit={(requirement) => setEditing(requirement)}
          onDelete={(requirement) => setDeleteTarget(requirement)}
        />
      )}

      {editing && (
        <RequirementFormDialog requirement={editing} onClose={() => setEditing(null)} onSaved={applySaved} />
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
                {formatBudget(deleteTarget)} · {formatSize(deleteTarget)} · {deleteTarget.listing_type === "Rent" ? "Rent" : "Buy"}
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
  onOpenDetail: (requirement: BrokerRequirementRecord) => void;
  onEdit: (requirement: BrokerRequirementRecord) => void;
  onDelete: (requirement: BrokerRequirementRecord) => void;
}

/** Edit and Delete, in that order, matching the Properties page's own row
 *  actions so the two tables' action columns line up visually and
 *  muscle-memory carries across. Shared between the table cell and the card
 *  footer so the two layouts can never drift apart. */
function RowActions({
  requirement,
  onEdit,
  onDelete,
}: {
  requirement: BrokerRequirementRecord;
  onEdit: (requirement: BrokerRequirementRecord) => void;
  onDelete: (requirement: BrokerRequirementRecord) => void;
}) {
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
      <button
        type="button"
        className="row-actions__btn row-actions__btn--danger"
        title="Delete"
        aria-label="Delete this requirement"
        onClick={() => onDelete(requirement)}
      >
        <IconTrash size={15} />
      </button>
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
  freshIds,
  onOpenDetail,
  onEdit,
  onDelete,
}: ListProps & {
  sortKey: SortKey;
  sortDir: SortDir;
  toggleSort: (key: SortKey) => void;
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
                    {column.sort ? (
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
                <td className="cell-num" style={{ textAlign: "right" }}>
                  {formatSize(requirement)}
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
                  {requirement.contact_phone && (
                    <div className="cell-muted">
                      <Copyable text={requirement.contact_phone} />
                    </div>
                  )}
                </td>
                <td className="cell-truncate" title={sourceDetail(requirement)}>
                  <span className="faint small" style={{ display: "block" }}>
                    {requirement.chat_type === "group" ? "Group" : "Personal"}
                  </span>
                  <Highlight text={sourceLabel(requirement)} query={query} />
                </td>
                <td className="cell-num" style={{ whiteSpace: "nowrap" }}>
                  {requirement.formatted_timestamp}
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

function RequirementCards({ requirements, query, freshIds, onOpenDetail, onEdit, onDelete }: ListProps) {
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
            {(requirement.carpet_area_min !== null || requirement.carpet_area_max !== null) && (
              <span className="fact">
                <IconRuler size={12} />
                {formatSize(requirement)}
              </span>
            )}
            {requirement.furnishing && (
              <span className="fact">
                <IconTag size={12} />
                {requirement.furnishing}
              </span>
            )}
          </div>

          {requirement.contact_phone && (
            <div className="fact" style={{ alignSelf: "flex-start" }}>
              <IconPhone size={12} />
              <Copyable text={requirement.contact_phone}>
                {requirement.contact_name
                  ? `${requirement.contact_name} · ${requirement.contact_phone}`
                  : requirement.contact_phone}
              </Copyable>
            </div>
          )}

          <div className="pcard__foot">
            <span className="cell-truncate" title={sourceDetail(requirement)}>
              <IconUsers size={11} /> {sourceLabel(requirement)} · {requirement.formatted_timestamp}
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
 * the original WhatsApp message — the point of a dedicated dialog is that
 * nothing about the requirement is left behind the click. The same Edit and
 * Delete actions available inline are repeated in the footer alongside
 * Cancel, so acting on a requirement never requires closing the dialog
 * first to reach them.
 */
function RequirementDetailDialog({
  requirement,
  onClose,
  onEdit,
  onDelete,
}: {
  requirement: BrokerRequirementRecord;
  onClose: () => void;
  onEdit: (requirement: BrokerRequirementRecord) => void;
  onDelete: (requirement: BrokerRequirementRecord) => void;
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

  const subtitle = [areasLabel(requirement), requirement.address].filter(Boolean).join(" · ");

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="Requirement details">
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">
              Requirement · {requirement.listing_type === "Rent" ? "Rent" : "Buy"}
            </div>
            <h2 className="detail-modal__title cell-truncate">{requirementTitle(requirement)}</h2>
            {subtitle && <div className="detail-modal__sub cell-truncate">{subtitle}</div>}
            <div className="detail-modal__badges">
              <Badge tone={requirement.listing_type === "Rent" ? "info" : "ok"}>
                {requirement.listing_type === "Rent" ? "Rent" : "Buy"}
              </Badge>
              {requirement.bhk && (
                <span className="fact">
                  <IconBuilding size={12} />
                  {requirement.bhk}
                </span>
              )}
              {(requirement.carpet_area_min !== null || requirement.carpet_area_max !== null) && (
                <span className="fact">
                  <IconRuler size={12} />
                  {formatSize(requirement)}
                </span>
              )}
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
              <div className="detail__k">Size wanted</div>
              <div className="detail__v">{formatSize(requirement)}</div>
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

            <div className="detail__block">
              <div className="detail__k">Furnishing</div>
              <div className="detail__v">{requirement.furnishing ?? "—"}</div>
            </div>

            <div className="detail__block">
              <div className="detail__k">Contact</div>
              <div className="detail__v">{requirement.contact_name ?? "—"}</div>
              {requirement.contact_phone && (
                <div className="detail__v" style={{ marginTop: 4 }}>
                  <Copyable text={requirement.contact_phone} />
                </div>
              )}
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
              <div className="detail__v">{sourceLabel(requirement)}</div>
              <div className="faint small" style={{ marginTop: 4 }}>
                {requirement.chat_type === "group" ? "Group" : "Personal"} · {requirement.formatted_timestamp}
              </div>
            </div>
          </div>

          {requirement.description && (
            <div className="detail__block">
              <div className="detail__k">Description</div>
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
            <Button variant="ghost" icon={<IconEdit size={14} />} onClick={() => onEdit(requirement)}>
              Edit
            </Button>
            <Button className="btn--danger" icon={<IconTrash size={14} />} onClick={() => onDelete(requirement)}>
              Delete
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
