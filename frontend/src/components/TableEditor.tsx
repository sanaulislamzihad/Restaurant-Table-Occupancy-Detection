import {
  Check,
  Copy,
  Eye,
  EyeOff,
  LoaderCircle,
  PenLine,
  RefreshCw,
  Save,
  Sparkles,
  Trash,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import type { EditorFrame, Video } from "../api/types";
import { formatDuration } from "../lib/format";
import {
  centroid,
  nextTableId,
  nextTableName,
  outlineProblem,
  pointInPolygon,
  polygonsOverlap,
  roundPolygon,
  scalePolygon,
  tidyPolygon,
  type Point,
  type Size,
} from "../lib/geometry";
import TableCanvas, { tableColor, type EditorTable } from "./TableCanvas";
import { Banner, Button, Card, ErrorBanner } from "./ui";

const FRAME_STEP_SECONDS = 7; // "Another frame" jumps this far into the video file

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

const frameSize = (frame: EditorFrame): Size => ({ width: frame.frame_width, height: frame.frame_height });

/** Tables in a stable text form, to tell whether anything changed since the last save. */
const snapshot = (tables: EditorTable[]) =>
  JSON.stringify(tables.map((table) => ({ ...table, polygon: roundPolygon(table.polygon) })));

function useHtmlImage(src: string | null): HTMLImageElement | null {
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  useEffect(() => {
    if (!src) return;
    const element = new window.Image();
    element.onload = () => setImage(element);
    element.src = src;
    return () => {
      element.onload = null;
    };
  }, [src]);
  return image;
}

const isTyping = (target: EventTarget | null) =>
  target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);

/** Draw, adjust, name and save the table outlines of one video. */
export default function TableEditor({ video, videos }: { video: Video; videos: Video[] }) {
  const [frame, setFrame] = useState<EditorFrame | null>(null);
  const [loading, setLoading] = useState(true);
  const [tables, setTables] = useState<EditorTable[]>([]);
  const [savedSnapshot, setSavedSnapshot] = useState(snapshot([]));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Point[] | null>(null);
  const [showPeople, setShowPeople] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [focusId, setFocusId] = useState<string | null>(null);
  const nameInputs = useRef<Record<string, HTMLInputElement | null>>({});
  const image = useHtmlImage(frame?.image ?? null);

  // ------------------------------------------------------------------ loading

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([api.editorFrame(video.id), api.videoConfig(video.id)])
      .then(([loadedFrame, config]) => {
        if (cancelled) return;
        const loaded = (config?.tables ?? []).map((table) => ({
          ...table,
          polygon: scalePolygon(
            table.polygon,
            { width: config!.frame_width, height: config!.frame_height },
            frameSize(loadedFrame),
          ),
        }));
        setFrame(loadedFrame);
        setTables(loaded);
        setSavedSnapshot(snapshot(loaded));
      })
      .catch((err) => !cancelled && setError(errorText(err)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [video.id]);

  const anotherFrame = async () => {
    if (!frame) return;
    const duration = video.duration_seconds ?? 0;
    const at = frame.live ? 1 : duration > 1 ? ((frame.at_seconds ?? 0) + FRAME_STEP_SECONDS) % duration : 1;
    setLoading(true);
    setError(null);
    try {
      const next = await api.editorFrame(video.id, { at });
      if (next.frame_width !== frame.frame_width || next.frame_height !== frame.frame_height) {
        const from = frameSize(frame);
        setTables((current) =>
          current.map((table) => ({ ...table, polygon: scalePolygon(table.polygon, from, frameSize(next)) })),
        );
      }
      setFrame(next);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  };

  // ------------------------------------------------------------------ checks

  const frameWidth = frame?.frame_width ?? 0;
  const frameHeight = frame?.frame_height ?? 0;
  const problems = useMemo(() => {
    const result: Record<string, string | null> = {};
    for (const table of tables) {
      result[table.id] = frameWidth ? outlineProblem(table.polygon, { width: frameWidth, height: frameHeight }) : null;
      if (!result[table.id] && !table.name.trim()) result[table.id] = "it needs a name";
    }
    return result;
  }, [tables, frameWidth, frameHeight]);

  const overlaps = useMemo(() => {
    const result: Record<string, string[]> = {};
    tables.forEach((a, i) =>
      tables.forEach((b, j) => {
        if (i !== j && polygonsOverlap(a.polygon, b.polygon)) (result[a.id] ??= []).push(b.name);
      }),
    );
    return result;
  }, [tables]);

  const dirty = snapshot(tables) !== savedSnapshot;
  const problemCount = Object.values(problems).filter(Boolean).length;

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  // ------------------------------------------------------------------ editing

  const changeTables = (update: (current: EditorTable[]) => EditorTable[]) => {
    setNotice(null);
    setTables(update);
  };

  const startDrawing = () => {
    setSelectedId(null);
    setDraft([]);
    setNotice(null);
  };

  const finishDraft = useCallback(() => {
    if (!draft || draft.length < 3) return;
    const id = nextTableId(tables.map((table) => table.id));
    const name = nextTableName(tables.map((table) => table.name));
    setTables([...tables, { id, name, polygon: tidyPolygon(draft) }]);
    setSelectedId(id);
    setFocusId(id);
    setDraft(null);
    setNotice(null);
  }, [draft, tables]);

  const removeTable = useCallback((id: string) => {
    setTables((current) => current.filter((table) => table.id !== id));
    setSelectedId((current) => (current === id ? null : current));
  }, []);

  const renameTable = (id: string, name: string) =>
    changeTables((current) => current.map((table) => (table.id === id ? { ...table, name } : table)));

  const changePolygon = (id: string, polygon: Point[]) =>
    changeTables((current) => current.map((table) => (table.id === id ? { ...table, polygon } : table)));

  useEffect(() => {
    if (!focusId) return;
    const input = nameInputs.current[focusId];
    input?.focus();
    input?.select();
    setFocusId(null);
  }, [focusId, tables]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isTyping(event.target)) {
        if (event.key === "Enter" || event.key === "Escape") (event.target as HTMLElement).blur();
        return;
      }
      if (draft) {
        if (event.key === "Enter") finishDraft();
        else if (event.key === "Escape") setDraft(null);
        else if (event.key === "Backspace") setDraft(draft.slice(0, -1));
        else return;
        event.preventDefault();
      } else if (event.key === "Escape") {
        setSelectedId(null);
      } else if (event.key === "Delete" && selectedId) {
        removeTable(selectedId);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [draft, finishDraft, selectedId, removeTable]);

  const suggest = () => {
    if (!frame) return;
    // Skip suggestions for tables that are already drawn.
    const fresh = frame.suggested_tables.filter(
      (outline) =>
        !tables.some(
          (table) => pointInPolygon(centroid(outline), table.polygon) || pointInPolygon(centroid(table.polygon), outline),
        ),
    );
    if (fresh.length === 0) {
      setNotice(
        frame.suggested_tables.length === 0
          ? "No tables were recognised in this frame. Try another frame, or draw them yourself."
          : "All suggested tables are already drawn.",
      );
      return;
    }
    changeTables((current) => {
      const next = [...current];
      for (const outline of fresh) {
        next.push({
          id: nextTableId(next.map((table) => table.id)),
          name: nextTableName(next.map((table) => table.name)),
          polygon: outline,
        });
      }
      return next;
    });
    setSelectedId(null);
    setNotice(
      `Added ${fresh.length} suggested table${fresh.length === 1 ? "" : "s"}. ` +
        "Check each one: drag the corners to fit, and delete the wrong ones.",
    );
  };

  const copyFrom = async (sourceId: string) => {
    if (!frame || !sourceId) return;
    const source = videos.find((item) => item.id === sourceId);
    setError(null);
    try {
      const config = await api.videoConfig(sourceId);
      if (!config || config.tables.length === 0) {
        setError(`${source?.name ?? sourceId} has no tables to copy.`);
        return;
      }
      if (
        tables.length > 0 &&
        !window.confirm(
          `Replace the ${tables.length} table(s) here with the ${config.tables.length} from "${source?.name ?? sourceId}"?`,
        )
      ) {
        return;
      }
      const from = { width: config.frame_width, height: config.frame_height };
      changeTables(() =>
        config.tables.map((table) => ({ ...table, polygon: scalePolygon(table.polygon, from, frameSize(frame)) })),
      );
      setSelectedId(null);
      setDraft(null);
      setNotice(
        `Copied ${config.tables.length} tables from "${source?.name ?? sourceId}". ` +
          "This only fits if both videos show the same camera view; adjust them if needed, then save.",
      );
    } catch (err) {
      setError(errorText(err));
    }
  };

  const clearAll = () => {
    if (tables.length > 0 && window.confirm(`Remove all ${tables.length} tables? (Nothing is saved until you press Save.)`)) {
      changeTables(() => []);
      setSelectedId(null);
    }
  };

  const save = async () => {
    if (!frame) return;
    setSaving(true);
    setError(null);
    try {
      const payload = tables.map((table) => ({ ...table, name: table.name.trim(), polygon: roundPolygon(table.polygon) }));
      const config = await api.saveTables(video.id, payload, frame.frame_width, frame.frame_height);
      const saved = config.tables.map((table) => ({ ...table }));
      setTables(saved);
      setSavedSnapshot(snapshot(saved));
      const stream = await api.streamStatus().catch(() => null);
      const live = stream?.state === "running" && stream.video_id === video.id;
      setNotice(
        live
          ? `Saved ${saved.length} tables. The Live Monitor uses them now.`
          : `Saved ${saved.length} tables. They are used when this video is streamed.`,
      );
    } catch (err) {
      setError(errorText(err));
    } finally {
      setSaving(false);
    }
  };

  // ------------------------------------------------------------------ view

  const copySources = videos.filter((item) => item.id !== video.id && item.has_tables);
  const frameLabel = frame
    ? frame.live
      ? "Live frame"
      : `Frame at ${formatDuration(frame.at_seconds ?? 0)} of the video file`
    : "";

  if (!frame) {
    return (
      <div className="space-y-4">
        {error && <ErrorBanner message={error} />}
        <Card className="flex items-center justify-center gap-3 px-5 py-16 text-sm text-slate-500">
          {loading && <LoaderCircle className="h-5 w-5 animate-spin" />}
          {loading ? "Loading the frame and looking for people and tables…" : "No frame to draw on."}
        </Card>
      </div>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
      <div className="min-w-0 space-y-3">
        <Card className="flex flex-wrap items-center gap-2 px-3 py-2">
          {draft ? (
            <>
              <Button onClick={finishDraft} disabled={draft.length < 3}>
                <Check className="h-4 w-4" /> Finish
              </Button>
              <Button variant="secondary" onClick={() => setDraft(null)}>
                <X className="h-4 w-4" /> Cancel
              </Button>
              <span className="text-xs text-slate-500">
                {draft.length} corner{draft.length === 1 ? "" : "s"}
              </span>
            </>
          ) : (
            <>
              <Button onClick={startDrawing}>
                <PenLine className="h-4 w-4" /> Draw table
              </Button>
              <Button
                variant="secondary"
                onClick={suggest}
                title="Add outlines around the tables the detector recognised in this frame"
              >
                <Sparkles className="h-4 w-4" /> Suggest tables
                {frame.suggested_tables.length > 0 && (
                  <span className="rounded-full bg-slate-100 px-1.5 text-xs text-slate-600">
                    {frame.suggested_tables.length}
                  </span>
                )}
              </Button>
              {copySources.length > 0 && (
                <label className="relative inline-flex items-center">
                  <Copy className="pointer-events-none absolute left-2.5 h-4 w-4 text-slate-500" />
                  <select
                    value=""
                    onChange={(event) => void copyFrom(event.target.value)}
                    className="rounded-md border border-slate-300 bg-white py-1.5 pl-8 pr-2 text-sm text-slate-700 hover:bg-slate-50"
                    aria-label="Copy tables from another video"
                  >
                    <option value="">Copy from video…</option>
                    {copySources.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.name} ({item.table_count})
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </>
          )}
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant="secondary"
              onClick={() => setShowPeople((value) => !value)}
              title="People's reference points: a person counts for a table when this dot is inside it"
            >
              {showPeople ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              {showPeople ? "Hide people" : "Show people"}
            </Button>
            <Button variant="secondary" onClick={() => void anotherFrame()} disabled={loading || !!draft}>
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              {frame.live ? "Newer frame" : "Another frame"}
            </Button>
          </div>
        </Card>

        <TableCanvas
          image={image}
          frame={frameSize(frame)}
          tables={tables}
          problems={problems}
          selectedId={selectedId}
          draft={draft}
          people={frame.people}
          showPeople={showPeople}
          onSelect={setSelectedId}
          onAddCorner={(point) => setDraft((current) => [...(current ?? []), point])}
          onFinishDraft={finishDraft}
          onChangePolygon={changePolygon}
        />

        <p className="text-xs text-slate-500">
          {frameLabel} · {frame.frame_width}×{frame.frame_height} · {frame.people.length} people ·{" "}
          {frame.suggested_tables.length} tables recognised
          {frame.hints_error && <span className="text-amber-700"> · {frame.hints_error}</span>}
        </p>
      </div>

      <div className="space-y-3">
        {error && <ErrorBanner message={error} onClose={() => setError(null)} />}
        {notice && <Banner tone="success">{notice}</Banner>}

        <Card className="space-y-3 p-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-800">Tables ({tables.length})</h2>
            {tables.length > 0 && (
              <button onClick={clearAll} className="text-xs text-slate-500 hover:text-red-600">
                Clear all
              </button>
            )}
          </div>
          {tables.length === 0 ? (
            <p className="text-sm text-slate-500">
              No tables yet. Press <b>Draw table</b>, or <b>Suggest tables</b> to start from the recognised ones.
            </p>
          ) : (
            <ul className="max-h-105 space-y-1.5 overflow-y-auto pr-1">
              {tables.map((table, index) => (
                <li
                  key={table.id}
                  onClick={() => !draft && setSelectedId(table.id)}
                  className={`cursor-pointer rounded-lg border px-2.5 py-2 ${
                    table.id === selectedId ? "border-slate-400 bg-slate-50" : "border-slate-200 hover:bg-slate-50"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="h-3 w-3 shrink-0 rounded-full" style={{ background: tableColor(index) }} />
                    <input
                      ref={(element) => {
                        nameInputs.current[table.id] = element;
                      }}
                      value={table.name}
                      maxLength={64}
                      onChange={(event) => renameTable(table.id, event.target.value)}
                      onFocus={() => !draft && setSelectedId(table.id)}
                      className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-sm font-medium text-slate-800 hover:border-slate-200 focus:border-slate-300 focus:bg-white focus:outline-none"
                      aria-label={`Name of ${table.id}`}
                    />
                    <button
                      onClick={(event) => {
                        event.stopPropagation();
                        removeTable(table.id);
                      }}
                      className="text-slate-400 hover:text-red-600"
                      title="Delete table"
                    >
                      <Trash className="h-4 w-4" />
                    </button>
                  </div>
                  {problems[table.id] && <p className="mt-1 pl-5 text-xs text-red-600">Unusable: {problems[table.id]}</p>}
                  {!problems[table.id] && overlaps[table.id] && (
                    <p className="mt-1 pl-5 text-xs text-amber-700">Overlaps {overlaps[table.id].join(", ")}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center gap-2 border-t border-slate-100 pt-3">
            <Button onClick={() => void save()} disabled={saving || !dirty || problemCount > 0 || !!draft}>
              {saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Save
            </Button>
            <span className="text-xs text-slate-500">
              {problemCount > 0
                ? `Fix ${problemCount} table${problemCount === 1 ? "" : "s"} first`
                : dirty
                  ? "Unsaved changes"
                  : "All changes saved"}
            </span>
          </div>
        </Card>

        <Card className="space-y-1.5 p-4 text-xs leading-relaxed text-slate-500">
          <p className="font-semibold text-slate-700">How to draw</p>
          <p>
            <b>Draw table</b>, then click the corners. Click the first corner or press <b>Enter</b> to finish.{" "}
            <b>Backspace</b> removes the last corner, <b>Esc</b> cancels.
          </p>
          <p>
            Click a table to select it: drag it or its corners. Double-click an edge to add a corner, right-click a
            corner to remove it, <b>Delete</b> removes the table.
          </p>
          <p>
            Include the chairs: a person counts for a table when their pink dot is inside its outline. Outlines should
            not overlap.
          </p>
        </Card>
      </div>
    </div>
  );
}
