import { useQuery } from "@tanstack/react-query";
import { Activity, CheckCircle2, CircleDashed, Clock3, LockKeyhole, TriangleAlert } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { RunnerHealthBanner, RunnerStatus } from "../components/RunnerStatus";
import { Tag } from "../components/Tag";
import { InfoTip } from "../components/InfoTip";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, IDLE_DATABASE_HINT, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { forwardForecastsQuery, forwardPerformanceQuery, forwardQualityQuery, forwardRunsQuery, forwardStatusQuery } from "../lib/queries";
import { checkName, checkReading, compact, dateTime, decimal, marketDay, percent, runPosition, signedDecimal, signedPercent } from "../lib/format";
import type { ForwardQualityRecord, ForwardStatusResponse } from "../lib/types";

/**
 * Below this many settled outcomes the live figures are reported as a running
 * log rather than a result. It is a readability threshold, not a statistical
 * test: the frozen study's own locked test used 1,794 events.
 */
const EARLY_RESULT_COUNT = 100;

/**
 * Warnings that describe a normal condition rather than a problem: on most days
 * no new filing is accepted, so a run has nothing to score. Mirrors
 * `_EXPECTED_QUALITY_WARNINGS` in src/edgar_moe/forward/alerts.py.
 */
const EXPECTED_WARNINGS = new Set(["prospective_candidate_count"]);

type CheckTone = "failed" | "warning" | "passed" | "expected";
const TONE_ORDER: Record<CheckTone, number> = { failed: 0, warning: 1, passed: 2, expected: 3 };

function checkTone(check: ForwardQualityRecord): CheckTone {
  return check.status === "warning" && EXPECTED_WARNINGS.has(check.name) ? "expected" : check.status;
}

/**
 * The newest result of each check, problems first. A scheduled job records a
 * forecast run and a settlement run, each with its own checks, so "the latest
 * run" alone would miss half of them.
 */
function latestChecks(checks: ForwardQualityRecord[]): ForwardQualityRecord[] {
  const newest = new Map<string, ForwardQualityRecord>();
  for (const check of [...checks].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))) {
    if (!newest.has(check.name)) newest.set(check.name, check);
  }
  return [...newest.values()].sort((a, b) => TONE_ORDER[checkTone(a)] - TONE_ORDER[checkTone(b)]);
}

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
    return <div className="page"><ForwardHeader answer={null} early /><LoadingState label="Checking the live forecast records" slowHint={IDLE_DATABASE_HINT} skeleton={["figures", "rows"]} /></div>;
  }
  if (status.error) {
    return <div className="page"><ForwardHeader /><ErrorState error={status.error} onRetry={() => void status.refetch()} /></div>;
  }
  if (!status.data?.available) {
    return <UnconfiguredForwardLab configured={Boolean(status.data?.configured)} />;
  }
  if (performance.isLoading || runs.isLoading || forecasts.isLoading || quality.isLoading) {
    return <div className="page"><ForwardHeader answer={null} early /><LoadingState label="Loading live forecasts" slowHint={IDLE_DATABASE_HINT} skeleton={["figures", "rows"]} /></div>;
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
  const intervalStatus = metrics.rank_ic_interval_status;
  const knownIntervalStatus = intervalStatus === "ready"
    || intervalStatus === "insufficient_pairs"
    || intervalStatus === "insufficient_months"
    || intervalStatus === "undefined_rank_ic"
    || intervalStatus === "degenerate_resamples"
    || intervalStatus === "capacity_review_required";
  const calendarMonths = metrics.rank_ic_calendar_months;
  const hasReviewedProtocol = metrics.rank_ic_interval_method === "calendar_month_moving_block"
    && knownIntervalStatus
    && Number.isInteger(calendarMonths)
    && (calendarMonths ?? -1) >= 0
    && metrics.rank_ic_block_months === 2
    && metrics.rank_ic_bootstrap_samples === 1000;
  const intervalStatusConsistent = hasReviewedProtocol && (
    intervalStatus === "insufficient_pairs"
      ? metrics.matured_count < EARLY_RESULT_COUNT
      : intervalStatus === "insufficient_months"
        ? metrics.matured_count >= EARLY_RESULT_COUNT && (calendarMonths ?? 0) < 12
        : metrics.matured_count >= EARLY_RESULT_COUNT && (calendarMonths ?? 0) >= 12
  );
  const validBounds = typeof metrics.rank_ic_low === "number"
    && typeof metrics.rank_ic_high === "number"
    && Number.isFinite(metrics.rank_ic_low)
    && Number.isFinite(metrics.rank_ic_high)
    && -1 <= metrics.rank_ic_low
    && metrics.rank_ic_low <= metrics.rank_ic_high
    && metrics.rank_ic_high <= 1;
  const hasClusteredInterval = intervalStatusConsistent
    && intervalStatus === "ready"
    && typeof metrics.rank_ic === "number"
    && Number.isFinite(metrics.rank_ic)
    && validBounds;
  const unreviewedIntervalResponse = metrics.forecast_count > 0
    && (!intervalStatusConsistent
      || (intervalStatus === "ready" && !hasClusteredInterval)
      || (intervalStatus !== "ready" && (metrics.rank_ic_low != null || metrics.rank_ic_high != null)));
  const rankIcDetail = hasClusteredInterval
    ? `95% time-clustered interval ${decimal(metrics.rank_ic_low, 2)} to ${decimal(metrics.rank_ic_high, 2)}`
    : unreviewedIntervalResponse
      ? "Interval withheld: this API release has not supplied the reviewed uncertainty contract"
      : intervalStatus === "insufficient_months"
        ? `${metrics.rank_ic_calendar_months ?? 0} of 12 filing months settled; interval pending`
        : intervalStatus === "insufficient_pairs"
          ? `${compact(metrics.matured_count)} of 100 results settled; interval pending`
          : intervalStatus === "capacity_review_required"
            ? "Interval paused for capacity review"
            : intervalStatus === "undefined_rank_ic" || intervalStatus === "degenerate_resamples"
              ? "No stable time-clustered interval yet"
              : `${percent(metrics.coverage)} of forecasts have results`;
  const rankIcInfo = hasClusteredInterval ? "confidenceInterval" : "rankIc";
  const historicalFailedChecks = checks.filter((check) => check.status === "failed").length;
  const historicalWarningChecks = checks.filter((check) => check.status === "warning").length;
  const latest = latestChecks(checks);
  const latestFailed = latest.filter((check) => checkTone(check) === "failed");
  const latestWarned = latest.filter((check) => checkTone(check) === "warning");
  const latestQualityLabel = !checks.length
    ? "None yet"
    : latestFailed.length
      ? `${latestFailed.length} failed`
      : latestWarned.length
        ? `${latestWarned.length} warning${latestWarned.length === 1 ? "" : "s"}`
        : "Passing";
  const LatestQualityIcon = latestFailed.length ? TriangleAlert : latestWarned.length ? Activity : CheckCircle2;
  // Name what needs attention; when nothing does, say how much history there is.
  const latestQualityDetail = latestFailed.length || latestWarned.length
    ? [...latestFailed, ...latestWarned].map((check) => checkName(check.name)).join(" · ")
    : [
        `${checks.length} checks recorded`,
        historicalFailedChecks ? `${historicalFailedChecks} failed before` : "",
        historicalWarningChecks ? `${historicalWarningChecks} warned before` : "",
      ].filter(Boolean).join(" · ");

  const early = metrics.forecast_count > 0 && metrics.matured_count < EARLY_RESULT_COUNT;
  const answer = metrics.forecast_count === 0
    ? "No forecasts have been recorded yet."
    : early
      ? <><mark>Too early to tell</mark>: {compact(metrics.matured_count)} of {compact(metrics.forecast_count)} forecasts have a result so far.</>
      : <>On {compact(metrics.matured_count)} settled forecasts, live ranking skill is <mark>{decimal(metrics.rank_ic, 3)}</mark>.</>;

  return (
    <div className="page">
      <ForwardHeader status={status.data} answer={answer} early={early} />
      {/* Quiet when healthy: the header says so. Loud when not: it needs action. */}
      {status.data.health_status !== "ok" && <RunnerHealthBanner status={status.data} />}

      <section className="figures" aria-label="Live results">
        <MetricCard label="Forecasts recorded" value={compact(metrics.forecast_count)} detail={`${compact(metrics.pending_count)} still waiting for results`} />
        <MetricCard label="Ranking skill, live" info={rankIcInfo} value={decimal(metrics.rank_ic, 3)} detail={rankIcDetail} />
        <MetricCard label="Prediction error, live" info="rmse" value={decimal(metrics.rmse, 4)} detail={`${compact(metrics.matured_count)} results in so far`} />
        <MetricCard label="Latest data checks" value={latestQualityLabel} detail={latestQualityDetail} adornment={checks.length ? <LatestQualityIcon size={20} aria-hidden="true" /> : undefined} />
      </section>

      {unreviewedIntervalResponse && (
        <p className="figures__note">
          <strong>Live uncertainty unavailable</strong>
          <span>This API returned an older or inconsistent interval format. Its bounds are hidden; treat the ranking score as a preliminary running log until the deployment is updated.</span>
        </p>
      )}

      {!unreviewedIntervalResponse && intervalStatus === "insufficient_months" && (
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

      <Protocol />
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
          <header><div><h2>Data checks</h2><p>The latest result of each automatic check on a run's inputs and outputs.</p></div></header>
          <QualityList rows={latest} />
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

function ForwardHeader({ status, answer, early = false }: { status?: ForwardStatusResponse; answer?: ReactNode; early?: boolean }) {
  return (
    <PageHeader
      title="Live tracking"
      answer={answer}
      placeholder="Too early to tell: 24 of 48 forecasts have a result so far."
      aside={status ? <RunnerStatus status={status} /> : undefined}
    >
      Since the model was frozen, it has kept scoring new filings as they arrive, with no chance to adjust them in
      hindsight.
      {early && " Until 100 have a result, treat these figures as a running log, not evidence: the frozen study's own test used 1,794 filings."}
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
          <Tag
            tone={run.status === "succeeded" ? "good" : run.status === "running" ? "warning" : "critical"}
            quiet={run.status === "succeeded"}
            className="run-status"
          >
            {run.status}
          </Tag>
          <div><strong>{run.run_type}</strong><small>{dateTime(run.started_at)}</small></div>
          <code title="Code version">{run.code_revision.slice(0, 8)}</code>
        </div>
      ))}
    </div>
  );
}

/** Quiet when a check passed; a warning or failure carries a tag, so it is what the eye finds. */
function QualityList({ rows }: { rows: ForwardQualityRecord[] }) {
  if (!rows.length) return <div className="empty-state">Checks appear after the first run.</div>;
  return (
    <div className="quality-list">
      {rows.map((check) => {
        const tone = checkTone(check);
        const Icon = tone === "passed" ? CheckCircle2 : tone === "expected" ? CircleDashed : TriangleAlert;
        const reading = tone === "expected"
          ? "No new filing to score on this run"
          : checkReading(check.name, check.observed_value, check.threshold);
        return (
          <div key={check.check_id} className={`quality-list__check quality-list__check--${tone}`}>
            <Icon size={15} aria-hidden="true" />
            <div>
              <strong>{checkName(check.name)}</strong>
              <small>{[reading, dateTime(check.created_at)].filter(Boolean).join(" · ")}</small>
            </div>
            {(tone === "failed" || tone === "warning") && <Tag tone={tone === "failed" ? "critical" : "warning"} className="quality-state">{tone}</Tag>}
          </div>
        );
      })}
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
              <td className="num" data-label="20-day result">{row.realized_abnormal_return == null ? <span className="pending-label">Due {marketDay(row.horizon_at)}</span> : signedPercent(row.realized_abnormal_return)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
