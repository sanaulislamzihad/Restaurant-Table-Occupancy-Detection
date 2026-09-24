// Small pure helpers for display and validation (unit tested in format.test.ts).
import type { SourceLabel, TableEvent, UploadLimits } from "../api/types";

/** 75 -> "01:15", 3725 -> "1:02:05". */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mmss = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return h > 0 ? `${h}:${mmss}` : mmss;
}

/** 1536 -> "1.5 KB". */
export function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`;
}

/** Unix seconds -> local "HH:MM:SS". */
export function formatClock(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleTimeString([], { hour12: false });
}

/** Why a file cannot be uploaded, or null if it looks fine. */
export function uploadProblem(file: { name: string; size: number }, limits: UploadLimits): string | null {
  const dot = file.name.lastIndexOf(".");
  const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
  if (!limits.extensions.includes(extension)) {
    const allowed = limits.extensions.map((e) => e.replace(".", "")).join(", ");
    return `Not a supported video type (${allowed}).`;
  }
  if (file.size === 0) return "The file is empty.";
  if (file.size > limits.max_upload_mb * 1024 * 1024) return `Larger than ${limits.max_upload_mb} MB.`;
  return null;
}

/**
 * Events worth showing in the live log: a table became OCCUPIED, or became
 * AVAILABLE after being occupied. Passers-by (AVAILABLE -> PENDING -> AVAILABLE)
 * and the pending steps are left out.
 */
export function isLogWorthy(event: TableEvent): boolean {
  return (
    event.new_status === "OCCUPIED" ||
    (event.new_status === "AVAILABLE" && event.old_status === "PENDING_AVAILABLE")
  );
}

export type ConnectionLabel = "LIVE" | "RECONNECTING" | "OFFLINE";

/** One word for the dashboard: combines the WebSocket state and the camera state. */
export function connectionLabel(
  socketOpen: boolean,
  failedAttempts: number,
  source: SourceLabel | undefined,
): ConnectionLabel {
  if (!socketOpen) return failedAttempts >= 3 ? "OFFLINE" : "RECONNECTING";
  if (source === "LIVE") return "LIVE";
  if (source === "RECONNECTING") return "RECONNECTING";
  return "OFFLINE";
}

/** A length of time in words: "45s", "4m 05s", "2h 07m". */
export function formatSpan(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const pad = (value: number) => String(value).padStart(2, "0");
  if (total < 60) return `${total}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m ${pad(total % 60)}s`;
  return `${Math.floor(total / 3600)}h ${pad(Math.floor((total % 3600) / 60))}m`;
}

/** A 0..1 share as a whole percentage, or a dash when unknown. */
export const formatPercent = (share: number | null): string => (share === null ? "–" : `${Math.round(share * 100)}%`);

/** Chart step in words: "10 seconds", "minute", "6 hours". */
export function bucketLabel(seconds: number): string {
  if (seconds % 86400 === 0) return seconds === 86400 ? "day" : `${seconds / 86400} days`;
  if (seconds % 3600 === 0) return seconds === 3600 ? "hour" : `${seconds / 3600} hours`;
  if (seconds % 60 === 0) return seconds === 60 ? "minute" : `${seconds / 60} minutes`;
  return `${seconds} seconds`;
}

/** Axis label of a time: seconds only for short steps, the date only for steps of a day or more. */
export function formatTick(unixSeconds: number, bucketSeconds: number, withDate = false): string {
  const date = new Date(unixSeconds * 1000);
  if (bucketSeconds >= 86400) return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const time = date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: bucketSeconds < 60 ? "2-digit" : undefined,
    hour12: false,
  });
  return withDate ? `${date.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${time}` : time;
}
