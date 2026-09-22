import { useQuery } from "@tanstack/react-query";
import { AlertOctagon, Clock3, DatabaseZap, Network, Scale } from "lucide-react";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";

export function MethodologyPage() {
  const methodology = useQuery({ queryKey: ["methodology"], queryFn: api.methodology });
  if (methodology.isLoading) return <div className="page"><LoadingState label="Loading methodology contract" /></div>;
  if (methodology.error) return <div className="page"><ErrorState error={methodology.error} onRetry={() => void methodology.refetch()} /></div>;
  const data = methodology.data!;
  const steps = [
    { icon: DatabaseZap, title: "Point-in-time ingest", text: "SEC acceptance timestamps, XBRL filing dates, adjusted prices, corporate actions, and vintage macro observations." },
    { icon: Clock3, title: "Availability audit", text: "Every feature carries available_at metadata. Future information fails the dataset build instead of being silently shifted." },
    { icon: Network, title: "Modality experts", text: data.model },
    { icon: Scale, title: "Neutral portfolio", text: data.portfolio },
  ];
  return (
    <div className="page">
      <PageHeader section="08" kicker="Reproducibility contract" title="Method before metric.">
        The project is designed so a skeptical reviewer can trace every score back to what was known when the decision was made.
      </PageHeader>
      <ol className="method-flow" aria-label="Research pipeline">
        {steps.map(({ icon: Icon, title, text }, index) => (
          <li key={title}>
            <span>§ 8.{index + 1}</span>
            <Icon size={22} aria-hidden="true" />
            <h2>{title}</h2>
            <p>{text}</p>
          </li>
        ))}
      </ol>
      <section className="content-grid content-grid--two">
        <article className="panel"><header><div><span className="panel__kicker">Target</span><h2>{data.target}</h2></div></header><dl className="method-facts"><div><dt>Split</dt><dd>{data.split}</dd></div><div><dt>Costs</dt><dd>{data.costs}</dd></div></dl></article>
        <article className="panel limitations"><header><div><span className="panel__kicker">Risk factors</span><h2>Claims we do not make</h2></div><AlertOctagon size={20} aria-hidden="true" /></header><ul>{data.limitations.map((item) => <li key={item}>{item}</li>)}</ul></article>
      </section>
    </div>
  );
}
