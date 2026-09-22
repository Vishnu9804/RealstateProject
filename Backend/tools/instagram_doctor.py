"""Read-only health check for the Instagram connection.

Run it whenever comments or shared reels are not being answered, BEFORE
changing any code — it separates "this application is broken" from "Meta is
not delivering the event", which look identical from the terminal and have
completely different fixes.

    cd Backend
    venv/Scripts/python.exe tools/instagram_doctor.py

Touches nothing: no messages are sent, no rows are written, no settings are
changed. It reads the stored token and asks Meta four questions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Database import settings_repository  # noqa: E402
from Service.InstagramInquiryHandlingService import (  # noqa: E402
    instagram_graph_client as graph,
    instagram_reel_matcher as matcher,
)
from Service.InstagramInquiryHandlingService.instagram_graph_client import InstagramApiError  # noqa: E402

stored = settings_repository.get_value("instagram_official_connection") or {}
token = stored.get("access_token")
if not token:
    raise SystemExit("Nothing is connected — connect Instagram on the Connection page first.")

print(f"Connected as @{stored.get('username')}  (ig_user_id={stored.get('ig_user_id')})\n")


def ask(label, path, params=None):
    try:
        return graph.graph_get(path, token=token, params=params)
    except InstagramApiError as exc:
        print(f"[FAIL] {label}: {exc}")
        return None


account = ask("account", "me", {"fields": "user_id,username,account_type"}) or {}
kind = account.get("account_type")
print(f"[{'OK  ' if kind in ('BUSINESS', 'MEDIA_CREATOR') else 'FAIL'}] Account type: {kind}")

subs = ask("subscription", "me/subscribed_apps") or {}
fields = sorted({f for row in subs.get("data", []) for f in row.get("subscribed_fields", [])})
missing = {"comments", "messages"} - set(fields)
print(f"[{'OK  ' if not missing else 'FAIL'}] Webhook fields Meta holds for this account: {fields or 'none'}"
      + (f"  MISSING: {sorted(missing)}" if missing else ""))

media = ask("media", "me/media", {"fields": "id,permalink", "limit": 25}) or {}
items = media.get("data", [])
print(f"[{'OK  ' if items else 'WARN'}] Posts readable through the API: {len(items)}")

# The question that actually matters day to day: can each reel-linked
# property still be resolved to one of this account's posts? A property
# whose reel lives on a DIFFERENT account can never be matched, and this is
# the only place that shows it.
print("\nReel-linked properties:")
by_code = {matcher.extract_reel_code(i.get("permalink")): i for i in items}
from Service.WhatsAppDataFetchingService import property_vector_store  # noqa: E402

linked = property_vector_store.get_reel_link_index()
if not linked:
    print("  (none — add an Instagram reel link to a property first)")
for record_id, url, _ in linked[:25]:
    code = matcher.extract_reel_code(url)
    hit = by_code.get(code)
    print(f"  [{'OK  ' if hit else 'FAIL'}] {record_id}: {code}"
          + (f" -> media {hit['id']}" if hit else "  NOT one of this account's recent posts"))

print(
    "\nIf every line above is OK but a real comment or shared reel still produces no\n"
    "'Instagram webhook received' line in the server terminal, the fault is Meta-side\n"
    "delivery, not this application. In that order, check:\n"
    "  1. the app is PUBLISHED (App Dashboard -> Publish). Meta's own webhooks panel\n"
    "     states webhooks require a published app.\n"
    "  2. the person commenting/sharing has ACCEPTED an Instagram Tester invite\n"
    "     (their Instagram -> Settings -> Apps and websites -> Tester invites).\n"
    "     Required for every non-role account until Advanced Access is granted.\n"
    "  3. Advanced Access for instagram_business_manage_messages and\n"
    "     instagram_business_manage_comments (App Review + Business Verification).\n"
    "     Until then ONLY tester accounts can trigger anything."
)
