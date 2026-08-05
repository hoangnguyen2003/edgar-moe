import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRouter } from "./router-context";
import { Link, RouterProvider } from "./router";

function CurrentRoute() {
  const { pathname } = useRouter();
  return <output>{pathname}</output>;
}

describe("client router", () => {
  afterEach(() => {
    window.history.replaceState({}, "", "/");
    vi.restoreAllMocks();
  });

  it("navigates without reloading the document", () => {
    vi.stubGlobal("scrollTo", vi.fn());
    render(
      <RouterProvider>
        <CurrentRoute />
        <Link to="/research">Research</Link>
      </RouterProvider>,
    );

    fireEvent.click(screen.getByRole("link", { name: "Research" }));

    expect(screen.getByText("/research")).toBeInTheDocument();
    expect(window.location.pathname).toBe("/research");
  });
});
