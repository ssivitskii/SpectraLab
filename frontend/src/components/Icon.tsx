import type { ReactNode } from "react";

const paths = {
  spectrum: <path d="M3 18h3l3-7 3 5 4-12 3 14h2M3 21h18" />,
  flask: <path d="M9 3h6M10 3v6L4.8 18a2 2 0 0 0 1.7 3h11a2 2 0 0 0 1.7-3L14 9V3M8 14h8" />,
  experiments: <path d="M4 20V10m5 10V4m6 16v-8m5 8V7M2 21h20" />,
  layers: <path d="m3 7 9-4 9 4-9 4-9-4Zm0 5 9 4 9-4M3 17l9 4 9-4" />,
  upload: <path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5" />,
  file: <path d="M14 2H5v20h14V7l-5-5Zm0 0v6h5M8 13h8m-8 4h5" />,
  arrowRight: <path d="M4 12h16m-6-6 6 6-6 6" />,
  download: <path d="M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5" />,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6m0-10v.01" /></>,
  check: <path d="m5 12 4 4L19 6" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  settings: <><path d="M4 7h16M4 17h16" /><circle cx="9" cy="7" r="3" /><circle cx="15" cy="17" r="3" /></>,
  activity: <path d="M2 12h5l3-8 4 16 3-8h5" />,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
  sliders: <path d="M6 3v18M12 3v18M18 3v18M3 8h6m0 8h6m0-10h6" />,
  arrowUpRight: <path d="M5 19 19 5M5 5h14v14" />,
  folder: <path d="M3 6h7l2 3h9v12H3V6Z" />,
  play: <path d="m8 4 12 8-12 8V4Z" />,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6.1 6a8 8 0 0 1 13.3 3M4.6 15a8 8 0 0 0 13.3 3" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
} satisfies Record<string, ReactNode>;

export type IconName = keyof typeof paths;

export function Icon({ name, size = 20, className = "" }: { name: IconName; size?: number; className?: string }) {
  return <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{paths[name]}</svg>;
}

export function SpectrumMark() {
  return <svg className="brand-mark" width="31" height="34" viewBox="0 0 31 34" fill="none" aria-hidden="true"><path d="m2 27 6-12 5 10L20 4l8 23" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}
