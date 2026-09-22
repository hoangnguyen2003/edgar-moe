import { EXPERTS } from "../lib/experts";
import { percent } from "../lib/format";
import type { EventRecord } from "../lib/types";

type Weights = EventRecord["expert_weights"];

function describe(weights: Weights): string {
  return EXPERTS.map(({ key, label }) => `${label} ${percent(weights[key], 0)}`).join(", ");
}

export function ExpertLegend() {
  return (
    <ul className="expert-legend" aria-label="Expert colors">
      {EXPERTS.map(({ key, label }) => (
        <li key={key}><i className={`expert-swatch expert--${key}`} aria-hidden="true" />{label}</li>
      ))}
    </ul>
  );
}

/** A stacked bar of the gate's split, with the values printed beneath it. */
export function ExpertMix({ weights }: { weights: Weights }) {
  return (
    <div className="expert-mix">
      <div className="expert-mix__bar" role="img" aria-label={`Expert allocation: ${describe(weights)}`}>
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
