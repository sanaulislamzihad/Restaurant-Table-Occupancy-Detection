import { ArrowLeft, MonitorPlay } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, apiUrl } from "../api/client";
import type { Video } from "../api/types";
import TableEditor from "../components/TableEditor";
import { buttonClasses, Card, ErrorBanner, PageHeader } from "../components/ui";

/** Pick a video, then draw its tables in the editor. */
export default function TableSetupPage() {
  const { videoId } = useParams();
  const [videos, setVideos] = useState<Video[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .videos()
      .then(setVideos)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [videoId]);

  const video = videoId ? videos?.find((item) => item.id === videoId) : undefined;

  if (videoId) {
    return (
      <div className="space-y-4">
        <PageHeader
          title="Table Setup"
          subtitle={video ? video.name : videoId}
          actions={
            <div className="flex gap-2">
              <Link to="/setup" className={buttonClasses("secondary")}>
                <ArrowLeft className="h-4 w-4" /> Other video
              </Link>
              {video?.is_streaming && (
                <Link to="/live" className={buttonClasses("secondary")}>
                  <MonitorPlay className="h-4 w-4" /> Live Monitor
                </Link>
              )}
            </div>
          }
        />
        {error && <ErrorBanner message={error} />}
        {videos !== null && !video && (
          <ErrorBanner message={`There is no video with id "${videoId}". Pick one from the list.`} />
        )}
        {video && videos && <TableEditor key={video.id} video={video} videos={videos} />}
      </div>
    );
  }

  const sorted = [...(videos ?? [])].sort((a, b) => Number(b.is_streaming) - Number(a.is_streaming));
  return (
    <div className="space-y-6">
      <PageHeader title="Table Setup" subtitle="Draw where the tables are, once per camera view." />
      {error && <ErrorBanner message={error} />}
      {videos === null ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : videos.length === 0 ? (
        <Card className="px-5 py-8 text-center text-sm text-slate-500">
          No videos yet.{" "}
          <Link to="/videos" className="font-medium text-emerald-700 hover:underline">
            Upload one first
          </Link>
          .
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {sorted.map((item) => (
            <Link
              key={item.id}
              to={`/setup/${item.id}`}
              className={`overflow-hidden rounded-xl border bg-white shadow-sm transition hover:shadow-md ${
                item.is_streaming ? "border-emerald-400 ring-2 ring-emerald-200" : "border-slate-200"
              }`}
            >
              <img src={apiUrl(item.thumbnail_url)} alt="" className="aspect-video w-full bg-slate-200 object-cover" />
              <div className="space-y-1 p-3">
                <p className="truncate text-sm font-semibold text-slate-800" title={item.name}>
                  {item.name}
                </p>
                <p className="text-xs text-slate-500">
                  {item.has_tables ? `${item.table_count} tables` : "No tables yet"}
                  {item.is_streaming && " · streaming"}
                </p>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
