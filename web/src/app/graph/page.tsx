import type { Metadata } from "next";

import { GraphClient } from "./graph-client";

export const metadata: Metadata = {
  title: "The graph",
  description:
    "Every save in the network as a point on a sphere — hover to read one, click to open it.",
};

export default function GraphPage() {
  return <GraphClient />;
}
