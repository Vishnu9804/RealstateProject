import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { agentApi } from "../api/agentApi";
import type { AgentSummary } from "../api/types";
import { friendlyError } from "../lib/apiError";
import { useToast } from "./ui/Toast";
import { Button } from "./ui/Primitives";
import { IconPlus, IconTag, IconX } from "./ui/Icons";

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
  const [phone, setPhone] = useState(agent?.phone ?? "");
  const [areas, setAreas] = useState<string[]>(agent?.coverage_areas ?? []);
  const [newArea, setNewArea] = useState("");
  const [saving, setSaving] = useState(false);
  const areaInputRef = useRef<HTMLInputElement>(null);

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

  const canSave = name.trim().length > 0 && phone.trim().length > 0 && !saving;

  async function handleSave() {
    if (!canSave) return;
    setSaving(true);
    try {
      const body = { name: name.trim(), phone: phone.trim(), coverage_areas: areas };
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
      toast.push({ tone: "bad", title: mode === "edit" ? "Couldn't update this agent" : "Couldn't add this agent", message: friendlyError(err) });
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

        <div className="detail-modal__body">
          <div className="stack stack-4">
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
              <div className="field">
                <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
                  Name
                </label>
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Ravi Patel" autoFocus />
              </div>
              <div className="field">
                <label className="field__hint" style={{ fontWeight: 560, color: "var(--ink-2)" }}>
                  WhatsApp number
                </label>
                <input className="input" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="90034 55667" />
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

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <span style={{ marginLeft: "auto" }}>
            <Button variant="primary" onClick={handleSave} busy={saving} disabled={!canSave}>
              {mode === "edit" ? "Save changes" : "Add agent"}
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
