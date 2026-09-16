import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { matchingApi } from "../api/matchingApi";
import { propertyApi } from "../api/propertyApi";
import type {
  BrokerRequirementRecord,
  MatchBucket,
  MatchedProperty,
  PropertyRecord,
  PropertySource,
  RequirementMatchResult,
} from "../api/types";
import { friendlyError } from "../lib/apiError";
import { formatArea, formatPrice, relativeTime } from "../lib/formatters";
import { getCachedPropertyList, patchCachedProperty, setCachedPropertyList } from "../lib/propertyListCache";
import { requirementTypes } from "../lib/requirementFilters";
import type { SharePropertyLike } from "../lib/propertyShareTemplate";
import { PropertyMatchDetailDialog, type DialogItem } from "./ClientMatchesDialog";
import ShareRequirementPropertiesDialog from "./ShareRequirementPropertiesDialog";
import SourceTag from "./ui/SourceTag";
import { useToast } from "./ui/Toast";
import { Badge, Button, EmptyState, Note, Segmented, SkeletonRows } from "./ui/Primitives";
import {
  IconAlert,
  IconBuilding,
  IconCheck,
  IconClock,
  IconInbox,
  IconMessage,
  IconPin,
  IconRefresh,
  IconRuler,
  IconSend,
  IconX,
} from "./ui/Icons";

/**
 * "Match properties" on the Broker Requirements page opens THIS — the
 * demand side's answer to ClientMatchesDialog.tsx.
 *
 * WHAT IT SHARES with the client-inquiry dialog, and why: the two Main /
 * Outsider drawers, the per-property-type row under them, the High / Medium
 * / Low sections, the score badges and the tick-then-send flow. A broker
 * requirement and a client inquiry are scored by literally the same engine
 * (see Backend/Service/BrokerRequirementService/requirement_matching_service.py),
 * so presenting the result differently would be a lie about how it was
 * produced. That is exactly why the type row belongs here too: a
 * requirement naming several acceptable types ("Flat, Row House") is scored
 * once per type, the same way a client's multi-select is, and each card
 * carries the type it matched under.
 *
 * WHAT IT DELIBERATELY DOES NOT HAVE: any agent assignment. No agent
 * picker, no hand-off, no "Assigned to" badge, no Clear-assignments, no
 * Completed tab. A site visit is arranged for a CLIENT of ours; a broker
 * asking in a group is a peer, and there is nobody to hand them over to.
 * The only outbound action here is sending them the shortlist they asked
 * for — which is why the footer holds exactly one primary button.
 *
 * It also has no "Add property by hand": a requirement's shortlist is
 * whatever the scoring found, and a property that should have been in it
 * belongs in the property table, not stapled to one requirement.
 */

/** The two drawers a matchable property can be filed under, exactly as
 *  ClientMatchesDialog defines them — there is no "needs review" drawer
 *  because a flagged property is never scored at all. */
type PropertyCategory = "main" | "outsider";

const CATEGORY_LABEL: Record<PropertyCategory, string> = { main: "Main", outsider: "Outsider" };

const BUCKET_ORDER: MatchBucket[] = ["high", "medium", "low"];
const BUCKET_LABEL: Record<MatchBucket, string> = {
  high: "High matches",
  medium: "Medium matches",
  low: "Low matches",
};
const BUCKET_TONE: Record<MatchBucket, "ok" | "warn" | "bad"> = { high: "ok", medium: "warn", low: "bad" };

/** The type row's "everything" option — a value no real type can have.
 *  Same sentinel, for the same reason, as ClientMatchesDialog's. */
const ALL_TYPES = "__all__";

function sameType(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

/** One card. `property` is the full record when the property list has
 *  landed and `match` is always present — unlike the client dialog there
 *  are no hand-picked or website-enquired cards here, so every card is a
 *  scored match. `source` says whether it is a property or a builder
 *  project (both are matched); a builder project never has a `property`
 *  record — its card and detail view read the match's own fields. */
interface MatchItem {
  recordId: string;
  bucket: MatchBucket;
  category: PropertyCategory;
  source: PropertySource;
  match: MatchedProperty;
  property: PropertyRecord | null;
}

/** Read the drawer off whatever is freshest — the live property record when
 *  we have it, the match's own copy of review_status otherwise. The
 *  match's cached `property_category` is a snapshot from scoring time and
 *  is deliberately never consulted, same as in ClientMatchesDialog. */
function categoryOf(record: { review_status: "accepted" | "outsider" }): PropertyCategory {
  return record.review_status === "outsider" ? "outsider" : "main";
}

/** What a "move to X" writes — the same patch ClientMatchesDialog's own
 *  move buttons send (filing a property into a drawer resolves its review
 *  flag). */
function categoryPatch(target: PropertyCategory): { review_status: "accepted" | "outsider"; needs_review: boolean } {
  return { review_status: target === "outsider" ? "outsider" : "accepted", needs_review: false };
}

/** Rewrite the category fields on every copy of one property inside a
 *  match result, leaving scores untouched — so a card whose full record
 *  never loaded still moves drawers with everything else. */
function patchMatchFields(result: RequirementMatchResult, updated: PropertyRecord): RequirementMatchResult {
  const patch = (list: MatchedProperty[]) =>
    list.map((match) =>
      match.record_id === updated.record_id
        ? {
            ...match,
            review_status: updated.review_status,
            needs_review: updated.needs_review,
            property_category: categoryOf(updated),
          }
        : match,
    );
  return { ...result, high: patch(result.high), medium: patch(result.medium), low: patch(result.low) };
}

export default function RequirementMatchesDialog({
  requirement,
  onClose,
  onMatchesLoaded,
}: {
  requirement: BrokerRequirementRecord;
  onClose: () => void;
  /** Fired with how many properties this dialog lists every time a result
   *  arrives (open and Refresh), so the page's Matches column can show the
   *  number the catch-up just produced without a request of its own. */
  onMatchesLoaded?: (recordId: string, total: number) => void;
}) {
  const toast = useToast();
  // Held in a ref so a new function identity from the page never changes
  // `load` below — which would re-run its effect and re-fetch in a loop.
  const onMatchesLoadedRef = useRef(onMatchesLoaded);
  onMatchesLoadedRef.current = onMatchesLoaded;
  const [result, setResult] = useState<RequirementMatchResult | null>(null);
  // Shares the Properties page's own list and cache, so a recently-opened
  // Properties page means the full records are already here. Purely
  // additive: a card renders from the match's own display fields without
  // it, which is why it never gates the dialog.
  const [properties, setProperties] = useState<PropertyRecord[] | null>(() => getCachedPropertyList()?.data ?? null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [category, setCategory] = useState<PropertyCategory>("main");
  // null = every type. Only meaningful for a requirement that named more
  // than one acceptable type — see requirementTypeList below.
  const [typeView, setTypeView] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [shareOpen, setShareOpen] = useState(false);
  // The card whose full property details are open on top of this dialog —
  // an id, not the item, so a refresh keeps it current and a property that
  // drops out of the result closes it on its own.
  const [openItemId, setOpenItemId] = useState<string | null>(null);
  const [movingId, setMovingId] = useState<string | null>(null);

  const load = useCallback(
    (manual = false) => {
      if (manual) setRefreshing(true);
      setError(null);
      // An open reads the stored matches (brought current by the backend);
      // Refresh asks for a full re-score, same as the client matches dialog.
      (manual
        ? matchingApi.recomputeRequirementMatches(requirement.record_id)
        : matchingApi.getRequirementMatches(requirement.record_id)
      )
        .then((fresh) => {
          setResult(fresh);
          onMatchesLoadedRef.current?.(
            requirement.record_id,
            fresh.high.length + fresh.medium.length + fresh.low.length,
          );
        })
        .catch((err) => setError(friendlyError(err)))
        .finally(() => {
          setLoading(false);
          setRefreshing(false);
        });

      // Allowed to fail quietly — see the `properties` state comment.
      propertyApi
        .getProperties(500)
        .then((data) => {
          setProperties(data);
          setCachedPropertyList(data, null);
        })
        .catch(() => {});
    },
    [requirement.record_id],
  );

  useEffect(() => {
    load();
  }, [load]);

  // The share dialog or a property's detail view is the top-most layer
  // while it is open and answers Escape / outside clicks itself.
  const nestedOpen = shareOpen || openItemId !== null;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !nestedOpen) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, nestedOpen]);

  const propertiesById = useMemo(() => {
    const map = new Map<string, PropertyRecord>();
    for (const property of properties ?? []) map.set(property.record_id, property);
    return map;
  }, [properties]);

  const items = useMemo<MatchItem[]>(() => {
    const out: MatchItem[] = [];
    for (const bucket of BUCKET_ORDER) {
      for (const match of result?.[bucket] ?? []) {
        const source: PropertySource = match.property_source ?? "property";
        const property = source === "property" ? (propertiesById.get(match.record_id) ?? null) : null;
        out.push({
          recordId: match.record_id,
          bucket,
          // A builder project is always "accepted", so it files under Main.
          category: categoryOf(property ?? match),
          source,
          match,
          property,
        });
      }
    }
    return out;
  }, [result, propertiesById]);

  const countByCategory = useMemo(() => {
    const counts: Record<PropertyCategory, number> = { main: 0, outsider: 0 };
    for (const item of items) counts[item.category] += 1;
    return counts;
  }, [items]);

  const visibleItems = useMemo(() => items.filter((item) => item.category === category), [items, category]);

  /** The types this requirement would accept, when it named more than one
   *  ("Flat, Row House") — each gets a capsule of its own under the
   *  Main/Outsider row, exactly as a client's do in ClientMatchesDialog.
   *  Read with the page's own splitter, so the capsules here and the Type
   *  column's filter over there can never disagree about what the field
   *  says. The backend tags every scored match with the type it matched
   *  under (MatchedProperty.matched_type — see requirement_matching_service's
   *  _type_plan), so a capsule is an exact split, not a guess made here. */
  const requirementTypeList = useMemo(() => requirementTypes(requirement), [requirement]);
  const showTypeTabs = requirementTypeList.length > 1;
  const typeFilter =
    showTypeTabs && typeView !== null && requirementTypeList.some((type) => sameType(type, typeView))
      ? typeView
      : null;

  /** A match scored before per-type scoring existed carries no matched_type.
   *  It stays on every capsule rather than vanishing from all of them — the
   *  same rule the client dialog applies, and it is what keeps a requirement
   *  readable in the moment between this shipping and its next re-score. */
  const typeVisibleItems = useMemo(
    () =>
      typeFilter === null
        ? visibleItems
        : visibleItems.filter((item) => !item.match.matched_type || sameType(item.match.matched_type, typeFilter)),
    [visibleItems, typeFilter],
  );

  const countByType = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of visibleItems) {
      const type = item.match.matched_type?.trim().toLowerCase();
      if (type) counts.set(type, (counts.get(type) ?? 0) + 1);
    }
    return counts;
  }, [visibleItems]);

  const sections = useMemo(
    () =>
      BUCKET_ORDER.map((bucket) => ({
        bucket,
        items: typeVisibleItems.filter((item) => item.bucket === bucket).sort((a, b) => b.match.score - a.match.score),
      })).filter((section) => section.items.length > 0),
    [typeVisibleItems],
  );

  /** Only the ticked cards, and only ones still on screen somewhere — so a
   *  selection can never carry a property that has since dropped out of the
   *  result entirely (a refresh after it was deleted or flagged). */
  const selectedProperties = useMemo<SharePropertyLike[]>(
    () => items.filter((item) => selectedIds.has(item.recordId)).map((item) => item.property ?? item.match),
    [items, selectedIds],
  );

  // Kept in step with `items` for the same reason: the footer count and
  // what actually gets sent must be the same set.
  useEffect(() => {
    setSelectedIds((previous) => {
      if (previous.size === 0) return previous;
      const live = new Set(items.map((item) => item.recordId));
      const next = new Set([...previous].filter((id) => live.has(id)));
      return next.size === previous.size ? previous : next;
    });
  }, [items]);

  function toggleSelected(recordId: string) {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(recordId)) next.delete(recordId);
      else next.add(recordId);
      return next;
    });
  }

  /** The open card in the shape the client-inquiry property detail view
   *  takes, so both dialogs show a property identically. Nothing here is
   *  hand-picked, website-enquired or assigned, so those parts of that view
   *  simply never appear. */
  const openItem = useMemo<DialogItem | null>(() => {
    const item = items.find((candidate) => candidate.recordId === openItemId);
    if (!item) return null;
    return {
      recordId: item.recordId,
      section: item.bucket,
      category: item.category,
      source: item.source,
      match: item.match,
      property: item.property,
      handoff: item.property ?? item.match,
      fromWebsiteInquiry: false,
    };
  }, [items, openItemId]);

  /** Move one property between Main and Outsider — the same action, patch
   *  and local update ClientMatchesDialog's detail view performs. A drawer
   *  change alters nothing about how well it fits, so nothing is re-scored. */
  async function handleMove(recordId: string, target: PropertyCategory) {
    // Main/Outsider is a property's filing — a builder project has neither
    // (the detail view offers no move for one; this only guards it).
    if (items.find((item) => item.recordId === recordId)?.source === "builder_project") return;
    setMovingId(recordId);
    try {
      const updated = await propertyApi.updateProperty(recordId, categoryPatch(target));
      setProperties((previous) => {
        if (previous === null) return [updated];
        const index = previous.findIndex((p) => p.record_id === updated.record_id);
        if (index === -1) return [...previous, updated];
        const next = [...previous];
        next[index] = updated;
        return next;
      });
      patchCachedProperty(updated.record_id, updated);
      setResult((previous) => (previous ? patchMatchFields(previous, updated) : previous));
      setCategory(target);
      toast.push({
        tone: "ok",
        title: `Moved to ${CATEGORY_LABEL[target]}`,
        message: updated.society_name ?? updated.area_name ?? undefined,
      });
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not move property", message: friendlyError(err) });
    } finally {
      setMovingId(null);
    }
  }

  const total = items.length;

  return createPortal(
    <>
      <div
        className="modal-scrim"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget && !nestedOpen) onClose();
        }}
      >
        <div
          className="detail-modal detail-modal--wide matches-dialog anim-rise"
          role="dialog"
          aria-modal="true"
          aria-label="Properties matched for this requirement"
        >
          {/* Compact header (see .matches-dialog in app.css): the badges sit on
              the title's own line instead of a row of their own, so the cards
              below get that height back. Same layout as ClientMatchesDialog. */}
          <div className="detail-modal__head">
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="detail-modal__eyebrow">Matched properties</div>
              <div className="matches-dialog__title-row">
                <h2 className="detail-modal__title cell-truncate">
                  {requirement.contact_name || requirement.sender_saved_name || requirement.sender_name}
                </h2>
                <div className="detail-modal__badges">
                  <Badge tone="accent">
                    {total} propert{total === 1 ? "y" : "ies"}
                  </Badge>
                  {result?.computed_at && (
                    <span className="fact">
                      <IconClock size={12} /> Scored {relativeTime(new Date(result.computed_at))}
                    </span>
                  )}
                </div>
              </div>
              <div className="detail-modal__sub cell-truncate">
                {requirement.sender_phone}
                {result?.requirement_summary ? ` · ${result.requirement_summary}` : ""}
              </div>
            </div>
            <div className="row-flex" style={{ gap: 8, flex: "none" }}>
              <Button
                size="sm"
                variant="ghost"
                icon={<IconRefresh size={14} />}
                onClick={() => load(true)}
                busy={refreshing}
              >
                Refresh
              </Button>
              <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
                <IconX size={15} />
              </button>
            </div>
          </div>

          {/* Pinned above the tabs rather than inside the scrolling body, so
              what the broker actually asked for stays readable against
              every card below it. */}
          {requirement.description?.trim() && (
            <div className="matches-dialog__brief" role="note" aria-label="Requirement description">
              <div className="matches-dialog__brief-k">
                <IconMessage size={12} /> Requirement description
              </div>
              <div className="matches-dialog__brief-v">{requirement.description.trim()}</div>
            </div>
          )}

          <div className="matches-dialog__tabs">
            <Segmented<PropertyCategory>
              ariaLabel="Which properties to show"
              value={category}
              onChange={setCategory}
              options={(["main", "outsider"] as PropertyCategory[]).map((key) => ({
                value: key,
                label: `${CATEGORY_LABEL[key]}${countByCategory[key] ? ` (${countByCategory[key]})` : ""}`,
              }))}
            />
            {selectedIds.size > 0 && (
              <span className="faint small" style={{ marginLeft: "auto" }}>
                {selectedIds.size} selected
              </span>
            )}
          </div>

          {/* Shown only when the broker named more than one acceptable type
              — the same row, the same styling and the same behaviour as the
              client-inquiry dialog's, because it is the same question being
              asked of the same scoring. */}
          {showTypeTabs && (
            <div className="matches-dialog__tabs matches-dialog__tabs--types">
              <span className="section-head__eyebrow" style={{ marginBottom: 0 }}>
                Property type
              </span>
              <Segmented<string>
                ariaLabel="Which of the requirement's property types to show"
                value={typeFilter ?? ALL_TYPES}
                onChange={(value) => setTypeView(value === ALL_TYPES ? null : value)}
                options={[
                  {
                    value: ALL_TYPES,
                    label: `All${visibleItems.length ? ` (${visibleItems.length})` : ""}`,
                  },
                  ...requirementTypeList.map((type) => {
                    const count = countByType.get(type.toLowerCase()) ?? 0;
                    return { value: type, label: `${type}${count ? ` (${count})` : ""}` };
                  }),
                ]}
              />
            </div>
          )}

          <div className="detail-modal__body">
            {error && (
              <Note tone="bad" icon={<IconAlert size={16} />}>
                {error}
              </Note>
            )}

            {loading && <SkeletonRows rows={5} />}

            {!loading && result && !result.has_requirements && (
              <EmptyState
                icon={<IconInbox size={36} />}
                title="Nothing to match on"
                body="This requirement doesn't state a property type, BHK, budget or area, so there is nothing to compare stored properties against. Edit it and add what the broker asked for, then refresh."
              />
            )}

            {!loading && result?.has_requirements && total === 0 && (
              <EmptyState
                icon={<IconInbox size={36} />}
                title="No properties matched yet"
                body="No stored property scored high enough against this requirement. New listings are scored the moment you reopen this."
              />
            )}

            {!loading && total > 0 && sections.length === 0 && (
              <EmptyState
                icon={<IconInbox size={36} />}
                title={
                  typeFilter
                    ? `No ${typeFilter} matches in ${CATEGORY_LABEL[category]}`
                    : `Nothing in ${CATEGORY_LABEL[category]}`
                }
                body={
                  typeFilter
                    ? "Nothing here matched that part of the requirement yet — try one of its other property types above, or the other tab."
                    : "Every property matched for this requirement is filed under the other tab above."
                }
              />
            )}

            {!loading &&
              sections.map((section) => (
                <div key={section.bucket} className="stack stack-3">
                  <div className="matches-dialog__section-head">
                    <span className="section-head__eyebrow" style={{ marginBottom: 0 }}>
                      {BUCKET_LABEL[section.bucket]}
                    </span>
                    <span className="matches-dialog__section-count">{section.items.length}</span>
                    <span className="matches-dialog__rule" />
                  </div>
                  <div className="matches-grid">
                    {section.items.map((item) => (
                      <RequirementMatchCard
                        key={item.recordId}
                        item={item}
                        selected={selectedIds.has(item.recordId)}
                        onToggleSelect={() => toggleSelected(item.recordId)}
                        onOpen={() => setOpenItemId(item.recordId)}
                      />
                    ))}
                  </div>
                </div>
              ))}
          </div>

          <div className="detail-modal__foot">
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
            <span style={{ marginLeft: "auto" }}>
              <Button
                variant="primary"
                icon={<IconSend size={15} />}
                onClick={() => setShareOpen(true)}
                disabled={selectedIds.size === 0}
              >
                Send details on WhatsApp ({selectedIds.size})
              </Button>
            </span>
          </div>
        </div>
      </div>

      {/* A requirement's shortlist has no agents and no hand-picked cards,
          so the view is given no assigned agent and no Remove action. */}
      {openItem && (
        <PropertyMatchDetailDialog
          item={openItem}
          assignedAgent={null}
          moving={movingId === openItem.recordId}
          removing={false}
          selected={selectedIds.has(openItem.recordId)}
          onToggleSelect={() => toggleSelected(openItem.recordId)}
          onMove={(target) => handleMove(openItem.recordId, target)}
          onClose={() => setOpenItemId(null)}
        />
      )}

      {shareOpen && (
        <ShareRequirementPropertiesDialog
          requirement={requirement}
          properties={selectedProperties}
          onClose={() => setShareOpen(false)}
          onBack={() => setShareOpen(false)}
          onSent={() => setSelectedIds(new Set())}
        />
      )}
    </>,
    document.body,
  );
}

/* ========================================================================
   Card
   ======================================================================== */

/**
 * Written here rather than shared with ClientMatchesDialog's own card on
 * purpose: that one is built around a DialogItem that carries hand-picked /
 * website-enquiry / assigned-agent state which does not exist on this side
 * at all. Generalising it to cover both would have meant threading four
 * always-absent props through the client dialog's hot path to save a small
 * amount of markup here.
 */
function RequirementMatchCard({
  item,
  selected,
  onToggleSelect,
  onOpen,
}: {
  item: MatchItem;
  selected: boolean;
  onToggleSelect: () => void;
  /** Opens the property's full details, exactly as a card does in
   *  ClientMatchesDialog — selecting is the corner checkbox's job alone. */
  onOpen: () => void;
}) {
  const source = item.property ?? item.match;
  const title = source.society_name || source.property_type || "Property";
  const location = [source.area_name, source.address].filter(Boolean).join(" · ");

  return (
    <div
      className={["match-card", selected && "match-card--selected"].filter(Boolean).join(" ")}
      role="button"
      tabIndex={0}
      aria-label={`View details of ${title}`}
      onClick={onOpen}
      onKeyDown={(event) => {
        // Only the card's own keys — a key pressed on the checkbox inside
        // must toggle it, not also open the details.
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
    >
      <button
        type="button"
        className={`select-toggle match-card__check${selected ? " select-toggle--add" : ""}`}
        aria-pressed={selected}
        aria-label={selected ? `Deselect ${title}` : `Select ${title}`}
        onClick={(event) => {
          event.stopPropagation();
          onToggleSelect();
        }}
      >
        <IconCheck size={12} strokeWidth={2.4} />
      </button>

      <div className="match-card__head">
        <div style={{ minWidth: 0 }}>
          <SourceTag source={item.source} />
          <div className="pcard__title cell-truncate">{title}</div>
          {location && <div className="pcard__sub cell-truncate">{location}</div>}
        </div>
        <span className={`match-card__score match-card__score--${item.match.bucket}`}>
          {Math.round(item.match.score * 100)}%
        </span>
      </div>

      <div className="match-card__facts">
        {(source.property_type || source.bhk) && (
          <span className="fact">
            <IconBuilding size={12} />
            {[source.bhk, source.property_type].filter(Boolean).join(" ")}
          </span>
        )}
        {source.area_name && (
          <span className="fact">
            <IconPin size={12} />
            {source.area_name}
          </span>
        )}
        {formatArea(source.area_sqft, source.area_vaar) !== "—" && (
          <span className="fact">
            <IconRuler size={12} />
            {formatArea(source.area_sqft, source.area_vaar)}
          </span>
        )}
      </div>

      <div className="match-card__badges">
        <Badge tone={BUCKET_TONE[item.match.bucket]}>{BUCKET_LABEL[item.match.bucket].replace(" matches", " match")}</Badge>
        {/* Only ever set for a requirement that named more than one type —
            says which of them this property answers. */}
        {item.match.matched_type && <Badge tone="accent">For {item.match.matched_type}</Badge>}
        {item.match.is_partial_match && <Badge tone="info">Partial data</Badge>}
      </div>

      {item.match.reason && <div className="match-card__reason">{item.match.reason}</div>}

      <div className="match-card__foot">
        <span className="pcard__price">{formatPrice(source.price_text, source.price_amount_inr)}</span>
        <span className="faint small">{source.listing_type}</span>
      </div>
    </div>
  );
}
