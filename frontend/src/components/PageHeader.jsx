import { useNavigate } from "react-router-dom";
import useHealthStatus from "../hooks/useHealthStatus";

const SunIcon = (
  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
  </svg>
);

const MoonIcon = (
  <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
  </svg>
);

export default function PageHeader({ title, theme, onToggleTheme, actions, children }) {
  const navigate = useNavigate();
  const health = useHealthStatus();

  const isHealthy = health && health.status === "ok" && health.db === "ok" && health.claude_cli === "ok";
  const chipCls = health === null
    ? "bg-gray-500/15 text-gray-400"
    : isHealthy
      ? "bg-green-500/15 text-green-500"
      : "bg-red-500/15 text-red-400";
  const dotColor = health === null ? "bg-gray-400" : isHealthy ? "bg-green-500" : "bg-red-500";
  const chipLabel = health === null ? "..." : isHealthy ? "OK" : "Error";

  return (
    <div className="shrink-0 bg-page border-b border-divider z-10">
      <div className="flex items-center gap-3 px-4 pb-2" style={{ paddingTop: "max(1rem, env(safe-area-inset-top, 1rem))" }}>
        <h1 className="text-xl font-bold text-heading flex-1 shrink-0">{title}</h1>
        {actions}
        <button
          type="button"
          onClick={() => navigate("/monitor")}
          title={health === null ? "Checking system health..." : isHealthy ? "System healthy" : "System issue detected"}
          className={`shrink-0 inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium transition-colors hover:opacity-80 ${chipCls}`}
        >
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${dotColor} ${!isHealthy && health !== null ? "animate-pulse" : ""}`} />
          {chipLabel}
        </button>
        {onToggleTheme && (
          <button
            type="button"
            onClick={onToggleTheme}
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            className="shrink-0 w-8 h-8 flex items-center justify-center rounded-lg text-dim hover:text-heading hover:bg-input transition-colors"
          >
            {theme === "dark" ? SunIcon : MoonIcon}
          </button>
        )}
      </div>
      {children}
    </div>
  );
}
