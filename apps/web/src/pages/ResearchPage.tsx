import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, GitCompareArrows } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ErrorState, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { decimal } from "../lib/format";

export function ResearchPage() {
  const experiments = useQuery({ queryKey: ["experiments"], queryFn: api.experiments });
  const summary = useQuery({ queryKey: ["summary"], queryFn: api.summary });
  if (experiments.isLoading || summary.isLoading) return <div className="page"><LoadingState label="Loading experiment registry" /></div>;
  if (experiments.error) return <div className="page"><ErrorState error={experiments.error} /></div>;
  const rows = experiments.data!;
  const selected = rows.find((row) => row.selected);
  return (
    <div className="page">
      <PageHeader kicker="Experiment registry" title="Models earn their place." description="All candidates use identical chronological splits. Selection uses pre-test folds only; the frozen locked result is reported separately." />
      <section className="content-grid content-grid--three">
        <article className="metric-card metric-card--green"><div className="metric-card__top"><span>Selected model</span><CheckCircle2 size={18} /></div><strong className="metric-card__model">{selected?.name}</strong><small>Epoch {selected?.best_epoch ?? "—"}</small></article>
        <article className="metric-card metric-card--blue"><div className="metric-card__top"><span>Validation RMSE</span><GitCompareArrows size={18} /></div><strong>{decimal(selected?.validation_rmse, 4)}</strong><small>Lower is better</small></article>
        <article className="metric-card metric-card--amber"><div className="metric-card__top"><span>Validation rank IC</span><GitCompareArrows size={18} /></div><strong>{decimal(selected?.validation_rank_ic, 3)}</strong><small>Cross-sectional ordering</small></article>
      </section>
      <section className="content-grid content-grid--two">
        <article className="panel chart-panel">
          <header><div><span className="panel__kicker">Baseline comparison</span><h2>Validation error</h2></div></header>
          <div className="chart-wrap">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} layout="vertical" margin={{ left: 20, right: 24 }}>
                <CartesianGrid horizontal={false} stroke="rgba(255,255,255,.07)" />
                <XAxis type="number" tick={{ fill: "#82958e", fontSize: 11 }} />
                <YAxis type="category" dataKey="name" width={112} tick={{ fill: "#cbd7d2", fontSize: 11 }} />
                <Tooltip contentStyle={{ background: "#10221c", border: "1px solid #294239", borderRadius: 8 }} />
                <Bar dataKey="validation_rmse" fill="#55d89b" radius={[0, 5, 5, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </article>
        <article className="panel">
          <header><div><span className="panel__kicker">Ablation logic</span><h2>What the comparison proves</h2></div></header>
          <ol className="research-list">
            <li><span>01</span><div><strong>Linear baseline</strong><p>Tests whether the result is merely a stable additive factor model.</p></div></li>
            <li><span>02</span><div><strong>Non-linear tabular baseline</strong><p>Measures what flexible trees capture without a modality gate.</p></div></li>
            <li><span>03</span><div><strong>Regime-gated MoE</strong><p>Must improve rank quality and survive cost-aware portfolio tests—not only prediction loss.</p></div></li>
          </ol>
        </article>
      </section>
      {summary.data?.metadata.data_mode === "synthetic_fixture" && <DemoNotice />}
    </div>
  );
}

function DemoNotice() {
  return <div className="notice"><strong>Verification mode</strong><span>These values come from a deterministic synthetic dataset. They validate model selection, API contracts, and visualization—not market alpha.</span></div>;
}

function PageHeader({ kicker, title, description }: { kicker: string; title: string; description: string }) {
  return <header className="page-header"><span>{kicker}</span><h1>{title}</h1><p>{description}</p></header>;
}
