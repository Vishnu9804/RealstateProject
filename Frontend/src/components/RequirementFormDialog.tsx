import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { requirementApi, type RequirementContentFields } from "../api/requirementApi";
import type { BrokerRequirementRecord } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { MAX_INR, amountError } from "../lib/fieldChecks";
import { phoneList, toStoredNumber, toTypedNumber } from "../lib/phone";
import ContactPhonesField, { phoneBoxesError, toPhoneBoxes } from "./ContactPhonesField";
import { REQUIREMENT_TYPE_OPTIONS } from "../lib/requirementFilters";
import { FURNISHING_OPTIONS } from "./PropertyFormDialog";
import { useToast } from "./ui/Toast";
import { Button, Note, Segmented } from "./ui/Primitives";
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
 * nothing to compare stored properties against (see validationError below,
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
  requirement_type: string;
  bhk: string;
  preferred_areas: string;
  society_name: string;
  /** One of FURNISHING_OPTIONS, or "" for "not stated" — the same three
   *  values a property's furnishing uses, which is what lets matching
   *  compare the two sides at all. */
  furnishing: string;
  budget_text: string;
  budget_min_inr: string;
  budget_max_inr: string;
  listing_type: "Sale" | "Rent";
  contact_name: string;
  // One box per number, holding what is TYPED (the bare ten digits) —
  // see ContactPhonesField.
  contact_phone_boxes: string[];
  description: string;
}

/** An Add starts from a blank form — same shape, every field empty, and
 *  "Buy" preselected because that is the backend's own default for a
 *  requirement that states nothing either way. */
function toFormState(requirement?: BrokerRequirementRecord): FormState {
  return {
    requirement_type: requirement?.requirement_type ?? "",
    bhk: requirement?.bhk ?? "",
    // Edited as one comma-separated line rather than a list widget: these
    // are short free-text localities copied from the message, and typing
    // "Vesu, Althan" is faster than managing chips for two of them.
    preferred_areas: requirement?.preferred_areas.join(", ") ?? "",
    society_name: requirement?.society_name ?? "",
    furnishing: requirement?.furnishing ?? "",
    budget_text: requirement?.budget_text ?? "",
    budget_min_inr: requirement?.budget_min_inr?.toString() ?? "",
    budget_max_inr: requirement?.budget_max_inr?.toString() ?? "",
    listing_type: requirement?.listing_type ?? "Sale",
    contact_name: requirement?.contact_name ?? "",
    contact_phone_boxes: toPhoneBoxes(phoneList(requirement).map(toTypedNumber)),
    description: requirement?.description ?? "",
  };
}

/** Blank strings become null, not "" — an empty field must actually clear
 *  the value server-side, not overwrite it with an empty string. */
function toPayload(form: FormState): RequirementContentFields {
  const text = (value: string) => (value.trim() ? value.trim() : null);
  const num = (value: string) => (value.trim() ? Number(value) : null);
  const areas = form.preferred_areas
    .split(",")
    .map((area) => area.trim())
    .filter(Boolean);
  return {
    requirement_type: text(form.requirement_type),
    bhk: text(form.bhk),
    preferred_areas: areas,
    // area_name is the primary locality and is never edited on its own —
    // it is simply the first of preferred_areas, exactly as the structuring
    // stage sets it, so the two can never disagree after an edit.
    area_name: areas[0] ?? null,
    society_name: text(form.society_name),
    furnishing: text(form.furnishing),
    budget_text: text(form.budget_text),
    budget_min_inr: num(form.budget_min_inr),
    budget_max_inr: num(form.budget_max_inr),
    listing_type: form.listing_type,
    contact_name: text(form.contact_name),
    contact_phones: form.contact_phone_boxes
      .map((box) => box.trim())
      .filter(Boolean)
      .map((box) => toStoredNumber(box) ?? box),
    description: text(form.description),
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
 *  - a requirement with no type, no area and no budget has nothing to
 *    compare properties against. Saving one used to produce a card reading
 *    "— / —" with a hundred meaningless "Low" matches behind it.
 *
 * Returns null when the form is good to send.
 */
function validationError(form: FormState): string | null {
  for (const [key, label] of [
    ["budget_min_inr", "Budget from"],
    ["budget_max_inr", "Budget to"],
  ] as const) {
    const error = amountError(form[key], label, MAX_INR);
    if (error) return error;
  }
  const min = form.budget_min_inr.trim() ? Number(form.budget_min_inr) : null;
  const max = form.budget_max_inr.trim() ? Number(form.budget_max_inr) : null;
  if (min !== null && max !== null && min > max) return "The minimum budget is above the maximum.";

  const phoneError = phoneBoxesError(form.contact_phone_boxes);
  if (phoneError) return phoneError;

  const statesSomething =
    form.requirement_type.trim() ||
    form.preferred_areas.trim() ||
    form.budget_text.trim() ||
    form.budget_min_inr.trim() ||
    form.budget_max_inr.trim();
  if (!statesSomething) {
    return "Fill in at least one of Property type, Preferred areas or Budget — a requirement with none of them has nothing to match properties against.";
  }
  return null;
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
  const errorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (formError) errorRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [formError]);

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

  async function handleSave() {
    if (saving) return;
    const invalid = validationError(form);
    if (invalid) {
      setFormError(invalid);
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
              Fill in only what the broker actually asked for — at least the property type, an area or a budget,
              so there is something to match properties against.
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={saving} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          <div className="stack stack-4">
            <div style={GRID_STYLE}>
              <Field label="Property type wanted" hint="Several accepted? Separate them with commas, main one first.">
                <input
                  className="input"
                  list="requirement-type-options"
                  value={form.requirement_type}
                  onChange={(e) => set("requirement_type", e.target.value)}
                  placeholder="e.g. Flat, Bungalow, Plot"
                />
                <datalist id="requirement-type-options">
                  {REQUIREMENT_TYPE_OPTIONS.map((option) => (
                    <option key={option} value={option} />
                  ))}
                </datalist>
              </Field>
              <Field label="BHK" hint="e.g. 2 BHK, or 4 BHK, 5 BHK">
                <input className="input" value={form.bhk} onChange={(e) => set("bhk", e.target.value)} placeholder="e.g. 2 BHK" />
              </Field>

              <Field
                label="Preferred areas"
                hint="Comma-separated. The first one is used as this requirement's main Area."
                span
              >
                <input
                  className="input"
                  value={form.preferred_areas}
                  onChange={(e) => set("preferred_areas", e.target.value)}
                  placeholder="e.g. Vesu, Althan, Pal"
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
              <Field label="Buy or Rent">
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

              {/* The two numeric boxes below are what filtering and matching
                  read. This one is the broker's own wording, and its numbers
                  are only worked out when BOTH of those are left empty (see
                  requirement_structurer._fill_missing_budget_amounts) — said
                  here in the hint, because a form that quietly ignores what
                  someone typed reads as a bug. */}
              <Field
                label="Budget (as written)"
                hint="e.g. 45L, 80L-1cr, 15k/month. Read as the budget only when both boxes below are empty."
              >
                <input
                  className="input"
                  value={form.budget_text}
                  onChange={(e) => set("budget_text", e.target.value)}
                  placeholder="e.g. 80L-1cr"
                />
              </Field>
              <Field label="Budget from (₹)">
                <input
                  className="input"
                  type="number"
                  inputMode="decimal"
                  min={0}
                  value={form.budget_min_inr}
                  onChange={(e) => set("budget_min_inr", e.target.value)}
                  placeholder="e.g. 8000000"
                />
              </Field>
              <Field label="Budget to (₹)">
                <input
                  className="input"
                  type="number"
                  inputMode="decimal"
                  min={0}
                  value={form.budget_max_inr}
                  onChange={(e) => set("budget_max_inr", e.target.value)}
                  placeholder="e.g. 10000000"
                />
              </Field>

              <Field label="Contact name">
                <input
                  className="input"
                  value={form.contact_name}
                  onChange={(e) => set("contact_name", e.target.value)}
                  placeholder="e.g. Ramesh Broker"
                />
              </Field>
              <Field label="Contact numbers" hint="Just the 10 digits — the +91 is added for you.">
                <ContactPhonesField
                  boxes={form.contact_phone_boxes}
                  onChange={(boxes) => set("contact_phone_boxes", boxes)}
                />
              </Field>
            </div>

            <Field
              label="Description & other details"
              hint="Size, location detail, who it is for, food, possession, urgency, token ready, vaya — anything else the broker asked for. The broker's own furnishing wording belongs here too; the field above holds only the level."
            >
              <textarea
                className="textarea"
                rows={4}
                value={form.description}
                onChange={(e) => set("description", e.target.value)}
                placeholder="e.g. Fully furnished, veg family, possession 1-15 Sep, 1 vaya"
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
