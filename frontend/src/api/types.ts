// Shapes of the backend's JSON (see backend/app/schemas.py).

export type TableStatusName = "AVAILABLE" | "PENDING_OCCUPIED" | "OCCUPIED" | "PENDING_AVAILABLE";
export type SourceLabel = "LIVE" | "RECONNECTING" | "NO SOURCE";
export type StreamState = "running" | "stopped" | "error";

export interface TableStatus {
  id: string;
  name: string;
  status: TableStatusName;
  /** OCCUPIED, or waiting to become AVAILABLE. */
  occupied: boolean;
  people_count: number;
  track_ids: number[];
  /** Unix time (seconds) the current guests arrived. */
  occupied_since: number | null;
  current_session_seconds: number;
  total_occupied_seconds: number;
  session_count: number;
}

export interface PipelineStatus {
  video_id: string | null;
  has_tables: boolean;
  source_status: SourceLabel;
  processing_fps: number;
  stream_fps: number;
  frame_width: number | null;
  frame_height: number | null;
  people_count: number;
  table_count: number;
  available_count: number;
  tables: TableStatus[];
  timestamp: number;
}

export interface TableEvent {
  id: number;
  video_id: string | null;
  table_id: string;
  table_name: string;
  old_status: TableStatusName;
  new_status: TableStatusName;
  timestamp: number;
}

export interface Video {
  id: string;
  name: string;
  filename: string;
  size_bytes: number;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  codec: string | null;
  uploaded_at: number;
  has_tables: boolean;
  table_count: number;
  is_streaming: boolean;
  thumbnail_url: string;
}

export interface StreamStatus {
  state: StreamState;
  video_id: string | null;
  video_name: string | null;
  has_tables: boolean;
  started_at: number | null;
  uptime_seconds: number | null;
  rtsp_url: string;
  error: string | null;
  mediamtx: "running" | "not running";
  mediamtx_managed: boolean;
}

export interface UploadLimits {
  max_upload_mb: number;
  extensions: string[];
}

/** Messages pushed on /ws/status. */
export type LiveMessage = { type: "status"; data: PipelineStatus } | { type: "event"; data: TableEvent };

export type Point = [number, number];

export interface TableDef {
  id: string;
  name: string;
  /** Corners in pixels of the config's frame_width x frame_height frame. */
  polygon: Point[];
}

export interface TableConfig {
  video_id: string;
  source: string;
  frame_width: number;
  frame_height: number;
  tables: TableDef[];
  occupancy: {
    confidence_threshold: number;
    enter_seconds: number;
    leave_seconds: number;
    reference_point: "bottom_center" | "center";
    presence_hold_seconds: number;
  };
}

/** A frame to draw tables on; all points are in pixels of this frame. */
export interface EditorFrame {
  video_id: string;
  /** data:image/jpeg;base64 URL */
  image: string;
  frame_width: number;
  frame_height: number;
  live: boolean;
  at_seconds: number | null;
  people: Point[];
  suggested_tables: Point[][];
  hints_error: string | null;
}
