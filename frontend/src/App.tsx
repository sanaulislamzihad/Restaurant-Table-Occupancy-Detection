import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import AnalyticsPage from "./pages/AnalyticsPage";
import LiveMonitorPage from "./pages/LiveMonitorPage";
import VideosPage from "./pages/VideosPage";

// The table editor brings the canvas library; load it only when it is opened.
const TableSetupPage = lazy(() => import("./pages/TableSetupPage"));

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/videos" replace />} />
        <Route path="videos" element={<VideosPage />} />
        <Route path="live" element={<LiveMonitorPage />} />
        <Route
          path="setup/:videoId?"
          element={
            <Suspense fallback={<p className="text-sm text-slate-400">Loading…</p>}>
              <TableSetupPage />
            </Suspense>
          }
        />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="*" element={<Navigate to="/videos" replace />} />
      </Route>
    </Routes>
  );
}
