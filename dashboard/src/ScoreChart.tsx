import type { Round } from "./types";

interface Point {
  version: number;
  accuracy: number;
  grade: number | null;
  kept: boolean;
}

/** One point for each version tried, with the version the first round started from. */
function pointsOf(rounds: Round[]): Point[] {
  const oldest = [...rounds].reverse();
  const points: Point[] = [];
  const first = oldest.find((r) => r.held_out_before);
  if (first?.held_out_before) {
    points.push({
      version: 1,
      accuracy: first.held_out_before.accuracy,
      grade: first.held_out_before.grade,
      kept: true,
    });
  }
  for (const round of oldest) {
    if (round.tried_version !== null && round.held_out_after) {
      points.push({
        version: round.tried_version,
        accuracy: round.held_out_after.accuracy,
        grade: round.held_out_after.grade,
        kept: round.verdict === "kept",
      });
    }
  }
  return points;
}

const WIDTH = 420;
const HEIGHT = 170;
const LEFT = 44;
const TOP = 14;
const BOTTOM = 32;

function Chart({
  title,
  points,
  floor,
  top,
  value,
  format,
}: Readonly<{
  title: string;
  points: Point[];
  floor: number;
  top: number;
  value: (point: Point) => number | null;
  format: (value: number) => string;
}>) {
  const plotHeight = HEIGHT - TOP - BOTTOM;
  const y = (v: number) => TOP + plotHeight * (1 - v / top);
  const step = (WIDTH - LEFT - 16) / Math.max(points.length, 1);
  const x = (index: number) => LEFT + step * (index + 0.5);
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg
        viewBox={`0 0 ${String(WIDTH)} ${String(HEIGHT)}`}
        role="img"
        aria-label={`${title}: ${points
          .map((p) => `version ${String(p.version)} ${format(value(p) ?? 0)}`)
          .join(", ")}`}
      >
        {[0, top / 2, top].map((tick) => (
          <g key={tick}>
            <text
              x={LEFT - 6}
              y={y(tick) + 4}
              textAnchor="end"
              className="axis"
            >
              {format(tick)}
            </text>
          </g>
        ))}
        <line
          x1={LEFT}
          x2={WIDTH - 8}
          y1={y(floor)}
          y2={y(floor)}
          className="floor"
        />
        <text x={WIDTH - 8} y={y(floor) - 4} textAnchor="end" className="axis">
          Objective {format(floor)}
        </text>
        {points.map((point, index) => {
          const v = value(point);
          return (
            <g key={point.version}>
              {v !== null &&
                (point.kept ? (
                  <circle cx={x(index)} cy={y(v)} r={6} className="mark-kept" />
                ) : (
                  <rect
                    x={x(index) - 5}
                    y={y(v) - 5}
                    width={10}
                    height={10}
                    transform={`rotate(45 ${String(x(index))} ${String(y(v))})`}
                    className="mark-rolled-back"
                  />
                ))}
              {v !== null && (
                <text
                  x={x(index)}
                  y={y(v) - 11}
                  textAnchor="middle"
                  className="axis"
                >
                  {format(v)}
                </text>
              )}
              <text
                x={x(index)}
                y={HEIGHT - 8}
                textAnchor="middle"
                className="axis"
              >
                Version {point.version}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

/** The held-out accuracy and grade of each version tried, against the objectives. */
export function ScoreChart({ rounds }: Readonly<{ rounds: Round[] }>) {
  const points = pointsOf(rounds);
  if (points.length === 0) {
    return (
      <p className="muted">
        No version has been tried yet, so there is nothing to chart.
      </p>
    );
  }
  const { objectives } = rounds[0].settings;
  return (
    <div className="charts">
      <Chart
        title="Held-out accuracy"
        points={points}
        floor={objectives.accuracy}
        top={1}
        value={(p) => p.accuracy}
        format={(v) => `${String(Math.round(v * 100))}%`}
      />
      <Chart
        title="Held-out grade"
        points={points}
        floor={objectives.grade}
        top={3}
        value={(p) => p.grade}
        format={(v) => v.toFixed(2)}
      />
      <p className="legend muted">
        A circle is a version that was kept. A diamond is one that was rolled
        back.
      </p>
    </div>
  );
}
