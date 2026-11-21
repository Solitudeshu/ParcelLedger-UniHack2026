import canonicalize from "canonicalize";

export const SCHEMA_VERSION = "evidence-v1";
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export function normalize(value: Json): Json {
  if (typeof value === "string") return value.normalize("NFC");
  if (typeof value === "number" && !Number.isFinite(value)) {
    throw new Error("Non-finite numbers cannot be committed");
  }
  if (typeof value === "number" && Number.isInteger(value) && !Number.isSafeInteger(value)) {
    throw new Error("Use a decimal string for integers outside the safe JSON range");
  }
  if (Array.isArray(value)) return value.map(normalize);
  if (value !== null && typeof value === "object") {
    const output: Record<string, Json> = Object.create(null);
    for (const [key, item] of Object.entries(value)) {
      const normalizedKey = key.normalize("NFC");
      if (Object.hasOwn(output, normalizedKey)) {
        throw new Error("Duplicate key after Unicode normalization");
      }
      output[normalizedKey] = normalize(item);
    }
    return output;
  }
  return value;
}

export function canonicalBytes(document: Json): Uint8Array {
  const serialized = canonicalize(normalize(document));
  if (serialized === undefined) throw new Error("Invalid JSON document");
  return new TextEncoder().encode(serialized);
}

export async function documentHash(document: Json): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", canonicalBytes(document) as BufferSource);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
