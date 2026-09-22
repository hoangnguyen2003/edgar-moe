import { useQuery } from "@tanstack/react-query";
import { PageHeader } from "../components/PageHeader";
import { ErrorState, LoadingState } from "../components/QueryState";
import { api } from "../lib/api";
import { GLOSSARY, GLOSSARY_ORDER } from "../lib/glossary";

const STEPS = [
  {
    title: "Collect data as it was known",
    text: "SEC filings with their financial statements, daily prices and corporate actions, and economic data, each exactly as it was published at the time.",
  },
  {
    title: "Check every timestamp",
    text: "Each input records when it became available. If anything from after a filing slips in, the dataset build fails instead of quietly using it.",
  },
  {
    title: "Score with three specialists",
    text: "Separate models read the filing text, the financial statements, and the market backdrop. A gate weighs them by market conditions, and a separate financial-statement model anchors the final score.",
  },
  {
    title: "Test with a market-neutral portfolio",
    text: "Buy the top 10% of scores and bet against the bottom 10%, with equal money on each side and limits on market, industry, and single-stock exposure.",
  },
];

export function MethodologyPage() {
  const methodology = useQuery({ queryKey: ["methodology"], queryFn: api.methodology });
  const header = (
    <PageHeader title="How it works">
      How a filing becomes a score, how the score was tested, and what the results can't tell you.
    </PageHeader>
  );
  if (methodology.isLoading) return <div className="page">{header}<LoadingState label="Loading the method" /></div>;
  if (methodology.error) return <div className="page">{header}<ErrorState error={methodology.error} onRetry={() => void methodology.refetch()} /></div>;
  const data = methodology.data!;
  return (
    <div className="page">
      {header}
      <ol className="steps" aria-label="From filing to score">
        {STEPS.map(({ title, text }, index) => (
          <li key={title}>
            <span className="steps__number" aria-hidden="true">{index + 1}</span>
            <h2>{title}</h2>
            <p>{text}</p>
          </li>
        ))}
      </ol>
      <section className="content-grid content-grid--two">
        <article className="panel">
          <header><div><h2>Technical summary</h2><p>The same design in the study's own terms.</p></div></header>
          <dl className="method-facts">
            <div><dt>Prediction target</dt><dd>{data.target}</dd></div>
            <div><dt>Data split</dt><dd>{data.split}</dd></div>
            <div><dt>Frozen model</dt><dd>{data.model}</dd></div>
            <div><dt>Portfolio</dt><dd>{data.portfolio}</dd></div>
            <div><dt>Costs</dt><dd>{data.costs}</dd></div>
          </dl>
        </article>
        <article className="panel limitations">
          <header><div><h2>What the results can't tell you</h2><p>Known gaps in the data and the method.</p></div></header>
          <ul>{data.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
        </article>
      </section>
      <section className="panel glossary" aria-labelledby="key-terms">
        <header><div><h2 id="key-terms">Key terms</h2><p>The words used across this site, in plain English.</p></div></header>
        <dl>
          {GLOSSARY_ORDER.map((key) => (
            <div key={key}><dt>{GLOSSARY[key].term}</dt><dd>{GLOSSARY[key].definition}</dd></div>
          ))}
        </dl>
      </section>
    </div>
  );
}
