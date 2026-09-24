import { useEffect, useState } from "react";

import { apiUrl } from "../api/client";

/**
 * The annotated MJPEG stream from /api/stream. It is reloaded when the backend
 * connection comes back (reloadKey changes) and retried if it breaks.
 */
export default function LiveVideo({ reloadKey }: { reloadKey: number }) {
  const [attempt, setAttempt] = useState(0);
  const [broken, setBroken] = useState(false);

  useEffect(() => {
    setBroken(false);
    setAttempt((value) => value + 1);
  }, [reloadKey]);

  const retryLater = () => {
    setBroken(true);
    window.setTimeout(() => {
      setBroken(false);
      setAttempt((value) => value + 1);
    }, 3000);
  };

  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-xl bg-slate-900">
      {!broken && (
        <img
          key={attempt}
          src={`${apiUrl("/api/stream")}?attempt=${attempt}`}
          alt="Live camera with table status"
          className="h-full w-full object-contain"
          onError={retryLater}
        />
      )}
      {broken && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-400">
          Video not reachable, retrying…
        </div>
      )}
    </div>
  );
}
