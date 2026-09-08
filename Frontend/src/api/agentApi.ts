import { apiClient } from "./client";
import type { AgentRecord, AgentSummary, HandoffTemplates, VisitRecord } from "./types";

/** Mirrors Backend/Controller/AgentManagementController/agent_controller.py's
 *  AgentCreateRequest — name and phone are required, coverage_areas
 *  defaults to empty. Also used for the Edit dialog (same field set). */
export interface AgentCreateRequest {
  name: string;
  phone: string;
  coverage_areas: string[];
}

/** Mirrors Backend/Controller/AgentManagementController/agent_controller.py's
 *  VisitCompleteRequest — the "Mark visit complete" action for one
 *  specific active visit (client + property). */
export interface VisitCompleteRequest {
  client_phone: string;
  property_record_id: string;
  notes?: string | null;
}

export const agentApi = {
  /** Every agent plus the clients currently assigned to them and their
   *  completed-visit history — see Backend/Service/AgentManagementService/
   *  agent_store.py's get_all_agents_with_stats. */
  getAgents: (): Promise<AgentSummary[]> => apiClient.get("/agents"),
  createAgent: (body: AgentCreateRequest): Promise<AgentRecord> => apiClient.post("/agents", body),
  updateAgent: (agentId: string, body: AgentCreateRequest): Promise<AgentRecord> =>
    apiClient.patch(`/agents/${encodeURIComponent(agentId)}`, body),
  deleteAgent: (agentId: string): Promise<void> => apiClient.delete(`/agents/${encodeURIComponent(agentId)}`),

  /** Records a completed site visit and drops the client out of this
   *  agent's active list — see agent_store.complete_visit. */
  completeVisit: (agentId: string, body: VisitCompleteRequest): Promise<VisitRecord> =>
    apiClient.post(`/agents/${encodeURIComponent(agentId)}/visits`, body),

  /** Settings page's editable hand-off message templates. */
  getHandoffTemplates: (): Promise<HandoffTemplates> => apiClient.get("/agents/handoff-templates"),
  setHandoffTemplates: (body: HandoffTemplates): Promise<HandoffTemplates> => apiClient.put("/agents/handoff-templates", body),
};
