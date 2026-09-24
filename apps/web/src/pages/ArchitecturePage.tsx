import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import { CopyCommand } from "../components/CopyCommand";
import { PageHeader } from "../components/PageHeader";
import { remainingNeed } from "../lib/format";
import { governanceQuery } from "../lib/queries";
import { Link } from "../lib/router";

const REPOSITORY = "https://github.com/hoangnguyen2003/edgar-moe";
const VERIFY_COMMAND = `uv run python scripts/smoke_deployment.py \\
  https://edgar-moe.vercel.app \\
  --expect-lock config/public_snapshot.lock.json`;

type Step = { title: string; detail: string };

const LANES: Array<{ id: string; title: string; zone: string; summary: string; steps: Step[] }> = [
  {
    id: "research",
    title: "Research study",
    zone: "Finished and frozen",
    summary: "Run once, offline, then locked so the results cannot be tuned afterwards.",
    steps: [
      { title: "Collect data", detail: "SEC filings, financial statements, prices, and macro data, as known at the time" },
      { title: "Build the dataset", detail: "Every input is time-stamped; anything from after a filing fails the build" },
      { title: "Compare 33 models", detail: "Chosen on 2023–2024 data only" },
      { title: "Freeze, then test once", detail: "Fingerprinted first, then scored once on 2025–2026" },
      { title: "Publish a snapshot", detail: "The results file is pinned by a SHA-256 lock" },
    ],
  },
  {
    id: "serving",
    title: "Public site",
    zone: "Public, read-only",
    summary: "What you are using now. It holds no write credentials and cannot change any evidence.",
    steps: [
      { title: "Your browser", detail: "Anonymous visitors" },
      { title: "Web app", detail: "Static React files on the Vercel CDN" },
      { title: "API", detail: "Read-only endpoints in one small Vercel function" },
      { title: "Evidence", detail: "The locked snapshot and, when connected, the live registry through a read-only session" },
    ],
  },
  {
    id: "forward",
    title: "Live tracking",
    zone: "Private, scheduled",
    summary: "Keeps testing the frozen model on new filings, with no chance to adjust it in hindsight.",
    steps: [
      { title: "Scheduled runner", detail: "GitHub Actions, Tuesday to Saturday" },
      { title: "Forecast before trading", detail: "Saved only if the stock cannot yet be traded" },
      { title: "Append-only registry", detail: "Postgres triggers reject any edit or deletion" },
      { title: "Evidence mirror", detail: "Content-addressed copies in Cloudflare R2" },
      { title: "Independent audit", detail: "A read-only Go auditor cross-checks rows against stored bytes" },
    ],
  },
  {
    id: "copilot",
    title: "Evidence copilot",
    zone: "Private, operator-run",
    summary: "Explains the research record without entering the forecasting, registry, or public-serving path.",
    steps: [
      { title: "Operator asks a question", detail: "A local CLI sends a bounded question to an explicitly configured OpenAI-compatible provider" },
      { title: "Read-only evidence tools", detail: "The agent can inspect the frozen snapshot, governance state, derived filing events, and opt-in redacted diagnostics" },
      { title: "Citations and identity", detail: "Tool-payload hashes, frozen identity, policy/tool-contract digests, and bounded usage are retained" },
      { title: "Human review", detail: "Offline evaluation and review history are required before generated prose is relied on" },
    ],
  },
  {
    id: "delivery",
    title: "Delivery and checks",
    zone: "Verification",
    summary: "Every change is reviewed, tested, and checked again after it goes live.",
    steps: [
      { title: "Pull request", detail: "Each change is proposed and reviewed on its own branch" },
      { title: "Automated checks", detail: "Python and web tests, bundle and lock validators, CodeQL" },
      { title: "Deploy", detail: "Vercel builds the reviewed commit" },
      { title: "Post-deploy check", detail: "Production must serve exactly the reviewed fingerprints" },
    ],
  },
];

const ZONES = [
  { zone: "Public", runs: "Browser, web app, and API", can: "Read the snapshot and registry", holds: "No secrets; at most a read-only database address" },
  { zone: "Private", runs: "Scheduled runner and research pipeline", can: "Add forecasts, results, and evidence", holds: "Database writer, R2, and data-source keys, each scoped to the step that needs it" },
  { zone: "AI copilot", runs: "Operator CLI and an LLM provider", can: "Explain cited evidence and compare controls", holds: "A local provider key; no write credentials and no public endpoint" },
  { zone: "Verification", runs: "CI, CodeQL, post-deploy check, evidence auditor", can: "Check and report", holds: "No secrets, or read-only access" },
];

const DECISIONS = [
  { adr: "0001-separate-serving-and-batch", title: "Keep the public site separate from the private runner", why: "A compromised website still cannot write evidence, and the site stays cheap to host." },
  { adr: "0002-freeze-v1-prospective-evaluation", title: "Freeze the model before the final test", why: "Any later change becomes a new version, judged only on new data." },
  { adr: "0004-runtime-snapshot-lock", title: "Check the results file's fingerprint before serving it", why: "The API refuses to serve a snapshot that differs from its lock." },
  { adr: "0016-database-append-only-triggers", title: "Make the database itself refuse edits", why: "Integrity does not depend on the application behaving well." },
  { adr: "0018-workflow-supply-chain", title: "Pin every workflow action and scope every secret", why: "A compromised action or step sees as little as possible." },
  { adr: "0020-pre-open-schedule-margin", title: "Record how close each run gets to the market open", why: "A late scheduler becomes visible instead of silently risky." },
  { adr: "0022-copilot-review-profiles", title: "Keep AI review profiles bounded and read-only", why: "The copilot can help with quant and architecture reviews without gaining model or deployment authority." },
  { adr: "0028-forward-history-review-attestations", title: "Separate human review records from diagnostic evidence", why: "A reviewer decision is bound to one immutable history without implying model approval." },
];

export function ArchitecturePage() {
  // The page stands on its own; the governance contract only adds live figures.
  const governance = useQuery(governanceQuery);
  const controls = governance.data?.controls;
  const enforced = controls?.filter((control) => control.status === "enforced").length;
  const sha = governance.data?.frozen_v1.sha256;
  return (
    <div className="page">
      <PageHeader title="System architecture" answer="The public site can only read, and recorded evidence can only be added to.">
        Every published number comes from a fingerprinted snapshot, and every live forecast is saved before trading and
        never edited.
        {controls && enforced != null && ` ${enforced} of ${controls.length} safeguards are enforced in code${
          enforced < controls.length ? `; ${remainingNeed(controls.length - enforced)} operator evidence` : ""
        }.`}
      </PageHeader>

      <section className="lanes" aria-label="How the system fits together">
        {LANES.map((lane) => (
          <section key={lane.id} className="lane" aria-labelledby={`lane-${lane.id}`}>
            <header className="lane__head">
              <div>
                <h2 id={`lane-${lane.id}`}>{lane.title}</h2>
                <p>{lane.summary}</p>
              </div>
              <span className="zone-badge">{lane.zone}</span>
            </header>
            <ol className="lane__steps">
              {lane.steps.map((step) => (
                <li key={step.title}>
                  <div className="step"><strong>{step.title}</strong><span>{step.detail}</span></div>
                </li>
              ))}
            </ol>
          </section>
        ))}
      </section>

      <section className="content-grid content-grid--two">
        <article className="panel">
          <header><div><h2>Trust zones</h2><p>Each part of the system gets only the access its job needs.</p></div></header>
          <div className="zone-table">
            <table>
              <thead><tr><th scope="col">Zone</th><th scope="col">What runs there</th><th scope="col">What it can do</th><th scope="col">Credentials</th></tr></thead>
              <tbody>
                {ZONES.map((row) => (
                  <tr key={row.zone}>
                    <th scope="row">{row.zone}</th>
                    <td data-label="What runs there">{row.runs}</td>
                    <td data-label="What it can do">{row.can}</td>
                    <td data-label="Credentials">{row.holds}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </article>
        <article className="panel verify">
          <header>
            <div>
              <h2>Verify it yourself</h2>
              <p>You don't have to take the site's word for it. You need no account or credentials.</p>
            </div>
          </header>
          <ol className="verify__steps">
            <li>
              <strong>Compare the fingerprint</strong>
              <span>
                The live snapshot fingerprint
                {sha ? <>, <code title={sha}>{sha.slice(0, 16)}…</code>,</> : " (shown on the Audit page)"} must match{" "}
                <a href={`${REPOSITORY}/blob/main/config/public_snapshot.lock.json`}>the lock in the repository</a>.
              </span>
            </li>
            <li>
              <strong>Run the post-deploy check</strong>
              <span>From a clone of the repository:</span>
              <CopyCommand command={VERIFY_COMMAND} label="command" />
            </li>
            <li>
              <strong>Read the design</strong>
              <span>
                The <a href={`${REPOSITORY}/blob/main/docs/solution-architecture.md`}>solution architecture</a> maps
                each requirement to the code and test that enforce it.
              </span>
            </li>
          </ol>
          <p className="panel__note">
            <span>Safeguard status and fingerprints are on the <Link to="/governance">Audit page</Link>.</span>
          </p>
        </article>
      </section>

      <section className="panel decisions" aria-labelledby="decisions-title">
        <header>
          <div>
            <h2 id="decisions-title">Key design decisions</h2>
            <p>Eight of the 28 recorded decisions, each with its reasoning, alternatives, and how it is tested.</p>
          </div>
          <a className="button button--secondary button--small" href={`${REPOSITORY}/blob/main/docs/adr/README.md`}>
            All decisions <ArrowUpRight size={14} aria-hidden="true" />
          </a>
        </header>
        <ul className="decision-list">
          {DECISIONS.map((decision) => (
            <li key={decision.adr}>
              <a href={`${REPOSITORY}/blob/main/docs/adr/${decision.adr}.md`}>
                <small>ADR {decision.adr.slice(0, 4)}</small>
                <strong>{decision.title}</strong>
                <span>{decision.why}</span>
                <ArrowUpRight size={16} aria-hidden="true" />
              </a>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
