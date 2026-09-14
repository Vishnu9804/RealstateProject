import { inquiryClientApi } from "../api/inquiryClientApi";
import type { InquiryClientRecord } from "../api/types";
import { LruCache } from "./lruCache";

/**
 * Client photos, fetched one client at a time and only when someone opens
 * that client's details or Edit dialog — the client list itself only says
 * whether there is one (InquiryClientRecord.has_photo). See Backend/
 * Database/client_models.py's ClientRow.photo_url for why the photo never
 * travels with the list.
 *
 * Keyed by phone AND updated_at: any change to a client — a new photo
 * included — moves updated_at, so a stale photo can never be served from
 * here; the key simply stops matching. A record without an updated_at (the
 * backend's in-memory fallback never stamps one) is never cached, for
 * exactly that reason.
 *
 * Bounded like lib/propertyDetailCache.ts, and concurrent opens of the same
 * photo (the details dialog and the Edit dialog, say) share one request.
 */
type PhotoOwner = Pick<InquiryClientRecord, "phone" | "updated_at" | "has_photo">;

const MAX_ENTRIES = 24;

const cache = new LruCache<string, string | null>(MAX_ENTRIES);
const inFlight = new Map<string, Promise<string | null>>();

function keyOf(client: PhotoOwner): string | null {
  return client.updated_at ? `${client.phone}|${client.updated_at}` : null;
}

/** The photo, `null` for "no photo", or `undefined` when it isn't known here
 *  yet and has to be fetched. */
export function getCachedClientPhoto(client: PhotoOwner): string | null | undefined {
  if (!client.has_photo) return null;
  const key = keyOf(client);
  return key ? cache.get(key) : undefined;
}

/** Called right after a save with the record the backend returned, so the
 *  details dialog shows the photo it was just given without asking for it. */
export function setCachedClientPhoto(client: PhotoOwner, photoUrl: string | null): void {
  const key = keyOf(client);
  if (key) cache.set(key, client.has_photo ? photoUrl : null);
}

export function loadClientPhoto(client: PhotoOwner): Promise<string | null> {
  const known = getCachedClientPhoto(client);
  if (known !== undefined) return Promise.resolve(known);
  const requestKey = keyOf(client) ?? `${client.phone}|unversioned`;
  const pending = inFlight.get(requestKey);
  if (pending) return pending;
  const request = inquiryClientApi
    .getClientPhoto(client.phone)
    .then(({ photo_url }) => {
      setCachedClientPhoto(client, photo_url);
      return photo_url;
    })
    .finally(() => inFlight.delete(requestKey));
  inFlight.set(requestKey, request);
  return request;
}
