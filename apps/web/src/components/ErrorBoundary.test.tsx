import { render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PageErrorBoundary } from "./ErrorBoundary";

function Boom({ message }: { message: string }): never {
  throw new Error(message);
}

function Fine() {
  return <p>Page content</p>;
}

describe("PageErrorBoundary", () => {
  beforeEach(() => {
    // React logs every caught render error; the assertions below are the record.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("passes children through when nothing throws", () => {
    render(
      <PageErrorBoundary>
        <Fine />
      </PageErrorBoundary>,
    );

    expect(screen.getByText("Page content")).toBeInTheDocument();
  });

  it("reports a render failure instead of unmounting the page", () => {
    render(
      <PageErrorBoundary>
        <Boom message="cohort_size is undefined" />
      </PageErrorBoundary>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("This page could not be displayed");
    expect(alert).toHaveTextContent("cohort_size is undefined");
    expect(screen.getByRole("button", { name: /Reload the page/ })).toBeInTheDocument();
  });

  it("explains a failed chunk load as a new deployment", () => {
    render(
      <PageErrorBoundary>
        <Boom message="Failed to fetch dynamically imported module: /assets/ForwardPage-abc123.js" />
      </PageErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("This page has been updated");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "A new version of the site was published while this tab was open.",
    );
  });

  it("clears the failure when the route changes", () => {
    function Harness({ route }: { route: string }) {
      return (
        <PageErrorBoundary resetKey={route}>
          {route === "/broken" ? <Boom message="render failed" /> : <Fine />}
        </PageErrorBoundary>
      );
    }

    const view = render(<Harness route="/broken" />);
    expect(screen.getByRole("alert")).toBeInTheDocument();

    view.rerender(<Harness route="/working" />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("Page content")).toBeInTheDocument();
  });

  it("keeps the failure while the reader stays on the same route", () => {
    function Harness({ label }: { label: string }) {
      return (
        <PageErrorBoundary resetKey="/forward">
          <Boom message={label} />
        </PageErrorBoundary>
      );
    }

    const view = render(<Harness label="render failed" />);
    view.rerender(<Harness label="render failed again" />);

    expect(screen.getByRole("alert")).toHaveTextContent("render failed");
  });

  it("reloads the document when the reader asks for it", () => {
    const reload = vi.fn();
    const original = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...original, reload },
    });

    render(
      <PageErrorBoundary>
        <Boom message="render failed" />
      </PageErrorBoundary>,
    );
    screen.getByRole("button", { name: /Reload the page/ }).click();

    expect(reload).toHaveBeenCalledTimes(1);
    Object.defineProperty(window, "location", { configurable: true, value: original });
  });

  it("does not swallow errors thrown outside rendering", () => {
    const seen: string[] = [];
    function AfterMount() {
      useEffect(() => {
        seen.push("effect ran");
      }, []);
      return <Fine />;
    }

    render(
      <PageErrorBoundary>
        <AfterMount />
      </PageErrorBoundary>,
    );

    expect(seen).toEqual(["effect ran"]);
  });
});
