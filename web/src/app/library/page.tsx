import type { Metadata } from "next";
import { Suspense } from "react";

import { LibraryClient } from "./library-client";

export const metadata: Metadata = {
  title: "Library",
  description: "Browse and search everything in the Mycelium knowledge network.",
};

export default function LibraryPage() {
  // useSearchParams inside LibraryClient requires a Suspense boundary for
  // static prerendering, same as the admin login form.
  return (
    <Suspense>
      <LibraryClient />
    </Suspense>
  );
}
