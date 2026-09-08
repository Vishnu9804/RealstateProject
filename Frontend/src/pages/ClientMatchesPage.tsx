import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useParams } from "react-router-dom";
import { matchingApi } from "../api/matchingApi";
import { propertyApi } from "../api/propertyApi";
import type { ClientMatchResult, MatchBucket, MatchedProperty, PropertyRecord } from "../api/types";
import { useAppStatus } from "../state/StatusProvider";
import { friendlyError } from "../lib/apiError";
import { getCachedPropertyList, setCachedPropertyList } from "../lib/propertyListCache";
import { formatCarpetArea, formatPrice, formatPricePerUnit, relativeTime } from "../lib/formatters";
import { sourceDetail, sourceLabel } from "../lib/propertyFilters";
import { useToast } from "../components/ui/Toast";
import { Badge, Button, Copyable, EmptyState, Note, Panel, SkeletonRows, Stat } from "../components/ui/Primitives";
import {
  IconAlert,
  IconArrowRight,
  IconBuilding,
  IconClock,
  IconInbox,
  IconMessage,
  IconMove,
  IconPin,
  IconRefresh,
  IconRuler,
  IconSparkle,
  IconX,
} from "../components/ui/Icons";

/**
 * The Client-Property Matching feature's dashboard — "View Matches" for one
 * client, reached from InquiryClientsPage. On open this only reads the
 * cached result (Backend/Service/ClientPropertyMatchingService/
 * matching_service.py's get_cached_result) — matches are (re)computed
 * automatically whenever the client's requirements are saved
 * (Backend/Service/WhatsAppInquiryHandlingService/client_store.py), not on
 * every page load. "Refresh matches" below is a manual override for when a
 * new property might now be a better fit even though this client's own
 * requirements haven't changed.
 */
export default function ClientMatchesPage() {
  const { phone = "" } = useParams<{ phone: string }>();
  const toast = useToast();

  const [result, setResult] = useState<ClientMatchResult | null>(null);
  const [properties, setProperties] = useState<PropertyRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [recomputing, setRecomputing] = useState(false);
  const [movingId, setMovingId] = useState<string | null>(null);
  const [selectedMatch, setSelectedMatch] = useState<MatchedProperty | null>(null);

  const { status: appStatus } = useAppStatus();

  const load = useCallback(async () => {
    try {
      setResult(await matchingApi.getMatches(phone));
      setError(null);
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setLoading(false);
    }
    // Full property records (source message, sender, timestamps, ...) for
    // the detail dialog — reuses the existing Properties-dashboard endpoint
    // as-is, and shares its cache (lib/propertyListCache.ts) with the
    // Properties/Landing Page pages: still fetched every time this page is
    // opened (this is a one-shot page, not polled, so there's no ongoing
    // version check to lean on instead), but a cache hit means the dialog
    // has real data to show immediately while that fetch is in flight
    // rather than sitting blank. Fetched separately and allowed to fail
    // quietly: the dialog still works from the match's own fields if this
    // list isn't available.
    try {
      const data = await propertyApi.getProperties(500);
      setProperties(data);
      setCachedPropertyList(data, appStatus?.properties_version ?? null);
    } catch {
      setProperties(null);
    }
  }, [phone, appStatus?.properties_version]);

  useEffect(() => {
    const cached = getCachedPropertyList();
    if (cached) setProperties(cached.data);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phone]);

  async function handleRefresh() {
    setRecomputing(true);
    try {
      setResult(await matchingApi.recompute(phone));
      setError(null);
      toast.push({ tone: "ok", title: "Matches refreshed" });
    } catch (err) {
      toast.push({ tone: "bad", title: "Refresh failed", message: friendlyError(err) });
    } finally {
      setRecomputing(false);
    }
  }

  async function handleMoveToMain(match: MatchedProperty) {
    setMovingId(match.record_id);
    try {
      await propertyApi.updateProperty(match.record_id, { review_status: "accepted", needs_review: false });
      // Optimistic local update — the property's category in the cached
      // match result reflects its state when scored; no need to re-run
      // the whole pipeline just to reflect one property moving tabs.
      setResult((prev) => (prev ? { ...prev, ...remapCategory(prev, match.record_id) } : prev));
      setSelectedMatch((prev) => (prev && prev.record_id === match.record_id ? { ...prev, property_category: "main" } : prev));
      toast.push({ tone: "ok", title: "Moved to Main", message: match.society_name ?? match.area_name ?? undefined });
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not move property", message: friendlyError(err) });
    } finally {
      setMovingId(null);
    }
  }

  const propertiesById = useMemo(() => {
    const map = new Map<string, PropertyRecord>();
    for (const property of properties ?? []) map.set(property.record_id, property);
    return map;
  }, [properties]);

  return (
    <div className="stack stack-5">
      <header className="section-head">
        <div>
          <Link to="/inquiries" className="faint small" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
            ← Back to Inquiries
          </Link>
          <div className="section-head__eyebrow">Client-Property Matching</div>
          <h1 className="page-title">{result?.client_name || "Matches"}</h1>
          <p className="section-head__sub">{phone}</p>
        </div>
        <div className="row-flex">
          {result?.computed_at && (
            <span className="toolbar__meta">
              <IconClock size={13} /> Computed {relativeTime(new Date(result.computed_at))}
            </span>
          )}
          <Button icon={<IconRefresh size={15} />} onClick={handleRefresh} busy={recomputing}>
            Refresh matches
          </Button>
        </div>
      </header>

      {error && (
        <Panel>
          <div className="row-flex" style={{ color: "var(--bad)" }}>
            <IconAlert size={16} /> {error}
          </div>
        </Panel>
      )}

      {loading && (
        <Panel>
          <SkeletonRows rows={6} />
        </Panel>
      )}

      {!loading && result && !result.has_requirements && (
        <Panel>
          <EmptyState
            icon={<IconInbox size={38} />}
            title="No requirements yet"
            body="This client hasn't submitted their property requirements (purpose, type, budget, area) yet — matches will appear here automatically once they do."
          />
        </Panel>
      )}

      {!loading && result && result.has_requirements && (
        <>
          <div className="stat-grid">
            <Stat label="High match" value={result.high.length} icon={<IconSparkle size={13} />} tone="ok" delay={0} />
            <Stat label="Medium match" value={result.medium.length} icon={<IconSparkle size={13} />} tone="warn" delay={60} />
            <Stat label="Low match" value={result.low.length} icon={<IconSparkle size={13} />} delay={120} />
          </div>

          {result.high.length === 0 && result.medium.length === 0 && result.low.length === 0 ? (
            <Panel>
              <EmptyState
                icon={<IconInbox size={38} />}
                title="No properties to compare yet"
                body="There are no stored properties yet — matches will appear once some come in through WhatsApp."
              />
            </Panel>
          ) : (
            <>
              <MatchSection title="High match" bucket="high" matches={result.high} onMoveToMain={handleMoveToMain} movingId={movingId} onSelect={setSelectedMatch} />
              <MatchSection title="Medium match" bucket="medium" matches={result.medium} onMoveToMain={handleMoveToMain} movingId={movingId} onSelect={setSelectedMatch} />
              <MatchSection title="Low match" bucket="low" matches={result.low} onMoveToMain={handleMoveToMain} movingId={movingId} onSelect={setSelectedMatch} />
            </>
          )}
        </>
      )}

      {selectedMatch && (
        <MatchDetailDialog
          match={selectedMatch}
          property={propertiesById.get(selectedMatch.record_id) ?? null}
          moving={movingId === selectedMatch.record_id}
          onMoveToMain={handleMoveToMain}
          onClose={() => setSelectedMatch(null)}
        />
      )}
    </div>
  );
}

function remapCategory(result: ClientMatchResult, recordId: string): Partial<ClientMatchResult> {
  const patch = (list: MatchedProperty[]) =>
    list.map((m) => (m.record_id === recordId ? { ...m, property_category: "main" as const } : m));
  return { high: patch(result.high), medium: patch(result.medium), low: patch(result.low) };
}

const BUCKET_TONE: Record<MatchBucket, "ok" | "warn" | "bad"> = { high: "ok", medium: "warn", low: "bad" };
const CATEGORY_LABEL: Record<MatchedProperty["property_category"], string> = {
  main: "Main",
  outsider: "Outsider",
  needs_review: "Needs Review",
};

function MatchSection({
  title,
  bucket,
  matches,
  onMoveToMain,
  movingId,
  onSelect,
}: {
  title: string;
  bucket: MatchBucket;
  matches: MatchedProperty[];
  onMoveToMain: (match: MatchedProperty) => void;
  movingId: string | null;
  onSelect: (match: MatchedProperty) => void;
}) {
  if (matches.length === 0) return null;
  return (
    <Panel className="stack stack-3">
      <div className="section-head__eyebrow" style={{ marginBottom: 0 }}>
        {title} · {matches.length}
      </div>
      <div className="stack stack-3">
        {matches.map((match) => (
          <MatchRow
            key={match.record_id}
            match={match}
            bucket={bucket}
            onMoveToMain={onMoveToMain}
            moving={movingId === match.record_id}
            onSelect={onSelect}
          />
        ))}
      </div>
    </Panel>
  );
}

function MatchRow({
  match,
  bucket,
  onMoveToMain,
  moving,
  onSelect,
}: {
  match: MatchedProperty;
  bucket: MatchBucket;
  onMoveToMain: (match: MatchedProperty) => void;
  moving: boolean;
  onSelect: (match: MatchedProperty) => void;
}) {
  const title = match.society_name || match.property_type || "Property";
  return (
    <div
      className="detail"
      style={{ padding: "12px 14px", cursor: "pointer" }}
      role="button"
      tabIndex={0}
      onClick={() => onSelect(match)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(match);
        }
      }}
    >
      <div className="row-flex" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <div className="stack stack-1">
          <div className="row-flex" style={{ gap: 8 }}>
            <strong>{title}</strong>
            <Badge tone={BUCKET_TONE[bucket]}>{Math.round(match.score * 100)}%</Badge>
            {match.is_partial_match && <Badge tone="info">Partial data</Badge>}
            <Badge tone={match.property_category === "main" ? "ok" : "info"}>{CATEGORY_LABEL[match.property_category]}</Badge>
          </div>
          <div className="faint small row-flex" style={{ gap: 10 }}>
            {match.property_type && (
              <span className="row-flex" style={{ gap: 4 }}>
                <IconBuilding size={12} /> {match.property_type}
                {match.bhk ? ` · ${match.bhk} BHK` : ""}
              </span>
            )}
            {match.area_name && (
              <span className="row-flex" style={{ gap: 4 }}>
                <IconPin size={12} /> {match.area_name}
              </span>
            )}
            <span>{formatPrice(match.price_text, match.price_amount_inr)}</span>
            {match.carpet_area_sqft !== null && <span>{formatCarpetArea(match.carpet_area_sqft, match.carpet_area_unit)}</span>}
            <span>{match.listing_type}</span>
          </div>
          <div className="faint small">{match.reason}</div>
        </div>

        {match.property_category !== "main" && (
          <Button
            size="sm"
            variant="ghost"
            icon={<IconMove size={14} />}
            busy={moving}
            onClick={(event) => {
              event.stopPropagation();
              onMoveToMain(match);
            }}
          >
            Move to Main <IconArrowRight size={12} />
          </Button>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ detail dialog */

const FIELD_SCORE_LABEL: Record<string, string> = {
  budget: "Budget",
  location: "Location",
  bhk: "BHK",
  semantic: "Overall fit",
  purpose_gate: "Purpose match",
  property_type_gate: "Property type match",
};

/**
 * Everything stored about one matched property, in one place — the
 * full PropertyRecord (source message, sender, timestamps, ...) when it's
 * available from the Properties dashboard's own endpoint (reused as-is),
 * plus this feature's own match breakdown (score, evidence, why it scored
 * the way it did). Falls back to just the match's own fields if the full
 * record can't be found (e.g. still loading, or the property was since
 * deleted) rather than blocking the dialog from opening at all. Modeled on
 * DashboardPage.tsx's PropertyDetailDialog for a consistent look.
 */
function MatchDetailDialog({
  match,
  property,
  moving,
  onMoveToMain,
  onClose,
}: {
  match: MatchedProperty;
  property: PropertyRecord | null;
  moving: boolean;
  onMoveToMain: (match: MatchedProperty) => void;
  onClose: () => void;
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

  const title = property?.society_name || match.society_name || match.property_type || "Property";
  const subtitle = [property?.area_name ?? match.area_name, property?.address ?? match.address].filter(Boolean).join(" · ");
  const fieldScoreEntries = Object.entries(match.field_scores).filter(([, value]) => value !== null) as [string, number][];

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="Matched property details">
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">
              {(property?.property_type ?? match.property_type) || "Property"} · {property?.listing_type ?? match.listing_type}
            </div>
            <h2 className="detail-modal__title cell-truncate">{title}</h2>
            {subtitle && <div className="detail-modal__sub cell-truncate">{subtitle}</div>}
            <div className="detail-modal__badges">
              <Badge tone={BUCKET_TONE[match.bucket]}>{Math.round(match.score * 100)}% match</Badge>
              {match.is_partial_match && <Badge tone="info">Partial data</Badge>}
              <Badge tone={match.property_category === "main" ? "ok" : "info"}>{CATEGORY_LABEL[match.property_category]}</Badge>
              {(property?.bhk ?? match.bhk) && (
                <span className="fact">
                  <IconBuilding size={12} />
                  {property?.bhk ?? match.bhk}
                </span>
              )}
              {(property?.carpet_area_sqft ?? match.carpet_area_sqft) !== null && (
                <span className="fact">
                  <IconRuler size={12} />
                  {formatCarpetArea(property?.carpet_area_sqft ?? match.carpet_area_sqft, property?.carpet_area_unit ?? match.carpet_area_unit)}
                </span>
              )}
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          {property?.needs_review && property?.review_notes && (
            <Note tone="warn" icon={<IconAlert size={16} />}>
              <strong>Flagged for review:</strong> {property.review_notes}
            </Note>
          )}

          <div className="detail__block">
            <div className="detail__k">Why this match</div>
            <div className="detail__v">{match.reason}</div>
            {fieldScoreEntries.length > 0 && (
              <div className="faint small row-flex" style={{ gap: 10, marginTop: 6, flexWrap: "wrap" }}>
                {fieldScoreEntries.map(([name, value]) => (
                  <span key={name}>
                    {FIELD_SCORE_LABEL[name] ?? name}: {Math.round(value * 100)}%
                  </span>
                ))}
              </div>
            )}
            <div className="faint small" style={{ marginTop: 6 }}>
              Data available for {Math.round(match.evidence_ratio * 100)}% of the fields compared.
            </div>
          </div>

          <div className="detail__grid">
            <div className="detail__block">
              <div className="detail__k">Price as written</div>
              <div className="detail__v">{property?.price_text ?? match.price_text ?? "—"}</div>
              {(property?.price_amount_inr ?? match.price_amount_inr) !== null && (
                <div className="faint small" style={{ marginTop: 4 }}>
                  Read as {formatPrice(null, property?.price_amount_inr ?? match.price_amount_inr)}
                </div>
              )}
            </div>

            {property && (
              <div className="detail__block">
                <div className="detail__k">Price per unit</div>
                <div className="detail__v">
                  {property.price_per_unit_text ?? formatPricePerUnit(null, property.price_per_unit_amount_inr)}
                </div>
              </div>
            )}

            <div className="detail__block">
              <div className="detail__k">Contact</div>
              <div className="detail__v">{property?.contact_name ?? match.contact_name ?? "—"}</div>
              {(property?.contact_phone ?? match.contact_phone) && (
                <div className="detail__v" style={{ marginTop: 4 }}>
                  <Copyable text={(property?.contact_phone ?? match.contact_phone) as string} />
                </div>
              )}
            </div>

            {property && (
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
            )}

            {property && (
              <div className="detail__block">
                <div className="detail__k">Source</div>
                <div className="detail__v">{sourceLabel(property)}</div>
                <div className="faint small" style={{ marginTop: 4 }}>
                  {sourceDetail(property)} · {property.formatted_timestamp}
                </div>
              </div>
            )}
          </div>

          {(property?.description ?? match.description) && (
            <div className="detail__block">
              <div className="detail__k">Description</div>
              <div className="detail__v">{property?.description ?? match.description}</div>
            </div>
          )}

          {property && (
            <div className="detail__block">
              <div className="detail__k">
                <IconMessage size={11} /> Original message
              </div>
              <div className="detail__msg">{property.message_text}</div>
            </div>
          )}
        </div>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          {match.property_category !== "main" && (
            <span className="row-flex" style={{ marginLeft: "auto" }}>
              <Button icon={<IconMove size={14} />} busy={moving} onClick={() => onMoveToMain(match)}>
                Move to Main
              </Button>
            </span>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
