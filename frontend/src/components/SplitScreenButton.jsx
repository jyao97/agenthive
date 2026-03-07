import { useNavigate, useLocation } from "react-router-dom";
import { useCallback } from "react";
import DraggableFab from "./DraggableFab";

const defaultPos = () => ({
  x: window.innerWidth - 64,
  y: window.innerHeight - 80,
});

export default function SplitScreenButton() {
  const navigate = useNavigate();
  const location = useLocation();

  const handleClick = useCallback(() => {
    navigate("/split", { state: { initialPath: location.pathname } });
  }, [navigate, location.pathname]);

  // Hide on split screen page itself and on login
  if (location.pathname === "/split" || location.pathname === "/login") return null;

  return (
    <DraggableFab
      storageKey="ah:fab-pos-split-enter"
      defaultPosition={defaultPos}
      onClick={handleClick}
      className="w-11 h-11 flex items-center justify-center rounded-full bg-surface shadow-lg border border-edge text-dim hover:text-cyan-400 hover:border-cyan-500/50 transition-all hover:shadow-cyan-500/10"
    >
      <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
        <rect x="2" y="3" width="9" height="18" rx="2" />
        <rect x="13" y="3" width="9" height="18" rx="2" />
      </svg>
    </DraggableFab>
  );
}
