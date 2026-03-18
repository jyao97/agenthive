import { useState, useCallback, useEffect, useMemo, useRef, lazy, Suspense } from "react";
import {
  useNavigate, useLocation, MemoryRouter, Routes, Route, Navigate,
  useNavigate as usePaneNavigate,
  useLocation as usePaneLocation,
  UNSAFE_LocationContext, UNSAFE_NavigationContext, UNSAFE_RouteContext,
} from "react-router";
import useTheme from "../hooks/useTheme";
import DraggableFab from "../components/DraggableFab";
import BottomNavBar from "../components/BottomNavBar";

// Reset parent router context so MemoryRouter can be nested inside BrowserRouter.
const ROUTE_CTX_DEFAULT = { outlet: null, matches: [], isDataRoute: false };
function RouterIsolator({ children }) {
  return (
    <UNSAFE_LocationContext.Provider value={null}>
      <UNSAFE_NavigationContext.Provider value={null}>
        <UNSAFE_RouteContext.Provider value={ROUTE_CTX_DEFAULT}>
          {children}
        </UNSAFE_RouteContext.Provider>
      </UNSAFE_NavigationContext.Provider>
    </UNSAFE_LocationContext.Provider>
  );
}

// Lazy-load all page components (same chunks as App.jsx)
const ProjectsPage = lazy(() => import("./ProjectsPage"));
const TrashPage = lazy(() => import("./TrashPage"));
const ProjectDetailPage = lazy(() => import("./ProjectDetailPage"));
const AgentsPage = lazy(() => import("./AgentsPage"));
const AgentChatPage = lazy(() => import("./AgentChatPage"));
const TasksPage = lazy(() => import("./TasksPage"));
const TaskDetailPage = lazy(() => import("./TaskDetailPage"));
const MonitorPage = lazy(() => import("./MonitorPage"));
const NewPage = lazy(() => import("./NewPage"));
const GitPage = lazy(() => import("./GitPage"));

// --- Layout definitions ---

const LAYOUTS = [
  {
    key: "2col",
    label: "2 Columns",
    count: 2,
    gridClass: "grid-cols-2",
    icon: (
      <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <rect x="2" y="3" width="9" height="18" rx="1" />
        <rect x="13" y="3" width="9" height="18" rx="1" />
      </svg>
    ),
  },
  {
    key: "2row",
    label: "2 Rows",
    count: 2,
    gridClass: "grid-cols-1 grid-rows-2",
    icon: (
      <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <rect x="3" y="2" width="18" height="9" rx="1" />
        <rect x="3" y="13" width="18" height="9" rx="1" />
      </svg>
    ),
  },
  {
    key: "3col",
    label: "3 Columns",
    count: 3,
    gridClass: "grid-cols-3",
    icon: (
      <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <rect x="1" y="3" width="6" height="18" rx="1" />
        <rect x="9" y="3" width="6" height="18" rx="1" />
        <rect x="17" y="3" width="6" height="18" rx="1" />
      </svg>
    ),
  },
  {
    key: "2x2",
    label: "2x2 Grid",
    count: 4,
    gridClass: "grid-cols-2 grid-rows-2",
    icon: (
      <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <rect x="2" y="2" width="9" height="9" rx="1" />
        <rect x="13" y="2" width="9" height="9" rx="1" />
        <rect x="2" y="13" width="9" height="9" rx="1" />
        <rect x="13" y="13" width="9" height="9" rx="1" />
      </svg>
    ),
  },
];


// --- PaneShell: mini-app rendered inside each MemoryRouter pane ---

function PaneShell({ theme, onToggleTheme, onPathChange }) {
  const location = usePaneLocation();
  const paneNav = usePaneNavigate();
  const themeProps = { theme, onToggleTheme };

  // Report path changes back to parent for persistence
  useEffect(() => {
    if (onPathChange) onPathChange(location.pathname);
  }, [location.pathname, onPathChange]);

  // Navigate to another agent within this pane's MemoryRouter
  const onNavigateAgent = useCallback((agentId) => {
    paneNav(`/agents/${agentId}`);
  }, [paneNav]);

  const onCloseChat = useCallback(() => {
    paneNav("/agents");
  }, [paneNav]);

  // Hide pane nav on detail pages (same logic as main App)
  const hideNav =
    location.pathname.match(/^\/agents\/[^/]+$/) ||
    location.pathname.match(/^\/tasks\/[^/]+$/);

  return (
    <div className="flex flex-col h-full bg-page text-heading overflow-hidden">
      <main className="flex-1 min-h-0 overflow-hidden">
        <Suspense fallback={<div className="flex items-center justify-center h-full text-dim text-sm animate-pulse">Loading...</div>}>
          <Routes>
            <Route path="/" element={<Navigate to="/agents" replace />} />
            <Route path="/projects" element={<ProjectsPage {...themeProps} />} />
            <Route path="/projects/trash" element={<TrashPage {...themeProps} />} />
            <Route path="/projects/:name" element={<ProjectDetailPage {...themeProps} />} />
            <Route path="/agents" element={<AgentsPage {...themeProps} />} />
            <Route path="/agents/:id" element={<AgentChatPage {...themeProps} embedded onClose={onCloseChat} onNavigateAgent={onNavigateAgent} />} />
            <Route path="/tasks" element={<TasksPage {...themeProps} />} />
            <Route path="/tasks/:id" element={<TaskDetailPage {...themeProps} />} />
            <Route path="/new" element={<NewPage {...themeProps} />} />
            <Route path="/monitor" element={<MonitorPage {...themeProps} />} />
            <Route path="/git" element={<GitPage {...themeProps} />} />
          </Routes>
        </Suspense>
      </main>

      {/* Pane bottom nav — reuses shared BottomNavBar component */}
      {!hideNav && (
        <BottomNavBar className="shrink-0 flex justify-center px-3 pb-1.5 -mt-1" />
      )}
    </div>
  );
}

// --- Pane ID generator ---

let _nextId = 1;
function makePaneId() {
  return `pane-${_nextId++}`;
}

// --- Main SplitScreenPage ---

function useIsWide() {
  const [wide, setWide] = useState(() => window.innerWidth >= 1024);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const handler = (e) => setWide(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return wide;
}

export default function SplitScreenPage() {
  const navigate = useNavigate();
  const { state } = useLocation();
  const { theme, toggle } = useTheme();
  const isWide = useIsWide();

  const initialPath = state?.initialPath || "/agents";

  // Small screens: only 2row. Large screens: all layouts.
  const availableLayouts = isWide ? LAYOUTS : LAYOUTS.filter((l) => l.key === "2row");

  const [layout, setLayout] = useState(() => {
    const saved = localStorage.getItem("ah:split-layout");
    if (!isWide) return "2row";
    return saved || "2col";
  });

  // Force 2row on small screens
  const effectiveLayout = isWide ? layout : "2row";
  const layoutDef = LAYOUTS.find((l) => l.key === effectiveLayout) || LAYOUTS.find((l) => l.key === "2row");

  const [panes, setPanes] = useState(() => {
    // Restore saved pane paths from localStorage
    try {
      const saved = JSON.parse(localStorage.getItem("ah:split-panes") || "null");
      if (saved && Array.isArray(saved) && saved.length === layoutDef.count) {
        return saved.map((path) => ({ id: makePaneId(), path: path || initialPath }));
      }
    } catch { /* ignore */ }
    return Array.from({ length: layoutDef.count }, () => ({
      id: makePaneId(),
      path: initialPath,
    }));
  });

  // Persist pane paths when they change
  const panePathsRef = useRef(panes.map((p) => p.path));
  const handlePanePathChange = useCallback((index, newPath) => {
    panePathsRef.current = [...panePathsRef.current];
    panePathsRef.current[index] = newPath;
    localStorage.setItem("ah:split-panes", JSON.stringify(panePathsRef.current));
  }, []);

  // Adjust pane count when screen size changes
  useEffect(() => {
    setPanes((prev) => {
      if (prev.length === layoutDef.count) return prev;
      let next;
      if (prev.length < layoutDef.count) {
        next = [
          ...prev,
          ...Array.from({ length: layoutDef.count - prev.length }, () => ({
            id: makePaneId(),
            path: initialPath,
          })),
        ];
      } else {
        next = prev.slice(0, layoutDef.count);
      }
      panePathsRef.current = next.map((p) => p.path);
      localStorage.setItem("ah:split-panes", JSON.stringify(panePathsRef.current));
      return next;
    });
  }, [layoutDef.count, initialPath]);

  const handleLayoutChange = useCallback(
    (newKey) => {
      const newDef = LAYOUTS.find((l) => l.key === newKey) || LAYOUTS[0];
      setLayout(newKey);
      localStorage.setItem("ah:split-layout", newKey);
      setPanes((prev) => {
        if (prev.length === newDef.count) return prev;
        let next;
        if (prev.length < newDef.count) {
          next = [
            ...prev,
            ...Array.from({ length: newDef.count - prev.length }, () => ({
              id: makePaneId(),
              path: initialPath,
            })),
          ];
        } else {
          next = prev.slice(0, newDef.count);
        }
        panePathsRef.current = next.map((p) => p.path);
        localStorage.setItem("ah:split-panes", JSON.stringify(panePathsRef.current));
        return next;
      });
    },
    [initialPath]
  );

  const handleExit = useCallback(() => {
    // Try going back; if no history (fresh app load), navigate to a real page
    const saved = localStorage.getItem("ah:last-route");
    if (window.history.length > 1) {
      navigate(-1);
    } else {
      navigate(saved || "/agents", { replace: true });
    }
  }, [navigate]);
  const handleForceExit = useCallback(() => {
    // Long-press: always go to /projects regardless of history
    localStorage.removeItem("ah:split-panes");
    navigate("/projects", { replace: true });
  }, [navigate]);
  const exitBtnDefault = useMemo(() => () => ({
    x: window.innerWidth - 64,
    y: window.innerHeight - 140,
  }), []);

  return (
    <div className="flex flex-col h-screen bg-surface">
      {/* Top toolbar — desktop only */}
      {isWide && (
        <div className="shrink-0 flex items-center gap-2 px-3 py-1 bg-surface/80 border-b border-divider">
          <button
            type="button"
            onClick={handleExit}
            className="flex items-center gap-0.5 text-xs text-label hover:text-heading transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
            </svg>
            Exit
          </button>

          <div className="h-4 w-px bg-divider" />
          <div className="flex gap-0.5">
            {availableLayouts.map((l) => (
              <button
                key={l.key}
                type="button"
                onClick={() => handleLayoutChange(l.key)}
                title={l.label}
                className={`p-1 rounded-md transition-colors ${
                  effectiveLayout === l.key
                    ? "bg-cyan-500/20 text-cyan-400"
                    : "text-dim hover:text-body hover:bg-input"
                }`}
              >
                {l.icon}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={toggle}
            title={theme === "dark" ? "Light mode" : "Dark mode"}
            className="ml-auto w-6 h-6 flex items-center justify-center rounded-md text-dim hover:text-heading hover:bg-input transition-colors"
          >
            {theme === "dark" ? (
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
              </svg>
            ) : (
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
              </svg>
            )}
          </button>
        </div>
      )}

      {/* Mobile floating exit button — draggable */}
      {!isWide && (
        <DraggableFab
          storageKey="ah:fab-pos-split-v3"
          defaultPosition={exitBtnDefault}
          onClick={handleExit}
          onLongPress={handleForceExit}
          className="w-8 h-8 flex items-center justify-center rounded-full bg-surface shadow-lg border border-edge text-dim hover:text-heading transition-colors"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </DraggableFab>
      )}

      {/* Panes grid */}
      <div className={`flex-1 grid ${layoutDef.gridClass} gap-1.5 p-1.5 min-h-0 ${!isWide ? "safe-area-pt" : ""}`}>
        {panes.map((pane, idx) => (
          <div
            key={pane.id}
            className="split-pane relative overflow-hidden min-h-0 min-w-0 rounded-xl border border-divider bg-page"
          >
            <RouterIsolator>
              <MemoryRouter initialEntries={[pane.path]}>
                <PaneShell theme={theme} onToggleTheme={toggle} onPathChange={(p) => handlePanePathChange(idx, p)} />
              </MemoryRouter>
            </RouterIsolator>
          </div>
        ))}
      </div>
    </div>
  );
}
