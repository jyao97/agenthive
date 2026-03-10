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
  { value: "claude-opus-4-6", label: "Opus" },
  { value: "claude-sonnet-4-6", label: "Sonnet" },
  { value: "claude-haiku-4-5-20251001", label: "Haiku" },
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

// ---- Timing constants (ms) ----

/** Polling interval when agent is active (EXECUTING/SYNCING). */
export const POLL_ACTIVE_INTERVAL = 3000;

/** Polling interval when agent is idle. */
export const POLL_IDLE_INTERVAL = 10000;

/** Auto-clear streaming content after this much inactivity. */
export const STREAM_TIMEOUT = 6000;

/** Duration to show "Copied" toast. */
export const COPY_TOAST_DURATION = 1500;

/** Duration to show transient error toasts. */
export const ERROR_TOAST_DURATION = 4000;

/** Duration to show success/info toasts. */
export const TOAST_DURATION = 3000;

/** Escape key cooldown to match backend rate limit. */
export const ESCAPE_COOLDOWN = 2500;

/** Long-press duration for touch actions. */
export const LONG_PRESS_DELAY = 500;

/** Double-tap detection window. */
export const DOUBLE_TAP_WINDOW = 350;

/** Scroll-save debounce delay. */
export const SCROLL_SAVE_DEBOUNCE = 200;

/** Lightbox swipe navigation threshold (px). */
export const SWIPE_THRESHOLD = 80;

/** Lightbox dismiss swipe threshold (px). */
export const DISMISS_THRESHOLD = 100;

/** Lightbox double-tap detection window (ms). */
export const LIGHTBOX_DOUBLE_TAP_WINDOW = 300;

/** Lightbox double-tap max distance (px). */
export const LIGHTBOX_DOUBLE_TAP_DIST = 30;

/** Max zoom scale for lightbox. */
export const MAX_ZOOM_SCALE = 5;

// ---- Task v2 ----

export const TASK_STATUS_COLORS = {
  INBOX: "bg-blue-500",
  PLANNING: "bg-violet-500",
  PENDING: "bg-gray-500",
  EXECUTING: "bg-cyan-500 animate-pulse",
  REVIEW: "bg-amber-500",
  MERGING: "bg-purple-500",
  CONFLICT: "bg-red-500",
  COMPLETE: "bg-green-500",
  REJECTED: "bg-orange-500",
  CANCELLED: "bg-gray-600",
  FAILED: "bg-red-500",
  TIMEOUT: "bg-orange-500",
};

export const TASK_STATUS_TEXT_COLORS = {
  INBOX: "text-blue-400",
  PLANNING: "text-violet-400",
  PENDING: "text-dim",
  EXECUTING: "text-cyan-400",
  REVIEW: "text-amber-400",
  MERGING: "text-purple-400",
  CONFLICT: "text-red-400",
  COMPLETE: "text-green-400",
  REJECTED: "text-orange-400",
  CANCELLED: "text-faint",
  FAILED: "text-red-400",
  TIMEOUT: "text-orange-400",
};

export const TASK_PERSPECTIVE_TABS = [
  { key: "INBOX", label: "Inbox" },
  { key: "PLANNING", label: "Planning" },
  { key: "EXECUTING", label: "Executing" },
  { key: "REVIEW", label: "Review" },
  { key: "DONE", label: "Done" },
];

// ---- Agent helpers ----

/** Map agent status to BotIcon visual state. */
export function agentBotState(status) {
  if (status === "EXECUTING" || status === "SYNCING") return "running";
  if (status === "ERROR") return "error";
  if (status === "IDLE") return "completed";
  if (status === "STOPPED") return "idle";
  return "idle";
}

/** Check if system health object indicates all systems OK. */
export function isSystemHealthy(health) {
  return health && health.status === "ok" && health.db === "ok" && health.claude_cli === "ok";
}
