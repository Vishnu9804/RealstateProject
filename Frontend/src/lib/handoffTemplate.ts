import type { AgentSummary, InquiryClientRecord } from "../api/types";
import { formatArea, formatCompactInr, formatVisitTime } from "./formatters";
import { formatPhoneList } from "./phone";

export const BUSINESS_NAME = "Estate Signal";

/** The fields a hand-off message actually needs from a property, common to
 *  both a matched property (MatchedProperty) and a manually-added one
 *  (PropertyRecord) — both types already have every one of these fields
 *  with the same shape, so either can be passed here as-is. */
export interface HandoffPropertyLike {
  record_id: string;
  property_type: string | null;
  bhk: string | null;
  unit_no: string | null;
  society_name: string | null;
  area_name: string | null;
  area_sqft: number | null;
  area_vaar: number | null;
  contact_name: string | null;
  /** Every number on the listing, so a share message carries all of them —
   *  see lib/phone.ts. The only contact-number field there is: the derived
   *  `contact_phone` scalar was removed from the API. */
  contact_phones?: string[] | null;
  /* No `location_url`, deliberately — this message is sent to a field agent
     over WhatsApp. See PropertyRecord.location_url. */
}

/** What the matches dialog's visit planner decided for one property on top
 *  of "which agent": when the visit is booked for, and whether it is a
 *  re-visit of a property this client has already seen. */
export interface VisitMeta {
  /** ISO instant; null = the operator skipped picking a time. */
  scheduledAt: string | null;
  /** 2 for the first re-visit, 3 for the one after…; null for a first visit. */
  revisitNumber: number | null;
}

/** One agent's slice of a hand-off: which properties (matched and/or
 *  manually-added, mixed freely) are assigned to them. */
export interface AgentAssignment {
  agent: AgentSummary;
  properties: HandoffPropertyLike[];
  /** Per-property visit details keyed by record_id. A property with no
   *  entry is a first visit with no time fixed — exactly what every
   *  hand-off looked like before the visit planner existed, so messages
   *  for it read exactly as they always did. */
  visitMeta?: Record<string, VisitMeta>;
}

/** Fills in `{token_name}` placeholders — any token not present in
 *  `tokens` is left exactly as written, so a custom template with a typo'd
 *  or removed token degrades gracefully instead of throwing. */
export function renderTemplate(template: string, tokens: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) => (key in tokens ? tokens[key] : match));
}

function budgetRange(min: number | null, max: number | null): string {
  if (min === null && max === null) return "Not specified";
  if (min !== null && max !== null) return `₹${formatCompactInr(min)} – ${formatCompactInr(max)}`;
  if (min !== null) return `₹${formatCompactInr(min)}+`;
  return `Up to ₹${formatCompactInr(max as number)}`;
}

function firstName(name: string): string {
  return name.trim().split(/\s+/)[0] ?? name;
}

/** The one display string used everywhere a property needs a short name —
 *  the hand-off message, the assignment record sent to the backend
 *  (Backend/Model/AgentManagementModel/assignment_record.py's snapshot),
 *  the Agents page's active-visit list. */
export function propertyLabel(property: HandoffPropertyLike): string {
  return property.society_name || property.property_type || "Property";
}

function describeProperty(property: HandoffPropertyLike, index: number, meta: VisitMeta | undefined): string[] {
  // The AREA only. `propertyLabel` on the next line already IS the society
  // name whenever there is one, so including it here printed it twice —
  // "1) Shreepal Residancy, Shreepal Residancy, Pal". Nothing is lost by
  // dropping it: when there is no society name the label falls back to the
  // property type, and the area is exactly what should follow it.
  const location = [property.area_name].filter(Boolean).join(", ");
  const size = formatArea(property.area_sqft, property.area_vaar);
  const details = [property.unit_no && `Unit ${property.unit_no}`, property.bhk, size === "—" ? null : size]
    .filter(Boolean)
    .join(" · ");
  const lines = [`${index + 1}) ${propertyLabel(property)}${location ? `, ${location}` : ""}`];
  if (details) lines.push(`   ${details}`);
  const numbers = formatPhoneList(property);
  if (property.contact_name || numbers) {
    lines.push(`   Listed by: ${[property.contact_name, numbers].filter(Boolean).join(" ")}`);
  }
  // Carried inside {matches} rather than as tokens of their own, so a
  // template customized on the Settings page before these existed still
  // tells the agent when to be there and that it is a re-visit.
  if (meta?.revisitNumber) lines.push(`   🔁 Re-visit (visit #${meta.revisitNumber})`);
  if (meta?.scheduledAt) lines.push(`   📅 Visit: ${formatVisitTime(meta.scheduledAt)}`);
  return lines;
}

/** Token values for the "message your agent receives" template — the
 *  client's requirement plus only the properties assigned to THIS agent
 *  (matched and manually-added properties are listed exactly the same
 *  way; an agent has no reason to care which source a property came
 *  from). */
export function buildAgentTokens(
  client: InquiryClientRecord,
  properties: HandoffPropertyLike[],
  visitMeta?: Record<string, VisitMeta>,
): Record<string, string> {
  const requirement = [client.bhk, client.property_type].filter(Boolean).join(" ") || "Property";
  const purpose = client.purpose ? ` (${client.purpose})` : "";

  const propertyLines = properties.flatMap((property, index) => describeProperty(property, index, visitMeta?.[property.record_id]));

  return {
    client_name: client.name || "Unnamed",
    client_phone: client.phone,
    requirement: `${requirement}${purpose}`,
    budget: budgetRange(client.budget_min_inr, client.budget_max_inr),
    areas: client.preferred_areas || "Not specified",
    notes_line: client.additional_requirements ? `Note: ${client.additional_requirements}\n` : "",
    match_count: String(properties.length),
    matches: propertyLines.length > 0 ? propertyLines.join("\n") : "No properties selected.",
  };
}

/** Token values for the "message your client receives" template — only
 *  used for the single-agent case (see buildClientMessage below for why
 *  more than one agent takes a different path entirely). */
export function buildClientTokens(client: InquiryClientRecord, agent: AgentSummary, propertyCount: number): Record<string, string> {
  return {
    client_name: client.name || "there",
    business_name: BUSINESS_NAME,
    agent_name: agent.name,
    agent_phone: agent.phone,
    agent_first_name: firstName(agent.name),
    match_count: String(propertyCount),
    property_word: propertyCount === 1 ? "property" : "properties",
    areas: client.preferred_areas || "your preferred areas",
  };
}

/** "📅 Site visit schedule:" plus one line per property that has a booked
 *  time or is a re-visit — empty when none do, so a hand-off with no times
 *  and no re-visits produces exactly the message it always did. */
function scheduleLines(assignments: AgentAssignment[]): string[] {
  const lines: string[] = [];
  for (const { properties, visitMeta } of assignments) {
    for (const property of properties) {
      const meta = visitMeta?.[property.record_id];
      if (!meta || (!meta.scheduledAt && !meta.revisitNumber)) continue;
      const when = meta.scheduledAt ? formatVisitTime(meta.scheduledAt) : "time to be confirmed";
      lines.push(`• ${propertyLabel(property)}${meta.revisitNumber ? " (re-visit)" : ""} — ${when}`);
    }
  }
  return lines.length > 0 ? ["📅 Site visit schedule:", ...lines] : [];
}

/**
 * Builds the one message the CLIENT receives, covering every agent
 * involved in this hand-off round. The customizable Settings-page
 * template (agent_name/agent_phone are single tokens) only has a shape
 * for exactly one coordinator, which is the overwhelmingly common case —
 * so that's rendered through the template unchanged. When properties were
 * split across more than one agent (e.g. one matched property assigned to
 * agent A, a manually-added one to agent B), there's no single
 * "{agent_name}" to substitute, so this composes a plain, clear message
 * naming each agent and what they're handling instead of stretching the
 * single-agent template to fit a shape it wasn't designed for.
 *
 * Booked visit times are appended as their own short block rather than
 * being a template token, for the same reason as describeProperty's visit
 * lines: a template saved before times existed must still carry them.
 */
export function buildClientMessage(client: InquiryClientRecord, assignments: AgentAssignment[], clientTemplate: string): string {
  const schedule = scheduleLines(assignments);

  if (assignments.length === 1) {
    const { agent, properties } = assignments[0];
    const rendered = renderTemplate(clientTemplate, buildClientTokens(client, agent, properties.length));
    return schedule.length > 0 ? `${rendered}\n\n${schedule.join("\n")}` : rendered;
  }

  const totalProperties = assignments.reduce((sum, a) => sum + a.properties.length, 0);
  const lines = [
    `Hi ${client.name || "there"} 👋`,
    "",
    `Thanks for sharing your requirement with ${BUSINESS_NAME}.`,
    "",
    `Your requirement and ${totalProperties} shortlisted ${totalProperties === 1 ? "property" : "properties"} are being handled by ${assignments.length} coordinators:`,
    "",
  ];
  assignments.forEach(({ agent, properties }) => {
    lines.push(`👤 ${agent.name} (📞 ${agent.phone}) — ${properties.map(propertyLabel).join(", ")}`);
  });
  if (schedule.length > 0) lines.push("", ...schedule);
  lines.push("", "Each of them will call you shortly to fix a convenient time.", "", "You can reply to this chat any time to change your requirement.", `— ${BUSINESS_NAME}`);
  return lines.join("\n");
}

/** Everything the "tell them about the visit time" messages need. */
export interface VisitTimeMessageInput {
  clientName: string | null;
  clientPhone: string;
  agentName: string;
  agentPhone: string;
  propertyLabel: string;
  scheduledAt: string;
  /** The time this replaces; null when the visit had no time before. */
  previousScheduledAt: string | null;
  revisitNumber: number | null;
}

/**
 * The two messages offered right after a visit time is set or changed on
 * the matches dialog's Assigned tab — one for the agent, one for the
 * client. Plain composed text rather than a Settings template: they are
 * short, factual, and always shown in an editable box before anything is
 * sent, so the operator adjusts the wording right there when they need to.
 */
export function buildVisitTimeMessages(input: VisitTimeMessageInput): { agent: string; client: string } {
  const when = formatVisitTime(input.scheduledAt);
  const earlier = input.previousScheduledAt ? formatVisitTime(input.previousScheduledAt) : null;
  const visitWord = input.revisitNumber ? `re-visit (visit #${input.revisitNumber})` : "site visit";

  const agent = [
    earlier ? "🔁 Site visit rescheduled" : "📅 Site visit time fixed",
    "",
    `Client: ${input.clientName || "Unnamed"}`,
    `📞 ${input.clientPhone}`,
    `Property: ${input.propertyLabel}${input.revisitNumber ? ` — re-visit (visit #${input.revisitNumber})` : ""}`,
    `When: ${when}`,
    ...(earlier ? [`Earlier time: ${earlier}`] : []),
    "",
    earlier ? "Please plan for the new time." : "Please be there on time and take the client around.",
  ].join("\n");

  const client = [
    `Hi ${input.clientName || "there"} 👋`,
    "",
    earlier
      ? `Your ${visitWord} for ${input.propertyLabel} has been moved to:`
      : `Your ${visitWord} for ${input.propertyLabel} is fixed for:`,
    `📅 ${when}`,
    "",
    `${input.agentName} (📞 ${input.agentPhone}) will take you around.`,
    "",
    "Reply to this chat any time if you need to change it.",
    `— ${BUSINESS_NAME}`,
  ].join("\n");

  return { agent, client };
}
