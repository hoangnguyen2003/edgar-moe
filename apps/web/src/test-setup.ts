import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest runs without globals, so Testing Library cannot register its automatic
// cleanup; without this, rendered DOM leaks between tests in the same file.
afterEach(() => cleanup());
