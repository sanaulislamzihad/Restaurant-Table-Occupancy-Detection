"""Tests for the table outline checks."""

from __future__ import annotations

import pytest

from app.geometry import compactness, outline_problem, polygon_area, self_intersects, tidy_polygon

SQUARE = [(100.0, 100.0), (200.0, 100.0), (200.0, 200.0), (100.0, 200.0)]
BOW_TIE = [(100.0, 100.0), (200.0, 100.0), (100.0, 200.0), (200.0, 200.0)]  # corners clicked criss-cross
FRAME = (640, 360)


def test_area_and_compactness_of_a_square() -> None:
    assert polygon_area(SQUARE) == 10_000
    assert compactness(SQUARE) == pytest.approx(0.785, abs=0.01)


def test_bow_tie_is_detected_and_tidied_into_the_square() -> None:
    assert self_intersects(BOW_TIE)
    assert not self_intersects(SQUARE)
    tidy = tidy_polygon(BOW_TIE)
    assert not self_intersects(tidy)
    assert polygon_area(tidy) == pytest.approx(10_000)


def test_good_outline_is_left_alone() -> None:
    assert tidy_polygon(SQUARE) == SQUARE
    assert outline_problem(SQUARE, FRAME) is None


def test_points_in_a_line_and_slivers_are_rejected() -> None:
    # Clicks along one edge of a table, taken from a real drawing.
    line = [(224.0, 316.0), (282.0, 294.0), (355.0, 268.0)]
    assert outline_problem(line, FRAME) is not None
    sliver = [(100.0, 100.0), (400.0, 100.0), (400.0, 115.0), (100.0, 115.0)]  # 20:1
    assert outline_problem(sliver, FRAME) == "it is too thin (the corners are almost in a line)"
    thin_triangle = [(244.0, 249.0), (204.0, 274.0), (291.0, 240.0)]
    assert outline_problem(thin_triangle, FRAME) is not None


def test_normal_table_shapes_are_accepted() -> None:
    perspective_quad = [(200.0, 225.0), (330.0, 225.0), (345.0, 320.0), (185.0, 320.0)]
    triangle = [(300.0, 100.0), (360.0, 100.0), (330.0, 150.0)]
    assert outline_problem(perspective_quad, FRAME) is None
    assert outline_problem(triangle, FRAME) is None


def test_tiny_and_crossed_outlines_are_rejected() -> None:
    tiny = [(618.0, 175.0), (610.0, 178.0), (606.0, 174.0), (610.0, 172.0)]
    assert outline_problem(tiny, FRAME) == "it is too small"
    assert outline_problem(BOW_TIE, FRAME) == "its edges cross each other"
    assert outline_problem(SQUARE[:2], FRAME) == "needs at least 3 corners"
