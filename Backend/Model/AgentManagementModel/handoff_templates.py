from pydantic import BaseModel

DEFAULT_AGENT_TEMPLATE = (
    "⚠️ New site visit assigned\n\n"
    "Client: {client_name}\n"
    "📞 {client_phone}\n"
    "Looking for: {requirement}\n"
    "Budget: {budget}\n"
    "Preferred: {areas}\n"
    "{notes_line}"
    "\nMatching properties ({match_count}):\n{matches}"
)

DEFAULT_CLIENT_TEMPLATE = (
    "Hi {client_name} 👋\n\n"
    "Thanks for sharing your requirement with {business_name}.\n\n"
    "Your site visit coordinator:\n"
    "👤 {agent_name}\n"
    "📞 {agent_phone}\n\n"
    "Your requirement and {match_count} shortlisted {property_word} in {areas} are already with "
    "{agent_first_name}, who will call you shortly to fix a convenient time.\n\n"
    "You can reply to this chat any time to change your requirement.\n"
    "— {business_name}"
)


class HandoffTemplates(BaseModel):
    """The two WhatsApp hand-off message templates the Settings page lets a
    real-estate client customize — placeholder tokens (e.g. "{client_name}")
    are filled in by the frontend (see Frontend/src/lib/handoffTemplate.ts)
    right before "Send both on WhatsApp" in HandoffDialog.tsx. The backend
    only stores and returns the template text; it never renders it."""

    agent_template: str = DEFAULT_AGENT_TEMPLATE
    client_template: str = DEFAULT_CLIENT_TEMPLATE
