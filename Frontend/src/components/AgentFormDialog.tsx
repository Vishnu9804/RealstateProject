import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { agentApi } from "../api/agentApi";
import type { AgentSummary } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { phoneFieldError, toStoredNumber, toTypedNumber } from "../lib/phone";
import { PhoneInput } from "./ContactPhonesField";
import { useToast } from "./ui/Toast";
import { Button } from "./ui/Primitives";
import { FormIssues, useFocusFirstIssue, type FieldIssue } from "./ui/FormIssues";
import { IconAlert, IconPlus, IconTag, IconX } from "./ui/Icons";

/**
 * The Agents page's Add/Edit agent dialog — name, WhatsApp number, and the
 * areas this agent covers (a tag list, same add/remove pattern as
 * SettingsPage.tsx's area-keywords editor). Every field here maps straight
 * to Backend/Controller/AgentManagementController/agent_controller.py's
 * AgentCreateRequest, used for both create (POST) and edit (PATCH).
 */
export default function AgentFormDialog({
  agent,
  onClose,
  onSaved,
}: {
  /** Omitted for Add; passed for Edit — its fields prefill the form. */
  agent?: AgentSummary;
  onClose: () => void;
  onSaved: (agent: AgentSummary) => void;
}) {
  const mode: "add" | "edit" = agent ? "edit" : "add";
  const toast = useToast();
  const [name, setName] = useState(agent?.name ?? "");
  // The ten digits, not the stored "+91…" — the same rule every other phone
  // box in the application follows now (see components/ContactPhonesField's
  // PhoneInput). toStoredNumber in handleSave puts the country code back.
  const [phone, setPhone] = useState(agent ? toTypedNumber(agent.phone) : "");
  const [areas, setAreas] = useState<string[]>(agent?.coverage_areas ?? []);
  const [newArea, setNewArea] = useState("");
  const [saving, setSaving] = useState(false);
  const areaInputRef = useRef<HTMLInputElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const phoneInputRef = useRef<HTMLInputElement>(null);
  // Why the last Save attempt didn't go through — the missing-field case
  // included. It used to be silent: Save was simply disabled while a
  // required field was blank, so pressing it did nothing, said nothing, and
  // read as a broken button. The "Add a client" dialog has always answered
  // this properly (ClientFormDialog's formError); this is the same answer.
  const [formError, setFormError] = useState<string | null>(null);
  // Which required boxes to outline. Cleared per field as it is filled in,
  // so the red goes away as the problem is fixed rather than on the next
  // Save.
  const [invalid, setInvalid] = useState<{ name: boolean; phone: boolean }>({ name: false, phone: false });
  // Both problems at once rather than whichever was found first: a dialog
  // with two boxes in it should not need two Saves to say so. Shown in the
  // pinned bar above the footer -- see components/ui/FormIssues.
  const [issues, setIssues] = useState<FieldIssue[]>([]);
  const bodyRef = useRef<HTMLDivElement>(null);
  useFocusFirstIssue(bodyRef, issues);



  function addArea() {
    const cleaned = newArea.trim();
    if (!cleaned) return;
    if (areas.some((a) => a.toLowerCase() === cleaned.toLowerCase())) {
      setNewArea("");
      return;
    }
    setAreas((prev) => [...prev, cleaned]);
    setNewArea("");
    areaInputRef.current?.focus();
  }

  function removeArea(area: string) {
    setAreas((prev) => prev.filter((a) => a !== area));
  }

  /**
   * Save is always pressable (except while a save is in flight) — a disabled
   * button is the one control that cannot explain itself. Whatever is
   * missing is named here instead, in the order the fields are read.
   */
  async function handleSave() {
    if (saving) return;
    const trimmedName = name.trim();
    const trimmedPhone = phone.trim();
    // BOTH boxes are judged, and both answers are kept. This used to report
    // whichever it met first -- a blank name and a bad number meant two
    // Saves to be told about two boxes that were both visible the whole
    // time.
    const found: FieldIssue[] = [];
    if (trimmedName.length === 0) {
      found.push({ field: "name", message: "Enter the agent's name." });
    }
    if (trimmedPhone.length === 0) {
      found.push({
        field: "phone",
        message: "Enter the agent's WhatsApp number — it's where every site-visit hand-off is sent.",
      });
    } else {
      // Present, but is it a number? "abc" used to save happily and the
      // agent card then read "abc" — an agent nothing can ever be sent to.
      // The same ten-digit rule as every other phone box in the
      // application, rather than a looser one of its own. The DUPLICATE
      // check is deliberately not attempted here: only the backend can see
      // the other agents, and it answers with a 409 that lands in
      // formError below.
      const badPhone = phoneFieldError(trimmedPhone);
      if (badPhone) found.push({ field: "phone", message: badPhone });
    }
    setIssues(found);
    setInvalid({ name: found.some((i) => i.field === "name"), phone: found.some((i) => i.field === "phone") });
    if (found.length > 0) {
      setFormError(null);
      return;
    }


    setSaving(true);
    setFormError(null);
    try {
      // "+919003455667" — what the backend normalizes to anyway
      // (phone_utils.normalize_phone), sent already canonical so what the
      // dialog shows and what the row holds can never disagree.
      const body = { name: trimmedName, phone: toStoredNumber(trimmedPhone) ?? trimmedPhone, coverage_areas: areas };
      if (mode === "edit" && agent) {
        const updated = await agentApi.updateAgent(agent.agent_id, body);
        toast.push({ tone: "ok", title: "Agent updated", message: updated.name });
        onSaved({ ...agent, ...updated });
      } else {
        const created = await agentApi.createAgent(body);
        toast.push({ tone: "ok", title: "Agent added", message: created.name });
        onSaved({ ...created, active_clients: [], completed_visits: [], visits_this_month: 0 });
      }
    } catch (err) {
      // Also kept in the dialog, not only in a toast: the toast fades while
      // this modal is still open, and the backend's own wording ("already
      // exists", "not a valid number") is the explanation the user needs
      // while they are looking at the field it is about.
      const message = friendlyError(err);
      setFormError(message);
      toast.push({ tone: "bad", title: mode === "edit" ? "Couldn't update this agent" : "Couldn't add this agent", message });
    } finally {
      setSaving(false);
    }
  }

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label={mode === "edit" ? "Edit agent" : "Add an agent"}>
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">Field team</div>
            <h2 className="detail-modal__title">{mode === "edit" ? "Edit agent" : "Add an agent"}</h2>
            <div className="detail-modal__sub">
              {mode === "edit" ? "Update their number or coverage areas." : "Every site visit needs someone to run it — add them here."}
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} disabled={saving} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body" ref={bodyRef}>
          <div className="stack stack-4">
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
              <div className={`field${invalid.name ? " field--bad" : ""}`} data-field="name">
                <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }} htmlFor="agent-name">
                  Name <span style={{ color: "var(--bad)" }}>*</span>
                </label>
                <input
                  id="agent-name"
                  ref={nameInputRef}
                  className={`input${invalid.name ? " input--bad" : ""}`}
                  aria-invalid={invalid.name || undefined}
                  value={name}
                  onChange={(e) => {
                    setName(e.target.value);
                    if (invalid.name && e.target.value.trim()) setInvalid((prev) => ({ ...prev, name: false }));
                  }}
                  placeholder="e.g. Ravi Patel"
                  autoFocus
                />
              </div>
              <div className={`field${invalid.phone ? " field--bad" : ""}`} data-field="phone">
                <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }} htmlFor="agent-phone">
                  WhatsApp number <span style={{ color: "var(--bad)" }}>*</span>
                </label>
                <PhoneInput
                  id="agent-phone"
                  inputRef={phoneInputRef}
                  invalid={invalid.phone}
                  value={phone}
                  onChange={(value) => {
                    setPhone(value);
                    if (invalid.phone && value) setInvalid((prev) => ({ ...prev, phone: false }));
                  }}
                  ariaLabel="WhatsApp number"
                  placeholder="9003455667"
                />
                <span className="field__hint field__hint--key">
                  <IconAlert size={14} />
                  <span>10 digits — every site-visit hand-off is sent here.</span>
                </span>
              </div>
            </div>

            <div className="field">
              <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
                Areas they cover
              </label>

              {areas.length > 0 && (
                <div className="row-flex" style={{ gap: 9, marginBottom: 4 }}>
                  {areas.map((area) => (
                    <span key={area} className="chip">
                      <IconTag size={12} />
                      {area}
                      <button type="button" className="chip__x" onClick={() => removeArea(area)} aria-label={`Remove ${area}`}>
                        <IconX size={12} />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              <div className="row-flex" style={{ alignItems: "flex-start" }}>
                <input
                  ref={areaInputRef}
                  className="input"
                  style={{ flex: "1 1 220px" }}
                  value={newArea}
                  onChange={(e) => setNewArea(e.target.value)}
                  placeholder="Add an area, e.g. Vesu"
                  aria-label="New coverage area"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addArea();
                    }
                  }}
                />
                <Button icon={<IconPlus size={15} />} variant="ghost" onClick={addArea} disabled={!newArea.trim()}>
                  Add
                </Button>
              </div>
              <span className="field__hint">Optional — used to flag "Covers this area" when assigning a client.</span>
            </div>

          </div>
        </div>

        <FormIssues issues={issues} error={formError} />

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <span style={{ marginLeft: "auto" }}>
            <Button variant="primary" onClick={handleSave} busy={saving}>
              {mode === "edit" ? "Save changes" : "Add agent"}
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
