/**
 * Turns a picked/dropped image File into a data URL the property record can
 * store directly (see PropertyImagesField and PropertyFormDialog). Resized
 * and re-encoded client-side first — a photo straight off a phone camera
 * can be several MB, and nothing downstream needs that: this keeps what
 * actually gets sent to the backend (and stored in the DB as JSON) to a
 * reasonable size no matter what the source file looked like.
 */

const MAX_DIMENSION = 1600;
const JPEG_QUALITY = 0.82;

/** A client's photo is only ever shown as a portrait-sized avatar or a
 *  small preview (see ClientFormDialog and ClientAvatar), never full-screen
 *  like a property's — so it is kept much smaller than a property photo. */
const CLIENT_PHOTO_MAX_DIMENSION = 800;

export function fileToPropertyImage(file: File): Promise<string> {
  return fileToResizedDataUrl(file, MAX_DIMENSION);
}

export function fileToClientPhoto(file: File): Promise<string> {
  return fileToResizedDataUrl(file, CLIENT_PHOTO_MAX_DIMENSION);
}

async function fileToResizedDataUrl(file: File, maxDimension: number): Promise<string> {
  try {
    const bitmap = await createImageBitmap(file);
    try {
      const scale = Math.min(1, maxDimension / Math.max(bitmap.width, bitmap.height));
      const width = Math.max(1, Math.round(bitmap.width * scale));
      const height = Math.max(1, Math.round(bitmap.height * scale));

      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("2D canvas context unavailable");
      ctx.drawImage(bitmap, 0, 0, width, height);

      return canvas.toDataURL("image/jpeg", JPEG_QUALITY);
    } finally {
      bitmap.close();
    }
  } catch {
    // The browser couldn't decode this file as an image (an unusual/older
    // format createImageBitmap doesn't support) — still accept it, just
    // unresized, rather than blocking the add. "Whatever format, no
    // problem" is the whole point of this field.
    return readFileAsDataUrl(file);
  }
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error ?? new Error("Could not read file"));
    reader.readAsDataURL(file);
  });
}
