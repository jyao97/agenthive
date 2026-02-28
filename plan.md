# Plan: Fullscreen Image Viewer with Gesture Support

## Overview

Replace the simple lightbox in `FilePreview.jsx` with a full-featured `ImageLightbox` component supporting pinch-to-zoom, pan, double-tap zoom toggle, swipe-down dismiss, and multi-image navigation.

## Approach

**Pure React + touch events** — no external libraries. The gestures are well-scoped (pinch, pan, swipe, double-tap) and don't warrant adding a dependency.

## Files to Create/Modify

### 1. NEW: `frontend/src/components/ImageLightbox.jsx`

Standalone fullscreen image viewer component.

**Props:**
- `images` — array of `{ src, filename }` objects
- `initialIndex` — which image to show first
- `onClose` — callback to dismiss

**State:**
- `currentIndex` — active image in gallery
- `scale` — current zoom level (1 = fit-to-screen)
- `translate` — `{ x, y }` pan offset
- `isZoomed` — whether scale > 1 (for gesture routing)

**Gesture handling (all via `onTouchStart/Move/End` + `onPointerDown/Move/Up`):**

| Gesture | Detection | Action |
|---------|-----------|--------|
| Pinch-to-zoom | 2 touches, track distance delta | Scale image around pinch midpoint |
| Pan | 1 touch drag while zoomed | Translate image |
| Double-tap | 2 taps within 300ms, <20px apart | Toggle between scale=1 and scale=2 (centered on tap point) |
| Swipe down | 1 touch drag downward while NOT zoomed, dy > 100px | Dismiss (call onClose) |
| Swipe left/right | 1 touch horizontal drag while NOT zoomed, dx > 80px | Navigate prev/next image |

**Visual design:**
- `fixed inset-0 z-50 bg-black` fullscreen overlay
- Close button (X) top-right corner
- Image counter badge top-center ("2 / 5") when multiple images
- CSS `transition` on scale/translate for animated snap-back
- `touch-action: none` to prevent browser defaults
- `user-select: none` to prevent text selection

**Zoom behavior:**
- Min scale: 1 (fit-to-screen)
- Max scale: 5
- When scale resets to 1, translate resets to {0,0}
- Double-tap zooms to 2x centered on tap point, or back to 1x

### 2. MODIFY: `frontend/src/components/FilePreview.jsx`

**Changes to `ImagePreview`:**
- Remove inline lightbox markup
- Instead, when tapped, call a new `onOpenLightbox(index)` callback
- Requires the parent (`FileAttachments`) to collect all images and manage lightbox state

**Changes to `FileAttachments`:**
- Add `useState` for lightbox: `{ open, initialIndex }`
- Collect all image attachments into an array with `{ src, filename }`
- Pass `onOpenLightbox` to each `ImagePreview` with its index
- Render `<ImageLightbox>` when open, passing the full images array

### 3. MODIFY: `frontend/src/lib/formatters.jsx`

**Changes to inline markdown images:**
- Currently renders plain `<img>` tags that aren't tappable for lightbox
- Wrap inline images to also support opening the lightbox
- This requires threading an `onImageClick` callback through `renderMarkdown`
- OR: simpler approach — use event delegation on the chat bubble container to intercept clicks on any `<img>` within the message and open a lightbox

**Chosen approach:** Event delegation in `AgentChatPage.jsx` chat bubble rendering. When a click on an `<img>` inside a message bubble is detected, collect all `<img>` elements in that bubble, find the clicked index, and open `ImageLightbox`. This avoids modifying the markdown renderer.

### 4. MODIFY: `frontend/src/pages/AgentChatPage.jsx`

- Import `ImageLightbox`
- Add lightbox state: `{ open, images, initialIndex }`
- Add click handler on message content area that checks if `e.target` is an `<img>`, collects sibling images, opens lightbox
- Render `<ImageLightbox>` at page level

## Implementation Order

1. Create `ImageLightbox.jsx` with all gesture logic
2. Update `FilePreview.jsx` to use `ImageLightbox` for multi-image support
3. Add event delegation in `AgentChatPage.jsx` for inline markdown images
4. Test build passes

## Gesture Implementation Details

```
// Pinch zoom tracking
onTouchStart: if 2 touches → record initial distance + current scale
onTouchMove: if 2 touches → new scale = initialScale * (newDist / initialDist), clamp [1, 5]

// Pan tracking
onTouchMove: if 1 touch + zoomed → translate += delta

// Double-tap detection
onTouchEnd: if 1 touch → check time since last tap, if < 300ms → toggle zoom

// Swipe detection
onTouchEnd: if 1 touch + NOT zoomed → check dx/dy for swipe direction
```

All transitions use `will-change: transform` for GPU acceleration and CSS `transition` for animated snapping (e.g., when bouncing back from over-scroll or animating zoom toggle).
