import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { matchingApi } from "../api/matchingApi";
import { propertyApi } from "../api/propertyApi";
import type {
  BrokerRequirementRecord,
  MatchBucket,
  MatchedProperty,
  PropertyRecord,
  RequirementMatchResult,
} from "../api/types";
import { friendlyError } from "../lib/apiError";
import { formatCarpetArea, formatPrice, relativeTime } from "../lib/formatters";
import { getCachedPropertyList, setCachedPropertyList } from "../lib/propertyListCache";
import type { SharePropertyLike } from "../lib/propertyShareTemplate";
import ShareRequirementPropertiesDialog from "./ShareRequirementPropertiesDialog";
import { Badge, Button, EmptyState, Note, Segmented, SkeletonRows } from "./ui/Primitives";
import {
  IconAlert,
  IconBuilding,
  IconCheck,
  IconClock,
  IconInbox,
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
 * Outsider drawers, the High / Medium / Low sections, the score badges and
 * the tick-then-send flow. A broker requirement and a client inquiry are
 * scored by literally the same engine (see Backend/Service/
 * ClientPropertyMatchingService/requirement_matching_service.py), so
 * presenting the result differently would be a lie about how it was
 * produced.
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

/** One card. `property` is the full record when the property list has
 *  landed and `match` is always present — unlike the client dialog there
 *  are no hand-picked or website-enquired cards here, so every card is a
 *  scored match. */
interface MatchItem {
  recordId: string;
  bucket: MatchBucket;
  category: PropertyCategory;
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

export default function RequirementMatchesDialog({
  requirement,
  onClose,
}: {
  requirement: BrokerRequirementRecord;
  onClose: () => void;
}) {
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
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [shareOpen, setShareOpen] = useState(false);

  const load = useCallback(
    (manual = false) => {
      if (manual) setRefreshing(true);
      setError(null);
      matchingApi
        .getRequirementMatches(requirement.record_id)
        .then(setResult)
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

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      // The share dialog is the top-most layer while it is open and
      // answers Escape itself.
      if (event.key === "Escape" && !shareOpen) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, shareOpen]);

  const propertiesById = useMemo(() => {
    const map = new Map<string, PropertyRecord>();
    for (const property of properties ?? []) map.set(property.record_id, property);
    return map;
  }, [properties]);

  const items = useMemo<MatchItem[]>(() => {
    const out: MatchItem[] = [];
    for (const bucket of BUCKET_ORDER) {
      for (const match of result?.[bucket] ?? []) {
        const property = propertiesById.get(match.record_id) ?? null;
        out.push({
          recordId: match.record_id,
          bucket,
          category: categoryOf(property ?? match),
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

  const sections = useMemo(
    () =>
      BUCKET_ORDER.map((bucket) => ({
        bucket,
        items: visibleItems.filter((item) => item.bucket === bucket).sort((a, b) => b.match.score - a.match.score),
      })).filter((section) => section.items.length > 0),
    [visibleItems],
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

  const total = items.length;

  return createPortal(
    <>
      <div
        className="modal-scrim"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget && !shareOpen) onClose();
        }}
      >
        <div
          className="detail-modal detail-modal--wide anim-rise"
          role="dialog"
          aria-modal="true"
          aria-label="Properties matched for this requirement"
        >
          <div className="detail-modal__head">
            <div style={{ minWidth: 0 }}>
              <div className="detail-modal__eyebrow">Matched properties</div>
              <h2 className="detail-modal__title cell-truncate">
                {requirement.contact_name || requirement.sender_saved_name || requirement.sender_name}
              </h2>
              <div className="detail-modal__sub cell-truncate">
                {requirement.sender_phone}
                {result?.requirement_summary ? ` · ${result.requirement_summary}` : ""}
              </div>
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
                title={`Nothing in ${CATEGORY_LABEL[category]}`}
                body="Every property matched for this requirement is filed under the other tab above."
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
}: {
  item: MatchItem;
  selected: boolean;
  onToggleSelect: () => void;
}) {
  const source = item.property ?? item.match;
  const title = source.society_name || source.property_type || "Property";
  const location = [source.area_name, source.address].filter(Boolean).join(" · ");

  return (
    <div
      className={["match-card", selected && "match-card--selected"].filter(Boolean).join(" ")}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={onToggleSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onToggleSelect();
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
        {source.carpet_area_sqft !== null && (
          <span className="fact">
            <IconRuler size={12} />
            {formatCarpetArea(source.carpet_area_sqft, source.carpet_area_unit)}
          </span>
        )}
      </div>

      <div className="match-card__badges">
        <Badge tone={BUCKET_TONE[item.match.bucket]}>{BUCKET_LABEL[item.match.bucket].replace(" matches", " match")}</Badge>
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
