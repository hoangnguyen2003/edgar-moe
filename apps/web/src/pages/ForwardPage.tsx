import { useQuery } from "@tanstack/react-query";
import { Activity, CheckCircle2, Clock3, LockKeyhole, TriangleAlert } from "lucide-react";
import { InfoTip } from "../components/InfoTip";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, IDLE_DATABASE_HINT, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { compact, dateTime, decimal, percent, runPosition, signedDecimal, signedPercent } from "../lib/format";
import type { ForwardQualityRecord, ForwardStatusResponse } from "../lib/types";

/**
 * Below this many settled outcomes the live figures are reported as a running
 * log rather than a result. It is a readability threshold, not a statistical
 * test: the frozen study's own locked test used 1,794 events.
 */
const EARLY_RESULT_COUNT = 100;

export function ForwardPage() {
  const status = useQuery({ queryKey: ["forward-status"], queryFn: api.forwardStatus });
  const enabled = Boolean(status.data?.available);
  const performance = useQuery({
    queryKey: ["forward-performance"],
    queryFn: api.forwardPerformance,
    enabled,
  });
  const runs = useQuery({ queryKey: ["forward-runs"], queryFn: api.forwardRuns, enabled });
  const forecasts = useQuery({
    queryKey: ["forward-forecasts"],
    queryFn: api.forwardForecasts,
    enabled,
  });
  const quality = useQuery({
    queryKey: ["forward-quality"],
    queryFn: api.forwardDataQuality,
    enabled,
  });

  if (status.isLoading) {
    return <div className="page"><ForwardHeader /><LoadingState label="Checking the live forecast records" slowHint={IDLE_DATABASE_HINT} /></div>;
  }
  if (status.error) {
    return <div className="page"><ForwardHeader /><ErrorState error={status.error} onRetry={() => void status.refetch()} /></div>;
  }
  if (!status.data?.available) {
    return <UnconfiguredForwardLab configured={Boolean(status.data?.configured)} />;
  }
  if (performance.isLoading || runs.isLoading || forecasts.isLoading || quality.isLoading) {
    return <div className="page"><ForwardHeader /><LoadingState label="Loading live forecasts" slowHint={IDLE_DATABASE_HINT} /></div>;
  }
  const error = performance.error ?? runs.error ?? forecasts.error ?? quality.error;
  if (error) {
    const retry = () => void Promise.all([performance.refetch(), runs.refetch(), forecasts.refetch(), quality.refetch()]);
    return <div className="page"><ForwardHeader /><ErrorState error={error} onRetry={retry} /></div>;
  }

  const metrics = performance.data!;
  const checks = quality.data!;
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
      <ForwardHeader />
      <Protocol />
      <ForwardHealthBanner status={status.data} />

      <section className="figures" aria-label="Live results">
        <MetricCard label="Forecasts recorded" value={compact(metrics.forecast_count)} detail={`${compact(metrics.pending_count)} still waiting for results`} />
        <MetricCard label="Ranking skill, live" info="rankIc" value={decimal(metrics.rank_ic, 3)} detail={`${percent(metrics.coverage)} of forecasts have results`} />
        <MetricCard label="Prediction error, live" info="rmse" value={decimal(metrics.rmse, 4)} detail={`${compact(metrics.matured_count)} results in so far`} />
        <MetricCard label="Latest data checks" value={latestQualityLabel} detail={historicalQualityDetail} adornment={<LatestQualityIcon size={20} aria-hidden="true" />} />
      </section>

      {metrics.forecast_count > 0 && metrics.matured_count < EARLY_RESULT_COUNT && (
        <div className="notice">
          <Clock3 size={18} aria-hidden="true" />
          <div>
            <strong>Too early to read these numbers</strong>
            <span>
              {compact(metrics.matured_count)} of {compact(metrics.forecast_count)} forecasts have a
              result so far. Treat the live figures as a running log, not evidence: the frozen
              study's own test used 1,794 filings.
            </span>
          </div>
        </div>
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
          <span className="count-chip">{forecasts.data!.total} forecasts</span>
        </header>
        <ForecastTable rows={forecasts.data!.items} />
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
      <li><span>1</span><div><strong>Freeze the model</strong><small>Its settings are fingerprinted and never changed.</small></div></li>
      <li><span>2</span><div><strong>Save the forecast first</strong><small>Each score is stored before the stock can be traded.</small></div></li>
      <li><span>3</span><div><strong>Add the result later</strong><small>The outcome is appended once 20 trading days pass.</small></div></li>
    </ol>
  );
}

function ForwardHealthBanner({ status }: { status: ForwardStatusResponse }) {
  const healthy = status.health_status === "ok";
  const warning = status.health_status === "warning";
  const Icon = healthy ? CheckCircle2 : warning ? Activity : TriangleAlert;
  const tone = healthy ? "notice--forward" : "notice--warning";
  const age = status.age_seconds == null
    ? "no successful run yet"
    : `${formatAge(status.age_seconds)} ago`;
  const running = status.running_run_count === 1
    ? "1 run in progress"
    : `${status.running_run_count} runs in progress`;

  return (
    <div className={`notice ${tone} forward-health-banner`}>
      <Icon size={18} aria-hidden="true" />
      <div>
        <strong>{status.health_message ?? status.message}</strong>
        <span>
          Last successful run: {dateTime(status.latest_successful_run_at)} ({age}) · {running}
        </span>
      </div>
    </div>
  );
}

function formatAge(seconds: number): string {
  if (seconds < 60) return "less than a minute";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.floor(hours / 24)} days`;
}

function ForwardHeader() {
  return (
    <PageHeader title="Live tracking">
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
          <div><strong>{check.name.replaceAll("_", " ")}</strong><small>{dateTime(check.created_at)}</small></div>
          <span className={`quality-state quality-state--${check.status}`}>{check.status}</span>
        </div>
      ))}
    </div>
  );
}

function ForecastTable({ rows }: { rows: Awaited<ReturnType<typeof api.forwardForecasts>>["items"] }) {
  if (!rows.length) return <div className="empty-state">No forecasts recorded yet.</div>;
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
