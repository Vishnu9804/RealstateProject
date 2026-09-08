import type { InquiryClientRecord, LandingLeadRecord } from "../api/types";

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
