from pydantic import BaseModel

# Sent to the BROKER who asked for something in a monitored requirement
# chat. Addressed to a peer, not a buyer: no "thanks for your enquiry", no
# coordinator, just the shortlist against what they asked for.
DEFAULT_REQUIREMENT_TEMPLATE = (
    "Hi {contact_name} 👋\n\n"
    "For your requirement — {requirement}\n"
    "Budget: {budget}\n"
    "Areas: {areas}\n\n"
    "{property_count} {property_word} that fit:\n\n"
    "{properties}\n\n"
    "Let me know which one to arrange a visit for.\n"
    "— {business_name}"
)

# Sent to the CLIENT whose inquiry we already hold requirements for. Same
# shortlist, different relationship — this one is the business writing to a
# customer, so it opens and closes like it.
DEFAULT_CLIENT_TEMPLATE = (
    "Hi {client_name} 👋\n\n"
    "Based on what you're looking for — {requirement}\n"
    "Budget: {budget}\n"
    "Areas: {areas}\n\n"
    "Here {property_word_is} {property_count} {property_word} you may like:\n\n"
    "{properties}\n\n"
    "Reply to this chat and we'll arrange a visit for whichever interests you.\n"
    "— {business_name}"
)


class PropertyShareTemplates(BaseModel):
    """The two "here are the properties" WhatsApp message templates the
    Settings page lets a real-estate client customize — one for a broker
    requirement, one for a client inquiry.

    Deliberately SEPARATE from Model/AgentManagementModel/
    handoff_templates.py rather than a third field on it. Those two are
    about handing a client to an agent (they name an agent, a phone number,
    a site visit); these two are about sending a property shortlist to the
    person who asked for it, and no agent is involved at any point. Sharing
    a model would mean one Settings section trying to explain four messages
    with two different token vocabularies.

    Like the hand-off templates, the backend only stores and returns the
    text — the tokens are filled in by the frontend
    (Frontend/src/lib/propertyShareTemplate.ts) right before sending, which
    is also what makes last-second editing in the send dialog possible:
    whatever text arrives at the send endpoint is what goes out, and the
    stored template is not touched by it.
    """

    requirement_template: str = DEFAULT_REQUIREMENT_TEMPLATE
    client_template: str = DEFAULT_CLIENT_TEMPLATE
