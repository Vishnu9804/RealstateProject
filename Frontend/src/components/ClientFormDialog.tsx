import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ApiError } from "../api/client";
import { inquiryClientApi, type ClientDetailsBody } from "../api/inquiryClientApi";
import { settingsApi } from "../api/settingsApi";
import type { InquiryClientRecord } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { loadClientPhoto, setCachedClientPhoto } from "../lib/clientPhotoCache";
import { SIZE_HINT, splitTypes } from "../lib/propertyTypeOptions";
import { formatCompactInr, parseCompactInr } from "../lib/formatters";
import { fileToClientPhoto } from "../lib/imageProcessing";
import { formatPhone, phoneFieldError, toStoredNumber, toTypedNumber } from "../lib/phone";
import { joinAreas, mergeAreas, splitAreas, SURAT_AREAS } from "../lib/suratAreas";
import { PhoneInput } from "./ContactPhonesField";
import { FURNISHING_OPTIONS } from "./PropertyFormDialog";
import { useToast } from "./ui/Toast";
import AreaPicker from "./ui/AreaPicker";
import { Button, Note } from "./ui/Primitives";
import { FormIssues, hasIssue, useFocusFirstIssue, type FieldIssue } from "./ui/FormIssues";
import {
  PropertyTypeChips,
  TypeSizeRows,
  sizesToSend as sizesToSendFor,
  toTypeSizeState,
  typeSizeIssues,
  type TypeSizeState,
} from "./ui/PropertyTypePicker";
import { IconAlert, IconImage, IconPlus, IconTrash, IconX } from "./ui/Icons";

/**
 * The Inquiries page's own Add / Edit client dialog — one tall,
 * single-column form, filled in right here on the dashboard. It replaced
 * opening the public site's requirements form in a new tab: staff never
 * leave this page, no form link is minted, and nothing is sent to the
 * client (see Backend/Service/WhatsAppInquiryHandlingService/
 * manual_client_service.py).
 *
 * FIVE THINGS ARE NOT OPTIONAL HERE (marked * on their labels):
 *  - the WhatsApp number, which is the client's identity in this system;
 *  - their name;
 *  - whether they want to Buy or Rent;
 *  - at least one property type;
 *  - at least one of the two budget boxes.
 * The last three are the requirement fields, and they are checked only
 * while those fields are actually editable: when the client has properties
 * out with an agent their requirements are LOCKED (see `requirementsLocked`
 * below), the boxes are disabled, and insisting on a value nobody can type
 * would trap a name or photo edit in a dialog with no way out. The number
 * is likewise checked on Add only — on Edit its box is disabled and always
 * already holds one.
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

/** The sizes worth sending: one per picked type, in the order picked, blanks
 *  left out — null when none are left, which is how the backend is told
 *  there is nothing to keep. A size typed for a type that is then un-ticked
 *  stays in the boxes (ticking it again brings it back) but is never sent;
 *  the backend applies the identical rule on the way in (see
 *  manual_client_service._apply_size_rule).
 *
 *  Each value carries the unit picked on its capsule ("1000-1500 sqft",
 *  "150 var"), which is what the matcher reads it by — see
 *  components/ui/PropertyTypePicker and Backend/Service/
 *  ClientPropertyMatchingService/normalization.py's parse_size_requirement. */
function sizesToSend(form: FormState): Record<string, string> | null {
  return sizesToSendFor(splitTypes(form.property_type), form.property_sizes);
}

/** The boxes as the backend stores them: "+91" back on the front of each
 *  one, blanks dropped, in the order typed — the same "empty means cleared"
 *  shape sizesToSend uses. De-duplication and the count cap are the
 *  backend's own job (manual_client_service._clean_additional_phones); this
 *  only avoids sending obviously-empty rows left by a removed box.
 *
 *  The boxes hold the ten digits a person typed, never the stored form (see
 *  PhoneInput) — toStoredNumber is the one end of that conversion and
 *  toTypedNumber in toFormState below is the other. A box that is not a
 *  number at all is a legacy value this dialog loaded and is sent exactly
 *  as written; the form refuses to save while one is there (phoneFieldError),
 *  so the fallback only matters for the frame before that check runs. */
function phonesToSend(phones: string[]): string[] | null {
  const cleaned = phones
    .map((phone) => phone.trim())
    .filter(Boolean)
    .map((phone) => toStoredNumber(phone) ?? phone);
  return cleaned.length > 0 ? cleaned : null;
}

const EMAIL_PATTERN = /^\S+@\S+\.\S+$/;

interface FormState {
  phone: string;
  name: string;
  email: string;
  current_address: string;
  about_loan: string;
  /** Free-form catch-all notes — same staff-only rule as the two above. */
  notes: string;
  /** Extra numbers besides `phone` — never verified, just stored and shown
   *  as typed. Kept as a plain list of boxes (not comma-separated text) so
   *  a number containing a comma or a name note isn't mistaken for two. */
  additional_phones: string[];
  purpose: string;
  /** Every picked type, comma-separated ("Flat, Bungalow") — the shape the
   *  backend stores and the matcher reads one type at a time. */
  property_type: string;
  /** The number/range and the unit typed for each picked type, held apart
   *  while they are being edited and joined into the one string the backend
   *  stores at save time (see sizesToSend). Keyed by the type exactly as it
   *  appears in `property_type`. Sent as `property_sizes`, never as text. */
  property_sizes: TypeSizeState;
  bhk: string;
  /** One of FURNISHING_OPTIONS, or "" for no preference — the same three
   *  values a property's own furnishing uses, which is what lets the matcher
   *  compare them at all. */
  furnishing: string;
  budget_min_inr: string;
  budget_max_inr: string;
  /** Picked from the area picker (or typed in as a new one) — see
   *  components/ui/AreaPicker.tsx. Joined into the one stored string only
   *  at save time (see buildBody), the same way property_sizes and
   *  additional_phones below are handled outside the generic TEXT_KEYS
   *  loop. */
  preferred_areas: string[];
  additional_requirements: string;
}

type TextKey =
  | "name"
  | "email"
  | "current_address"
  | "about_loan"
  | "notes"
  | "purpose"
  | "property_type"
  | "bhk"
  | "furnishing"
  | "additional_requirements";
const TEXT_KEYS: TextKey[] = [
  "name",
  "email",
  "current_address",
  "about_loan",
  "notes",
  "purpose",
  "property_type",
  "bhk",
  "furnishing",
  "additional_requirements",
];

const BLANK_FORM: FormState = {
  phone: "",
  name: "",
  email: "",
  current_address: "",
  about_loan: "",
  notes: "",
  additional_phones: [],
  purpose: "",
  property_type: "",
  property_sizes: { texts: {}, units: {} },
  bhk: "",
  furnishing: "",
  budget_min_inr: "",
  budget_max_inr: "",
  preferred_areas: [],
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
    // The ten digits, not the stored "+91…" — every phone box in the
    // application now holds what was typed and prints the country code as
    // furniture beside it (PhoneInput). phonesToSend/toStoredNumber put it
    // back on the way out.
    phone: toTypedNumber(client.phone),
    name: client.name ?? "",
    email: client.email ?? "",
    current_address: client.current_address ?? "",
    about_loan: client.about_loan ?? "",
    notes: client.notes ?? "",
    additional_phones: (client.additional_phones ?? []).map(toTypedNumber),
    purpose: client.purpose ?? "",
    property_type: types.join(", "),
    property_sizes: toTypeSizeState(types, client.property_sizes),
    bhk: client.bhk ?? "",
    furnishing: client.furnishing ?? "",
    budget_min_inr: budgetText(client.budget_min_inr),
    budget_max_inr: budgetText(client.budget_max_inr),
    preferred_areas: splitAreas(client.preferred_areas),
    additional_requirements: client.additional_requirements ?? "",
  };
}

function textOrNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
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
): { body: ClientDetailsBody; issues: FieldIssue[] } {
  const changed = (key: keyof FormState) => mode === "add" || form[key] !== initial[key];
  const body: ClientDetailsBody = {};
  // EVERY problem, not the first one. This used to return on the first
  // fault it met, so a client with a bad budget AND a bad size took one
  // Save per mistake to find out. The body is still built as far as it can
  // be -- it is simply not sent while issues is non-empty.
  const issues: FieldIssue[] = [];
  const add = (field: string, message: string | null) => {
    if (message) issues.push({ field, message });
  };

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
      add(key, `The ${label} budget isn't an amount — try 45L, 1.2cr or 4500000.`);
      continue;
    }
    if (key === "budget_min_inr") min = amount;
    else max = amount;
    if (mode === "edit" || amount !== null) body[key] = amount;
  }
  // Only when neither box is separately wrong, so a reversed pair is not
  // also told it is unreadable. Raised against the maximum: with two boxes
  // and one relationship between them, that is the one just left.
  if (issues.length === 0 && (changed("budget_min_inr") || changed("budget_max_inr")) && min !== null && max !== null && min > max) {
    add("budget_max_inr", "The minimum budget is above the maximum.");
  }

  if (changed("email") && form.email.trim() && !EMAIL_PATTERN.test(form.email.trim())) {
    add("email", "That email doesn't look right — try name@example.com.");
  }

  // A size box per picked type: what is typed has to be a number or a
  // range, and a box with anything in it has to say whether that is sqft or
  // var. Both rules live with the picker itself, so this dialog, the Broker
  // Requirements dialog and the public form can never drift apart on them.
  for (const issue of typeSizeIssues(splitTypes(form.property_type), form.property_sizes)) {
    add(issue.field, issue.message);
  }

  for (const key of TEXT_KEYS) {
    if (!changed(key)) continue;
    const value = textOrNull(form[key]);
    if (mode === "add" && value === null) continue;
    body[key] = value;
  }

  // Preferred areas, picked from AreaPicker rather than typed — joined into
  // the one string the backend stores, and compared by what would actually
  // be SENT (the same rule sizes and additional_phones follow above), so
  // re-picking the same areas in a different order is not a change worth
  // saving or re-matching over.
  const areas = form.preferred_areas.length > 0 ? joinAreas(form.preferred_areas) : null;
  const initialAreas = initial.preferred_areas.length > 0 ? joinAreas(initial.preferred_areas) : null;
  if (mode === "add" ? areas !== null : areas !== initialAreas) {
    body.preferred_areas = areas;
  }

  // Sizes ride with the types they belong to, and are compared by what
  // would actually be SENT — so re-typing the same size, or un-ticking a
  // type whose box was empty anyway, is not a change worth saving.
  const sizes = sizesToSend(form);
  if (mode === "add" ? sizes !== null : JSON.stringify(sizes) !== JSON.stringify(sizesToSend(initial))) {
    body.property_sizes = sizes;
  }

  // Same idea for the extra phone boxes: compared by what would actually be
  // SENT, so an empty box left by "+ Add" and never filled in is not a
  // change worth saving.
  const phones = phonesToSend(form.additional_phones);
  if (mode === "add" ? phones !== null : JSON.stringify(phones) !== JSON.stringify(phonesToSend(initial.additional_phones))) {
    body.additional_phones = phones;
  }
  return { body, issues };
}

/** Same two kinds of hint PropertyFormDialog's Field draws, for the same
 *  reason and with the same styling — see that component's comment.
 *  `keyHint` marks a box whose value is read by machinery rather than only
 *  by a person, where typing it the wrong way stores the wrong thing
 *  without ever failing. */
function Field({
  label,
  htmlFor,
  hint,
  keyHint,
  field,
  invalid,
  required,
  children,
}: {
  label: string;
  htmlFor?: string;
  hint?: React.ReactNode;
  keyHint?: boolean;
  /** This box has to be filled in — marks the label with a red asterisk, so
   *  the rule is visible before Save rather than only after it. */
  required?: boolean;
  /** The key this box raises its issues under -- written out as
   *  `data-field` so focusFirstIssue can find it. */
  field?: string;
  /** A save was just refused over this box. */
  invalid?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`field${invalid ? " field--bad" : ""}`} data-field={field}>
      <label className="field__hint" htmlFor={htmlFor} style={{ fontWeight: 560, color: invalid ? undefined : "var(--ink-2)" }}>
        {label}
        {required && (
          <span style={{ color: "var(--bad)", marginLeft: 3 }} title="Required" aria-hidden>
            *
          </span>
        )}
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
  // Everything the LAST Save attempt refused, together, in the pinned bar
  // above the footer -- see components/ui/FormIssues.
  const [issues, setIssues] = useState<FieldIssue[]>([]);
  const bodyRef = useRef<HTMLDivElement>(null);
  useFocusFirstIssue(bodyRef, issues);
  const requirementsLocked = mode === "edit" && assignedCount > 0;

  // The area picker's offered list: the well-known Surat localities plus
  // whichever ones this app's own Settings page has configured that aren't
  // already on it (see lib/suratAreas.ts's mergeAreas) — the exact same
  // source the public requirements form's own picker reads. Failure is
  // silent on purpose: the picker still works perfectly off the hardcoded
  // list, and typing an area is always allowed regardless.
  const [areaOptions, setAreaOptions] = useState<string[]>(SURAT_AREAS);
  useEffect(() => {
    let cancelled = false;
    settingsApi
      .getAreaKeywords()
      .then((settings) => {
        if (!cancelled && settings.keywords.length > 0) setAreaOptions(mergeAreas(SURAT_AREAS, settings.keywords));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

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

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  /** Adds one empty extra-phone box, focused via the "+" button. */
  function addPhone() {
    setForm((prev) => ({ ...prev, additional_phones: [...prev.additional_phones, ""] }));
  }

  function setPhoneAt(index: number, value: string) {
    setForm((prev) => ({
      ...prev,
      additional_phones: prev.additional_phones.map((phone, i) => (i === index ? value : phone)),
    }));
  }

  function removePhoneAt(index: number) {
    setForm((prev) => ({ ...prev, additional_phones: prev.additional_phones.filter((_, i) => i !== index) }));
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
    const typedPhone = form.phone.trim();
    // The same ten-digit rule every other phone box in the application now
    // applies, checked here rather than only by the backend so the answer
    // arrives in the dialog the box is in.
    //
    // The primary number is checked in "add" only. In "edit" its box is
    // disabled and holds whatever is already stored — a client's number is
    // who they are here and cannot be changed from this dialog at all — so
    // refusing to save a name change over a number nobody can retype from
    // here would be a dead end, not a correction. The other numbers ARE
    // editable in both modes, so they are checked in both.
    const phoneIssues: FieldIssue[] = [];
    if (mode === "add") {
      // Missing and malformed are one box's two ways of being wrong.
      // Raised together with everything else rather than returned on the
      // spot, so an Add with no number AND no name is told both at once.
      const bad = typedPhone ? phoneFieldError(typedPhone) : "Enter the client's WhatsApp number.";
      if (bad) phoneIssues.push({ field: "phone", message: bad });
    }
    form.additional_phones.forEach((extra, index) => {
      const bad = phoneFieldError(extra);
      if (bad) phoneIssues.push({ field: `other-phone-${index}`, message: bad });
    });
    // "+919016987654" — the one shape the database holds. The backend
    // normalizes it again (phone_utils.normalize_phone); sending it already
    // canonical is what keeps the row, the dialog and every other page
    // showing one spelling of one number.
    const phone = toStoredNumber(typedPhone) ?? typedPhone;
    const { body, issues: bodyIssues } = buildBody(form, initial, mode, client);

    // A client with no name is a row nobody can recognise in a list of
    // rows. Checked in BOTH modes — the box is editable in both.
    const nameIssues: FieldIssue[] = [];
    if (!form.name.trim()) nameIssues.push({ field: "name", message: "Enter the client's name." });

    // The three requirement must-haves — skipped entirely while those
    // fields are locked, because their boxes are disabled and there would
    // be no way to satisfy them. See the note at the top of this file.
    const requirementIssues: FieldIssue[] = [];
    if (!requirementsLocked) {
      if (!form.purpose.trim()) {
        requirementIssues.push({ field: "purpose", message: "Choose whether they want to Buy or Rent." });
      }
      if (splitTypes(form.property_type).length === 0) {
        requirementIssues.push({ field: "property_type", message: "Pick at least one property type." });
      }
      // Either box satisfies it, so the same message is raised against BOTH
      // — both go red, and the bar de-duplicates by message, so it is said
      // once and shown wherever the answer can go.
      if (!form.budget_min_inr.trim() && !form.budget_max_inr.trim()) {
        const message = "Add a budget — a minimum or a maximum, at least one of the two.";
        requirementIssues.push({ field: "budget_min_inr", message }, { field: "budget_max_inr", message });
      }
    }

    // The phone boxes first: they are the top of the form, so this is the
    // order the reader meets the problems in, which is the order
    // focusFirstIssue should travel in.
    const found = [...phoneIssues, ...nameIssues, ...bodyIssues, ...requirementIssues];
    setIssues(found);
    if (found.length > 0) {
      setFormError(null);
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
        message: saved.name || formatPhone(saved.phone),
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

  const title =
    mode === "add" ? "Add a client" : client?.name || (client ? formatPhone(client.phone) : "") || "Edit client";
  const purposeOptions = withCurrent(PURPOSES, form.purpose);
  const furnishingOptions = withCurrent(
    FURNISHING_OPTIONS.map((option) => ({ value: option, label: option })),
    form.furnishing,
  );
  // The chips themselves add any stored type that isn't one of the offered
  // options (PropertyTypeChips' withStored), which is the equivalent of
  // withCurrent above: opening Edit never drops a type.
  const pickedTypes = splitTypes(form.property_type);
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
                ? "Filled in here, on the dashboard — nothing is sent to the client. Fields marked * are required."
                : "Pre-filled with everything on file. Only what you change is saved. Fields marked * are required."}
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={saving} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body" ref={bodyRef}>
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
              field="phone"
              invalid={hasIssue(issues, "phone")}
              required={mode === "add"}
              hint={
                mode === "add"
                  ? "10 digits — this can never be changed later."
                  : "A client's number is who they are here, so it can't be changed."
              }
              keyHint={mode === "add"}
            >
              <PhoneInput
                id="client-form-phone"
                value={form.phone}
                onChange={(value) => set("phone", value)}
                autoFocus={mode === "add"}
                disabled={mode === "edit" || saving}
                ariaLabel="WhatsApp number"
              />
            </Field>

            <Field
              label="Other numbers"
              hint="Not verified — a landline, a spouse's number, anything worth having on file. 10 digits per box."
            >
              <div className="stack stack-2">
                {form.additional_phones.map((phone, index) => (
                  <div
                    className="row-flex"
                    style={{ gap: 8, flexWrap: "nowrap" }}
                    data-field={`other-phone-${index}`}
                    key={index}
                  >
                    <PhoneInput
                      value={phone}
                      onChange={(value) => setPhoneAt(index, value)}
                      invalid={hasIssue(issues, `other-phone-${index}`)}
                      autoFocus
                      disabled={saving}
                      ariaLabel={`Other number ${index + 1}`}
                    />
                    <Button
                      variant="ghost"
                      size="sm"
                      icon={<IconTrash size={14} />}
                      onClick={() => removePhoneAt(index)}
                      disabled={saving}
                      aria-label={`Remove other number ${index + 1}`}
                    />
                  </div>
                ))}
                <Button variant="ghost" size="sm" icon={<IconPlus size={14} />} onClick={addPhone} disabled={saving}>
                  Add a number
                </Button>
              </div>
            </Field>

            <Field
              label="Name"
              htmlFor="client-form-name"
              field="name"
              invalid={hasIssue(issues, "name")}
              required
            >
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

            <Field label="Email" htmlFor="client-form-email" hint="Optional." field="email" invalid={hasIssue(issues, "email")}>
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

            <Field label="Notes" htmlFor="client-form-notes-field" hint="Optional — anything else worth keeping on file about this client.">
              <textarea
                id="client-form-notes-field"
                className="textarea"
                rows={3}
                value={form.notes}
                onChange={(event) => set("notes", event.target.value)}
                placeholder="e.g. Prefers evening calls, referred by Mehta Realty…"
                disabled={saving}
                maxLength={2000}
              />
            </Field>

            <div className="detail-modal__eyebrow" style={{ margin: "6px 0 -4px" }}>
              Requirements
            </div>

            <Field
              label="Wants to"
              htmlFor="client-form-purpose"
              field="purpose"
              invalid={hasIssue(issues, "purpose")}
              required={!requirementsLocked}
            >
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
            <Field
              label="Property type"
              field="property_type"
              invalid={hasIssue(issues, "property_type")}
              required={!requirementsLocked}
              hint="Pick every type they'd consider — as many as you like."
            >
              <PropertyTypeChips
                picked={pickedTypes}
                onToggle={toggleType}
                disabled={requirementsLocked || saving}
              />
            </Field>

            {/* One optional box per picked type, with BOTH units offered
                beside it rather than one guessed from the type. A number
                on its own is ambiguous — 200 sqft and 200 var are not the
                same property — so a box with anything typed in it cannot be
                saved until the unit is picked (typeSizeIssues). An empty
                box stays perfectly fine: a size is optional, and it only
                ever nudges the matching, never rules a property out. */}
            {pickedTypes.length > 0 && (
              <Field label="Preferred size (optional)" hint={SIZE_HINT} keyHint>
                <TypeSizeRows
                  picked={pickedTypes}
                  state={form.property_sizes}
                  onChange={(next) => setForm((prev) => ({ ...prev, property_sizes: next }))}
                  issues={issues}
                  disabled={requirementsLocked || saving}
                  idPrefix="client-form"
                />
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

            <Field
              label="Budget (₹)"
              required={!requirementsLocked}
              hint="At least one of the two. Write it the way they'd say it — 2.5 cr, 85 L, or 25 K a month to rent."
            >
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
                  data-field="budget_min_inr"
                  className={`input${hasIssue(issues, "budget_min_inr") ? " input--bad" : ""}`}
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
                  data-field="budget_max_inr"
                  className={`input${hasIssue(issues, "budget_max_inr") ? " input--bad" : ""}`}
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

            <Field
              label="Preferred areas"
              htmlFor="client-form-areas"
              hint="Tap an area to add it. Not on the list? Type it and press Add — we'll still match against it."
            >
              <AreaPicker
                id="client-form-areas"
                selected={form.preferred_areas}
                onChange={(areas) => set("preferred_areas", areas)}
                options={areaOptions}
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

          </div>
        </div>

        <FormIssues issues={issues} error={formError} />

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
