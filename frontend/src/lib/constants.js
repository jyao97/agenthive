export const STATUS_COLORS = {
  PENDING: "bg-gray-500",
  SYNCING: "bg-violet-500",
  EXECUTING: "bg-cyan-500",
  COMPLETED: "bg-green-500",
  FAILED: "bg-red-500",
  TIMEOUT: "bg-orange-500",
  CANCELLED: "bg-gray-600",
};

export const STATUS_TEXT_COLORS = {
  PENDING: "text-dim",
  SYNCING: "text-violet-400",
  EXECUTING: "text-cyan-400",
  COMPLETED: "text-green-400",
  FAILED: "text-red-400",
  TIMEOUT: "text-orange-400",
  CANCELLED: "text-faint",
};

export const AGENT_STATUS_COLORS = {
  STARTING: "bg-gray-500",
  IDLE: "bg-green-500",
  EXECUTING: "bg-cyan-500",
  SYNCING: "bg-violet-500",
  ERROR: "bg-red-500",
  STOPPED: "bg-gray-600",
};

export const AGENT_STATUS_TEXT_COLORS = {
  STARTING: "text-dim",
  IDLE: "text-green-400",
  EXECUTING: "text-cyan-400",
  SYNCING: "text-violet-400",
  ERROR: "text-red-400",
  STOPPED: "text-faint",
};

export const MODE_COLORS = {
  INTERVIEW: "bg-violet-500/20 text-violet-400 border border-violet-500/40",
  AUTO: "bg-green-500/20 text-green-400 border border-green-500/40",
};

export const AGENT_MODES = [
  { value: "AUTO", label: "Auto" },
];

export const MODEL_OPTIONS = [
  { value: "claude-opus-4-6", label: "Opus 4.6" },
  { value: "claude-sonnet-4-6", label: "Sonnet 4.6" },
  { value: "claude-haiku-4-5-20251001", label: "Haiku 4.5" },
];

/** Map full model ID to short display name. */
export function modelDisplayName(modelId) {
  if (!modelId) return null;
  const opt = MODEL_OPTIONS.find((m) => m.value === modelId);
  if (opt) return opt.label;
  // Fallback: strip "claude-" prefix and date suffixes
  return modelId
    .replace(/^claude-/, "")
    .replace(/-\d{8}$/, "")
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join(" ");
}

export const STATUS_TABS = [
  { key: "ALL", label: "All" },
  { key: "PENDING", label: "Pending" },
  { key: "SYNCING", label: "Syncing" },
  { key: "EXECUTING", label: "Executing" },
  { key: "COMPLETED", label: "Completed" },
  { key: "FAILED", label: "Failed" },
];

/** Deterministic color palette for project badges. */
const PROJECT_PALETTE = [
  "bg-cyan-500/15 text-cyan-600 dark:text-cyan-400",
  "bg-violet-500/15 text-violet-600 dark:text-violet-400",
  "bg-amber-500/15 text-amber-600 dark:text-amber-400",
  "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  "bg-rose-500/15 text-rose-600 dark:text-rose-400",
  "bg-sky-500/15 text-sky-600 dark:text-sky-400",
  "bg-orange-500/15 text-orange-600 dark:text-orange-400",
  "bg-indigo-500/15 text-indigo-600 dark:text-indigo-400",
];

export function projectBadgeColor(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = ((hash << 5) - hash + name.charCodeAt(i)) | 0;
  }
  return PROJECT_PALETTE[Math.abs(hash) % PROJECT_PALETTE.length];
}

export const POLL_INTERVAL = 5000;
