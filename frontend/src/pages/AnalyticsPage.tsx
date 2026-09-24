import { Card, PageHeader } from "../components/ui";

/** Occupancy statistics (filled in once the analytics API exists). */
export default function AnalyticsPage() {
  return (
    <div className="space-y-6">
      <PageHeader title="Analytics" subtitle="Occupied time and sessions per table, and occupancy over time." />
      <Card className="px-5 py-8 text-center text-sm text-slate-500">Analytics are not available yet.</Card>
    </div>
  );
}
