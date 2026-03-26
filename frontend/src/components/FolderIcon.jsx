/**
 * Folder SVG icon with state-based coloring.
 * state: "idle" | "running" | "error" | "planning" | "completed"
 */
const STATE_COLORS = {
  idle: "text-dim",
  running: "text-blue-400",
  error: "text-red-400",
  planning: "text-amber-400",
  completed: "text-green-400",
};

export default function FolderIcon({ state = "idle", className = "w-8 h-8" }) {
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
      <path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
    </svg>
  );
}
