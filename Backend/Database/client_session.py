"""Session helpers for whatsappInquiryHandling's client-records tables
(Database/client_models.py, Database/client_property_match_models.py).

These used to live in their own engine/session pointed at a separate
`CLIENT_DATABASE_URL` — in practice that was always set to the exact same
connection string as `DATABASE_URL` (this project uses one physical Neon
database for everything), so keeping a second variable and a second engine
only invited the two to drift apart and, worse, meant two independent
connections could race to create the same pgvector extension at startup
(see Database/session.py's init_db() docstring for that incident). This
module now just re-exports Database/session.py's shared engine/session
under names that read naturally at each client-repository call site
(`get_client_session()` inside Database/client_repository.py etc.) — there
is exactly one engine and one place tables get created in the whole
project.
"""

from __future__ import annotations

from Database.session import get_session, is_database_configured

is_client_database_configured = is_database_configured
get_client_session = get_session
