import { useQuery } from "@tanstack/react-query";
import { Activity, CheckCircle2, Clock3, LockKeyhole, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";
import { RunnerHealthBanner, RunnerStatus } from "../components/RunnerStatus";
import { InfoTip } from "../components/InfoTip";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, IDLE_DATABASE_HINT, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { forwardForecastsQuery, forwardPerformanceQuery, forwardQualityQuery, forwardRunsQuery, forwardStatusQuery } from "../lib/queries";
import { checkReading, compact, dateTime, decimal, percent, runPosition, signedDecimal, signedPercent } from "../lib/format";
import type { ForwardQualityRecord, ForwardStatusResponse } from "../lib/types";

/**
 * Below this many settled outcomes the live figures are reported as a running
 * log rather than a result. It is a readability threshold, not a statistical
 * test: the frozen study's own locked test used 1,794 events.
 */
const EARLY_RESULT_COUNT = 100;

export function ForwardPage() {
  const status = useQuery(forwardStatusQuery);
  const enabled = Boolean(status.data?.available);
  const performance = useQuery({ ...forwardPerformanceQuery, enabled });
  const runs = useQuery({ ...forwardRunsQuery, enabled });
  const forecasts = useQuery({ ...forwardForecastsQuery, enabled });
  const outcomeLabelId = useId();
  const [outcome, setOutcome] = useState<"all" | "settled" | "pending">("all");
  const quality = useQuery({ ...forwardQualityQuery, enabled });

  if (status.isLoading) {
    return <div className="page"><ForwardHeader /><LoadingState label="Checking the live forecast records" slowHint={IDLE_DATABASE_HINT} skeleton={["figures", "rows"]} /></div>;
  }
  if (status.error) {
    return <div className="page"><ForwardHeader /><ErrorState error={status.error} onRetry={() => void status.refetch()} /></div>;
  }
  if (!status.data?.available) {
    return <UnconfiguredForwardLab configured={Boolean(status.data?.configured)} />;
  }
  if (performance.isLoading || runs.isLoading || forecasts.isLoading || quality.isLoading) {
    return <div className="page"><ForwardHeader /><LoadingState label="Loading live forecasts" slowHint={IDLE_DATABASE_HINT} skeleton={["figures", "rows"]} /></div>;
  }
  const error = performance.error ?? runs.error ?? forecasts.error ?? quality.error;
  if (error) {
    const retry = () => void Promise.all([performance.refetch(), runs.refetch(), forecasts.refetch(), quality.refetch()]);
    return <div className="page"><ForwardHeader /><ErrorState error={error} onRetry={retry} /></div>;
  }

  const metrics = performance.data!;
  const checks = quality.data!;
  const loaded = forecasts.data!.items;
  const settledCount = loaded.filter((row) => row.realized_abnormal_return !== null).length;
  const visible =
    outcome === "all"
      ? loaded
      : loaded.filter((row) =>
          outcome === "settled"
            ? row.realized_abnormal_return !== null
            : row.realized_abnormal_return === null,
        );
  const hasClusteredInterval = metrics.rank_ic_interval_status === "ready"
    && metrics.rank_ic_low !== null && metrics.rank_ic_high !== null;
  const hasLegacyInterval = metrics.rank_ic_interval_status == null
    && metrics.rank_ic_low !== null && metrics.rank_ic_high !== null;
  const rankIcDetail = hasClusteredInterval
    ? `95% time-clustered interval ${decimal(metrics.rank_ic_low, 2)} to ${decimal(metrics.rank_ic_high, 2)}`
    : hasLegacyInterval
      ? `95% independent-event approximation ${decimal(metrics.rank_ic_low, 2)} to ${decimal(metrics.rank_ic_high, 2)}`
      : metrics.rank_ic_interval_status === "insufficient_months"
        ? `${metrics.rank_ic_calendar_months ?? 0} of 12 filing months settled; interval pending`
        : metrics.rank_ic_interval_status === "insufficient_pairs"
          ? `${compact(metrics.matured_count)} of 100 results settled; interval pending`
          : metrics.rank_ic_interval_status === "capacity_review_required"
            ? "Interval paused for capacity review"
            : metrics.rank_ic_interval_status === "undefined_rank_ic" || metrics.rank_ic_interval_status === "degenerate_resamples"
              ? "No stable time-clustered interval yet"
              : `${percent(metrics.coverage)} of forecasts have results`;
  const rankIcInfo = hasClusteredInterval ? "confidenceInterval" : "rankIc";
  const historicalFailedChecks = checks.filter((check) => check.status === "failed").length;
  const historicalWarningChecks = checks.filter((check) => check.status === "warning").length;
  const latestQualityLabel = status.data.latest_quality_failures
    ? `${status.data.latest_quality_failures} failed`
    : status.data.latest_quality_warnings
      ? `${status.data.latest_quality_warnings} warning${status.data.latest_quality_warnings === 1 ? "" : "s"}`
      : "Passing";
  const LatestQualityIcon = status.data.latest_quality_failures
    ? TriangleAlert
    : status.data.latest_quality_warnings
      ? Activity
      : CheckCircle2;
  const historicalQualityDetail = [
    `${checks.length} checks recorded`,
    historicalFailedChecks ? `${historicalFailedChecks} failed before` : "",
    historicalWarningChecks ? `${historicalWarningChecks} warned before` : "",
  ].filter(Boolean).join(" · ");

  return (
    <div className="page">
      <ForwardHeader status={status.data} />
      {/* Quiet when healthy: the header says so. Loud when not: it needs action. */}
      {status.data.health_status !== "ok" && <RunnerHealthBanner status={status.data} />}
      <Protocol />

      <section className="figures" aria-label="Live results">
        <MetricCard label="Forecasts recorded" value={compact(metrics.forecast_count)} detail={`${compact(metrics.pending_count)} still waiting for results`} />
        <MetricCard label="Ranking skill, live" info={rankIcInfo} value={decimal(metrics.rank_ic, 3)} detail={rankIcDetail} />
        <MetricCard label="Prediction error, live" info="rmse" value={decimal(metrics.rmse, 4)} detail={`${compact(metrics.matured_count)} results in so far`} />
        <MetricCard label="Latest data checks" value={latestQualityLabel} detail={historicalQualityDetail} adornment={<LatestQualityIcon size={20} aria-hidden="true" />} />
      </section>

      {metrics.forecast_count > 0 && metrics.matured_count < EARLY_RESULT_COUNT && (
        <p className="figures__note">
          <strong>Too early to read these numbers</strong>
          <span>
            {compact(metrics.matured_count)} of {compact(metrics.forecast_count)} forecasts have a
            result so far. Treat the live figures as a running log, not evidence: the frozen
            study's own test used 1,794 filings.
          </span>
        </p>
      )}

      {metrics.rank_ic_interval_status === "insufficient_months" && (
        <p className="figures__note">
          <strong>More calendar history is needed</strong>
          <span>
            Settled filings cover {metrics.rank_ic_calendar_months ?? 0} distinct calendar months; the time-clustered
            interval waits for 12. The point estimate is a running log, not evidence of a reliable edge.
          </span>
        </p>
      )}

      {metrics.forecast_count === 0 && (
        <div className="notice notice--forward">
          <Clock3 size={18} aria-hidden="true" />
          <div>
            <strong>No qualifying forecasts yet</strong>
            <span>A forecast only counts if it was saved after the filing appeared and before the stock could next be traded. Past data is never relabeled as a live forecast.</span>
          </div>
        </div>
      )}

      <section className="panel forward-forecast-panel">
        <header>
          <div>
            <h2>Recorded forecasts</h2>
            <p>
              Scores can't be changed once saved. Each result appears only after its 20 trading days have passed.
              "Rank in run" compares a forecast only with the other filings scored in the same run.
            </p>
          </div>
          <div className="control">
            <span className="control__label" id={outcomeLabelId}>Show</span>
            <div className="segmented" role="group" aria-labelledby={outcomeLabelId}>
              <button type="button" aria-pressed={outcome === "all"} onClick={() => setOutcome("all")}>
                All {loaded.length}
              </button>
              <button type="button" aria-pressed={outcome === "settled"} onClick={() => setOutcome("settled")}>
                With results {settledCount}
              </button>
              <button type="button" aria-pressed={outcome === "pending"} onClick={() => setOutcome("pending")}>
                Awaiting {loaded.length - settledCount}
              </button>
            </div>
          </div>
        </header>
        {forecasts.data!.total > loaded.length && (
          <p className="detail-hint">
            Showing the {loaded.length} most recent of {compact(forecasts.data!.total)} recorded.
          </p>
        )}
        <ForecastTable rows={visible} outcome={outcome} />
      </section>

      <section className="content-grid content-grid--two forward-top-grid">
        <article className="panel">
          <header><div><h2>Recent runs</h2><p>Each scheduled run, including any that failed, is kept.</p></div></header>
          <RunLedger rows={runs.data!} />
        </article>
        <article className="panel">
          <header><div><h2>Data checks</h2><p>Automatic checks on each run's inputs and outputs.</p></div></header>
          <QualityList rows={checks} />
        </article>
      </section>
    </div>
  );
}

/** The three rules that make a live forecast trustworthy. */
function Protocol() {
  return (
    <ol className="protocol" aria-label="How live tracking works">
      <li><span aria-hidden="true">01</span><div><strong>Freeze the model</strong><small>Its settings are fingerprinted and never changed.</small></div></li>
      <li><span aria-hidden="true">02</span><div><strong>Save the forecast first</strong><small>Each score is stored before the stock can be traded.</small></div></li>
      <li><span aria-hidden="true">03</span><div><strong>Add the result later</strong><small>The outcome is appended once 20 trading days pass.</small></div></li>
    </ol>
  );
}

function ForwardHeader({ status }: { status?: ForwardStatusResponse }) {
  return (
    <PageHeader title="Live tracking" aside={status ? <RunnerStatus status={status} /> : undefined}>
      Since the model was frozen, it has kept scoring new filings as they arrive. This page shows how those
      forecasts are doing, with no chance to adjust them in hindsight.
    </PageHeader>
  );
}

function UnconfiguredForwardLab({ configured }: { configured: boolean }) {
  return (
    <div className="page">
      <ForwardHeader />
      <Protocol />
      <section className="forward-empty panel">
        <h2>{configured ? "Live results are temporarily unavailable" : "Live results aren't connected here"}</h2>
        <p>
          {configured
            ? "The forecast database is set up, but this site couldn't read it just now. The rest of the research is still available."
            : "This copy of the site has no connection to the forecast database, so it can't show live results yet."}
        </p>
        <div className="forward-empty__note"><LockKeyhole size={15} aria-hidden="true" /> No made-up or back-dated rows are ever shown as live forecasts.</div>
      </section>
    </div>
  );
}

function RunLedger({ rows }: { rows: Awaited<ReturnType<typeof api.forwardRuns>> }) {
  if (!rows.length) return <div className="empty-state">No runs recorded yet.</div>;
  return (
    <div className="forward-list">
      {rows.slice(0, 8).map((run) => (
        <div key={run.run_id}>
          <span className={`run-status run-status--${run.status}`}>{run.status}</span>
          <div><strong>{run.run_type}</strong><small>{dateTime(run.started_at)}</small></div>
          <code title="Code version">{run.code_revision.slice(0, 8)}</code>
        </div>
      ))}
    </div>
  );
}

function QualityList({ rows }: { rows: ForwardQualityRecord[] }) {
  if (!rows.length) return <div className="empty-state">Checks appear after the first run.</div>;
  return (
    <div className="quality-list">
      {rows.slice(0, 8).map((check) => (
        <div key={check.check_id}>
          {check.status === "passed" ? <CheckCircle2 size={15} aria-hidden="true" /> : <TriangleAlert size={15} aria-hidden="true" />}
          <div>
            <strong>{check.name.replaceAll("_", " ")}</strong>
            <small>{[checkReading(check.name, check.observed_value, check.threshold), dateTime(check.created_at)].filter(Boolean).join(" · ")}</small>
          </div>
          <span className={`quality-state quality-state--${check.status}`}>{check.status}</span>
        </div>
      ))}
    </div>
  );
}

const EMPTY_FORECASTS: Record<"all" | "settled" | "pending", string> = {
  all: "No forecasts recorded yet.",
  settled: "No forecast has a result yet. Each one appears 20 trading days after its entry.",
  pending: "Every recorded forecast already has its result.",
};

function ForecastTable({
  rows,
  outcome = "all",
}: {
  rows: Awaited<ReturnType<typeof api.forwardForecasts>>["items"];
  outcome?: "all" | "settled" | "pending";
}) {
  if (!rows.length) return <div className="empty-state">{EMPTY_FORECASTS[outcome]}</div>;
  return (
    <div className="forward-table-wrap">
      <table className="forward-table">
        <thead>
          <tr>
            <th scope="col">Company</th>
            <th scope="col">Saved</th>
            <th scope="col">Tradable from</th>
            <th scope="col" className="num">Score</th>
            <th scope="col" className="num"><span className="th-with-tip">Rank in run <InfoTip term="runRank" /></span></th>
            <th scope="col" className="num">20-day result</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.forecast_id}>
              <td><strong>{row.ticker}</strong><span>{row.form} · {row.company_name}</span></td>
              <td data-label="Saved">{dateTime(row.forecast_as_of)}</td>
              <td data-label="Tradable from">{dateTime(row.entry_at)}</td>
              <td className="num" data-label="Score">{signedDecimal(row.score, 4)}</td>
              <td className="num" data-label="Rank in run">{runPosition(row.rank, row.cohort_size)}</td>
              <td className="num" data-label="20-day result">{row.realized_abnormal_return == null ? <span className="pending-label">Not yet known</span> : signedPercent(row.realized_abnormal_return)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
