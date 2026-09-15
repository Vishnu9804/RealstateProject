import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { propertyApi, type PropertyContentFields } from "../api/propertyApi";
import type { PropertyRecord } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { useToast } from "./ui/Toast";
import { Button, Segmented } from "./ui/Primitives";
import { IconImage, IconInstagram, IconPin, IconX } from "./ui/Icons";
import PropertyImagesField from "./PropertyImagesField";

/**
 * The Properties page's Add/Edit dialog — the same field set either way
 * (everything the LLM structuring stage would otherwise fill in, plus
 * instagram_reel_url and super_built, which only a human ever sets). Every
 * field is optional: there is nothing here that blocks Save.
 *
 * Also the Builder Projects page's Add/Edit dialog: a builder project has
 * exactly these fields under exactly these names (see Backend/Model/
 * BuilderProjectModel/builder_project.py), so that page passes its own
 * endpoints as `api` and its own `noun` rather than keeping a second copy
 * of this form in step by hand.
 *
 * Cancel and the header's X both discard the in-progress edit without
 * calling the backend — onClose is the only thing either one does, and
 * neither is wired to onSave.
 */

/** What the dialog reads off the record it edits: the content fields plus
 *  the record's id and photo bookkeeping. A PropertyRecord and a
 *  BuilderProjectRecord both carry exactly these. */
export type EditableContentRecord = Pick<
  PropertyRecord,
  | "record_id"
  | "property_type"
  | "bhk"
  | "unit_no"
  | "society_name"
  | "area_name"
  | "address"
  | "area_sqft"
  | "area_vaar"
  | "super_built"
  | "furnishing"
  | "price_text"
  | "price_amount_inr"
  | "listing_type"
  | "contact_name"
  | "contact_phone"
  | "description"
  | "instagram_reel_url"
  | "image_urls"
  | "image_count"
  | "location_url"
  | "video_available"
  | "extra_notes"
  | "is_available"
>;

/** The three values the backend normalizes furnishing onto (see
 *  Agent/WhatsAppDataFetchingAgent/glm_extraction_schema.py). A stored value
 *  that somehow isn't one of them still round-trips: the select below adds
 *  it as an extra option rather than silently resetting the field to blank. */
export const FURNISHING_OPTIONS = ["Unfurnished", "Semi furnished", "Fully furnished"];

/** Where the dialog saves to and loads photos from. */
export interface ContentFormApi<T extends EditableContentRecord> {
  create: (body: PropertyContentFields) => Promise<T>;
  update: (recordId: string, body: PropertyContentFields) => Promise<T>;
  getImages: (recordId: string) => Promise<{ image_urls: string[] }>;
}

const PROPERTY_FORM_API: ContentFormApi<PropertyRecord> = {
  create: propertyApi.createProperty,
  update: propertyApi.updateProperty,
  getImages: propertyApi.getPropertyImages,
};

interface FormState {
  property_type: string;
  bhk: string;
  unit_no: string;
  society_name: string;
  area_name: string;
  address: string;
  area_sqft: string;
  area_vaar: string;
  super_built: string;
  furnishing: string;
  price_text: string;
  price_amount_inr: string;
  listing_type: "Sale" | "Rent";
  contact_name: string;
  contact_phone: string;
  description: string;
  instagram_reel_url: string;
  image_urls: string[];
  location_url: string;
  video_available: boolean;
  extra_notes: string;
  is_available: boolean;
}

const BLANK_FORM: FormState = {
  property_type: "",
  bhk: "",
  unit_no: "",
  society_name: "",
  area_name: "",
  address: "",
  area_sqft: "",
  area_vaar: "",
  super_built: "",
  furnishing: "",
  price_text: "",
  price_amount_inr: "",
  listing_type: "Sale",
  contact_name: "",
  contact_phone: "",
  description: "",
  instagram_reel_url: "",
  image_urls: [],
  location_url: "",
  video_available: false,
  extra_notes: "",
  // A newly added property is on the market — that is what adding it means.
  is_available: true,
};

function toFormState(property: EditableContentRecord): FormState {
  return {
    property_type: property.property_type ?? "",
    bhk: property.bhk ?? "",
    unit_no: property.unit_no ?? "",
    society_name: property.society_name ?? "",
    area_name: property.area_name ?? "",
    address: property.address ?? "",
    area_sqft: property.area_sqft?.toString() ?? "",
    area_vaar: property.area_vaar?.toString() ?? "",
    super_built: property.super_built ?? "",
    furnishing: property.furnishing ?? "",
    price_text: property.price_text ?? "",
    price_amount_inr: property.price_amount_inr?.toString() ?? "",
    listing_type: property.listing_type,
    contact_name: property.contact_name ?? "",
    contact_phone: property.contact_phone ?? "",
    description: property.description ?? "",
    instagram_reel_url: property.instagram_reel_url ?? "",
    image_urls: property.image_urls ?? [],
    location_url: property.location_url ?? "",
    video_available: property.video_available,
    extra_notes: property.extra_notes ?? "",
    is_available: property.is_available,
  };
}

/** Blank strings become null, not "" — an empty field must actually clear
 *  the value server-side, not overwrite it with an empty string.
 *
 *  `includeImages` is a data-safety switch, not a preference. A property's
 *  photos are no longer loaded just because its Edit dialog was opened (see
 *  propertyApi.getProperty), so when they have NOT been loaded, form
 *  .image_urls is empty simply because nobody asked for them — and sending
 *  that empty list would tell the backend to delete every photo the
 *  property has. Leaving the key out entirely is what makes the PATCH
 *  ignore the field (the endpoint applies only the keys actually present —
 *  see PropertyUpdateRequest's exclude_unset), so an edit to a price can
 *  never cost a property its photos. */
function toPayload(form: FormState, includeImages: boolean): PropertyContentFields {
  const text = (value: string) => (value.trim() ? value.trim() : null);
  const num = (value: string) => (value.trim() ? Number(value) : null);
  const payload: PropertyContentFields = {
    property_type: text(form.property_type),
    bhk: text(form.bhk),
    unit_no: text(form.unit_no),
    society_name: text(form.society_name),
    area_name: text(form.area_name),
    address: text(form.address),
    area_sqft: num(form.area_sqft),
    area_vaar: num(form.area_vaar),
    super_built: text(form.super_built),
    furnishing: text(form.furnishing),
    price_text: text(form.price_text),
    price_amount_inr: num(form.price_amount_inr),
    listing_type: form.listing_type,
    contact_name: text(form.contact_name),
    contact_phone: text(form.contact_phone),
    description: text(form.description),
    instagram_reel_url: text(form.instagram_reel_url),
    image_urls: form.image_urls,
    location_url: text(form.location_url),
    video_available: form.video_available,
    extra_notes: text(form.extra_notes),
    is_available: form.is_available,
  };
  if (!includeImages) delete payload.image_urls;
  return payload;
}

const GRID_STYLE: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: 14,
};

function Field({
  label,
  hint,
  span,
  children,
}: {
  label: string;
  hint?: string;
  span?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="field" style={span ? { gridColumn: "1 / -1" } : undefined}>
      <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
        {label}
      </label>
      {children}
      {hint && <span className="field__hint">{hint}</span>}
    </div>
  );
}

export default function PropertyFormDialog<T extends EditableContentRecord = PropertyRecord>({
  mode,
  property,
  onClose,
  onSaved,
  // The cast is the price of a generic default: the Properties endpoints
  // return PropertyRecords, which is exactly what T is whenever a caller
  // does not pass an api of its own (T then defaults to PropertyRecord).
  api = PROPERTY_FORM_API as unknown as ContentFormApi<T>,
  noun = "property",
}: {
  mode: "add" | "edit";
  property?: T;
  onClose: () => void;
  onSaved: (property: T, mode: "add" | "edit") => void;
  /** Where to save and load photos. Defaults to the Properties endpoints. */
  api?: ContentFormApi<T>;
  /** What the record is called in this dialog's wording ("property",
   *  "builder project"). */
  noun?: string;
}) {
  const toast = useToast();
  const [form, setForm] = useState<FormState>(property ? toFormState(property) : BLANK_FORM);
  const [saving, setSaving] = useState(false);
  const Noun = noun.charAt(0).toUpperCase() + noun.slice(1);

  // Whether this dialog is actually holding the property's photos.
  //
  // Opening a property no longer downloads them (see
  // propertyApi.getProperty), so an Edit dialog starts photo-less unless
  // there are none to begin with, or the record it was handed already
  // carries them (the one a save just returned). Until they are loaded the
  // photos field is replaced by a Show photos button, and image_urls is
  // left out of the save entirely — see toPayload. Replacing the field
  // rather than showing an empty one is deliberate: a half-loaded photo
  // list that accepted additions could be saved back over the real one.
  const [imagesLoaded, setImagesLoaded] = useState(
    mode === "add" || !property || property.image_urls.length > 0 || property.image_count === 0,
  );
  const [loadingImages, setLoadingImages] = useState(false);

  async function loadImages() {
    if (!property) return;
    setLoadingImages(true);
    try {
      const { image_urls } = await api.getImages(property.record_id);
      setForm((prev) => ({ ...prev, image_urls }));
      setImagesLoaded(true);
    } catch (err) {
      toast.push({ tone: "bad", title: `Couldn't load this ${noun}'s photos`, message: friendlyError(err) });
    } finally {
      setLoadingImages(false);
    }
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, saving]);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  // Functional updates, not "[...form.image_urls, ...dataUrls]" closed over
  // the render's `form` — PropertyImagesField resolves each file async, and
  // a second drop landing before the first finishes must never clobber it.
  function addImages(dataUrls: string[]) {
    setForm((prev) => ({ ...prev, image_urls: [...prev.image_urls, ...dataUrls] }));
  }
  function removeImage(index: number) {
    setForm((prev) => ({ ...prev, image_urls: prev.image_urls.filter((_, i) => i !== index) }));
  }
  function reorderImages(fromIndex: number, toIndex: number) {
    setForm((prev) => {
      const next = [...prev.image_urls];
      const [moved] = next.splice(fromIndex, 1);
      next.splice(toIndex, 0, moved);
      return { ...prev, image_urls: next };
    });
  }

  async function handleSave() {
    setSaving(true);
    try {
      const payload = toPayload(form, imagesLoaded);
      const saved = mode === "add" ? await api.create(payload) : await api.update(property!.record_id, payload);
      toast.push({
        tone: "ok",
        title: mode === "add" ? `${Noun} added` : `${Noun} updated`,
        message: saved.society_name ?? saved.area_name ?? "Saved.",
      });
      onSaved(saved, mode);
    } catch (err) {
      toast.push({ tone: "bad", title: `Couldn't save this ${noun}`, message: friendlyError(err) });
    } finally {
      setSaving(false);
    }
  }

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label={mode === "add" ? `Add ${noun}` : `Edit ${noun}`}>
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">{mode === "add" ? `New ${noun}` : `Edit ${noun}`}</div>
            <h2 className="detail-modal__title cell-truncate">
              {mode === "add" ? `Add a ${noun}` : (property?.society_name ?? property?.area_name ?? `Edit ${noun}`)}
            </h2>
            <div className="detail-modal__sub">Every field here is optional — fill in only what you know.</div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={saving} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          <div className="stack stack-4">
            <Field
              label={`${Noun} photos`}
              hint={
                imagesLoaded
                  ? `Optional — add photos of this ${noun}.`
                  : "Photos aren't loaded yet. Load them to view, reorder or remove them — your other edits save fine either way."
              }
            >
              {imagesLoaded ? (
                <PropertyImagesField
                  images={form.image_urls}
                  onAdd={addImages}
                  onRemove={removeImage}
                  onReorder={reorderImages}
                />
              ) : (
                <Button variant="ghost" icon={<IconImage size={14} />} busy={loadingImages} onClick={loadImages}>
                  {`Show ${property?.image_count ?? 0} photo${(property?.image_count ?? 0) === 1 ? "" : "s"}`}
                </Button>
              )}
            </Field>

            <div style={GRID_STYLE}>
              <Field label="Society / Building name">
                <input className="input" value={form.society_name} onChange={(e) => set("society_name", e.target.value)} placeholder="e.g. Black Residency" />
              </Field>
              <Field label="Unit / Flat number">
                <input className="input" value={form.unit_no} onChange={(e) => set("unit_no", e.target.value)} placeholder="e.g. 402 or A-404" />
              </Field>
              <Field label="Area / locality">
                <input className="input" value={form.area_name} onChange={(e) => set("area_name", e.target.value)} placeholder="e.g. Vesu" />
              </Field>
              <Field label="Address" span>
                <input className="input" value={form.address} onChange={(e) => set("address", e.target.value)} placeholder="Street, road or landmark" />
              </Field>
              <Field label="Property type">
                <input className="input" value={form.property_type} onChange={(e) => set("property_type", e.target.value)} placeholder="e.g. Flat, Shop, Land/Plot" />
              </Field>
              <Field label="BHK">
                <input className="input" value={form.bhk} onChange={(e) => set("bhk", e.target.value)} placeholder="e.g. 2 BHK" />
              </Field>
              <Field label="Sale or Rent">
                <Segmented
                  ariaLabel="Sale or Rent"
                  value={form.listing_type}
                  onChange={(value) => set("listing_type", value)}
                  options={[
                    { value: "Sale", label: "Sale" },
                    { value: "Rent", label: "Rent" },
                  ]}
                />
              </Field>

              <Field label="Area (sqft)" hint="Only if the size is quoted in square feet">
                <input className="input" type="number" inputMode="decimal" value={form.area_sqft} onChange={(e) => set("area_sqft", e.target.value)} placeholder="e.g. 1200" />
              </Field>
              <Field label="Area (vaar)" hint="Only if the size is quoted in vaar / gaj">
                <input className="input" type="number" inputMode="decimal" value={form.area_vaar} onChange={(e) => set("area_vaar", e.target.value)} placeholder="e.g. 155" />
              </Field>
              <Field label="Super built" hint="As you'd write it — e.g. 1850 sq ft">
                <input className="input" value={form.super_built} onChange={(e) => set("super_built", e.target.value)} placeholder="e.g. 1850 sq ft" />
              </Field>
              <Field label="Furnishing">
                <select className="select" value={form.furnishing} onChange={(e) => set("furnishing", e.target.value)}>
                  <option value="">—</option>
                  {/* The stored value first when it is something other than
                      the three standard ones, so opening and saving a record
                      can never quietly blank a value it didn't recognise. */}
                  {form.furnishing && !FURNISHING_OPTIONS.includes(form.furnishing) && (
                    <option value={form.furnishing}>{form.furnishing}</option>
                  )}
                  {FURNISHING_OPTIONS.map((option) => (
                    <option key={option} value={option}>
                      {option}
                    </option>
                  ))}
                </select>
              </Field>

              <Field label="Price (as written)" hint="e.g. 45L, 1.25cr, 15k/month">
                <input className="input" value={form.price_text} onChange={(e) => set("price_text", e.target.value)} placeholder="e.g. 45L" />
              </Field>
              <Field label="Price (₹ amount)">
                <input className="input" type="number" inputMode="decimal" value={form.price_amount_inr} onChange={(e) => set("price_amount_inr", e.target.value)} placeholder="e.g. 4500000" />
              </Field>

              <Field label="Contact name">
                <input className="input" value={form.contact_name} onChange={(e) => set("contact_name", e.target.value)} placeholder="e.g. Ramesh Broker" />
              </Field>
              <Field label="Contact phone">
                <input className="input" value={form.contact_phone} onChange={(e) => set("contact_phone", e.target.value)} placeholder="Digits, with country code" />
              </Field>

              <Field label="Availability" hint={`Is this ${noun} still on the market?`}>
                <Segmented
                  ariaLabel="Availability"
                  value={form.is_available ? "yes" : "no"}
                  onChange={(value) => set("is_available", value === "yes")}
                  options={[
                    { value: "yes", label: "Available" },
                    { value: "no", label: "Not available" },
                  ]}
                />
              </Field>
              <Field label="Video" hint={`Whether a video of this ${noun} exists.`}>
                <Segmented
                  ariaLabel="Video"
                  value={form.video_available ? "yes" : "no"}
                  onChange={(value) => set("video_available", value === "yes")}
                  options={[
                    { value: "no", label: "No video" },
                    { value: "yes", label: "Video available" },
                  ]}
                />
              </Field>

              <Field
                label="Location link (internal only)"
                hint="A map/pin link for your own team. Never shown on the public site and never included in any message sent to a client, broker or agent."
                span
              >
                <div className="input-wrap">
                  <span className="input-wrap__icon">
                    <IconPin size={16} />
                  </span>
                  <input
                    className="input"
                    style={{ paddingLeft: 40 }}
                    value={form.location_url}
                    onChange={(e) => set("location_url", e.target.value)}
                    placeholder="https://maps.app.goo.gl/…"
                  />
                </div>
              </Field>

              <Field
                label="Instagram reel link"
                hint={`If this ${noun} was posted as an Instagram reel, paste its link — comments and shares of that reel get matched back to this ${noun}.`}
                span
              >
                <div className="input-wrap">
                  <span className="input-wrap__icon">
                    <IconInstagram size={16} />
                  </span>
                  <input
                    className="input"
                    style={{ paddingLeft: 40 }}
                    value={form.instagram_reel_url}
                    onChange={(e) => set("instagram_reel_url", e.target.value)}
                    placeholder="https://www.instagram.com/reel/…"
                  />
                </div>
              </Field>
            </div>

            <Field label="Description">
              <textarea className="textarea" rows={3} value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="Any other details worth noting" />
            </Field>

            <Field label="Extra" hint="Anything else you keep against this record that has no field of its own.">
              <textarea className="textarea" rows={2} value={form.extra_notes} onChange={(e) => set("extra_notes", e.target.value)} placeholder="e.g. floor, facing, parking, possession" />
            </Field>
          </div>
        </div>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <span style={{ marginLeft: "auto" }}>
            <Button variant="primary" onClick={handleSave} busy={saving}>
              {mode === "add" ? `Add ${noun}` : "Save changes"}
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
