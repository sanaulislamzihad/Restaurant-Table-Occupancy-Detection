import { Navigate, Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import AnalyticsPage from "./pages/AnalyticsPage";
import LiveMonitorPage from "./pages/LiveMonitorPage";
import TableSetupPage from "./pages/TableSetupPage";
import VideosPage from "./pages/VideosPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/videos" replace />} />
        <Route path="videos" element={<VideosPage />} />
        <Route path="live" element={<LiveMonitorPage />} />
        <Route path="setup/:videoId?" element={<TableSetupPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="*" element={<Navigate to="/videos" replace />} />
      </Route>
    </Routes>
  );
}
