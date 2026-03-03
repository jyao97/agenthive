import { useState, useEffect } from "react";

/**
 * Returns true when the page is visible, false when hidden/backgrounded.
 * Use to pause polling when the user isn't looking.
 */
export default function usePageVisible() {
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");

  useEffect(() => {
    const handler = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, []);

  return visible;
}
