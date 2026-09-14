import { useEffect, useState } from "react";
import type { InquiryClientRecord } from "../api/types";
import { getCachedClientPhoto, loadClientPhoto } from "../lib/clientPhotoCache";
import { Avatar } from "./ui/Primitives";

/**
 * A client's photo where staff added one, their initials where they didn't
 * — the Inquiries page's client details header. The photo is fetched on its
 * own, once, through lib/clientPhotoCache.ts, so reopening the same client
 * (or opening them right after a save) costs no request at all.
 */
export default function ClientAvatar({ client, size = 56 }: { client: InquiryClientRecord; size?: number }) {
  const [photo, setPhoto] = useState<string | null>(() => getCachedClientPhoto(client) ?? null);
  const [failed, setFailed] = useState(false);
  const { phone, updated_at: updatedAt, has_photo: hasPhoto } = client;

  useEffect(() => {
    setFailed(false);
    const owner = { phone, updated_at: updatedAt, has_photo: hasPhoto };
    const known = getCachedClientPhoto(owner);
    if (known !== undefined) {
      setPhoto(known);
      return;
    }
    let cancelled = false;
    loadClientPhoto(owner)
      .then((url) => {
        if (!cancelled) setPhoto(url);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [phone, updatedAt, hasPhoto]);

  const label = client.name || client.phone;
  const style = { width: size, height: size };
  if (photo) return <img className="client-avatar" src={photo} alt={`Photo of ${label}`} style={style} />;
  if (hasPhoto && !failed) {
    return (
      <span className="client-avatar" style={style} aria-label="Loading photo">
        <span className="spinner" />
      </span>
    );
  }
  return <Avatar name={label} size={size} />;
}
