/**
 * Mirrors Backend/Model/WhatsAppDataFetchingModel/area_knowledge.py — the
 * internal area knowledge base the property pipeline grows as a SIDE EFFECT
 * of LLM structuring, and the analysis of how well it is doing. Read-only:
 * nothing in this app writes to it, the pipeline does (see
 * Backend/Service/WhatsAppDataFetchingService/area_knowledge_service.py) —
 * the one exception is "reset analysis", which only zeroes the counters.
 */
export interface AreaKnowledgeTotals {
  batches_observed: number;
  /** Visits to the knowledge base — one per property the LLM produced. */
  properties_seen: number;
  properties_recorded: number;
  properties_skipped_no_area: number;
  properties_all_known: number;
  properties_with_new_places: number;
  /** Place STRINGS checked. One visit usually checks several. */
  place_lookups: number;
  place_hits: number;
  place_writes: number;
  cross_area_collisions: number;
  hit_rate: number;
  area_count: number;
  place_count: number;
  first_observed_at: string | null;
  last_observed_at: string | null;
  stats_since: string | null;
}

export interface AreaKnowledgeBreakdown {
  /** "area" | "address" | "society", or "accepted" | "outsider". */
  name: string;
  lookups: number;
  hits: number;
  writes: number;
  hit_rate: number;
}

export interface AreaKnowledgeArea {
  area: string;
  place_count: number;
  places: string[];
  lookups: number;
  hits: number;
  writes: number;
  hit_rate: number;
  properties: number;
  first_seen: string | null;
  last_updated: string | null;
}

export interface AreaKnowledgeEvent {
  at: string;
  area: string | null;
  source_message_id: string | null;
  record_id: string | null;
  review_status: string | null;
  lookups: number;
  hits: number;
  writes: number;
  hit_places: string[];
  new_places: string[];
  collisions: string[];
  skipped: boolean;
  skip_reason: string | null;
}

export interface AreaKnowledgeOverview {
  /** Absolute path of the .py knowledge base file on the server. */
  file_path: string;
  totals: AreaKnowledgeTotals;
  by_source: AreaKnowledgeBreakdown[];
  by_status: AreaKnowledgeBreakdown[];
  areas: AreaKnowledgeArea[];
  events: AreaKnowledgeEvent[];
}

/** One on-disk knowledge base file, exactly as it reads on disk. */
export interface AreaKnowledgeFile {
  name: string;
  path: string;
  content: string;
}

export interface AreaKnowledgeFilesResponse {
  files: AreaKnowledgeFile[];
}

/* ------------------------------------------------------------ LLM cost */

/**
 * Mirrors Backend/Model/LLMUsageModel/llm_usage.py. One set of usage
 * numbers — used both for a site's own totals and for one model's row
 * underneath it, so the two are always directly comparable.
 */
export interface LLMUsageMetrics {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  avg_input_tokens_per_call: number;
  avg_output_tokens_per_call: number;
  avg_total_tokens_per_call: number;
}

/** One model's usage under a site — e.g. "glm-4.6" under "property". */
export interface LLMUsageModelBreakdown extends LLMUsageMetrics {
  model: string;
}

/** One LLM call site (property / requirement / intent). `totals` is always
 *  the sum of `models`, computed server-side — never a separate counter —
 *  so the two can never disagree. */
export interface LLMUsageSite {
  totals: LLMUsageMetrics;
  models: LLMUsageModelBreakdown[];
}

/** Keyed by site id: "property" | "requirement" | "intent". Keyed as a
 *  plain record (not a fixed union) so a backend that ever adds a fourth
 *  site needs no frontend type change to keep compiling. */
export interface LLMUsageOverview {
  sites: Record<string, LLMUsageSite>;
}

/* ------------------------------------------------------------- neon db */

/** Mirrors Backend/Model/NeonUsageModel/neon_usage.py. Every number is
 *  measured locally by the backend — reading this costs no CU-hours and no
 *  network transfer (see that feature's service docstring). */
export interface NeonAssumptions {
  compute_units: number;
  autosuspend_seconds: number;
  tracking_since: string | null;
}

export interface NeonTotals {
  wakeups: number;
  queries: number;
  cu_hours: number;
  awake_seconds: number;
  bytes: number;
  bytes_text: string;
}

/** One operation inside a wake-up, or the folded "smaller operations" line
 *  standing in for everything too small to deserve its own row. */
export interface NeonOperation {
  label: string;
  area: string;
  queries: number;
  seconds: number;
  bytes: number;
  summary: string;
}

/** One wake-up — a run of queries with no gap long enough for the compute
 *  to suspend. This, not a single query, is what adds CU-hours. */
export interface NeonComputeWindow {
  at: string;
  ended_at: string;
  still_awake: boolean;
  trigger: string;
  trigger_kind: string;
  queries: number;
  active_seconds: number;
  idle_tail_seconds: number;
  billed_seconds: number;
  cu_hours: number;
  share_percent: number;
  summary: string;
  operations: NeonOperation[];
}

export interface NeonTransferEntry {
  at: string;
  trigger: string;
  trigger_kind: string;
  label: string;
  area: string;
  queries: number;
  bytes: number;
  rows: number;
  share_percent: number;
  summary: string;
}

export interface NeonUsageOverview {
  assumptions: NeonAssumptions;
  totals: NeonTotals;
  compute: NeonComputeWindow[];
  transfer: NeonTransferEntry[];
}

/* -------------------------------------------------------- hourly feeds */

/** Every hourly feed answers with only what changed after the cursor it was
 *  sent (see Backend/Service/BackendUsageService/usage_feed.py). */
export interface FeedResponse<T> {
  cursor: string;
  items: T[];
}

/** One IST hour × call site × model — Backend llm_usage_service. */
export interface LLMHourlyItem {
  k: string;
  hour: number;
  site: string;
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
}

/** A wake-up's rows without their share of the total — the page computes
 *  shares against the 48 hours it shows. */
export type NeonComputeRow = Omit<NeonComputeWindow, "share_percent">;
export type NeonTransferRow = Omit<NeonTransferEntry, "share_percent">;

export interface NeonWindowItem {
  k: string;
  compute: NeonComputeRow;
  transfer: NeonTransferRow[];
}

export interface NeonWindowsResponse extends FeedResponse<NeonWindowItem> {
  assumptions: NeonAssumptions;
}

/** One IST hour × one endpoint / background job — Backend cpu_usage_service. */
export interface CpuEntryItem {
  k: string;
  hour: number;
  label: string;
  kind: string;
  area: string;
  count: number;
  /** CPU seconds. */
  cpu: number;
  /** Wall-clock seconds (includes waiting on the network / database). */
  wall: number;
  max_cpu: number;
}

/** The whole process's exact CPU and average memory for one IST hour. */
export interface CpuProcessItem {
  k: string;
  hour: number;
  label: "__process__";
  cpu: number;
  rss_byte_seconds: number;
  seconds: number;
  rss_peak: number;
}

export type CpuItem = CpuEntryItem | CpuProcessItem;

export function isProcessItem(item: CpuItem): item is CpuProcessItem {
  return item.label === "__process__";
}

export interface CpuResponse extends FeedResponse<CpuItem> {
  python_threads: number;
  uptime_seconds: number;
}

export interface MemoryBreakdown {
  label: string;
  bytes: number;
}

export interface MemoryItem {
  id: string;
  label: string;
  detail: string;
  unit: string;
  count: number;
  bytes: number;
  /** False when a sample was measured and scaled up. */
  exact: boolean;
  loaded: boolean;
  fallback_only: boolean;
  breakdown: MemoryBreakdown[];
}

export interface MemoryGroup {
  id: string;
  label: string;
  items: MemoryItem[];
}

export interface MemorySnapshot {
  measured_at: string;
  took_ms: number;
  database_mode: boolean | null;
  process: {
    rss_bytes: number | null;
    peak_rss_bytes: number | null;
    container_bytes: number | null;
    container_limit_bytes: number | null;
    python_threads: number;
    uptime_seconds: number | null;
    platform: string;
  };
  billable_bytes: number | null;
  billable_source: "container" | "process" | null;
  measured_bytes: number;
  unaccounted_bytes: number | null;
  groups: MemoryGroup[];
}

export type MessageOutcome = "converted" | "skipped" | "rerouted" | "missing";

/** One WhatsApp message the property or requirement LLM read, and every model
 *  it turned that message into — Backend message_model_service. */
export interface MessageModelEntry {
  id: string;
  /** Unix seconds when the LLM answered. */
  at: number;
  site: "property" | "requirement";
  model: string;
  message_id: string;
  received_at: string | null;
  group: string;
  chat_type: string;
  sender: string;
  sender_saved: string;
  sender_phone: string;
  text: string;
  text_truncated: boolean;
  outcome: MessageOutcome;
  note: string | null;
  models: Record<string, unknown>[];
  /** This message's share of its batch call(s). */
  tokens: { input: number; output: number; total: number };
  /** The whole batch the message was sent in. */
  batch: { messages: number; calls: number; input: number; output: number; retry: boolean };
}
