"use client";

import { API_BASE } from "./api";
import { getStoredSecret } from "./admin-auth";

/**
 * Every admin call, direct to the FastAPI backend (see src/lib/admin-auth.ts
 * for why there is no server-side proxy on GitHub Pages). The real
 * authorization is the backend's own constant-time secret comparison
 * (app/main.py's `_check_secret`) -- this module just attaches whatever is
 * currently in sessionStorage and lets the backend say yes or no.
 */

export class AdminUnauthorized extends Error {}

async function adminFetch(path: string, init: RequestInit = {}) {
  const secret = getStoredSecret();
  if (!secret) throw new AdminUnauthorized("no admin secret in this session");

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), "x-admin-secret": secret },
  });
  if (res.status === 401) throw new AdminUnauthorized("invalid or expired admin secret");
  return res;
}

export async function verifyAdminSecret(secret: string): Promise<boolean> {
  const res = await fetch(`${API_BASE}/api/admin/overview`, {
    headers: { "x-admin-secret": secret },
  });
  return res.ok;
}

export async function fetchAdminOverview() {
  const res = await adminFetch("/api/admin/overview");
  return res.json();
}

export async function fetchAdminScoutQueue() {
  const res = await adminFetch("/api/admin/scout-queue");
  return res.json();
}

/** The pipeline jobs an admin can fire by hand. POSTs straight to the
 *  backend's own trigger endpoints, each of which expects `{secret}` in the
 *  JSON body (not the header) -- matching app/main.py's existing
 *  NightlyRequest-shaped endpoints, unchanged from before this ever had a
 *  Next.js proxy in front of it. */
const JOBS: Record<string, string> = {
  nightly: "/nightly",
  "daily-digest": "/daily-digest",
  "weekly-digest": "/weekly-digest",
};

export async function triggerJob(job: string) {
  const path = JOBS[job];
  if (!path) throw new Error(`unknown job: ${job}`);
  const secret = getStoredSecret();
  if (!secret) throw new AdminUnauthorized("no admin secret in this session");

  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ secret }),
  });
  if (res.status === 401) throw new AdminUnauthorized("invalid or expired admin secret");
  return res.json();
}

/** Resource attach: `/attach` answers with either a confirmation or a
 *  disambiguation menu; `/attach/confirm` commits one specific choice. Field
 *  names match app/models.py's AttachRequest/AttachConfirmRequest exactly
 *  (both set extra="forbid", so a renamed field is a 422, not silently
 *  ignored). */
export async function attachResource(payload: {
  resource_url: string;
  shortcode_or_note?: string;
}) {
  const secret = getStoredSecret();
  if (!secret) throw new AdminUnauthorized("no admin secret in this session");

  const res = await fetch(`${API_BASE}/attach`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...payload, secret }),
  });
  if (res.status === 401) throw new AdminUnauthorized("invalid or expired admin secret");
  return res.json();
}

export async function confirmAttach(payload: { shortcode: string; resource_url: string }) {
  const secret = getStoredSecret();
  if (!secret) throw new AdminUnauthorized("no admin secret in this session");

  const res = await fetch(`${API_BASE}/attach/confirm`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...payload, secret }),
  });
  if (res.status === 401) throw new AdminUnauthorized("invalid or expired admin secret");
  return res.json();
}
