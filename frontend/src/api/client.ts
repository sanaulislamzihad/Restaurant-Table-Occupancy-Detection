// Calls to the backend REST API. The address comes from VITE_API_BASE_URL.
import type { EditorFrame, StreamStatus, TableConfig, TableDef, TableEvent, UploadLimits, Video } from "./types";

export const API_BASE_URL = __API_BASE_URL__.replace(/\/+$/, "");

/** Full URL of a backend path such as "/api/stream". */
export const apiUrl = (path: string): string => `${API_BASE_URL}${path}`;

/** WebSocket URL of a backend path (http -> ws, https -> wss). */
export const wsUrl = (path: string): string => apiUrl(path).replace(/^http/, "ws");

/** A failed API call, with the backend's explanation as the message. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function errorText(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), init);
  } catch {
    throw new ApiError(0, `Cannot reach the backend at ${API_BASE_URL}. Is it running?`);
  }
  if (!response.ok) throw new ApiError(response.status, await errorText(response));
  return (response.status === 204 ? undefined : await response.json()) as T;
}

const postJson = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  videos: () => request<Video[]>("/api/videos"),
  uploadLimits: () => request<UploadLimits>("/api/videos/limits"),
  deleteVideo: (id: string) => request<void>(`/api/videos/${encodeURIComponent(id)}`, { method: "DELETE" }),
  startStream: (videoId: string) => request<StreamStatus>("/api/stream/start", postJson({ video_id: videoId })),
  stopStream: () => request<StreamStatus>("/api/stream/stop", { method: "POST" }),
  streamStatus: () => request<StreamStatus>("/api/stream/status"),
  events: (limit = 50) => request<TableEvent[]>(`/api/events?limit=${limit}`),
  /** The video's table config, or null if it has none yet. */
  videoConfig: async (videoId: string): Promise<TableConfig | null> => {
    try {
      return await request<TableConfig>(`/api/videos/${encodeURIComponent(videoId)}/config`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) return null;
      throw error;
    }
  },
  editorFrame: (videoId: string, options: { at?: number; live?: boolean } = {}) => {
    const query = new URLSearchParams({ at: String(options.at ?? 1), live: String(options.live ?? true) });
    return request<EditorFrame>(`/api/videos/${encodeURIComponent(videoId)}/editor-frame?${query}`);
  },
  saveTables: (videoId: string, tables: TableDef[], frameWidth: number, frameHeight: number) =>
    request<TableConfig>(`/api/videos/${encodeURIComponent(videoId)}/tables`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tables, frame_width: frameWidth, frame_height: frameHeight }),
    }),
};

/**
 * Upload a video with progress reports (fetch cannot report upload progress,
 * so this uses XMLHttpRequest). onProgress gets a fraction from 0 to 1.
 */
export function uploadVideo(
  file: File,
  onProgress: (fraction: number) => void,
): { promise: Promise<Video>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<Video>((resolve, reject) => {
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    xhr.onload = () => {
      let body: { detail?: unknown } | Video | null = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // keep null: reported below
      }
      if (xhr.status === 201 && body) {
        resolve(body as Video);
      } else {
        const detail = body && "detail" in body ? body.detail : null;
        reject(new ApiError(xhr.status, typeof detail === "string" ? detail : `Upload failed (HTTP ${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, `Cannot reach the backend at ${API_BASE_URL}.`));
    xhr.onabort = () => reject(new ApiError(0, "Upload cancelled."));
    const form = new FormData();
    form.append("file", file);
    xhr.open("POST", apiUrl("/api/videos"));
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
}
