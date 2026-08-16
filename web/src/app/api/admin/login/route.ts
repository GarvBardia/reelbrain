import { NextResponse } from "next/server";
import { ADMIN_COOKIE, isValidPassword, sessionToken } from "@/lib/admin-auth";

export async function POST(req: Request) {
  const { password } = await req.json().catch(() => ({ password: "" }));

  if (!process.env.ADMIN_PASSWORD) {
    return NextResponse.json(
      { error: "ADMIN_PASSWORD is not configured on the server." },
      { status: 500 },
    );
  }
  if (!(await isValidPassword(password ?? ""))) {
    // Deliberately vague: distinguishing "wrong password" from "no such user"
    // would be pointless here (there is one admin) and only helps an attacker.
    return NextResponse.json({ error: "Incorrect password." }, { status: 401 });
  }

  const res = NextResponse.json({ ok: true });
  res.cookies.set(ADMIN_COOKIE, await sessionToken(), {
    httpOnly: true,               // unreadable from JS, so XSS cannot exfiltrate it
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 12,
  });
  return res;
}
