import type { AgentSummary, InquiryClientRecord } from "../api/types";
import { formatCompactInr } from "./formatters";

export const BUSINESS_NAME = "Estate Signal";

/** The fields a hand-off message actually needs from a property, common to
 *  both a matched property (MatchedProperty) and a manually-added one
 *  (PropertyRecord) — both types already have every one of these fields
 *  with the same shape, so either can be passed here as-is. */
export interface HandoffPropertyLike {
  record_id: string;
  property_type: string | null;
  bhk: string | null;
  society_name: string | null;
  area_name: string | null;
  carpet_area_sqft: number | null;
  carpet_area_unit: string | null;
  contact_name: string | null;
  contact_phone: string | null;
}

/** One agent's slice of a hand-off: which properties (matched and/or
 *  manually-added, mixed freely) are assigned to them. */
export interface AgentAssignment {
  agent: AgentSummary;
  properties: HandoffPropertyLike[];
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

function describeProperty(property: HandoffPropertyLike, index: number): string[] {
  const location = [property.society_name, property.area_name].filter(Boolean).join(", ");
  const size = property.carpet_area_sqft ? `${Math.round(property.carpet_area_sqft)} ${property.carpet_area_unit ?? "sqft"}` : null;
  const details = [property.bhk, size].filter(Boolean).join(" · ");
  const lines = [`${index + 1}) ${propertyLabel(property)}${location ? `, ${location}` : ""}`];
  if (details) lines.push(`   ${details}`);
  if (property.contact_name || property.contact_phone) {
    lines.push(`   Listed by: ${[property.contact_name, property.contact_phone].filter(Boolean).join(" ")}`);
  }
  return lines;
}

/** Token values for the "message your agent receives" template — the
 *  client's requirement plus only the properties assigned to THIS agent
 *  (matched and manually-added properties are listed exactly the same
 *  way; an agent has no reason to care which source a property came
 *  from). */
export function buildAgentTokens(client: InquiryClientRecord, properties: HandoffPropertyLike[]): Record<string, string> {
  const requirement = [client.bhk, client.property_type].filter(Boolean).join(" ") || "Property";
  const purpose = client.purpose ? ` (${client.purpose})` : "";

  const propertyLines = properties.flatMap(describeProperty);

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
 */
export function buildClientMessage(client: InquiryClientRecord, assignments: AgentAssignment[], clientTemplate: string): string {
  if (assignments.length === 1) {
    const { agent, properties } = assignments[0];
    return renderTemplate(clientTemplate, buildClientTokens(client, agent, properties.length));
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
  lines.push("", "Each of them will call you shortly to fix a convenient time.", "", "You can reply to this chat any time to change your requirement.", `— ${BUSINESS_NAME}`);
  return lines.join("\n");
}
