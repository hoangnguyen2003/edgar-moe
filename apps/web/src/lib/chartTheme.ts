import { type Theme, useTheme } from "./theme";

// SVG chart colors mirror the stylesheet tokens for each edition (styles.css).
const chartThemes = {
  light: {
    series: "#2a78d6",
    paper: "#f4f0e6",
    grid: "#e3dccd",
    axisLine: "#857c6b",
    tick: "#5f6468",
    reference: "#3d4247",
    font: "IBM Plex Mono, IBM Plex Mono Fallback, monospace",
  },
  dark: {
    series: "#3987e5",
    paper: "#121416",
    grid: "rgba(237, 232, 220, 0.09)",
    axisLine: "#6f757c",
    tick: "#9c9587",
    reference: "#c8c1b2",
    font: "IBM Plex Mono, IBM Plex Mono Fallback, monospace",
  },
} as const satisfies Record<Theme, Record<string, string>>;

export function useChartTheme() {
  return chartThemes[useTheme().theme];
}
