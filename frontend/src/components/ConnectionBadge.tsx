import { Wifi, WifiOff } from "lucide-react";

import type { ConnectionLabel } from "../lib/format";

const STYLES: Record<ConnectionLabel, string> = {
  LIVE: "bg-emerald-100 text-emerald-800",
  RECONNECTING: "bg-amber-100 text-amber-800",
  OFFLINE: "bg-slate-200 text-slate-600",
};

/** LIVE / RECONNECTING / OFFLINE pill. */
export default function ConnectionBadge({ label }: { label: ConnectionLabel }) {
  const Icon = label === "OFFLINE" ? WifiOff : Wifi;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-bold tracking-wide ${STYLES[label]}`}>
      {label === "LIVE" ? (
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-600" />
        </span>
      ) : (
        <Icon className={`h-3.5 w-3.5 ${label === "RECONNECTING" ? "animate-pulse" : ""}`} />
      )}
      {label}
    </span>
  );
}
