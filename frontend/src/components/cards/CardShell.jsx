import { memo } from "react";

/**
 * Shared card wrapper with pop-out expand effect.
 * All task cards should use this as their outer container.
 */
export default memo(function CardShell({ expanded, selecting, selected, className = "", children, ...props }) {
  return (
    <div
      data-card
      className={`w-full text-left rounded-2xl bg-surface overflow-hidden transform-gpu transition-[transform,box-shadow,ring-color,opacity] duration-400 ease-[cubic-bezier(0.22,1.15,0.36,1)] ${
        expanded && !selecting
          ? "shadow-lg scale-[1.02] ring-1 ring-cyan-500/20 z-10"
          : "shadow-card scale-100"
      } ${selecting && selected ? "ring-2 ring-cyan-500/50 brightness-[0.88]" : ""} ${className}`}
      {...props}
    >
      {children}
    </div>
  );
});

/** Padding class for the clickable content area inside a card. */
export function cardPadding(expanded, selecting) {
  return expanded && !selecting ? "py-5" : "py-[18px]";
}
