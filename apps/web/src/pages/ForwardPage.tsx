import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  CheckCircle2,
  Clock3,
  DatabaseZap,
  GitCommitHorizontal,
  LockKeyhole,
  Orbit,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { MetricCard } from "../components/MetricCard";
import { ErrorState, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { compact, dateTime, decimal, percent } from "../lib/format";
import type { ForwardQualityRecord, ForwardStatusResponse } from "../lib/types";

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
    return <div className="page"><LoadingState label="Checking forward registry" /></div>;
  }
  if (status.error) {
    return <div className="page"><ErrorState error={status.error} /></div>;
  }
  if (!status.data?.available) {
    return <UnconfiguredForwardLab configured={Boolean(status.data?.configured)} />;
  }
  if (performance.isLoading || runs.isLoading || forecasts.isLoading || quality.isLoading) {
    return <div className="page"><LoadingState label="Loading prospective evidence" /></div>;
  }
  const error = performance.error ?? runs.error ?? forecasts.error ?? quality.error;
  if (error) return <div className="page"><ErrorState error={error} /></div>;

  const metrics = performance.data!;
  const checks = quality.data!;
  const failedChecks = checks.filter((check) => check.status === "failed").length;
  const warningChecks = checks.filter((check) => check.status === "warning").length;

  return (
    <div className="page">
      <PageHeader />
      <div className="forward-trust-strip">
        <span><LockKeyhole size={14} /> Frozen model</span>
        <i />
        <span><Clock3 size={14} /> Pre-entry timestamp</span>
        <i />
        <span><DatabaseZap size={14} /> Append-only outcome</span>
      </div>

      <ForwardHealthBanner status={status.data} />

      <section className="metric-grid">
        <MetricCard label="Recorded forecasts" value={compact(metrics.forecast_count)} detail={`${compact(metrics.pending_count)} awaiting maturity`} icon={Orbit} />
        <MetricCard label="Forward rank IC" value={decimal(metrics.rank_ic, 3)} detail={`${percent(metrics.coverage)} label coverage`} icon={ShieldCheck} tone="blue" />
        <MetricCard label="Forward RMSE" value={decimal(metrics.rmse, 4)} detail={`${compact(metrics.matured_count)} matured outcomes`} icon={DatabaseZap} tone="amber" />
        <MetricCard label="Quality status" value={failedChecks ? `${failedChecks} failed` : warningChecks ? `${warningChecks} warn` : "Passing"} detail={`${checks.length} immutable checks`} icon={failedChecks ? TriangleAlert : CheckCircle2} />
      </section>

      {metrics.forecast_count === 0 && (
        <div className="notice notice--forward">
          <Clock3 size={18} />
          <div><strong>Registry ready; first qualifying batch pending</strong><span>A forecast is accepted only when it is recorded after the filing arrives and before the next tradable entry. Historical rows are never relabeled as live predictions.</span></div>
        </div>
      )}

      <section className="content-grid content-grid--two forward-top-grid">
        <article className="panel">
          <header><div><span className="panel__kicker">Run ledger</span><h2>Every state transition is preserved</h2></div><GitCommitHorizontal size={20} /></header>
          <RunLedger rows={runs.data!} />
        </article>
        <article className="panel">
          <header><div><span className="panel__kicker">Data contracts</span><h2>Quality gates</h2></div><ShieldCheck size={20} /></header>
          <QualityList rows={checks} />
        </article>
      </section>

      <section className="panel forward-forecast-panel">
        <header><div><span className="panel__kicker">Prospective tape</span><h2>Forecasts before outcomes</h2><p>Scores and expert weights are immutable; realized returns appear only after the 20-session horizon matures.</p></div><span className="forward-count">{forecasts.data!.total} rows</span></header>
        <ForecastTable rows={forecasts.data!.items} />
      </section>
    </div>
  );
}

function ForwardHealthBanner({ status }: { status: ForwardStatusResponse }) {
  const healthy = status.health_status === "ok";
  const warning = status.health_status === "warning";
  const Icon = healthy ? CheckCircle2 : warning ? Activity : TriangleAlert;
  const tone = healthy ? "notice--forward" : "notice--warning";
  const age = status.age_seconds == null
    ? "no successful run recorded"
    : `age ${formatAge(status.age_seconds)}`;
  const running = status.running_run_count === 1
    ? "1 run currently active"
    : `${status.running_run_count} runs currently active`;

  return (
    <div className={`notice ${tone} forward-health-banner`}>
      <Icon size={18} />
      <div>
        <strong>{status.health_message ?? status.message}</strong>
        <span>
          Latest success: {dateTime(status.latest_successful_run_at)} · {age} · {running}
        </span>
      </div>
    </div>
  );
}

function formatAge(seconds: number): string {
  if (seconds < 60) return "less than a minute";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

function PageHeader() {
  return (
    <header className="page-header">
      <span>Prospective evaluation</span>
      <h1>Evidence that cannot look ahead.</h1>
      <p>The v1 research result stays frozen. New filing forecasts are timestamped before entry, stored with content hashes, and evaluated only when their outcomes become observable.</p>
    </header>
  );
}

function UnconfiguredForwardLab({ configured }: { configured: boolean }) {
  return (
    <div className="page">
      <PageHeader />
      <section className="forward-empty panel">
        <div className="forward-empty__icon"><Orbit size={30} /></div>
        <span className="panel__kicker">Infrastructure state</span>
        <h2>{configured ? "Registry temporarily unavailable" : "Registry connection pending"}</h2>
        <p>{configured ? "The database is configured, but the API could not read it. Historical research remains available elsewhere in the terminal." : "The application and append-only schema are ready. This deployment has no database URL, so it cannot claim or display prospective observations yet."}</p>
        <div className="forward-protocol">
          <div><span>01</span><strong>Freeze</strong><small>Hash-pin model and selection</small></div>
          <div><span>02</span><strong>Forecast</strong><small>Record before tradable entry</small></div>
          <div><span>03</span><strong>Settle</strong><small>Append labels after maturity</small></div>
        </div>
        <div className="forward-empty__note"><LockKeyhole size={15} /> No synthetic or backfilled rows are presented as forward evidence.</div>
      </section>
    </div>
  );
}

function RunLedger({ rows }: { rows: Awaited<ReturnType<typeof api.forwardRuns>> }) {
  if (!rows.length) return <div className="empty-state">No forward runs recorded yet.</div>;
  return (
    <div className="forward-list">
      {rows.slice(0, 8).map((run) => (
        <div key={run.run_id}>
          <span className={`run-status run-status--${run.status}`}>{run.status}</span>
          <div><strong>{run.run_type}</strong><small>{dateTime(run.started_at)}</small></div>
          <code>{run.code_revision.slice(0, 8)}</code>
        </div>
      ))}
    </div>
  );
}

function QualityList({ rows }: { rows: ForwardQualityRecord[] }) {
  if (!rows.length) return <div className="empty-state">Checks appear with the first run.</div>;
  return (
    <div className="quality-list">
      {rows.slice(0, 8).map((check) => (
        <div key={check.check_id}>
          {check.status === "passed" ? <CheckCircle2 size={15} /> : <TriangleAlert size={15} />}
          <div><strong>{check.name.replaceAll("_", " ")}</strong><small>{dateTime(check.created_at)}</small></div>
          <span className={`quality-state quality-state--${check.status}`}>{check.status}</span>
        </div>
      ))}
    </div>
  );
}

function ForecastTable({ rows }: { rows: Awaited<ReturnType<typeof api.forwardForecasts>>["items"] }) {
  if (!rows.length) return <div className="empty-state">No qualifying pre-entry forecasts have been recorded.</div>;
  return (
    <div className="forward-table-wrap">
      <table className="forward-table">
        <thead><tr><th>Event</th><th>Recorded</th><th>Entry</th><th>Score</th><th>Rank</th><th>Outcome</th></tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.forecast_id}>
              <td><strong>{row.ticker}</strong><span>{row.form} · {row.company_name}</span></td>
              <td>{dateTime(row.forecast_as_of)}</td>
              <td>{dateTime(row.entry_at)}</td>
              <td className={row.score >= 0 ? "positive" : "negative"}>{decimal(row.score, 4)}</td>
              <td>{percent(row.rank, 0)}</td>
              <td>{row.realized_abnormal_return == null ? <span className="pending-label">Pending</span> : percent(row.realized_abnormal_return)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
