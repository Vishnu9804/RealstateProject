import type { AgentSummary } from "../api/types";

/**
 * The field-team list, shared the same way lib/propertyListCache.ts shares
 * the properties list. AgentsPage polls agentApi.getAgents() live and
 * writes every result here as a side effect; ClientMatchesDialog,
 * SelectPropertyPage, and anything else that just needs to know who's
 * assigned to what reads this to paint instantly (~2s round trip
 * otherwise — a full agents+active-visits join, see Database/
 * agent_repository.get_all_agents_with_active_clients's own docstring on
 * why that's one query rather than several) and then always fetches its
 * own fresh copy in the background, the same "seed once, keep correcting"
 * contract lib/clientMatchCache.ts documents — a stale hit here is never
 * trusted longer than one more round trip.
 *
 * Deliberately a plain module object, not React Context — same reasoning
 * as propertyListCache.ts's own docstring: nothing here needs to be
 * reactive across components, only readable once at whichever screen
 * mounts next.
 */
let cached: AgentSummary[] | null = null;

export function getCachedAgents(): AgentSummary[] | null {
  return cached;
}

export function setCachedAgents(data: AgentSummary[]): void {
  cached = data;
}
