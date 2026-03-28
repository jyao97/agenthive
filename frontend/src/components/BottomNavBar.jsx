import { useRef } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import useLongPress from "../hooks/useLongPress";

// Shared tab definitions — single source of truth for both App.jsx and SplitScreenPage
export const navTabs = [
  {
    to: "/tasks",
    key: "tasks",
    label: "Inbox",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
      </svg>
    ),
  },
  {
    to: "/projects",
    key: "projects",
    label: "Projects",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
      </svg>
    ),
  },
  {
    to: "/new",
    label: "New",
    isCenter: true,
    icon: (
      <svg className="w-7 h-7" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
      </svg>
    ),
  },
  {
    to: "/agents",
    key: "agents",
    label: "Agents",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
      </svg>
    ),
  },
  {
    to: "/git",
    label: "Git",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z" />
      </svg>
    ),
  },
];

function CenterFab({ tab, compact }) {
  const navigate = useNavigate();
  const location = useLocation();
  const isActive = location.pathname === tab.to;
  const longPressedRef = useRef(false);

  const handlers = useLongPress(
    // Long press → navigate to the /new landing page (all creation options)
    () => {
      longPressedRef.current = true;
      if (navigator.vibrate) navigator.vibrate(30);
      navigate("/new", { replace: true });
    },
    // Normal tap → navigate to voice-first NewTaskPage (overlay)
    () => {
      navigate("/new/task", { state: { backgroundLocation: location } });
    },
    500,
  );

  return (
    <button
      type="button"
      {...handlers}
      className={`flex items-center justify-center mx-auto ${
        compact ? "-mt-2 w-9 h-9" : "-mt-4 w-13 h-13"
      } rounded-full transition-colors shadow-lg shadow-cyan-500/20 select-none touch-none ${
        isActive
          ? "bg-cyan-500 text-white"
          : "bg-cyan-600 text-white hover:bg-cyan-500"
      }`}
    >
      {compact ? <span className="[&_svg]:w-5 [&_svg]:h-5">{tab.icon}</span> : tab.icon}
    </button>
  );
}

/**
 * BottomNavBar — shared between App.jsx (main) and SplitScreenPage (panes).
 *
 * Props:
 *   badges        — optional { agents: number, projects: number }
 *   onDoubleTap   — optional (key, event) => void
 *   onProjectsTap — optional (event) => void  (custom Projects nav logic)
 *   className     — extra classes on the outer wrapper
 */
export default function BottomNavBar({ badges, onDoubleTap, onProjectsTap, className = "", compact = false }) {
  const location = useLocation();

  return (
    <div className={className}>
      <div
        className={`glass-bar-nav ${compact ? "nav-compact rounded-[20px]" : "rounded-[28px]"} grid grid-cols-5 items-center w-full`}
        style={{ maxWidth: compact ? "20rem" : "24rem" }}
      >
        {navTabs.map((tab) =>
          tab.isCenter ? (
            <CenterFab key={tab.to} tab={tab} compact={compact} />
          ) : (
            <NavLink
              key={tab.to}
              to={tab.to}
              replace
              onClick={
                (tab.key === "agents" || tab.key === "tasks") && onDoubleTap
                  ? (e) => onDoubleTap(tab.key, e)
                  : tab.key === "projects" && onProjectsTap
                    ? onProjectsTap
                    : undefined
              }
              className={({ isActive }) => {
                const active = tab.key === "projects" ? location.pathname.startsWith("/projects") : isActive;
                return `relative flex flex-col items-center justify-center ${
                  compact ? "min-h-[38px] py-1" : "min-h-[58px] py-2.5"
                } transition-colors ${
                  active ? "text-cyan-400" : "text-dim hover:text-body"
                }`;
              }}
            >
              {compact ? <span className="[&_svg]:w-[18px] [&_svg]:h-[18px]">{tab.icon}</span> : tab.icon}
              <span className={`${compact ? "text-[8px]" : "text-[10px]"} mt-0.5`}>{tab.label}</span>
              {tab.key === "agents" && badges?.agents > 0 && (
                <span className="absolute top-1.5 left-[calc(50%+6px)] inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-full bg-red-500 text-white text-[10px] font-bold leading-none">
                  {badges.agents > 99 ? "99+" : badges.agents}
                </span>
              )}
              {tab.key === "projects" && badges?.projects > 0 && (
                <span className="absolute top-1.5 left-[calc(50%+6px)] inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-full bg-amber-500 text-white text-[10px] font-bold leading-none">
                  {badges.projects}
                </span>
              )}
            </NavLink>
          )
        )}
      </div>
    </div>
  );
}
