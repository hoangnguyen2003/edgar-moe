import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { LockKeyhole } from "lucide-react";
import { RunnerHealthBanner, RunnerStatus } from "../components/RunnerStatus";
import { CopyValue } from "../components/CopyValue";
import { InfoTip } from "../components/InfoTip";
import { MetricCard } from "../components/MetricCard";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, IDLE_DATABASE_HINT, LoadingState } from "../components/QueryState";
import { governanceQuery } from "../lib/queries";
import { humanize, shortDate } from "../lib/format";
import type { GovernanceControl, GovernanceResponse } from "../lib/types";

const CONTROL_LABELS: Record<string, string> = {
  frozen_v1_identity: "Frozen model identity",
  pre_entry_forecasts: "Forecasts saved before trading",
  append_only_outcomes: "Results can only be added, never edited",
  provider_operations: "Hosting and storage operations",
};

export function GovernancePage() {
  const governance = useQuery(governanceQuery);
  const header = (answer?: ReactNode) => (
    <PageHeader title="Audit trail" answer={answer}>
      Evidence that the published results come from the frozen model and haven't been edited since.
    </PageHeader>
  );

  if (governance.isLoading) {
    return <div className="page">{header(null)}<LoadingState label="Loading the audit trail" slowHint={IDLE_DATABASE_HINT} skeleton={["rows"]} /></div>;
  }
  if (governance.error) {
    return <div className="page">{header()}<ErrorState error={governance.error} onRetry={() => void governance.refetch()} /></div>;
  }

  const data = governance.data!;
  const enforced = data.controls.filter((control) => control.status === "enforced").length;

  return (
    <div className="page">
      {header(
        <>
          <mark>{enforced} of {data.controls.length} safeguards</mark> are enforced in code; the rest need evidence from the
          people running the service.
        </>,
      )}

      <section className="content-grid content-grid--two governance-top-grid">
        <FrozenIdentity identity={data.frozen_v1} />
        <PublicBoundary data={data} />
      </section>

      <section className="panel governance-controls-panel">
        <header>
          <div>
            <h2>Safeguards</h2>
            <p>"Enforced" means the code or database refuses to break the rule. The others need evidence from the operator.</p>
          </div>
        </header>
        <div className="governance-controls">
          {data.controls.map((control) => <ControlRow key={control.key} control={control} />)}
        </div>
      </section>

      <section className="panel governance-forward-panel">
        <header>
          <div>
            <h2>Live tracking records</h2>
            <p>Counts from the forecast database behind the Live tracking page.</p>
          </div>
        </header>
        {data.forward_status.available ? (
          <>
            <RunnerStatus status={data.forward_status} />
            {data.forward_status.health_status !== "ok" && <RunnerHealthBanner status={data.forward_status} />}
          </>
        ) : (
          <p className="caveat">
            <strong>Not connected.</strong> {data.forward_status.health_message ?? data.forward_status.message}
          </p>
        )}
        <section className="figures" aria-label="Forecast database counts">
          <MetricCard label="Models" value={String(data.forward_status.model_count)} />
          <MetricCard label="Runs" value={String(data.forward_status.run_count)} />
          <MetricCard label="Forecasts" value={String(data.forward_status.forecast_count)} />
          <MetricCard label="Waiting for results" value={String(data.forward_status.pending_count)} />
        </section>
        <p className="panel__note">
          If the database is disconnected, this page says so. Past results are never swapped in as live forecasts.
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
          <div className="panel__title"><h2>Frozen model fingerprints</h2><InfoTip term="fingerprint" /></div>
          <p>If a single byte of the published data or the model's selection changed, these codes would change too.</p>
        </div>
        <span className="badge badge--frozen">Frozen v1</span>
      </header>
      <dl className="governance-facts">
        <div><dt>Data through</dt><dd>{shortDate(identity.as_of)}</dd></div>
        <div><dt>Data type</dt><dd>{humanize(identity.data_mode)}</dd></div>
        <div><dt>Snapshot file</dt><dd><code>{identity.path}</code></dd></div>
        <div>
          <dt>Snapshot fingerprint</dt>
          <dd><code>{identity.sha256}</code></dd>
          <CopyValue value={identity.sha256} label="the snapshot fingerprint" />
        </div>
        <div>
          <dt>Model-selection fingerprint</dt>
          <dd><code>{identity.selection_hash}</code></dd>
          <CopyValue value={identity.selection_hash} label="the model-selection fingerprint" />
        </div>
        <div>
          <dt>Final-test fingerprint</dt>
          <dd><code>{identity.locked_test_hash}</code></dd>
          <CopyValue value={identity.locked_test_hash} label="the final-test fingerprint" />
        </div>
      </dl>
      <p className="panel__note"><LockKeyhole size={14} aria-hidden="true" /> Changing any of these would publish a new version, not edit this one.</p>
    </article>
  );
}

function PublicBoundary({ data }: { data: GovernanceResponse }) {
  return (
    <article className="panel governance-boundary-panel">
      <header>
        <div>
          <h2>What's public</h2>
          <p>The site publishes results derived from the data, not the raw data itself.</p>
        </div>
      </header>
      <div className="governance-boundary-list">
        <BoundaryRow label="Raw source data" value={data.public_data.raw_sources_public ? "Public" : "Private"} safe={!data.public_data.raw_sources_public} />
        <BoundaryRow label="Derived results" value={data.public_data.derived_output_public ? "Public" : "Private"} safe={data.public_data.derived_output_public} />
        <BoundaryRow label="Redistribution review" value="Required" safe={false} />
      </div>
      <p className="panel__note">
        Data-provider terms remain the operator's decision; this page does not claim legal approval.
      </p>
    </article>
  );
}

function BoundaryRow({ label, value, safe }: { label: string; value: string; safe: boolean }) {
  return (
    <div className="governance-boundary-row">
      <span>{label}</span>
      <strong className={safe ? "positive" : "pending-label"}>{value}</strong>
    </div>
  );
}

function ControlRow({ control }: { control: GovernanceControl }) {
  const enforced = control.status === "enforced";
  return (
    <div className={`governance-control ${enforced ? "" : "governance-control--pending"}`}>
      <div><strong>{CONTROL_LABELS[control.key] ?? humanize(control.key)}</strong><span>{control.summary}</span></div>
      <small>{enforced ? "Enforced" : "Needs operator evidence"}</small>
    </div>
  );
}
