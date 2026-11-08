/**
 * A random UUID v4 for an export's client_request_id.
 *
 * crypto.randomUUID only exists in secure contexts (HTTPS or localhost);
 * crypto.getRandomValues exists everywhere Web Crypto does, so it is the
 * fallback. Throws when the browser has neither, rather than inventing a
 * weak id.
 */
export function newRequestId(source: Partial<Crypto> | undefined = globalThis.crypto): string {
  const randomUUID = source?.randomUUID;
  if (typeof randomUUID === "function") {
    return randomUUID.call(source);
  }
  const getRandomValues = source?.getRandomValues;
  if (typeof getRandomValues !== "function") {
    throw new Error("This browser cannot generate a request id.");
  }
  // getRandomValues fills the array it is given, in place (its return value
  // is that same array). Using our own Uint8Array keeps the bytes' type
  // exact: calling the generic method through .call() loses its type
  // parameter, so its return type would be a union of all typed arrays.
  const bytes = new Uint8Array(16);
  getRandomValues.call(source, bytes);
  bytes[6] =(bytes[6]! & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8]! & 0x3f) | 0x80; // RFC 4122 variant
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
