import { useCallback, useMemo, useState } from "react";
import { agentApi } from "../api/agentApi";
import { inquiryClientApi } from "../api/inquiryClientApi";
import type { AgentSummary, AssignedClientSummary, InquiryClientRecord } from "../api/types";
import { usePolling } from "../hooks/usePolling";
import { friendlyError } from "../lib/apiError";
import { formatCompactInr, relativeTime } from "../lib/formatters";
import AgentFormDialog from "../components/AgentFormDialog";
import CompleteVisitDialog from "../components/CompleteVisitDialog";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import { useToast } from "../components/ui/Toast";
import { Avatar, Badge, Button, EmptyState, Panel, Stat } from "../components/ui/Primitives";
import { IconCheck, IconClock, IconEdit, IconInbox, IconPlus, IconTag, IconTrash, IconUserCheck, IconUsers } from "../components/ui/Icons";

const REFRESH_INTERVAL_MS = 8000;

export default function AgentsPage() {
  const toast = useToast();
  const [agents, setAgents] = useState<AgentSummary[] | null>(null);
  const [clients, setClients] = useState<InquiryClientRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [showAddAgent, setShowAddAgent] = useState(false);
  const [editingAgent, setEditingAgent] = useState<AgentSummary | null>(null);
  const [deletingAgent, setDeletingAgent] = useState<AgentSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [completingVisit, setCompletingVisit] = useState<{ agent: AgentSummary; client: AssignedClientSummary } | null>(null);

  const load = useCallback(async (manual = false) => {
    setRefreshing(true);
    try {
      const [agentData, clientData] = await Promise.all([agentApi.getAgents(), inquiryClientApi.getClients(500)]);
      setAgents(agentData);
      setClients(clientData);
      setLastUpdated(new Date());
      setError(null);
    } catch (err) {
      const message = friendlyError(err);
      setError(message);
      if (manual) toast.push({ tone: "bad", title: "Refresh failed", message });
    } finally {
      setRefreshing(false);
    }
  }, [toast]);

  usePolling(() => load(false), REFRESH_INTERVAL_MS);

  const allAgents = useMemo(() => agents ?? [], [agents]);
  const allClients = useMemo(() => clients ?? [], [clients]);
  const assignedCount = useMemo(() => allClients.filter((c) => c.assigned_agent_id).length, [allClients]);
  const unassignedCount = allClients.length - assignedCount;
  const visitsThisMonth = useMemo(() => allAgents.reduce((sum, a) => sum + a.monthly_visits, 0), [allAgents]);

  const loading = agents === null && error === null;

  function replaceAgent(agentId: string, updater: (agent: AgentSummary) => AgentSummary) {
    setAgents((prev) => (prev ? prev.map((a) => (a.agent_id === agentId ? updater(a) : a)) : prev));
  }

  async function handleDelete() {
    if (!deletingAgent) return;
    setDeleting(true);
    try {
      await agentApi.deleteAgent(deletingAgent.agent_id);
      setAgents((prev) => (prev ? prev.filter((a) => a.agent_id !== deletingAgent.agent_id) : prev));
      toast.push({ tone: "ok", title: "Agent removed", message: deletingAgent.name });
      setDeletingAgent(null);
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not remove this agent", message: friendlyError(err) });
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="stack stack-5">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Step 4 — Field team</div>
          <h1 className="page-title">Agents</h1>
          <p className="section-head__sub">
            Your site-visit team. Assign a client to an agent and both sides get everything they need on WhatsApp — no
            phone call in between.
          </p>
        </div>
        <Button variant="primary" icon={<IconPlus size={15} />} onClick={() => setShowAddAgent(true)}>
          Add agent
        </Button>
      </header>

      {allAgents.length > 0 && (
        <div className="stat-grid">
          <Stat label="Agents" value={allAgents.length} icon={<IconUserCheck size={13} />} delay={0} />
          <Stat label="Clients assigned" value={assignedCount} icon={<IconUsers size={13} />} tone="ok" delay={60} />
          <Stat label="Unassigned" value={unassignedCount} icon={<IconUsers size={13} />} tone={unassignedCount > 0 ? "warn" : undefined} delay={120} />
          <Stat label="Visits this month" value={visitsThisMonth} icon={<IconClock size={13} />} tone="accent" delay={180} />
        </div>
      )}

      {error && (
        <Panel>
          <p style={{ color: "var(--bad)" }}>{error}</p>
        </Panel>
      )}

      {loading && (
        <Panel>
          <div className="row-flex faint small">
            <span className="spinner" /> Loading agents…
          </div>
        </Panel>
      )}

      {agents !== null && allAgents.length === 0 && (
        <Panel>
          <EmptyState
            icon={<IconInbox size={38} />}
            title="No agents yet"
            body="Add your first field agent to start assigning clients to site visits."
            action={
              <Button icon={<IconPlus size={15} />} onClick={() => setShowAddAgent(true)}>
                Add agent
              </Button>
            }
          />
        </Panel>
      )}

      {allAgents.length > 0 && (
        <div className="card-grid">
          {allAgents.map((agent, index) => (
            <AgentCard
              key={agent.agent_id}
              agent={agent}
              delay={Math.min(index * 40, 320)}
              onEdit={() => setEditingAgent(agent)}
              onDelete={() => setDeletingAgent(agent)}
              onCompleteVisit={(client) => setCompletingVisit({ agent, client })}
            />
          ))}
        </div>
      )}

      {lastUpdated && (
        <span className="toolbar__meta faint small">
          {refreshing ? (
            <>
              <span className="spinner" style={{ width: 12, height: 12 }} /> Syncing…
            </>
          ) : (
            <>Updated {relativeTime(lastUpdated)}</>
          )}
        </span>
      )}

      {showAddAgent && (
        <AgentFormDialog
          onClose={() => setShowAddAgent(false)}
          onSaved={(created) => {
            setAgents((prev) => [...(prev ?? []), created]);
            setShowAddAgent(false);
          }}
        />
      )}

      {editingAgent && (
        <AgentFormDialog
          agent={editingAgent}
          onClose={() => setEditingAgent(null)}
          onSaved={(updated) => {
            replaceAgent(updated.agent_id, () => updated);
            setEditingAgent(null);
          }}
        />
      )}

      {deletingAgent && (
        <ConfirmDialog
          title="Remove this agent?"
          body={
            <>
              <strong>{deletingAgent.name}</strong> will be removed from the field team.
              {deletingAgent.active_clients.length > 0 && (
                <>
                  {" "}
                  Their {deletingAgent.active_clients.length} active client
                  {deletingAgent.active_clients.length === 1 ? "" : "s"} will become unassigned, not deleted.
                </>
              )}
            </>
          }
          confirmLabel="Remove agent"
          tone="danger"
          busy={deleting}
          onConfirm={handleDelete}
          onClose={() => setDeletingAgent(null)}
        />
      )}

      {completingVisit && (
        <CompleteVisitDialog
          agentId={completingVisit.agent.agent_id}
          agentName={completingVisit.agent.name}
          client={completingVisit.client}
          onClose={() => setCompletingVisit(null)}
          onCompleted={(visit) => {
            replaceAgent(completingVisit.agent.agent_id, (a) => ({
              ...a,
              active_clients: a.active_clients.filter(
                (c) => !(c.phone === completingVisit.client.phone && c.property_record_id === completingVisit.client.property_record_id),
              ),
              completed_visits: [visit, ...a.completed_visits],
            }));
            setCompletingVisit(null);
          }}
        />
      )}
    </div>
  );
}

function AgentCard({
  agent,
  delay,
  onEdit,
  onDelete,
  onCompleteVisit,
}: {
  agent: AgentSummary;
  delay: number;
  onEdit: () => void;
  onDelete: () => void;
  onCompleteVisit: (client: AssignedClientSummary) => void;
}) {
  return (
    <Panel interactive pad className="stack stack-3" delay={delay}>
      <div className="row-flex" style={{ gap: 12, alignItems: "flex-start" }}>
        <Avatar name={agent.name} size={44} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="pcard__title cell-truncate">{agent.name}</div>
          <div className="pcard__sub cell-truncate">{agent.phone}</div>
        </div>
        <div className="row-flex" style={{ gap: 4 }} onClick={(event) => event.stopPropagation()}>
          <Button size="sm" variant="ghost" iconOnly icon={<IconEdit size={14} />} onClick={onEdit} aria-label="Edit agent" />
          <Button size="sm" variant="ghost" iconOnly icon={<IconTrash size={14} />} onClick={onDelete} aria-label="Remove agent" />
        </div>
      </div>

      {agent.coverage_areas.length > 0 && (
        <div className="stack stack-1">
          <div className="detail__k">Covers</div>
          <div className="row-flex" style={{ gap: 6, flexWrap: "wrap" }}>
            {agent.coverage_areas.map((area) => (
              <span key={area} className="fact">
                <IconTag size={11} /> {area}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="stack stack-1">
        <div className="row-flex" style={{ justifyContent: "space-between" }}>
          <div className="detail__k">Active visits</div>
          <Badge tone={agent.active_clients.length > 0 ? "ok" : "info"}>{agent.active_clients.length}</Badge>
        </div>
        {agent.active_clients.length === 0 ? (
          <div className="faint small">Free right now</div>
        ) : (
          <div className="stack stack-2">
            {agent.active_clients.map((client) => (
              <div key={`${client.phone}-${client.property_record_id}`} className="row-flex" style={{ justifyContent: "space-between" }}>
                <div style={{ minWidth: 0 }}>
                  <div className="cell-truncate">
                    {client.name || client.phone} <span className="faint small">— {client.property_label}</span>
                  </div>
                  <div className="faint small">{budgetRange(client.budget_min_inr, client.budget_max_inr)}</div>
                </div>
                <Button size="sm" variant="ghost" icon={<IconCheck size={13} />} onClick={() => onCompleteVisit(client)}>
                  Mark complete
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>

      {agent.completed_visits.length > 0 && (
        <div className="stack stack-1">
          <div className="detail__k">Completed visits</div>
          <div className="stack stack-2">
            {agent.completed_visits.map((visit) => (
              <div key={visit.visit_id} className="row-flex" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                <div style={{ minWidth: 0 }}>
                  <div className="cell-truncate">
                    {visit.client_name || visit.client_phone}
                    {visit.property_label && <span className="faint small"> — {visit.property_label}</span>}
                  </div>
                  {visit.notes && <div className="faint small">{visit.notes}</div>}
                </div>
                <Badge tone="ok">{visit.completed_at ? relativeTime(new Date(visit.completed_at)) : "Done"}</Badge>
              </div>
            ))}
          </div>
        </div>
      )}

      <hr className="rule" style={{ margin: "2px 0" }} />

      <div className="row-flex" style={{ justifyContent: "space-between" }}>
        <div>
          <div className="detail__k">On team since</div>
          <div className="faint small">{agent.created_at ? new Date(agent.created_at).toLocaleDateString("en-IN", { month: "short", year: "numeric" }) : "—"}</div>
        </div>
        <div>
          <div className="detail__k">Visits / month</div>
          <Badge tone="info">{agent.monthly_visits}</Badge>
        </div>
      </div>
    </Panel>
  );
}

function budgetRange(min: number | null, max: number | null): string {
  if (min === null && max === null) return "—";
  if (min !== null && max !== null) return `${formatCompactInr(min)} – ${formatCompactInr(max)}`;
  if (min !== null) return `${formatCompactInr(min)}+`;
  return `Up to ${formatCompactInr(max as number)}`;
}
