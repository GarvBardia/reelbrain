import { NextResponse, type NextRequest } from "next/server";
import { ADMIN_COOKIE, isValidSession } from "@/lib/admin-auth";

/**
 * Gate for the admin dashboard. Runs before the page renders, so an
 * unauthenticated visitor never receives the dashboard HTML at all -- as
 * opposed to a client-side redirect, which ships the markup first and hides it
 * afterwards.
 *
 * The /api/admin/* handlers re-check the session themselves rather than
 * trusting this: middleware protects the route, but a proxy handler that
 * forwards a real secret to the backend should not depend on something outside
 * itself for authorization.
 */
export async function middleware(req: NextRequest) {
  const ok = await isValidSession(req.cookies.get(ADMIN_COOKIE)?.value);
  if (ok) return NextResponse.next();

  const url = req.nextUrl.clone();
  url.pathname = "/admin";
  url.searchParams.set("next", req.nextUrl.pathname);
  return NextResponse.redirect(url);
}

export const config = { matcher: ["/admin/dashboard/:path*"] };
