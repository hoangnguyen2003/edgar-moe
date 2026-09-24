import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("states the answer under the title, holds its line while it loads, and leaves it out when there is none", () => {
    const { container, rerender } = render(<PageHeader title="Backtest" answer={null} placeholder="A sentence shaped like the answer.">Context</PageHeader>);
    // The placeholder wraps like the answer will, so its arrival moves nothing; readers don't hear it.
    const placeholder = container.querySelector(".page-header__answer .page-header__placeholder");
    expect(placeholder).toHaveTextContent("A sentence shaped like the answer.");
    expect(placeholder).toHaveAttribute("aria-hidden", "true");

    rerender(<PageHeader title="Backtest" answer={<>The portfolio <mark>lost 3.3% a year</mark>.</>}>Context</PageHeader>);
    const answer = container.querySelector(".page-header__answer");
    expect(answer).toHaveTextContent("The portfolio lost 3.3% a year.");
    expect(answer?.nextElementSibling).toHaveTextContent("Context");

    rerender(<PageHeader title="Backtest">Context</PageHeader>);
    expect(container.querySelector(".page-header__answer")).toBeNull();
  });
});
