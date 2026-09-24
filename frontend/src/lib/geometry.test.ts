import { describe, expect, it } from "vitest";

import {
  compactness,
  insertCorner,
  movePolygon,
  nextTableId,
  nextTableName,
  outlineProblem,
  pointInPolygon,
  polygonArea,
  polygonsOverlap,
  scalePolygon,
  selfIntersects,
  tidyPolygon,
  type Point,
} from "./geometry";

const SQUARE: Point[] = [
  [100, 100],
  [200, 100],
  [200, 200],
  [100, 200],
];
const BOW_TIE: Point[] = [
  [100, 100],
  [200, 100],
  [100, 200],
  [200, 200],
];
const FRAME = { width: 640, height: 360 };

describe("outline checks (same cases as the backend tests)", () => {
  it("measures a square", () => {
    expect(polygonArea(SQUARE)).toBe(10_000);
    expect(compactness(SQUARE)).toBeCloseTo(0.785, 2);
  });

  it("tidies a bow tie into the square", () => {
    expect(selfIntersects(BOW_TIE)).toBe(true);
    expect(selfIntersects(SQUARE)).toBe(false);
    const tidy = tidyPolygon(BOW_TIE);
    expect(selfIntersects(tidy)).toBe(false);
    expect(polygonArea(tidy)).toBe(10_000);
    expect(tidyPolygon(SQUARE)).toBe(SQUARE);
  });

  it("accepts normal table shapes", () => {
    expect(outlineProblem(SQUARE, FRAME)).toBeNull();
    const quad: Point[] = [
      [200, 225],
      [330, 225],
      [345, 320],
      [185, 320],
    ];
    expect(outlineProblem(quad, FRAME)).toBeNull();
  });

  it("rejects lines, slivers, tiny and crossed outlines", () => {
    const line: Point[] = [
      [224, 316],
      [282, 294],
      [355, 268],
    ];
    expect(outlineProblem(line, FRAME)).not.toBeNull();
    const sliver: Point[] = [
      [100, 100],
      [400, 100],
      [400, 115],
      [100, 115],
    ];
    expect(outlineProblem(sliver, FRAME)).toBe("it is too thin (the corners are almost in a line)");
    const tiny: Point[] = [
      [618, 175],
      [610, 178],
      [606, 174],
      [610, 172],
    ];
    expect(outlineProblem(tiny, FRAME)).toBe("it is too small");
    expect(outlineProblem(BOW_TIE, FRAME)).toBe("its edges cross each other");
    expect(outlineProblem(SQUARE.slice(0, 2), FRAME)).toBe("needs at least 3 corners");
  });
});

describe("moving and scaling", () => {
  it("scales between frame sizes", () => {
    expect(scalePolygon([[320, 180]], FRAME, { width: 1280, height: 720 })).toEqual([[640, 360]]);
  });

  it("stops at the frame edge", () => {
    const moved = movePolygon(SQUARE, -500, 1000, FRAME);
    expect(moved[0]).toEqual([0, 259]);
    expect(moved[2]).toEqual([100, 359]);
  });
});

describe("overlap", () => {
  it("finds inside points and overlapping outlines", () => {
    expect(pointInPolygon([150, 150], SQUARE)).toBe(true);
    expect(pointInPolygon([250, 150], SQUARE)).toBe(false);
    const shifted = scalePolygon(SQUARE, FRAME, FRAME).map(([x, y]) => [x + 50, y] as Point);
    const far = SQUARE.map(([x, y]) => [x + 300, y] as Point);
    const inner = SQUARE.map(([x, y]) => [x / 4 + 110, y / 4 + 110] as Point);
    expect(polygonsOverlap(SQUARE, shifted)).toBe(true);
    expect(polygonsOverlap(SQUARE, far)).toBe(false);
    expect(polygonsOverlap(SQUARE, inner)).toBe(true); // fully inside, no edges cross
  });
});

describe("insertCorner", () => {
  it("adds the corner on the nearest edge", () => {
    expect(insertCorner(SQUARE, [150, 205])).toEqual([[100, 100], [200, 100], [200, 200], [150, 205], [100, 200]]);
    expect(insertCorner(SQUARE, [95, 150])).toEqual([...SQUARE, [95, 150]]); // closing edge
  });
});

describe("new table ids and names", () => {
  it("uses the first free number", () => {
    expect(nextTableId([])).toBe("T1");
    expect(nextTableId(["T1", "T3"])).toBe("T2");
    expect(nextTableName(["Table 1", "Window"])).toBe("Table 2");
  });
});
