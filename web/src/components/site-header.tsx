import Link from "next/link";

const NAV = [
  { href: "/how-it-works", label: "How it works" },
  { href: "/library", label: "Library" },
  { href: "/scout", label: "Scout queue" },
];

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 border-b border-slate-200/70 bg-white/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2.5">
          <MyceliumMark />
          <span className="text-[17px] font-semibold tracking-tight text-slate-900">Mycelium</span>
        </Link>
        <nav className="flex items-center gap-1">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-lg px-3 py-2 text-sm text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
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
