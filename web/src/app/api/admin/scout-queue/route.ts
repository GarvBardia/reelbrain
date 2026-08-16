import { adminGet, requireAdmin } from "@/lib/admin-proxy";

export async function GET() {
  // The UNREDACTED queue: includes the comment-gate keyword and the attached
  // resource URL, which the public /api/public/scout-queue strips.
  return (await requireAdmin()) ?? adminGet("/api/admin/scout-queue?limit=50");
}
