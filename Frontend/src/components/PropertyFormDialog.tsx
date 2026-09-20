import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { propertyApi, type PropertyContentFields } from "../api/propertyApi";
import type { PropertyRecord } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { MAX_AREA, MAX_INR, amountError, instagramReelError, urlError } from "../lib/fieldChecks";
import { phoneList, toStoredNumber, toTypedNumber } from "../lib/phone";
import ContactPhonesField, { phoneBoxesError, toPhoneBoxes } from "./ContactPhonesField";
import { useToast } from "./ui/Toast";
import { Button, Segmented } from "./ui/Primitives";
import { FormIssues, hasIssue, useFocusFirstIssue, type FieldIssue } from "./ui/FormIssues";
import { IconAlert, IconImage, IconInstagram, IconPin, IconX } from "./ui/Icons";
import PropertyImagesField from "./PropertyImagesField";

/**
 * The Properties page's Add/Edit dialog — the same field set either way
 * (everything the LLM structuring stage would otherwise fill in, plus
 * instagram_reel_url and super_built, which only a human ever sets). Every
 * field is optional — nothing here has to be filled in.
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
 *
 * "Optional" does not mean "anything goes": a measurement cannot be
 * negative, a contact box has to hold a number, and a link has to be a link
 * — see validationIssues below, and Backend/Model/field_validation.py, which
 * enforces exactly the same rules whatever calls the API.
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
  | "contact_phones"
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
  // One box per number, each holding what is TYPED (the bare ten digits),
  // never the stored "+91..." form — see ContactPhonesField.
  contact_phone_boxes: string[];
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
  contact_phone_boxes: [""],
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
    contact_phone_boxes: toPhoneBoxes(phoneList(property).map(toTypedNumber)),
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
    // Blank boxes are dropped rather than sent as "": emptying every box is
    // how a listing's numbers are cleared, and [] is what says that.
    // toStoredNumber is what puts the "+91" back on; a box that is not a
    // number at all (a legacy note this dialog loaded) is sent as written,
    // exactly as the backend stores it.
    contact_phones: form.contact_phone_boxes
      .map((box) => box.trim())
      .filter(Boolean)
      .map((box) => toStoredNumber(box) ?? box),
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

/**
 * EVERY reason this form refuses to save, in the order the fields are read
 * — not just the first. [] when it is good to send.
 *
 * It used to stop at the first problem, so a listing with a bad price AND a
 * bad link took two saves to find that out. Each entry names the field it
 * belongs to, which is what marks that box red (`hasIssue`) and what the
 * body scrolls to (`focusFirstIssue`).
 *
 * The Instagram link is the one worth naming: a value that is not a reel
 * link still counted as "this property has a reel", which is what put a
 * property with `reel = hello` (and an area of -100 sqft) into the public
 * landing page's Ready to Add list.
 */
function validationIssues(form: FormState): FieldIssue[] {
  const issues: FieldIssue[] = [];
  const add = (field: string, message: string | null) => {
    if (message) issues.push({ field, message });
  };
  for (const [key, label, max] of [
    ["area_sqft", "The area in sqft", MAX_AREA],
    ["area_vaar", "The area in vaar", MAX_AREA],
    ["price_amount_inr", "The price", MAX_INR],
  ] as const) {
    add(key, amountError(form[key], label, max));
  }
  add("contact_phone_boxes", phoneBoxesError(form.contact_phone_boxes));
  add("location_url", urlError(form.location_url));
  add("instagram_reel_url", instagramReelError(form.instagram_reel_url));
  return issues;
}

const GRID_STYLE: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: 14,
};

/**
 * One labelled box.
 *
 * `keyHint` is the difference between "here is some help" and "here is the
 * only spelling this box is stored in". An ordinary hint is a grey line
 * under the input that costs nothing to skip; a key hint gets the tinted,
 * ruled panel `.field__hint--key` describes (styles/controls.css), because
 * skipping it stores the wrong thing without ever failing.
 *
 * Every key hint in this dialog marks the same class of mistake: a box
 * whose value is READ BY MACHINERY — the rupee figure every price filter,
 * sort and match compares, the areas the size filters compare, the numbers
 * that get dialled. Typing "65 lakh" into the rupee box does not warn and
 * does not fail; it leaves the figure empty, and the property then quietly
 * misses every price filter, every sort and every client match, with
 * nothing on the page saying why. That is the bug these panels exist to
 * stop, and it is the reason they are not used on the boxes where free text
 * is genuinely free text.
 */
function Field({
  label,
  hint,
  keyHint,
  span,
  field,
  invalid,
  children,
}: {
  label: string;
  hint?: string;
  /** Draw `hint` as a rule, not a nicety. See above. */
  keyHint?: boolean;
  span?: boolean;
  /** The key this box raises its issues under. Written out as `data-field`
   *  so focusFirstIssue can find it; omitted on boxes nothing can refuse. */
  field?: string;
  /** A save was just refused over this box — label and control go red. */
  invalid?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`field${invalid ? " field--bad" : ""}`}
      data-field={field}
      style={span ? { gridColumn: "1 / -1" } : undefined}
    >
      <label className="field__hint" style={{ fontWeight: 560, color: invalid ? undefined : "var(--ink-2)" }}>
        {label}
      </label>
      {children}
      {hint &&
        (keyHint ? (
          <span className="field__hint field__hint--key">
            <IconAlert size={14} />
            <span>{hint}</span>
          </span>
        ) : (
          <span className="field__hint">{hint}</span>
        ))}
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
  // Why the last Save didn't go through, kept in the dialog rather than only
  // in a toast that fades while the modal is still open — the same treatment
  // the Agents and client dialogs already give it.
  const [formError, setFormError] = useState<string | null>(null);
  // What the LAST Save attempt refused, all of it -- cleared the moment
  // another is made, so the bar never shows a problem that is already
  // fixed. The dialog body is what gets scrolled (to the first offending
  // field); the bar itself is pinned above the footer and never moves.
  const [issues, setIssues] = useState<FieldIssue[]>([]);
  const bodyRef = useRef<HTMLDivElement>(null);
  useFocusFirstIssue(bodyRef, issues);
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
    if (saving) return;
    const found = validationIssues(form);
    setIssues(found);
    if (found.length > 0) {
      setFormError(null);
      return;
    }
    setSaving(true);
    setFormError(null);
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
      const message = friendlyError(err);
      setFormError(message);
      toast.push({ tone: "bad", title: `Couldn't save this ${noun}`, message });
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

        <div className="detail-modal__body" ref={bodyRef}>
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

              <Field
                field="area_sqft"
                invalid={hasIssue(issues, "area_sqft")}
                label="Area (sqft)"
                hint="Digits only, if the size is quoted in sqft."
                keyHint
              >
                <input className="input" type="number" inputMode="decimal" min={0} value={form.area_sqft} onChange={(e) => set("area_sqft", e.target.value)} placeholder="e.g. 1200" />
              </Field>
              <Field
                field="area_vaar"
                invalid={hasIssue(issues, "area_vaar")}
                label="Area (vaar)"
                hint="Digits only, if the size is quoted in vaar / gaj."
                keyHint
              >
                <input className="input" type="number" inputMode="decimal" min={0} value={form.area_vaar} onChange={(e) => set("area_vaar", e.target.value)} placeholder="e.g. 155" />
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

              {/* The two price boxes are the pair the "65 lakh" bug lives
                  in. This one is wording and is shown as written; the one
                  beside it is the number everything COMPUTES on, and it is
                  not filled in from this one — a manually added listing has
                  no structuring stage to work it out (that only runs on
                  WhatsApp messages). So both hints are key hints, and both
                  say what the other box is for: writing the price in only
                  one of them is the actual mistake. */}
              <Field
                label="Price (as written)"
                hint="Wording only — fill in the ₹ box too."
                keyHint
              >
                <input className="input" value={form.price_text} onChange={(e) => set("price_text", e.target.value)} placeholder="e.g. 45L" />
              </Field>
              <Field
                field="price_amount_inr"
                invalid={hasIssue(issues, "price_amount_inr")}
                label="Price (₹ amount)"
                hint="Digits only — 6500000, not &quot;65 lakh&quot;."
                keyHint
              >
                <input className="input" type="number" inputMode="decimal" min={0} value={form.price_amount_inr} onChange={(e) => set("price_amount_inr", e.target.value)} placeholder="e.g. 6500000" />
              </Field>

              <Field label="Contact name">
                <input className="input" value={form.contact_name} onChange={(e) => set("contact_name", e.target.value)} placeholder="e.g. Ramesh Broker" />
              </Field>
              <Field
                field="contact_phone_boxes"
                invalid={hasIssue(issues, "contact_phone_boxes")}
                label="Contact numbers"
                hint="10 digits per box — the +91 is added for you."
                keyHint
              >
                <ContactPhonesField
                  boxes={form.contact_phone_boxes}
                  onChange={(boxes) => set("contact_phone_boxes", boxes)}
                />
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
                field="location_url"
                invalid={hasIssue(issues, "location_url")}
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
                field="instagram_reel_url"
                invalid={hasIssue(issues, "instagram_reel_url")}
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

        <FormIssues issues={issues} error={formError} />

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
