import { describe, expect, it } from "vitest";

import type { TableEvent } from "../api/types";
import { connectionLabel, formatBytes, formatDuration, isLogWorthy, uploadProblem } from "./format";

const limits = { max_upload_mb: 1, extensions: [".mp4", ".avi", ".mov", ".mkv"] };

const event = (old_status: TableEvent["old_status"], new_status: TableEvent["new_status"]): TableEvent => ({
  id: 1,
  video_id: "v",
  table_id: "T1",
  table_name: "Table 1",
  old_status,
  new_status,
  timestamp: 0,
});

describe("formatDuration", () => {
  it("shows mm:ss, and hours only when needed", () => {
    expect(formatDuration(0)).toBe("00:00");
    expect(formatDuration(75.9)).toBe("01:15");
    expect(formatDuration(3725)).toBe("1:02:05");
    expect(formatDuration(-5)).toBe("00:00");
  });
});

describe("formatBytes", () => {
  it("picks a readable unit", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("uploadProblem", () => {
  it("accepts supported videos within the size limit", () => {
    expect(uploadProblem({ name: "Clip.MP4", size: 1000 }, limits)).toBeNull();
  });
  it("rejects other types, empty and too large files", () => {
    expect(uploadProblem({ name: "notes.txt", size: 10 }, limits)).toMatch(/supported/);
    expect(uploadProblem({ name: "noextension", size: 10 }, limits)).toMatch(/supported/);
    expect(uploadProblem({ name: "a.mkv", size: 0 }, limits)).toMatch(/empty/);
    expect(uploadProblem({ name: "a.mov", size: 2 * 1024 * 1024 }, limits)).toMatch(/1 MB/);
  });
});

describe("isLogWorthy", () => {
  it("keeps real arrivals and departures only", () => {
    expect(isLogWorthy(event("PENDING_OCCUPIED", "OCCUPIED"))).toBe(true);
    expect(isLogWorthy(event("PENDING_AVAILABLE", "AVAILABLE"))).toBe(true);
    expect(isLogWorthy(event("PENDING_OCCUPIED", "AVAILABLE"))).toBe(false); // a passer-by
    expect(isLogWorthy(event("AVAILABLE", "PENDING_OCCUPIED"))).toBe(false);
    expect(isLogWorthy(event("OCCUPIED", "PENDING_AVAILABLE"))).toBe(false);
  });
});

describe("connectionLabel", () => {
  it("combines the socket and the camera state", () => {
    expect(connectionLabel(true, 0, "LIVE")).toBe("LIVE");
    expect(connectionLabel(true, 0, "RECONNECTING")).toBe("RECONNECTING");
    expect(connectionLabel(true, 0, "NO SOURCE")).toBe("OFFLINE");
    expect(connectionLabel(false, 1, "LIVE")).toBe("RECONNECTING");
    expect(connectionLabel(false, 3, "LIVE")).toBe("OFFLINE");
  });
});
