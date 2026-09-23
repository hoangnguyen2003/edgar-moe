import { EXPERTS } from "../lib/experts";
import { percent } from "../lib/format";
import type { EventRecord } from "../lib/types";

type Weights = EventRecord["expert_weights"];

function describe(weights: Weights): string {
  return EXPERTS.map(({ key, label }) => `${label} ${percent(weights[key], 0)}`).join(", ");
}

/**
 * A stacked bar of the gate's split. In a table the values would repeat on
 * every row, so `compact` draws the bar alone and the page shows a legend once;
 * the bar keeps its full description for assistive technology either way.
 */
export function ExpertMix({ weights, compact = false }: { weights: Weights; compact?: boolean }) {
  if (compact) {
    return (
      <div className="expert-mix__bar expert-mix__bar--compact" role="img" aria-label={`Weight given to each specialist: ${describe(weights)}`} title={describe(weights)}>
        {EXPERTS.map(({ key }) => (
          <span key={key} className={`expert--${key}`} style={{ flexGrow: weights[key] }} />
        ))}
      </div>
    );
  }
  return (
    <div className="expert-mix">
      <div className="expert-mix__bar" role="img" aria-label={`Weight given to each specialist: ${describe(weights)}`}>
        {EXPERTS.map(({ key }) => (
          <span key={key} className={`expert--${key}`} style={{ flexGrow: weights[key] }} />
        ))}
      </div>
      <p className="expert-mix__values" aria-hidden="true">
        {EXPERTS.map(({ key, label }) => (
          <span key={key}><i className={`expert-swatch expert--${key}`} />{label} {percent(weights[key], 0)}</span>
        ))}
      </p>
    </div>
  );
}

/** Labeled bars, one per expert, for the detail view. */
export function ExpertBars({ weights }: { weights: Weights }) {
  return (
    <div className="expert-bars">
      {EXPERTS.map(({ key, label }) => (
        <div key={key}>
          <span><i className={`expert-swatch expert--${key}`} aria-hidden="true" />{label}</span>
          <i className="expert-bars__track" aria-hidden="true">
            <b className={`expert--${key}`} style={{ width: `${weights[key] * 100}%` }} />
          </i>
          <strong>{percent(weights[key], 0)}</strong>
        </div>
      ))}
    </div>
  );
}

/** The key for compact bars, shown once above the rows that use them. */
export function ExpertLegend() {
  return (
    <p className="expert-legend">
      {EXPERTS.map(({ key, label }) => (
        <span key={key}><i className={`expert-swatch expert--${key}`} aria-hidden="true" />{label}</span>
      ))}
    </p>
  );
}
