import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { requirementApi, type RequirementContentFields } from "../api/requirementApi";
import type { BrokerRequirementRecord } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { useToast } from "./ui/Toast";
import { Button, Segmented } from "./ui/Primitives";
import { IconX } from "./ui/Icons";

/**
 * The Broker Requirements page's Edit dialog. Edit-only, deliberately: a
 * requirement exists because a broker asked for something in a monitored
 * chat, so there is no "Add a requirement by hand" action in the product
 * (and no POST endpoint behind one either) the way there is for a property.
 *
 * Every field is optional — there is nothing here that blocks Save. Cancel
 * and the header's X both discard the in-progress edit without calling the
 * backend.
 *
 * The WhatsApp metadata (sender, group, original message, timestamp) is not
 * in this form at all. It is the audit trail for where the requirement came
 * from, and the backend refuses to rewrite it — showing it as an editable
 * field would only promise something that cannot happen.
 */

interface FormState {
  requirement_type: string;
  bhk: string;
  preferred_areas: string;
  society_name: string;
  address: string;
  carpet_area_min: string;
  carpet_area_max: string;
  carpet_area_unit: string;
  budget_text: string;
  budget_min_inr: string;
  budget_max_inr: string;
  listing_type: "Sale" | "Rent";
  furnishing: string;
  contact_name: string;
  contact_phone: string;
  description: string;
}

function toFormState(requirement: BrokerRequirementRecord): FormState {
  return {
    requirement_type: requirement.requirement_type ?? "",
    bhk: requirement.bhk ?? "",
    // Edited as one comma-separated line rather than a list widget: these
    // are short free-text localities copied from the message, and typing
    // "Vesu, Althan" is faster than managing chips for two of them.
    preferred_areas: requirement.preferred_areas.join(", "),
    society_name: requirement.society_name ?? "",
    address: requirement.address ?? "",
    carpet_area_min: requirement.carpet_area_min?.toString() ?? "",
    carpet_area_max: requirement.carpet_area_max?.toString() ?? "",
    carpet_area_unit: requirement.carpet_area_unit ?? "",
    budget_text: requirement.budget_text ?? "",
    budget_min_inr: requirement.budget_min_inr?.toString() ?? "",
    budget_max_inr: requirement.budget_max_inr?.toString() ?? "",
    listing_type: requirement.listing_type,
    furnishing: requirement.furnishing ?? "",
    contact_name: requirement.contact_name ?? "",
    contact_phone: requirement.contact_phone ?? "",
    description: requirement.description ?? "",
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
    address: text(form.address),
    carpet_area_min: num(form.carpet_area_min),
    carpet_area_max: num(form.carpet_area_max),
    carpet_area_unit: text(form.carpet_area_unit),
    budget_text: text(form.budget_text),
    budget_min_inr: num(form.budget_min_inr),
    budget_max_inr: num(form.budget_max_inr),
    listing_type: form.listing_type,
    furnishing: text(form.furnishing),
    contact_name: text(form.contact_name),
    contact_phone: text(form.contact_phone),
    description: text(form.description),
  };
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
  requirement,
  onClose,
  onSaved,
}: {
  requirement: BrokerRequirementRecord;
  onClose: () => void;
  onSaved: (requirement: BrokerRequirementRecord) => void;
}) {
  const toast = useToast();
  const [form, setForm] = useState<FormState>(() => toFormState(requirement));
  const [saving, setSaving] = useState(false);

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
    setSaving(true);
    try {
      const saved = await requirementApi.updateRequirement(requirement.record_id, toPayload(form));
      toast.push({
        tone: "ok",
        title: "Requirement updated",
        message: saved.bhk ?? saved.requirement_type ?? saved.area_name ?? "Saved.",
      });
      onSaved(saved);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't save this requirement", message: friendlyError(err) });
    } finally {
      setSaving(false);
    }
  }

  const title = [requirement.bhk, requirement.requirement_type].filter(Boolean).join(" ") || "Requirement";

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="Edit requirement">
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">Edit requirement</div>
            <h2 className="detail-modal__title cell-truncate">{title}</h2>
            <div className="detail-modal__sub">
              Every field here is optional — fill in only what the broker actually asked for.
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={saving} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          <div className="stack stack-4">
            <div style={GRID_STYLE}>
              <Field label="Property type wanted">
                <input
                  className="input"
                  value={form.requirement_type}
                  onChange={(e) => set("requirement_type", e.target.value)}
                  placeholder="e.g. Flat, Shop, Land/Plot"
                />
              </Field>
              <Field label="BHK">
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

              <Field label="Location detail" span>
                <input
                  className="input"
                  value={form.address}
                  onChange={(e) => set("address", e.target.value)}
                  placeholder="Road, landmark, 'near …'"
                />
              </Field>

              <Field label="Size from">
                <input
                  className="input"
                  type="number"
                  inputMode="decimal"
                  value={form.carpet_area_min}
                  onChange={(e) => set("carpet_area_min", e.target.value)}
                  placeholder="e.g. 1000"
                />
              </Field>
              <Field label="Size to">
                <input
                  className="input"
                  type="number"
                  inputMode="decimal"
                  value={form.carpet_area_max}
                  onChange={(e) => set("carpet_area_max", e.target.value)}
                  placeholder="e.g. 1200"
                />
              </Field>
              <Field label="Size unit">
                <select
                  className="select"
                  value={form.carpet_area_unit}
                  onChange={(e) => set("carpet_area_unit", e.target.value)}
                >
                  <option value="">—</option>
                  <option value="sqft">sqft</option>
                  <option value="vaar">vaar</option>
                  <option value="vigha">vigha</option>
                </select>
              </Field>

              <Field label="Budget (as written)" hint="e.g. 45L, 80L-1cr, 15k/month">
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
                  value={form.budget_max_inr}
                  onChange={(e) => set("budget_max_inr", e.target.value)}
                  placeholder="e.g. 10000000"
                />
              </Field>

              <Field label="Furnishing">
                <select className="select" value={form.furnishing} onChange={(e) => set("furnishing", e.target.value)}>
                  <option value="">—</option>
                  <option value="Furnished">Furnished</option>
                  <option value="Semi-furnished">Semi-furnished</option>
                  <option value="Unfurnished">Unfurnished</option>
                </select>
              </Field>

              <Field label="Contact name">
                <input
                  className="input"
                  value={form.contact_name}
                  onChange={(e) => set("contact_name", e.target.value)}
                  placeholder="e.g. Ramesh Broker"
                />
              </Field>
              <Field label="Contact phone">
                <input
                  className="input"
                  value={form.contact_phone}
                  onChange={(e) => set("contact_phone", e.target.value)}
                  placeholder="Digits, with country code"
                />
              </Field>
            </div>

            <Field label="Description">
              <textarea
                className="textarea"
                rows={3}
                value={form.description}
                onChange={(e) => set("description", e.target.value)}
                placeholder="Any other details worth noting"
              />
            </Field>
          </div>
        </div>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <span style={{ marginLeft: "auto" }}>
            <Button variant="primary" onClick={handleSave} busy={saving}>
              Save changes
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
