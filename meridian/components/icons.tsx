/* Minimal hand-drawn icon set — 1.5px strokes, currentColor. */

type P = { className?: string };
const base = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export const MicIcon = ({ className }: P) => (
  <svg viewBox="0 0 24 24" width="18" height="18" className={className} {...base}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5 11a7 7 0 0 0 14 0" />
    <path d="M12 18v3" />
  </svg>
);

export const AudioLinesIcon = ({ className }: P) => (
  <svg viewBox="0 0 24 24" width="16" height="16" className={className} {...base}>
    <path d="M4 10v4" />
    <path d="M8 7v10" />
    <path d="M12 4v16" />
    <path d="M16 7v10" />
    <path d="M20 10v4" />
  </svg>
);

export const VolumeIcon = ({ className }: P) => (
  <svg viewBox="0 0 24 24" width="16" height="16" className={className} {...base}>
    <path d="M11 5 6.5 8.5H3.5v7h3L11 19V5Z" />
    <path d="M15.5 8.7a5 5 0 0 1 0 6.6" />
    <path d="M18.2 6a9 9 0 0 1 0 12" />
  </svg>
);

export const VolumeOffIcon = ({ className }: P) => (
  <svg viewBox="0 0 24 24" width="16" height="16" className={className} {...base}>
    <path d="M11 5 6.5 8.5H3.5v7h3L11 19V5Z" />
    <path d="m15.5 9.5 5 5" />
    <path d="m20.5 9.5-5 5" />
  </svg>
);

export const KeyboardIcon = ({ className }: P) => (
  <svg viewBox="0 0 24 24" width="16" height="16" className={className} {...base}>
    <rect x="2.5" y="6" width="19" height="12" rx="2" />
    <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M6 14h.01M18 14h.01M9 14h6" />
  </svg>
);

export const SendIcon = ({ className }: P) => (
  <svg viewBox="0 0 24 24" width="15" height="15" className={className} {...base}>
    <path d="M4.5 12 20 4.5 15.5 20l-3.8-6.2L4.5 12Z" />
    <path d="M11.7 13.8 20 4.5" />
  </svg>
);

export const CompassIcon = ({ className }: P) => (
  <svg viewBox="0 0 24 24" width="16" height="16" className={className} {...base}>
    <circle cx="12" cy="12" r="9" />
    <path d="m15.5 8.5-2.2 5-4.8 2 2.2-5 4.8-2Z" />
  </svg>
);
