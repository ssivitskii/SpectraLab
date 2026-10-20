import type { Layout, LayoutAxis } from "plotly.js";

export const chartColors = {
  surface: "#18262d",
  ink: "#e4edf1",
  muted: "#a7b8c2",
  grid: "#2b3e49",
  observed: "#67d5db",
  clean: "#9db6ff",
  reference: "#edc485",
  active: "#f2a5a5",
} as const;

const fontFamily = '"Golos Text", "Segoe UI", sans-serif';

function themedAxis(axis: Partial<LayoutAxis> = {}): Partial<LayoutAxis> {
  return {
    automargin: true,
    ticklen: 5,
    ticks: "outside",
    ...axis,
    color: chartColors.muted,
    gridcolor: chartColors.grid,
    linecolor: chartColors.grid,
    zerolinecolor: chartColors.grid,
    tickcolor: chartColors.grid,
    spikecolor: chartColors.muted,
    tickfont: {
      size: 12,
      ...axis.tickfont,
      family: fontFamily,
      color: chartColors.muted,
    },
    title: {
      standoff: 14,
      ...axis.title,
      font: {
        size: 12,
        ...axis.title?.font,
        family: fontFamily,
        color: chartColors.muted,
      },
    },
  };
}

/** Preserve data/interaction overrides while keeping every Cartesian axis legible. */
export function photonChartLayout(layout: Partial<Layout>): Partial<Layout> {
  const axes = Object.fromEntries(
    [
      ...new Set([
        "xaxis",
        "yaxis",
        ...Object.keys(layout).filter((key) => /^[xy]axis[2-9]$/.test(key)),
      ]),
    ].map((key) => [
      key,
      themedAxis(layout[key as keyof Layout] as Partial<LayoutAxis> | undefined),
    ]),
  );
  return {
    autosize: true,
    height: 350,
    hovermode: "closest",
    ...layout,
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: chartColors.surface,
    colorway: [
      chartColors.observed,
      chartColors.clean,
      chartColors.reference,
      chartColors.active,
    ],
    margin: { l: 64, r: 24, t: 44, b: 56, ...layout.margin },
    font: {
      size: 12,
      ...layout.font,
      family: fontFamily,
      color: chartColors.ink,
    },
    title: {
      ...layout.title,
      font: { ...layout.title?.font, family: fontFamily, color: chartColors.ink },
    },
    legend: {
      orientation: "h",
      x: 0,
      y: 1.08,
      yanchor: "bottom",
      ...layout.legend,
      bgcolor: "rgba(0,0,0,0)",
      bordercolor: chartColors.grid,
      font: {
        size: 12,
        ...layout.legend?.font,
        family: fontFamily,
        color: chartColors.ink,
      },
      title: {
        ...layout.legend?.title,
        font: {
          ...layout.legend?.title?.font,
          family: fontFamily,
          color: chartColors.ink,
        },
      },
    },
    hoverlabel: {
      ...layout.hoverlabel,
      bgcolor: chartColors.surface,
      bordercolor: chartColors.grid,
      font: {
        ...layout.hoverlabel?.font,
        family: fontFamily,
        color: chartColors.ink,
      },
    },
    modebar: {
      ...layout.modebar,
      bgcolor: chartColors.surface,
      color: chartColors.muted,
      activecolor: chartColors.observed,
    },
    annotations: layout.annotations?.map((annotation) => ({
      ...annotation,
      font: {
        ...annotation.font,
        family: fontFamily,
        color: chartColors.ink,
      },
    })),
    ...axes,
  };
}
