import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="border-t border-slate-200/70">
      <div className="mx-auto flex max-w-6xl flex-col gap-3 px-6 py-10 text-sm text-slate-500 sm:flex-row sm:items-center sm:justify-between">
        <p>Mycelium — a self-organizing knowledge network.</p>
        <div className="flex items-center gap-5">
          <Link href="/how-it-works" className="hover:text-slate-800">How it works</Link>
          <Link href="/library" className="hover:text-slate-800">Library</Link>
          <Link href="/scout" className="hover:text-slate-800">Scout queue</Link>
          <Link href="/admin" className="hover:text-slate-800">Admin</Link>
        </div>
      </div>
    </footer>
  );
}
