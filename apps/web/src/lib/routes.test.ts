import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { prepareRoute } from "./routes";

/** The first argument of each call, as the query key it names. */
const keys = (spy: { mock: { calls: unknown[][] } }) => spy.mock.calls.map(([options]) => (options as { queryKey: unknown[] }).queryKey);

describe("route preparation", () => {
  afterEach(() => vi.restoreAllMocks());

  it("starts the requests a page makes first, read from its address", async () => {
    const client = new QueryClient();
    const infinite = vi.spyOn(client, "prefetchInfiniteQuery").mockResolvedValue();
    const single = vi.spyOn(client, "prefetchQuery").mockResolvedValue();

    await prepareRoute(client, "/filings?q=MU%20&signal=long");
    await prepareRoute(client, "/portfolio");
    await prepareRoute(client, "/research/");

    expect(keys(infinite)).toEqual([["events", "long", "MU", 100]]);
    expect(keys(single)).toEqual([["equity", 10], ["experiments"], ["summary"]]);
  });

  it("asks for Live tracking's records alongside its status, not a round trip later", async () => {
    const client = new QueryClient();
    let settleStatus: () => void = () => {};
    const single = vi.spyOn(client, "prefetchQuery").mockImplementation(async (options) => {
      // The status answers last; every record request must already be on its way.
      if ((options as { queryKey: unknown[] }).queryKey[0] === "forward-status") await new Promise<void>((resolve) => { settleStatus = resolve; });
    });

    const prepared = prepareRoute(client, "/forward");
    await Promise.resolve();

    expect(keys(single)).toEqual([["forward-status"], ["forward-performance"], ["forward-runs"], ["forward-forecasts"], ["forward-quality"]]);
    settleStatus();
    await prepared;
  });
});
