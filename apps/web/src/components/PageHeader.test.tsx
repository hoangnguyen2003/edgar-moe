import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("states the answer under the title, holds its line while it loads, and leaves it out when there is none", () => {
    const { container, rerender } = render(<PageHeader title="Backtest" answer={null}>Context</PageHeader>);
    expect(container.querySelector(".page-header__answer .skeleton-bar")).not.toBeNull();

    rerender(<PageHeader title="Backtest" answer={<>The portfolio <mark>lost 3.3% a year</mark>.</>}>Context</PageHeader>);
    const answer = container.querySelector(".page-header__answer");
    expect(answer).toHaveTextContent("The portfolio lost 3.3% a year.");
    expect(answer?.nextElementSibling).toHaveTextContent("Context");

    rerender(<PageHeader title="Backtest">Context</PageHeader>);
    expect(container.querySelector(".page-header__answer")).toBeNull();
  });
});
