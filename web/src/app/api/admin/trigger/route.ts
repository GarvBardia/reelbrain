import { NextResponse } from "next/server";
import { adminPost, requireAdmin } from "@/lib/admin-proxy";

/** The pipeline jobs an admin can fire by hand. An allow-list, not a
 *  pass-through: without it this route would proxy an authenticated POST to
 *  ANY path on the backend that the caller named. */
const JOBS: Record<string, string> = {
  nightly: "/nightly",
  "daily-digest": "/daily-digest",
  "weekly-digest": "/weekly-digest",
};

export async function POST(req: Request) {
  const denied = await requireAdmin();
  if (denied) return denied;

  const { job } = await req.json().catch(() => ({ job: "" }));
  const path = JOBS[job];
  if (!path) {
    return NextResponse.json(
      { error: `unknown job: ${job}. Allowed: ${Object.keys(JOBS).join(", ")}` },
      { status: 400 },
    );
  }
  return adminPost(path);
}
