import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { requirementApi, type RequirementContentFields } from "../api/requirementApi";
import { settingsApi } from "../api/settingsApi";
import type { BrokerRequirementRecord } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { MAX_INR } from "../lib/fieldChecks";
import { formatCompactInr, parseCompactInr } from "../lib/formatters";
import { phoneList, toStoredNumber, toTypedNumber } from "../lib/phone";
import ContactPhonesField, { phoneBoxesError, toPhoneBoxes } from "./ContactPhonesField";
import { SIZE_HINT, splitTypes } from "../lib/propertyTypeOptions";
import { mergeAreas, SURAT_AREAS } from "../lib/suratAreas";
import { FURNISHING_OPTIONS } from "./PropertyFormDialog";
import { useToast } from "./ui/Toast";
import AreaPicker from "./ui/AreaPicker";
import { Button, Segmented } from "./ui/Primitives";
import { FormIssues, hasIssue, useFocusFirstIssue, type FieldIssue } from "./ui/FormIssues";
import {
  PropertyTypeChips,
  TypeSizeRows,
  sizesToSend,
  toTypeSizeState,
  typeSizeIssues,
  type TypeSizeState,
} from "./ui/PropertyTypePicker";
import { IconAlert, IconX } from "./ui/Icons";

/**
 * The Broker Requirements page's Add and Edit dialog — one form for both, the
 * same way PropertyFormDialog serves both on the Properties page, so the two
 * can never drift apart. `mode` decides only the wording and where Save goes
 * (POST vs PATCH); the fields themselves are identical, because a requirement
 * typed by hand holds exactly what a captured one holds.
 *
 * Almost every field is optional, but a requirement has to ask for
 * SOMETHING — at least a property type, an area or a budget — or there is
 * nothing to compare stored properties against (see validationIssues below,
 * and the matching identical rule in Backend/Controller/
 * BrokerRequirementController/broker_requirement_controller.py). Cancel and
 * the header's X both discard the in-progress edit without calling the
 * backend.
 *
 * The WhatsApp metadata (sender, group, original message, timestamp) is not
 * in this form at all. It is the audit trail for where the requirement came
 * from, and the backend refuses to rewrite it — showing it as an editable
 * field would only promise something that cannot happen. On an Add there is
 * no such metadata to begin with: the backend stamps the placeholders a
 * manual entry gets (see requirement_pipeline_service.create_requirement).
 */

interface FormState {
  /** Every type the broker will take, comma-separated ("Flat, Bungalow") —
   *  main one first, which is the shape the backend stores and the type
   *  gate reads (Backend/Agent/BrokerRequirementAgent/
   *  requirement_normalization.py's canonical_requirement_type). */
  requirement_type: string;
  /** The number/range and the unit asked for against each picked type,
   *  held apart while they are edited and joined into the one string the
   *  backend stores at save time. */
  property_sizes: TypeSizeState;
  bhk: string;
  /** Picked from the area picker (or typed in as a new one) — see
   *  components/ui/AreaPicker.tsx. The first one is this requirement's main
   *  Area. */
  preferred_areas: string[];
  society_name: string;
  /** One of FURNISHING_OPTIONS, or "" for "not stated" — the same three
   *  values a property's furnishing uses, which is what lets matching
   *  compare the two sides at all. */
  furnishing: string;
  budget_min_inr: string;
  budget_max_inr: string;
  /** null = nothing chosen yet, which is only ever an Add's starting state.
   *  A stored requirement always carries one of the two, so Edit never
   *  opens unset. */
  listing_type: "Sale" | "Rent" | null;
  contact_name: string;
  // One box per number, holding what is TYPED (the bare ten digits) —
  // see ContactPhonesField.
  contact_phone_boxes: string[];
  description: string;
  /** Staff-only catch-all — never matched against properties. */
  notes: string;
}

/** The short form ("45L") only when it reads back as EXACTLY the stored
 *  amount — otherwise the full number, so showing a budget can never be what
 *  changes it. Character-for-character the Inquiries page's client dialog,
 *  because the two budget boxes are now the same box. */
function budgetText(amount: number | null): string {
  if (amount === null) return "";
  const compact = formatCompactInr(amount);
  return parseCompactInr(compact) === amount ? compact : String(amount);
}

/** An Add starts from a blank form — same shape, every field empty, and
 *  Buy/Rent UNSET rather than preselected: the form now insists on an
 *  answer, and a pre-ticked "Buy" is the form answering on the reader's
 *  behalf. (The backend still defaults a requirement that says nothing to
 *  Sale; nothing saved from here says nothing any more.) */
function toFormState(requirement?: BrokerRequirementRecord): FormState {
  // Read through splitTypes so the chips and the stored string agree from
  // the start, exactly as the Inquiries page's client dialog does.
  const types = splitTypes(requirement?.requirement_type ?? "");
  return {
    requirement_type: types.join(", "),
    property_sizes: toTypeSizeState(types, requirement?.property_sizes),
    bhk: requirement?.bhk ?? "",
    preferred_areas: requirement?.preferred_areas ?? [],
    society_name: requirement?.society_name ?? "",
    furnishing: requirement?.furnishing ?? "",
    // Shown the way the broker would say it ("85L", "1.2cr") — see
    // budgetText, and see the Budget field below for why this box stopped
    // being a bare digits-only number box.
    budget_min_inr: budgetText(requirement?.budget_min_inr ?? null),
    budget_max_inr: budgetText(requirement?.budget_max_inr ?? null),
    listing_type: requirement?.listing_type ?? null,
    contact_name: requirement?.contact_name ?? "",
    contact_phone_boxes: toPhoneBoxes(phoneList(requirement).map(toTypedNumber)),
    description: requirement?.description ?? "",
    notes: requirement?.notes ?? "",
  };
}

/** Blank strings become null, not "" — an empty field must actually clear
 *  the value server-side, not overwrite it with an empty string. */
function toPayload(form: FormState): RequirementContentFields {
  const text = (value: string) => (value.trim() ? value.trim() : null);
  // "85 L" -> 8500000. The boxes accept what a broker would say, so what is
  // SENT has to be the rupee figure they meant — validationIssues has
  // already refused anything parseCompactInr cannot read, so a null here is
  // an empty box and nothing else.
  const num = (value: string) => (value.trim() ? parseCompactInr(value) : null);
  // Already a clean, de-duplicated list — AreaPicker never lets a blank or
  // repeated area in.
  const areas = form.preferred_areas;
  return {
    requirement_type: text(form.requirement_type),
    // One per picked type, each carrying the unit picked beside it
    // ("1000-1500 sqft", "150 var"). null when none are filled in, which is
    // how the backend is told there is nothing to keep.
    property_sizes: sizesToSend(splitTypes(form.requirement_type), form.property_sizes),
    bhk: text(form.bhk),
    preferred_areas: areas,
    // area_name is the primary locality and is never edited on its own —
    // it is simply the first of preferred_areas, exactly as the structuring
    // stage sets it, so the two can never disagree after an edit.
    area_name: areas[0] ?? null,
    society_name: text(form.society_name),
    furnishing: text(form.furnishing),
    // budget_text (the broker's own wording) is not on this form any more —
    // deliberately omitted rather than sent as null, so saving an edit never
    // wipes the wording a WhatsApp-captured requirement already has. The two
    // ₹ boxes below are what filtering and matching actually read.
    budget_min_inr: num(form.budget_min_inr),
    budget_max_inr: num(form.budget_max_inr),
    // Never null on the wire — the API type has no third value, and
    // validationIssues has already refused an unset one.
    listing_type: form.listing_type ?? "Sale",
    contact_name: text(form.contact_name),
    contact_phones: form.contact_phone_boxes
      .map((box) => box.trim())
      .filter(Boolean)
      .map((box) => toStoredNumber(box) ?? box),
    description: text(form.description),
    notes: text(form.notes),
  };
}

/**
 * Everything this form refuses to save, in the order the fields are read —
 * the browser half of Backend/Controller/BrokerRequirementController/
 * broker_requirement_controller.py's own rules, so a mistake is answered
 * here instead of coming back as a server error.
 *
 * Why each one:
 *  - a budget is a rupee figure, so "-500" and "1e20" are not budgets (the
 *    latter rendered on the card as "up to 10000000000000cr");
 *  - a reversed pair is REFUSED, in the same words the Inquiries page's
 *    client form has always used. The structuring stage still silently
 *    swaps a reversed pair from the LLM, which is right for a model with no
 *    one to ask — but a person typing here can simply be told;
 *  - FOUR THINGS ARE NOT OPTIONAL: at least one property type, Buy or Rent,
 *    at least one of the two ₹ budget boxes, and at least one contact
 *    number. A requirement is a thing to match properties against and a
 *    broker to ring when one matches; without those it is neither. This
 *    replaces the older, looser "type OR area OR budget" rule, which let a
 *    card be saved reading "— / —" with a hundred meaningless "Low" matches
 *    behind it — the new rules are strictly stronger, so nothing that would
 *    have been refused before is accepted now.
 *
 * Returns EVERY problem it finds rather than only the first, so a form
 * with two mistakes in it takes one Save to discover both. Each entry names
 * the box it belongs to, which marks that box red and is where the body
 * scrolls. [] when the form is good to send.
 */
function validationIssues(form: FormState): FieldIssue[] {
  const issues: FieldIssue[] = [];
  const add = (field: string, message: string | null) => {
    if (message) issues.push({ field, message });
  };
  // Collected in the order the boxes appear in the form, so the bar reads
  // top-to-bottom and focusFirstIssue lands on the first thing wrong.
  if (splitTypes(form.requirement_type).length === 0) {
    add("requirement_type", "Pick at least one property type.");
  }
  // A size box per picked type: a number or a range, and — once anything is
  // typed in one — which unit that is. The same two rules the client dialog
  // and the public form apply, from the same place.
  for (const issue of typeSizeIssues(splitTypes(form.requirement_type), form.property_sizes)) {
    add(issue.field, issue.message);
  }
  if (form.listing_type === null) {
    add("listing_type", "Choose whether the broker wants to Buy or to Rent.");
  }
  // "85 L", "1.2cr", "8500000" — all read by parseCompactInr, the same
  // function the Inquiries page's client dialog reads its two budget boxes
  // with. Anything it cannot read is refused here rather than being sent as
  // a NaN.
  const amounts: Record<"budget_min_inr" | "budget_max_inr", number | null> = {
    budget_min_inr: null,
    budget_max_inr: null,
  };
  // How many problems were already found before the budget boxes were
  // read — the reversed-pair check below needs to know whether either BOX
  // was itself refused, not whether the form has any other fault at all.
  // (It used to test `issues.length === 0`, which was the same thing while
  // the budget boxes were the first thing checked and is not any more.)
  const beforeBudget = issues.length;
  for (const [key, label] of [
    ["budget_min_inr", "minimum"],
    ["budget_max_inr", "maximum"],
  ] as const) {
    const raw = form[key].trim();
    if (!raw) continue;
    const amount = parseCompactInr(raw);
    if (amount === null) {
      add(key, `The ${label} budget isn't an amount — try 45L, 1.2cr or 4500000.`);
      continue;
    }
    if (amount > MAX_INR) {
      add(key, `That ${label} budget is too large — check the figure.`);
      continue;
    }
    amounts[key] = amount;
  }
  const min = amounts.budget_min_inr;
  const max = amounts.budget_max_inr;
  // Raised against the MAXIMUM box: with two boxes and one relationship
  // between them, the second is the one the reader was last in and the one
  // they will change. Only when neither box is separately wrong, so a
  // reversed pair does not also complain about being reversed.
  if (issues.length === beforeBudget && min !== null && max !== null && min > max) {
    add("budget_max_inr", "The minimum budget is above the maximum.");
  }
  // Either box satisfies it, so the same message is raised against BOTH —
  // both go red, and the bar de-duplicates by message, so it is said once
  // and shown wherever the answer can go.
  if (!form.budget_min_inr.trim() && !form.budget_max_inr.trim()) {
    const message = "Add a budget — a minimum or a maximum, at least one of the two.";
    add("budget_min_inr", message);
    add("budget_max_inr", message);
  }
  // Missing first, then malformed: an empty field cannot also be badly
  // typed, and phoneBoxesError has nothing to say about empty boxes.
  if (!form.contact_phone_boxes.some((box) => box.trim())) {
    add("contact_phone_boxes", "Add at least one contact number.");
  }
  add("contact_phone_boxes", phoneBoxesError(form.contact_phone_boxes));
  return issues;
}

const GRID_STYLE: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
  gap: 14,
};

/** Same two kinds of hint PropertyFormDialog's Field draws, for the same
 *  reason and with the same styling — see that component's comment.
 *  `keyHint` marks a box whose value is read by machinery (the rupee
 *  figures filtering and matching compare, the numbers that get dialled),
 *  where a value typed the wrong way is stored wrongly without failing. */
function Field({
  label,
  hint,
  keyHint,
  span,
  field,
  invalid,
  required,
  children,
}: {
  label: string;
  hint?: string;
  keyHint?: boolean;
  span?: boolean;
  /** The key this box raises its issues under -- written out as
   *  `data-field` so focusFirstIssue can find it. */
  field?: string;
  /** A save was just refused over this box. */
  invalid?: boolean;
  /** This box has to be filled in — marks the label with a red asterisk, so
   *  the rule is visible before Save rather than only after it. */
  required?: boolean;
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

export default function RequirementFormDialog({
  mode,
  requirement,
  onClose,
  onSaved,
}: {
  mode: "add" | "edit";
  /** The record being edited — absent on an Add. */
  requirement?: BrokerRequirementRecord;
  onClose: () => void;
  onSaved: (requirement: BrokerRequirementRecord, mode: "add" | "edit") => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<FormState>(() => toFormState(requirement));
  const [saving, setSaving] = useState(false);
  // Why the last Save didn't go through, shown in the dialog itself rather
  // than only as a toast — the toast fades while this modal is still open.
  // Same treatment the Agents dialog and the client dialog already give it.
  const [formError, setFormError] = useState<string | null>(null);
  // Everything the LAST Save attempt refused, shown together in the pinned
  // bar above the footer -- see components/ui/FormIssues.
  const [issues, setIssues] = useState<FieldIssue[]>([]);
  const bodyRef = useRef<HTMLDivElement>(null);
  useFocusFirstIssue(bodyRef, issues);

  // The area picker's offered list: the well-known Surat localities plus
  // whichever ones this app's own Settings page has configured that aren't
  // already on it (see lib/suratAreas.ts's mergeAreas) — the exact same
  // source the public requirements form's own picker reads. Failure is
  // silent on purpose: the picker still works perfectly off the hardcoded
  // list, and typing an area is always allowed regardless, so a dead
  // settings call is not worth a visible error on this dialog.
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

  /** Blur: show the short form ("85L") when that is exact — see budgetText.
   *  Leaves an unreadable box exactly as typed, so the reader still sees
   *  what they wrote beside the message explaining it. */
  function onBudgetBlur(key: "budget_min_inr" | "budget_max_inr") {
    const amount = parseCompactInr(form[key]);
    if (amount !== null) set(key, budgetText(amount));
  }

  /** Ticks or unticks a type, keeping the order they were picked in — the
   *  first is this requirement's main one. A size typed against a type
   *  survives it being unticked (ticking it again brings it back) but only
   *  picked types are ever sent. */
  function toggleType(type: string) {
    setForm((prev) => {
      const picked = splitTypes(prev.requirement_type);
      const next = picked.some((existing) => existing.toLowerCase() === type.toLowerCase())
        ? picked.filter((existing) => existing.toLowerCase() !== type.toLowerCase())
        : [...picked, type];
      return { ...prev, requirement_type: next.join(", ") };
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
      const payload = toPayload(form);
      const saved =
        mode === "add"
          ? await requirementApi.createRequirement(payload)
          : await requirementApi.updateRequirement(requirement!.record_id, payload);
      toast.push({
        tone: "ok",
        title: mode === "add" ? "Requirement added" : "Requirement updated",
        message: saved.bhk ?? saved.requirement_type ?? saved.area_name ?? "Saved.",
      });
      onSaved(saved, mode);
    } catch (err) {
      const message = friendlyError(err);
      setFormError(message);
      toast.push({ tone: "bad", title: "Couldn't save this requirement", message });
    } finally {
      setSaving(false);
    }
  }

  const title =
    mode === "add"
      ? "Add a requirement"
      : [requirement?.bhk, requirement?.requirement_type].filter(Boolean).join(" ") || "Requirement";

  // The chips add any stored type that isn't one of the offered options
  // (PropertyTypeChips' withStored), so opening Edit on a requirement
  // structured with an older name never drops it.
  const pickedTypes = splitTypes(form.requirement_type);

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}>
      <div
        className="detail-modal anim-rise"
        role="dialog"
        aria-modal="true"
        aria-label={mode === "add" ? "Add requirement" : "Edit requirement"}
      >
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">{mode === "add" ? "New requirement" : "Edit requirement"}</div>
            <h2 className="detail-modal__title cell-truncate">{title}</h2>
            <div className="detail-modal__sub">
              Fill in what the broker actually asked for. Fields marked * are required — a property type, Buy or
              Rent, a budget and one contact number, so there is something to match properties against and someone
              to ring when they do.
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={saving} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body" ref={bodyRef}>
          <div className="stack stack-4">
            {/* Chips, not free text: a broker who will take a flat OR a
                bungalow says both, each is scored on its own, and nothing
                depends on remembering to separate them with commas. The
                first one picked stays this requirement's main type. */}
            <Field
              label="Property type wanted"
              field="requirement_type"
              invalid={hasIssue(issues, "requirement_type")}
              required
              hint="Pick every type they'd take — the first one is the main one."
            >
              <PropertyTypeChips
                picked={pickedTypes}
                onToggle={toggleType}
                disabled={saving}
                ariaLabel="Property type wanted"
              />
            </Field>

            {/* One optional box per picked type, with both units beside it.
                A bare number is ambiguous — 200 sqft and 200 var are not the
                same property — so a box with anything typed in it has to say
                which. Empty is fine: it only nudges the matching. */}
            {pickedTypes.length > 0 && (
              <Field label="Size wanted (optional)" hint={SIZE_HINT} keyHint>
                <TypeSizeRows
                  picked={pickedTypes}
                  state={form.property_sizes}
                  onChange={(next) => set("property_sizes", next)}
                  issues={issues}
                  disabled={saving}
                  idPrefix="requirement-form"
                />
              </Field>
            )}

            <div style={GRID_STYLE}>
              <Field
                label="BHK"
                hint="Write it however they think of it — “3 BHK”, “2 to 5 BHK”, “2 or 3 BHK”, “3+ BHK”, “exactly 3 BHK”, “1 RK”."
              >
                <input
                  className="input"
                  value={form.bhk}
                  onChange={(e) => set("bhk", e.target.value)}
                  placeholder="e.g. 3 BHK, 2 to 5 BHK, 3+ BHK"
                />
              </Field>

              <Field
                label="Preferred areas"
                hint="Tap an area to add it. Not on the list? Type it and press Add — the first one picked is used as this requirement's main Area."
                span
              >
                <AreaPicker
                  id="requirement-form-areas"
                  selected={form.preferred_areas}
                  onChange={(areas) => set("preferred_areas", areas)}
                  options={areaOptions}
                  disabled={saving}
                />
              </Field>

              <Field label="Society / building asked for">
                <input
                  className="input"
                  value={form.society_name}
                  onChange={(e) => set("society_name", e.target.value)}
                  placeholder="e.g. Black Residency"
                />
              </Field>
              {/* A dropdown, not free text: this value is MATCHED against a
                  property's own furnishing, which only ever holds these
                  three words. Anything else typed here would simply never
                  compare. Weighed low on purpose — see Backend/Service/
                  ClientPropertyMatchingService/scoring.py. The broker's own
                  wording still belongs in Description below. */}
              <Field label="Furnishing" hint="Optional — nudges matching gently, never rules a property out.">
                <select
                  className="select"
                  value={form.furnishing}
                  onChange={(e) => set("furnishing", e.target.value)}
                >
                  <option value="">—</option>
                  {/* A stored value that is none of the three (an older
                      free-text one) is offered as well, so opening Edit can
                      never silently blank it. */}
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
              {/* Nothing selected until somebody chooses — see toFormState. */}
              <Field
                label="Buy or Rent"
                field="listing_type"
                invalid={hasIssue(issues, "listing_type")}
                required
              >
                <Segmented
                  ariaLabel="Buy or Rent"
                  value={form.listing_type}
                  onChange={(value) => set("listing_type", value)}
                  options={[
                    { value: "Sale", label: "Buy" },
                    { value: "Rent", label: "Rent" },
                  ]}
                />
              </Field>

              {/* One Budget field with two boxes, identical to the
                  Inquiries page's client dialog — same legend, same
                  placeholders, same parser (parseCompactInr) and the same
                  tidy-up on blur. It used to be two separate "digits only"
                  number boxes, which asked the reader to expand "80L" into
                  8000000 in their head before they could type it; now the
                  boxes take the wording and do the arithmetic. What is
                  STORED is unchanged — a plain rupee figure in each column.
                  Spans the row because the pair belongs together. */}
              <Field
                label="Budget (₹)"
                required
                hint="At least one of the two. Write it the way they'd say it — 2.5 cr, 85 L, or 25 K a month to rent."
                span
              >
                {/* The key, before the boxes — it answers "how do I write
                    this?" before anyone has to wonder. */}
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
                    onChange={(e) => set("budget_min_inr", e.target.value)}
                    onBlur={() => onBudgetBlur("budget_min_inr")}
                    placeholder="Min — e.g. 80 L"
                    maxLength={20}
                  />
                  <input
                    data-field="budget_max_inr"
                    className={`input${hasIssue(issues, "budget_max_inr") ? " input--bad" : ""}`}
                    inputMode="decimal"
                    aria-label="Maximum budget"
                    value={form.budget_max_inr}
                    onChange={(e) => set("budget_max_inr", e.target.value)}
                    onBlur={() => onBudgetBlur("budget_max_inr")}
                    placeholder="Max — e.g. 1.2 cr"
                    maxLength={20}
                  />
                </div>
              </Field>

              <Field label="Contact name">
                <input
                  className="input"
                  value={form.contact_name}
                  onChange={(e) => set("contact_name", e.target.value)}
                  placeholder="e.g. Ramesh Broker"
                />
              </Field>
              <Field
                field="contact_phone_boxes"
                invalid={hasIssue(issues, "contact_phone_boxes")}
                label="Contact numbers"
                required
                hint="At least one. 10 digits per box — the +91 is added for you."
                keyHint
              >
                <ContactPhonesField
                  boxes={form.contact_phone_boxes}
                  onChange={(boxes) => set("contact_phone_boxes", boxes)}
                />
              </Field>
            </div>

            <Field
              label="Additional requirements"
              hint="Size, location detail, who it is for, food, possession, urgency, token ready, vaya — anything else the broker asked for. The broker's own furnishing wording belongs here too; the field above holds only the level. Matched against properties, like every field above."
            >
              <textarea
                className="textarea"
                rows={4}
                value={form.description}
                onChange={(e) => set("description", e.target.value)}
                placeholder="e.g. Fully furnished, veg family, possession 1-15 Sep, 1 vaya"
              />
            </Field>

            <Field label="Notes" hint="Optional — anything else worth keeping on file about this requirement. Never matched against properties.">
              <textarea
                className="textarea"
                rows={3}
                value={form.notes}
                onChange={(e) => set("notes", e.target.value)}
                placeholder="e.g. Prefers evening calls, referred by Mehta Realty…"
                maxLength={2000}
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
            <Button variant="primary" onClick={handleSave} busy={saving}>
              {mode === "add" ? "Add requirement" : "Save changes"}
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
