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
    default: "Mycelium — a self-organizing knowledge network",
    template: "%s · Mycelium",
  },
  description:
    "Mycelium turns scattered saved content into a self-organizing, self-improving knowledge network.",
  openGraph: {
    title: "Mycelium — a self-organizing knowledge network",
    description:
      "Mycelium turns scattered saved content into a self-organizing, self-improving knowledge network.",
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
