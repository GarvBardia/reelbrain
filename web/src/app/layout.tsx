import type { Metadata } from "next";
import { Manrope } from "next/font/google";

import "./globals.css";
import { SiteHeader } from "@/components/site-header";
import { SiteFooter } from "@/components/site-footer";

// Manrope, not Inter: same "just works everywhere" reliability as a next/font
// Google font, but with real character in the display weights -- Inter at
// default settings is the single most common tell that a UI was AI-generated
// rather than designed (redesign-existing-projects audit, 2026-08-19).
const manrope = Manrope({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: {
    default: "Mycelium — saved reels, turned into a knowledge base",
    template: "%s · Mycelium",
  },
  description:
    "An automation pipeline that turns saved Instagram reels into structured notes: fetched, transcribed and summarized by AI, stored in Notion, and published as a browsable graph and library.",
  openGraph: {
    title: "Mycelium — saved reels, turned into a knowledge base",
    description:
      "An automation pipeline that turns saved Instagram reels into structured notes: fetched, transcribed and summarized by AI, stored in Notion, and published as a browsable graph and library.",
    type: "website",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={manrope.variable}>
      <body className="flex min-h-screen flex-col bg-white">
        <SiteHeader />
        <main className="flex-1">{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}
