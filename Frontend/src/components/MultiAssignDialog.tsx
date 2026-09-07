import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import type { AgentSummary, InquiryClientRecord } from "../api/types";
import type { AgentAssignment, HandoffPropertyLike } from "../lib/handoffTemplate";
import { Avatar, Badge, Button } from "./ui/Primitives";
import { IconArrowRight, IconTag, IconX } from "./ui/Icons";

export interface SelectableProperty {
  property: HandoffPropertyLike;
  source: "matched" | "manual";
}

/**
 * "Step 4 — Assign": which agent takes each selected property — one agent
 * card grid per property (name, phone, coverage areas, whether they cover
 * this client's wanted area, active clients, visits this month — the same
 * card the single-property flow always showed), so picking an agent for
 * two properties usually means clicking the same card twice; picking a
 * different agent for a different property is just as direct. Each
 * distinct agent chosen ends up with their own message in
 * HandoffDialog.tsx, naming only the property(ies) assigned to them.
 */
export default function MultiAssignDialog({
  client,
  agents,
  selected,
  onClose,
  onContinue,
}: {
  client: InquiryClientRecord;
  agents: AgentSummary[];
  selected: SelectableProperty[];
  onClose: () => void;
  onContinue: (assignments: AgentAssignment[]) => void;
}) {
  const [assignedAgentId, setAssignedAgentId] = useState<Record<string, string>>({});

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

  const wantedAreas = (client.preferred_areas ?? "")
    .split(/[,/]/)
    .map((a) => a.trim().toLowerCase())
    .filter(Boolean);

  function coversWantedArea(agent: AgentSummary): boolean {
    if (wantedAreas.length === 0) return false;
    return agent.coverage_areas.some((area) => wantedAreas.includes(area.trim().toLowerCase()));
  }

  const allAssigned = selected.length > 0 && selected.every(({ property }) => assignedAgentId[property.record_id]);

  const assignments = useMemo<AgentAssignment[]>(() => {
    const byAgent = new Map<string, HandoffPropertyLike[]>();
    for (const { property } of selected) {
      const agentId = assignedAgentId[property.record_id];
      if (!agentId) continue;
      if (!byAgent.has(agentId)) byAgent.set(agentId, []);
      byAgent.get(agentId)!.push(property);
    }
    const result: AgentAssignment[] = [];
    for (const [agentId, properties] of byAgent) {
      const agent = agents.find((a) => a.agent_id === agentId);
      if (agent) result.push({ agent, properties });
    }
    return result;
  }, [assignedAgentId, selected, agents]);

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="Assign an agent" style={{ maxWidth: 920 }}>
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">Step 4 — Assign</div>
            <h2 className="detail-modal__title">
              Who takes {client.name || "this client"} on the site visit{selected.length > 1 ? "s" : ""}?
            </h2>
            <div className="detail-modal__sub">Areas wanted: {client.preferred_areas || "Not specified"}</div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body stack stack-5">
          {agents.length === 0 ? (
            <p className="faint small">No agents added yet — add one from the Agents page first.</p>
          ) : (
            selected.map(({ property, source }) => {
              const chosenId = assignedAgentId[property.record_id];
              return (
                <div key={property.record_id} className="stack stack-3">
                  <div className="row-flex" style={{ gap: 8, justifyContent: "space-between", flexWrap: "wrap" }}>
                    <div className="row-flex" style={{ gap: 8 }}>
                      <strong>{property.society_name || property.property_type || "Property"}</strong>
                      <span className="faint small">{[property.bhk, property.area_name].filter(Boolean).join(" · ")}</span>
                    </div>
                    <Badge tone={source === "matched" ? "accent" : "info"}>{source === "matched" ? "Matched" : "Manually Added"}</Badge>
                  </div>

                  <div className="card-grid">
                    {agents.map((agent) => {
                      const covers = coversWantedArea(agent);
                      const isChosen = chosenId === agent.agent_id;
                      return (
                        <button
                          key={agent.agent_id}
                          type="button"
                          className={`panel panel--interactive pcard${isChosen ? " panel--selected" : ""}`}
                          style={{ textAlign: "left", cursor: "pointer" }}
                          onClick={() => setAssignedAgentId((prev) => ({ ...prev, [property.record_id]: agent.agent_id }))}
                        >
                          <div className="pcard__top">
                            <div className="row-flex" style={{ gap: 10 }}>
                              <Avatar name={agent.name} size={38} />
                              <div style={{ minWidth: 0 }}>
                                <div className="pcard__title cell-truncate">{agent.name}</div>
                                <div className="pcard__sub cell-truncate">{agent.phone}</div>
                              </div>
                            </div>
                          </div>

                          {agent.coverage_areas.length > 0 && <div className="faint small">Covers {agent.coverage_areas.join(", ")}</div>}

                          <div style={{ marginTop: 4 }}>
                            <Badge tone={covers ? "ok" : "info"}>
                              <IconTag size={11} /> {covers ? "Covers this area" : "Different area"}
                            </Badge>
                          </div>

                          <div className="pcard__facts" style={{ marginTop: 10 }}>
                            <span className="fact">Active {agent.active_clients.length}</span>
                            <span className="fact">Visits/mo {agent.monthly_visits}</span>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div className="detail-modal__foot">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <span style={{ marginLeft: "auto" }}>
            <Button variant="primary" icon={<IconArrowRight size={14} />} onClick={() => onContinue(assignments)} disabled={!allAssigned}>
              Continue to hand-off
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
