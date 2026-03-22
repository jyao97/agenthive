import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import App from "./App.jsx";

// Proactively unregister any stale precaching service workers from prior
// production builds.  In dev mode only push-handler.js should be registered
// (which has no fetch handler and doesn't cache).  VitePWA's generated SW
// from a prior `vite build` can serve stale assets indefinitely.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.getRegistrations().then((regs) => {
    for (const reg of regs) {
      // Keep push-handler.js (lightweight, no fetch caching)
      if (reg.active?.scriptURL?.endsWith("/push-handler.js")) continue;
      reg.unregister().then(() => {
        console.debug("[sw] unregistered stale SW:", reg.active?.scriptURL);
      });
    }
  }).catch(() => {});
  // Also clear any Workbox caches left behind
  if ("caches" in window) {
    caches.keys().then((keys) => {
      for (const k of keys) {
        if (k.startsWith("workbox-") || k.startsWith("vite-")) {
          caches.delete(k);
          console.debug("[sw] cleared stale cache:", k);
        }
      }
    }).catch(() => {});
  }
}

// Global error handlers — catch async/event-handler errors that React
// error boundaries cannot intercept.  Shows a raw DOM toast so it works
// even if React itself has crashed.
function showErrorToast(msg) {
  // Skip expected auth errors
  if (typeof msg === "string" && msg.includes("Not authenticated")) return;

  let container = document.getElementById("global-error-toast");
  if (!container) {
    container = document.createElement("div");
    container.id = "global-error-toast";
    Object.assign(container.style, {
      position: "fixed",
      bottom: "80px",
      left: "50%",
      transform: "translateX(-50%)",
      zIndex: "99999",
      maxWidth: "90vw",
      padding: "10px 18px",
      borderRadius: "10px",
      background: "#dc2626",
      color: "#fff",
      fontSize: "13px",
      fontFamily: "system-ui, sans-serif",
      boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
      pointerEvents: "none",
      opacity: "0",
      transition: "opacity 0.3s",
    });
    document.body.appendChild(container);
  }
  container.textContent = String(msg).slice(0, 200);
  container.style.opacity = "1";
  clearTimeout(container._timer);
  container._timer = setTimeout(() => {
    container.style.opacity = "0";
  }, 5000);
}

window.addEventListener("error", (e) => {
  showErrorToast(e.message || "Uncaught error");
});

window.addEventListener("unhandledrejection", (e) => {
  const msg = e.reason?.message || e.reason || "Unhandled promise rejection";
  showErrorToast(msg);
});

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>
);
