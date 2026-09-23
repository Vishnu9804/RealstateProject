import type { InquiryClientRecord, LandingLeadRecord, MatchCounts } from "../api/types";

/**
 * Same idea as lib/propertyListCache.ts, for the Inquiries page's two lists
 * (clients, leads). Only one page reads these today, so the "shared between
 * pages" benefit doesn't apply here the way it does for properties — this
 * still buys the "leave the page and come back shortly after" case: without
 * it, remounting InquiryClientsPage always re-fetched both lists from
 * scratch, regardless of whether clients_version/leads_version had changed.
 */
interface CachedList<T> {
  data: T[];
  version: string;
  fetchedAt: number;
}

let clientsCache: CachedList<InquiryClientRecord> | null = null;
let leadsCache: CachedList<LandingLeadRecord> | null = null;

export function getCachedClients(): CachedList<InquiryClientRecord> | null {
  return clientsCache;
}

export function setCachedClients(data: InquiryClientRecord[], version: string): void {
  clientsCache = { data, version, fetchedAt: Date.now() };
}

export function getCachedLeads(): CachedList<LandingLeadRecord> | null {
  return leadsCache;
}

export function setCachedLeads(data: LandingLeadRecord[], version: string): void {
  leadsCache = { data, version, fetchedAt: Date.now() };
}

/**
 * The Inquiries table's Matches / Completed / Status numbers — the map
 * behind those three columns, kept for the same "leave and come straight
 * back" reason the two lists above are.
 *
 * Without it, every remount of that page showed three spinning columns
 * while one request went out and came back, even when the numbers it
 * returned were the ones already on screen a moment earlier. The rows
 * themselves painted instantly from the client cache, which made the
 * spinners the only thing anyone waited for.
 *
 * `signature` is the (clients_version | leads_version | nonce) triple the
 * page fetched this map for, so the effect that revalidates can tell "this
 * is the map for the state we are in" from "this is a map for some earlier
 * state" without refetching to find out.
 */
interface CachedCounts {
  data: Record<string, MatchCounts>;
  signature: string | null;
  fetchedAt: number;
}

let matchCountsCache: CachedCounts | null = null;

export function getCachedMatchCounts(): CachedCounts | null {
  return matchCountsCache;
}

export function setCachedMatchCounts(
  data: Record<string, MatchCounts>,
  signature: string | null,
): void {
  matchCountsCache = { data, signature, fetchedAt: Date.now() };
}
