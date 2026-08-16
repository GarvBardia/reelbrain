"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Link2,
  Loader2,
  LogOut,
  Play,
  RefreshCw,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type Overview = {
  health?: Record<string, unknown>;
  stats?: { total_reels: number; total_topics: number; actionable_items: number; high_priority: number };
  scout_queue_size?: number;
};

type AdminScoutItem = {
  shortcode: string;
  title: string;
  suggested_action: string;
  category_label: string;
  color: string;
  value_score: number;
  priority: string;
  gate_keyword?: string | null;
  gate_resource?: string | null;
  status_label?: string | null;
  notion_url?: string | null;
};

export default function AdminDashboard() {
  const router = useRouter();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [queue, setQueue] = useState<AdminScoutItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const [o, q] = await Promise.all([
        fetch("/api/admin/overview").then((r) => r.json()),
        fetch("/api/admin/scout-queue").then((r) => r.json()),
      ]);
      if (o.error) throw new Error(o.error);
      setOverview(o);
      setQueue(q.items ?? []);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not load dashboard data.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function logout() {
    await fetch("/api/admin/logout", { method: "POST" });
    router.push("/admin");
    router.refresh();
  }

  return (
    <div className="mx-auto max-w-5xl px-6 py-12">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight text-slate-900">Admin</h1>
          <p className="mt-1 text-slate-500">Operational view — unredacted.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
            Refresh
          </Button>
          <Button variant="ghost" size="sm" onClick={() => void logout()}>
            <LogOut className="h-3.5 w-3.5" />
            Sign out
          </Button>
        </div>
      </div>

      {err && (
        <div className="mb-6 flex items-start gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{err}</span>
        </div>
      )}

      <HealthPanel health={overview?.health} loading={loading} />

      {overview?.stats && (
        <div className="mt-6 grid grid-cols-2 gap-4 md:grid-cols-4">
          {[
            { label: "Saves", value: overview.stats.total_reels },
            { label: "Topics", value: overview.stats.total_topics },
            { label: "High priority", value: overview.stats.high_priority },
            { label: "Scout queue", value: overview.scout_queue_size ?? 0 },
          ].map((s) => (
            <Card key={s.label} className="border-slate-200/80">
              <CardContent className="p-5">
                <p className="text-2xl font-semibold tabular-nums text-slate-900">
                  {s.value.toLocaleString()}
                </p>
                <p className="mt-0.5 text-sm text-slate-500">{s.label}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <TriggerPanel />
      <AttachPanel />

      <section className="mt-10">
        <h2 className="mb-4 text-lg font-semibold text-slate-900">
          Scout queue <span className="font-normal text-slate-400">(unredacted)</span>
        </h2>
        {loading ? (
          <div className="flex justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          </div>
        ) : queue.length === 0 ? (
          <p className="rounded-xl border border-dashed py-12 text-center text-slate-500">
            Queue is empty.
          </p>
        ) : (
          <ul className="space-y-3">
            {queue.map((item) => (
              <li key={item.shortcode}>
                <Card className="border-slate-200/80">
                  <CardContent className="p-5">
                    <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                      <span
                        className="rounded-full px-2 py-0.5 font-medium"
                        style={{ backgroundColor: `${item.color}14`, color: item.color }}
                      >
                        {item.category_label}
                      </span>
                      <span className="tabular-nums text-slate-400">v{item.value_score}</span>
                      {item.status_label && (
                        <span className="text-slate-500">{item.status_label}</span>
                      )}
                      {/* The whole reason this view exists: the gate keyword
                          and the DM'd link, both stripped from the public API. */}
                      {item.gate_keyword && (
                        <span className="rounded bg-amber-50 px-2 py-0.5 font-medium text-amber-800">
                          comment “{item.gate_keyword}”
                        </span>
                      )}
                      {item.gate_resource ? (
                        <a
                          href={item.gate_resource}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 rounded bg-emerald-50 px-2 py-0.5 font-medium text-emerald-800"
                        >
                          resource attached <ExternalLink className="h-3 w-3" />
                        </a>
                      ) : item.gate_keyword ? (
                        <span className="rounded bg-slate-100 px-2 py-0.5 text-slate-600">
                          no resource yet
                        </span>
                      ) : null}
                    </div>
                    <p className="font-medium leading-snug text-slate-900">{item.title}</p>
                    <p className="mt-1.5 text-sm text-slate-600">{item.suggested_action}</p>
                    <div className="mt-2 flex gap-3 text-xs text-slate-400">
                      <span className="font-mono">{item.shortcode}</span>
                      {item.notion_url && (
                        <a
                          href={item.notion_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="hover:text-slate-700"
                        >
                          Open in Notion
                        </a>
                      )}
                    </div>
                  </CardContent>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function HealthPanel({ health, loading }: { health?: Record<string, unknown>; loading: boolean }) {
  if (loading && !health) {
    return (
      <Card className="border-slate-200/80">
        <CardContent className="flex justify-center p-8">
          <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
        </CardContent>
      </Card>
    );
  }
  if (!health) return null;

  const entries = Object.entries(health);
  const bad = (k: string, v: unknown) =>
    v === false || v === "degraded" || (k === "status" && v !== "ok");

  return (
    <Card className="border-slate-200/80">
      <CardContent className="p-6">
        <h2 className="mb-4 text-sm font-semibold uppercase tracking-wider text-slate-500">
          Backend health
        </h2>
        <dl className="grid gap-x-8 gap-y-2.5 sm:grid-cols-2">
          {entries.map(([k, v]) => (
            <div key={k} className="flex items-center justify-between gap-4 text-sm">
              <dt className="text-slate-600">{k.replace(/_/g, " ")}</dt>
              <dd
                className={cn(
                  "inline-flex items-center gap-1.5 font-medium",
                  bad(k, v) ? "text-amber-700" : "text-slate-900",
                )}
              >
                {bad(k, v) ? (
                  <AlertTriangle className="h-3.5 w-3.5" />
                ) : (
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                )}
                {String(v)}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

function TriggerPanel() {
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<string>("");

  async function trigger(job: string) {
    setBusy(job);
    setResult("");
    try {
      const res = await fetch("/api/admin/trigger", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ job }),
      });
      const body = await res.json();
      setResult(
        res.ok
          ? `${job}: ${JSON.stringify(body).slice(0, 300)}`
          : `${job} failed: ${body.error ?? res.status}`,
      );
    } catch {
      setResult(`${job}: could not reach the server.`);
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="mt-6">
      <Card className="border-slate-200/80">
        <CardContent className="p-6">
          <h2 className="mb-1 text-sm font-semibold uppercase tracking-wider text-slate-500">
            Run a pipeline job
          </h2>
          <p className="mb-4 text-sm text-slate-500">
            These normally run on a schedule. Triggering by hand spends real quota.
          </p>
          <div className="flex flex-wrap gap-2">
            {["nightly", "daily-digest", "weekly-digest"].map((job) => (
              <Button
                key={job}
                variant="outline"
                size="sm"
                disabled={busy !== null}
                onClick={() => void trigger(job)}
              >
                {busy === job ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Play className="h-3.5 w-3.5" />
                )}
                {job}
              </Button>
            ))}
          </div>
          {result && (
            <pre className="mt-4 overflow-x-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs text-slate-700">
              {result}
            </pre>
          )}
        </CardContent>
      </Card>
    </section>
  );
}

function AttachPanel() {
  const [url, setUrl] = useState("");
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState(false);
  const [response, setResponse] = useState<any>(null);

  async function send(payload: Record<string, unknown>) {
    setBusy(true);
    try {
      const res = await fetch("/api/admin/attach", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      setResponse(await res.json());
    } catch {
      setResponse({ error: "could not reach the server" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-6">
      <Card className="border-slate-200/80">
        <CardContent className="p-6">
          <h2 className="mb-1 text-sm font-semibold uppercase tracking-wider text-slate-500">
            Attach a resource
          </h2>
          <p className="mb-4 text-sm text-slate-500">
            Paste a DM&apos;d link. The matcher finds which save earned it; if it is unsure it
            returns candidates to choose from.
          </p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://…"
              className="flex-1"
            />
            <Input
              value={hint}
              onChange={(e) => setHint(e.target.value)}
              placeholder="shortcode or note (optional)"
              className="sm:w-64"
            />
            <Button
              disabled={busy || !url}
              onClick={() =>
                void send({ resource_url: url, ...(hint ? { shortcode_or_note: hint } : {}) })
              }
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
              Attach
            </Button>
          </div>

          {response && (
            <div className="mt-4 space-y-3">
              <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-xs text-slate-700">
                {JSON.stringify(response, null, 2)}
              </pre>
              {/* /attach answers ambiguity with a menu; these commit one choice. */}
              {Array.isArray(response.candidates) && response.candidates.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {response.candidates.map((c: any) => (
                    <Button
                      key={c.shortcode ?? c}
                      size="sm"
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        void send({
                          confirm: true,
                          resource_url: url,
                          shortcode: c.shortcode ?? c,
                        })
                      }
                    >
                      Confirm {c.shortcode ?? c}
                    </Button>
                  ))}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </section>
  );
}
