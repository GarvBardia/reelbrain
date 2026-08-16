"use client";

/**
 * Admin session handling — GitHub Pages edition.
 *
 * GitHub Pages serves static files with no server, so the earlier design
 * (browser holds nothing, a Next.js server proxy attaches the real secret)
 * is not achievable here: there is no server to be the proxy. The trade-off
 * this forces, stated plainly: the CAPTURE_SECRET now lives in the browser's
 * sessionStorage for the duration of an admin session, and is sent directly
 * to the FastAPI backend as the `x-admin-secret` header on every admin call.
 *
 * This is materially different from — and weaker than — an httpOnly cookie:
 * sessionStorage is readable by any JS running on the page, so a successful
 * XSS on this admin page could exfiltrate the secret. Mitigations actually
 * in place: sessionStorage clears when the tab closes (not persisted like
 * localStorage), the secret is never written into the JS bundle or any
 * static file, and this is a small hand-rolled admin surface with no
 * third-party scripts. For a security posture beyond that, host the admin
 * dashboard somewhere with a real server (Vercel, etc.) instead of GitHub
 * Pages, and keep the public marketing site here.
 */

const STORAGE_KEY = "mycelium_admin_secret";

export function getStoredSecret(): string | null {
  if (typeof window === "undefined") return null;
  return sessionStorage.getItem(STORAGE_KEY);
}

export function storeSecret(secret: string): void {
  sessionStorage.setItem(STORAGE_KEY, secret);
}

export function clearSecret(): void {
  sessionStorage.removeItem(STORAGE_KEY);
}
