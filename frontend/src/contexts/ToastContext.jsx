import { createContext, useContext, useState, useCallback, useEffect, useRef } from "react";

const ToastContext = createContext(null);

function ToastItem({ toast, onDismiss }) {
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), toast.duration || 4000);
    return () => clearTimeout(timer);
  }, [toast.id, toast.duration, onDismiss]);

  const styles = {
    success: "bg-green-600/90 border-green-500",
    error: "bg-red-600/90 border-red-500",
    warning: "bg-amber-600/90 border-amber-500",
    info: "bg-cyan-600/90 border-cyan-500",
  };
  const bg = styles[toast.type] || styles.info;

  const icons = {
    success: <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />,
    error: <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />,
    warning: <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />,
    info: <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />,
  };

  return (
    <div className={`${bg} border rounded-lg px-4 py-3 text-sm text-white shadow-lg backdrop-blur-sm animate-slide-in`}>
      <div className="flex items-start gap-2">
        <span className="shrink-0 mt-0.5">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            {icons[toast.type] || icons.info}
          </svg>
        </span>
        <span className="leading-snug flex-1">{toast.message}</span>
        <button type="button" onClick={() => onDismiss(toast.id)} className="shrink-0 text-white/60 hover:text-white text-xs font-bold ml-1">&times;</button>
      </div>
    </div>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);

  const addToast = useCallback((message, type = "info", duration) => {
    const id = ++idRef.current;
    setToasts((prev) => [...prev, { id, message, type, duration }]);
    return id;
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback((message, type = "info", duration) => addToast(message, type, duration), [addToast]);
  toast.success = (msg, duration) => addToast(msg, "success", duration);
  toast.error = (msg, duration) => addToast(msg, "error", duration || 6000);
  toast.warning = (msg, duration) => addToast(msg, "warning", duration);
  toast.info = (msg, duration) => addToast(msg, "info", duration);

  return (
    <ToastContext.Provider value={toast}>
      {children}
      {toasts.length > 0 && (
        <div className="fixed right-4 left-4 sm:left-auto sm:w-96 z-[9999] space-y-2 pointer-events-none safe-area-toast">
          {toasts.map((t) => (
            <div key={t.id} className="pointer-events-auto">
              <ToastItem toast={t} onDismiss={dismissToast} />
            </div>
          ))}
        </div>
      )}
      <style>{`
        @keyframes slide-in {
          from { opacity: 0; transform: translateY(-8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-slide-in { animation: slide-in 0.2s ease-out; }
      `}</style>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
