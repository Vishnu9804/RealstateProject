import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { builderProjectApi } from "../api/builderProjectApi";
import { ApiError } from "../api/client";
import { propertyApi } from "../api/propertyApi";
import type { BuilderProjectRecord, PropertyRecord, PropertySource } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { getCachedBuilderProjectList } from "../lib/builderProjectListCache";
import { formatArea, formatPrice } from "../lib/formatters";
import { getCachedPropertyDetail, setCachedPropertyDetail } from "../lib/propertyDetailCache";
import { getCachedPropertyList } from "../lib/propertyListCache";
import { sourceDetail, sourceLabel } from "../lib/propertyFilters";

/** propertyApi.getProperties' own list is missing only image_urls/
 *  embeddings (see Backend/Database/property_repository.py's
 *  get_all_properties_summary docstring) — nothing this read-only dialog
 *  ever shows. So a hit in that shared, cross-page cache (already warmed
 *  by whichever of Properties/Landing Page/this dialog's own callers last
 *  fetched it) is exactly as good as the single-property endpoint here,
 *  without paying that endpoint's own ~1-2s round trip. */
function findInPropertyListCache(recordId: string): PropertyRecord | null {
  return getCachedPropertyList()?.data.find((property) => property.record_id === recordId) ?? null;
}

/** The builder-project counterpart: the Builder Projects list cache (kept
 *  warm by that page and by the match dialogs) holds every field this view
 *  shows, so a hit there costs no request at all. */
function findInBuilderProjectListCache(recordId: string): BuilderProjectRecord | null {
  return getCachedBuilderProjectList()?.data.find((project) => project.record_id === recordId) ?? null;
}
import SourceTag from "./ui/SourceTag";
import { Button, Copyable, EmptyState, Note, SkeletonRows } from "./ui/Primitives";
import { IconAlert, IconBuilding, IconCheck, IconMessage, IconPin, IconRuler, IconX } from "./ui/Icons";

/** Backs the Select/Deselect button in this dialog's footer — used by
 *  SelectPropertyPage and anywhere else that opens this read-only dialog
 *  from a selectable list. Selected always renders red (see
 *  .select-toggle-btn--remove) since "Deselect" is always the destructive
 *  direction here, unlike the Landing Page page's own add/remove split. */
export interface ReadOnlySelectAction {
  selected: boolean;
  /** Set (to the reason) when a selected property can't be deselected —
   *  already handed to an agent or already visited. Renders the button
   *  disabled with that reason as its title instead of removing it, so the
   *  constraint is visible rather than just silently unclickable. */
  locked?: string | null;
  onToggle: () => void;
}

/**
 * Read-only listing view, shared by every place that only ever holds a
 * property_record_id + a display label — an agent's active/completed
 * visit row (AgentVisitsDialog.tsx) and a client's assigned/completed-visit
 * card (ClientMatchesDialog.tsx) alike — and needs the full record (price,
 * contact, sender, source, original message) on demand. Fetched here via
 * the same on-demand-plus-shared-cache pattern InquiryClientsPage's
 * expanded leads use, so a property recently opened from the Properties
 * or Landing Page page costs no extra request.
 *
 * `source` says which kind of listing that id is. A visit can be to a
 * builder project as well as a property (both are matched and assigned the
 * same way), and the two live behind different endpoints, so a builder
 * project gets its own body — the same facts, minus what only a WhatsApp
 * capture has (sender, source chat, original message). Absent means
 * "property", which is what every caller that predates builder-project
 * matching passes by not passing it.
 *
 * No edit/move/assign actions — this exists purely so whoever is looking
 * at a visit record can see enough about the listing to make sense of it,
 * not to manage it. That management happens on the Properties / Builder
 * Projects pages or inside the matches dialog's own scored cards, never
 * from here.
 */
export default function PropertyReadOnlyDialog({
  recordId,
  onClose,
  badges,
  selectAction,
  source,
}: {
  recordId: string;
  onClose: () => void;
  /** Extra badges to show alongside the property's own (e.g. "Completed"
   *  tone from whichever visit list this was opened from) — rendered
   *  after the property's own facts, before the Close button. */
  badges?: React.ReactNode;
  /** Only set by callers that open this dialog from a selectable list
   *  (SelectPropertyPage) — an extra footer button, Select/Deselect, next
   *  to Close. Undefined everywhere else, so the button simply doesn't
   *  render there. Properties only: nothing selectable lists builder
   *  projects. */
  selectAction?: ReadOnlySelectAction;
  /** Which kind of listing `recordId` is — see the component docstring. */
  source?: PropertySource;
}) {
  return source === "builder_project" ? (
    <BuilderProjectReadOnlyDialog recordId={recordId} onClose={onClose} badges={badges} />
  ) : (
    <PropertyRecordReadOnlyDialog recordId={recordId} onClose={onClose} badges={badges} selectAction={selectAction} />
  );
}

function PropertyRecordReadOnlyDialog({
  recordId,
  onClose,
  badges,
  selectAction,
}: {
  recordId: string;
  onClose: () => void;
  badges?: React.ReactNode;
  selectAction?: ReadOnlySelectAction;
}) {
  const [property, setProperty] = useState<PropertyRecord | null | undefined>(
    () => getCachedPropertyDetail(recordId) ?? findInPropertyListCache(recordId) ?? undefined,
  );
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const cachedDetail = getCachedPropertyDetail(recordId);
    if (cachedDetail) {
      setProperty(cachedDetail);
      return;
    }
    // Second-best, not second-choice-by-luck: the list cache is kept warm
    // by every page that shows properties, so this hits far more often
    // than the detail cache alone would. No background refetch needed on
    // top of it — the list is already someone else's job to keep current
    // (Properties/Landing Page poll it; ClientMatchesDialog/
    // AgentVisitsDialog refresh it on their own mount), and this dialog
    // never shows anything (photos) that list is missing.
    const fromList = findInPropertyListCache(recordId);
    if (fromList) {
      setProperty(fromList);
      return;
    }
    setProperty(undefined);
    setNotFound(false);
    setError(null);
    propertyApi
      .getProperty(recordId)
      .then((full) => {
        if (cancelled) return;
        setProperty(full);
        setCachedPropertyDetail(full);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
          setProperty(null);
        } else {
          setError(friendlyError(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [recordId]);

  return (
    <ReadOnlyFrame
      onClose={onClose}
      ariaLabel="Property details"
      heading="Property details"
      loading={property === undefined && !notFound && !error}
      error={error}
      // Two ways a property reaches this state now: it was deleted, or its
      // deal closed and it was moved to Sold out (see the Properties page's
      // Sold out tab) — which takes it out of the property database
      // entirely. This dialog only ever gets an id, so it cannot tell which,
      // and naming both is more use than confidently naming the wrong one.
      notFoundBody={
        notFound
          ? "This property is no longer in your property database — it was either deleted or marked sold out."
          : null
      }
    >
      {property && (
        <PropertyReadOnlyBody property={property} badges={badges} onClose={onClose} selectAction={selectAction} />
      )}
    </ReadOnlyFrame>
  );
}

function BuilderProjectReadOnlyDialog({
  recordId,
  onClose,
  badges,
}: {
  recordId: string;
  onClose: () => void;
  badges?: React.ReactNode;
}) {
  const [project, setProject] = useState<BuilderProjectRecord | null | undefined>(
    () => findInBuilderProjectListCache(recordId) ?? undefined,
  );
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Same order as the property path: the shared list cache first (no
    // request at all), then the single-project endpoint — served from the
    // backend's memory and HTTP-cached, so it costs no database query
    // either way.
    const fromList = findInBuilderProjectListCache(recordId);
    if (fromList) {
      setProject(fromList);
      return;
    }
    setProject(undefined);
    setNotFound(false);
    setError(null);
    builderProjectApi
      .getBuilderProject(recordId)
      .then((found) => {
        if (!cancelled) setProject(found);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
          setProject(null);
        } else {
          setError(friendlyError(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [recordId]);

  return (
    <ReadOnlyFrame
      onClose={onClose}
      ariaLabel="Builder project details"
      heading="Builder project details"
      loading={project === undefined && !notFound && !error}
      error={error}
      notFoundBody={notFound ? "This builder project is no longer on the Builder Projects page — it was deleted." : null}
    >
      {project && <BuilderProjectReadOnlyBody project={project} badges={badges} onClose={onClose} />}
    </ReadOnlyFrame>
  );
}

/** The dialog shell both bodies share: the scrim, Escape to close, and the
 *  loading / error / no-longer-available states. */
function ReadOnlyFrame({
  onClose,
  ariaLabel,
  heading,
  loading,
  error,
  notFoundBody,
  children,
}: {
  onClose: () => void;
  ariaLabel: string;
  heading: string;
  loading: boolean;
  error: string | null;
  notFoundBody: string | null;
  children: React.ReactNode;
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

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label={ariaLabel}>
        {loading && (
          <>
            <div className="detail-modal__head">
              <div className="detail-modal__eyebrow">Loading…</div>
              <button type="button" className="toast__close" onClick={onClose} aria-label="Close" style={{ position: "absolute", right: 22, top: 22 }}>
                <IconX size={15} />
              </button>
            </div>
            <div className="detail-modal__body">
              <SkeletonRows rows={5} />
            </div>
          </>
        )}

        {error && (
          <>
            <div className="detail-modal__head">
              <div className="detail-modal__eyebrow">{heading}</div>
              <button type="button" className="toast__close" onClick={onClose} aria-label="Close" style={{ position: "absolute", right: 22, top: 22 }}>
                <IconX size={15} />
              </button>
            </div>
            <div className="detail-modal__body">
              <Note tone="bad" icon={<IconAlert size={16} />}>
                {error}
              </Note>
            </div>
            <div className="detail-modal__foot">
              <Button variant="ghost" onClick={onClose}>
                Close
              </Button>
            </div>
          </>
        )}

        {notFoundBody && (
          <>
            <div className="detail-modal__head">
              <div className="detail-modal__eyebrow">{heading}</div>
              <button type="button" className="toast__close" onClick={onClose} aria-label="Close" style={{ position: "absolute", right: 22, top: 22 }}>
                <IconX size={15} />
              </button>
            </div>
            <div className="detail-modal__body">
              <EmptyState icon={<IconAlert size={36} />} title="No longer available" body={notFoundBody} />
            </div>
            <div className="detail-modal__foot">
              <Button variant="ghost" onClick={onClose}>
                Close
              </Button>
            </div>
          </>
        )}

        {children}
      </div>
    </div>,
    document.body,
  );
}

function PropertyReadOnlyBody({
  property,
  badges,
  onClose,
  selectAction,
}: {
  property: PropertyRecord;
  badges?: React.ReactNode;
  onClose: () => void;
  selectAction?: ReadOnlySelectAction;
}) {
  const title = property.society_name || property.area_name || "Property";
  const subtitle = [property.unit_no && `Unit ${property.unit_no}`, property.area_name, property.address]
    .filter(Boolean)
    .join(" · ");
  const area = formatArea(property.area_sqft, property.area_vaar);
  return (
    <>
      <div className="detail-modal__head">
        <div style={{ minWidth: 0 }}>
          <div className="detail-modal__eyebrow">
            {property.property_type || "Property"} · {property.listing_type}
          </div>
          <h2 className="detail-modal__title cell-truncate">{title}</h2>
          {subtitle && <div className="detail-modal__sub cell-truncate">{subtitle}</div>}
          <div className="detail-modal__badges">
            <SourceTag source="property" />
            {badges}
            {property.bhk && (
              <span className="fact">
                <IconBuilding size={12} />
                {property.bhk}
              </span>
            )}
            {property.area_name && (
              <span className="fact">
                <IconPin size={12} />
                {property.area_name}
              </span>
            )}
            {area !== "—" && (
              <span className="fact">
                <IconRuler size={12} />
                {area}
              </span>
            )}
            {property.super_built && (
              <span className="fact" title="Super built">
                <IconRuler size={12} />
                Super built {property.super_built}
              </span>
            )}
            {property.furnishing && <span className="fact">{property.furnishing}</span>}
            {!property.is_available && (
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
        {property.needs_review && property.review_notes && (
          <Note tone="warn" icon={<IconAlert size={16} />}>
            <strong>Flagged for review:</strong> {property.review_notes}
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
        </div>

        {property.description && (
          <div className="detail__block">
            <div className="detail__k">Description</div>
            <div className="detail__v">{property.description}</div>
          </div>
        )}

        {property.extra_notes && (
          <div className="detail__block">
            <div className="detail__k">Extra</div>
            <div className="detail__v">{property.extra_notes}</div>
          </div>
        )}

        {/* Internal only — this dialog is staff-facing (see
            PropertyRecord.location_url). It is not in any share, hand-off or
            public shape, so this is the one place a pin is ever rendered. */}
        {property.location_url && (
          <div className="detail__block">
            <div className="detail__k">
              <IconPin size={11} /> Location link <span className="faint">· internal only</span>
            </div>
            <div className="detail__v">
              <a href={property.location_url} target="_blank" rel="noreferrer noopener">
                Open map
              </a>
            </div>
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
        {selectAction && (
          <Button
            variant="ghost"
            className={`select-toggle-btn${selectAction.selected ? " select-toggle-btn--remove" : ""}`}
            icon={<IconCheck size={14} />}
            disabled={Boolean(selectAction.selected && selectAction.locked)}
            title={selectAction.selected && selectAction.locked ? `Can't deselect — ${selectAction.locked}` : undefined}
            onClick={() => {
              selectAction.onToggle();
              onClose();
            }}
          >
            {selectAction.selected ? "Deselect" : "Select"}
          </Button>
        )}
      </div>
    </>
  );
}

/** A builder project's facts — the property body above minus what only a
 *  WhatsApp capture has (sender, source chat, original message, review
 *  flag), plus when it was added. */
function BuilderProjectReadOnlyBody({
  project,
  badges,
  onClose,
}: {
  project: BuilderProjectRecord;
  badges?: React.ReactNode;
  onClose: () => void;
}) {
  const title = project.society_name || project.area_name || "Builder project";
  const subtitle = [project.unit_no && `Unit ${project.unit_no}`, project.area_name, project.address]
    .filter(Boolean)
    .join(" · ");
  const area = formatArea(project.area_sqft, project.area_vaar);
  return (
    <>
      <div className="detail-modal__head">
        <div style={{ minWidth: 0 }}>
          <div className="detail-modal__eyebrow">
            {project.property_type || "Builder project"} · {project.listing_type}
          </div>
          <h2 className="detail-modal__title cell-truncate">{title}</h2>
          {subtitle && <div className="detail-modal__sub cell-truncate">{subtitle}</div>}
          <div className="detail-modal__badges">
            <SourceTag source="builder_project" />
            {badges}
            {project.bhk && (
              <span className="fact">
                <IconBuilding size={12} />
                {project.bhk}
              </span>
            )}
            {project.area_name && (
              <span className="fact">
                <IconPin size={12} />
                {project.area_name}
              </span>
            )}
            {area !== "—" && (
              <span className="fact">
                <IconRuler size={12} />
                {area}
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
            <div className="detail__k">Contact</div>
            <div className="detail__v">{project.contact_name ?? "—"}</div>
            {project.contact_phone && (
              <div className="detail__v" style={{ marginTop: 4 }}>
                <Copyable text={project.contact_phone} />
              </div>
            )}
          </div>

          <div className="detail__block">
            <div className="detail__k">Added</div>
            <div className="detail__v">{project.formatted_timestamp}</div>
            <div className="faint small" style={{ marginTop: 4 }}>
              Builder Projects page
            </div>
          </div>
        </div>

        {project.description && (
          <div className="detail__block">
            <div className="detail__k">Description</div>
            <div className="detail__v">{project.description}</div>
          </div>
        )}

        {project.extra_notes && (
          <div className="detail__block">
            <div className="detail__k">Extra</div>
            <div className="detail__v">{project.extra_notes}</div>
          </div>
        )}

        {/* Internal only, exactly as on a property — see
            BuilderProjectRecord.location_url. */}
        {project.location_url && (
          <div className="detail__block">
            <div className="detail__k">
              <IconPin size={11} /> Location link <span className="faint">· internal only</span>
            </div>
            <div className="detail__v">
              <a href={project.location_url} target="_blank" rel="noreferrer noopener">
                Open map
              </a>
            </div>
          </div>
        )}
      </div>

      <div className="detail-modal__foot">
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
    </>
  );
}
