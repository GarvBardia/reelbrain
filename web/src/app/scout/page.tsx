import type { Metadata } from "next";

import { ScoutClient } from "./scout-client";

export const metadata: Metadata = {
  title: "Scout queue",
  description:
    "The highest-value saves in the network, each with one concrete next step attached.",
};

export default function ScoutPage() {
  return <ScoutClient />;
}
