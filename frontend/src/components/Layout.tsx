import { ChartColumn, MonitorPlay, PenLine, ScanEye, Video } from "lucide-react";
import { Link, NavLink, Outlet } from "react-router-dom";

const LINKS = [
  { to: "/videos", label: "Videos", icon: Video },
  { to: "/live", label: "Live Monitor", icon: MonitorPlay },
  { to: "/setup", label: "Table Setup", icon: PenLine },
  { to: "/analytics", label: "Analytics", icon: ChartColumn },
];

/** Top navigation bar and page frame. */
export default function Layout() {
  return (
    <div className="min-h-screen">
      <header className="bg-slate-900 text-white shadow">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4">
          <Link to="/videos" className="flex items-center gap-2 font-semibold tracking-tight">
            <ScanEye className="h-5 w-5 text-emerald-400" />
            Table Occupancy
          </Link>
          <nav className="flex gap-1">
            {LINKS.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  `flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                    isActive ? "bg-slate-700 text-white" : "text-slate-300 hover:bg-slate-800 hover:text-white"
                  }`
                }
              >
                <Icon className="h-4 w-4" />
                <span className="hidden sm:inline">{label}</span>
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
