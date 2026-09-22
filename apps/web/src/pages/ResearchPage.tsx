import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp } from "lucide-react";
import { useState } from "react";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { decimal, signedDecimal, splitModelName } from "../lib/format";
import type { ExperimentRecord } from "../lib/types";

type SortKey = "rank_ic" | "rmse";
const PREVIEW_ROWS = 10;

function compareNumbers(a: number, b: number): number {
  return a === b ? 0 : a < b ? -1 : 1;
}

const sorters: Record<SortKey, (a: ExperimentRecord, b: ExperimentRecord) => number> = {
  rank_ic: (a, b) => compareNumbers(b.validation_rank_ic ?? -Infinity, a.validation_rank_ic ?? -Infinity),
  rmse: (a, b) => compareNumbers(a.validation_rmse, b.validation_rmse),
};

export function ResearchPage() {
  const experiments = useQuery({ queryKey: ["experiments"], queryFn: api.experiments });
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  if (experiments.isLoading || summary.isLoading) return <div className="page"><LoadingState label="Loading experiment registry" /></div>;
  if (experiments.error) return <div className="page"><ErrorState error={experiments.error} onRetry={() => void experiments.refetch()} /></div>;
  const rows = experiments.data!;
  const selected = rows.find((row) => row.selected);
  const selectedName = selected ? splitModelName(selected.name) : null;
  return (
    <div className="page">
      <PageHeader section="02" kicker="Experiment registry" title="Models earn their place.">
        All candidates use identical chronological splits. Selection uses pre-test folds only; the frozen locked result is reported separately.
      </PageHeader>
      <section className="selection" aria-label="Selected model">
        <article className="panel selection__model">
          <header>
            <div>
              <span className="panel__kicker">Selected model</span>
              <h2>{selectedName?.family ?? "—"}</h2>
            </div>
            <span className="stamp">Frozen</span>
          </header>
          {selectedName && selectedName.params.length > 0 && <ParamList params={selectedName.params} />}
          <p className="panel__note">{selected?.best_epoch != null ? `Best epoch ${selected.best_epoch}. ` : ""}Chosen on pre-test folds; the locked test is reported separately.</p>
        </article>
        <div className="figures figures--stack">
          <MetricCard label="Validation RMSE" value={decimal(selected?.validation_rmse, 5)} detail="Lower is better" />
          <MetricCard label="Validation rank IC" value={decimal(selected?.validation_rank_ic, 3)} detail="Cross-sectional ordering; higher is better" />
        </div>
      </section>
      <Leaderboard rows={rows} />
      <article className="panel">
        <header><div><span className="panel__kicker">Ablation logic</span><h2>What the comparison proves</h2></div></header>
        <ol className="research-list research-list--columns">
          <li><span>01</span><div><strong>Linear baseline</strong><p>Tests whether the result is merely a stable additive factor model.</p></div></li>
          <li><span>02</span><div><strong>Non-linear tabular baseline</strong><p>Measures what flexible trees capture without a modality gate.</p></div></li>
          <li><span>03</span><div><strong>Regime-gated MoE</strong><p>Must improve rank quality and survive cost-aware portfolio tests—not only prediction loss.</p></div></li>
        </ol>
      </article>
      {summary.data?.metadata.data_mode === "synthetic_fixture" && <DemoNotice />}
    </div>
  );
}

function Leaderboard({ rows }: { rows: ExperimentRecord[] }) {
  const [sort, setSort] = useState<SortKey>("rank_ic");
  const [expanded, setExpanded] = useState(false);
  const entries = [...rows]
    .sort((a, b) => sorters[sort](a, b) || a.name.localeCompare(b.name))
    .map((row, index) => ({ row, rank: index + 1 }));
  const visible = expanded ? entries : entries.slice(0, PREVIEW_ROWS);
  // Keep the frozen selection on screen even when it ranks below the preview.
  const pinned = expanded ? undefined : entries.find((entry) => entry.row.selected && entry.rank > PREVIEW_ROWS);
  const maxIc = Math.max(...rows.map((row) => Math.abs(row.validation_rank_ic ?? 0)), Number.EPSILON);
  const bestRmse = Math.min(...rows.map((row) => row.validation_rmse));
  const sortLabel = sort === "rank_ic" ? "rank IC, highest first" : "RMSE, lowest first";
  return (
    <article className="panel leaderboard-panel">
      <header>
        <div>
          <span className="panel__kicker">Baseline comparison</span>
          <h2>Every candidate on the same folds</h2>
          <p>{rows.length} candidates · sorted by validation {sortLabel}</p>
        </div>
      </header>
      <div className="table-scroll">
        <table className="leaderboard" role="table">
          <caption className="sr-only">Validation metrics for {rows.length} candidate models, sorted by {sortLabel}.</caption>
          <thead role="rowgroup">
            <tr role="row">
              <th scope="col" role="columnheader" className="leaderboard__rank">#</th>
              <th scope="col" role="columnheader">Model</th>
              <th scope="col" role="columnheader" aria-sort={sort === "rank_ic" ? "descending" : "none"}>
                <SortButton active={sort === "rank_ic"} onClick={() => setSort("rank_ic")} icon={ArrowDown}>Rank IC</SortButton>
              </th>
              <th scope="col" role="columnheader" aria-sort={sort === "rmse" ? "ascending" : "none"}>
                <SortButton active={sort === "rmse"} onClick={() => setSort("rmse")} icon={ArrowUp}>RMSE</SortButton>
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
          {expanded ? `Show top ${PREVIEW_ROWS}` : `Show all ${rows.length} candidates`}
        </button>
      )}
    </article>
  );
}

function SortButton({ active, onClick, icon: Icon, children }: {
  active: boolean;
  onClick: () => void;
  icon: typeof ArrowDown;
  children: string;
}) {
  return (
    <button type="button" className={active ? "sort-button active" : "sort-button"} onClick={onClick}>
      {children}{active && <Icon size={13} aria-hidden="true" />}
    </button>
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
          {row.selected && <span className="badge badge--selected">Selected</span>}
        </div>
        {params.length > 0 && <ParamList params={params} />}
      </td>
      <td role="cell" data-label="Rank IC">
        <div className="ic-cell">
          <span className="ic-bar" aria-hidden="true">
            <i style={ic != null && ic < 0 ? { right: "50%", width: `${width}%` } : { left: "50%", width: `${width}%` }} />
          </span>
          <span className="num">{signedDecimal(ic, 3)}</span>
        </div>
      </td>
      <td role="cell" data-label="RMSE">
        <span className="num">{decimal(row.validation_rmse, 5)}</span>
        <small className="leaderboard__delta">{delta === 0 ? "best" : `+${decimal(delta, 5)}`}</small>
      </td>
    </tr>
  );
}

function ParamList({ params }: { params: string[] }) {
  return <ul className="param-list" aria-label="Hyperparameters">{params.map((param) => <li key={param}>{param}</li>)}</ul>;
}

function DemoNotice() {
  return <div className="notice"><strong>Verification mode</strong><span>These values come from a deterministic synthetic dataset. They validate model selection, API contracts, and visualization—not market alpha.</span></div>;
}
