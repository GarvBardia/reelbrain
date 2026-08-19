import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="border-t border-slate-200/70">
      <div className="mx-auto flex max-w-6xl flex-col gap-3 px-6 py-10 text-sm text-slate-500 sm:flex-row sm:items-center sm:justify-between">
        <p>Mycelium — a self-organizing knowledge network.</p>
        {/* Admin deliberately isn't linked here -- it's operator tooling, not
            part of what a visitor evaluating the product needs to reach, and
            there's no reason to put a login surface in front of every
            visitor (PRODUCT.md: admin is secondary to the public surfaces). */}
        <div className="flex items-center gap-5">
          <Link href="/how-it-works" className="hover:text-slate-800">How it works</Link>
          <Link href="/library" className="hover:text-slate-800">Library</Link>
          <Link href="/scout" className="hover:text-slate-800">Scout queue</Link>
        </div>
      </div>
    </footer>
  );
}
