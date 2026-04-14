import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "katex/dist/katex.min.css";
import "./index.css";
import App from "./App.jsx";
import { registerSW } from "virtual:pwa-register";

// Register VitePWA service worker with autoUpdate.
// Precaches all static assets (JS/CSS/HTML with content hashes).
// Periodic check every hour ensures long-lived tabs pick up new deploys.
if ("serviceWorker" in navigator) {
  registerSW({
    onRegisteredSW(swUrl, registration) {
      if (!registration) return;
      // Check for SW updates every 30 minutes
      setInterval(() => { registration.update(); }, 30 * 60 * 1000);
    },
  });
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
