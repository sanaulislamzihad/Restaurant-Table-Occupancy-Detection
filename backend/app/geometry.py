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
