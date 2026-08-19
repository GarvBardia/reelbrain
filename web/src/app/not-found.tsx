import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { Button } from "@/components/ui/button";

// Static export has no server to run a catch-all route on, but Next still
// statically renders this for any path that doesn't match a generated page
// -- so a stale or mistyped link gets an answer in the site's own voice
// instead of the framework's bare default.
export default function NotFound() {
  return (
    <div className="mx-auto flex max-w-xl flex-col items-center px-6 py-32 text-center">
      <p className="text-sm font-medium uppercase tracking-widest text-slate-400">404</p>
      <h1 className="mt-4 text-balance text-3xl font-semibold tracking-tight text-slate-900">
        Nothing&apos;s connected at this address.
      </h1>
      <p className="mt-4 text-lg leading-relaxed text-slate-600">
        The page you&apos;re looking for doesn&apos;t exist, or the link is out of date.
      </p>
      <Link href="/" className="mt-8">
        <Button size="lg">
          Back to the network
          <ArrowRight className="h-4 w-4" />
        </Button>
      </Link>
    </div>
  );
}
