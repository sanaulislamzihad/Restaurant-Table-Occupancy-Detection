import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { OccupancyPoint } from "../api/types";
import { formatSpan, formatTick } from "../lib/format";

// Occupancy is a magnitude, so it gets a neutral series colour; green and red
// stay reserved for the AVAILABLE / OCCUPIED status.
const SERIES = "#2a78d6";
const GRID = "#e2e8f0";
const AXIS_TEXT = "#64748b";
const MAX_TICKS = 6;

interface ChartRow {
  t: number;
  start: number;
  end: number;
  value: number | null;
  tables: number | null;
  watched: number;
}

function ChartTooltip({
  row,
  bucketSeconds,
  tableCount,
  withDate,
}: {
  row: ChartRow;
  bucketSeconds: number;
  tableCount: number;
  withDate: boolean;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-md">
      <p className="text-slate-500">
        {formatTick(row.start, bucketSeconds, withDate)} – {formatTick(row.end, bucketSeconds)}
      </p>
      {row.value === null ? (
        <p className="mt-1 font-medium text-slate-700">Not watched</p>
      ) : (
        <>
          <p className="mt-1 flex items-center gap-2">
            <span className="h-0.5 w-3 rounded-full" style={{ background: SERIES }} />
            <span className="text-sm font-semibold text-slate-900">{Math.round(row.value)}%</span>
            <span className="text-slate-500">occupied</span>
          </p>
          <p className="text-slate-500">
            {(row.tables ?? 0).toFixed(1)} of {tableCount} tables on average
          </p>
          {row.watched < bucketSeconds - 0.5 && <p className="text-slate-400">watched {formatSpan(row.watched)} of it</p>}
        </>
      )}
    </div>
  );
}

/** Share of tables occupied over time, one point per chart step. */
export default function OccupancyChart({
  timeline,
  bucketSeconds,
  tableCount,
}: {
  timeline: OccupancyPoint[];
  bucketSeconds: number;
  tableCount: number;
}) {
  const rows: ChartRow[] = timeline.map((point) => ({
    t: (point.start + point.end) / 2,
    start: point.start,
    end: point.end,
    value: point.occupancy_rate === null ? null : point.occupancy_rate * 100,
    tables: point.occupied_tables,
    watched: point.monitored_seconds,
  }));
  if (rows.length === 0) return null;

  const first = timeline[0].start;
  const last = timeline[timeline.length - 1].end;
  const every = Math.ceil(timeline.length / MAX_TICKS);
  const ticks = timeline.filter((_, index) => index % every === 0).map((point) => point.start);
  const withDate = last - first > 86400 && bucketSeconds < 86400;
  const sparse = rows.filter((row) => row.value !== null).length <= 2;

  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke={GRID} />
        <XAxis
          dataKey="t"
          type="number"
          domain={[first, last]}
          ticks={ticks}
          tickFormatter={(value: number) => formatTick(value, bucketSeconds, withDate)}
          tick={{ fill: AXIS_TEXT, fontSize: 12 }}
          tickLine={false}
          axisLine={{ stroke: GRID }}
          minTickGap={16}
        />
        <YAxis
          domain={[0, 100]}
          ticks={[0, 25, 50, 75, 100]}
          tickFormatter={(value: number) => `${value}%`}
          tick={{ fill: AXIS_TEXT, fontSize: 12 }}
          tickLine={false}
          axisLine={false}
          width={44}
        />
        <Tooltip
          cursor={{ stroke: "#94a3b8", strokeWidth: 1 }}
          isAnimationActive={false}
          content={({ active, payload }) => {
            const row = payload?.[0]?.payload as ChartRow | undefined;
            return active && row ? (
              <ChartTooltip row={row} bucketSeconds={bucketSeconds} tableCount={tableCount} withDate={withDate} />
            ) : null;
          }}
        />
        <Area
          type="linear"
          dataKey="value"
          name="Occupied"
          stroke={SERIES}
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
          fill={SERIES}
          fillOpacity={0.1}
          connectNulls={false}
          dot={sparse ? { r: 4, fill: SERIES, stroke: "#ffffff", strokeWidth: 2 } : false}
          activeDot={{ r: 4, fill: SERIES, stroke: "#ffffff", strokeWidth: 2 }}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
