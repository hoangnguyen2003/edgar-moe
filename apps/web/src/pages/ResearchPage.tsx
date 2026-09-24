import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useId, useState } from "react";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { experimentsQuery, researchEvidenceQuery, summaryQuery } from "../lib/queries";
import { decimal, signedDecimal, splitModelName } from "../lib/format";
import type { ExperimentRecord, ResearchEvidenceResponse } from "../lib/types";

type SortKey = "rank_ic" | "rmse";
const PREVIEW_ROWS = 10;

function compareNumbers(a: number, b: number): number {
  return a === b ? 0 : a < b ? -1 : 1;
}

const sorters: Record<SortKey, (a: ExperimentRecord, b: ExperimentRecord) => number> = {
  rank_ic: (a, b) => compareNumbers(b.validation_rank_ic ?? -Infinity, a.validation_rank_ic ?? -Infinity),
  rmse: (a, b) => compareNumbers(a.validation_rmse, b.validation_rmse),
};

function ranked(rows: ExperimentRecord[], sort: SortKey) {
  return [...rows]
    .sort((a, b) => sorters[sort](a, b) || a.name.localeCompare(b.name))
    .map((row, index) => ({ row, rank: index + 1 }));
}

export function ResearchPage() {
  const experiments = useQuery(experimentsQuery);
  const summary = useQuery(summaryQuery);
  const evidence = useQuery(researchEvidenceQuery);
  const header = (answer?: ReactNode) => (
    <PageHeader
      title="Model comparison"
      answer={answer}
      placeholder="Of 33 candidates, Fundamental-Anchored MoE was chosen: the most consistent across both validation years, not the highest average (#2 of 33)."
    >
      Every candidate learned from the same filings and was compared on 2023–2024 validation data; the chosen one was
      frozen before the final test.
    </PageHeader>
  );
  if (experiments.isLoading || summary.isLoading) return <div className="page">{header(null)}<LoadingState label="Loading the model comparison" skeleton={["figures:3", "rows"]} /></div>;
  if (experiments.error) return <div className="page">{header()}<ErrorState error={experiments.error} onRetry={() => void experiments.refetch()} /></div>;
  const rows = experiments.data!;
  const selected = rows.find((row) => row.selected);
  const selectedName = selected ? splitModelName(selected.name) : null;
  const byRankIc = ranked(rows, "rank_ic");
  const chosen = byRankIc.find((entry) => entry.row.selected);
  // The best of each plainer family, so the comparison below shows its outcome.
  const bestOf = (family: string) => byRankIc.find((entry) => entry.row.family === family);
  const finalIc = summary.data?.predictive_metrics?.locked_test?.rank_ic;
  return (
    <div className="page">
      {header(selectedName ? (
        <>
          Of {rows.length} candidates, <mark>{selectedName.family}</mark> was chosen: the most consistent across both
          validation years, not the highest average (#{chosen?.rank} of {rows.length}).
        </>
      ) : undefined)}
      <section className="figures figures--three" aria-label="Chosen model results">
        <MetricCard label="Ranking skill, validation" info="rankIc" value={decimal(selected?.validation_rank_ic, 3)} detail="2023–2024; higher is better" />
        <MetricCard label="Prediction error, validation" info="rmse" value={decimal(selected?.validation_rmse, 5)} detail="2023–2024; lower is better" />
        <MetricCard label="Ranking skill, final test" info="lockedTest" value={decimal(finalIc, 3)} detail="2025–2026, scored once after freezing" />
      </section>
      <EvidencePanel
        evidence={evidence.data}
        loading={evidence.isPending}
        error={evidence.error}
        retry={() => void evidence.refetch()}
      />
      <Leaderboard rows={rows} />
      <article className="panel">
        <header>
          <div>
            <h2>Why compare against simpler models?</h2>
            <p>The mixture of experts only earns its complexity if it beats plainer approaches on the same data.</p>
          </div>
        </header>
        <ol className="reason-list">
          <li>
            <strong>Linear model</strong>
            <p>Checks whether a simple weighted sum of the same inputs does just as well.</p>
            <Outcome entry={bestOf("linear")} total={rows.length} />
          </li>
          <li>
            <strong>Tree model</strong>
            <p>Checks what a flexible model finds without the specialists and the gate.</p>
            <Outcome entry={bestOf("tree")} total={rows.length} />
          </li>
          <li>
            <strong>Mixture of experts</strong>
            <p>Has to beat both on ranking skill, and then hold up in the cost-aware backtest.</p>
            <Outcome entry={chosen} total={rows.length} />
          </li>
        </ol>
      </article>
      {summary.data?.metadata.data_mode === "synthetic_fixture" && <DemoNotice />}
    </div>
  );
}

function EvidencePanel({ evidence, loading, error, retry }: {
  evidence?: ResearchEvidenceResponse;
  loading: boolean;
  error: Error | null;
  retry: () => void;
}) {
  return (
    <section className="panel research-evidence" aria-labelledby="research-evidence-heading">
      <header>
        <div>
          <h2 id="research-evidence-heading">Evidence and uncertainty</h2>
          <p>Frozen v1 results are separate from duration-aware v2 development research.</p>
        </div>
      </header>
      {loading && <p role="status">Loading reviewed research evidence…</p>}
      {error && <div role="alert"><p>Reviewed evidence is unavailable. The model leaderboard above remains readable, but its provenance cannot be verified here.</p><button type="button" className="text-button" onClick={retry}>Retry evidence</button></div>}
      {evidence && <>
        <div className="evidence-grid">
          <div>
            <h3>Frozen v1 · locked test</h3>
            <p><strong>{evidence.frozen_v1.locked_test_events.toLocaleString()}</strong> locked-test events; rank IC <strong>{signedDecimal(evidence.frozen_v1.locked_rank_ic, 3)}</strong>.</p>
            <p>95% calendar-month block interval: <strong>{signedDecimal(evidence.frozen_v1.locked_rank_ic_interval_95.low, 3)} to {signedDecimal(evidence.frozen_v1.locked_rank_ic_interval_95.high, 3)}</strong> ({evidence.frozen_v1.locked_rank_ic_interval_95.calendar_months} months; {evidence.frozen_v1.locked_rank_ic_interval_95.resamples.toLocaleString()} resamples).</p>
            <p>10 bps cost-aware Sharpe: <strong>{signedDecimal(evidence.frozen_v1.portfolio_10bps_sharpe, 3)}</strong>. {evidence.frozen_v1.interpretation}</p>
          </div>
          <div>
            <h3>Duration-aware v2 · development</h3>
            {evidence.duration_aware_v2.status === "pending_review" ? (
              <p role="status">Pending review. No v2 comparison or cost-aware result is published yet.</p>
            ) : <ReviewedV2 evidence={evidence.duration_aware_v2} />}
          </div>
        </div>
        <p className="evidence-provenance">Catalog SHA-256: <code>{evidence.catalog_sha256}</code> · Frozen selection: <code>{evidence.frozen_v1.selection_hash}</code></p>
      </>}
    </section>
  );
}

function ReviewedV2({ evidence }: {
  evidence: Extract<ResearchEvidenceResponse["duration_aware_v2"], { status: "reviewed_pretest" }>;
}) {
  return <>
    <p>{evidence.oof_events.toLocaleString()} out-of-fold development events. Selected {evidence.champion_name}: weighted rank IC {signedDecimal(evidence.champion_weighted_rank_ic, 3)}.</p>
    <p>Paired calendar-month block intervals within each fold: {evidence.block_months} months, {evidence.bootstrap_resamples.toLocaleString()} resamples. These intervals are conditional on model selection.</p>
    <p>{evidence.interpretation}</p>
    <div className="table-scroll">
      <table className="evidence-table">
        <caption>Development-fold rank IC difference against simple baselines</caption>
        <thead><tr><th scope="col">Baseline</th><th scope="col">Difference</th><th scope="col">95% interval</th></tr></thead>
        <tbody>{evidence.comparisons.map((row) => <tr key={row.baseline}>
          <th scope="row">{row.baseline}</th>
          <td>{signedDecimal(row.rank_ic_delta, 3)}</td>
          <td>{row.interval_status === "ready" ? `${signedDecimal(row.interval_low, 3)} to ${signedDecimal(row.interval_high, 3)}` : "Unavailable: insufficient reliable resamples"}</td>
        </tr>)}</tbody>
      </table>
    </div>
    {evidence.portfolio_status === "development_only" ? <div className="table-scroll">
      <table className="evidence-table">
        <caption>Development-fold cost scenarios; {evidence.cost_definition}; not a live or independent trading estimate</caption>
        <thead><tr><th scope="col">Model</th><th scope="col">Cost</th><th scope="col">Sharpe</th><th scope="col">Annualized return</th></tr></thead>
        <tbody>{evidence.cost_scenarios.map((row) => <tr key={`${row.model}-${row.cost_bps}`}>
          <th scope="row">{row.model}</th><td>{row.cost_bps} bps</td><td>{decimal(row.sharpe, 2)}</td><td>{row.annualized_return == null ? "—" : `${decimal(row.annualized_return * 100, 1)}%`}</td>
        </tr>)}</tbody>
      </table>
    </div> : <p>Cost-aware portfolio comparison unavailable: {evidence.portfolio_status.replaceAll("_", " ")}.</p>}
    <p className="evidence-provenance">Review SHA-256: <code>{evidence.review_sha256}</code> · Approval reference: <code>{evidence.approval_reference}</code></p>
  </>;
}

/** Where one model finished on validation ranking skill: "Elastic Net · #32 of 33 · −0.081". */
function Outcome({ entry, total }: { entry?: { row: ExperimentRecord; rank: number }; total: number }) {
  if (!entry) return null;
  return (
    <p className="reason-list__outcome">
      {splitModelName(entry.row.name).family} · #{entry.rank} of {total} · {signedDecimal(entry.row.validation_rank_ic, 3)}
    </p>
  );
}

/** The codes in each candidate's settings, in words. */
const SETTINGS = [
  ["h", "hidden units per layer"],
  ["dropout", "share of units switched off in training, against overfitting"],
  ["gate", "how far market conditions can shift the specialists' weights; 0 keeps them fixed"],
  ["moe", "the mixture's share of the score; the Fundamental-Only Expert supplies the rest"],
] as const;

function SettingsKey() {
  const labelId = useId();
  return (
    <div className="settings-key">
      <span className="settings-key__label" id={labelId}>Settings</span>
      <dl aria-labelledby={labelId}>
        {SETTINGS.map(([code, meaning]) => (
          <div key={code}><dt>{code}</dt><dd>{meaning}</dd></div>
        ))}
      </dl>
    </div>
  );
}

function Leaderboard({ rows }: { rows: ExperimentRecord[] }) {
  const [sort, setSort] = useState<SortKey>("rank_ic");
  const [expanded, setExpanded] = useState(false);
  const sortLabelId = useId();
  const entries = ranked(rows, sort);
  const visible = expanded ? entries : entries.slice(0, PREVIEW_ROWS);
  // Keep the frozen selection on screen even when it ranks below the preview.
  const pinned = expanded ? undefined : entries.find((entry) => entry.row.selected && entry.rank > PREVIEW_ROWS);
  const maxIc = Math.max(...rows.map((row) => Math.abs(row.validation_rank_ic ?? 0)), Number.EPSILON);
  const bestRmse = Math.min(...rows.map((row) => row.validation_rmse));
  const sortLabel = sort === "rank_ic" ? "ranking skill, highest first" : "prediction error, lowest first";
  return (
    <article className="panel leaderboard-panel">
      <header>
        <div>
          <h2>All {rows.length} candidates</h2>
          <p>Validation results for every model tried, sorted by {sortLabel}. The chosen model is highlighted.</p>
        </div>
        <div className="control">
          <span className="control__label" id={sortLabelId}>Sort by</span>
          <div className="segmented" role="group" aria-labelledby={sortLabelId}>
            <button type="button" aria-pressed={sort === "rank_ic"} onClick={() => setSort("rank_ic")}>Ranking skill</button>
            <button type="button" aria-pressed={sort === "rmse"} onClick={() => setSort("rmse")}>Prediction error</button>
          </div>
        </div>
      </header>
      <SettingsKey />
      <div className="table-scroll">
        <table className="leaderboard" role="table">
          <caption className="sr-only">Validation results for {rows.length} candidate models, sorted by {sortLabel}.</caption>
          <thead role="rowgroup">
            <tr role="row">
              <th scope="col" role="columnheader" className="leaderboard__rank">#</th>
              <th scope="col" role="columnheader">Model and settings</th>
              <th scope="col" role="columnheader" aria-sort={sort === "rank_ic" ? "descending" : "none"}>
                Ranking skill (rank IC) <small>higher is better</small>
              </th>
              <th scope="col" role="columnheader" aria-sort={sort === "rmse" ? "ascending" : "none"}>
                Error (RMSE) <small>lower is better</small>
              </th>
            </tr>
          </thead>
          <tbody role="rowgroup">
            {visible.map((entry) => <LeaderboardRow key={entry.row.name} {...entry} maxIc={maxIc} bestRmse={bestRmse} />)}
            {pinned && (
              <>
                <tr className="leaderboard__gap" aria-hidden="true"><td colSpan={4}>⋯</td></tr>
                <LeaderboardRow {...pinned} maxIc={maxIc} bestRmse={bestRmse} />
              </>
            )}
          </tbody>
        </table>
      </div>
      {rows.length > PREVIEW_ROWS && (
        <button type="button" className="text-button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
          {expanded ? `Show top ${PREVIEW_ROWS} only` : `Show all ${rows.length} candidates`}
        </button>
      )}
    </article>
  );
}

function LeaderboardRow({ row, rank, maxIc, bestRmse }: {
  row: ExperimentRecord;
  rank: number;
  maxIc: number;
  bestRmse: number;
}) {
  const { family, params } = splitModelName(row.name);
  const ic = row.validation_rank_ic;
  const width = ic == null ? 0 : (Math.abs(ic) / maxIc) * 50;
  const delta = row.validation_rmse - bestRmse;
  return (
    <tr role="row" className={row.selected ? "is-selected" : undefined}>
      <td role="cell" className="leaderboard__rank">{rank}</td>
      <td role="cell">
        <div className="leaderboard__model">
          <strong>{family}</strong>
          {row.selected && <span className="badge badge--chosen">Chosen</span>}
        </div>
        {params.length > 0 && <ParamList params={params} />}
      </td>
      <td role="cell" data-label="Ranking skill">
        <div className="ic-cell">
          <span className="ic-bar" aria-hidden="true">
            <i style={ic != null && ic < 0 ? { right: "50%", width: `${width}%` } : { left: "50%", width: `${width}%` }} />
          </span>
          <span className="num">{signedDecimal(ic, 3)}</span>
        </div>
      </td>
      <td role="cell" data-label="Error (RMSE)">
        <span className="num">{decimal(row.validation_rmse, 5)}</span>
        <small className="leaderboard__delta">{delta === 0 ? "lowest" : `+${decimal(delta, 5)} vs lowest`}</small>
      </td>
    </tr>
  );
}

function ParamList({ params }: { params: string[] }) {
  return <ul className="param-list" aria-label="Settings">{params.map((param) => <li key={param}>{param}</li>)}</ul>;
}

function DemoNotice() {
  return (
    <div className="notice">
      <div>
        <strong>Synthetic test data</strong>
        <span>These values come from a generated dataset that checks the software works. They say nothing about real markets.</span>
      </div>
    </div>
  );
}
