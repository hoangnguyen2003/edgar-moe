/**
 * Plain-language definitions for the terms the pages use. Info buttons and the
 * "Key terms" list on How it works both read from here, so every page explains
 * a term the same way.
 */
export const GLOSSARY = {
  rankIc: {
    term: "Rank IC",
    definition: "How closely the model's ranking of stocks matched the ranking of their actual returns. 0 is no better than random and 1 would be perfect; for stock returns, even a few hundredths can matter if it holds up.",
  },
  rmse: {
    term: "RMSE",
    definition: "Root-mean-square error: the typical size of the model's mistakes when predicting returns. Lower is better.",
  },
  sharpe: {
    term: "Sharpe ratio",
    definition: "Yearly return divided by how much returns swing. Above 0 means the strategy was paid for its risk; below 0 means it lost money.",
  },
  volatility: {
    term: "Volatility",
    definition: "How much returns swing up and down over a year. Higher means a bumpier ride.",
  },
  drawdown: {
    term: "Worst drop",
    definition: "The maximum drawdown: the largest fall from a peak to a later low.",
  },
  turnover: {
    term: "Turnover",
    definition: "The share of the portfolio bought or sold on an average day. More trading means more costs.",
  },
  tradingCost: {
    term: "Trading cost",
    definition: "A round-trip cost charged for opening and later closing each position; 10 basis points (bps) is 0.10%. Short positions also pay a 2% yearly fee to borrow shares.",
  },
  target: {
    term: "20-day result",
    definition: "The stock's return over the 20 trading days after the filing, with the part explained by the overall market (its beta) removed. This is what the model tries to predict.",
  },
  lockedTest: {
    term: "Final test",
    definition: "The locked test: 2025–2026 filings kept out of reach until the model was frozen, then scored once. It is the fairest estimate of how the model does on new data.",
  },
  validation: {
    term: "Validation data",
    definition: "Filings from 2023–2024, used to compare the candidate models and choose one. Because the choice was made on this data, its scores flatter the winner a little.",
  },
  embargo: {
    term: "Embargo",
    definition: "A 20-trading-day gap between the training, selection, and test periods, so returns from one period cannot leak into the next.",
  },
  experts: {
    term: "Mixture of experts (MoE)",
    definition: "Three specialist models: one reads the filing text, one the financial statements, and one the market backdrop. A gate combines their scores.",
  },
  gate: {
    term: "Gate",
    definition: "The part of the model that decides how much to trust each specialist, based on current market conditions (the regime).",
  },
  xbrl: {
    term: "XBRL",
    definition: "The machine-readable financial statements attached to SEC filings, such as revenue and net income.",
  },
  direction: {
    term: "Long, short, neutral",
    definition: "Long: the top 10% of scores, which a long-short portfolio would buy. Short: the bottom 10%, which it would bet against. Neutral: everything in between, with no position.",
  },
  percentile: {
    term: "Percentile rank",
    definition: "Where a filing's score falls among scored filings. \"Top 1%\" means it scored higher than 99% of them.",
  },
  frozen: {
    term: "Frozen model",
    definition: "The model and its settings were fingerprinted and locked before the final test. Changing anything would make a new version rather than edit this one.",
  },
  fingerprint: {
    term: "Fingerprint (SHA-256)",
    definition: "A code computed from a file's contents. Changing even one byte changes the fingerprint, so a matching fingerprint shows the file is unchanged.",
  },
  forward: {
    term: "Live tracking",
    definition: "Forecasts saved before the stock can be traded, then scored once the 20 trading days have passed. Nothing can be adjusted with hindsight.",
  },
} as const;

export type GlossaryKey = keyof typeof GLOSSARY;

/** The order terms appear in on How it works. */
export const GLOSSARY_ORDER: GlossaryKey[] = [
  "experts", "gate", "xbrl", "target", "direction", "percentile",
  "rankIc", "rmse", "validation", "lockedTest", "embargo", "frozen",
  "sharpe", "volatility", "drawdown", "turnover", "tradingCost", "forward", "fingerprint",
];
