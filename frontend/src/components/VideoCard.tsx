import { LoaderCircle, PenLine, Play, Square, Trash } from "lucide-react";

import { apiUrl } from "../api/client";
import type { Video } from "../api/types";
import { formatBytes, formatDuration } from "../lib/format";
import { Button } from "./ui";

interface VideoCardProps {
  video: Video;
  busy: boolean;
  onStart: () => void;
  onStop: () => void;
  onSetup: () => void;
  onDelete: () => void;
}

/** Thumbnail, details and actions of one uploaded video. */
export default function VideoCard({ video, busy, onStart, onStop, onSetup, onDelete }: VideoCardProps) {
  const details = [
    video.duration_seconds !== null ? formatDuration(video.duration_seconds) : null,
    video.width && video.height ? `${video.width}×${video.height}` : null,
    video.fps ? `${Math.round(video.fps)} fps` : null,
    formatBytes(video.size_bytes),
  ].filter(Boolean);

  return (
    <div
      className={`overflow-hidden rounded-xl border bg-white shadow-sm ${
        video.is_streaming ? "border-emerald-400 ring-2 ring-emerald-200" : "border-slate-200"
      }`}
    >
      <div className="relative aspect-video bg-slate-200">
        <img src={apiUrl(video.thumbnail_url)} alt="" className="h-full w-full object-cover" loading="lazy" />
        {video.is_streaming && (
          <span className="absolute left-2 top-2 inline-flex items-center gap-1.5 rounded-full bg-red-600 px-2 py-0.5 text-xs font-bold text-white">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" /> STREAMING
          </span>
        )}
      </div>
      <div className="space-y-3 p-4">
        <div>
          <h3 className="truncate font-semibold text-slate-800" title={video.name}>
            {video.name}
          </h3>
          <p className="mt-0.5 text-xs text-slate-500">{details.join(" · ")}</p>
        </div>
        <span
          className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${
            video.has_tables ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"
          }`}
        >
          {video.has_tables ? `${video.table_count} tables set up` : "No tables yet"}
        </span>
        <div className="flex flex-wrap gap-2">
          {video.is_streaming ? (
            <Button variant="secondary" onClick={onStop} disabled={busy}>
              <Square className="h-4 w-4" /> Stop
            </Button>
          ) : (
            <Button onClick={onStart} disabled={busy}>
              {busy ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} Start Stream
            </Button>
          )}
          <Button variant="secondary" onClick={onSetup} disabled={busy}>
            <PenLine className="h-4 w-4" /> Setup Tables
          </Button>
          <Button variant="danger" onClick={onDelete} disabled={busy} className="ml-auto" title="Delete video">
            <Trash className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
