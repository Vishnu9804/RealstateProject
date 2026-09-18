/**
 * How many rows each list endpoint is asked for, in ONE place.
 *
 * WHY THIS EXISTS
 *
 * These numbers used to be a literal `500` repeated across a dozen files —
 * two API defaults, four page constants and five inline call sites. The
 * property table has since grown past that, and the effect was not a
 * truncated table with a warning on it: it was a Properties page whose
 * "Stored" tile read 500 out of 1663, whose Area filter only offered the
 * localities those 500 happened to cover, and whose matches dialogs quietly
 * dropped every card whose property sat outside the window (a matched
 * property that isn't in the fetched list has no record to render from).
 * Every count on that page is derived from the fetched list, so a limit
 * below the table size doesn't just hide rows — it makes every number on
 * the page wrong, in a way that looks like real data.
 *
 * WHY THESE VALUES ARE SAFE TO RAISE
 *
 * PROPERTY_FETCH_LIMIT and SOLDOUT_FETCH_LIMIT are matched to the backend's
 * own in-memory capacity for those lists (Backend/Service/
 * WhatsAppDataFetchingService/property_snapshot.py's _SNAPSHOT_LIMIT and
 * soldout_property_store's _CACHE_LIMIT, both 5000, and the controller
 * clamps to the same number). Both lists are served entirely from that
 * memory, so asking for all of them costs the database nothing whatsoever —
 * there is no per-row database cost to trade off here, only the one-off
 * transfer, which is gzipped, revalidated with an ETag and therefore only
 * actually sent when a property has changed since the browser last asked.
 *
 * CLIENT_FETCH_LIMIT is a real query, but it is fetched only when the
 * backend's clients_version says something changed (see InquiryClientsPage's
 * load()), and the row it returns carries neither the client's photo nor
 * their requirement vector (both deferred columns). It sat at 500 with 494
 * clients in the table — one more registration away from silently dropping
 * someone off the Inquiries page.
 */

/** The Properties list (GET /properties) — served from the backend's
 *  in-memory snapshot, so this costs no database work at any value up to
 *  the snapshot's own capacity. */
export const PROPERTY_FETCH_LIMIT = 5000;

/** The Sold out tab's own list — likewise served from that feature's
 *  in-memory cache, with the same 5000-row capacity. */
export const SOLDOUT_FETCH_LIMIT = 5000;

/** The Inquiries client list (GET /whatsapp-inquiry/clients). */
export const CLIENT_FETCH_LIMIT = 5000;
