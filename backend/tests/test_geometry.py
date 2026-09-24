"""Tests for the table outline checks."""

from __future__ import annotations

import pytest

import numpy as np

from app.geometry import (
    compactness,
    outline_problem,
    polygon_area,
    self_intersects,
    suggest_table_outlines,
    tidy_polygon,
)

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


def test_suggestions_grow_tables_over_their_chairs_and_people() -> None:
    tables = np.array([[100, 100, 200, 160], [102, 101, 199, 161], [400, 100, 500, 160]])
    scores = np.array([0.5, 0.2, 0.4])  # the second box is a duplicate of the first
    chairs = np.array([
        [205, 110, 235, 150],  # right of table 1
        [430, 170, 470, 200],  # below table 2
        [600, 300, 630, 340],  # far from everything
    ])
    people = np.array([[150, 190], [900, 900]])  # sitting at table 1; far away
    outlines = suggest_table_outlines(tables, scores, chairs, people, frame_size=(640, 360))
    assert len(outlines) == 2
    first, second = outlines
    assert first == [(100, 100), (235, 100), (235, 196), (100, 196)]  # chair + seated person included
    # The chair below grows it by at most 60% of its height (36 px).
    assert second == [(400, 100), (500, 100), (500, 196), (400, 196)]
    assert all(outline_problem(outline, (640, 360)) is None for outline in outlines)


def test_nested_duplicates_are_dropped_and_neighbours_do_not_overlap() -> None:
    tables = np.array([[100, 100, 200, 160], [250, 100, 350, 160], [120, 105, 180, 150]])
    scores = np.array([0.5, 0.4, 0.3])  # the third box lies inside the first
    chairs = np.array([[185, 110, 255, 150]])  # between both tables, nearer the first
    outlines = suggest_table_outlines(tables, scores, chairs, np.empty((0, 2)), frame_size=(640, 360))
    assert outlines == [
        [(100, 100), (250, 100), (250, 160), (100, 160)],  # grew over the chair up to the other table
        [(250, 100), (350, 100), (350, 160), (250, 160)],  # untouched: the cut is at its edge
    ]


def test_no_tables_no_suggestions() -> None:
    empty = np.empty((0, 4))
    assert suggest_table_outlines(empty, np.empty(0), empty, np.empty((0, 2)), (640, 360)) == []


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
