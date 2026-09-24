import type { TableEvent } from "../api/types";
import { formatClock, isLogWorthy } from "../lib/format";

/** Newest-first list of tables becoming OCCUPIED / AVAILABLE. */
export default function EventLog({ events, videoId }: { events: TableEvent[]; videoId: string | null }) {
  const shown = events.filter((event) => isLogWorthy(event) && (videoId === null || event.video_id === videoId));
  if (shown.length === 0) {
    return <p className="px-4 py-6 text-center text-sm text-slate-400">No table has changed yet.</p>;
  }
  return (
    <ul className="divide-y divide-slate-100">
      {shown.map((event) => {
        const occupied = event.new_status === "OCCUPIED";
        return (
          <li key={event.id} className="flex items-center gap-3 px-4 py-2 text-sm">
            <span className={`h-2 w-2 shrink-0 rounded-full ${occupied ? "bg-red-500" : "bg-emerald-500"}`} />
            <span className="font-mono text-xs text-slate-500">{formatClock(event.timestamp)}</span>
            <span className="text-slate-700">
              {event.table_name} became{" "}
              <span className={`font-semibold ${occupied ? "text-red-700" : "text-emerald-700"}`}>
                {occupied ? "OCCUPIED" : "AVAILABLE"}
              </span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
