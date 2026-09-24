import { Gauge, PenLine, Users, Video } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import ConnectionBadge from "../components/ConnectionBadge";
import EventLog from "../components/EventLog";
import LiveVideo from "../components/LiveVideo";
import TableCard from "../components/TableCard";
import { Banner, Card } from "../components/ui";
import { useLiveStatus } from "../hooks/useLiveStatus";
import { useNow } from "../hooks/useNow";
import { connectionLabel } from "../lib/format";

/** Live annotated video, one card per table, summary bar and event log. */
export default function LiveMonitorPage() {
  const { status, events, socketOpen, failedAttempts, connectionCount } = useLiveStatus();
  const now = useNow();
  const [videoName, setVideoName] = useState<string | null>(null);
  const videoId = status?.video_id ?? null;

  useEffect(() => {
    if (videoId === null) {
      setVideoName(null);
      return;
    }
    api
      .streamStatus()
      .then((stream) => setVideoName(stream.video_id === videoId ? stream.video_name : null))
      .catch(() => setVideoName(null));
  }, [videoId]);

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
          <h2 className="border-b border-slate-100 px-4 py-2.5 text-sm font-semibold text-slate-700">
            Tables {tables.length > 0 && <span className="font-normal text-slate-400">({tables.length})</span>}
          </h2>
          {tables.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-slate-400">No tables to show.</p>
          ) : (
            <div className="grid grid-cols-2 gap-2 overflow-y-auto p-3">
              {tables.map((table) => (
                <TableCard key={table.id} table={table} now={now} />
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
