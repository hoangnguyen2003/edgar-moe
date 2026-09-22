import { EXPERTS, type ExpertKey } from "../lib/experts";
import { percent } from "../lib/format";
import { COMPACT_LAYOUT, useMediaQuery } from "../lib/useMediaQuery";

// Sized so the labels render near body-text size in a half-width panel.
const WIDTH = 640;
const HEIGHT = 320;
const BUNDLE = 132;
const START_X = 248;
const GATE_X = 448;
const GATE_WIDTH = 26;
const ARROW_TIP = 566;
const GATE_Y = HEIGHT / 2;

/** Stack the streams where they meet the gate, each as thick as its share. */
function layoutStreams(shares: Record<ExpertKey, number>) {
  const streams = [];
  let offset = GATE_Y - BUNDLE / 2;
  for (const [index, { key, name }] of EXPERTS.entries()) {
    const thickness = Math.max(BUNDLE * shares[key], 4);
    const startY = 58 + index * 102;
    const endY = offset + thickness / 2;
    offset += thickness;
    streams.push({
      key,
      label: name,
      share: shares[key],
      thickness,
      startY,
      path: `M ${START_X} ${startY} C ${START_X + 110} ${startY}, ${GATE_X - 100} ${endY}, ${GATE_X} ${endY}`,
    });
  }
  return streams;
}

/**
 * The gate as a flow: each specialist's stream is as thick as its average
 * weight, and the streams merge into the gate that produces the score.
 */
export function GateDiagram({ weights }: { weights: Record<ExpertKey, number> | null }) {
  const streams = layoutStreams(weights ?? { text: 1 / 3, fundamental: 1 / 3, market: 1 / 3 });
  // Phones hide the side labels (the legend below carries them), so crop their space.
  const compact = useMediaQuery(COMPACT_LAYOUT);
  const viewBox = compact ? `${START_X - 6} 0 ${ARROW_TIP + 12 - START_X} ${HEIGHT}` : `0 0 ${WIDTH} ${HEIGHT}`;
  const summary = streams.map((stream) => `${stream.label} ${percent(stream.share, 0)}`).join(", ");
  return (
    <div className="gate">
      <svg className="gate__svg" viewBox={viewBox} role="img" aria-label={`Average weight given to each specialist: ${summary}.`}>
        {streams.map((stream) => (
          <g key={stream.key} className={`expert--${stream.key}`}>
            <path className="gate__stream" d={stream.path} strokeWidth={stream.thickness} />
            <path className="gate__flow" d={stream.path} strokeWidth={Math.min(2, Math.max(1, stream.thickness / 8))} />
            <text className="gate__label" x={START_X - 14} y={stream.startY - 2} textAnchor="end">{stream.label}</text>
            <text className="gate__share" x={START_X - 14} y={stream.startY + 22} textAnchor="end">{percent(stream.share, 0)}</text>
          </g>
        ))}
        <rect className="gate__node" x={GATE_X} y={GATE_Y - BUNDLE / 2 - 14} width={GATE_WIDTH} height={BUNDLE + 28} rx={4} />
        <text className="gate__caption" x={GATE_X + GATE_WIDTH / 2} y={GATE_Y - BUNDLE / 2 - 26} textAnchor="middle">Gate</text>
        <path className="gate__output" d={`M ${GATE_X + GATE_WIDTH} ${GATE_Y} L ${ARROW_TIP - 24} ${GATE_Y}`} />
        <path className="gate__arrow" d={`M ${ARROW_TIP - 24} ${GATE_Y - 13} L ${ARROW_TIP} ${GATE_Y} L ${ARROW_TIP - 24} ${GATE_Y + 13} Z`} />
        {compact ? (
          <text className="gate__outcome" x={ARROW_TIP} y={GATE_Y + 48} textAnchor="end">Score</text>
        ) : (
          <text className="gate__outcome" x={ARROW_TIP + 10} y={GATE_Y + 7}>Score</text>
        )}
      </svg>
      <ul className="gate__legend">
        {streams.map((stream) => (
          <li key={stream.key}><i className={`expert-swatch expert--${stream.key}`} aria-hidden="true" />{stream.label} <strong>{percent(stream.share, 0)}</strong></li>
        ))}
      </ul>
    </div>
  );
}
