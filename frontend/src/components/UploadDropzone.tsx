import { CircleAlert, CircleCheck, LoaderCircle, Upload } from "lucide-react";
import { useRef, useState } from "react";
import type { DragEvent } from "react";

import { uploadVideo } from "../api/client";
import type { UploadLimits } from "../api/types";
import { formatBytes, uploadProblem } from "../lib/format";

interface UploadItem {
  key: number;
  name: string;
  size: number;
  progress: number; // 0..1
  state: "waiting" | "uploading" | "done" | "error";
  message?: string;
}

/**
 * Drag-and-drop (or click to choose) video upload. Files are checked in the
 * browser first, then uploaded one after the other with a progress bar.
 */
export default function UploadDropzone({ limits, onUploaded }: { limits: UploadLimits | null; onUploaded: () => void }) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const nextKey = useRef(0);

  const update = (key: number, changes: Partial<UploadItem>) =>
    setItems((current) => current.map((item) => (item.key === key ? { ...item, ...changes } : item)));

  const handleFiles = async (files: File[]) => {
    const queued = files.map((file) => {
      const problem = limits ? uploadProblem(file, limits) : null;
      const item: UploadItem = {
        key: nextKey.current++,
        name: file.name,
        size: file.size,
        progress: 0,
        state: problem ? "error" : "waiting",
        message: problem ?? undefined,
      };
      return { file, item };
    });
    setItems((current) => [...queued.map(({ item }) => item), ...current]);

    for (const { file, item } of queued) {
      if (item.state === "error") continue;
      update(item.key, { state: "uploading" });
      try {
        await uploadVideo(file, (progress) => update(item.key, { progress })).promise;
        update(item.key, { state: "done", progress: 1 });
        onUploaded();
      } catch (error) {
        update(item.key, { state: "error", message: error instanceof Error ? error.message : String(error) });
      }
    }
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    void handleFiles(Array.from(event.dataTransfer.files));
  };

  const accept = limits?.extensions.join(",") ?? "video/*";

  return (
    <div className="space-y-3">
      <div
        role="button"
        tabIndex={0}
        onClick={() => input.current?.click()}
        onKeyDown={(event) => (event.key === "Enter" || event.key === " ") && input.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors ${
          dragging ? "border-emerald-500 bg-emerald-50" : "border-slate-300 bg-white hover:border-emerald-400"
        }`}
      >
        <Upload className="h-8 w-8 text-slate-400" />
        <p className="mt-2 text-sm font-medium text-slate-700">Drop restaurant videos here, or click to choose</p>
        <p className="mt-1 text-xs text-slate-500">
          {limits
            ? `${limits.extensions.map((e) => e.replace(".", "")).join(", ")} · up to ${limits.max_upload_mb} MB`
            : "mp4, avi, mov or mkv"}
        </p>
        <input
          ref={input}
          type="file"
          accept={accept}
          multiple
          className="hidden"
          onChange={(event) => {
            void handleFiles(Array.from(event.target.files ?? []));
            event.target.value = ""; // allow choosing the same file again
          }}
        />
      </div>

      {items.length > 0 && (
        <ul className="space-y-2">
          {items.map((item) => (
            <li key={item.key} className="rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-sm">
              <div className="flex items-center gap-2">
                {item.state === "done" && <CircleCheck className="h-4 w-4 text-emerald-600" />}
                {item.state === "error" && <CircleAlert className="h-4 w-4 text-red-600" />}
                {(item.state === "uploading" || item.state === "waiting") && (
                  <LoaderCircle className="h-4 w-4 animate-spin text-slate-400" />
                )}
                <span className="flex-1 truncate font-medium text-slate-700">{item.name}</span>
                <span className="text-xs text-slate-500">
                  {item.state === "uploading" ? `${Math.round(item.progress * 100)}% of ` : ""}
                  {formatBytes(item.size)}
                </span>
              </div>
              {item.state === "uploading" && (
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                  <div className="h-full bg-emerald-500 transition-all" style={{ width: `${item.progress * 100}%` }} />
                </div>
              )}
              {item.state === "error" && <p className="mt-1 text-xs text-red-700">{item.message}</p>}
              {item.state === "done" && (
                <p className="mt-1 text-xs text-emerald-700">Uploaded, and checked as a readable video.</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
