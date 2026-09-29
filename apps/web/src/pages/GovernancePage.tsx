import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { LockKeyhole } from "lucide-react";
import { CopyValue } from "../components/CopyValue";
import { InfoTip } from "../components/InfoTip";
import { PageHeader } from "../components/PageHeader";
import { Tag } from "../components/Tag";
import { ErrorState, IDLE_DATABASE_HINT, LoadingState } from "../components/QueryState";
import { governanceQuery } from "../lib/queries";
import { humanize, remainingNeed, shortDate } from "../lib/format";
import type { GovernanceControl, GovernanceResponse } from "../lib/types";

const CONTROL_LABELS: Record<string, string> = {
  published_snapshot_identity: "Published synthetic snapshot identity",
  pre_entry_forecasts: "Forecasts saved before trading",
  append_only_outcomes: "Results can only be added, never edited",
  prospective_publication: "Prospective records withheld during review",
  provider_operations: "Hosting and storage operations",
};

export function GovernancePage() {
  const governance = useQuery(governanceQuery);
  const header = (answer?: ReactNode) => (
    <PageHeader title="Audit trail" answer={answer} placeholder="The public demo is synthetic; historical v1 output remains withheld pending source-rights review.">
      The current public dataset is a fingerprinted synthetic fixture, not investment research evidence.
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
  const operatorEvidence = data.controls.filter((control) => control.status === "pending_operator_evidence").length;
  const withheld = data.controls.filter((control) => control.status === "withheld_review").length;

  return (
    <div className="page">
      {header(
        <>
          <mark>{enforced} of {data.controls.length} safeguards</mark> are enforced in code
          {operatorEvidence > 0 ? `; ${remainingNeed(operatorEvidence)} evidence from the people running the service` : ""}
          {withheld > 0 ? `; ${withheld} public-data boundary is withheld during review` : ""}.
        </>,
      )}

      <section className="content-grid content-grid--two governance-top-grid">
        <PublishedIdentity identity={data.published_snapshot} />
        <PublicBoundary data={data} />
      </section>

      <section className="panel governance-controls-panel">
        <header>
          <div>
            <h2>Safeguards</h2>
            <p>"Enforced" means code refuses the action; operator evidence and deliberate review holds are labeled separately.</p>
          </div>
        </header>
        <div className="governance-controls">
          {data.controls.map((control) => <ControlRow key={control.key} control={control} />)}
        </div>
      </section>

      <section className="panel governance-forward-panel">
        <header>
          <div>
            <h2>Prospective evidence</h2>
            <p>The private runner and append-only registry are not a public data source.</p>
          </div>
        </header>
          <p className="caveat">
            <strong>Withheld from public view.</strong> {data.forward_status.message}
          </p>
        <p className="panel__note">
          Run details, forecasts, labels, quality checks, and performance metrics are not returned by the public API while source-rights review remains open.
        </p>
      </section>
    </div>
  );
}

function PublishedIdentity({ identity }: { identity: GovernanceResponse["published_snapshot"] }) {
  return (
    <article className="panel governance-identity-panel">
      <header>
        <div>
          <div className="panel__title"><h2>Synthetic demo fingerprint</h2><InfoTip term="fingerprint" /></div>
          <p>This identifies the public software fixture. It is not the frozen v1 research record.</p>
        </div>
        <Tag tone="stamp">Synthetic demo</Tag>
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
          <dd>Not applicable to synthetic demo</dd>
        </div>
        <div>
          <dt>Final-test fingerprint</dt>
          <dd>Not applicable to synthetic demo</dd>
        </div>
      </dl>
      <p className="panel__note"><LockKeyhole size={14} aria-hidden="true" /> The former v1 event-level snapshot is withheld while its source-use review remains open.</p>
    </article>
  );
}

function PublicBoundary({ data }: { data: GovernanceResponse }) {
  return (
    <article className="panel governance-boundary-panel">
      <header>
        <div>
          <h2>What's public</h2>
          <p>The live demo publishes generated values only; no market or filing data is included.</p>
        </div>
      </header>
      <div className="governance-boundary-list">
        <BoundaryRow label="Raw source data" value={data.public_data.raw_sources_public ? "Public" : "Private"} safe={!data.public_data.raw_sources_public} />
        <BoundaryRow label="Current output" value="Synthetic demo" safe />
        <BoundaryRow label="Historical v1 output served by this app" value={data.public_data.historical_v1_served_by_application ? "Public" : "Withheld"} safe={!data.public_data.historical_v1_served_by_application} />
        <BoundaryRow label="Prospective outputs served by this app" value={data.public_data.prospective_outputs_served_by_application ? "Public" : "Withheld"} safe={!data.public_data.prospective_outputs_served_by_application} />
        <BoundaryRow label="Redistribution review" value="Required" safe={false} />
      </div>
      <p className="panel__note">
        Historical source and redistribution rights remain under review. The live app withholds v1 results, but older reports may remain in the public repository, Git history, and prior deployments; this is not legal clearance or a full takedown.
      </p>
    </article>
  );
}

function BoundaryRow({ label, value, safe }: { label: string; value: string; safe: boolean }) {
  return (
    <div className="governance-boundary-row">
      <span className="governance-boundary-row__label">{label}</span>
      <Tag tone={safe ? "good" : "warning"} quiet={safe}>{value}</Tag>
    </div>
  );
}

function ControlRow({ control }: { control: GovernanceControl }) {
  const enforced = control.status === "enforced";
  const withheld = control.status === "withheld_review";
  return (
    <div className={`governance-control ${enforced ? "" : "governance-control--pending"}`}>
      <div><strong>{CONTROL_LABELS[control.key] ?? humanize(control.key)}</strong><span>{control.summary}</span></div>
      <Tag tone={enforced ? "good" : "warning"}>{enforced ? "Enforced" : withheld ? "Withheld for review" : "Needs operator evidence"}</Tag>
    </div>
  );
}
