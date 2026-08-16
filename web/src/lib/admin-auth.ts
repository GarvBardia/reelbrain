/**
 * Admin session handling.
 *
 * THE RULE THIS FILE EXISTS TO ENFORCE: the browser never receives the
 * backend's API secret. The flow is
 *
 *   browser --(password)--> /api/admin/login   (Next.js server)
 *          <--(httpOnly session cookie)--
 *   browser --(cookie)-----> /api/admin/*      (Next.js server)
 *                            server attaches ADMIN_API_SECRET
 *                            server --> FastAPI on Render
 *
 * so `ADMIN_API_SECRET` lives only in Vercel's server environment. It is
 * deliberately NOT prefixed `NEXT_PUBLIC_` -- that prefix is what inlines a
 * value into the client bundle, and using it here would publish the secret to
 * anyone who opens devtools.
 *
 * The session cookie is an HMAC of a fixed string keyed by ADMIN_PASSWORD, so
 * it is unforgeable without the password and needs no session store (there is
 * exactly one admin). Web Crypto rather than node:crypto because middleware
 * runs on the Edge runtime, where node:crypto is unavailable.
 */

export const ADMIN_COOKIE = "mycelium_admin";
const SESSION_PAYLOAD = "mycelium-admin-session-v1";

async function hmac(key: string, message: string): Promise<string> {
  const enc = new TextEncoder();
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    enc.encode(key),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", cryptoKey, enc.encode(message));
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** The value stored in the session cookie for a valid login. */
export async function sessionToken(): Promise<string> {
  const password = process.env.ADMIN_PASSWORD;
  if (!password) throw new Error("ADMIN_PASSWORD is not configured");
  return hmac(password, SESSION_PAYLOAD);
}

/** Length-constant string compare -- avoids leaking the token via timing. */
function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function isValidSession(cookieValue: string | undefined): Promise<boolean> {
  if (!cookieValue || !process.env.ADMIN_PASSWORD) return false;
  try {
    return safeEqual(cookieValue, await sessionToken());
  } catch {
    return false;
  }
}

export async function isValidPassword(submitted: string): Promise<boolean> {
  const password = process.env.ADMIN_PASSWORD;
  if (!password || !submitted) return false;
  // Compared as HMACs rather than raw strings so the comparison is over
  // fixed-length values regardless of what was submitted.
  return safeEqual(await hmac(submitted, SESSION_PAYLOAD), await sessionToken());
}
