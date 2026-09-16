import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ApiError } from "../api/client";
import { inquiryClientApi, type ClientDetailsBody } from "../api/inquiryClientApi";
import type { InquiryClientRecord } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { loadClientPhoto, setCachedClientPhoto } from "../lib/clientPhotoCache";
import { formatCompactInr, parseCompactInr } from "../lib/formatters";
import { fileToClientPhoto } from "../lib/imageProcessing";
import { FURNISHING_OPTIONS } from "./PropertyFormDialog";
import { useToast } from "./ui/Toast";
import { Button, Note } from "./ui/Primitives";
import { IconAlert, IconCheck, IconImage, IconTrash, IconX } from "./ui/Icons";

/**
 * The Inquiries page's own Add / Edit client dialog — one tall,
 * single-column form, filled in right here on the dashboard. It replaced
 * opening the public site's requirements form in a new tab: staff never
 * leave this page, no form link is minted, and nothing is sent to the
 * client (see Backend/Service/WhatsAppInquiryHandlingService/
 * manual_client_service.py).
 *
 * Edit opens pre-filled with everything on file, photo included, and saves
 * ONLY the fields that actually changed. That is what lets an untouched
 * budget or area list survive a save exactly as stored, and what keeps an
 * untouched requirement from re-running matching.
 *
 * The photo is staff-only. The client is never asked for one — the public
 * requirements form has no photo field (see Backend/Database/
 * client_models.py's ClientRow.photo_url).
 */

/** The public requirements form's own vocabulary (LandingPage/src/components/
 *  RequirementsForm.tsx), so a client reads the same however their details
 *  reached us. A stored value outside it is kept and offered as well, never
 *  silently dropped. */
const PURPOSES = [
  { value: "buy", label: "Buy" },
  { value: "rent", label: "Rent" },
];
const PROPERTY_TYPES = [
  "Flat",
  "Penthouse",
  "Row House",
  "Bungalow",
  "Farm House",
  "Shop",
  "Office",
  "Land/Plot",
  "Warehouse",
  "Other",
];

/** Types whose size is given in vaar (square yards) — land, or a home that
 *  comes with its own plot. Every other type is asked in sq ft. The same
 *  rule the public form applies, and the same one the matcher reads a bare
 *  number by (Backend/Service/ClientPropertyMatchingService/
 *  normalization.py's default_size_unit). */
const VAAR_TYPE_RE = /bungalow|land|plot|farm/i;

function sizeUnitOf(type: string): "vaar" | "sq ft" {
  return VAAR_TYPE_RE.test(type) ? "vaar" : "sq ft";
}

/** The stored comma-separated types ("Flat, Bungalow") back into chips, in
 *  the order they were picked. A value that isn't one of the options (an
 *  older free-text one, or one typed on the public form) is kept as a chip
 *  of its own rather than silently dropped on the next save. */
function splitTypes(raw: string): string[] {
  const picked: string[] = [];
  for (const part of raw.split(",")) {
    const label = part.trim().replace(/\s+/g, " ");
    if (!label) continue;
    const type = PROPERTY_TYPES.find((option) => option.toLowerCase() === label.toLowerCase()) ?? label;
    if (!picked.some((existing) => existing.toLowerCase() === type.toLowerCase())) picked.push(type);
  }
  return picked;
}

/** Saved sizes re-keyed onto the chips they belong to. */
function matchSizes(types: string[], sizes: Record<string, string> | null | undefined): Record<string, string> {
  const matched: Record<string, string> = {};
  for (const [key, value] of Object.entries(sizes ?? {})) {
    const type = types.find((candidate) => candidate.toLowerCase() === key.trim().toLowerCase());
    if (type && value) matched[type] = value;
  }
  return matched;
}

/** The sizes worth sending: one per picked type, in the order picked, blanks
 *  left out — null when none are left, which is how the backend is told
 *  there is nothing to keep. A size typed for a type that is then un-ticked
 *  stays in the boxes (ticking it again brings it back) but is never sent;
 *  the backend applies the identical rule on the way in (see
 *  manual_client_service._apply_size_rule). */
function sizesToSend(form: FormState): Record<string, string> | null {
  const sizes: Record<string, string> = {};
  for (const type of splitTypes(form.property_type)) {
    const size = (form.property_sizes[type] ?? "").trim();
    if (size) sizes[type] = size;
  }
  return Object.keys(sizes).length > 0 ? sizes : null;
}

const EMAIL_PATTERN = /^\S+@\S+\.\S+$/;

interface FormState {
  phone: string;
  name: string;
  email: string;
  current_address: string;
  about_loan: string;
  purpose: string;
  /** Every picked type, comma-separated ("Flat, Bungalow") — the shape the
   *  backend stores and the matcher reads one type at a time. */
  property_type: string;
  /** Free text per picked type, keyed by the type exactly as it appears in
   *  `property_type`. Sent as `property_sizes`, never as text. */
  property_sizes: Record<string, string>;
  bhk: string;
  /** One of FURNISHING_OPTIONS, or "" for no preference — the same three
   *  values a property's own furnishing uses, which is what lets the matcher
   *  compare them at all. */
  furnishing: string;
  budget_min_inr: string;
  budget_max_inr: string;
  preferred_areas: string;
  additional_requirements: string;
}

type TextKey =
  | "name"
  | "email"
  | "current_address"
  | "about_loan"
  | "purpose"
  | "property_type"
  | "bhk"
  | "furnishing"
  | "preferred_areas"
  | "additional_requirements";
const TEXT_KEYS: TextKey[] = [
  "name",
  "email",
  "current_address",
  "about_loan",
  "purpose",
  "property_type",
  "bhk",
  "furnishing",
  "preferred_areas",
  "additional_requirements",
];

const BLANK_FORM: FormState = {
  phone: "",
  name: "",
  email: "",
  current_address: "",
  about_loan: "",
  purpose: "",
  property_type: "",
  property_sizes: {},
  bhk: "",
  furnishing: "",
  budget_min_inr: "",
  budget_max_inr: "",
  preferred_areas: "",
  additional_requirements: "",
};

/** The short form ("45L") only when it reads back as EXACTLY the stored
 *  amount — otherwise the full number, so showing a budget can never be
 *  what changes it. */
function budgetText(amount: number | null): string {
  if (amount === null) return "";
  const compact = formatCompactInr(amount);
  return parseCompactInr(compact) === amount ? compact : String(amount);
}

function toFormState(client: InquiryClientRecord): FormState {
  // Read through splitTypes so the chips and the saved string agree from the
  // start: an edit that touches nothing else then has nothing to send here.
  const types = splitTypes(client.property_type ?? "");
  return {
    phone: client.phone,
    name: client.name ?? "",
    email: client.email ?? "",
    current_address: client.current_address ?? "",
    about_loan: client.about_loan ?? "",
    purpose: client.purpose ?? "",
    property_type: types.join(", "),
    property_sizes: matchSizes(types, client.property_sizes),
    bhk: client.bhk ?? "",
    furnishing: client.furnishing ?? "",
    budget_min_inr: budgetText(client.budget_min_inr),
    budget_max_inr: budgetText(client.budget_max_inr),
    preferred_areas: client.preferred_areas ?? "",
    additional_requirements: client.additional_requirements ?? "",
  };
}

function textOrNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

/** Split on commas or slashes, trimmed, de-duplicated case-insensitively and
 *  re-joined with ", " — the shape the public form stores (its
 *  lib/suratAreas.ts splitAreas/joinAreas), so both read identically in the
 *  table and to the matcher. */
function normalizeAreas(raw: string): string | null {
  const seen = new Set<string>();
  const areas: string[] = [];
  for (const part of raw.split(/[,/]/)) {
    const cleaned = part.trim();
    if (!cleaned || seen.has(cleaned.toLowerCase())) continue;
    seen.add(cleaned.toLowerCase());
    areas.push(cleaned);
  }
  return areas.length > 0 ? areas.join(", ") : null;
}

/**
 * What to send. On Add, every filled-in field. On Edit, ONLY the fields
 * whose input differs from what the dialog opened with — the endpoint
 * applies just the keys present, so everything untouched stays exactly as
 * stored, byte for byte. Validates only what is being sent: a value the
 * public form stored that this dialog would not have accepted is not the
 * operator's problem until they change it.
 */
function buildBody(
  form: FormState,
  initial: FormState,
  mode: "add" | "edit",
  client: InquiryClientRecord | undefined,
): { body: ClientDetailsBody; error: string | null } {
  const changed = (key: keyof FormState) => mode === "add" || form[key] !== initial[key];
  const body: ClientDetailsBody = {};

  let min = client?.budget_min_inr ?? null;
  let max = client?.budget_max_inr ?? null;
  for (const [key, label] of [
    ["budget_min_inr", "minimum"],
    ["budget_max_inr", "maximum"],
  ] as const) {
    if (!changed(key)) continue;
    const raw = form[key].trim();
    const amount = raw ? parseCompactInr(raw) : null;
    if (raw && amount === null) {
      return { body, error: `The ${label} budget isn't an amount — try 45L, 1.2cr or 4500000.` };
    }
    if (key === "budget_min_inr") min = amount;
    else max = amount;
    if (mode === "edit" || amount !== null) body[key] = amount;
  }
  if ((changed("budget_min_inr") || changed("budget_max_inr")) && min !== null && max !== null && min > max) {
    return { body, error: "The minimum budget is above the maximum." };
  }

  if (changed("email") && form.email.trim() && !EMAIL_PATTERN.test(form.email.trim())) {
    return { body, error: "That email address doesn't look right." };
  }

  for (const key of TEXT_KEYS) {
    if (!changed(key)) continue;
    const value = key === "preferred_areas" ? normalizeAreas(form[key]) : textOrNull(form[key]);
    if (mode === "add" && value === null) continue;
    body[key] = value;
  }

  // Sizes ride with the types they belong to, and are compared by what
  // would actually be SENT — so re-typing the same size, or un-ticking a
  // type whose box was empty anyway, is not a change worth saving.
  const sizes = sizesToSend(form);
  if (mode === "add" ? sizes !== null : JSON.stringify(sizes) !== JSON.stringify(sizesToSend(initial))) {
    body.property_sizes = sizes;
  }
  return { body, error: null };
}

function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor?: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="field">
      <label className="field__hint" htmlFor={htmlFor} style={{ fontWeight: 560, color: "var(--ink-2)" }}>
        {label}
      </label>
      {children}
      {hint && <span className="field__hint">{hint}</span>}
    </div>
  );
}

/** The options for a select: the known vocabulary, plus the stored value
 *  when it is something else — so opening Edit never blanks it. */
function withCurrent(options: { value: string; label: string }[], current: string) {
  return current && !options.some((option) => option.value === current)
    ? [...options, { value: current, label: current }]
    : options;
}

export default function ClientFormDialog({
  mode,
  client,
  assignedCount = 0,
  onOpenProperties,
  onClose,
  onSaved,
}: {
  mode: "add" | "edit";
  /** The client being edited — required for "edit", ignored for "add". */
  client?: InquiryClientRecord;
  /** How many of this client's properties are out with an agent right now.
   *  Above zero the requirement fields open read-only: re-scoring would
   *  rewrite the property list underneath visits already booked. The
   *  backend enforces the same rule on save regardless. */
  assignedCount?: number;
  /** Closes this dialog and opens the client's properties, where the
   *  assignments that lock the requirements can be cleared. */
  onOpenProperties?: () => void;
  onClose: () => void;
  onSaved: (client: InquiryClientRecord, mode: "add" | "edit") => void;
}) {
  const toast = useToast();
  const [initial] = useState<FormState>(() => (mode === "edit" && client ? toFormState(client) : BLANK_FORM));
  const [form, setForm] = useState<FormState>(initial);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const requirementsLocked = mode === "edit" && assignedCount > 0;

  // --- photo -------------------------------------------------------------
  // `photoDirty` is what decides whether the photo is sent at all: an edit
  // that never touched it leaves photo_url out of the request, so the
  // stored photo can't be lost to one that failed to load here.
  const hadPhoto = mode === "edit" && Boolean(client?.has_photo);
  const [photo, setPhoto] = useState<string | null>(null);
  const [photoDirty, setPhotoDirty] = useState(false);
  const photoDirtyRef = useRef(false);
  const [photoLoading, setPhotoLoading] = useState(hadPhoto);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [photoOver, setPhotoOver] = useState(false);

  useEffect(() => {
    if (!hadPhoto || !client) return;
    let cancelled = false;
    loadClientPhoto(client)
      .then((url) => {
        // A photo picked while this was loading wins — it is newer.
        if (!cancelled && !photoDirtyRef.current) setPhoto(url);
      })
      .catch((err) => {
        if (!cancelled) {
          setPhotoError(
            `Couldn't load the current photo (${friendlyError(err)}). Saving keeps it as it is unless you choose a new one.`,
          );
        }
      })
      .finally(() => {
        if (!cancelled) setPhotoLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // Once, for the client this dialog was opened on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  // The error sits at the foot of a tall form — bring it into view.
  useEffect(() => {
    if (formError) errorRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [formError]);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  /** Ticks or unticks a type, keeping the order they were picked in. The
   *  size typed against a type survives it being unticked — ticking it
   *  again brings the size back — but only picked types are ever sent. */
  function toggleType(type: string) {
    const picked = splitTypes(form.property_type);
    const next = picked.some((existing) => existing.toLowerCase() === type.toLowerCase())
      ? picked.filter((existing) => existing.toLowerCase() !== type.toLowerCase())
      : [...picked, type];
    set("property_type", next.join(", "));
  }

  function setPhotoValue(next: string | null) {
    photoDirtyRef.current = true;
    setPhotoDirty(true);
    setPhoto(next);
    setPhotoError(null);
  }

  async function pickPhoto(file: File | undefined) {
    if (!file) return;
    if (file.type && !file.type.startsWith("image/")) {
      setPhotoError("That file isn't an image — choose a photo.");
      return;
    }
    setPhotoBusy(true);
    setPhotoError(null);
    try {
      const dataUrl = await fileToClientPhoto(file);
      if (!dataUrl.startsWith("data:image/")) throw new Error("Not an image");
      setPhotoValue(dataUrl);
    } catch {
      setPhotoError("That photo couldn't be read — try a JPG or PNG.");
    } finally {
      setPhotoBusy(false);
    }
  }

  /** Blur: show the short form ("85L") when that is exact — see budgetText. */
  function onBudgetBlur(key: "budget_min_inr" | "budget_max_inr") {
    const amount = parseCompactInr(form[key]);
    if (amount !== null) set(key, budgetText(amount));
  }

  async function handleSave() {
    if (saving) return;
    const phone = form.phone.trim();
    if (mode === "add" && !phone) {
      setFormError("Enter the client's WhatsApp number — it's what every message and match is tied to.");
      return;
    }
    const { body, error } = buildBody(form, initial, mode, client);
    if (error) {
      setFormError(error);
      return;
    }
    if (photoDirty) body.photo_url = photo;
    if (mode === "edit" && Object.keys(body).length === 0) {
      toast.push({ tone: "info", title: "Nothing to save", message: "No changes were made." });
      onClose();
      return;
    }

    setSaving(true);
    setFormError(null);
    try {
      const saved =
        mode === "add"
          ? await inquiryClientApi.createClient({ ...body, phone })
          : await inquiryClientApi.updateClient(client!.phone, body);
      // Filed under the record's NEW updated_at, so the details dialog shows
      // the photo it was just handed without asking the backend for it —
      // but only when this dialog actually knows what the photo now is.
      const photoKnown = photoDirty || !hadPhoto || (!photoLoading && photoError === null);
      if (photoKnown) setCachedClientPhoto(saved, photo);
      toast.push({
        tone: "ok",
        title: mode === "add" ? "Client added" : "Client updated",
        message: saved.name || saved.phone,
      });
      onSaved(saved, mode);
    } catch (err) {
      // The backend's own wording ("already exists", "can't change right
      // now") is the explanation — shown as it is, not as a status code.
      setFormError(err instanceof ApiError ? err.message : friendlyError(err));
    } finally {
      setSaving(false);
    }
  }

  const title = mode === "add" ? "Add a client" : client?.name || client?.phone || "Edit client";
  const purposeOptions = withCurrent(PURPOSES, form.purpose);
  const furnishingOptions = withCurrent(
    FURNISHING_OPTIONS.map((option) => ({ value: option, label: option })),
    form.furnishing,
  );
  // Every option, plus any stored type that isn't one of them — the chips'
  // equivalent of withCurrent above, so opening Edit never drops a type.
  const pickedTypes = splitTypes(form.property_type);
  const typeChoices = [...PROPERTY_TYPES, ...pickedTypes.filter((type) => !PROPERTY_TYPES.includes(type))];
  const frameClass = [
    "client-photo__frame",
    photo && "client-photo__frame--filled",
    photoOver && "client-photo__frame--over",
  ]
    .filter(Boolean)
    .join(" ");

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}>
      <div
        className="detail-modal detail-modal--tall anim-rise"
        role="dialog"
        aria-modal="true"
        aria-label={mode === "add" ? "Add client" : "Edit client"}
      >
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">{mode === "add" ? "New client" : "Edit client"}</div>
            <h2 className="detail-modal__title cell-truncate">{title}</h2>
            <div className="detail-modal__sub">
              {mode === "add"
                ? "Filled in here, on the dashboard — nothing is sent to the client."
                : "Pre-filled with everything on file. Only what you change is saved."}
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={saving} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          <div className="stack stack-4">
            {requirementsLocked && (
              <Note tone="warn" icon={<IconAlert size={16} />}>
                <strong>
                  {assignedCount} propert{assignedCount === 1 ? "y is" : "ies are"} out with an agent for a site visit
                </strong>
                , so this client's requirements are locked — changing them would re-run matching underneath visits that
                are already booked. Clear those assignments from their properties first. Name, email and photo can still
                be changed.
                {onOpenProperties && (
                  <div style={{ marginTop: 10 }}>
                    <Button size="sm" onClick={onOpenProperties} disabled={saving}>
                      Open properties
                    </Button>
                  </div>
                )}
              </Note>
            )}

            <Field label="Photo">
              <div className="client-photo">
                <div
                  className={frameClass}
                  onDragOver={(event) => {
                    if (!event.dataTransfer.types.includes("Files")) return;
                    event.preventDefault();
                    setPhotoOver(true);
                  }}
                  onDragLeave={() => setPhotoOver(false)}
                  onDrop={(event) => {
                    if (!event.dataTransfer.types.includes("Files")) return;
                    event.preventDefault();
                    setPhotoOver(false);
                    if (!saving) void pickPhoto(event.dataTransfer.files?.[0]);
                  }}
                >
                  {photoLoading || photoBusy ? (
                    <span className="spinner" />
                  ) : photo ? (
                    <img src={photo} alt={`Photo of ${form.name || form.phone || "the client"}`} />
                  ) : (
                    <IconImage size={26} />
                  )}
                </div>
                <div className="client-photo__side">
                  <div className="client-photo__buttons">
                    <label className={`btn btn--sm client-photo__upload${saving || photoBusy ? " is-disabled" : ""}`}>
                      <IconImage size={14} />
                      {photo ? "Change photo" : "Upload photo"}
                      <input
                        type="file"
                        accept="image/*"
                        disabled={saving || photoBusy}
                        onChange={(event) => {
                          void pickPhoto(event.target.files?.[0]);
                          event.target.value = "";
                        }}
                      />
                    </label>
                    {photo && (
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={<IconTrash size={14} />}
                        onClick={() => setPhotoValue(null)}
                        disabled={saving || photoBusy}
                      >
                        Remove
                      </Button>
                    )}
                  </div>
                  <span className={`field__hint${photoError ? " client-photo__error" : ""}`}>
                    {photoError ??
                      "Optional, and only ever added here — the client is never asked for a photo. Drop an image on the circle, or upload one."}
                  </span>
                </div>
              </div>
            </Field>

            <Field
              label="WhatsApp number"
              htmlFor="client-form-phone"
              hint={
                mode === "add"
                  ? "With or without the country code — it's how this client is recognised everywhere."
                  : "A client's number is who they are here, so it can't be changed."
              }
            >
              <input
                id="client-form-phone"
                className="input"
                type="tel"
                inputMode="tel"
                autoComplete="off"
                autoFocus={mode === "add"}
                value={form.phone}
                onChange={(event) => set("phone", event.target.value)}
                placeholder="e.g. 98765 43210"
                disabled={mode === "edit" || saving}
                maxLength={24}
              />
            </Field>

            <Field label="Name" htmlFor="client-form-name">
              <input
                id="client-form-name"
                className="input"
                value={form.name}
                onChange={(event) => set("name", event.target.value)}
                placeholder="e.g. Rohan Mehta"
                disabled={saving}
                maxLength={120}
              />
            </Field>

            <Field label="Email" htmlFor="client-form-email" hint="Optional.">
              <input
                id="client-form-email"
                className="input"
                type="email"
                value={form.email}
                onChange={(event) => set("email", event.target.value)}
                placeholder="name@example.com"
                disabled={saving}
                maxLength={160}
              />
            </Field>

            <Field
              label="Current address"
              htmlFor="client-form-current-address"
              hint="Where they live now — not what they're looking for."
            >
              <textarea
                id="client-form-current-address"
                className="textarea"
                rows={2}
                value={form.current_address}
                onChange={(event) => set("current_address", event.target.value)}
                placeholder="e.g. B-402, Shilp Residency, Vesu"
                disabled={saving}
                maxLength={500}
              />
            </Field>

            <Field label="Loan" htmlFor="client-form-about-loan" hint="Optional — pre-approval, bank, amount, cash buyer.">
              <textarea
                id="client-form-about-loan"
                className="textarea"
                rows={2}
                value={form.about_loan}
                onChange={(event) => set("about_loan", event.target.value)}
                placeholder="e.g. HDFC pre-approved up to 60L"
                disabled={saving}
                maxLength={500}
              />
            </Field>

            <div className="detail-modal__eyebrow" style={{ margin: "6px 0 -4px" }}>
              Requirements
            </div>

            <Field label="Wants to" htmlFor="client-form-purpose">
              <select
                id="client-form-purpose"
                className="select"
                value={form.purpose}
                onChange={(event) => set("purpose", event.target.value)}
                disabled={requirementsLocked || saving}
              >
                <option value="">—</option>
                {purposeOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>

            {/* Chips, not a dropdown: a client happy with a flat OR a
                bungalow should be able to say both, exactly as the public
                requirements form lets them say it. Stored as one
                comma-separated string, which is how the matcher reads it —
                each picked type scored on its own. */}
            <Field label="Property type" hint="Pick every type they'd consider — as many as you like.">
              <div className="type-chips" role="group" aria-label="Property type">
                {typeChoices.map((type) => {
                  const active = pickedTypes.some((picked) => picked.toLowerCase() === type.toLowerCase());
                  return (
                    <button
                      key={type}
                      type="button"
                      className={`type-chip${active ? " type-chip--on" : ""}`}
                      onClick={() => toggleType(type)}
                      aria-pressed={active}
                      disabled={requirementsLocked || saving}
                    >
                      {active && <IconCheck size={13} />}
                      {type}
                    </button>
                  );
                })}
              </div>
            </Field>

            {/* One optional box per picked type, in the unit people actually
                use for it. Free text on purpose — "about 1200", "150–250"
                both read fine (Backend/Service/ClientPropertyMatchingService/
                normalization.py's parse_size_requirement) — and it only ever
                nudges the matching, never rules a property out. */}
            {pickedTypes.length > 0 && (
              <Field
                label="Preferred size (optional)"
                hint="Only if they have one in mind — a rough number or a range is perfect."
              >
                <div className="size-rows">
                  {pickedTypes.map((type) => {
                    const unit = sizeUnitOf(type);
                    const inputId = `client-form-size-${type.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
                    return (
                      <div className="size-row" key={type}>
                        <label className="size-row__type" htmlFor={inputId}>
                          {type}
                        </label>
                        <div className="size-row__box">
                          <input
                            id={inputId}
                            className="input"
                            value={form.property_sizes[type] ?? ""}
                            onChange={(event) =>
                              setForm((prev) => ({
                                ...prev,
                                property_sizes: { ...prev.property_sizes, [type]: event.target.value },
                              }))
                            }
                            placeholder={unit === "vaar" ? "e.g. 200 or 150–250" : "e.g. 1200 or 1000–1500"}
                            disabled={requirementsLocked || saving}
                            maxLength={80}
                          />
                          <span className="size-row__unit" aria-hidden="true">
                            {unit}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </Field>
            )}

            {/* Each of these maps to a shape the matcher actually parses
                (normalization.py's parse_bhk_intent) — "2 to 5" takes
                everything in between, "3+" opens up everything larger,
                "exactly 3" closes it down again. */}
            <Field
              label="BHK"
              htmlFor="client-form-bhk"
              hint="Write it however they think of it — “3 BHK”, “2 to 5 BHK”, “2 or 3 BHK”, “3+ BHK”, “exactly 3 BHK”, “1 RK”."
            >
              <input
                id="client-form-bhk"
                className="input"
                value={form.bhk}
                onChange={(event) => set("bhk", event.target.value)}
                placeholder="e.g. 3 BHK, 2 to 5 BHK, 3+ BHK"
                disabled={requirementsLocked || saving}
                maxLength={40}
              />
            </Field>

            {/* A gentle preference, and matching treats it as one: it is the
                lowest-weighted field the matcher scores (Backend/Service/
                ClientPropertyMatchingService/scoring.py's _FURNISHING_WEIGHT),
                because furnishing is the easiest thing about a property to
                change. Left at "—" it is not a preference at all and is never
                scored. */}
            <Field
              label="Furnishing"
              htmlFor="client-form-furnishing"
              hint="Optional — nudges matching gently, never rules a property out."
            >
              <select
                id="client-form-furnishing"
                className="select"
                value={form.furnishing}
                onChange={(event) => set("furnishing", event.target.value)}
                disabled={requirementsLocked || saving}
              >
                <option value="">—</option>
                {furnishingOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="Budget (₹)" hint="Write it the way they'd say it — 2.5 cr, 85 L, or 25 K a month to rent.">
              {/* The key, before the boxes — it answers "how do I write
                  this?" before anyone has to wonder, as the public form
                  does. */}
              <div className="budget-units">
                <span>
                  <b>cr</b> crore
                </span>
                <span>
                  <b>L</b> lakh
                </span>
                <span>
                  <b>K</b> thousand
                </span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 10 }}>
                <input
                  className="input"
                  inputMode="decimal"
                  aria-label="Minimum budget"
                  value={form.budget_min_inr}
                  onChange={(event) => set("budget_min_inr", event.target.value)}
                  onBlur={() => onBudgetBlur("budget_min_inr")}
                  placeholder="Min — e.g. 80 L"
                  disabled={requirementsLocked || saving}
                  maxLength={20}
                />
                <input
                  className="input"
                  inputMode="decimal"
                  aria-label="Maximum budget"
                  value={form.budget_max_inr}
                  onChange={(event) => set("budget_max_inr", event.target.value)}
                  onBlur={() => onBudgetBlur("budget_max_inr")}
                  placeholder="Max — e.g. 1.2 cr"
                  disabled={requirementsLocked || saving}
                  maxLength={20}
                />
              </div>
            </Field>

            <Field label="Preferred areas" htmlFor="client-form-areas" hint="Separate areas with commas.">
              <input
                id="client-form-areas"
                className="input"
                value={form.preferred_areas}
                onChange={(event) => set("preferred_areas", event.target.value)}
                placeholder="e.g. Vesu, Althan, Pal"
                disabled={requirementsLocked || saving}
              />
            </Field>

            <Field label="Anything else?" htmlFor="client-form-notes">
              <textarea
                id="client-form-notes"
                className="textarea"
                rows={3}
                value={form.additional_requirements}
                onChange={(event) => set("additional_requirements", event.target.value)}
                placeholder="Parking, floor, possession date — anything that matters to them…"
                disabled={requirementsLocked || saving}
                maxLength={1000}
              />
            </Field>

            {formError && (
              <div ref={errorRef}>
                <Note tone="bad" icon={<IconAlert size={16} />}>
                  {formError}
                </Note>
              </div>
            )}
          </div>
        </div>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <span style={{ marginLeft: "auto" }}>
            <Button variant="primary" onClick={handleSave} busy={saving} disabled={photoBusy}>
              {mode === "add" ? "Add client" : "Save changes"}
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
