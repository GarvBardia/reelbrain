import { adminGet, requireAdmin } from "@/lib/admin-proxy";

export async function GET() {
  return (await requireAdmin()) ?? adminGet("/api/admin/overview");
}
