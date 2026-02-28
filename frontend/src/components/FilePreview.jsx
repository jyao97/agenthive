import { useState, useRef, useCallback } from "react";
import { authedFetch } from "../lib/api";
import ImageLightbox from "./ImageLightbox";

// --- Image Preview (compact thumbnail, tappable fullscreen) ---

function ImagePreview({ src, filename, onOpen }) {
  const [error, setError] = useState(false);

  if (error) return null;

  return (
    <div className="group cursor-pointer" onClick={onOpen}>
      <img
        src={src}
        alt={filename}
        loading="lazy"
        onError={() => setError(true)}
        className="max-h-[120px] max-w-full rounded-lg border border-divider object-contain"
      />
      <p className="text-xs text-dim mt-1 truncate max-w-[200px]">{filename}</p>
    </div>
  );
}

// --- Video Preview (thumbnail, tappable to open in lightbox) ---

function VideoPreview({ src, filename, onOpen }) {
  const [error, setError] = useState(false);
  const vidRef = useRef(null);

  if (error) return null;

  return (
    <div className="group cursor-pointer" onClick={onOpen}>
      <div className="relative inline-block">
        <video
          ref={vidRef}
          src={src}
          preload="metadata"
          muted
          onLoadedData={() => { if (vidRef.current) vidRef.current.currentTime = 0.1; }}
          onError={() => setError(true)}
          className="max-h-[120px] max-w-full rounded-lg border border-divider object-contain block"
        />
        {/* Play icon overlay — scoped to video area only */}
        <div className="absolute inset-0 flex items-center justify-center rounded-lg">
          <div className="w-8 h-8 rounded-full bg-black/50 flex items-center justify-center">
            <svg className="w-4 h-4 ml-0.5 text-white" fill="currentColor" viewBox="0 0 24 24">
              <path d="M8 5v14l11-7z" />
            </svg>
          </div>
        </div>
      </div>
      <p className="text-xs text-dim mt-1 truncate max-w-[200px]">{filename}</p>
    </div>
  );
}

// --- Doc/Code File Preview (collapsible card) ---

function DocFilePreview({ src, filename, ext }) {
  const [expanded, setExpanded] = useState(false);
  const [content, setContent] = useState(null);
  const [loadState, setLoadState] = useState("idle"); // idle | loading | loaded | error

  const loadContent = useCallback(async () => {
    if (loadState === "loading") return;
    setLoadState("loading");
    try {
      const res = await authedFetch(src);
      if (!res.ok) throw new Error("fetch failed");
      const text = await res.text();
      setContent(text);
      setLoadState("loaded");
    } catch {
      setLoadState("error");
    }
  }, [src, loadState]);

  const handleToggle = () => {
    if (!expanded && loadState === "idle") loadContent();
    setExpanded((v) => !v);
  };

  const isPdf = ext === "pdf";

  return (
    <div className="rounded-lg bg-elevated overflow-hidden max-w-[280px]">
      <button
        type="button"
        onClick={handleToggle}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-hover transition-colors text-left"
      >
        <svg className="w-4 h-4 text-cyan-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
        </svg>
        <span className="text-xs text-label truncate flex-1 min-w-0">{filename}</span>
        <span className="text-[10px] text-dim uppercase shrink-0">{ext}</span>
        <svg className={`w-3 h-3 text-dim shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" d="m19 9-7 7-7-7" />
        </svg>
      </button>
      {expanded && (
        <div className="border-t border-divider">
          {loadState === "loading" && (
            <div className="px-3 py-2 text-xs text-dim">Loading...</div>
          )}
          {loadState === "error" && (
            <div className="px-3 py-2 text-xs text-red-400">Failed to load</div>
          )}
          {loadState === "loaded" && !isPdf && content != null && (
            <pre className="px-3 py-2 text-xs text-body font-mono overflow-x-auto max-h-48 whitespace-pre-wrap break-words">
              {content.length > 3000 ? content.slice(0, 3000) + "\n..." : content}
            </pre>
          )}
          {isPdf && (
            <a
              href={src}
              target="_blank"
              rel="noopener noreferrer"
              className="block px-3 py-2 text-xs text-cyan-400 hover:underline"
            >
              Open PDF in new tab
            </a>
          )}
        </div>
      )}
    </div>
  );
}

// --- Generic File Card (non-media, non-doc — fallback for user uploads) ---

function GenericFilePreview({ src, filename }) {
  return (
    <a
      href={src}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-elevated hover:bg-hover transition-colors max-w-[240px]"
    >
      <svg className="w-4 h-4 text-dim shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
      </svg>
      <span className="text-xs text-label truncate flex-1 min-w-0">{filename}</span>
    </a>
  );
}

// --- Grouped doc files card (collapsible list for 2+ doc files) ---

function DocGroupCard({ docs }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg bg-elevated overflow-hidden max-w-[280px]">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-hover transition-colors text-left"
      >
        <svg className="w-4 h-4 text-cyan-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
        </svg>
        <span className="text-xs text-label flex-1 min-w-0">{docs.length} files referenced</span>
        <svg className={`w-3 h-3 text-dim shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" d="m19 9-7 7-7-7" />
        </svg>
      </button>
      {expanded && (
        <div className="border-t border-divider max-h-60 overflow-y-auto">
          {docs.map((att) => {
            const filename = att.path.split("/").pop();
            return (
              <a
                key={att.path}
                href={att.resolvedUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 px-3 py-1.5 hover:bg-hover transition-colors text-left"
              >
                <svg className="w-3.5 h-3.5 text-dim shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                </svg>
                <span className="text-xs text-label truncate flex-1 min-w-0">{filename}</span>
                <span className="text-[10px] text-dim uppercase shrink-0">{att.ext}</span>
              </a>
            );
          })}
        </div>
      )}
    </div>
  );
}

// --- Main component ---

export default function FileAttachments({ attachments }) {
  const [lightbox, setLightbox] = useState(null); // { media, initialIndex } or null

  if (!attachments || attachments.length === 0) return null;

  // Split into media (inline) vs doc/file (groupable)
  const mediaAtts = [];
  const docs = [];
  const other = [];
  for (const att of attachments) {
    if (att.type === "image" || att.type === "video") mediaAtts.push(att);
    else if (att.type === "doc") docs.push(att);
    else other.push(att);
  }

  // Unified media gallery: images and videos in one swipeable lightbox
  const galleryMedia = mediaAtts.map((att) => ({
    type: att.type,
    src: att.resolvedUrl,
    filename: att.path.split("/").pop(),
  }));

  const openLightbox = (mediaIndex) => {
    setLightbox({ media: galleryMedia, initialIndex: mediaIndex });
  };

  return (
    <div className="flex flex-col gap-2 mt-1.5">
      {/* Images and videos always render inline */}
      {mediaAtts.map((att, idx) => {
        const filename = att.path.split("/").pop();
        if (att.type === "image") {
          return (
            <ImagePreview
              key={att.path}
              src={att.resolvedUrl}
              filename={filename}
              onOpen={() => openLightbox(idx)}
            />
          );
        }
        return <VideoPreview key={att.path} src={att.resolvedUrl} filename={filename} onOpen={() => openLightbox(idx)} />;
      })}
      {/* Doc files: single card if 1, grouped card if 2+ */}
      {docs.length === 1 && (
        <DocFilePreview src={docs[0].resolvedUrl} filename={docs[0].path.split("/").pop()} ext={docs[0].ext} />
      )}
      {docs.length >= 2 && <DocGroupCard docs={docs} />}
      {/* Generic fallback for non-media, non-doc */}
      {other.map((att) => (
        <GenericFilePreview key={att.path} src={att.resolvedUrl} filename={att.path.split("/").pop()} />
      ))}

      {/* Lightbox for media gallery */}
      {lightbox && (
        <ImageLightbox
          media={lightbox.media}
          initialIndex={lightbox.initialIndex}
          onClose={() => setLightbox(null)}
        />
      )}
    </div>
  );
}
