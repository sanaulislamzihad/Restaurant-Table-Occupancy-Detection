import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import LiveMonitorPage from "./pages/LiveMonitorPage";
import VideosPage from "./pages/VideosPage";

// The table editor and the charts bring big libraries; load them only when opened.
const TableSetupPage = lazy(() => import("./pages/TableSetupPage"));
const AnalyticsPage = lazy(() => import("./pages/AnalyticsPage"));

const loading = (page: ReactNode) => (
  <Suspense fallback={<p className="text-sm text-slate-400">Loading…</p>}>{page}</Suspense>
);

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/videos" replace />} />
        <Route path="videos" element={<VideosPage />} />
        <Route path="live" element={<LiveMonitorPage />} />
        <Route path="setup/:videoId?" element={loading(<TableSetupPage />)} />
        <Route path="analytics" element={loading(<AnalyticsPage />)} />
        <Route path="*" element={<Navigate to="/videos" replace />} />
      </Route>
    </Routes>
  );
}
