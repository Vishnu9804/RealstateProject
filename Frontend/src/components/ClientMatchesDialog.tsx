import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { agentApi } from "../api/agentApi";
import { BUILDER_PROJECT_LIST_LIMIT, builderProjectApi } from "../api/builderProjectApi";
import { ApiError } from "../api/client";
import { inquiryClientApi } from "../api/inquiryClientApi";
import { landingLeadApi } from "../api/landingLeadApi";
import { matchingApi } from "../api/matchingApi";
import { propertyApi } from "../api/propertyApi";
import type {
  AgentSummary,
  AssignedClientSummary,
  BuilderProjectRecord,
  ClientMatchResult,
  InquiryClientRecord,
  MatchBucket,
  MatchedProperty,
  PropertyRecord,
  PropertySource,
  VisitRecord,
} from "../api/types";
import { friendlyError } from "../lib/apiError";
import { getCachedAgents, setCachedAgents } from "../lib/agentListCache";
import { getCachedBuilderProjectList, setCachedBuilderProjectList } from "../lib/builderProjectListCache";
import {
  getCachedCompletedVisits,
  getCachedMatchResult,
  setCachedCompletedVisits,
  setCachedMatchResult,
} from "../lib/clientMatchCache";
import { formatArea, formatPrice, formatVisitTime, relativeTime } from "../lib/formatters";
import {
  buildVisitTimeMessages,
  propertyLabel,
  type AgentAssignment,
  type HandoffPropertyLike,
} from "../lib/handoffTemplate";
import { sourceDetail, sourceLabel } from "../lib/propertyFilters";
import {
  getCachedPropertyList,
  patchCachedProperty,
  setCachedPropertyList,
} from "../lib/propertyListCache";
import type { SharePropertyLike } from "../lib/propertyShareTemplate";
import HandoffDialog from "./HandoffDialog";
import PropertyReadOnlyDialog from "./PropertyReadOnlyDialog";
import ShareClientPropertiesDialog from "./ShareClientPropertiesDialog";
import VisitMessagesDialog from "./VisitMessagesDialog";
import VisitPlannerDialog from "./VisitPlannerDialog";
import ConfirmDialog from "./ui/ConfirmDialog";
import SourceTag from "./ui/SourceTag";
import { useToast } from "./ui/Toast";
import {
  Badge,
  Button,
  Copyable,
  EmptyState,
  Note,
  Segmented,
  SkeletonRows,
} from "./ui/Primitives";
import {
  IconAlert,
  IconBuilding,
  IconCheck,
  IconClock,
  IconEdit,
  IconInbox,
  IconMessage,
  IconMove,
  IconPin,
  IconPlus,
  IconRefresh,
  IconRuler,
  IconSend,
  IconTrash,
  IconUserCheck,
  IconX,
} from "./ui/Icons";

/* ========================================================================
   Categories & sections
   ======================================================================== */

/** The two drawers a matchable property can be sitting in on the
 *  Properties page. There is deliberately no "needs review" drawer here:
 *  a flagged property is one almost nothing could be extracted from, so
 *  it is excluded from matching entirely (see Backend/Service/
 *  ClientPropertyMatchingService/matching_service.py's _is_matchable) and
 *  there would never be anything to show under such a tab. The Needs
 *  review queue lives only on the Properties page, where a human completes
 *  a flagged property and files it into one of these two. */
export type PropertyCategory = "main" | "outsider";

/** What the dialog's top row can be showing — the two review drawers
 *  above, the Assigned view (every site visit currently out with an agent
 *  for this client, re-visits included), or the Completed view. Neither of
 *  the last two is a drawer: which drawer a property sits in on the
 *  Properties page is not the relevant fact about a visit. Segmented's own
 *  `null` state (see ui/Primitives.tsx's own comment on it) is exactly "a
 *  different control currently owns the view", which is precisely what's
 *  true while this reads "assigned" or "completed". */
export type DialogView = PropertyCategory | "assigned" | "completed";

/** What onChanged can tell its caller about the change it reports, so the
 *  caller can show the new figure straight away instead of waiting for its
 *  own re-read. Only a hand-off passes one: `newlyAssigned` is how many
 *  still-outstanding properties it put out with an agent that weren't
 *  already. */
export interface CountsChangeHint {
  newlyAssigned: number;
}

const CATEGORY_LABEL: Record<PropertyCategory, string> = {
  main: "Main",
  outsider: "Outsider",
};

/** Ordered exactly as the sections render: hand-picked properties first
 *  (the operator put them there on purpose, so they lead), then the
 *  algorithm's own three buckets strongest-first.
 *
 *  "website" is deliberately NOT in this list — it is never a card's
 *  PRIMARY section (see DialogItem.fromWebsiteInquiry and the Web Site
 *  Property Inquiry block below), only a fallback home for a
 *  website-enquired property that didn't otherwise score or get hand-picked,
 *  so it must never double-render through this normal loop too. */
type SectionKey = "manual" | MatchBucket | "website";
const SECTION_ORDER: SectionKey[] = ["manual", "high", "medium", "low"];
const SECTION_LABEL: Record<SectionKey, string> = {
  manual: "Manually added",
  high: "High matches",
  medium: "Medium matches",
  low: "Low matches",
  website: "Web Site Property Inquiry",
};

const BUCKET_TONE: Record<MatchBucket, "ok" | "warn" | "bad"> = {
  high: "ok",
  medium: "warn",
  low: "bad",
};

const FIELD_SCORE_LABEL: Record<string, string> = {
  budget: "Budget",
  location: "Location",
  bhk: "BHK",
  semantic: "Overall fit",
  size: "Size",
  furnishing: "Furnishing",
  purpose_gate: "Purpose match",
  property_type_gate: "Property type match",
};

/** The type row's "everything" option — a value no real type can have. */
const ALL_TYPES = "__all__";

/** A client's property types, one per comma-separated entry — the same
 *  reading the backend gives them (Backend/Service/
 *  ClientPropertyMatchingService/normalization.py's split_type_groups). */
function splitClientTypes(raw: string | null): string[] {
  const types: string[] = [];
  for (const part of (raw ?? "").split(",")) {
    const label = part.trim().replace(/\s+/g, " ");
    if (label && !types.some((type) => sameType(type, label)))
      types.push(label);
  }
  return types;
}

function sameType(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

/** Read the category off whatever is freshest. Both PropertyRecord and
 *  MatchedProperty carry `review_status`; the match's own cached
 *  `property_category` is a snapshot from scoring time, so it is never
 *  consulted here — moving a property between drawers has to show up
 *  immediately, without a recompute.
 *
 *  `needs_review` is deliberately NOT consulted: it is a flag, not a
 *  drawer. Scored matches are never flagged (they're filtered out
 *  server-side), but a hand-picked or website-enquired property can be —
 *  and one a human or a client deliberately chose belongs on screen, filed
 *  under the drawer it actually sits in, with the "Flagged for review"
 *  note below spelling out that its data is thin. */
function categoryOf(record: {
  review_status: "accepted" | "outsider";
}): PropertyCategory {
  return record.review_status === "outsider" ? "outsider" : "main";
}

/** What a "move to X" button actually writes. Both drawers clear
 *  `needs_review`: filing a property into Main or Outsider IS how the
 *  review flag gets resolved (see the Properties page's Needs review
 *  dialog, which offers exactly these two actions). */
function categoryPatch(target: PropertyCategory): {
  review_status: "accepted" | "outsider";
  needs_review: boolean;
} {
  return {
    review_status: target === "outsider" ? "outsider" : "accepted",
    needs_review: false,
  };
}

/** One card in the dialog — a scored match, a hand-picked property, or
 *  (when the operator manually added something the algorithm also
 *  matched) both at once, in which case the score is still shown but the
 *  card lives in the Manually added section. */
export interface DialogItem {
  recordId: string;
  section: SectionKey;
  category: PropertyCategory;
  /** A property or a builder project — both are matched. Read off the
   *  match itself (MatchedProperty.property_source); hand-picked and
   *  website-enquired cards are always properties. A builder project never
   *  has a `property` record (its card reads the match's own fields), files
   *  under Main, and has no Main/Outsider move. */
  source: PropertySource;
  match: MatchedProperty | null;
  property: PropertyRecord | null;
  /** Whatever the hand-off flow needs — the full record when we have it,
   *  the match's own display fields otherwise. */
  handoff: MatchedProperty | PropertyRecord;
  /** True when this client specifically enquired about this property on
   *  the public site (LandingPage/), as opposed to it merely having
   *  scored or been hand-picked. Independent of `section`: a website
   *  enquiry that also scored High keeps section "high" and this flag
   *  true, so it renders once in High matches and once more in the Web
   *  Site Property Inquiry block below — never a section move, just an
   *  extra appearance. */
  fromWebsiteInquiry: boolean;
}

/** One site visit currently out with an agent for THIS client — the
 *  Assigned tab's unit, read straight off the agent list the dialog already
 *  loads (AgentSummary.active_clients), so it costs no request of its own. */
interface ActiveVisit {
  agent: AgentSummary;
  active: AssignedClientSummary;
}

/** The full record behind an Assigned/Completed card, whichever kind of
 *  listing it is — both carry every field those cards read. */
type ListingRecord = PropertyRecord | BuilderProjectRecord;

/** The visit planner's answer for one ticked property: which agent and,
 *  optionally, when. Held here, on the card, until "Assign & send" — no
 *  request is made until then. */
interface VisitPlan {
  agentId: string;
  scheduledAt: string | null;
}

/** Which small planner dialog is open, and for what. */
type PlannerState =
  | { kind: "assign"; recordId: string }
  | {
      kind: "revisit";
      recordId: string;
      agentId: string | null;
      scheduledAt: string | null;
    }
  | { kind: "reschedule"; visit: ActiveVisit }
  | null;

/** The hand-off messages step. A re-visit remembers what the planner
 *  picked, so "Change assignment" can reopen the planner right where it
 *  was. */
type AssignFlow =
  | { kind: "assign"; assignments: AgentAssignment[] }
  | {
      kind: "revisit";
      recordId: string;
      agentId: string;
      scheduledAt: string | null;
      assignments: AgentAssignment[];
    }
  | null;

/** The "tell them about the new time?" step after a time is saved. */
interface TimeMessagesState {
  agent: AgentSummary;
  scheduledAt: string;
  rescheduled: boolean;
  agentMessage: string;
  clientMessage: string;
}

function timeOf(iso: string | null): number {
  return iso ? new Date(iso).getTime() : 0;
}

/** A hand-off-shaped property for a re-visit whose full record is not in
 *  the loaded list (e.g. beyond the list's size cap) — just the label the
 *  completed visit already snapshotted, which is all the message needs. */
function snapshotProperty(
  recordId: string,
  label: string | null,
): HandoffPropertyLike {
  return {
    record_id: recordId,
    property_type: null,
    bhk: null,
    unit_no: null,
    society_name: label,
    area_name: null,
    area_sqft: null,
    area_vaar: null,
    contact_name: null,
    contact_phone: null,
  };
}

/* ========================================================================
   The dialog
   ======================================================================== */

/**
 * "N properties" on the Inquiries table opens THIS — everything that used
 * to be a whole page of its own (ClientMatchesPage.tsx, which is now a
 * thin wrapper around this same component so old links still work), in one
 * focused dialog over the client list the operator was already reading.
 *
 * The shape of the screen mirrors how the work actually goes: pick the
 * drawer you're working out of (Main / Outsider), read down the properties
 * in it strongest-first, tick the ones worth showing — each tick opens the
 * small visit planner (agent, then an optional time) — and hand them off,
 * without ever losing your place in the Inquiries table. The Assigned tab
 * then holds every visit out with an agent (set or change its time there),
 * and the Completed tab every visit already made (book a re-visit there).
 */
export default function ClientMatchesDialog({
  phone,
  clientName,
  initialView,
  onClose,
  onChanged,
}: {
  phone: string;
  /** Shown in the header until the real record loads. */
  clientName?: string | null;
  /** Which tab to open on — "completed" when reached from the Inquiries
   *  table's own green "N completed" button, "main" (the default)
   *  everywhere else, including the old /inquiries/:phone/matches route. */
  initialView?: DialogView;
  onClose: () => void;
  /** Fired after anything that changes this client's property counts, so
   *  the Inquiries table's Matches/Status/Completed cells can refresh
   *  themselves. `hint` is only passed by a hand-off — see
   *  CountsChangeHint. */
  onChanged?: (hint?: CountsChangeHint) => void;
}) {
  const navigate = useNavigate();
  const toast = useToast();

  // Seeded from the module-level caches (lib/clientMatchCache.ts,
  // lib/agentListCache.ts) so a client whose dialog was already opened
  // once this session paints instantly on reopen — load() below always
  // fires a fresh fetch behind that regardless, so a seed is never the
  // last word, only a head start (see those caches' own docstrings).
  const [result, setResult] = useState<ClientMatchResult | null>(() =>
    getCachedMatchResult(phone),
  );
  const [properties, setProperties] = useState<PropertyRecord[] | null>(
    () => getCachedPropertyList()?.data ?? null,
  );
  // The Builder Projects list, for the one thing a matched builder project's
  // own fields can't give: its full record once it is only an Assigned or
  // Completed visit (facts on the card, the read-only view). Shares the
  // Builder Projects page's cache and its exact request (a bodyless 304 from
  // the backend's memory when nothing changed), and — like `properties` —
  // never gates anything: those cards fall back to the visit's own snapshot.
  const [builderProjects, setBuilderProjects] = useState<
    BuilderProjectRecord[] | null
  >(() => getCachedBuilderProjectList()?.data ?? null);
  const [client, setClient] = useState<InquiryClientRecord | null>(null);
  const [agents, setAgents] = useState<AgentSummary[] | null>(() =>
    getCachedAgents(),
  );
  const [manualPropertyIds, setManualPropertyIds] = useState<string[] | null>(
    null,
  );
  const [completedVisits, setCompletedVisits] = useState<VisitRecord[] | null>(
    () => getCachedCompletedVisits(phone),
  );
  // Property ids this client specifically enquired about on the public
  // site (LandingPage/) — see DialogItem.fromWebsiteInquiry and the Web
  // Site Property Inquiry section below. No loading gate of its own
  // (defaults to empty): purely additive information, same reasoning as
  // `agents` below — nothing here should hold up the rest of the dialog
  // for however long this one extra read takes.
  const [websitePropertyIds, setWebsitePropertyIds] = useState<string[]>([]);

  // Four independent flags, one per fetch, rather than one shared
  // "loading" — matchingApi.getMatches (scoring every stored property
  // against this one client, see matching_service.py's own docstring) is
  // far slower than the other three, and used to hold EVERYTHING behind
  // one skeleton for as long as it took, Completed tab included even
  // though completed-visits/manual-ids/agents typically resolve several
  // times faster on their own. Each starts "not loading" when a cached
  // value already exists, so a reopened dialog shows that immediately
  // instead of a skeleton it doesn't need.
  const [loadingMatches, setLoadingMatches] = useState(
    () => getCachedMatchResult(phone) === null,
  );
  const [loadingCompleted, setLoadingCompleted] = useState(
    () => getCachedCompletedVisits(phone) === null,
  );
  // client + manual ids — the two remaining reads with no cache of their
  // own (they're already at this backend's ~1.1s floor for any single
  // round trip, so there's nothing slow to hide behind a cache here).
  // agentApi.getAgents() is deliberately NOT part of this flag: nothing
  // downstream actually needs it to render — assignedAgentByProperty
  // already tolerates `agents` being null (see its own `agents ?? []`)
  // and just shows a card without its "Assigned to X" badge for a moment
  // — so gating the whole dialog on however long that ~2s+ agents+
  // active-visits join takes would cost far more than the badge is worth.
  // It gets its own ungated fetch below and simply updates state (and the
  // shared cache) whenever it lands.
  const [loadingMeta, setLoadingMeta] = useState(true);
  // The property list is its own flag for the same reason as the others
  // — only the hand-picked cards strictly need it (a scored match carries
  // its own display fields), so this shouldn't hold up matched cards.
  const [loadingProperties, setLoadingProperties] = useState(
    () => getCachedPropertyList() === null,
  );
  const [error, setError] = useState<string | null>(null);
  const [recomputing, setRecomputing] = useState(false);
  const [movingId, setMovingId] = useState<string | null>(null);
  const [removingManualId, setRemovingManualId] = useState<string | null>(null);

  const [category, setCategory] = useState<DialogView>(initialView ?? "main");
  // null = every type. Only meaningful for a client who asked for more
  // than one property type — see clientTypes below.
  const [typeView, setTypeView] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  // Agent + optional time per ticked property — see VisitPlan.
  const [plans, setPlans] = useState<Record<string, VisitPlan>>({});
  const [planner, setPlanner] = useState<PlannerState>(null);
  const [savingSchedule, setSavingSchedule] = useState(false);
  const [timeMessages, setTimeMessages] = useState<TimeMessagesState | null>(
    null,
  );
  const [openItemId, setOpenItemId] = useState<string | null>(null);
  // The read-only view opened from an Assigned/Completed card whose listing
  // is no longer among the outstanding matches — the id plus which kind of
  // listing it is, since a property and a builder project are fetched from
  // different places (see PropertyReadOnlyDialog's `source`).
  const [viewingListing, setViewingListing] = useState<{
    recordId: string;
    source: PropertySource;
  } | null>(null);
  const [assignFlow, setAssignFlow] = useState<AssignFlow>(null);
  // The "send the shortlist straight to the client" flow, deliberately
  // separate from assignFlow above: it involves no agent and no visit
  // record, so it is not a step of the assignment wizard and must not share
  // its state machine.
  const [shareOpen, setShareOpen] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [clearing, setClearing] = useState(false);

  // Every change to the agent list — a fetch, or a local patch after a
  // hand-off / time change / clear — is mirrored into the shared cache
  // here, once, so AgentsPage and the next dialog opened start from it.
  useEffect(() => {
    if (agents) setCachedAgents(agents);
  }, [agents]);

  /** The one re-read every visit action needs: assigning, re-visiting,
   *  changing a time and clearing only ever change the agents' active
   *  visits, so this — not the dialog's full five-request load() — is what
   *  runs after them. */
  const reloadAgents = useCallback(() => {
    agentApi
      .getAgents()
      .then(setAgents)
      .catch(() => {});
  }, []);

  const load = useCallback(() => {
    setError(null);

    matchingApi
      .getMatches(phone)
      .then((matchResult) => {
        setResult(matchResult);
        setCachedMatchResult(phone, matchResult);
      })
      .catch((err) => setError(friendlyError(err)))
      .finally(() => setLoadingMatches(false));

    // Allowed to fail quietly (no error surfaced): the Completed tab just
    // keeps whatever it last had — Main/Outsider don't
    // depend on this succeeding, and a genuine backend outage already
    // shows up loudly enough via the matches fetch above.
    matchingApi
      .getCompletedVisits(phone)
      .then((visits) => {
        setCompletedVisits(visits);
        setCachedCompletedVisits(phone, visits);
      })
      .catch(() => {})
      .finally(() => setLoadingCompleted(false));

    Promise.all([
      inquiryClientApi.getClient(phone),
      inquiryClientApi.getManualProperties(phone),
    ])
      .then(([clientRecord, manualIds]) => {
        setClient(clientRecord);
        setManualPropertyIds(manualIds);
      })
      // Never clobber a more specific error the matches fetch may already
      // have set — first genuine failure wins, same reasoning as
      // InquiryClientsPage's own dual-source status handling.
      .catch((err) => setError((previous) => previous ?? friendlyError(err)))
      .finally(() => setLoadingMeta(false));

    // Ungated on purpose — see loadingMeta's own comment above. The shared
    // cache is updated by the effect above, so AgentsPage/
    // SelectPropertyPage/the next ClientMatchesDialog opened all benefit
    // from whichever of them fetched most recently.
    reloadAgents();

    // Also ungated, and allowed to fail quietly: it only feeds the Web
    // Site Property Inquiry section below, which simply stays empty if
    // this doesn't land — nothing else in the dialog depends on it.
    landingLeadApi
      .getPropertyIdsForPhone(phone)
      .then(setWebsitePropertyIds)
      .catch(() => {});

    // Full property records (source message, sender, timestamps, photos
    // count, ...) for the cards and the detail view — reuses the
    // Properties dashboard's own endpoint and shares its cache, so a
    // recently-opened Properties/Landing Page/Agents-visit-dialog means
    // this has real data to show straight away. Allowed to fail quietly:
    // a matched card still renders from the match's own display fields
    // without it.
    propertyApi
      .getProperties(500)
      .then((data) => {
        setProperties(data);
        setCachedPropertyList(data, null);
      })
      .catch(() => {})
      .finally(() => setLoadingProperties(false));

    // Ungated and allowed to fail quietly, for the reason given at the
    // `builderProjects` state above.
    builderProjectApi
      .getBuilderProjects(BUILDER_PROJECT_LIST_LIMIT)
      .then((data) => {
        setBuilderProjects(data);
        setCachedBuilderProjectList(data);
      })
      .catch(() => {});
  }, [phone, reloadAgents]);

  // `properties`/`loadingProperties` above already seed from the cache
  // directly (their own useState initializers) — this just fires the
  // mandatory background refresh every one of load()'s fetches always
  // performs, regardless of what was cached.
  useEffect(() => {
    load();
  }, [load]);

  const nestedOpen =
    openItemId !== null ||
    viewingListing !== null ||
    assignFlow !== null ||
    clearOpen ||
    shareOpen ||
    planner !== null ||
    timeMessages !== null;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      // Only the top-most layer answers Escape — a nested property/assign
      // dialog closes itself first, and this one stays put.
      if (event.key === "Escape" && !nestedOpen) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, nestedOpen]);

  const propertiesById = useMemo(() => {
    const map = new Map<string, PropertyRecord>();
    for (const property of properties ?? [])
      map.set(property.record_id, property);
    return map;
  }, [properties]);

  const builderProjectsById = useMemo(
    () =>
      new Map(
        (builderProjects ?? []).map((project) => [project.record_id, project]),
      ),
    [builderProjects],
  );

  /** The full record for a listing of a known kind — looked up only where
   *  that kind lives, so an id can never be dressed in the wrong record. */
  const listingFor = useCallback(
    (recordId: string, source: PropertySource): ListingRecord | null =>
      source === "builder_project"
        ? (builderProjectsById.get(recordId) ?? null)
        : (propertiesById.get(recordId) ?? null),
    [builderProjectsById, propertiesById],
  );

  const matchById = useMemo(() => {
    const map = new Map<string, MatchedProperty>();
    for (const list of [
      result?.high ?? [],
      result?.medium ?? [],
      result?.low ?? [],
    ]) {
      for (const match of list) map.set(match.record_id, match);
    }
    return map;
  }, [result]);

  /** Every site visit currently out with an agent for THIS client —
   *  first visits and re-visits alike. Booked visits come first, soonest
   *  first; ones still waiting for a time follow, oldest assignment first. */
  const activeVisits = useMemo<ActiveVisit[]>(() => {
    const list: ActiveVisit[] = [];
    for (const agent of agents ?? []) {
      for (const active of agent.active_clients) {
        if (active.phone === phone) list.push({ agent, active });
      }
    }
    return list.sort((a, b) => {
      const aWhen = a.active.scheduled_at
        ? timeOf(a.active.scheduled_at)
        : Number.POSITIVE_INFINITY;
      const bWhen = b.active.scheduled_at
        ? timeOf(b.active.scheduled_at)
        : Number.POSITIVE_INFINITY;
      if (aWhen !== bWhen) return aWhen < bWhen ? -1 : 1;
      return timeOf(a.active.assigned_at) - timeOf(b.active.assigned_at);
    });
  }, [agents, phone]);

  /** The active visit (if any) for each property FOR THIS CLIENT — keyed
   *  per property, since one client's properties can be split across
   *  several agents. */
  const activeByProperty = useMemo(() => {
    const map = new Map<string, ActiveVisit>();
    for (const visit of activeVisits) {
      if (!map.has(visit.active.property_record_id))
        map.set(visit.active.property_record_id, visit);
    }
    return map;
  }, [activeVisits]);

  /** Which agent (if any) is actively handling each property for this
   *  client. */
  const assignedAgentByProperty = useMemo(() => {
    const map = new Map<string, AgentSummary>();
    for (const [recordId, visit] of activeByProperty)
      map.set(recordId, visit.agent);
    return map;
  }, [activeByProperty]);

  const agentsById = useMemo(
    () => new Map((agents ?? []).map((agent) => [agent.agent_id, agent])),
    [agents],
  );

  const manualIdSet = useMemo(
    () => new Set(manualPropertyIds ?? []),
    [manualPropertyIds],
  );
  const websiteIdSet = useMemo(
    () => new Set(websitePropertyIds),
    [websitePropertyIds],
  );

  /** Every completed visit, grouped per property and ordered oldest-first
   *  within each group — so a property visited twice (a first visit, then a
   *  re-visit) is ONE card listing visit 1 and visit 2, never two cards.
   *  Everything downstream (the exclusion below, the Completed section,
   *  the header badge, the re-visit numbering) reads off this map, so there
   *  is exactly one place that decides what "N completed" counts. */
  const completedByProperty = useMemo(() => {
    const map = new Map<string, VisitRecord[]>();
    for (const visit of completedVisits ?? []) {
      if (!visit.property_record_id) continue; // pre-existing rows with nothing to attribute this to
      const group = map.get(visit.property_record_id);
      if (group) group.push(visit);
      else map.set(visit.property_record_id, [visit]);
    }
    for (const group of map.values())
      group.sort((a, b) => timeOf(a.completed_at) - timeOf(b.completed_at));
    return map;
  }, [completedVisits]);

  /** 2 for a property visited once already, 3 after two visits…; null for
   *  a property this client has never completed a visit to. */
  const revisitNumberFor = useCallback(
    (recordId: string): number | null => {
      const visits = completedByProperty.get(recordId);
      return visits && visits.length > 0 ? visits.length + 1 : null;
    },
    [completedByProperty],
  );

  /** A property's display title from whatever is freshest, falling back to
   *  a label a visit already snapshotted. */
  const titleFor = useCallback(
    (recordId: string, fallback: string | null): string => {
      const source =
        propertiesById.get(recordId) ??
        builderProjectsById.get(recordId) ??
        matchById.get(recordId);
      return (
        (source && (source.society_name || source.property_type)) ||
        fallback ||
        "Property"
      );
    },
    [propertiesById, builderProjectsById, matchById],
  );

  /** Every card this dialog can show, already sectioned and categorised.
   *  A manually-added property that ALSO scored keeps its score badge but
   *  is filed under Manually added — it is there because someone put it
   *  there, and that is the more useful fact. A property with a completed
   *  visit is excluded entirely: it has already been shown to the client
   *  and visited, so it is no longer an outstanding match — it moves to
   *  the Completed view instead (see completedByProperty above). */
  const items = useMemo<DialogItem[]>(() => {
    const byId = new Map<string, DialogItem>();

    const pushMatch = (match: MatchedProperty, bucket: MatchBucket) => {
      if (completedByProperty.has(match.record_id)) return;
      const source: PropertySource = match.property_source ?? "property";
      const property =
        source === "property"
          ? (propertiesById.get(match.record_id) ?? null)
          : null;
      byId.set(match.record_id, {
        recordId: match.record_id,
        section: manualIdSet.has(match.record_id) ? "manual" : bucket,
        // A builder project is always "accepted", so it files under Main.
        category: categoryOf(property ?? match),
        source,
        match,
        property,
        handoff: property ?? match,
        fromWebsiteInquiry: websiteIdSet.has(match.record_id),
      });
    };
    for (const match of result?.high ?? []) pushMatch(match, "high");
    for (const match of result?.medium ?? []) pushMatch(match, "medium");
    for (const match of result?.low ?? []) pushMatch(match, "low");

    for (const recordId of manualPropertyIds ?? []) {
      if (byId.has(recordId) || completedByProperty.has(recordId)) continue;
      const property = propertiesById.get(recordId);
      if (!property) continue; // deleted since it was picked — skip, don't error
      byId.set(recordId, {
        recordId,
        section: "manual",
        category: categoryOf(property),
        source: "property",
        match: null,
        property,
        handoff: property,
        fromWebsiteInquiry: websiteIdSet.has(recordId),
      });
    }

    // Website-enquired properties with no other home yet — not scored
    // (their own requirements may be for something else entirely, or
    // haven't been derived from this specific listing) and not hand-picked
    // by staff. Their ONLY listing is the Web Site Property Inquiry
    // section itself (section: "website", deliberately absent from
    // SECTION_ORDER — see that array's own comment), so they never appear
    // twice for a reason that isn't true: they were never actually filed
    // under a real bucket or Manually added.
    for (const recordId of websiteIdSet) {
      if (byId.has(recordId) || completedByProperty.has(recordId)) continue;
      const property = propertiesById.get(recordId);
      if (!property) continue; // deleted since the enquiry came in
      byId.set(recordId, {
        recordId,
        section: "website",
        category: categoryOf(property),
        source: "property",
        match: null,
        property,
        handoff: property,
        fromWebsiteInquiry: true,
      });
    }

    return [...byId.values()];
  }, [
    result,
    manualPropertyIds,
    manualIdSet,
    propertiesById,
    completedByProperty,
    websiteIdSet,
  ]);

  const itemsById = useMemo(
    () => new Map(items.map((item) => [item.recordId, item])),
    [items],
  );

  /** Property or builder project, for any id this dialog can show: the
   *  live match first, then the snapshot an active or completed visit took
   *  when it was handed off (right even after the listing is deleted), and
   *  only then the loaded Builder Projects list. */
  const sourceFor = useCallback(
    (recordId: string): PropertySource => {
      const item = itemsById.get(recordId);
      if (item) return item.source;
      const active = activeByProperty.get(recordId);
      if (active) return active.active.property_source ?? "property";
      const visits = completedByProperty.get(recordId);
      if (visits && visits.length > 0)
        return visits[visits.length - 1].property_source ?? "property";
      return builderProjectsById.has(recordId) ? "builder_project" : "property";
    },
    [itemsById, activeByProperty, completedByProperty, builderProjectsById],
  );

  // A ticked property that has since dropped off the list (moved, removed,
  // sold) can't be planned any more — close its planner rather than leave
  // an invisible layer swallowing Escape.
  useEffect(() => {
    if (planner?.kind === "assign" && !itemsById.has(planner.recordId))
      setPlanner(null);
  }, [planner, itemsById]);

  const countByCategory = useMemo(() => {
    const counts: Record<PropertyCategory, number> = { main: 0, outsider: 0 };
    for (const item of items) counts[item.category] += 1;
    return counts;
  }, [items]);

  const inDrawer = category === "main" || category === "outsider";

  const visibleItems = useMemo(
    () => items.filter((item) => item.category === category),
    [items, category],
  );

  /** The client's property types when they asked for more than one
   *  ("Flat, Bungalow") — each gets a tab of its own under the
   *  Main/Outsider row. The backend tags every scored match with the type
   *  it matched (MatchedProperty.matched_type), so a tab is an exact split,
   *  not a guess made here. */
  const clientTypes = useMemo(
    () => splitClientTypes(client?.property_type ?? null),
    [client],
  );
  const showTypeTabs = clientTypes.length > 1 && category !== "completed";
  const typeFilter =
    showTypeTabs &&
    typeView !== null &&
    clientTypes.some((type) => sameType(type, typeView))
      ? typeView
      : null;

  /** Only a scored match knows which type it was for. A hand-picked or
   *  website-enquired property that never scored (or a match cached before
   *  types were tracked) has no type to be filed under, so it stays on
   *  every type tab rather than vanishing from all of them. */
  const typeVisibleItems = useMemo(
    () =>
      typeFilter === null
        ? visibleItems
        : visibleItems.filter(
            (item) =>
              !item.match?.matched_type ||
              sameType(item.match.matched_type, typeFilter),
          ),
    [visibleItems, typeFilter],
  );

  const countByType = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of visibleItems) {
      const type = item.match?.matched_type?.trim().toLowerCase();
      if (type) counts.set(type, (counts.get(type) ?? 0) + 1);
    }
    return counts;
  }, [visibleItems]);

  const sections = useMemo(
    () =>
      SECTION_ORDER.map((key) => ({
        key,
        items: typeVisibleItems
          .filter((item) => item.section === key)
          .sort((a, b) => (b.match?.score ?? 0) - (a.match?.score ?? 0)),
      })).filter((section) => section.items.length > 0),
    [typeVisibleItems],
  );

  // Every card that came from a website enquiry, regardless of its own
  // PRIMARY section — this is what makes a High-matching website enquiry
  // show up a second time here, right where it scored AND under this
  // heading, rather than one or the other. Rendered as its own block,
  // positioned right after Manually added (see the JSX below), never as
  // one of the sections above.
  const websiteItems = useMemo(
    () =>
      typeVisibleItems
        .filter((item) => item.fromWebsiteInquiry)
        .sort((a, b) => (b.match?.score ?? 0) - (a.match?.score ?? 0)),
    [typeVisibleItems],
  );
  const manualSection =
    sections.find((section) => section.key === "manual") ?? null;
  const bucketSections = sections.filter((section) => section.key !== "manual");

  const openItem = useMemo(
    () => items.find((item) => item.recordId === openItemId) ?? null,
    [items, openItemId],
  );

  /** Ticked cards that can actually be handed off — an already-assigned
   *  property is never sent a second time (its checkbox is hidden for the
   *  same reason), even if it was ticked a moment before its assignment
   *  arrived from another screen. */
  const assignableItems = useMemo(
    () =>
      items.filter(
        (item) =>
          selectedIds.has(item.recordId) &&
          !assignedAgentByProperty.has(item.recordId),
      ),
    [items, selectedIds, assignedAgentByProperty],
  );

  /** The same ticked cards the assignment flow uses, in the shape a
   *  WhatsApp share message needs. Built from `items` (not from
   *  selectedIds directly) so a property that has dropped off the list can
   *  never end up in a message — exactly the guarantee assignableItems
   *  above gives the hand-off. */
  const selectedShareProperties = useMemo<SharePropertyLike[]>(
    () =>
      items
        .filter((item) => selectedIds.has(item.recordId))
        .map((item) => item.handoff),
    [items, selectedIds],
  );

  /** Hand-picked properties whose full record hasn't arrived yet — they
   *  have no match record to fall back on, so they simply aren't
   *  renderable until the property list lands. Counted so the wait is
   *  stated rather than looking like the list is just short. */
  const pendingManualCount = useMemo(() => {
    if (loadingMeta || !loadingProperties) return 0;
    return (manualPropertyIds ?? []).filter((id) => !propertiesById.has(id))
      .length;
  }, [loadingMeta, loadingProperties, manualPropertyIds, propertiesById]);

  const assignedCount = useMemo(
    () =>
      items.filter((item) => assignedAgentByProperty.has(item.recordId)).length,
    [items, assignedAgentByProperty],
  );

  /** One card per property, the property whose latest visit is newest
   *  first — see completedByProperty's own comment for the grouping. */
  const completedList = useMemo(
    () =>
      [...completedByProperty.entries()]
        .map(([recordId, visits]) => ({ recordId, visits }))
        .sort(
          (a, b) =>
            timeOf(b.visits[b.visits.length - 1].completed_at) -
            timeOf(a.visits[a.visits.length - 1].completed_at),
        ),
    [completedByProperty],
  );

  const firstVisits = useMemo(
    () =>
      activeVisits.filter(
        (visit) => revisitNumberFor(visit.active.property_record_id) === null,
      ),
    [activeVisits, revisitNumberFor],
  );
  const reVisits = useMemo(
    () =>
      activeVisits.filter(
        (visit) => revisitNumberFor(visit.active.property_record_id) !== null,
      ),
    [activeVisits, revisitNumberFor],
  );

  const displayName = client?.name || clientName || phone;

  function clearSelection() {
    setSelectedIds(new Set());
    setPlans({});
  }

  /** Ticking a card selects it AND opens the small visit planner for it
   *  (agent, then an optional time). Closing the planner without choosing
   *  keeps the card ticked with no agent — it can still go out through
   *  "Send details on WhatsApp", and "Assign & send" asks for the agent
   *  when it gets to it. Unticking drops whatever was planned for it. */
  function handleToggleSelect(recordId: string) {
    if (selectedIds.has(recordId)) {
      setSelectedIds((previous) => {
        const next = new Set(previous);
        next.delete(recordId);
        return next;
      });
      setPlans((previous) => {
        if (!(recordId in previous)) return previous;
        const next = { ...previous };
        delete next[recordId];
        return next;
      });
      return;
    }
    setSelectedIds((previous) => new Set(previous).add(recordId));
    setPlanner({ kind: "assign", recordId });
  }

  /** Straight to the hand-off messages — the agents (and times) were
   *  already picked card by card. If any ticked card still lacks a usable
   *  plan (no agent, an agent since removed, or a time that has passed),
   *  its planner opens instead, one card at a time. */
  function handleAssignAndSend() {
    const now = Date.now();
    for (const item of assignableItems) {
      const plan = plans[item.recordId];
      const problem =
        !plan || !agentsById.has(plan.agentId)
          ? "doesn't have an agent yet"
          : plan.scheduledAt && timeOf(plan.scheduledAt) <= now
            ? "has a visit time that has already passed"
            : null;
      if (problem) {
        toast.push({
          tone: "info",
          title: "One more pick first",
          message: `${propertyLabel(item.handoff)} ${problem}.`,
        });
        setPlanner({ kind: "assign", recordId: item.recordId });
        return;
      }
    }

    const byAgent = new Map<string, AgentAssignment>();
    for (const item of assignableItems) {
      const plan = plans[item.recordId];
      const agent = agentsById.get(plan.agentId)!;
      let entry = byAgent.get(agent.agent_id);
      if (!entry) {
        entry = { agent, properties: [], visitMeta: {} };
        byAgent.set(agent.agent_id, entry);
      }
      entry.properties.push(item.handoff);
      entry.visitMeta![item.recordId] = {
        scheduledAt: plan.scheduledAt,
        revisitNumber: null,
      };
    }
    if (byAgent.size > 0)
      setAssignFlow({ kind: "assign", assignments: [...byAgent.values()] });
  }

  /** Puts just-handed-off visits on screen immediately (orange cards, the
   *  Assigned tab, a Completed card's "Re-visit with X"), before the
   *  confirming re-read of the agent list lands — which also means a
   *  second click can't book the same visit twice in that gap. */
  function addActiveLocally(assignments: AgentAssignment[]) {
    const assignedAt = new Date().toISOString();
    setAgents((previous) => {
      if (!previous) return previous;
      return previous.map((agent) => {
        const assignment = assignments.find(
          (a) => a.agent.agent_id === agent.agent_id,
        );
        if (!assignment) return agent;
        const additions: AssignedClientSummary[] = assignment.properties
          .filter(
            (p) =>
              !agent.active_clients.some(
                (a) =>
                  a.phone === phone && a.property_record_id === p.record_id,
              ),
          )
          .map((p) => ({
            phone,
            name: client?.name ?? null,
            budget_min_inr: client?.budget_min_inr ?? null,
            budget_max_inr: client?.budget_max_inr ?? null,
            property_record_id: p.record_id,
            property_label: propertyLabel(p),
            property_source: sourceFor(p.record_id),
            assigned_at: assignedAt,
            scheduled_at:
              assignment.visitMeta?.[p.record_id]?.scheduledAt ?? null,
          }));
        return additions.length > 0
          ? {
              ...agent,
              active_clients: [...agent.active_clients, ...additions],
            }
          : agent;
      });
    });
  }

  function handleRevisitConfirm(
    recordId: string,
    agentId: string,
    scheduledAt: string | null,
  ) {
    const agent = agentsById.get(agentId);
    // The hand-off step needs the client record to address its messages;
    // without it there would be nothing to show (the Revisit button is
    // disabled until it loads, so this is only a guard).
    if (!agent || !client) {
      setPlanner(null);
      return;
    }
    const visits = completedByProperty.get(recordId) ?? [];
    const latest = visits[visits.length - 1] ?? null;
    const property: HandoffPropertyLike =
      listingFor(recordId, sourceFor(recordId)) ??
      matchById.get(recordId) ??
      snapshotProperty(recordId, latest?.property_label ?? null);
    setPlanner(null);
    setAssignFlow({
      kind: "revisit",
      recordId,
      agentId,
      scheduledAt,
      assignments: [
        {
          agent,
          properties: [property],
          visitMeta: {
            [recordId]: { scheduledAt, revisitNumber: visits.length + 1 },
          },
        },
      ],
    });
  }

  /** The Assigned tab's time change: ONE write (see
   *  agentApi.updateVisitSchedule), the returned visit patched into the
   *  local agent list instead of re-reading it, then the optional
   *  "tell them" step. The time is saved before that step opens, so
   *  skipping it never loses the time. */
  async function handleSaveSchedule(visit: ActiveVisit, scheduledAt: string) {
    const recordId = visit.active.property_record_id;
    setSavingSchedule(true);
    try {
      const updated = await agentApi.updateVisitSchedule(visit.agent.agent_id, {
        client_phone: phone,
        property_record_id: recordId,
        scheduled_at: scheduledAt,
      });
      setAgents(
        (previous) =>
          previous?.map((agent) =>
            agent.agent_id !== visit.agent.agent_id
              ? agent
              : {
                  ...agent,
                  active_clients: agent.active_clients.map((active) =>
                    active.phone === phone &&
                    active.property_record_id === recordId
                      ? updated
                      : active,
                  ),
                },
          ) ?? previous,
      );
      setPlanner(null);
      const messages = buildVisitTimeMessages({
        clientName: client?.name || clientName || null,
        clientPhone: phone,
        agentName: visit.agent.name,
        agentPhone: visit.agent.phone,
        propertyLabel: titleFor(recordId, visit.active.property_label),
        scheduledAt,
        previousScheduledAt: visit.active.scheduled_at,
        revisitNumber: revisitNumberFor(recordId),
      });
      setTimeMessages({
        agent: visit.agent,
        scheduledAt,
        rescheduled: visit.active.scheduled_at !== null,
        agentMessage: messages.agent,
        clientMessage: messages.client,
      });
    } catch (err) {
      toast.push({
        tone: "bad",
        title: "Could not save the visit time",
        message: friendlyError(err),
      });
      // Gone meanwhile (completed or cleared from another screen) — drop
      // the stale card rather than leave a planner pointing at nothing.
      if (err instanceof ApiError && err.status === 404) {
        setPlanner(null);
        reloadAgents();
      }
    } finally {
      setSavingSchedule(false);
    }
  }

  async function handleRefresh() {
    setRecomputing(true);
    try {
      const fresh = await matchingApi.recompute(phone);
      setResult(fresh);
      setCachedMatchResult(phone, fresh);
      setError(null);
      toast.push({ tone: "ok", title: "Matches refreshed" });
      onChanged?.();
    } catch (err) {
      toast.push({
        tone: "bad",
        title: "Refresh failed",
        message: friendlyError(err),
      });
    } finally {
      setRecomputing(false);
    }
  }

  /** Move one property between Main and Outsider. Patched
   *  locally rather than re-running the whole matching pipeline: a
   *  property changing drawers changes nothing about how well it fits
   *  this client, only where it is filed. */
  async function handleMove(item: DialogItem, target: PropertyCategory) {
    // Main/Outsider is a property's filing — a builder project has neither
    // (the detail view offers no move for one; this only guards it).
    if (item.source === "builder_project") return;
    setMovingId(item.recordId);
    try {
      const updated = await propertyApi.updateProperty(
        item.recordId,
        categoryPatch(target),
      );
      setProperties((previous) => {
        if (previous === null) return [updated];
        const index = previous.findIndex(
          (p) => p.record_id === updated.record_id,
        );
        if (index === -1) return [...previous, updated];
        const next = [...previous];
        next[index] = updated;
        return next;
      });
      patchCachedProperty(updated.record_id, updated);
      // Keep the match's own copy of these two fields in step, so a card
      // whose full record never loaded still moves with everything else —
      // and keep the cached match result in step too, so a quick close/
      // reopen of this same dialog never flashes the pre-move category
      // before the guaranteed background refetch corrects it.
      setResult((previous) => {
        if (!previous) return previous;
        const patched = patchMatchFields(previous, updated);
        setCachedMatchResult(phone, patched);
        return patched;
      });
      setCategory(target);
      toast.push({
        tone: "ok",
        title: `Moved to ${CATEGORY_LABEL[target]}`,
        message: updated.society_name ?? updated.area_name ?? undefined,
      });
      onChanged?.();
    } catch (err) {
      toast.push({
        tone: "bad",
        title: "Could not move property",
        message: friendlyError(err),
      });
    } finally {
      setMovingId(null);
    }
  }

  /**
   * Calls off every site visit currently out with an agent for this client
   * (re-visits included) and messages each agent involved once — the
   * backend does both (see whatsapp_inquiry_controller.clear_assignments).
   *
   * Only ACTIVE assignments go. Completed visits are permanent history and
   * stay exactly where they are, which is the whole difference between
   * cancelling a visit and having done one.
   */
  async function handleClearAssignments() {
    setClearing(true);
    try {
      const result = await inquiryClientApi.clearAssignments(phone);
      setClearOpen(false);
      toast.push({
        tone: result.agents_failed > 0 ? "warn" : "ok",
        title: result.cleared > 0 ? "Assignments cleared" : "Nothing to clear",
        message:
          result.cleared > 0
            ? `${result.cleared} site visit${result.cleared === 1 ? "" : "s"} cancelled. ${result.agents_notified} agent${result.agents_notified === 1 ? "" : "s"} notified${result.agents_failed > 0 ? `, ${result.agents_failed} could not be reached` : ""}.`
            : "This client had no active site visits.",
      });
      onChanged?.();
      // Every card drops its "Assigned to X" badge immediately; the
      // re-read of the agent list confirms it. Nothing else changed, so
      // nothing else is re-read.
      setAgents(
        (previous) =>
          previous?.map((agent) =>
            agent.active_clients.some((active) => active.phone === phone)
              ? {
                  ...agent,
                  active_clients: agent.active_clients.filter(
                    (active) => active.phone !== phone,
                  ),
                }
              : agent,
          ) ?? previous,
      );
      if (category === "assigned") setCategory("main");
      reloadAgents();
    } catch (err) {
      toast.push({
        tone: "bad",
        title: "Could not clear the assignments",
        message: friendlyError(err),
      });
    } finally {
      setClearing(false);
    }
  }

  async function handleRemoveManual(recordId: string) {
    setRemovingManualId(recordId);
    try {
      setManualPropertyIds(
        await inquiryClientApi.removeManualProperty(phone, recordId),
      );
      setSelectedIds((previous) => {
        if (!previous.has(recordId)) return previous;
        const next = new Set(previous);
        next.delete(recordId);
        return next;
      });
      setPlans((previous) => {
        if (!(recordId in previous)) return previous;
        const next = { ...previous };
        delete next[recordId];
        return next;
      });
      setOpenItemId((previous) => (previous === recordId ? null : previous));
      onChanged?.();
    } catch (err) {
      toast.push({
        tone: "bad",
        title: "Could not remove this property",
        message: friendlyError(err),
      });
    } finally {
      setRemovingManualId(null);
    }
  }

  const total = items.length;

  // Composite flags matching the section each actually feeds — see the
  // four loading* state declarations' own comment for why they're split.
  // The Assigned tab needs no flag: it only exists once the agent list it
  // is built from has arrived, and its cards fall back to the visit's own
  // snapshot while property records load.
  const mainSectionLoading = loadingMatches || loadingMeta || loadingProperties;
  const completedSectionLoading =
    loadingCompleted || loadingMeta || loadingProperties;
  const sectionLoading =
    category === "completed"
      ? completedSectionLoading
      : category === "assigned"
        ? false
        : mainSectionLoading;
  const allSettled =
    !loadingMatches && !loadingCompleted && !loadingMeta && !loadingProperties;

  const renderMatchCard = (item: DialogItem) => {
    const plan = plans[item.recordId];
    const planAgent = plan ? (agentsById.get(plan.agentId) ?? null) : null;
    return (
      <PropertyMatchCard
        key={item.recordId}
        item={item}
        selected={selectedIds.has(item.recordId)}
        onToggleSelect={() => handleToggleSelect(item.recordId)}
        onOpen={() => setOpenItemId(item.recordId)}
        assigned={activeByProperty.get(item.recordId) ?? null}
        plan={
          planAgent
            ? { agentName: planAgent.name, scheduledAt: plan.scheduledAt }
            : null
        }
        onPlan={() => setPlanner({ kind: "assign", recordId: item.recordId })}
      />
    );
  };

  const renderActiveCard = (visit: ActiveVisit) => {
    const recordId = visit.active.property_record_id;
    const source = sourceFor(recordId);
    return (
      <AssignedVisitCard
        key={`${visit.agent.agent_id}-${recordId}`}
        visit={visit}
        source={source}
        property={listingFor(recordId, source)}
        match={matchById.get(recordId) ?? null}
        revisitNumber={revisitNumberFor(recordId)}
        onOpen={() =>
          itemsById.has(recordId)
            ? setOpenItemId(recordId)
            : setViewingListing({ recordId, source })
        }
        onEditTime={() => setPlanner({ kind: "reschedule", visit })}
      />
    );
  };

  const plannerItem =
    planner?.kind === "assign"
      ? (itemsById.get(planner.recordId) ?? null)
      : null;

  return createPortal(
    <>
      <div
        className="modal-scrim"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget && !nestedOpen) onClose();
        }}
      >
        <div
          className="detail-modal detail-modal--wide matches-dialog anim-rise"
          role="dialog"
          aria-modal="true"
          aria-label={`Properties matched for ${displayName}`}
        >
          {/* Compact header (see .matches-dialog in app.css): the badges sit on
              the title's own line instead of a row of their own, so the cards
              below get that height back. Same layout as
              RequirementMatchesDialog. */}
          <div className="detail-modal__head">
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="detail-modal__eyebrow">Matched properties</div>
              <div className="matches-dialog__title-row">
                <h2 className="detail-modal__title cell-truncate">
                  {client?.name || clientName || "Matches"}
                </h2>
                <div className="detail-modal__badges">
                  <Badge tone="accent">
                    {total} propert{total === 1 ? "y" : "ies"}
                  </Badge>
                  {assignedCount > 0 && (
                    <Badge tone="orange">
                      {assignedCount} assigned
                      {assignedCount < total
                        ? ` · ${total - assignedCount} remaining`
                        : ""}
                    </Badge>
                  )}
                  {result?.computed_at && (
                    <span className="fact">
                      <IconClock size={12} /> Computed{" "}
                      {relativeTime(new Date(result.computed_at))}
                    </span>
                  )}
                </div>
              </div>
              <div className="detail-modal__sub cell-truncate">
                {summariseRequirements(client, phone)}
              </div>
            </div>
            <div className="row-flex" style={{ gap: 8, flex: "none" }}>
              <Button
                size="sm"
                variant="ghost"
                icon={<IconRefresh size={14} />}
                onClick={handleRefresh}
                busy={recomputing}
              >
                Refresh
              </Button>
              <button
                type="button"
                className="toast__close"
                onClick={onClose}
                aria-label="Close"
              >
                <IconX size={15} />
              </button>
            </div>
          </div>

          {/* The client's own description of what they want — pinned above
              the tabs exactly like the Broker Requirements matches dialog's,
              so it stays readable against every card on every tab. */}
          {client?.additional_requirements?.trim() && (
            <div
              className="matches-dialog__brief"
              role="note"
              aria-label="Requirement description"
            >
              <div className="matches-dialog__brief-k">
                <IconMessage size={12} /> Requirement description
              </div>
              <div className="matches-dialog__brief-v">
                {client.additional_requirements.trim()}
              </div>
            </div>
          )}

          <div className="matches-dialog__tabs">
            <Segmented<PropertyCategory>
              ariaLabel="Which properties to show"
              value={inDrawer ? (category as PropertyCategory) : null}
              onChange={setCategory}
              options={(["main", "outsider"] as PropertyCategory[]).map(
                (key) => ({
                  value: key,
                  label: `${CATEGORY_LABEL[key]}${countByCategory[key] ? ` (${countByCategory[key]})` : ""}`,
                }),
              )}
            />
            {activeVisits.length > 0 && (
              <button
                type="button"
                className={`pill-orange${category === "assigned" ? " pill-orange--active" : ""}`}
                onClick={() =>
                  setCategory(category === "assigned" ? "main" : "assigned")
                }
                aria-pressed={category === "assigned"}
              >
                <IconUserCheck size={12} strokeWidth={2.2} />
                {activeVisits.length} assigned
              </button>
            )}
            {completedList.length > 0 && (
              <button
                type="button"
                className={`pill-ok${category === "completed" ? " pill-ok--active" : ""}`}
                onClick={() =>
                  setCategory(category === "completed" ? "main" : "completed")
                }
                aria-pressed={category === "completed"}
              >
                <IconCheck size={12} strokeWidth={2.4} />
                {completedList.length} completed
              </button>
            )}
            {selectedIds.size > 0 && (
              <span className="faint small" style={{ marginLeft: "auto" }}>
                {selectedIds.size} selected
              </span>
            )}
          </div>

          {showTypeTabs && (
            <div className="matches-dialog__tabs matches-dialog__tabs--types">
              <span
                className="section-head__eyebrow"
                style={{ marginBottom: 0 }}
              >
                Property type
              </span>
              <Segmented<string>
                ariaLabel="Which of the client's property types to show"
                value={typeFilter ?? ALL_TYPES}
                onChange={(value) =>
                  setTypeView(value === ALL_TYPES ? null : value)
                }
                options={[
                  {
                    value: ALL_TYPES,
                    label: `All${visibleItems.length ? ` (${visibleItems.length})` : ""}`,
                  },
                  ...clientTypes.map((type) => {
                    const count = countByType.get(type.toLowerCase()) ?? 0;
                    return {
                      value: type,
                      label: `${type}${count ? ` (${count})` : ""}`,
                    };
                  }),
                ]}
              />
            </div>
          )}

          <div className="detail-modal__body">
            {error && (
              <Note tone="bad" icon={<IconAlert size={16} />}>
                {error}
              </Note>
            )}

            {sectionLoading && <SkeletonRows rows={5} />}

            {allSettled &&
              result &&
              !result.has_requirements &&
              total === 0 &&
              completedList.length === 0 && (
                <EmptyState
                  icon={<IconInbox size={36} />}
                  title="No requirements yet"
                  body="This client hasn't submitted their property requirements (purpose, type, budget, area) yet — matches appear here automatically once they do. You can still add a property by hand."
                />
              )}

            {!mainSectionLoading &&
              inDrawer &&
              (result?.has_requirements ||
                total > 0 ||
                completedList.length > 0) &&
              sections.length === 0 &&
              websiteItems.length === 0 && (
                <EmptyState
                  icon={<IconInbox size={36} />}
                  title={
                    typeFilter
                      ? `No ${typeFilter} matches in ${CATEGORY_LABEL[category]}`
                      : `Nothing in ${CATEGORY_LABEL[category as PropertyCategory]}`
                  }
                  body={
                    typeFilter
                      ? `Nothing here matched this client's ${typeFilter} requirement yet — try their other property type above, or the other tab.`
                      : total > 0
                        ? "Every property found for this client is filed under one of the other tabs above."
                        : "No stored property matched this client's requirements yet. Add one by hand, or refresh the matches."
                  }
                />
              )}

            {/* ---- Assigned: every visit out with an agent ---- */}
            {category === "assigned" && activeVisits.length === 0 && (
              <EmptyState
                icon={<IconInbox size={36} />}
                title="No assigned visits"
                body="Nothing is out with an agent for this client right now. Tick a property under Main or Outsider to assign one."
              />
            )}
            {category === "assigned" && firstVisits.length > 0 && (
              <div className="stack stack-3">
                <div className="matches-dialog__section-head">
                  <span
                    className="section-head__eyebrow"
                    style={{ marginBottom: 0 }}
                  >
                    Site visits
                  </span>
                  <span className="matches-dialog__section-count">
                    {firstVisits.length}
                  </span>
                  <span className="matches-dialog__rule" />
                </div>
                <div className="matches-grid">
                  {firstVisits.map(renderActiveCard)}
                </div>
              </div>
            )}
            {category === "assigned" && reVisits.length > 0 && (
              <div className="stack stack-3">
                <div className="matches-dialog__section-head">
                  <span
                    className="section-head__eyebrow"
                    style={{ marginBottom: 0 }}
                  >
                    Re-visits
                  </span>
                  <span className="matches-dialog__section-count">
                    {reVisits.length}
                  </span>
                  <span className="matches-dialog__rule" />
                </div>
                <div className="matches-grid">
                  {reVisits.map(renderActiveCard)}
                </div>
              </div>
            )}

            {/* ---- Completed: one card per property, every visit listed ---- */}
            {!completedSectionLoading &&
              category === "completed" &&
              completedList.length === 0 && (
                <EmptyState
                  icon={<IconInbox size={36} />}
                  title="No completed visits yet"
                  body="Once a visit for one of this client's properties is marked complete on the Agents page, it moves here."
                />
              )}

            {!completedSectionLoading &&
              category === "completed" &&
              completedList.length > 0 && (
                <div className="stack stack-3">
                  <div className="matches-dialog__section-head">
                    <span
                      className="section-head__eyebrow"
                      style={{ marginBottom: 0 }}
                    >
                      Completed
                    </span>
                    <span className="matches-dialog__section-count">
                      {completedList.length}
                    </span>
                    <span className="matches-dialog__rule" />
                  </div>
                  <div className="matches-grid">
                    {completedList.map(({ recordId, visits }) => (
                      <CompletedPropertyCard
                        key={recordId}
                        visits={visits}
                        source={sourceFor(recordId)}
                        property={listingFor(recordId, sourceFor(recordId))}
                        onOpen={() =>
                          setViewingListing({
                            recordId,
                            source: sourceFor(recordId),
                          })
                        }
                        activeVisit={activeByProperty.get(recordId) ?? null}
                        canRevisit={client !== null}
                        onRevisit={() =>
                          setPlanner({
                            kind: "revisit",
                            recordId,
                            agentId: null,
                            scheduledAt: null,
                          })
                        }
                      />
                    ))}
                  </div>
                </div>
              )}

            {/* ---- Main / Outsider ---- */}
            {!mainSectionLoading && pendingManualCount > 0 && inDrawer && (
              <Note tone="info" icon={<IconClock size={16} />}>
                Loading {pendingManualCount} hand-picked propert
                {pendingManualCount === 1 ? "y" : "ies"}…
              </Note>
            )}

            {!mainSectionLoading && inDrawer && manualSection && (
              <div className="stack stack-3">
                <div className="matches-dialog__section-head">
                  <span
                    className="section-head__eyebrow"
                    style={{ marginBottom: 0 }}
                  >
                    {SECTION_LABEL[manualSection.key]}
                  </span>
                  <span className="matches-dialog__section-count">
                    {manualSection.items.length}
                  </span>
                  <span className="matches-dialog__rule" />
                </div>
                <div className="matches-grid">
                  {manualSection.items.map(renderMatchCard)}
                </div>
              </div>
            )}

            {/* Right after Manually added, per the feature's own ask —
                every card here also appears in its own native section
                above (High/Medium/Low/Manually added) when it has one;
                the only ones that DON'T are website enquiries that never
                scored or got hand-picked, whose only home is here. */}
            {!mainSectionLoading && inDrawer && websiteItems.length > 0 && (
              <div className="stack stack-3">
                <div className="matches-dialog__section-head">
                  <span
                    className="section-head__eyebrow"
                    style={{ marginBottom: 0 }}
                  >
                    {SECTION_LABEL.website}
                  </span>
                  <span className="matches-dialog__section-count">
                    {websiteItems.length}
                  </span>
                  <span className="matches-dialog__rule" />
                </div>
                <div className="matches-grid">
                  {websiteItems.map(renderMatchCard)}
                </div>
              </div>
            )}

            {!mainSectionLoading &&
              inDrawer &&
              bucketSections.map((section) => (
                <div key={section.key} className="stack stack-3">
                  <div className="matches-dialog__section-head">
                    <span
                      className="section-head__eyebrow"
                      style={{ marginBottom: 0 }}
                    >
                      {SECTION_LABEL[section.key]}
                    </span>
                    <span className="matches-dialog__section-count">
                      {section.items.length}
                    </span>
                    <span className="matches-dialog__rule" />
                  </div>
                  <div className="matches-grid">
                    {section.items.map(renderMatchCard)}
                  </div>
                </div>
              ))}
          </div>

          <div className="detail-modal__foot">
            <Button
              icon={<IconPlus size={15} />}
              onClick={() =>
                navigate(
                  `/select-property?forClient=${encodeURIComponent(phone)}&clientName=${encodeURIComponent(
                    client?.name || clientName || phone,
                  )}&from=inquiries`,
                )
              }
            >
              Add property
            </Button>
            {/* Only offered when there is something to call off — a
                button that can only ever say "nothing to clear" is noise.
                Counts every active visit, re-visits included. */}
            {activeVisits.length > 0 && (
              <Button
                variant="ghost"
                icon={<IconTrash size={15} />}
                onClick={() => setClearOpen(true)}
                busy={clearing}
              >
                Clear assignments ({activeVisits.length})
              </Button>
            )}
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
            <span className="row-flex" style={{ marginLeft: "auto", gap: 10 }}>
              {/* Two different errands, side by side rather than one behind
                  the other: "send this client the shortlist themselves" is
                  not a lesser version of "put an agent on it", and forcing
                  it through the agent picker would record visits nobody
                  arranged. */}
              <Button
                icon={<IconSend size={15} />}
                onClick={() => setShareOpen(true)}
                disabled={!client || selectedIds.size === 0}
              >
                Send details on WhatsApp ({selectedIds.size})
              </Button>
              <Button
                variant="primary"
                icon={<IconUserCheck size={15} />}
                onClick={handleAssignAndSend}
                disabled={!client || assignableItems.length === 0}
              >
                Assign &amp; send ({assignableItems.length})
              </Button>
            </span>
          </div>
        </div>
      </div>

      {openItem && (
        <PropertyMatchDetailDialog
          item={openItem}
          assignedAgent={assignedAgentByProperty.get(openItem.recordId) ?? null}
          moving={movingId === openItem.recordId}
          removing={removingManualId === openItem.recordId}
          selected={selectedIds.has(openItem.recordId)}
          onToggleSelect={() => {
            // Ticking from here hands straight over to the planner (which
            // closes this view, so the two never stack); unticking just
            // unticks.
            const wasSelected = selectedIds.has(openItem.recordId);
            handleToggleSelect(openItem.recordId);
            if (!wasSelected) setOpenItemId(null);
          }}
          onMove={(target) => handleMove(openItem, target)}
          onRemoveManual={
            openItem.section === "manual"
              ? () => handleRemoveManual(openItem.recordId)
              : undefined
          }
          onClose={() => setOpenItemId(null)}
        />
      )}

      {viewingListing && (
        <PropertyReadOnlyDialog
          recordId={viewingListing.recordId}
          source={viewingListing.source}
          onClose={() => setViewingListing(null)}
        />
      )}

      {clearOpen && (
        <ConfirmDialog
          title="Cancel every site visit for this client?"
          confirmLabel="Clear assignments"
          tone="danger"
          busy={clearing}
          onConfirm={handleClearAssignments}
          onClose={() => !clearing && setClearOpen(false)}
          body={
            <div className="stack stack-3">
              <p className="section-head__sub" style={{ margin: 0 }}>
                {activeVisits.length} site visit
                {activeVisits.length === 1 ? " is" : "s are"} currently out with
                an agent for <strong>{displayName}</strong>. Clearing removes{" "}
                {activeVisits.length === 1
                  ? "that assignment"
                  : "all of those assignments"}{" "}
                and messages every agent involved on WhatsApp to say the visits
                are cancelled.
              </p>
              <p className="section-head__sub" style={{ margin: 0 }}>
                The properties themselves stay on this list, ready to be
                assigned again.{" "}
                <strong>Completed visits are not affected.</strong>
              </p>
            </div>
          }
        />
      )}

      {shareOpen && client && (
        <ShareClientPropertiesDialog
          client={client}
          properties={selectedShareProperties}
          onClose={() => setShareOpen(false)}
          onBack={() => setShareOpen(false)}
          // Only the selection is cleared. Nothing about the client's
          // matches, assignments or counts changed by sending them a
          // message, so there is deliberately no onChanged() or reload
          // here — see handleClearAssignments for what a state-changing
          // action does instead.
          onSent={clearSelection}
        />
      )}

      {plannerItem && (
        <VisitPlannerDialog
          key={`assign-${plannerItem.recordId}`}
          mode="assign"
          clientName={displayName}
          propertyLabel={propertyLabel(plannerItem.handoff)}
          propertyArea={plannerItem.handoff.area_name}
          wantedAreas={client?.preferred_areas ?? null}
          agents={agents}
          initialAgentId={plans[plannerItem.recordId]?.agentId ?? null}
          initialScheduledAt={plans[plannerItem.recordId]?.scheduledAt ?? null}
          revisitNumber={null}
          onConfirm={(agentId, scheduledAt) => {
            setPlans((previous) => ({
              ...previous,
              [plannerItem.recordId]: { agentId, scheduledAt },
            }));
            setPlanner(null);
          }}
          onClose={() => setPlanner(null)}
        />
      )}

      {planner?.kind === "revisit" && (
        <VisitPlannerDialog
          key={`revisit-${planner.recordId}`}
          mode="revisit"
          clientName={displayName}
          propertyLabel={titleFor(
            planner.recordId,
            completedByProperty.get(planner.recordId)?.[0]?.property_label ??
              null,
          )}
          propertyArea={
            (
              listingFor(planner.recordId, sourceFor(planner.recordId)) ??
              matchById.get(planner.recordId)
            )?.area_name ?? null
          }
          wantedAreas={client?.preferred_areas ?? null}
          agents={agents}
          initialAgentId={planner.agentId}
          initialScheduledAt={planner.scheduledAt}
          revisitNumber={revisitNumberFor(planner.recordId)}
          onConfirm={(agentId, scheduledAt) =>
            handleRevisitConfirm(planner.recordId, agentId, scheduledAt)
          }
          onClose={() => setPlanner(null)}
        />
      )}

      {planner?.kind === "reschedule" && (
        <VisitPlannerDialog
          key={`reschedule-${planner.visit.agent.agent_id}-${planner.visit.active.property_record_id}`}
          mode="reschedule"
          clientName={displayName}
          propertyLabel={titleFor(
            planner.visit.active.property_record_id,
            planner.visit.active.property_label,
          )}
          propertyArea={null}
          wantedAreas={null}
          agents={[planner.visit.agent]}
          initialAgentId={planner.visit.agent.agent_id}
          initialScheduledAt={planner.visit.active.scheduled_at}
          revisitNumber={revisitNumberFor(
            planner.visit.active.property_record_id,
          )}
          busy={savingSchedule}
          onConfirm={(_agentId, scheduledAt) => {
            if (scheduledAt)
              void handleSaveSchedule(planner.visit, scheduledAt);
          }}
          onClose={() => !savingSchedule && setPlanner(null)}
        />
      )}

      {timeMessages && (
        <VisitMessagesDialog
          clientPhone={phone}
          clientName={client?.name || clientName || null}
          agentName={timeMessages.agent.name}
          agentPhone={timeMessages.agent.phone}
          rescheduled={timeMessages.rescheduled}
          whenLabel={formatVisitTime(timeMessages.scheduledAt)}
          agentMessage={timeMessages.agentMessage}
          clientMessage={timeMessages.clientMessage}
          onClose={() => setTimeMessages(null)}
        />
      )}

      {assignFlow && client && (
        <HandoffDialog
          client={client}
          assignments={assignFlow.assignments}
          revisit={assignFlow.kind === "revisit"}
          onClose={() => setAssignFlow(null)}
          onPickDifferentAgent={() => {
            // A normal hand-off goes back to the cards, whose agents and
            // times are edited in place; a re-visit reopens its planner
            // with what was picked.
            if (assignFlow.kind === "revisit") {
              setPlanner({
                kind: "revisit",
                recordId: assignFlow.recordId,
                agentId: assignFlow.agentId,
                scheduledAt: assignFlow.scheduledAt,
              });
            }
            setAssignFlow(null);
          }}
          onSent={(updated) => {
            setClient(updated);
            addActiveLocally(assignFlow.assignments);
            if (assignFlow.kind === "assign") {
              // Outstanding properties this hand-off put out with an agent
              // for the first time — re-sending one that was already
              // assigned doesn't raise the count, same as the server's own
              // set-based MatchCounts.assigned. A re-visit is never an
              // outstanding property (it's already completed), so it never
              // moves the Inquiries table's counts and reports nothing.
              const sentIds = new Set(
                assignFlow.assignments.flatMap((assignment) =>
                  assignment.properties.map((p) => p.record_id),
                ),
              );
              const newlyAssigned = [...sentIds].filter(
                (id) => itemsById.has(id) && !assignedAgentByProperty.has(id),
              ).length;
              clearSelection();
              onChanged?.({ newlyAssigned });
            }
            setAssignFlow(null);
            reloadAgents();
          }}
        />
      )}
    </>,
    document.body,
  );
}

/** Rewrite the two category fields on every copy of one property inside a
 *  cached match result, leaving scores untouched. */
function patchMatchFields(
  result: ClientMatchResult,
  updated: PropertyRecord,
): ClientMatchResult {
  const patch = (list: MatchedProperty[]) =>
    list.map((match) =>
      match.record_id === updated.record_id
        ? {
            ...match,
            review_status: updated.review_status,
            needs_review: updated.needs_review,
            property_category: categoryOf(updated),
          }
        : match,
    );
  return {
    ...result,
    high: patch(result.high),
    medium: patch(result.medium),
    low: patch(result.low),
  };
}

function summariseRequirements(
  client: InquiryClientRecord | null,
  phone: string,
): string {
  if (!client) return phone;
  const parts = [
    [client.bhk, client.property_type].filter(Boolean).join(" ") || null,
    client.purpose ? `to ${client.purpose}` : null,
    client.preferred_areas,
  ].filter(Boolean);
  return parts.length > 0 ? `${phone} · ${parts.join(" · ")}` : phone;
}

function formatCompletedDate(iso: string | null): string | null {
  return iso
    ? new Date(iso).toLocaleString("en-IN", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : null;
}

/* ========================================================================
   Cards
   ======================================================================== */

function PropertyMatchCard({
  item,
  selected,
  onToggleSelect,
  onOpen,
  assigned,
  plan,
  onPlan,
}: {
  item: DialogItem;
  selected: boolean;
  onToggleSelect: () => void;
  onOpen: () => void;
  /** The active visit for this property, when it's already out with an
   *  agent — the card turns orange and loses its checkbox. */
  assigned: ActiveVisit | null;
  /** What the visit planner picked for this ticked card, if anything. */
  plan: { agentName: string; scheduledAt: string | null } | null;
  onPlan: () => void;
}) {
  const source = item.property ?? item.match!;
  const title = source.society_name || source.property_type || "Property";
  const location = [
    source.area_name,
    item.property?.address ?? item.match?.address,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div
      className={[
        "match-card",
        selected && "match-card--selected",
        assigned && "match-card--assigned",
      ]
        .filter(Boolean)
        .join(" ")}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
    >
      {/* Already assigned to an agent for this client — nothing left to
          select, so the toggle simply doesn't render rather than sitting
          there disabled (see the dialog's own Select button below, hidden
          the same way). */}
      {!assigned && (
        <button
          type="button"
          className={`select-toggle match-card__check${selected ? " select-toggle--add" : ""}`}
          aria-pressed={selected}
          aria-label={selected ? `Deselect ${title}` : `Select ${title}`}
          onClick={(event) => {
            event.stopPropagation();
            onToggleSelect();
          }}
        >
          <IconCheck size={12} strokeWidth={2.4} />
        </button>
      )}

      <div className="match-card__head">
        <div style={{ minWidth: 0 }}>
          <SourceTag source={item.source} />
          <div className="pcard__title cell-truncate">{title}</div>
          {location && (
            <div className="pcard__sub cell-truncate">{location}</div>
          )}
        </div>
        {item.match && (
          <span
            className={`match-card__score match-card__score--${item.match.bucket}`}
          >
            {Math.round(item.match.score * 100)}%
          </span>
        )}
      </div>

      <div className="match-card__facts">
        {(source.property_type || source.bhk) && (
          <span className="fact">
            <IconBuilding size={12} />
            {[source.bhk, source.property_type].filter(Boolean).join(" ")}
          </span>
        )}
        {source.area_name && (
          <span className="fact">
            <IconPin size={12} />
            {source.area_name}
          </span>
        )}
        {formatArea(source.area_sqft, source.area_vaar) !== "—" && (
          <span className="fact">
            <IconRuler size={12} />
            {formatArea(source.area_sqft, source.area_vaar)}
          </span>
        )}
      </div>

      <div className="match-card__badges">
        <Badge
          tone={
            item.section === "manual" || item.section === "website"
              ? "info"
              : BUCKET_TONE[item.match?.bucket ?? "low"]
          }
        >
          {item.section === "manual" || item.section === "website"
            ? SECTION_LABEL[item.section]
            : SECTION_LABEL[item.section].replace(" matches", " match")}
        </Badge>
        {/* Only ever set for a client who asked for more than one type —
            says which of their requirements this property answers. */}
        {item.match?.matched_type && (
          <Badge tone="accent">For {item.match.matched_type}</Badge>
        )}
        {/* Only when this card's OWN section isn't already the Web Site
            Property Inquiry badge above — a website enquiry that also
            scored or was hand-picked gets this second, smaller badge to
            say so without duplicating the same words twice on one card. */}
        {item.fromWebsiteInquiry && item.section !== "website" && (
          <Badge tone="accent">Website enquiry</Badge>
        )}
        {item.match?.is_partial_match && (
          <Badge tone="info">Partial data</Badge>
        )}
        {assigned && (
          <Badge tone="orange">
            <IconUserCheck size={11} /> Assigned to {assigned.agent.name}
          </Badge>
        )}
        {assigned?.active.scheduled_at && (
          <span className="fact">
            <IconClock size={12} />{" "}
            {formatVisitTime(assigned.active.scheduled_at)}
          </span>
        )}
      </div>

      {/* The visit planner's pick, right on the ticked card — "Agent X
          selected" plus the time, or a prompt to choose one. */}
      {selected && !assigned && (
        <div className="match-card__plan">
          <span className="match-card__plan-text">
            <IconUserCheck size={12} />
            {plan ? (
              <>
                Agent <strong>{plan.agentName}</strong> selected
                <span className="faint">
                  {" "}
                  ·{" "}
                  {plan.scheduledAt
                    ? formatVisitTime(plan.scheduledAt)
                    : "no time yet"}
                </span>
              </>
            ) : (
              <span className="faint">No agent chosen yet</span>
            )}
          </span>
          <button
            type="button"
            className="match-card__plan-btn"
            onClick={(event) => {
              event.stopPropagation();
              onPlan();
            }}
          >
            {plan ? "Change" : "Choose agent"}
          </button>
        </div>
      )}

      {item.match?.reason && (
        <div className="match-card__reason">{item.match.reason}</div>
      )}

      <div className="match-card__foot">
        <span className="pcard__price">
          {formatPrice(source.price_text, source.price_amount_inr)}
        </span>
        <span className="faint small">{source.listing_type}</span>
      </div>
    </div>
  );
}

/** One visit on the Assigned tab — the same card shape as Main's, orange
 *  throughout, with who is taking it and when. The time is the card's one
 *  action: "Set visit time" until there is one, then the time itself,
 *  clickable to change it. A re-visit is dashed and says which visit it is. */
function AssignedVisitCard({
  visit,
  source: listingSource,
  property,
  match,
  revisitNumber,
  onOpen,
  onEditTime,
}: {
  visit: ActiveVisit;
  /** Property or builder project — see ClientMatchesDialog's sourceFor. */
  source: PropertySource;
  /** The full record of whichever kind it is, when loaded. */
  property: ListingRecord | null;
  match: MatchedProperty | null;
  revisitNumber: number | null;
  onOpen: () => void;
  onEditTime: () => void;
}) {
  const source = property ?? match;
  const title =
    (source && (source.society_name || source.property_type)) ||
    visit.active.property_label ||
    "Property";
  const location = [source?.area_name, property?.address ?? match?.address]
    .filter(Boolean)
    .join(" · ");
  const when = visit.active.scheduled_at;
  const passed = when !== null && timeOf(when) < Date.now();

  return (
    <div
      className={`match-card match-card--assigned${revisitNumber ? " match-card--revisit" : ""}`}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
    >
      <div className="match-card__head">
        <div style={{ minWidth: 0 }}>
          <SourceTag source={listingSource} />
          <div className="pcard__title cell-truncate">{title}</div>
          {location && (
            <div className="pcard__sub cell-truncate">{location}</div>
          )}
        </div>
        {match && (
          <span
            className={`match-card__score match-card__score--${match.bucket}`}
          >
            {Math.round(match.score * 100)}%
          </span>
        )}
      </div>

      {source && (
        <div className="match-card__facts">
          {(source.property_type || source.bhk) && (
            <span className="fact">
              <IconBuilding size={12} />
              {[source.bhk, source.property_type].filter(Boolean).join(" ")}
            </span>
          )}
          {source.area_name && (
            <span className="fact">
              <IconPin size={12} />
              {source.area_name}
            </span>
          )}
          {formatArea(source.area_sqft, source.area_vaar) !== "—" && (
            <span className="fact">
              <IconRuler size={12} />
              {formatArea(source.area_sqft, source.area_vaar)}
            </span>
          )}
        </div>
      )}

      <div className="match-card__badges">
        <Badge tone="orange">
          <IconUserCheck size={11} /> With {visit.agent.name}
        </Badge>
        {revisitNumber && (
          <Badge tone="info">
            <IconRefresh size={11} /> Re-visit · visit #{revisitNumber}
          </Badge>
        )}
      </div>

      <div className="match-card__foot">
        {when ? (
          <button
            type="button"
            className={`visit-when${passed ? " visit-when--past" : ""}`}
            title={
              passed
                ? "This time has passed — click to change it"
                : "Change the visit time"
            }
            onClick={(event) => {
              event.stopPropagation();
              onEditTime();
            }}
          >
            <IconClock size={13} /> {formatVisitTime(when)}{" "}
            <IconEdit size={12} />
          </button>
        ) : (
          <Button
            size="sm"
            icon={<IconClock size={13} />}
            onClick={(event) => {
              event.stopPropagation();
              onEditTime();
            }}
          >
            Set visit time
          </Button>
        )}
        {source && (
          <span className="pcard__price">
            {formatPrice(source.price_text, source.price_amount_inr)}
          </span>
        )}
      </div>
    </div>
  );
}

/** One property's completed visits — a single card however many times it
 *  was visited. No checkbox and no score badge (its fit is no longer the
 *  point; that it was already shown and visited is). One visit reads as it
 *  always did; two or more are listed as a numbered history, visit 1 first,
 *  each with its own date, agent and notes. The Revisit button (top right)
 *  books another visit; while one is out, a badge says so instead. Falls
 *  back to the visits' own snapshotted property_label when the full
 *  PropertyRecord hasn't loaded yet or the property was since deleted. */
export function CompletedPropertyCard({
  visits,
  source,
  property,
  onOpen,
  activeVisit,
  canRevisit,
  onRevisit,
}: {
  /** Oldest first; never empty. */
  visits: VisitRecord[];
  /** Property or builder project — see ClientMatchesDialog's sourceFor. */
  source: PropertySource;
  /** The full record of whichever kind it is, when loaded. */
  property: ListingRecord | null;
  onOpen: () => void;
  /** The re-visit currently out for this property, if any. */
  activeVisit: ActiveVisit | null;
  /** False until the client record has loaded — the re-visit hand-off
   *  needs it, exactly as "Assign & send" does. */
  canRevisit: boolean;
  onRevisit: () => void;
}) {
  const latest = visits[visits.length - 1];
  const title =
    property?.society_name ||
    property?.property_type ||
    latest.property_label ||
    "Property";
  const location = [property?.area_name, property?.address]
    .filter(Boolean)
    .join(" · ");
  const single = visits.length === 1;
  const completedDate = formatCompletedDate(latest.completed_at);

  return (
    <div
      className="match-card match-card--completed"
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen();
        }
      }}
    >
      <div className="match-card__head">
        <div style={{ minWidth: 0 }}>
          <SourceTag source={source} />
          <div className="pcard__title cell-truncate">{title}</div>
          {location && (
            <div className="pcard__sub cell-truncate">{location}</div>
          )}
        </div>
        {!activeVisit && (
          <Button
            size="sm"
            variant="ghost"
            className="match-card__revisit"
            icon={<IconRefresh size={13} />}
            disabled={!canRevisit}
            onClick={(event) => {
              event.stopPropagation();
              onRevisit();
            }}
          >
            Revisit
          </Button>
        )}
      </div>

      {property && (
        <div className="match-card__facts">
          {(property.property_type || property.bhk) && (
            <span className="fact">
              <IconBuilding size={12} />
              {[property.bhk, property.property_type].filter(Boolean).join(" ")}
            </span>
          )}
          {property.area_name && (
            <span className="fact">
              <IconPin size={12} />
              {property.area_name}
            </span>
          )}
          {formatArea(property.area_sqft, property.area_vaar) !== "—" && (
            <span className="fact">
              <IconRuler size={12} />
              {formatArea(property.area_sqft, property.area_vaar)}
            </span>
          )}
        </div>
      )}

      <div className="match-card__badges">
        <Badge tone="ok">
          <IconCheck size={11} />{" "}
          {single
            ? `Completed by ${latest.agent_name}`
            : `Visited ${visits.length} times`}
        </Badge>
        {activeVisit && (
          <Badge tone="orange">
            <IconRefresh size={11} /> Re-visit with {activeVisit.agent.name}
          </Badge>
        )}
      </div>

      {single ? (
        latest.notes && <div className="match-card__reason">{latest.notes}</div>
      ) : (
        <ol className="visit-history">
          {visits.map((visit, index) => {
            const date = formatCompletedDate(visit.completed_at);
            return (
              <li key={visit.visit_id} className="visit-history__item">
                <span className="visit-history__num">{index + 1}</span>
                <div className="visit-history__body">
                  <div className="visit-history__when">
                    {date ? `Completed ${date}` : "Completed"} ·{" "}
                    {visit.agent_name}
                  </div>
                  {visit.notes && (
                    <div className="visit-history__notes">{visit.notes}</div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}

      <div className="match-card__foot">
        <span className="faint small">
          {single
            ? completedDate
              ? `Completed ${completedDate}`
              : "Completed"
            : `${visits.length} visits`}
        </span>
        {property && (
          <span className="pcard__price">
            {formatPrice(property.price_text, property.price_amount_inr)}
          </span>
        )}
      </div>
    </div>
  );
}

/* ========================================================================
   Detail dialog (one property, plus the move actions)
   ======================================================================== */

/** Exported so the Broker Requirements matches dialog
 *  (RequirementMatchesDialog.tsx) opens a property with exactly this view
 *  rather than a second copy of it that could drift. */
export function PropertyMatchDetailDialog({
  item,
  assignedAgent,
  moving,
  removing,
  selected,
  onToggleSelect,
  onMove,
  onRemoveManual,
  onClose,
}: {
  item: DialogItem;
  assignedAgent: AgentSummary | null;
  moving: boolean;
  removing: boolean;
  selected: boolean;
  onToggleSelect: () => void;
  onMove: (target: PropertyCategory) => void;
  onRemoveManual?: () => void;
  onClose: () => void;
}) {
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

  const { match, property } = item;
  const source = property ?? match!;
  const title = source.society_name || source.property_type || "Property";
  const subtitle = [source.area_name, property?.address ?? match?.address]
    .filter(Boolean)
    .join(" · ");
  const fieldScoreEntries = Object.entries(match?.field_scores ?? {}).filter(
    ([, value]) => value !== null,
  ) as [string, number][];
  const isBuilderProject = item.source === "builder_project";
  // Every drawer except the one it is already in — the whole point of
  // these buttons is that they never present a no-op. None at all for a
  // builder project: Main/Outsider is a property's filing, and a builder
  // project has no such thing to move.
  const moveTargets = isBuilderProject
    ? []
    : (["main", "outsider"] as PropertyCategory[]).filter(
        (target) => target !== item.category,
      );

  return createPortal(
    <div
      className="modal-scrim"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div
        className="detail-modal anim-rise"
        role="dialog"
        aria-modal="true"
        aria-label="Property details"
      >
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">
              {source.property_type || "Property"} · {source.listing_type}
            </div>
            <h2 className="detail-modal__title cell-truncate">{title}</h2>
            {subtitle && (
              <div className="detail-modal__sub cell-truncate">{subtitle}</div>
            )}
            <div className="detail-modal__badges">
              <SourceTag source={item.source} />
              {match && (
                <Badge tone={BUCKET_TONE[match.bucket]}>
                  {Math.round(match.score * 100)}% match
                </Badge>
              )}
              {match?.is_partial_match && (
                <Badge tone="info">Partial data</Badge>
              )}
              {match?.matched_type && (
                <Badge tone="accent">For {match.matched_type}</Badge>
              )}
              {!isBuilderProject && (
                <Badge tone={item.category === "main" ? "ok" : "info"}>
                  {CATEGORY_LABEL[item.category]}
                </Badge>
              )}
              {item.section === "manual" && (
                <Badge tone="info">Manually added</Badge>
              )}
              {assignedAgent && (
                <Badge tone="orange">
                  <IconUserCheck size={11} /> Assigned to {assignedAgent.name}
                </Badge>
              )}
              {source.bhk && (
                <span className="fact">
                  <IconBuilding size={12} />
                  {source.bhk}
                </span>
              )}
              {formatArea(source.area_sqft, source.area_vaar) !== "—" && (
                <span className="fact">
                  <IconRuler size={12} />
                  {formatArea(source.area_sqft, source.area_vaar)}
                </span>
              )}
            </div>
          </div>
          <button
            type="button"
            className="toast__close"
            onClick={onClose}
            aria-label="Close"
          >
            <IconX size={15} />
          </button>
        </div>

        <div className="detail-modal__body">
          {property?.needs_review && property.review_notes && (
            <Note tone="warn" icon={<IconAlert size={16} />}>
              <strong>Flagged for review:</strong> {property.review_notes}
            </Note>
          )}

          {match && (
            <div className="detail__block">
              <div className="detail__k">Why this match</div>
              <div className="detail__v">{match.reason}</div>
              {fieldScoreEntries.length > 0 && (
                <div
                  className="faint small row-flex"
                  style={{ gap: 10, marginTop: 6, flexWrap: "wrap" }}
                >
                  {fieldScoreEntries.map(([name, value]) => (
                    <span key={name}>
                      {FIELD_SCORE_LABEL[name] ?? name}:{" "}
                      {Math.round(value * 100)}%
                    </span>
                  ))}
                </div>
              )}
              <div className="faint small" style={{ marginTop: 6 }}>
                Data available for {Math.round(match.evidence_ratio * 100)}% of
                the fields compared.
              </div>
            </div>
          )}

          <div className="detail__grid">
            <div className="detail__block">
              <div className="detail__k">Price as written</div>
              <div className="detail__v">{source.price_text ?? "—"}</div>
              {source.price_amount_inr !== null && (
                <div className="faint small" style={{ marginTop: 4 }}>
                  Read as {formatPrice(null, source.price_amount_inr)}
                </div>
              )}
            </div>


            <div className="detail__block">
              <div className="detail__k">Contact</div>
              <div className="detail__v">{source.contact_name ?? "—"}</div>
              {source.contact_phone && (
                <div className="detail__v" style={{ marginTop: 4 }}>
                  <Copyable text={source.contact_phone} />
                </div>
              )}
            </div>

            {property && (
              <div className="detail__block">
                <div className="detail__k">Sender</div>
                <div className="detail__v">
                  {property.sender_name}
                  {property.sender_saved_name &&
                    property.sender_saved_name !== property.sender_name && (
                      <span className="faint">
                        {" "}
                        · saved as {property.sender_saved_name}
                      </span>
                    )}
                </div>
                <div className="detail__v" style={{ marginTop: 4 }}>
                  <Copyable text={property.sender_phone} />
                </div>
              </div>
            )}

            {property && (
              <div className="detail__block">
                <div className="detail__k">Source</div>
                <div className="detail__v">{sourceLabel(property)}</div>
                <div className="faint small" style={{ marginTop: 4 }}>
                  {sourceDetail(property)} · {property.formatted_timestamp}
                </div>
              </div>
            )}
          </div>

          {(property?.description ?? match?.description) && (
            <div className="detail__block">
              <div className="detail__k">Description</div>
              <div className="detail__v">
                {property?.description ?? match?.description}
              </div>
            </div>
          )}

          {property && (
            <div className="detail__block">
              <div className="detail__k">
                <IconMessage size={11} /> Original message
              </div>
              <div className="detail__msg">{property.message_text}</div>
            </div>
          )}
        </div>

        <div className="detail-modal__foot detail-modal__foot--wrap">
          {/* Hidden once assigned — same reasoning as the card's own
              checkbox (see PropertyMatchCard above): an assigned property
              has nothing left to select. */}
          {!assignedAgent && (
            <Button
              size="sm"
              variant="ghost"
              className={`select-toggle-btn${selected ? " select-toggle-btn--remove" : ""}`}
              icon={<IconCheck size={14} />}
              onClick={onToggleSelect}
            >
              {selected ? "Deselect" : "Select"}
            </Button>
          )}
          {moveTargets.map((target) => (
            <Button
              key={target}
              size="sm"
              icon={<IconMove size={14} />}
              busy={moving}
              onClick={() => onMove(target)}
            >
              Move to {CATEGORY_LABEL[target]}
            </Button>
          ))}
          {onRemoveManual && (
            <Button
              size="sm"
              variant="ghost"
              icon={<IconTrash size={14} />}
              busy={removing}
              onClick={onRemoveManual}
            >
              Remove
            </Button>
          )}
          <span style={{ marginLeft: "auto" }}>
            <Button size="sm" variant="ghost" onClick={onClose}>
              Close
            </Button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
