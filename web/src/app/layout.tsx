import type { Metadata } from "next";
import { Inter } from "next/font/google";

import "./globals.css";
import { SiteHeader } from "@/components/site-header";
import { SiteFooter } from "@/components/site-footer";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

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
    <html lang="en" className={inter.variable}>
      <body className="flex min-h-screen flex-col bg-white">
        <SiteHeader />
        <main className="flex-1">{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}
