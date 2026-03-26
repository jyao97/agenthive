/**
 * Robot SVG icon with state-based coloring.
 * state: "idle" | "running" | "error" | "planning" | "completed"
 */
const STATE_COLORS = {
  idle: "text-dim",
  running: "text-blue-400",
  error: "text-red-400",
  planning: "text-amber-400",
  completed: "text-green-400",
};

export default function BotIcon({ state = "idle", className = "w-8 h-8" }) {
  const color = STATE_COLORS[state] || STATE_COLORS.idle;
  return (
    <svg
      className={`${className} ${color}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {/* Head */}
      <rect x="4" y="6" width="16" height="12" rx="2" />
      {/* Eyes */}
      <circle cx="9" cy="12" r="1.5" fill="currentColor" stroke="none" />
      <circle cx="15" cy="12" r="1.5" fill="currentColor" stroke="none" />
      {/* Antenna */}
      <line x1="12" y1="6" x2="12" y2="3" />
      <circle cx="12" cy="2.5" r="1" fill="currentColor" stroke="none" />
      {/* Legs */}
      <line x1="8" y1="18" x2="8" y2="21" />
      <line x1="16" y1="18" x2="16" y2="21" />
    </svg>
  );
}
