import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { agentApi } from "../api/agentApi";
import { inquiryClientApi } from "../api/inquiryClientApi";
import { landingLeadApi } from "../api/landingLeadApi";
import { matchingApi } from "../api/matchingApi";
import { propertyApi } from "../api/propertyApi";
import type {
  AgentSummary,
  ClientMatchResult,
  InquiryClientRecord,
  MatchBucket,
  MatchedProperty,
  PropertyRecord,
  VisitRecord,
} from "../api/types";
import { friendlyError } from "../lib/apiError";
import { getCachedAgents, setCachedAgents } from "../lib/agentListCache";
import {
  getCachedCompletedVisits,
  getCachedMatchResult,
  setCachedCompletedVisits,
  setCachedMatchResult,
} from "../lib/clientMatchCache";
import { formatCarpetArea, formatPrice, formatPricePerUnit, relativeTime } from "../lib/formatters";
import type { AgentAssignment } from "../lib/handoffTemplate";
import { sourceDetail, sourceLabel } from "../lib/propertyFilters";
import { getCachedPropertyList, patchCachedProperty, setCachedPropertyList } from "../lib/propertyListCache";
import type { SharePropertyLike } from "../lib/propertyShareTemplate";
import HandoffDialog from "./HandoffDialog";
import MultiAssignDialog, { type SelectableProperty } from "./MultiAssignDialog";
import PropertyReadOnlyDialog from "./PropertyReadOnlyDialog";
import ShareClientPropertiesDialog from "./ShareClientPropertiesDialog";
import ConfirmDialog from "./ui/ConfirmDialog";
import { useToast } from "./ui/Toast";
import { Badge, Button, Copyable, EmptyState, Note, Segmented, SkeletonRows } from "./ui/Primitives";
import {
  IconAlert,
  IconBuilding,
  IconCheck,
  IconClock,
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
 *  above, or the Completed view, which is not a drawer at all (a property
 *  there already had its visit; which drawer it happens to sit in on the
 *  Properties page stopped being the relevant fact about it the moment
 *  that happened). Segmented's own `null` state (see ui/Primitives.tsx's
 *  own comment on it) is exactly "a different control currently owns the
 *  view", which is precisely what's true while this reads "completed". */
export type DialogView = PropertyCategory | "completed";

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
  purpose_gate: "Purpose match",
  property_type_gate: "Property type match",
};

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
function categoryOf(record: { review_status: "accepted" | "outsider" }): PropertyCategory {
  return record.review_status === "outsider" ? "outsider" : "main";
}

/** What a "move to X" button actually writes. Both drawers clear
 *  `needs_review`: filing a property into Main or Outsider IS how the
 *  review flag gets resolved (see the Properties page's Needs review
 *  dialog, which offers exactly these two actions). */
function categoryPatch(target: PropertyCategory): { review_status: "accepted" | "outsider"; needs_review: boolean } {
  return { review_status: target === "outsider" ? "outsider" : "accepted", needs_review: false };
}

/** One card in the dialog — a scored match, a hand-picked property, or
 *  (when the operator manually added something the algorithm also
 *  matched) both at once, in which case the score is still shown but the
 *  card lives in the Manually added section. */
interface DialogItem {
  recordId: string;
  section: SectionKey;
  category: PropertyCategory;
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

type AssignFlow = { step: "pick" } | { step: "handoff"; assignments: AgentAssignment[] } | null;

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
 * drawer you're working out of (Main / Outsider), read down
 * the properties in it strongest-first, tick the ones worth showing, and
 * hand them off — without ever losing your place in the Inquiries table.
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
   *  themselves. */
  onChanged?: () => void;
}) {
  const navigate = useNavigate();
  const toast = useToast();

  // Seeded from the module-level caches (lib/clientMatchCache.ts,
  // lib/agentListCache.ts) so a client whose dialog was already opened
  // once this session paints instantly on reopen — load() below always
  // fires a fresh fetch behind that regardless, so a seed is never the
  // last word, only a head start (see those caches' own docstrings).
  const [result, setResult] = useState<ClientMatchResult | null>(() => getCachedMatchResult(phone));
  const [properties, setProperties] = useState<PropertyRecord[] | null>(() => getCachedPropertyList()?.data ?? null);
  const [client, setClient] = useState<InquiryClientRecord | null>(null);
  const [agents, setAgents] = useState<AgentSummary[] | null>(() => getCachedAgents());
  const [manualPropertyIds, setManualPropertyIds] = useState<string[] | null>(null);
  const [completedVisits, setCompletedVisits] = useState<VisitRecord[] | null>(() => getCachedCompletedVisits(phone));
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
  const [loadingMatches, setLoadingMatches] = useState(() => getCachedMatchResult(phone) === null);
  const [loadingCompleted, setLoadingCompleted] = useState(() => getCachedCompletedVisits(phone) === null);
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
  const [loadingProperties, setLoadingProperties] = useState(() => getCachedPropertyList() === null);
  const [error, setError] = useState<string | null>(null);
  const [recomputing, setRecomputing] = useState(false);
  const [movingId, setMovingId] = useState<string | null>(null);
  const [removingManualId, setRemovingManualId] = useState<string | null>(null);

  const [category, setCategory] = useState<DialogView>(initialView ?? "main");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [openItemId, setOpenItemId] = useState<string | null>(null);
  const [viewingCompletedId, setViewingCompletedId] = useState<string | null>(null);
  const [assignFlow, setAssignFlow] = useState<AssignFlow>(null);
  // The "send the shortlist straight to the client" flow, deliberately
  // separate from assignFlow above: it involves no agent and no visit
  // record, so it is not a step of the assignment wizard and must not share
  // its state machine.
  const [shareOpen, setShareOpen] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [clearing, setClearing] = useState(false);

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

    Promise.all([inquiryClientApi.getClient(phone), inquiryClientApi.getManualProperties(phone)])
      .then(([clientRecord, manualIds]) => {
        setClient(clientRecord);
        setManualPropertyIds(manualIds);
      })
      // Never clobber a more specific error the matches fetch may already
      // have set — first genuine failure wins, same reasoning as
      // InquiryClientsPage's own dual-source status handling.
      .catch((err) => setError((previous) => previous ?? friendlyError(err)))
      .finally(() => setLoadingMeta(false));

    // Ungated on purpose — see loadingMeta's own comment above. Updates
    // the shared cache too, so AgentsPage/SelectPropertyPage/the next
    // ClientMatchesDialog opened all benefit from whichever of them
    // fetched most recently.
    agentApi
      .getAgents()
      .then((agentList) => {
        setAgents(agentList);
        setCachedAgents(agentList);
      })
      .catch(() => {});

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
  }, [phone]);

  // `properties`/`loadingProperties` above already seed from the cache
  // directly (their own useState initializers) — this just fires the
  // mandatory background refresh every one of load()'s four fetches
  // always performs, regardless of what was cached.
  useEffect(() => {
    load();
  }, [load]);

  const nestedOpen =
    openItemId !== null || viewingCompletedId !== null || assignFlow !== null || clearOpen || shareOpen;

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
    for (const property of properties ?? []) map.set(property.record_id, property);
    return map;
  }, [properties]);

  /** Which agent (if any) is actively handling each property FOR THIS
   *  CLIENT — keyed per property, since one client's properties can be
   *  split across several agents. */
  const assignedAgentByProperty = useMemo(() => {
    const map = new Map<string, AgentSummary>();
    for (const agent of agents ?? []) {
      for (const active of agent.active_clients) {
        if (active.phone === phone) map.set(active.property_record_id, agent);
      }
    }
    return map;
  }, [agents, phone]);

  const manualIdSet = useMemo(() => new Set(manualPropertyIds ?? []), [manualPropertyIds]);
  const websiteIdSet = useMemo(() => new Set(websitePropertyIds), [websitePropertyIds]);

  /** One entry per property that already has a completed visit — deduped
   *  down from the raw visit list (a property visited, reopened, then
   *  visited again would otherwise appear twice), keeping whichever visit
   *  is newest since completedVisits already arrives newest-first from
   *  the backend. Everything downstream (the exclusion below, the
   *  Completed section, the header badge) reads off this map, so there is
   *  exactly one place that decides what "N completed" counts. */
  const completedByProperty = useMemo(() => {
    const map = new Map<string, VisitRecord>();
    for (const visit of completedVisits ?? []) {
      if (!visit.property_record_id) continue; // pre-existing rows with nothing to attribute this to
      if (!map.has(visit.property_record_id)) map.set(visit.property_record_id, visit);
    }
    return map;
  }, [completedVisits]);

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
      const property = propertiesById.get(match.record_id) ?? null;
      byId.set(match.record_id, {
        recordId: match.record_id,
        section: manualIdSet.has(match.record_id) ? "manual" : bucket,
        category: categoryOf(property ?? match),
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
        match: null,
        property,
        handoff: property,
        fromWebsiteInquiry: true,
      });
    }

    return [...byId.values()];
  }, [result, manualPropertyIds, manualIdSet, propertiesById, completedByProperty, websiteIdSet]);

  const countByCategory = useMemo(() => {
    const counts: Record<PropertyCategory, number> = { main: 0, outsider: 0 };
    for (const item of items) counts[item.category] += 1;
    return counts;
  }, [items]);

  const visibleItems = useMemo(() => items.filter((item) => item.category === category), [items, category]);

  const sections = useMemo(
    () =>
      SECTION_ORDER.map((key) => ({
        key,
        items: visibleItems
          .filter((item) => item.section === key)
          .sort((a, b) => (b.match?.score ?? 0) - (a.match?.score ?? 0)),
      })).filter((section) => section.items.length > 0),
    [visibleItems],
  );

  // Every card that came from a website enquiry, regardless of its own
  // PRIMARY section — this is what makes a High-matching website enquiry
  // show up a second time here, right where it scored AND under this
  // heading, rather than one or the other. Rendered as its own block,
  // positioned right after Manually added (see the JSX below), never as
  // one of the sections above.
  const websiteItems = useMemo(
    () => visibleItems.filter((item) => item.fromWebsiteInquiry).sort((a, b) => (b.match?.score ?? 0) - (a.match?.score ?? 0)),
    [visibleItems],
  );
  const manualSection = sections.find((section) => section.key === "manual") ?? null;
  const bucketSections = sections.filter((section) => section.key !== "manual");

  const openItem = useMemo(() => items.find((item) => item.recordId === openItemId) ?? null, [items, openItemId]);

  const selectedProperties = useMemo<SelectableProperty[]>(
    () =>
      items
        .filter((item) => selectedIds.has(item.recordId))
        .map((item) => ({
          property: item.handoff,
          source: item.section === "manual" ? "manual" : item.section === "website" ? "enquired" : "matched",
        })),
    [items, selectedIds],
  );

  /** The same ticked cards the assignment flow uses, in the shape a
   *  WhatsApp share message needs. Built from `items` (not from
   *  selectedIds directly) so a property that has dropped off the list can
   *  never end up in a message — exactly the guarantee selectedProperties
   *  above gives the hand-off. */
  const selectedShareProperties = useMemo<SharePropertyLike[]>(
    () => items.filter((item) => selectedIds.has(item.recordId)).map((item) => item.handoff),
    [items, selectedIds],
  );

  /** Hand-picked properties whose full record hasn't arrived yet — they
   *  have no match record to fall back on, so they simply aren't
   *  renderable until the property list lands. Counted so the wait is
   *  stated rather than looking like the list is just short. */
  const pendingManualCount = useMemo(() => {
    if (loadingMeta || !loadingProperties) return 0;
    return (manualPropertyIds ?? []).filter((id) => !propertiesById.has(id)).length;
  }, [loadingMeta, loadingProperties, manualPropertyIds, propertiesById]);

  const assignedCount = useMemo(
    () => items.filter((item) => assignedAgentByProperty.has(item.recordId)).length,
    [items, assignedAgentByProperty],
  );

  /** Newest-first, one card per property — see completedByProperty's own
   *  comment for how duplicates are resolved. */
  const completedList = useMemo(() => [...completedByProperty.values()], [completedByProperty]);

  function toggleSelected(recordId: string) {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(recordId)) next.delete(recordId);
      else next.add(recordId);
      return next;
    });
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
      toast.push({ tone: "bad", title: "Refresh failed", message: friendlyError(err) });
    } finally {
      setRecomputing(false);
    }
  }

  /** Move one property between Main and Outsider. Patched
   *  locally rather than re-running the whole matching pipeline: a
   *  property changing drawers changes nothing about how well it fits
   *  this client, only where it is filed. */
  async function handleMove(item: DialogItem, target: PropertyCategory) {
    setMovingId(item.recordId);
    try {
      const updated = await propertyApi.updateProperty(item.recordId, categoryPatch(target));
      setProperties((previous) => {
        if (previous === null) return [updated];
        const index = previous.findIndex((p) => p.record_id === updated.record_id);
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
      toast.push({ tone: "bad", title: "Could not move property", message: friendlyError(err) });
    } finally {
      setMovingId(null);
    }
  }

  /**
   * Calls off every site visit currently out with an agent for this client
   * and messages each agent involved once — the backend does both (see
   * whatsapp_inquiry_controller.clear_assignments).
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
      // Re-read so every card drops its "Assigned to X" badge immediately
      // rather than on some later poll.
      void load();
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not clear the assignments", message: friendlyError(err) });
    } finally {
      setClearing(false);
    }
  }

  async function handleRemoveManual(recordId: string) {
    setRemovingManualId(recordId);
    try {
      setManualPropertyIds(await inquiryClientApi.removeManualProperty(phone, recordId));
      setSelectedIds((previous) => {
        if (!previous.has(recordId)) return previous;
        const next = new Set(previous);
        next.delete(recordId);
        return next;
      });
      setOpenItemId((previous) => (previous === recordId ? null : previous));
      onChanged?.();
    } catch (err) {
      toast.push({ tone: "bad", title: "Could not remove this property", message: friendlyError(err) });
    } finally {
      setRemovingManualId(null);
    }
  }

  const total = items.length;

  // Composite flags matching the section each actually feeds — see the
  // four loading* state declarations' own comment for why they're split.
  const mainSectionLoading = loadingMatches || loadingMeta || loadingProperties;
  const completedSectionLoading = loadingCompleted || loadingMeta || loadingProperties;
  const sectionLoading = category === "completed" ? completedSectionLoading : mainSectionLoading;
  const allSettled = !loadingMatches && !loadingCompleted && !loadingMeta && !loadingProperties;

  return createPortal(
    <>
      <div
        className="modal-scrim"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget && !nestedOpen) onClose();
        }}
      >
        <div
          className="detail-modal detail-modal--wide anim-rise"
          role="dialog"
          aria-modal="true"
          aria-label={`Properties matched for ${client?.name || clientName || phone}`}
        >
          <div className="detail-modal__head">
            <div style={{ minWidth: 0 }}>
              <div className="detail-modal__eyebrow">Matched properties</div>
              <h2 className="detail-modal__title cell-truncate">{client?.name || clientName || "Matches"}</h2>
              <div className="detail-modal__sub cell-truncate">{summariseRequirements(client, phone)}</div>
              <div className="detail-modal__badges">
                <Badge tone="accent">
                  {total} propert{total === 1 ? "y" : "ies"}
                </Badge>
                {assignedCount > 0 && (
                  <Badge tone="ok">
                    {assignedCount} assigned
                    {assignedCount < total ? ` · ${total - assignedCount} remaining` : ""}
                  </Badge>
                )}
                {result?.computed_at && (
                  <span className="fact">
                    <IconClock size={12} /> Computed {relativeTime(new Date(result.computed_at))}
                  </span>
                )}
              </div>
            </div>
            <div className="row-flex" style={{ gap: 8, flex: "none" }}>
              <Button size="sm" variant="ghost" icon={<IconRefresh size={14} />} onClick={handleRefresh} busy={recomputing}>
                Refresh
              </Button>
              <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
                <IconX size={15} />
              </button>
            </div>
          </div>

          <div className="matches-dialog__tabs">
            <Segmented<PropertyCategory>
              ariaLabel="Which properties to show"
              value={category === "completed" ? null : category}
              onChange={setCategory}
              options={(["main", "outsider"] as PropertyCategory[]).map((key) => ({
                value: key,
                label: `${CATEGORY_LABEL[key]}${countByCategory[key] ? ` (${countByCategory[key]})` : ""}`,
              }))}
            />
            {completedList.length > 0 && (
              <button
                type="button"
                className={`pill-ok${category === "completed" ? " pill-ok--active" : ""}`}
                onClick={() => setCategory(category === "completed" ? "main" : "completed")}
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

          <div className="detail-modal__body">
            {error && (
              <Note tone="bad" icon={<IconAlert size={16} />}>
                {error}
              </Note>
            )}

            {sectionLoading && <SkeletonRows rows={5} />}

            {allSettled && result && !result.has_requirements && total === 0 && completedList.length === 0 && (
              <EmptyState
                icon={<IconInbox size={36} />}
                title="No requirements yet"
                body="This client hasn't submitted their property requirements (purpose, type, budget, area) yet — matches appear here automatically once they do. You can still add a property by hand."
              />
            )}

            {!mainSectionLoading &&
              category !== "completed" &&
              (result?.has_requirements || total > 0 || completedList.length > 0) &&
              sections.length === 0 &&
              websiteItems.length === 0 && (
                <EmptyState
                  icon={<IconInbox size={36} />}
                  title={`Nothing in ${CATEGORY_LABEL[category]}`}
                  body={
                    total > 0
                      ? "Every property found for this client is filed under one of the other tabs above."
                      : "No stored property matched this client's requirements yet. Add one by hand, or refresh the matches."
                  }
                />
              )}

            {!completedSectionLoading && category === "completed" && completedList.length === 0 && (
              <EmptyState
                icon={<IconInbox size={36} />}
                title="No completed visits yet"
                body="Once a visit for one of this client's properties is marked complete on the Agents page, it moves here."
              />
            )}

            {!completedSectionLoading && category === "completed" && completedList.length > 0 && (
              <div className="stack stack-3">
                <div className="matches-dialog__section-head">
                  <span className="section-head__eyebrow" style={{ marginBottom: 0 }}>
                    Completed
                  </span>
                  <span className="matches-dialog__section-count">{completedList.length}</span>
                  <span className="matches-dialog__rule" />
                </div>
                <div className="matches-grid">
                  {completedList.map((visit) => (
                    <CompletedPropertyCard
                      key={visit.visit_id}
                      visit={visit}
                      property={visit.property_record_id ? propertiesById.get(visit.property_record_id) ?? null : null}
                      onOpen={() => visit.property_record_id && setViewingCompletedId(visit.property_record_id)}
                    />
                  ))}
                </div>
              </div>
            )}

            {!mainSectionLoading && pendingManualCount > 0 && category !== "completed" && (
              <Note tone="info" icon={<IconClock size={16} />}>
                Loading {pendingManualCount} hand-picked propert{pendingManualCount === 1 ? "y" : "ies"}…
              </Note>
            )}

            {!mainSectionLoading && category !== "completed" && manualSection && (
              <div className="stack stack-3">
                <div className="matches-dialog__section-head">
                  <span className="section-head__eyebrow" style={{ marginBottom: 0 }}>
                    {SECTION_LABEL[manualSection.key]}
                  </span>
                  <span className="matches-dialog__section-count">{manualSection.items.length}</span>
                  <span className="matches-dialog__rule" />
                </div>
                <div className="matches-grid">
                  {manualSection.items.map((item) => (
                    <PropertyMatchCard
                      key={item.recordId}
                      item={item}
                      selected={selectedIds.has(item.recordId)}
                      onToggleSelect={() => toggleSelected(item.recordId)}
                      onOpen={() => setOpenItemId(item.recordId)}
                      assignedAgent={assignedAgentByProperty.get(item.recordId) ?? null}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Right after Manually added, per the feature's own ask —
                every card here also appears in its own native section
                above (High/Medium/Low/Manually added) when it has one;
                the only ones that DON'T are website enquiries that never
                scored or got hand-picked, whose only home is here. */}
            {!mainSectionLoading && category !== "completed" && websiteItems.length > 0 && (
              <div className="stack stack-3">
                <div className="matches-dialog__section-head">
                  <span className="section-head__eyebrow" style={{ marginBottom: 0 }}>
                    {SECTION_LABEL.website}
                  </span>
                  <span className="matches-dialog__section-count">{websiteItems.length}</span>
                  <span className="matches-dialog__rule" />
                </div>
                <div className="matches-grid">
                  {websiteItems.map((item) => (
                    <PropertyMatchCard
                      key={item.recordId}
                      item={item}
                      selected={selectedIds.has(item.recordId)}
                      onToggleSelect={() => toggleSelected(item.recordId)}
                      onOpen={() => setOpenItemId(item.recordId)}
                      assignedAgent={assignedAgentByProperty.get(item.recordId) ?? null}
                    />
                  ))}
                </div>
              </div>
            )}

            {!mainSectionLoading &&
              category !== "completed" &&
              bucketSections.map((section) => (
              <div key={section.key} className="stack stack-3">
                <div className="matches-dialog__section-head">
                  <span className="section-head__eyebrow" style={{ marginBottom: 0 }}>
                    {SECTION_LABEL[section.key]}
                  </span>
                  <span className="matches-dialog__section-count">{section.items.length}</span>
                  <span className="matches-dialog__rule" />
                </div>
                <div className="matches-grid">
                  {section.items.map((item) => (
                    <PropertyMatchCard
                      key={item.recordId}
                      item={item}
                      selected={selectedIds.has(item.recordId)}
                      onToggleSelect={() => toggleSelected(item.recordId)}
                      onOpen={() => setOpenItemId(item.recordId)}
                      assignedAgent={assignedAgentByProperty.get(item.recordId) ?? null}
                    />
                  ))}
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
                button that can only ever say "nothing to clear" is noise. */}
            {assignedCount > 0 && (
              <Button
                variant="ghost"
                icon={<IconTrash size={15} />}
                onClick={() => setClearOpen(true)}
                busy={clearing}
              >
                Clear assignments ({assignedCount})
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
                onClick={() => setAssignFlow({ step: "pick" })}
                disabled={!client || selectedIds.size === 0}
              >
                Assign &amp; send ({selectedIds.size})
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
          onToggleSelect={() => toggleSelected(openItem.recordId)}
          onMove={(target) => handleMove(openItem, target)}
          onRemoveManual={openItem.section === "manual" ? () => handleRemoveManual(openItem.recordId) : undefined}
          onClose={() => setOpenItemId(null)}
        />
      )}

      {viewingCompletedId && (
        <PropertyReadOnlyDialog recordId={viewingCompletedId} onClose={() => setViewingCompletedId(null)} />
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
                {assignedCount} propert{assignedCount === 1 ? "y is" : "ies are"} currently out with an agent for{" "}
                <strong>{client?.name || clientName || phone}</strong>. Clearing removes{" "}
                {assignedCount === 1 ? "that assignment" : "all of those assignments"} and messages every agent
                involved on WhatsApp to say the visits are cancelled.
              </p>
              <p className="section-head__sub" style={{ margin: 0 }}>
                The properties themselves stay on this list, ready to be assigned again.{" "}
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
          onSent={() => setSelectedIds(new Set())}
        />
      )}

      {assignFlow?.step === "pick" && client && (
        <MultiAssignDialog
          client={client}
          agents={agents ?? []}
          selected={selectedProperties}
          onClose={() => setAssignFlow(null)}
          onContinue={(assignments) => setAssignFlow({ step: "handoff", assignments })}
        />
      )}

      {assignFlow?.step === "handoff" && client && (
        <HandoffDialog
          client={client}
          assignments={assignFlow.assignments}
          onClose={() => setAssignFlow(null)}
          onPickDifferentAgent={() => setAssignFlow({ step: "pick" })}
          onSent={(updated) => {
            setClient(updated);
            setSelectedIds(new Set());
            setAssignFlow(null);
            onChanged?.();
            // Re-read the agents so every just-assigned property turns
            // green here immediately rather than on some later poll.
            void load();
          }}
        />
      )}
    </>,
    document.body,
  );
}

/** Rewrite the two category fields on every copy of one property inside a
 *  cached match result, leaving scores untouched. */
function patchMatchFields(result: ClientMatchResult, updated: PropertyRecord): ClientMatchResult {
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
  return { ...result, high: patch(result.high), medium: patch(result.medium), low: patch(result.low) };
}

function summariseRequirements(client: InquiryClientRecord | null, phone: string): string {
  if (!client) return phone;
  const parts = [
    [client.bhk, client.property_type].filter(Boolean).join(" ") || null,
    client.purpose ? `to ${client.purpose}` : null,
    client.preferred_areas,
  ].filter(Boolean);
  return parts.length > 0 ? `${phone} · ${parts.join(" · ")}` : phone;
}

/* ========================================================================
   Card
   ======================================================================== */

function PropertyMatchCard({
  item,
  selected,
  onToggleSelect,
  onOpen,
  assignedAgent,
}: {
  item: DialogItem;
  selected: boolean;
  onToggleSelect: () => void;
  onOpen: () => void;
  assignedAgent: AgentSummary | null;
}) {
  const source = item.property ?? item.match!;
  const title = source.society_name || source.property_type || "Property";
  const location = [source.area_name, item.property?.address ?? item.match?.address].filter(Boolean).join(" · ");

  return (
    <div
      className={[
        "match-card",
        selected && "match-card--selected",
        assignedAgent && "match-card--assigned",
      ]
        .filter(Boolean)
        .join(" ")}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
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
      {!assignedAgent && (
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
          <div className="pcard__title cell-truncate">{title}</div>
          {location && <div className="pcard__sub cell-truncate">{location}</div>}
        </div>
        {item.match && (
          <span className={`match-card__score match-card__score--${item.match.bucket}`}>
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
        {source.carpet_area_sqft !== null && (
          <span className="fact">
            <IconRuler size={12} />
            {formatCarpetArea(source.carpet_area_sqft, source.carpet_area_unit)}
          </span>
        )}
      </div>

      <div className="match-card__badges">
        <Badge
          tone={item.section === "manual" || item.section === "website" ? "info" : BUCKET_TONE[item.match?.bucket ?? "low"]}
        >
          {item.section === "manual" || item.section === "website"
            ? SECTION_LABEL[item.section]
            : SECTION_LABEL[item.section].replace(" matches", " match")}
        </Badge>
        {/* Only when this card's OWN section isn't already the Web Site
            Property Inquiry badge above — a website enquiry that also
            scored or was hand-picked gets this second, smaller badge to
            say so without duplicating the same words twice on one card. */}
        {item.fromWebsiteInquiry && item.section !== "website" && <Badge tone="accent">Website enquiry</Badge>}
        {item.match?.is_partial_match && <Badge tone="info">Partial data</Badge>}
        {assignedAgent && (
          <Badge tone="ok">
            <IconUserCheck size={11} /> Assigned to {assignedAgent.name}
          </Badge>
        )}
      </div>

      {item.match?.reason && <div className="match-card__reason">{item.match.reason}</div>}

      <div className="match-card__foot">
        <span className="pcard__price">{formatPrice(source.price_text, source.price_amount_inr)}</span>
        <span className="faint small">{source.listing_type}</span>
      </div>
    </div>
  );
}

/** One completed visit's card — no checkbox (a visited property isn't
 *  part of the next hand-off round) and no bucket/score badge (its fit is
 *  no longer the point; that it was already shown and visited is). Falls
 *  back to the visit's own snapshotted property_label/budget when the
 *  full PropertyRecord hasn't loaded yet or the property was since
 *  deleted, the same graceful-degradation the match cards use. */
export function CompletedPropertyCard({
  visit,
  property,
  onOpen,
}: {
  visit: VisitRecord;
  property: PropertyRecord | null;
  onOpen: () => void;
}) {
  const title = property?.society_name || property?.property_type || visit.property_label || "Property";
  const location = [property?.area_name, property?.address].filter(Boolean).join(" · ");
  const clickable = visit.property_record_id !== null;
  const completedDate = visit.completed_at
    ? new Date(visit.completed_at).toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" })
    : null;

  return (
    <div
      className="match-card match-card--assigned"
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={clickable ? onOpen : undefined}
      onKeyDown={
        clickable
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onOpen();
              }
            }
          : undefined
      }
      style={{ cursor: clickable ? "pointer" : "default" }}
    >
      <div className="match-card__head">
        <div style={{ minWidth: 0 }}>
          <div className="pcard__title cell-truncate">{title}</div>
          {location && <div className="pcard__sub cell-truncate">{location}</div>}
        </div>
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
          {property.carpet_area_sqft !== null && (
            <span className="fact">
              <IconRuler size={12} />
              {formatCarpetArea(property.carpet_area_sqft, property.carpet_area_unit)}
            </span>
          )}
        </div>
      )}

      <div className="match-card__badges">
        <Badge tone="ok">
          <IconCheck size={11} /> Completed by {visit.agent_name}
        </Badge>
      </div>

      {visit.notes && <div className="match-card__reason">{visit.notes}</div>}

      <div className="match-card__foot">
        <span className="faint small">{completedDate ? `Completed ${completedDate}` : "Completed"}</span>
        {property && <span className="pcard__price">{formatPrice(property.price_text, property.price_amount_inr)}</span>}
      </div>
    </div>
  );
}

/* ========================================================================
   Detail dialog (one property, plus the move actions)
   ======================================================================== */

function PropertyMatchDetailDialog({
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
  const subtitle = [source.area_name, property?.address ?? match?.address].filter(Boolean).join(" · ");
  const fieldScoreEntries = Object.entries(match?.field_scores ?? {}).filter(([, value]) => value !== null) as [
    string,
    number,
  ][];
  // Every drawer except the one it is already in — the whole point of
  // these buttons is that they never present a no-op.
  const moveTargets = (["main", "outsider"] as PropertyCategory[]).filter(
    (target) => target !== item.category,
  );

  return createPortal(
    <div className="modal-scrim" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="detail-modal anim-rise" role="dialog" aria-modal="true" aria-label="Property details">
        <div className="detail-modal__head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-modal__eyebrow">
              {source.property_type || "Property"} · {source.listing_type}
            </div>
            <h2 className="detail-modal__title cell-truncate">{title}</h2>
            {subtitle && <div className="detail-modal__sub cell-truncate">{subtitle}</div>}
            <div className="detail-modal__badges">
              {match && <Badge tone={BUCKET_TONE[match.bucket]}>{Math.round(match.score * 100)}% match</Badge>}
              {match?.is_partial_match && <Badge tone="info">Partial data</Badge>}
              <Badge tone={item.category === "main" ? "ok" : "info"}>
                {CATEGORY_LABEL[item.category]}
              </Badge>
              {item.section === "manual" && <Badge tone="info">Manually added</Badge>}
              {assignedAgent && (
                <Badge tone="ok">
                  <IconUserCheck size={11} /> Assigned to {assignedAgent.name}
                </Badge>
              )}
              {source.bhk && (
                <span className="fact">
                  <IconBuilding size={12} />
                  {source.bhk}
                </span>
              )}
              {source.carpet_area_sqft !== null && (
                <span className="fact">
                  <IconRuler size={12} />
                  {formatCarpetArea(source.carpet_area_sqft, source.carpet_area_unit)}
                </span>
              )}
            </div>
          </div>
          <button type="button" className="toast__close" onClick={onClose} aria-label="Close">
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
                <div className="faint small row-flex" style={{ gap: 10, marginTop: 6, flexWrap: "wrap" }}>
                  {fieldScoreEntries.map(([name, value]) => (
                    <span key={name}>
                      {FIELD_SCORE_LABEL[name] ?? name}: {Math.round(value * 100)}%
                    </span>
                  ))}
                </div>
              )}
              <div className="faint small" style={{ marginTop: 6 }}>
                Data available for {Math.round(match.evidence_ratio * 100)}% of the fields compared.
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

            {property && (
              <div className="detail__block">
                <div className="detail__k">Price per unit</div>
                <div className="detail__v">
                  {property.price_per_unit_text ?? formatPricePerUnit(null, property.price_per_unit_amount_inr)}
                </div>
              </div>
            )}

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
                  {property.sender_saved_name && property.sender_saved_name !== property.sender_name && (
                    <span className="faint"> · saved as {property.sender_saved_name}</span>
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
              <div className="detail__v">{property?.description ?? match?.description}</div>
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
            <Button key={target} size="sm" icon={<IconMove size={14} />} busy={moving} onClick={() => onMove(target)}>
              Move to {CATEGORY_LABEL[target]}
            </Button>
          ))}
          {onRemoveManual && (
            <Button size="sm" variant="ghost" icon={<IconTrash size={14} />} busy={removing} onClick={onRemoveManual}>
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
