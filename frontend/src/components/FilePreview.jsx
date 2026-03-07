import { useState, useCallback } from "react";
import { authedFetch } from "../lib/api";
import ImageLightbox from "./ImageLightbox";

// --- Shared action buttons (download + copy path) ---

function ActionButtons({ src, filename, originalPath }) {
  const [copied, setCopied] = useState(false);

  const handleDownload = async (e) => {
    e.stopPropagation();
    e.preventDefault();
    try {
      const res = await authedFetch(src);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch {
      window.open(src, "_blank");
    }
  };

  const handleCopyPath = (e) => {
    e.stopPropagation();
    e.preventDefault();
    navigator.clipboard.writeText(originalPath || filename).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <span className="inline-flex gap-0.5 shrink-0">
      <button
        type="button"
        onClick={handleDownload}
        title="Download file"
        className="p-0.5 rounded hover:bg-hover transition-colors text-dim hover:text-label"
      >
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
        </svg>
      </button>
      <button
        type="button"
        onClick={handleCopyPath}
        title={copied ? "Copied!" : "Copy file path"}
        className="p-0.5 rounded hover:bg-hover transition-colors text-dim hover:text-label"
      >
        {copied ? (
          <svg className="w-3.5 h-3.5 text-green-400" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
          </svg>
        ) : (
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <rect x="8" y="8" width="12" height="12" rx="2" />
            <path d="M16 8V6a2 2 0 00-2-2H6a2 2 0 00-2 2v8a2 2 0 002 2h2" />
          </svg>
        )}
      </button>
    </span>
  );
}

// --- Image Preview (compact thumbnail, tappable fullscreen) ---

function ImagePreview({ src, filename, originalPath, onOpen }) {
  const [error, setError] = useState(false);

  if (error) return null;

  return (
    <div>
      <div className="cursor-pointer" onClick={onOpen}>
        <img
          src={src}
          alt={filename}
          loading="lazy"
          onError={() => setError(true)}
          className="max-h-[120px] max-w-full rounded-lg border border-divider object-contain"
        />
      </div>
      <div className="flex items-center gap-1 mt-1">
        <p className="text-xs text-dim truncate max-w-[200px]">{filename}</p>
        <ActionButtons src={src} filename={filename} originalPath={originalPath} />
      </div>
    </div>
  );
}

// --- Video Preview (thumbnail, tappable to open in lightbox) ---

function VideoPreview({ src, filename, originalPath, onOpen }) {
  const [thumbError, setThumbError] = useState(false);
  const thumbUrl = src + ".thumb.jpg";

  return (
    <div>
      <div className="cursor-pointer" onClick={onOpen}>
        <div className="relative inline-block">
          {thumbError ? (
            /* Fallback: gray placeholder when no thumbnail available */
            <div className="w-[160px] h-[90px] rounded-lg border border-divider bg-elevated flex items-center justify-center" />
          ) : (
            <img
              src={thumbUrl}
              alt={filename}
              loading="lazy"
              onError={() => setThumbError(true)}
              className="max-h-[120px] max-w-full rounded-lg border border-divider object-contain block"
            />
          )}
          {/* Play icon overlay */}
          <div className="absolute inset-0 flex items-center justify-center rounded-lg">
            <div className="w-8 h-8 rounded-full bg-black/50 flex items-center justify-center">
              <svg className="w-4 h-4 ml-0.5 text-white" fill="currentColor" viewBox="0 0 24 24">
                <path d="M8 5v14l11-7z" />
              </svg>
            </div>
          </div>
        </div>
      </div>
      <div className="flex items-center gap-1 mt-1">
        <p className="text-xs text-dim truncate max-w-[200px]">{filename}</p>
        <ActionButtons src={src} filename={filename} originalPath={originalPath} />
      </div>
    </div>
  );
}

// --- Doc/Code File Preview (collapsible card) ---

function DocFilePreview({ src, filename, ext, originalPath }) {
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
      <div
        onClick={handleToggle}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-hover transition-colors text-left cursor-pointer"
      >
        <svg className="w-4 h-4 text-cyan-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
        </svg>
        <span className="text-xs text-label truncate flex-1 min-w-0">{filename}</span>
        <span className="text-[10px] text-dim uppercase shrink-0">{ext}</span>
        <ActionButtons src={src} filename={filename} originalPath={originalPath} />
        <svg className={`w-3 h-3 text-dim shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" d="m19 9-7 7-7-7" />
        </svg>
      </div>
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

function GenericFilePreview({ src, filename, originalPath }) {
  return (
    <div className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-elevated max-w-[240px]">
      <svg className="w-4 h-4 text-dim shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
      </svg>
      <span className="text-xs text-label truncate flex-1 min-w-0">{filename}</span>
      <ActionButtons src={src} filename={filename} originalPath={originalPath} />
    </div>
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
              <div
                key={att.path}
                className="flex items-center gap-2 px-3 py-1.5 hover:bg-hover transition-colors text-left"
              >
                <svg className="w-3.5 h-3.5 text-dim shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
                </svg>
                <span className="text-xs text-label truncate flex-1 min-w-0">{filename}</span>
                <span className="text-[10px] text-dim uppercase shrink-0">{att.ext}</span>
                <ActionButtons src={att.resolvedUrl} filename={filename} originalPath={att.originalPath} />
              </div>
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
              originalPath={att.originalPath}
              onOpen={() => openLightbox(idx)}
            />
          );
        }
        return <VideoPreview key={att.path} src={att.resolvedUrl} filename={filename} originalPath={att.originalPath} onOpen={() => openLightbox(idx)} />;
      })}
      {/* Doc files: single card if 1, grouped card if 2+ */}
      {docs.length === 1 && (
        <DocFilePreview src={docs[0].resolvedUrl} filename={docs[0].path.split("/").pop()} ext={docs[0].ext} originalPath={docs[0].originalPath} />
      )}
      {docs.length >= 2 && <DocGroupCard docs={docs} />}
      {/* Generic fallback for non-media, non-doc */}
      {other.map((att) => (
        <GenericFilePreview key={att.path} src={att.resolvedUrl} filename={att.path.split("/").pop()} originalPath={att.originalPath} />
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
