import { Routes, Route, NavLink, Navigate } from "react-router-dom";
import TasksPage from "./pages/TasksPage";
import NewTaskPage from "./pages/NewTaskPage";
import MonitorPage from "./pages/MonitorPage";
import GitPage from "./pages/GitPage";

const tabs = [
  {
    to: "/tasks",
    label: "Tasks",
    icon: (
      <svg
        className="w-6 h-6"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"
        />
      </svg>
    ),
  },
  {
    to: "/new",
    label: "New",
    icon: (
      <svg
        className="w-6 h-6"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M12 4v16m8-8H4"
        />
      </svg>
    ),
  },
  {
    to: "/monitor",
    label: "Monitor",
    icon: (
      <svg
        className="w-6 h-6"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M13 10V3L4 14h7v7l9-11h-7z"
        />
      </svg>
    ),
  },
  {
    to: "/git",
    label: "Git",
    icon: (
      <svg
        className="w-6 h-6"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M6 3v12M18 9a3 3 0 100-6 3 3 0 000 6zm0 0v3a3 3 0 01-3 3H9m-3 0a3 3 0 100 6 3 3 0 000-6z"
        />
      </svg>
    ),
  },
];

export default function App() {
  return (
    <div className="flex flex-col h-screen bg-gray-950 text-gray-100 min-w-[375px]">
      {/* Main content area */}
      <main className="flex-1 overflow-y-auto pb-20">
        <Routes>
          <Route path="/" element={<Navigate to="/tasks" replace />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route path="/new" element={<NewTaskPage />} />
          <Route path="/monitor" element={<MonitorPage />} />
          <Route path="/git" element={<GitPage />} />
        </Routes>
      </main>

      {/* Bottom tab bar */}
      <nav className="fixed bottom-0 left-0 right-0 bg-gray-900 border-t border-gray-800 safe-area-pb">
        <div className="flex justify-around items-center max-w-lg mx-auto">
          {tabs.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                `flex flex-col items-center justify-center min-w-[44px] min-h-[44px] py-2 px-3 transition-colors ${
                  isActive
                    ? "text-violet-400"
                    : "text-gray-500 hover:text-gray-300"
                }`
              }
            >
              {tab.icon}
              <span className="text-xs mt-1">{tab.label}</span>
            </NavLink>
          ))}
        </div>
      </nav>
    </div>
  );
}
