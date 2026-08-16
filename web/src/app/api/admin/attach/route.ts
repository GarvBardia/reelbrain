import { NextResponse } from "next/server";
import { adminPost, requireAdmin } from "@/lib/admin-proxy";

/**
 * Resource attach: the step where a DM'd link is matched back to the save
 * whose comment gate earned it.
 *
 * `/attach` answers with either a confirmation or a disambiguation menu, so
 * this route supports both stages -- `confirm: true` routes to
 * /attach/confirm, which commits one specific choice.
 *
 * Field names are forwarded EXACTLY as app/models.py declares them
 * (`shortcode_or_note`, not a friendlier alias): both request models set
 * `extra="forbid"`, so any renamed or additional key is a 422 rather than a
 * silently-ignored field.
 */
export async function POST(req: Request) {
  const denied = await requireAdmin();
  if (denied) return denied;

  const body = await req.json().catch(() => null);
  if (!body?.resource_url) {
    return NextResponse.json({ error: "resource_url is required" }, { status: 400 });
  }

  if (body.confirm) {
    if (!body.shortcode) {
      return NextResponse.json(
        { error: "shortcode is required when confirming" },
        { status: 400 },
      );
    }
    return adminPost("/attach/confirm", {
      shortcode: body.shortcode,
      resource_url: body.resource_url,
    });
  }

  return adminPost("/attach", {
    resource_url: body.resource_url,
    ...(body.shortcode_or_note ? { shortcode_or_note: body.shortcode_or_note } : {}),
  });
}
