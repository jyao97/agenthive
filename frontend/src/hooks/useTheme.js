import { useState, useEffect, useCallback } from "react";

const STORAGE_KEY = "agenthive-theme";

function getSystemTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(resolved) {
  const root = document.documentElement;
  if (resolved === "dark") {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.setAttribute("content", resolved === "dark" ? "#030712" : "#ffffff");
  }
}

export default function useTheme() {
  const [theme, setTheme] = useState(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    // Migrate old "system" preference to actual system value
    if (!stored || stored === "system") return getSystemTheme();
    return stored;
  });

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((prev) => {
      const next = prev === "light" ? "dark" : "light";
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  return { theme, toggle };
}
