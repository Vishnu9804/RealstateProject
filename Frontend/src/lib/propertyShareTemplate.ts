import type { BrokerRequirementRecord, InquiryClientRecord, MatchedProperty, PropertyRecord } from "../api/types";
import { formatCompactInr } from "./formatters";
import { BUSINESS_NAME, renderTemplate } from "./handoffTemplate";

/**
 * Fills in the two "here are the properties" message templates (see
 * Backend/Model/PropertySharingModel/property_share_templates.py) with real
 * data, right before a send dialog shows the message for approval.
 *
 * Deliberately a sibling of lib/handoffTemplate.ts rather than an addition
 * to it: a hand-off message is about an AGENT taking a client on a site
 * visit, while these two are about sending a shortlist to whoever asked for
 * it, with no agent involved anywhere. The one thing they genuinely share —
 * `renderTemplate`'s "leave an unknown {token} exactly as written" rule, so
 * a typo'd custom template degrades instead of throwing — is imported from
 * there rather than reimplemented.
 *
 * The property lines here are fuller than the hand-off's: a broker or a
 * client reading this needs the PRICE and the listing type to decide
 * anything, whereas an agent being briefed already has the client's budget
 * in the same message.
 */

/** The fields a share message needs from a property. Both MatchedProperty
 *  and PropertyRecord already carry every one of these with the same shape,
 *  so either can be passed as-is — the same arrangement
 *  HandoffPropertyLike has. */
export interface SharePropertyLike {
  record_id: string;
  property_type: string | null;
  bhk: string | null;
  society_name: string | null;
  area_name: string | null;
  address: string | null;
  price_text: string | null;
  price_amount_inr: number | null;
  listing_type: "Sale" | "Rent";
  carpet_area_sqft: number | null;
  carpet_area_unit: string | null;
  contact_name: string | null;
  contact_phone: string | null;
}

export function shareBudgetRange(min: number | null, max: number | null): string {
  if (min === null && max === null) return "Not specified";
  if (min !== null && max !== null)
    return min === max ? `₹${formatCompactInr(min)}` : `₹${formatCompactInr(min)} – ${formatCompactInr(max)}`;
  if (min !== null) return `₹${formatCompactInr(min)}+`;
  return `Up to ₹${formatCompactInr(max as number)}`;
}

/** The price as the recipient should read it: the parsed amount when we
 *  have one (comparable, and free of a broker's own shorthand), otherwise
 *  the wording exactly as it was written. Same priority as the Properties
 *  page's own formatPrice. */
function priceLine(property: SharePropertyLike): string | null {
  if (property.price_amount_inr !== null) return `₹${formatCompactInr(property.price_amount_inr)}`;
  return property.price_text;
}

function describeProperty(property: SharePropertyLike, index: number): string[] {
  const location = [property.society_name, property.area_name].filter(Boolean).join(", ");
  const title = property.society_name || property.property_type || "Property";
  const size = property.carpet_area_sqft
    ? `${Math.round(property.carpet_area_sqft)} ${property.carpet_area_unit ?? "sqft"}`
    : null;

  const lines = [`${index + 1}) ${title}${location && location !== title ? `, ${location}` : ""}`];
  const spec = [property.bhk, property.property_type, size].filter(Boolean).join(" · ");
  if (spec) lines.push(`   ${spec}`);
  const price = priceLine(property);
  if (price) lines.push(`   ${price} (${property.listing_type === "Rent" ? "Rent" : "Sale"})`);
  if (property.address && property.address !== location) lines.push(`   ${property.address}`);
  if (property.contact_name || property.contact_phone) {
    lines.push(`   Contact: ${[property.contact_name, property.contact_phone].filter(Boolean).join(" ")}`);
  }
  return lines;
}

/** Tokens every share message has, whichever side it is going to — the
 *  shortlist itself and the three ways of counting it. */
function propertyTokens(properties: SharePropertyLike[]): Record<string, string> {
  const lines = properties.flatMap(describeProperty);
  return {
    property_count: String(properties.length),
    property_word: properties.length === 1 ? "property" : "properties",
    // So a template can read naturally either way ("here IS 1 property" /
    // "here ARE 3 properties") without the author having to pick one.
    property_word_is: properties.length === 1 ? "is" : "are",
    properties: lines.length > 0 ? lines.join("\n") : "No properties selected.",
    business_name: BUSINESS_NAME,
  };
}

/** Token values for the message the BROKER who raised a requirement
 *  receives. `contact_name` prefers the name stated inside the requirement
 *  itself, then the name WhatsApp has saved for them, then whatever
 *  WhatsApp reported — the same order the backend resolves the recipient's
 *  display name in (property_share_service.get_requirement_target). */
export function buildRequirementShareTokens(
  requirement: BrokerRequirementRecord,
  properties: SharePropertyLike[],
): Record<string, string> {
  const areas =
    requirement.preferred_areas.length > 0
      ? requirement.preferred_areas.join(", ")
      : requirement.area_name || "Not specified";
  const wanted = [requirement.bhk, requirement.requirement_type].filter(Boolean).join(" ") || "Property";
  const purpose = requirement.listing_type === "Rent" ? "on rent" : "for sale";

  return {
    ...propertyTokens(properties),
    contact_name: requirement.contact_name || requirement.sender_saved_name || requirement.sender_name || "there",
    contact_phone: requirement.sender_phone,
    requirement: `${wanted} ${purpose}`,
    budget: shareBudgetRange(requirement.budget_min_inr, requirement.budget_max_inr),
    areas,
  };
}

/** Token values for the message a CLIENT receives for their own inquiry.
 *  Reads the client's stored requirements, which is what those properties
 *  were matched against in the first place. */
export function buildClientShareTokens(
  client: InquiryClientRecord,
  properties: SharePropertyLike[],
): Record<string, string> {
  const wanted = [client.bhk, client.property_type].filter(Boolean).join(" ") || "Property";
  const purpose = client.purpose ? ` (${client.purpose})` : "";

  return {
    ...propertyTokens(properties),
    client_name: client.name || "there",
    client_phone: client.phone,
    requirement: `${wanted}${purpose}`,
    budget: shareBudgetRange(client.budget_min_inr, client.budget_max_inr),
    areas: client.preferred_areas || "Not specified",
  };
}

export function buildRequirementShareMessage(
  template: string,
  requirement: BrokerRequirementRecord,
  properties: SharePropertyLike[],
): string {
  return renderTemplate(template, buildRequirementShareTokens(requirement, properties));
}

export function buildClientShareMessage(
  template: string,
  client: InquiryClientRecord,
  properties: SharePropertyLike[],
): string {
  return renderTemplate(template, buildClientShareTokens(client, properties));
}

/** Widens a matched property or a full property record to the share shape.
 *  Both already satisfy it structurally; this exists so call sites read as
 *  a deliberate conversion rather than a bare cast. */
export function asShareProperty(property: MatchedProperty | PropertyRecord): SharePropertyLike {
  return property;
}
