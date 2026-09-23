import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useDebouncedValue } from "./useDebouncedValue";

describe("useDebouncedValue", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns the first value without waiting", () => {
    const { result } = renderHook(() => useDebouncedValue("AAPL", 250));

    expect(result.current).toBe("AAPL");
  });

  it("holds the previous value until the delay has passed", () => {
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 250), {
      initialProps: { value: "A" },
    });

    rerender({ value: "AB" });
    act(() => {
      vi.advanceTimersByTime(249);
    });
    expect(result.current).toBe("A");

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe("AB");
  });

  it("sends one value when a reader keeps typing", () => {
    // Each keystroke restarts the wait, which is the point: the filings query
    // runs once for "AAPL" rather than four times for its prefixes.
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 250), {
      initialProps: { value: "A" },
    });

    for (const value of ["AA", "AAP", "AAPL"]) {
      act(() => {
        vi.advanceTimersByTime(200);
      });
      rerender({ value });
    }
    expect(result.current).toBe("A");

    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(result.current).toBe("AAPL");
  });

  it("drops a pending update when the component goes away", () => {
    const { rerender, unmount } = renderHook(({ value }) => useDebouncedValue(value, 250), {
      initialProps: { value: "A" },
    });

    rerender({ value: "AB" });
    unmount();

    // A timer surviving unmount would set state on a gone component; React
    // reports that as a console error, which the test setup treats as failure.
    expect(() => {
      act(() => {
        vi.advanceTimersByTime(500);
      });
    }).not.toThrow();
  });
});
