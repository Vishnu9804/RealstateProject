/**
 * Inline stroke icons. Kept as hand-written SVG rather than an icon package
 * so they share one stroke weight and one geometry with the rest of the UI
 * (and add zero bytes of dependency). Every icon inherits `currentColor`,
 * which is what lets a badge/button tint its icon by setting text colour.
 */

interface IconProps {
  size?: number;
  className?: string;
  strokeWidth?: number;
}

function svg(path: React.ReactNode, viewBox = "0 0 24 24") {
  return function Icon({ size = 16, className, strokeWidth = 1.8 }: IconProps) {
    return (
      <svg
        width={size}
        height={size}
        viewBox={viewBox}
        fill="none"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
        className={className}
        aria-hidden="true"
        focusable="false"
      >
        {path}
      </svg>
    );
  };
}

export const IconLink = svg(
  <>
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
  </>,
);

export const IconGrid = svg(
  <>
    <rect x="3" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="3" width="7" height="7" rx="1.5" />
    <rect x="3" y="14" width="7" height="7" rx="1.5" />
    <rect x="14" y="14" width="7" height="7" rx="1.5" />
  </>,
);

export const IconSliders = svg(
  <>
    <path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3" />
    <path d="M1 14h6M9 8h6M17 16h6" />
  </>,
);

export const IconSearch = svg(
  <>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.2-3.2" />
  </>,
);

export const IconX = svg(<path d="M18 6 6 18M6 6l12 12" />);
export const IconCheck = svg(<path d="M20 6 9 17l-5-5" />);
export const IconPlus = svg(<path d="M12 5v14M5 12h14" />);
export const IconChevron = svg(<path d="m6 9 6 6 6-6" />);
export const IconArrowRight = svg(<path d="M5 12h14M13 6l6 6-6 6" />);

export const IconCopy = svg(
  <>
    <rect x="9" y="9" width="12" height="12" rx="2.5" />
    <path d="M5 15V5a2 2 0 0 1 2-2h10" />
  </>,
);

export const IconSun = svg(
  <>
    <circle cx="12" cy="12" r="4.2" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </>,
);

export const IconMoon = svg(<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />);

export const IconCommand = svg(
  <path d="M18 3a3 3 0 0 0-3 3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6a3 3 0 1 0-3 3h12a3 3 0 0 0 0-6Z" />,
);

export const IconAlert = svg(
  <>
    <path d="M10.3 3.9 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
    <path d="M12 9v4M12 17h.01" />
  </>,
);

export const IconInfo = svg(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 16v-4M12 8h.01" />
  </>,
);

export const IconCheckCircle = svg(
  <>
    <path d="M21.2 11.1V12a9 9 0 1 1-5.3-8.2" />
    <path d="m9 11 3 3 9-9" />
  </>,
);

export const IconRefresh = svg(
  <>
    <path d="M3 12a9 9 0 0 1 15.5-6.2L21 8" />
    <path d="M21 3v5h-5" />
    <path d="M21 12a9 9 0 0 1-15.5 6.2L3 16" />
    <path d="M3 21v-5h5" />
  </>,
);

export const IconQr = svg(
  <>
    <rect x="3" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="3" width="7" height="7" rx="1.5" />
    <rect x="3" y="14" width="7" height="7" rx="1.5" />
    <path d="M14 14h3v3h-3zM20 20h1M17 21v-1M21 14v3" />
  </>,
);

export const IconUsers = svg(
  <>
    <path d="M16 20v-1.5a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4V20" />
    <circle cx="9" cy="7" r="3.4" />
    <path d="M22 20v-1.5a4 4 0 0 0-3-3.87M16.5 3.6a4 4 0 0 1 0 6.8" />
  </>,
);

export const IconPhone = svg(
  <path d="M21.5 16.9v2.6a2 2 0 0 1-2.2 2 19.6 19.6 0 0 1-8.5-3 19.3 19.3 0 0 1-6-6 19.6 19.6 0 0 1-3-8.6A2 2 0 0 1 3.8 1.7h2.6a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L7.5 9.5a16 16 0 0 0 6 6l1.2-1.1a2 2 0 0 1 2.1-.5c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.9 2.3Z" />,
);

export const IconRuler = svg(
  <>
    <path d="M3.5 15.5 15.5 3.5a2 2 0 0 1 2.8 0l2.2 2.2a2 2 0 0 1 0 2.8L8.5 20.5a2 2 0 0 1-2.8 0l-2.2-2.2a2 2 0 0 1 0-2.8Z" />
    <path d="m8 11 2 2M11 8l2 2M14 5l2 2M5 14l2 2" />
  </>,
);

export const IconPin = svg(
  <>
    <path d="M20 10.5c0 5.5-8 12-8 12s-8-6.5-8-12a8 8 0 0 1 16 0Z" />
    <circle cx="12" cy="10.2" r="2.8" />
  </>,
);

export const IconClock = svg(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 6.8V12l3.4 2" />
  </>,
);

export const IconSparkle = svg(
  <>
    <path d="M12 2.5 14 9l6.5 2-6.5 2-2 6.5-2-6.5L3.5 11 10 9l2-6.5Z" />
    <path d="M19 3v3M20.5 4.5h-3" />
  </>,
);

export const IconBuilding = svg(
  <>
    <rect x="4" y="2.5" width="16" height="19" rx="2" />
    <path d="M9 7h.01M15 7h.01M9 11.5h.01M15 11.5h.01M9 16h.01M15 16h.01" />
    <path d="M10 21.5v-2.8h4v2.8" />
  </>,
);

export const IconMessage = svg(
  <path d="M20.5 11.4a8 8 0 0 1-8.6 8 9 9 0 0 1-3.8-.9L3.5 20l1.5-4.4a8 8 0 0 1-.9-3.7 8 8 0 0 1 8-8.4 8 8 0 0 1 8.4 8Z" />,
);

export const IconTag = svg(
  <>
    <path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0l-7.2-7.2A2 2 0 0 1 2.8 12V4.8A2 2 0 0 1 4.8 2.8H12a2 2 0 0 1 1.4.6l7.2 7.2a2 2 0 0 1 0 2.8Z" />
    <path d="M7.5 7.5h.01" />
  </>,
);

export const IconList = svg(<path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01" />);
export const IconLayers = svg(
  <>
    <path d="m12 2.7 9 4.8-9 4.8-9-4.8 9-4.8Z" />
    <path d="m3 12.5 9 4.8 9-4.8M3 17l9 4.8 9-4.8" />
  </>,
);
export const IconZap = svg(<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" />);
export const IconDatabase = svg(
  <>
    <ellipse cx="12" cy="5.5" rx="8" ry="3.2" />
    <path d="M4 5.5v13c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2v-13" />
    <path d="M4 12c0 1.8 3.6 3.2 8 3.2s8-1.4 8-3.2" />
  </>,
);
export const IconInbox = svg(
  <>
    <path d="M21 12h-5l-2 3h-4l-2-3H3" />
    <path d="M5.4 4.9 3 12v6a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-6l-2.4-7.1A2 2 0 0 0 16.7 3.5H7.3a2 2 0 0 0-1.9 1.4Z" />
  </>,
);
export const IconPower = svg(
  <>
    <path d="M12 3v9" />
    <path d="M18.4 6.6a9 9 0 1 1-12.8 0" />
  </>,
);

export const IconTrash = svg(
  <>
    <path d="M4 7h16" />
    <path d="M9 7V4.5a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 4.5V7" />
    <path d="M6.5 7 7.4 19.5A2 2 0 0 0 9.4 21.3h5.2a2 2 0 0 0 2-1.8L17.5 7" />
    <path d="M10.3 11v6.3M13.7 11v6.3" />
  </>,
);

export const IconMove = svg(
  <>
    <path d="M8 4 4.3 7.7 8 11.4" />
    <path d="M4.3 7.7H14a5 5 0 0 1 5 5v1" />
    <path d="M16 20l3.7-3.7L16 12.6" />
    <path d="M19.7 16.3H10a5 5 0 0 1-5-5v-1" />
  </>,
);
