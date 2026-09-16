"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, X } from "lucide-react";

import { cn } from "@/lib/utils";

const NAV = [
  { href: "/how-it-works", label: "How it works" },
  { href: "/library", label: "Library" },
  { href: "/scout", label: "Scout queue" },
];

/**
 * MOBILE MENU (2026-09-XX). This header used to render all three links
 * unconditionally in a single row -- fine at desktop widths, but a mobile
 * audit found it genuinely cramped at 375px: "How it works" and "Scout
 * queue" wrap to two lines while "Library" doesn't (an uneven, unpolished
 * wrap, not an overlap), and there is a literal 0px gap between the
 * "Mycelium" wordmark and the first link. No hamburger pattern existed at
 * all -- every link just squeezed into whatever width it got.
 *
 * `md:` (768px) is the breakpoint, matching MIN_WIDTH in
 * components/graph-sphere/can-render.ts -- one "mobile mode" threshold
 * across the site rather than a second, arbitrary nav-only cutoff. At 768px the audit found the
 * plain row already comfortable, so nothing below needed tightening further.
 *
 * Hand-rolled rather than pulling in a Sheet/Dialog primitive: this project
 * has no Radix/shadcn dialog component installed (only Badge/Button/Card/
 * Input exist under components/ui), and ReelDetail's own modal is already
 * hand-rolled for the same reason -- adding a new UI dependency for one
 * dropdown is a worse trade than a few dozen lines of plain state.
 */
export function SiteHeader() {
  // basePath (/reelbrain on the deployed GitHub Pages build) is stripped from
  // what usePathname reports, so comparing against the plain route is correct
  // in both dev and production -- no need to know the basePath here at all.
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  // Close on route change -- otherwise the panel stays open behind the new
  // page after tapping a link, which is exactly the kind of "still there"
  // menu that makes a mobile nav feel broken even though the navigation
  // itself worked.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Escape closes it, same as the reel detail modal -- one consistent
  // "Escape dismisses the thing on top" rule across the site rather than a
  // menu that only closes via its own toggle button.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header className="sticky top-0 z-50 border-b border-slate-200/70 bg-white/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2.5" onClick={() => setOpen(false)}>
          <MyceliumMark />
          <span className="text-[17px] font-semibold tracking-tight text-slate-900">Mycelium</span>
        </Link>

        {/* Desktop/tablet: the plain row, unchanged. */}
        <nav className="hidden items-center gap-1 md:flex">
          {NAV.map((item) => (
            <NavLink key={item.href} item={item} pathname={pathname} />
          ))}
        </nav>

        {/* Mobile: a hamburger toggling the panel below, in place of the row. */}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls="mobile-nav-panel"
          aria-label={open ? "Close menu" : "Open menu"}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-600 hover:bg-slate-100 hover:text-slate-900 md:hidden"
        >
          {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </div>

      {open && (
        <nav
          id="mobile-nav-panel"
          className="border-t border-slate-200/70 bg-white px-6 py-3 md:hidden"
        >
          <div className="flex flex-col gap-1">
            {NAV.map((item) => (
              <NavLink key={item.href} item={item} pathname={pathname} block />
            ))}
          </div>
        </nav>
      )}
    </header>
  );
}

function NavLink({
  item,
  pathname,
  block = false,
}: {
  item: (typeof NAV)[number];
  pathname: string | null;
  /** Full-width stacked style for the mobile panel vs. the desktop row's
   *  inline pills -- same active/hover treatment either way. */
  block?: boolean;
}) {
  const active = pathname === item.href || pathname?.startsWith(`${item.href}/`);
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "rounded-lg px-3 py-2 text-sm transition-colors",
        block ? "w-full" : "shrink-0",
        active
          ? "bg-sphere-purple/[0.07] font-medium text-sphere-purple"
          : "text-slate-600 hover:bg-slate-100 hover:text-sphere-purple",
      )}
    >
      {item.label}
    </Link>
  );
}

/** Three linked nodes -- the smallest honest drawing of what the product is. */
function MyceliumMark() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M7 8.5 12 15.5 17 9.5" stroke="#cbd5e1" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="7" cy="8.5" r="3" fill="#FF5A1F" />
      <circle cx="17" cy="9.5" r="2.4" fill="#2563EB" />
      <circle cx="12" cy="15.5" r="2.8" fill="#7C3AED" />
    </svg>
  );
}
