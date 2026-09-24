import { Gauge, LoaderCircle, PenLine, RotateCw, Users, Video } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { StreamStatus } from "../api/types";
import ConnectionBadge from "../components/ConnectionBadge";
import EventLog from "../components/EventLog";
import LiveVideo from "../components/LiveVideo";
import TableCard from "../components/TableCard";
import { Banner, Button, Card } from "../components/ui";
import { useLiveStatus } from "../hooks/useLiveStatus";
import { useNow } from "../hooks/useNow";
import { connectionLabel } from "../lib/format";

const STREAM_POLL_MS = 5000;

/** Live annotated video, one card per table, summary bar and event log. */
export default function LiveMonitorPage() {
  const { status, events, socketOpen, failedAttempts, connectionCount } = useLiveStatus();
  const now = useNow();
  const [stream, setStream] = useState<StreamStatus | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [restartError, setRestartError] = useState<string | null>(null);
  const videoId = status?.video_id ?? null;

  // The fake camera's state (name of the video, and whether ffmpeg stopped).
  useEffect(() => {
    let cancelled = false;
    const poll = () =>
      api
        .streamStatus()
        .then((result) => !cancelled && setStream(result))
        .catch(() => undefined);
    void poll();
    const timer = window.setInterval(poll, STREAM_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [videoId]);

  // While the video is not live the table states are the last known ones: freeze their timers there.
  const live = status?.source_status === "LIVE";
  const [lastLiveAt, setLastLiveAt] = useState<number | null>(null);
  useEffect(() => {
    if (live && status) setLastLiveAt(status.timestamp);
  }, [live, status]);
  const cardClock = live || lastLiveAt === null ? now : lastLiveAt;

  const videoName = stream && stream.video_id === videoId ? stream.video_name : null;
  const streamFailed = stream?.state === "error" && stream.video_id !== null && stream.video_id === videoId;

  const restart = async () => {
    if (!stream?.video_id) return;
    setRestarting(true);
    setRestartError(null);
    try {
      setStream(await api.startStream(stream.video_id));
    } catch (err) {
      setRestartError(err instanceof Error ? err.message : String(err));
    } finally {
      setRestarting(false);
    }
  };

  const label = connectionLabel(socketOpen, failedAttempts, status?.source_status);
  const tables = status?.tables ?? [];

  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-center justify-between gap-4 px-5 py-3">
        <div className="flex min-w-0 items-center gap-2 text-sm text-slate-600">
          <Video className="h-4 w-4 shrink-0" />
          <span className="truncate font-medium text-slate-800">
            {videoId ? videoName ?? videoId : "No video running"}
          </span>
        </div>
        <div className="text-lg font-semibold text-slate-900">
          {status?.has_tables ? (
            <>
              <span className="text-emerald-600">{status.available_count}</span> / {status.table_count} tables
              available
            </>
          ) : (
            <span className="text-slate-400">No tables</span>
          )}
        </div>
        <div className="flex items-center gap-4 text-sm text-slate-600">
          <span className="flex items-center gap-1" title="People in view">
            <Users className="h-4 w-4" /> {status?.people_count ?? 0}
          </span>
          <span className="flex items-center gap-1" title="Detections per second">
            <Gauge className="h-4 w-4" /> {(status?.processing_fps ?? 0).toFixed(1)} fps
          </span>
          <ConnectionBadge label={label} />
        </div>
      </Card>

      {streamFailed && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-5 py-3 text-sm text-red-800 shadow-sm">
          <div className="min-w-0 flex-1">
            <p className="font-medium">The video stream stopped. Occupancy is paused until it runs again.</p>
            {(restartError ?? stream?.error) && (
              <p className="mt-0.5 line-clamp-2 text-xs text-red-700" title={restartError ?? stream?.error ?? ""}>
                {restartError ?? stream?.error}
              </p>
            )}
          </div>
          <Button onClick={() => void restart()} disabled={restarting}>
            {restarting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <RotateCw className="h-4 w-4" />}
            Restart stream
          </Button>
        </div>
      )}

      {socketOpen && videoId === null && (
        <Banner tone="info">
          No stream is running.{" "}
          <Link to="/videos" className="font-medium text-emerald-700 hover:underline">
            Start a video on the Videos page
          </Link>
          .
        </Banner>
      )}
      {videoId !== null && status !== null && !status.has_tables && (
        <Banner tone="warning" className="flex items-center justify-between gap-3">
          This video has no tables yet, so nothing is being counted.
          <Link
            to={`/setup/${videoId}`}
            className="inline-flex items-center gap-1 font-medium underline-offset-2 hover:underline"
          >
            <PenLine className="h-4 w-4" /> Set up tables
          </Link>
        </Banner>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        <LiveVideo reloadKey={connectionCount} />
        <Card className="flex max-h-[calc(100vh-14rem)] flex-col overflow-hidden">
          <h2 className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5 text-sm font-semibold text-slate-700">
            <span>
              Tables {tables.length > 0 && <span className="font-normal text-slate-400">({tables.length})</span>}
            </span>
            {!live && tables.length > 0 && (
              <span className="text-xs font-normal text-amber-700">Last known status: video not live</span>
            )}
          </h2>
          {tables.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-slate-400">No tables to show.</p>
          ) : (
            <div className={`grid grid-cols-2 gap-2 overflow-y-auto p-3 ${live ? "" : "opacity-50 grayscale"}`}>
              {tables.map((table) => (
                <TableCard key={table.id} table={table} now={cardClock} />
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card className="overflow-hidden">
        <h2 className="border-b border-slate-100 px-4 py-2.5 text-sm font-semibold text-slate-700">Event log</h2>
        <div className="max-h-64 overflow-y-auto">
          <EventLog events={events} videoId={videoId} />
        </div>
      </Card>
    </div>
  );
}
