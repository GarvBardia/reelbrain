import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { ADMIN_COOKIE, isValidSession } from "./admin-auth";
import { API_BASE } from "./api";

/**
 * The one place the backend secret is ever attached to a request.
 *
 * Every /api/admin/* handler goes through here, so there is a single spot to
 * audit for "does the browser ever see ADMIN_API_SECRET" -- and the answer is
 * no: it is read from the server environment, attached to an outbound
 * server-to-server request, and never included in any response body.
 *
 * Authorization is re-checked here rather than relying on middleware alone:
 * a function that forwards a real credential should not delegate its own
 * access control to something outside itself.
 */
export async function requireAdmin(): Promise<NextResponse | null> {
  const ok = await isValidSession(cookies().get(ADMIN_COOKIE)?.value);
  return ok ? null : NextResponse.json({ error: "unauthorized" }, { status: 401 });
}

function secret(): string {
  const value = process.env.ADMIN_API_SECRET;
  if (!value) throw new Error("ADMIN_API_SECRET is not configured");
  return value;
}

/** GET an admin endpoint on the FastAPI backend, secret attached as a header. */
export async function adminGet(path: string) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "x-admin-secret": secret() },
    cache: "no-store",   // admin views must never render a cached snapshot
  });
  const body = await res.json().catch(() => ({ error: "bad response from API" }));
  return NextResponse.json(body, { status: res.status });
}

/** POST to a backend endpoint whose body shape is `{secret, ...}`. */
export async function adminPost(path: string, payload: Record<string, unknown> = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ...payload, secret: secret() }),
    cache: "no-store",
  });
  const body = await res.json().catch(() => ({ error: "bad response from API" }));
  return NextResponse.json(body, { status: res.status });
}
