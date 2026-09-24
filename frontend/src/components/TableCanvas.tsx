import type { KonvaEventObject } from "konva/lib/Node";
import { useEffect, useRef, useState } from "react";
import { Circle, Image as KonvaImage, Label, Layer, Line, Stage, Tag, Text } from "react-konva";

import { centroid, clampPoint, insertCorner, movePolygon, type Point, type Size } from "../lib/geometry";

export interface EditorTable {
  id: string;
  name: string;
  /** Corners in pixels of the editor frame. */
  polygon: Point[];
}

export const TABLE_COLORS = ["#10b981", "#3b82f6", "#f59e0b", "#8b5cf6", "#06b6d4", "#84cc16", "#f97316", "#6366f1"];
const PROBLEM_COLOR = "#ef4444";
const PERSON_COLOR = "#ff00b4"; // same pink as the people dots of the desktop tools
const HANDLE_RADIUS = 6; // screen pixels
const CLOSE_RADIUS = 10; // clicking this close to the first corner finishes the outline

export const tableColor = (index: number) => TABLE_COLORS[index % TABLE_COLORS.length];

interface TableCanvasProps {
  image: HTMLImageElement | null;
  frame: Size;
  tables: EditorTable[];
  problems: Record<string, string | null>;
  selectedId: string | null;
  /** Corners of the outline being drawn, or null when not drawing. */
  draft: Point[] | null;
  people: Point[];
  showPeople: boolean;
  onSelect: (id: string | null) => void;
  onAddCorner: (point: Point) => void;
  onFinishDraft: () => void;
  onChangePolygon: (id: string, polygon: Point[]) => void;
}

const flat = (points: Point[]) => points.flat();

/**
 * The frame with the table outlines on top. Everything is drawn in frame
 * pixels; the layer is scaled to fit the width of the page.
 */
export default function TableCanvas({
  image,
  frame,
  tables,
  problems,
  selectedId,
  draft,
  people,
  showPeople,
  onSelect,
  onAddCorner,
  onFinishDraft,
  onChangePolygon,
}: TableCanvasProps) {
  const wrapper = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<Point | null>(null);
  const dragStart = useRef<Point[] | null>(null);

  useEffect(() => {
    const element = wrapper.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const scale = width > 0 ? width / frame.width : 1;
  const height = Math.round(frame.height * scale);
  const px = (screenPixels: number) => screenPixels / scale; // screen size -> frame pixels

  const pointer = (event: KonvaEventObject<Event>): Point | null => {
    const position = event.target.getStage()?.getPointerPosition();
    return position ? clampPoint([position.x / scale, position.y / scale], frame) : null;
  };

  const setCursor = (event: KonvaEventObject<Event>, cursor: string) => {
    const container = event.target.getStage()?.container();
    if (container) container.style.cursor = cursor;
  };

  const handleClick = (event: KonvaEventObject<MouseEvent | TouchEvent>) => {
    if ("button" in event.evt && event.evt.button !== 0) return;
    const point = pointer(event);
    if (!point) return;
    if (draft) {
      const first = draft[0];
      if (draft.length >= 3 && Math.hypot(point[0] - first[0], point[1] - first[1]) * scale <= CLOSE_RADIUS) {
        onFinishDraft();
      } else {
        onAddCorner(point);
      }
      return;
    }
    const target = event.target;
    if (target === target.getStage() || target.getClassName() === "Image") onSelect(null);
  };

  const nearFirst =
    draft && hover && draft.length >= 3 && Math.hypot(hover[0] - draft[0][0], hover[1] - draft[0][1]) * scale <= CLOSE_RADIUS;

  return (
    <div
      ref={wrapper}
      className={`w-full overflow-hidden rounded-lg bg-slate-900 ${draft ? "cursor-crosshair" : ""}`}
      style={{ height: height || undefined, aspectRatio: width ? undefined : `${frame.width} / ${frame.height}` }}
    >
      {width > 0 && (
        <Stage
          width={width}
          height={height}
          onClick={handleClick}
          onTap={handleClick}
          onMouseMove={(event) => draft && setHover(pointer(event))}
          onMouseLeave={() => setHover(null)}
          onContextMenu={(event) => event.evt.preventDefault()}
        >
          <Layer scaleX={scale} scaleY={scale}>
            {image && <KonvaImage image={image} width={frame.width} height={frame.height} />}

            {tables.map((table, index) => {
              const selected = table.id === selectedId;
              const color = problems[table.id] ? PROBLEM_COLOR : tableColor(index);
              const dimmed = selectedId !== null && !selected;
              return (
                <Line
                  key={table.id}
                  points={flat(table.polygon)}
                  closed
                  fill={`${color}${selected ? "40" : "26"}`}
                  stroke={color}
                  strokeWidth={selected ? 3 : 2}
                  strokeScaleEnabled={false}
                  dash={problems[table.id] ? [6, 4] : undefined}
                  opacity={dimmed ? 0.6 : 1}
                  draggable={selected && !draft}
                  onClick={(event) => {
                    if (draft) return; // keep drawing: the click adds a corner
                    event.cancelBubble = true;
                    onSelect(table.id);
                  }}
                  onTap={(event) => {
                    if (draft) return;
                    event.cancelBubble = true;
                    onSelect(table.id);
                  }}
                  onDblClick={(event) => {
                    const point = pointer(event);
                    if (selected && !draft && point) onChangePolygon(table.id, insertCorner(table.polygon, point));
                  }}
                  onMouseEnter={(event) => !draft && setCursor(event, selected ? "move" : "pointer")}
                  onMouseLeave={(event) => !draft && setCursor(event, "default")}
                  onDragStart={() => {
                    dragStart.current = table.polygon;
                  }}
                  onDragMove={(event) => {
                    // Konva moves the line; move its corners instead and keep the line at the origin.
                    const node = event.target;
                    const [dx, dy] = [node.x(), node.y()];
                    node.position({ x: 0, y: 0 });
                    if (dragStart.current) onChangePolygon(table.id, movePolygon(dragStart.current, dx, dy, frame));
                  }}
                  onDragEnd={() => {
                    dragStart.current = null;
                  }}
                />
              );
            })}

            {tables.map((table, index) => {
              const [x, y] = centroid(table.polygon);
              return (
                <Label key={`label-${table.id}`} x={x} y={y} listening={false}>
                  <Tag
                    fill={problems[table.id] ? PROBLEM_COLOR : tableColor(index)}
                    cornerRadius={px(4)}
                    pointerDirection="down"
                    pointerWidth={px(8)}
                    pointerHeight={px(5)}
                  />
                  <Text text={table.name} fontSize={px(12)} fontStyle="bold" padding={px(4)} fill="#ffffff" />
                </Label>
              );
            })}

            {showPeople &&
              people.map(([x, y], index) => (
                <Circle
                  key={`person-${index}`}
                  x={x}
                  y={y}
                  radius={px(4.5)}
                  fill={PERSON_COLOR}
                  stroke="#ffffff"
                  strokeWidth={1}
                  strokeScaleEnabled={false}
                  listening={false}
                />
              ))}

            {!draft &&
              tables
                .filter((table) => table.id === selectedId)
                .flatMap((table) =>
                  table.polygon.map(([x, y], corner) => (
                    <Circle
                      key={`corner-${table.id}-${corner}`}
                      x={x}
                      y={y}
                      radius={px(HANDLE_RADIUS)}
                      fill="#ffffff"
                      stroke="#0f172a"
                      strokeWidth={2}
                      strokeScaleEnabled={false}
                      draggable
                      onMouseEnter={(event) => setCursor(event, "grab")}
                      onMouseLeave={(event) => setCursor(event, "default")}
                      onDragMove={(event) => {
                        const node = event.target;
                        const point = clampPoint([node.x(), node.y()], frame);
                        node.position({ x: point[0], y: point[1] });
                        onChangePolygon(
                          table.id,
                          table.polygon.map((p, i) => (i === corner ? point : p)),
                        );
                      }}
                      onContextMenu={(event) => {
                        event.evt.preventDefault();
                        if (table.polygon.length > 3) {
                          onChangePolygon(
                            table.id,
                            table.polygon.filter((_, i) => i !== corner),
                          );
                        }
                      }}
                    />
                  )),
                )}

            {draft && (
              <>
                <Line
                  points={flat(hover ? [...draft, hover] : draft)}
                  stroke="#facc15"
                  strokeWidth={2}
                  strokeScaleEnabled={false}
                  dash={[6, 4]}
                  listening={false}
                />
                {draft.map(([x, y], index) => (
                  <Circle
                    key={`draft-${index}`}
                    x={x}
                    y={y}
                    radius={px(index === 0 && nearFirst ? HANDLE_RADIUS + 3 : HANDLE_RADIUS - 1)}
                    fill={index === 0 ? "#facc15" : "#ffffff"}
                    stroke="#0f172a"
                    strokeWidth={1.5}
                    strokeScaleEnabled={false}
                    listening={false}
                  />
                ))}
              </>
            )}
          </Layer>
        </Stage>
      )}
    </div>
  );
}
