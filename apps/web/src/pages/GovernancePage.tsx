import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  Clock3,
  GitBranch,
  LockKeyhole,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { dateTime, humanize } from "../lib/format";
import type { GovernanceControl, GovernanceResponse } from "../lib/types";

const CONTROL_LABELS: Record<string, string> = {
  frozen_v1_identity: "Frozen v1 identity",
  pre_entry_forecasts: "Pre-entry forecasts",
  append_only_outcomes: "Append-only outcomes",
  provider_operations: "Provider operations",
};

export function GovernancePage() {
  const governance = useQuery({ queryKey: ["governance"], queryFn: api.governance });

  if (governance.isLoading) {
    return <div className="page"><LoadingState label="Reading governance contract" /></div>;
  }
  if (governance.error) {
    return <div className="page"><ErrorState error={governance.error} onRetry={() => void governance.refetch()} /></div>;
  }

  const data = governance.data!;
  const forwardHealthy = data.forward_status.health_status === "ok";
  const ForwardIcon = forwardHealthy ? CheckCircle2 : TriangleAlert;
  const forwardTone = forwardHealthy ? "notice--forward" : "notice--warning";

  return (
    <div className="page">
      <PageHeader section="07" kicker="Governance plane" title="Evidence with an explicit boundary.">
        The frozen v1 identity, public-data boundary, and prospective controls are
        presented together so a reader can distinguish repository evidence from
        provider-side observations.
      </PageHeader>

      <div className={`notice ${forwardTone}`}>
        <ForwardIcon size={18} aria-hidden="true" />
        <div>
          <strong>{data.forward_status.health_message ?? data.forward_status.message}</strong>
          <span>
            Forward registry: {data.forward_status.available ? "connected" : "not connected"}
            {data.forward_status.latest_successful_run_at
              ? ` · latest success ${dateTime(data.forward_status.latest_successful_run_at)}`
              : " · no successful run observed"}
          </span>
        </div>
      </div>

      <section className="content-grid content-grid--two governance-top-grid">
        <FrozenIdentity identity={data.frozen_v1} />
        <PublicBoundary data={data} />
      </section>

      <section className="panel governance-controls-panel">
        <header>
          <div>
            <span className="panel__kicker">Control plane</span>
            <h2>What the repository can prove</h2>
            <p>Enforced controls are software evidence; pending controls require an operator exercise.</p>
          </div>
          <ShieldCheck size={20} aria-hidden="true" />
        </header>
        <div className="governance-controls">
          {data.controls.map((control) => <ControlRow key={control.key} control={control} />)}
        </div>
      </section>

      <section className="panel governance-forward-panel">
        <header>
          <div>
            <span className="panel__kicker">Prospective lane</span>
            <h2>Append-only forward evidence</h2>
          </div>
          <GitBranch size={20} aria-hidden="true" />
        </header>
        <div className="governance-lane">
          <LaneMetric label="Models" value={String(data.forward_status.model_count)} />
          <LaneMetric label="Runs" value={String(data.forward_status.run_count)} />
          <LaneMetric label="Forecasts" value={String(data.forward_status.forecast_count)} />
          <LaneMetric label="Pending labels" value={String(data.forward_status.pending_count)} />
        </div>
        <p className="panel__note">
          A disconnected registry is reported as disconnected; the historical snapshot is never
          replaced with synthetic or backfilled prospective rows.
        </p>
      </section>
    </div>
  );
}

function FrozenIdentity({ identity }: { identity: GovernanceResponse["frozen_v1"] }) {
  return (
    <article className="panel governance-identity-panel">
      <header>
        <div>
          <span className="panel__kicker">Frozen v1</span>
          <h2>Content-addressed identity</h2>
        </div>
        <span className="stamp">Frozen v1</span>
      </header>
      <dl className="governance-facts">
        <div><dt>Snapshot</dt><dd><code>{identity.path}</code></dd></div>
        <div><dt>As of</dt><dd>{identity.as_of}</dd></div>
        <div><dt>Data mode</dt><dd>{identity.data_mode}</dd></div>
        <div><dt>Snapshot SHA-256</dt><dd><code>{identity.sha256}</code></dd></div>
        <div><dt>Selection hash</dt><dd><code>{identity.selection_hash}</code></dd></div>
        <div><dt>Locked-test hash</dt><dd><code>{identity.locked_test_hash}</code></dd></div>
      </dl>
      <p className="panel__note"><LockKeyhole size={14} aria-hidden="true" /> Research-only output; changing this identity is a new reviewed v1.</p>
    </article>
  );
}

function PublicBoundary({ data }: { data: GovernanceResponse }) {
  return (
    <article className="panel governance-boundary-panel">
      <header>
        <div>
          <span className="panel__kicker">Public boundary</span>
          <h2>Derived data, not raw sources</h2>
        </div>
        <LockKeyhole size={20} aria-hidden="true" />
      </header>
      <div className="governance-boundary-list">
        <BoundaryRow label="Raw source data" value={data.public_data.raw_sources_public ? "Public" : "Private"} safe={!data.public_data.raw_sources_public} />
        <BoundaryRow label="Derived output" value={data.public_data.derived_output_public ? "Public" : "Private"} safe={data.public_data.derived_output_public} />
        <BoundaryRow label="Redistribution review" value="Required" safe={false} />
      </div>
      <p className="panel__note">
        Provider terms and CDN controls remain operator decisions; this endpoint does not claim legal approval.
      </p>
    </article>
  );
}

function BoundaryRow({ label, value, safe }: { label: string; value: string; safe: boolean }) {
  return (
    <div className="governance-boundary-row">
      {safe ? <CheckCircle2 size={16} aria-hidden="true" /> : <Clock3 size={16} aria-hidden="true" />}
      <span>{label}</span>
      <strong className={safe ? "positive" : "pending-label"}>{value}</strong>
    </div>
  );
}

function ControlRow({ control }: { control: GovernanceControl }) {
  const enforced = control.status === "enforced";
  return (
    <div className={`governance-control ${enforced ? "" : "governance-control--pending"}`}>
      {enforced ? <CheckCircle2 size={16} aria-hidden="true" /> : <Clock3 size={16} aria-hidden="true" />}
      <div><strong>{CONTROL_LABELS[control.key] ?? humanize(control.key)}</strong><span>{control.summary}</span></div>
      <small>{enforced ? "Enforced" : "Operator evidence"}</small>
    </div>
  );
}

function LaneMetric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}
