import { useState, useEffect, lazy, Suspense } from "react";
import { Routes, Route, NavLink, Navigate, useLocation, useNavigate } from "react-router-dom";
import LoginPage from "./pages/LoginPage";
import ErrorBoundary from "./components/ErrorBoundary";

const ProjectsPage = lazy(() => import("./pages/ProjectsPage"));
const TrashPage = lazy(() => import("./pages/TrashPage"));
const ProjectDetailPage = lazy(() => import("./pages/ProjectDetailPage"));
const AgentsPage = lazy(() => import("./pages/AgentsPage"));
const AgentChatPage = lazy(() => import("./pages/AgentChatPage"));
const TasksPage = lazy(() => import("./pages/TasksPage"));
const NewPage = lazy(() => import("./pages/NewPage"));
const MonitorPage = lazy(() => import("./pages/MonitorPage"));
const GitPage = lazy(() => import("./pages/GitPage"));
import useTheme from "./hooks/useTheme";
import { authCheck, clearAuthToken, fetchUnreadCount, fetchClaudeMdPending, getAuthToken } from "./lib/api";
import { isPushSupported, setupPushNotifications } from "./lib/pushNotifications";
import useIdleLock from "./hooks/useIdleLock";
import usePageVisible from "./hooks/usePageVisible";

const tabs = [
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
    to: "/tasks",
    label: "Tasks",
    icon: (
      <svg className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4" />
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

function AuthGuard({ children }) {
  const navigate = useNavigate();
  const [checked, setChecked] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [serverDown, setServerDown] = useState(false);
  const [retrying, setRetrying] = useState(false);

  // Auto-lock after 30 min of inactivity (clears token + redirects to login)
  useIdleLock(navigate);

  // Listen for auth-expired events dispatched by api.js (graceful 401 handling)
  useEffect(() => {
    const handler = () => {
      clearAuthToken();
      navigate("/login", { replace: true });
    };
    window.addEventListener("auth-expired", handler);
    return () => window.removeEventListener("auth-expired", handler);
  }, [navigate]);

  // Attempt auth check with auto-retry for transient server-down (e.g. restart)
  const attemptAuth = (token) => {
    const doCheck = () =>
      authCheck()
        .then((r) => {
          if (!token) {
            navigate("/login", { replace: true });
          } else if (r.authenticated) {
            setAuthed(true);
            setServerDown(false);
            if (typeof Notification !== "undefined" && Notification.permission === "default") {
              Notification.requestPermission().catch(() => {});
            }
            if (isPushSupported()) {
              setupPushNotifications().catch(() => {});
            }
          } else {
            clearAuthToken();
            navigate("/login", { replace: true });
          }
          setChecked(true);
        })
        .catch(() => {
          setServerDown(true);
          setChecked(true);
        });
    doCheck();
  };

  useEffect(() => {
    attemptAuth(getAuthToken());
  }, [navigate]);

  // Auto-retry when server is down (polls every 2s for up to 60s)
  useEffect(() => {
    if (!serverDown) return;
    setRetrying(true);
    let attempts = 0;
    const interval = setInterval(async () => {
      attempts++;
      try {
        await authCheck();
        // Server is back — do a full reload so all hooks reinitialize cleanly
        clearInterval(interval);
        window.location.reload();
      } catch {
        if (attempts >= 30) {
          // 60s elapsed — stop auto-retry, keep manual button
          clearInterval(interval);
          setRetrying(false);
        }
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [serverDown]);

  if (!checked) {
    return (
      <div className="flex items-center justify-center h-dvh bg-page">
        <div className="animate-pulse text-dim">Loading...</div>
      </div>
    );
  }

  if (serverDown) {
    return (
      <div className="flex flex-col items-center justify-center h-dvh bg-page gap-4">
        {retrying ? (
          <p className="text-label text-sm animate-pulse">Reconnecting to server...</p>
        ) : (
          <p className="text-label text-sm">Unable to reach server</p>
        )}
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="px-4 py-2 rounded-lg bg-cyan-600 text-white text-sm hover:bg-cyan-500 transition-colors"
        >
          Retry
        </button>
      </div>
    );
  }

  return authed ? children : null;
}

export default function App() {
  const { theme, toggle } = useTheme();
  const themeProps = { theme, onToggleTheme: toggle };
  const location = useLocation();
  const hideNav = location.pathname.match(/^\/agents\/[^/]+$/) || location.pathname === "/login";
  const [unread, setUnread] = useState(0);
  const [claudeMdPending, setClaudeMdPending] = useState(0);
  const visible = usePageVisible();

  useEffect(() => {
    // Only poll unread when not on login page and has a token
    if (!visible || location.pathname === "/login" || !getAuthToken()) return;
    const poll = () => fetchUnreadCount().then((r) => setUnread(r.unread)).catch(() => {});
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, [location.pathname, visible]);

  useEffect(() => {
    if (!visible || location.pathname === "/login" || !getAuthToken()) return;
    const poll = () => fetchClaudeMdPending().then((r) => setClaudeMdPending(r.count || 0)).catch(() => {});
    poll();
    const id = setInterval(poll, 30000);
    return () => clearInterval(id);
  }, [location.pathname, visible]);

  // Safari iOS: after keyboard dismiss the visual viewport desyncs from
  // the layout viewport.  The ONLY thing that fixes it is an actual
  // scroll event.  body::after adds 1px so scrollBy has room to move.
  useEffect(() => {
    const timers = [];
    const microScroll = () => {
      timers.forEach(clearTimeout);
      timers.length = 0;
      // Fire immediately, then once more after a short delay as a safety net
      const doIt = () => {
        window.scrollTo({ top: 1, behavior: "instant" });
        window.scrollTo({ top: 0, behavior: "instant" });
      };
      doIt();
      timers.push(setTimeout(doIt, 50));
    };

    // Keyboard dismiss: input/textarea loses focus
    const onFocusOut = (e) => {
      if (e.target?.tagName === "TEXTAREA" || e.target?.tagName === "INPUT") {
        microScroll();
      }
    };

    // Tab resume
    const onVisibility = () => {
      if (document.visibilityState === "visible") microScroll();
    };

    document.addEventListener("focusout", onFocusOut);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("resize", microScroll);
    return () => {
      timers.forEach(clearTimeout);
      document.removeEventListener("focusout", onFocusOut);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("resize", microScroll);
    };
  }, []);

  return (
    <ErrorBoundary>
    <div className="flex flex-col h-screen bg-page text-heading min-w-[320px] overflow-x-hidden">
      {/* Main content area */}
      <main className="flex-1 min-h-0 overflow-hidden">
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/*"
            element={
              <AuthGuard>
                <ErrorBoundary>
                  <Suspense fallback={<div/>}>
                  <Routes>
                    <Route path="/" element={<Navigate to="/projects" replace />} />
                    <Route path="/projects" element={<ProjectsPage {...themeProps} />} />
                    <Route path="/projects/trash" element={<TrashPage {...themeProps} />} />
                    <Route path="/projects/:name" element={<ProjectDetailPage {...themeProps} />} />
                    <Route path="/agents" element={<AgentsPage {...themeProps} />} />
                    <Route path="/agents/:id" element={<AgentChatPage {...themeProps} />} />
                    <Route path="/tasks" element={<TasksPage {...themeProps} />} />
                    <Route path="/new" element={<NewPage {...themeProps} />} />
                    <Route path="/monitor" element={<MonitorPage {...themeProps} />} />
                    <Route path="/git" element={<GitPage {...themeProps} />} />
                  </Routes>
                  </Suspense>
                </ErrorBoundary>
              </AuthGuard>
            }
          />
        </Routes>
      </main>

      {/* Bottom tab bar — floating glass pill */}
      {!hideNav && (
        <nav className="fixed bottom-2 left-0 right-0 z-40 safe-area-pb-tight flex justify-center px-4">
          <div className="glass-bar-nav rounded-[28px] grid grid-cols-5 items-center w-full" style={{ maxWidth: "24rem" }}>
            {tabs.map((tab) =>
              tab.isCenter ? (
                <NavLink
                  key={tab.to}
                  to={tab.to}
                  replace
                  className={({ isActive }) =>
                    `flex items-center justify-center mx-auto -mt-4 w-13 h-13 rounded-full transition-colors shadow-lg shadow-cyan-500/20 ${
                      isActive
                        ? "bg-cyan-500 text-white"
                        : "bg-cyan-600 text-white hover:bg-cyan-500"
                    }`
                  }
                >
                  {tab.icon}
                </NavLink>
              ) : (
                <NavLink
                  key={tab.to}
                  to={tab.to}
                  replace
                  className={({ isActive }) => {
                    const active = tab.key === "projects" ? location.pathname.startsWith("/projects") : isActive;
                    return `relative flex flex-col items-center justify-center min-h-[58px] py-2.5 transition-colors ${
                      active
                        ? "text-cyan-400"
                        : "text-dim hover:text-body"
                    }`;
                  }}
                >
                  {tab.icon}
                  <span className="text-[10px] mt-0.5">{tab.label}</span>
                  {tab.key === "agents" && unread > 0 && (
                    <span className="absolute top-1.5 left-[calc(50%+6px)] inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-full bg-red-500 text-white text-[10px] font-bold leading-none">
                      {unread > 99 ? "99+" : unread}
                    </span>
                  )}
                  {tab.key === "projects" && claudeMdPending > 0 && (
                    <span className="absolute top-1.5 left-[calc(50%+6px)] inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-full bg-amber-500 text-white text-[10px] font-bold leading-none">
                      {claudeMdPending}
                    </span>
                  )}
                </NavLink>
              )
            )}
          </div>
        </nav>
      )}

    </div>
    </ErrorBoundary>
  );
}
