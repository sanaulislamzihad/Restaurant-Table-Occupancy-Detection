import { MonitorPlay, Square } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { StreamStatus, UploadLimits, Video } from "../api/types";
import UploadDropzone from "../components/UploadDropzone";
import VideoCard from "../components/VideoCard";
import { Banner, Button, buttonClasses, Card, ErrorBanner, PageHeader } from "../components/ui";

const REFRESH_MS = 5000;

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** Upload videos, list them, and start / stop the fake camera. */
export default function VideosPage() {
  const navigate = useNavigate();
  const [videos, setVideos] = useState<Video[] | null>(null);
  const [stream, setStream] = useState<StreamStatus | null>(null);
  const [limits, setLimits] = useState<UploadLimits | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [videoList, streamStatus] = await Promise.all([api.videos(), api.streamStatus()]);
      setVideos(videoList);
      setStream(streamStatus);
    } catch (err) {
      setError(errorText(err));
    }
  }, []);

  useEffect(() => {
    void refresh();
    api.uploadLimits().then(setLimits).catch(() => undefined);
    const timer = window.setInterval(refresh, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const run = async (videoId: string, action: () => Promise<void>) => {
    setBusyId(videoId);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusyId(null);
      void refresh();
    }
  };

  const start = (video: Video) =>
    run(video.id, async () => {
      const status = await api.startStream(video.id);
      if (status.state === "error") throw new Error(`Could not start "${video.name}": ${status.error}`);
      navigate(status.has_tables ? "/live" : `/setup/${video.id}`);
    });

  const stop = (videoId: string) =>
    run(videoId, async () => {
      await api.stopStream();
    });

  const remove = (video: Video) => {
    if (!window.confirm(`Delete "${video.name}"? Its table layout, thumbnail and analytics history are deleted too.`)) return;
    void run(video.id, () => api.deleteVideo(video.id));
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Videos" subtitle="Upload restaurant footage and stream it as a live CCTV camera." />

      {stream?.state === "running" && (
        <Banner tone="success" className="flex flex-wrap items-center justify-between gap-3">
          <p>
            Streaming <span className="font-semibold">{stream.video_name ?? stream.video_id}</span> as{" "}
            <code className="rounded bg-white/70 px-1.5 py-0.5 text-xs">{stream.rtsp_url}</code>
          </p>
          <div className="flex gap-2">
            <Link to="/live" className={buttonClasses("primary")}>
              <MonitorPlay className="h-4 w-4" /> Live Monitor
            </Link>
            <Button variant="secondary" onClick={() => stream.video_id && stop(stream.video_id)}>
              <Square className="h-4 w-4" /> Stop
            </Button>
          </div>
        </Banner>
      )}
      {stream?.state === "error" && (
        <ErrorBanner message={`The stream of ${stream.video_name ?? stream.video_id} stopped: ${stream.error}`} />
      )}
      {error && <ErrorBanner message={error} onClose={() => setError(null)} />}

      <UploadDropzone limits={limits} onUploaded={refresh} />

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Uploaded videos {videos && videos.length > 0 && `(${videos.length})`}
        </h2>
        {videos === null ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : videos.length === 0 ? (
          <Card className="px-5 py-8 text-center text-sm text-slate-500">No videos yet. Upload one above.</Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {videos.map((video) => (
              <VideoCard
                key={video.id}
                video={video}
                busy={busyId === video.id}
                onStart={() => void start(video)}
                onStop={() => void stop(video.id)}
                onSetup={() => navigate(`/setup/${video.id}`)}
                onDelete={() => remove(video)}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
