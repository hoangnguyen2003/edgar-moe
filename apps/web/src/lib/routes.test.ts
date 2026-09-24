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

  it("asks for Live tracking's records only once the registry says it is connected", async () => {
    const client = new QueryClient();
    const status = vi.spyOn(client, "fetchQuery").mockResolvedValue({ available: false } as never);
    const single = vi.spyOn(client, "prefetchQuery").mockResolvedValue();

    await prepareRoute(client, "/forward");
    expect(single).not.toHaveBeenCalled();

    status.mockResolvedValue({ available: true } as never);
    await prepareRoute(client, "/forward");
    expect(keys(single)).toEqual([["forward-performance"], ["forward-runs"], ["forward-forecasts"], ["forward-quality"]]);
  });
});
