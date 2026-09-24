"""Polygon checks for table outlines.

A usable table outline encloses a real area and does not cross itself.
Clicking the corners of a table in a criss-cross order produces a "bow tie",
and clicking a few points along one edge produces a sliver with no area; both
make people land outside the table.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

Point = tuple[float, float]

# 4*pi*area / perimeter^2: 1.0 for a circle, ~0.79 for a square, ~0.6 for an
# equilateral triangle, ~0.26 for a 10:1 rectangle. Anything below this is a
# sliver (roughly thinner than 13:1), not a table.
MIN_COMPACTNESS = 0.2
# A table with its chairs covers far more of the frame than this.
MIN_AREA_FRACTION = 0.003


def polygon_area(points: list[Point]) -> float:
    """Area enclosed by the polygon (shoelace formula)."""
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    return float(abs(np.dot(xs, np.roll(ys, -1)) - np.dot(ys, np.roll(xs, -1))) / 2)


def compactness(points: list[Point]) -> float:
    """How "round" the polygon is: near 0 for a line or sliver, 1 for a circle."""
    perimeter = sum(math.dist(points[i], points[(i + 1) % len(points)]) for i in range(len(points)))
    if perimeter == 0:
        return 0.0
    return 4 * math.pi * polygon_area(points) / perimeter**2


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    """True if segment p1-p2 properly crosses segment q1-q2."""
    d1, d2 = _orientation(q1, q2, p1), _orientation(q1, q2, p2)
    d3, d4 = _orientation(p1, p2, q1), _orientation(p1, p2, q2)
    return d1 * d2 < 0 and d3 * d4 < 0


def self_intersects(points: list[Point]) -> bool:
    """True if two non-neighbouring edges of the polygon cross each other."""
    n = len(points)
    edges = [(points[i], points[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:  # first and last edge share a corner
                continue
            if _segments_cross(*edges[i], *edges[j]):
                return True
    return False


def convex_hull(points: list[Point]) -> list[Point]:
    """Smallest convex polygon around the points, in drawing order."""
    hull = cv2.convexHull(np.array(points, dtype=np.float32)).reshape(-1, 2)
    return [(float(x), float(y)) for x, y in hull]


def tidy_polygon(points: list[Point]) -> list[Point]:
    """Fix corners clicked in a criss-cross order by using their convex hull."""
    return convex_hull(points) if len(points) >= 4 and self_intersects(points) else list(points)


def _overlap_of_smaller(a: np.ndarray, b: np.ndarray) -> float:
    """Shared area of two (x1, y1, x2, y2) boxes as a fraction of the smaller one."""
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return float(inter / smaller) if smaller > 0 else 0.0


def _separate(a: np.ndarray, b: np.ndarray, core_a: np.ndarray, core_b: np.ndarray) -> None:
    """Shrink two overlapping regions (in place) so they only touch.

    The cut goes across the direction in which they overlap least, in the
    middle of the overlap, but outside both tables (cores) when there is room.
    """
    overlap_x = min(a[2], b[2]) - max(a[0], b[0])
    overlap_y = min(a[3], b[3]) - max(a[1], b[1])
    if overlap_x <= 0 or overlap_y <= 0:
        return
    low, high = (0, 2) if overlap_x <= overlap_y else (1, 3)
    if core_a[low] + core_a[high] <= core_b[low] + core_b[high]:  # a comes first along that axis
        first, second, core_first, core_second = a, b, core_a, core_b
    else:
        first, second, core_first, core_second = b, a, core_b, core_a
    cut = (max(first[low], second[low]) + min(first[high], second[high])) / 2
    if core_first[high] <= core_second[low]:
        cut = min(max(cut, core_first[high]), core_second[low])
    first[high] = min(first[high], cut)
    second[low] = max(second[low], cut)


def suggest_table_outlines(
    table_boxes: np.ndarray,
    table_scores: np.ndarray,
    chair_boxes: np.ndarray,
    people_points: np.ndarray,
    frame_size: tuple[int, int],
    reach: float = 0.6,
    duplicate_overlap: float = 0.6,
) -> list[list[Point]]:
    """Rectangles around detected tables, grown to cover their chairs and seated people.

    A person counts for a table when their reference point is inside its
    outline, and seated people are usually on the chairs next to the table, so
    a table box alone is too small. Each chair and each person's reference point
    is given to the nearest table (when within ``reach`` times the table's size
    of it), and that table's box grows to include it, by at most ``reach``
    times its size on each side. Boxes that mostly cover a better one are
    duplicates, and grown regions that overlap are cut apart, so a person is
    never counted for two suggested tables.

    Boxes are (x1, y1, x2, y2) in frame pixels; the result is one 4-corner
    polygon per table, best-scoring first.
    """
    boxes = np.asarray(table_boxes, dtype=float).reshape(-1, 4)
    kept: list[int] = []
    for index in np.argsort(-np.asarray(table_scores)):
        if all(_overlap_of_smaller(boxes[index], boxes[other]) < duplicate_overlap for other in kept):
            kept.append(int(index))
    if not kept:
        return []
    tables = boxes[kept]
    regions = tables.copy()
    centers = (tables[:, :2] + tables[:, 2:]) / 2
    sizes = tables[:, 2:] - tables[:, :2]
    limits = np.hstack([tables[:, :2] - sizes * reach, tables[:, 2:] + sizes * reach])

    def owner(point: np.ndarray) -> int | None:
        near = np.all(np.abs(point - centers) <= sizes * (0.5 + reach), axis=1)
        if not near.any():
            return None
        distances = np.linalg.norm(centers - point, axis=1)
        distances[~near] = np.inf
        return int(np.argmin(distances))

    def grow(table: int, low: np.ndarray, high: np.ndarray) -> None:
        regions[table, :2] = np.maximum(np.minimum(regions[table, :2], low), limits[table, :2])
        regions[table, 2:] = np.minimum(np.maximum(regions[table, 2:], high), limits[table, 2:])

    for box in np.asarray(chair_boxes, dtype=float).reshape(-1, 4):
        table = owner((box[:2] + box[2:]) / 2)
        if table is not None:
            grow(table, box[:2], box[2:])
    margin = 6.0  # keep a point a little inside the outline, not on its edge
    for point in np.asarray(people_points, dtype=float).reshape(-1, 2):
        table = owner(point)
        if table is not None:
            grow(table, point - margin, point + margin)

    for i in range(len(regions)):
        for j in range(i + 1, len(regions)):
            _separate(regions[i], regions[j], tables[i], tables[j])

    width, height = frame_size
    regions[:, [0, 2]] = regions[:, [0, 2]].clip(0, width - 1)
    regions[:, [1, 3]] = regions[:, [1, 3]].clip(0, height - 1)
    outlines = [
        [(round(x1), round(y1)), (round(x2), round(y1)), (round(x2), round(y2)), (round(x1), round(y2))]
        for x1, y1, x2, y2 in regions
    ]
    return [outline for outline in outlines if outline_problem(outline, frame_size) is None]


def outline_problem(points: list[Point], frame_size: tuple[int, int]) -> str | None:
    """Why the outline cannot be used as a table, or None if it is fine."""
    if len(points) < 3:
        return "needs at least 3 corners"
    if self_intersects(points):
        return "its edges cross each other"
    if polygon_area(points) < MIN_AREA_FRACTION * frame_size[0] * frame_size[1]:
        return "it is too small"
    if compactness(points) < MIN_COMPACTNESS:
        return "it is too thin (the corners are almost in a line)"
    return None
