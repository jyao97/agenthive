import { STATUS_COLORS } from "../lib/constants";

export default function StatusBadge({ status }) {
  const bg = STATUS_COLORS[status] || "bg-gray-500";
  const label = status === "PLAN_REVIEW" ? "REVIEW" : status;
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs font-medium text-white px-2 py-0.5 rounded-full ${bg}`}
    >
      {(status === "EXECUTING" || status === "PLANNING" || status === "SYNCING") && (
        <span className="relative flex h-2 w-2">
          <span className={`animate-ping absolute inline-flex h-full w-full rounded-full ${bg} opacity-75`} />
          <span className={`relative inline-flex rounded-full h-2 w-2 ${bg}`} />
        </span>
      )}
      {label}
    </span>
  );
}
