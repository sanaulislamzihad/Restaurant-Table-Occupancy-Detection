// Polygon helpers for the table editor. The outline checks match
// backend/app/geometry.py, so the editor rejects what the backend would reject.

export type Point = [number, number];

export interface Size {
  width: number;
  height: number;
}

/** 4*pi*area / perimeter^2 below this is a sliver, not a table (same as the backend). */
export const MIN_COMPACTNESS = 0.2;
/** A table with its chairs covers far more of the frame than this (same as the backend). */
export const MIN_AREA_FRACTION = 0.003;

/** Area enclosed by the polygon (shoelace formula). */
export function polygonArea(points: Point[]): number {
  let twice = 0;
  points.forEach(([x1, y1], i) => {
    const [x2, y2] = points[(i + 1) % points.length];
    twice += x1 * y2 - x2 * y1;
  });
  return Math.abs(twice) / 2;
}

/** How "round" the polygon is: near 0 for a line or sliver, 1 for a circle. */
export function compactness(points: Point[]): number {
  let perimeter = 0;
  points.forEach(([x1, y1], i) => {
    const [x2, y2] = points[(i + 1) % points.length];
    perimeter += Math.hypot(x2 - x1, y2 - y1);
  });
  return perimeter === 0 ? 0 : (4 * Math.PI * polygonArea(points)) / perimeter ** 2;
}

const orientation = (a: Point, b: Point, c: Point) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);

/** True if segment p1-p2 properly crosses segment q1-q2. */
function segmentsCross(p1: Point, p2: Point, q1: Point, q2: Point): boolean {
  return (
    orientation(q1, q2, p1) * orientation(q1, q2, p2) < 0 && orientation(p1, p2, q1) * orientation(p1, p2, q2) < 0
  );
}

/** True if two non-neighbouring edges of the polygon cross each other. */
export function selfIntersects(points: Point[]): boolean {
  const n = points.length;
  for (let i = 0; i < n; i++) {
    for (let j = i + 2; j < n; j++) {
      if (i === 0 && j === n - 1) continue; // first and last edge share a corner
      if (segmentsCross(points[i], points[(i + 1) % n], points[j], points[(j + 1) % n])) return true;
    }
  }
  return false;
}

/** Smallest convex polygon around the points (monotone chain). */
export function convexHull(points: Point[]): Point[] {
  const sorted = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (sorted.length < 3) return sorted;
  const half = (list: Point[]) => {
    const hull: Point[] = [];
    for (const point of list) {
      while (hull.length >= 2 && orientation(hull[hull.length - 2], hull[hull.length - 1], point) <= 0) hull.pop();
      hull.push(point);
    }
    hull.pop();
    return hull;
  };
  return [...half(sorted), ...half([...sorted].reverse())];
}

/** Fix corners clicked in a criss-cross order by using their convex hull. */
export function tidyPolygon(points: Point[]): Point[] {
  return points.length >= 4 && selfIntersects(points) ? convexHull(points) : points;
}

/** Why the outline cannot be used as a table, or null if it is fine. */
export function outlineProblem(points: Point[], frame: Size): string | null {
  if (points.length < 3) return "needs at least 3 corners";
  if (selfIntersects(points)) return "its edges cross each other";
  if (polygonArea(points) < MIN_AREA_FRACTION * frame.width * frame.height) return "it is too small";
  if (compactness(points) < MIN_COMPACTNESS) return "it is too thin (the corners are almost in a line)";
  return null;
}

/** Scale points from one frame size to another. */
export function scalePolygon(points: Point[], from: Size, to: Size): Point[] {
  const sx = to.width / from.width;
  const sy = to.height / from.height;
  return points.map(([x, y]) => [x * sx, y * sy]);
}

/** Move points by (dx, dy), but never past the frame edges. */
export function movePolygon(points: Point[], dx: number, dy: number, frame: Size): Point[] {
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const clampedDx = Math.min(Math.max(dx, -Math.min(...xs)), frame.width - 1 - Math.max(...xs));
  const clampedDy = Math.min(Math.max(dy, -Math.min(...ys)), frame.height - 1 - Math.max(...ys));
  return points.map(([x, y]) => [x + clampedDx, y + clampedDy]);
}

export const clampPoint = ([x, y]: Point, frame: Size): Point => [
  Math.min(Math.max(x, 0), frame.width - 1),
  Math.min(Math.max(y, 0), frame.height - 1),
];

/** Average of the corners: where a table's name label goes. */
export function centroid(points: Point[]): Point {
  const sum = points.reduce<Point>((acc, [x, y]) => [acc[0] + x, acc[1] + y], [0, 0]);
  return [sum[0] / points.length, sum[1] / points.length];
}

/** Ray casting: true if the point is inside the polygon. */
export function pointInPolygon([x, y]: Point, polygon: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i];
    const [xj, yj] = polygon[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** True if the two outlines share any area (a person there would count for both tables). */
export function polygonsOverlap(a: Point[], b: Point[]): boolean {
  for (let i = 0; i < a.length; i++) {
    for (let j = 0; j < b.length; j++) {
      if (segmentsCross(a[i], a[(i + 1) % a.length], b[j], b[(j + 1) % b.length])) return true;
    }
  }
  return pointInPolygon(centroid(a), b) || pointInPolygon(centroid(b), a);
}

/** Distance from p to the segment a-b. */
function distanceToSegment(p: Point, a: Point, b: Point): number {
  const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
  const lengthSq = dx * dx + dy * dy;
  const t = lengthSq === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / lengthSq));
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
}

/** The polygon with p added as a new corner on the edge nearest to it. */
export function insertCorner(points: Point[], p: Point): Point[] {
  let best = 0;
  let bestDistance = Infinity;
  points.forEach((a, i) => {
    const distance = distanceToSegment(p, a, points[(i + 1) % points.length]);
    if (distance < bestDistance) [best, bestDistance] = [i, distance];
  });
  return [...points.slice(0, best + 1), p, ...points.slice(best + 1)];
}

/** First unused table ID of the form T1, T2, … */
export function nextTableId(ids: string[]): string {
  let n = 1;
  while (ids.includes(`T${n}`)) n++;
  return `T${n}`;
}

/** First unused name of the form "Table 1", "Table 2", … */
export function nextTableName(names: string[]): string {
  let n = 1;
  while (names.includes(`Table ${n}`)) n++;
  return `Table ${n}`;
}

/** Points rounded to whole pixels, for saving. */
export const roundPolygon = (points: Point[]): Point[] => points.map(([x, y]) => [Math.round(x), Math.round(y)]);
