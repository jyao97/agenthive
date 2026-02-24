import { useState, useEffect } from "react";
import { Routes, Route, NavLink, Navigate, useLocation, useNavigate } from "react-router-dom";
import ProjectsPage from "./pages/ProjectsPage";
import TrashPage from "./pages/TrashPage";
import ProjectDetailPage from "./pages/ProjectDetailPage";
import AgentsPage from "./pages/AgentsPage";
import AgentChatPage from "./pages/AgentChatPage";
import TasksPage from "./pages/TasksPage";
import NewPage from "./pages/NewPage";
import MonitorPage from "./pages/MonitorPage";
import GitPage from "./pages/GitPage";
import LoginPage from "./pages/LoginPage";
import useTheme from "./hooks/useTheme";
import { authCheck, clearAuthToken, fetchUnreadCount, getAuthToken } from "./lib/api";

const tabs = [
  {
    to: "/projects",
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

  useEffect(() => {
    const token = getAuthToken();
    if (!token) {
      // No token — check if password is even set (might be first-time)
      authCheck()
        .then((r) => {
          if (r.needs_setup) {
            // No password set yet — redirect to login for setup
            navigate("/login", { replace: true });
          } else {
            // Password set but no token — must login
            navigate("/login", { replace: true });
          }
        })
        .catch(() => {
          // Server down? let through — API calls will fail with their own errors
          setAuthed(true);
        })
        .finally(() => setChecked(true));
    } else {
      // Has token — verify it's still valid
      authCheck()
        .then((r) => {
          if (r.authenticated) {
            setAuthed(true);
          } else {
            // Token expired or invalid — clear and redirect to login
            clearAuthToken();
            navigate("/login", { replace: true });
          }
        })
        .catch(() => {
          // Server down? let through — API calls will fail with their own errors
          setAuthed(true);
        })
        .finally(() => setChecked(true));
    }
  }, [navigate]);

  if (!checked) {
    return (
      <div className="flex items-center justify-center h-dvh bg-page">
        <div className="animate-pulse text-dim">Loading...</div>
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

  useEffect(() => {
    // Only poll unread when not on login page and has a token
    if (location.pathname === "/login" || !getAuthToken()) return;
    const poll = () => fetchUnreadCount().then((r) => setUnread(r.unread)).catch(() => {});
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, [location.pathname]);

  return (
    <div className="flex flex-col h-dvh bg-page text-heading min-w-[320px] overflow-x-hidden">
      {/* Main content area */}
      <main className="flex-1 min-h-0 overflow-hidden">
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/*"
            element={
              <AuthGuard>
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
              </AuthGuard>
            }
          />
        </Routes>
      </main>

      {/* Bottom tab bar — completely unmounted on chat page (has its own header + back button) */}
      {!hideNav && (
        <nav className="fixed bottom-0 left-0 right-0 bg-surface border-t border-divider safe-area-pb z-40">
          <div className="grid grid-cols-5 items-center max-w-lg mx-auto">
            {tabs.map((tab) =>
              tab.isCenter ? (
                <NavLink
                  key={tab.to}
                  to={tab.to}
                  className={({ isActive }) =>
                    `flex items-center justify-center mx-auto -mt-5 w-14 h-14 rounded-full transition-colors shadow-lg shadow-cyan-500/20 ${
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
                  className={({ isActive }) =>
                    `relative flex flex-col items-center justify-center min-h-[44px] py-2 transition-colors ${
                      isActive
                        ? "text-cyan-400"
                        : "text-dim hover:text-body"
                    }`
                  }
                >
                  {tab.icon}
                  <span className="text-xs mt-1">{tab.label}</span>
                  {tab.key === "agents" && unread > 0 && (
                    <span className="absolute top-1.5 left-[calc(50%+6px)] inline-flex items-center justify-center min-w-[16px] h-[16px] px-1 rounded-full bg-red-500 text-white text-[10px] font-bold leading-none">
                      {unread > 99 ? "99+" : unread}
                    </span>
                  )}
                </NavLink>
              )
            )}
          </div>
        </nav>
      )}

    </div>
  );
}
