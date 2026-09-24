import { LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Analytics, MonitoredVideo } from "../api/types";
import OccupancyChart from "../components/OccupancyChart";
import { Card, ErrorBanner, PageHeader } from "../components/ui";
import { bucketLabel, formatPercent, formatSpan, formatTick } from "../lib/format";

const RANGES = [
  { key: "15m", label: "15 min", seconds: 900 },
  { key: "1h", label: "1 hour", seconds: 3600 },
  { key: "24h", label: "24 hours", seconds: 86400 },
  { key: "7d", label: "7 days", seconds: 7 * 86400 },
  { key: "all", label: "All time", seconds: null },
] as const;
type RangeKey = (typeof RANGES)[number]["key"];

const LIVE_REFRESH_MS = 10_000;
const BAR_COLOR = "#2a78d6"; // same series colour as the chart

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

function StatTile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <Card className="px-5 py-4">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-1 text-3xl font-semibold tracking-tight text-slate-900">{value}</p>
      {note && <p className="mt-0.5 text-xs text-slate-400">{note}</p>}
    </Card>
  );
}

/** Occupied time and sessions per table, and occupancy over time. */
export default function AnalyticsPage() {
  const [videos, setVideos] = useState<MonitoredVideo[] | null>(null);
  const [videoId, setVideoId] = useState<string | null>(null); // null: the live video, else the last one watched
  const [range, setRange] = useState<RangeKey>("24h");
  const [data, setData] = useState<Analytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const seconds = RANGES.find((item) => item.key === range)?.seconds ?? null;
      const since = seconds === null ? undefined : Date.now() / 1000 - seconds;
      const [list, result] = await Promise.all([api.analyticsVideos(), api.analytics(videoId ?? undefined, since)]);
      setVideos(list);
      setData(result);
      setError(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [videoId, range]);

  useEffect(() => {
    void load();
  }, [load]);

  const selected = videos?.find((video) => video.id === data?.video_id) ?? null;
  const live = selected?.is_live ?? false;
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => void load(), LIVE_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [live, load]);

  const tables = [...(data?.tables ?? [])].sort((a, b) => b.occupied_seconds - a.occupied_seconds);
  const occupiedTotal = tables.reduce((sum, table) => sum + table.occupied_seconds, 0);
  const watched = (data?.monitored_seconds ?? 0) > 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Analytics"
        subtitle="Counted while a video streams with its tables. A table is occupied from when guests arrive until they leave."
      />

      {error && <ErrorBanner message={error} onClose={() => setError(null)} />}

      {videos !== null && videos.length === 0 ? (
        <Card className="px-5 py-10 text-center text-sm text-slate-500">
          No analytics yet. Draw the tables of a video and{" "}
          <Link to="/videos" className="font-medium text-emerald-700 hover:underline">
            start its stream
          </Link>
          ; occupancy is recorded while it runs.
        </Card>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <select
              value={data?.video_id ?? ""}
              onChange={(event) => setVideoId(event.target.value)}
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700"
              aria-label="Video"
            >
              {(videos ?? []).map((video) => (
                <option key={video.id} value={video.id}>
                  {video.name}
                  {video.is_live ? " (live)" : ""}
                </option>
              ))}
            </select>
            <div className="inline-flex rounded-md border border-slate-300 bg-white p-0.5" role="group" aria-label="Time range">
              {RANGES.map((item) => (
                <button
                  key={item.key}
                  onClick={() => setRange(item.key)}
                  aria-pressed={range === item.key}
                  className={`rounded px-3 py-1 text-sm font-medium transition-colors ${
                    range === item.key ? "bg-slate-800 text-white" : "text-slate-600 hover:bg-slate-100"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
            {loading && <LoaderCircle className="h-4 w-4 animate-spin text-slate-400" />}
            {live && !loading && <span className="text-xs text-slate-500">Live: updates every 10 seconds</span>}
          </div>

          {data && (
            <div className={`space-y-6 transition-opacity ${loading ? "opacity-60" : ""}`}>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <StatTile
                  label="Average occupancy"
                  value={formatPercent(data.average_occupancy)}
                  note={`of ${data.table_count} tables`}
                />
                <StatTile label="Watched" value={formatSpan(data.monitored_seconds)} note="in this time range" />
                <StatTile label="Sessions" value={data.session_count.toLocaleString()} note="groups of guests" />
                <StatTile
                  label="Average session"
                  value={data.session_count ? formatSpan(occupiedTotal / data.session_count) : "–"}
                  note="from arrival to leaving"
                />
              </div>

              <Card className="p-5">
                <h2 className="font-semibold text-slate-800">Occupancy over time</h2>
                <p className="mb-4 text-sm text-slate-500">
                  Share of tables occupied, per {bucketLabel(data.bucket_seconds)}. Gaps: the video was not streaming.
                </p>
                {watched ? (
                  <>
                    <OccupancyChart
                      timeline={data.timeline}
                      bucketSeconds={data.bucket_seconds}
                      tableCount={data.table_count}
                    />
                    <details className="mt-3 text-sm">
                      <summary className="cursor-pointer text-slate-500 hover:text-slate-700">Show as table</summary>
                      <div className="mt-2 max-h-64 overflow-y-auto">
                        <table className="w-full text-left text-sm tabular-nums">
                          <thead className="text-xs text-slate-500">
                            <tr>
                              <th className="py-1 font-medium">From</th>
                              <th className="py-1 font-medium">Occupied</th>
                              <th className="py-1 font-medium">Tables occupied (avg.)</th>
                              <th className="py-1 font-medium">Watched</th>
                            </tr>
                          </thead>
                          <tbody className="text-slate-700">
                            {data.timeline.map((point) => (
                              <tr key={point.start} className="border-t border-slate-100">
                                <td className="py-1">{formatTick(point.start, data.bucket_seconds, true)}</td>
                                <td className="py-1">{formatPercent(point.occupancy_rate)}</td>
                                <td className="py-1">{point.occupied_tables?.toFixed(1) ?? "–"}</td>
                                <td className="py-1">{formatSpan(point.monitored_seconds)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </details>
                  </>
                ) : (
                  <p className="py-10 text-center text-sm text-slate-500">
                    This video was not streaming in this time range. Pick a longer range.
                  </p>
                )}
              </Card>

              <Card className="overflow-x-auto p-5">
                <h2 className="mb-3 font-semibold text-slate-800">Tables</h2>
                {tables.length === 0 ? (
                  <p className="text-sm text-slate-500">This video has no tables.</p>
                ) : (
                  <table className="w-full min-w-[640px] text-left text-sm">
                    <thead className="text-xs text-slate-500">
                      <tr>
                        <th className="pb-2 font-medium">Table</th>
                        <th className="pb-2 font-medium">Occupancy</th>
                        <th className="pb-2 text-right font-medium">Occupied time</th>
                        <th className="pb-2 text-right font-medium">Sessions</th>
                        <th className="pb-2 text-right font-medium">Avg. session</th>
                        <th className="pb-2 text-right font-medium">Longest</th>
                      </tr>
                    </thead>
                    <tbody className="tabular-nums text-slate-700">
                      {tables.map((table) => (
                        <tr key={table.id} className="border-t border-slate-100">
                          <td className="py-2 pr-4 font-medium text-slate-800">{table.name}</td>
                          <td className="py-2 pr-4">
                            <div className="flex items-center gap-3">
                              <div className="h-2 w-32 bg-slate-100">
                                <div
                                  className="h-2 rounded-r"
                                  style={{ width: `${(table.occupancy_rate ?? 0) * 100}%`, background: BAR_COLOR }}
                                />
                              </div>
                              <span className="w-10 text-right">{formatPercent(table.occupancy_rate)}</span>
                            </div>
                          </td>
                          <td className="py-2 text-right">{formatSpan(table.occupied_seconds)}</td>
                          <td className="py-2 text-right">{table.session_count}</td>
                          <td className="py-2 text-right">
                            {table.average_session_seconds === null ? "–" : formatSpan(table.average_session_seconds)}
                          </td>
                          <td className="py-2 text-right">
                            {table.longest_session_seconds === null ? "–" : formatSpan(table.longest_session_seconds)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Card>
            </div>
          )}
        </>
      )}
    </div>
  );
}
