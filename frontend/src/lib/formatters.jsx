/** Turn an ISO / unix timestamp into a relative string like "2m ago". */
export function relativeTime(dateStr) {
  if (!dateStr) return "";
  const now = Date.now();
  // Backend returns UTC datetimes without timezone suffix — append Z so
  // JavaScript doesn't misinterpret them as local time.
  let str = String(dateStr);
  if (/^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(str)) str += "Z";
  const then = new Date(str).getTime();
  const diff = Math.max(0, now - then);
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/**
 * Extremely lightweight markdown-ish renderer.
 * Handles ## headers, fenced code blocks, inline code, bold, italic,
 * and image paths that look like local file references.
 */
export function renderMarkdown(text, project) {
  if (!text) return null;
  // We import React implicitly via JSX transform
  const lines = text.split("\n");
  const elements = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    if (line.startsWith("```")) {
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      elements.push(
        <pre
          key={elements.length}
          className="my-2 p-3 rounded-lg bg-inset text-sm text-body overflow-x-auto font-mono"
        >
          <code>{codeLines.join("\n")}</code>
        </pre>
      );
      continue;
    }

    // Headers
    if (line.startsWith("### ")) {
      elements.push(
        <h4 key={elements.length} className="text-sm font-semibold text-heading mt-3 mb-1">
          {line.slice(4)}
        </h4>
      );
      i++;
      continue;
    }
    if (line.startsWith("## ")) {
      elements.push(
        <h3 key={elements.length} className="text-base font-semibold text-heading mt-4 mb-1">
          {line.slice(3)}
        </h3>
      );
      i++;
      continue;
    }
    if (line.startsWith("# ")) {
      elements.push(
        <h2 key={elements.length} className="text-lg font-bold text-heading mt-4 mb-2">
          {line.slice(2)}
        </h2>
      );
      i++;
      continue;
    }

    // Image reference
    const imgMatch = line.trim().match(/^!\[.*?\]\((.+?)\)$/);
    const plainImgMatch =
      !imgMatch && line.trim().match(/^(\S+\.(png|jpg|jpeg|gif|svg|webp))$/i);
    if (imgMatch || plainImgMatch) {
      const src = imgMatch ? imgMatch[1] : plainImgMatch[1];
      const resolvedSrc = src.startsWith("http")
        ? src
        : `/api/files/${project}/${src.replace(/^\/+/, "")}`;
      elements.push(
        <img
          key={elements.length}
          src={resolvedSrc}
          alt=""
          className="my-2 max-w-full rounded-lg border border-divider"
        />
      );
      i++;
      continue;
    }

    // Empty line
    if (line.trim() === "") {
      elements.push(<div key={elements.length} className="h-2" />);
      i++;
      continue;
    }

    // Regular paragraph — apply inline formatting
    elements.push(
      <p key={elements.length} className="text-sm text-body leading-relaxed">
        {renderInline(line)}
      </p>
    );
    i++;
  }

  return <div className="space-y-0.5">{elements}</div>;
}

/** Inline formatting: bold, italic, inline code. */
export function renderInline(text) {
  const parts = text.split(/(`[^`]+`)/g);
  return parts.map((part, idx) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={idx}
          className="px-1 py-0.5 rounded bg-input text-cyan-300 text-xs font-mono"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    let processed = part.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    processed = processed.replace(/\*(.+?)\*/g, "<i>$1</i>");
    return <span key={idx} dangerouslySetInnerHTML={{ __html: processed }} />;
  });
}

// File extensions we detect for inline previews
const IMAGE_EXTS = /\.(png|jpg|jpeg|gif|svg|webp)$/i;
const VIDEO_EXTS = /\.(mp4|webm|mov)$/i;
const CSV_EXT = /\.csv$/i;
const ALL_EXTS = /\.(png|jpg|jpeg|gif|svg|webp|mp4|webm|mov|csv)$/i;

// Compiled regexes for path detection
const RE_MD_IMAGE = /!\[.*?\]\((\S+?\.(?:png|jpg|jpeg|gif|svg|webp|mp4|webm|mov|csv))\)/gi;
const RE_BACKTICK = /`([^`]*\/[^`]*\.(?:png|jpg|jpeg|gif|svg|webp|mp4|webm|mov|csv))`/gi;
const RE_BARE_PATH = /(?:^|[\s(])([^\s()\[\]!]*\/[^\s()\[\]]+\.(?:png|jpg|jpeg|gif|svg|webp|mp4|webm|mov|csv))(?=[\s),\]]|$)/gim;

/**
 * Extract file attachments (images, videos, CSVs) from message text.
 * Returns an array of { path, resolvedUrl, type, ext } objects (data, not JSX).
 * Skips images that renderMarkdown already renders inline (full-line ![](...)
 * or bare filename lines) to avoid double-rendering.
 */
export function extractFileAttachments(text, project) {
  if (!text) return [];

  // Collect paths already rendered inline by renderMarkdown
  const inlineRendered = new Set();
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    // Full-line markdown image: ![...](path.ext)
    const mdFull = trimmed.match(/^!\[.*?\]\((.+?)\)$/);
    if (mdFull) inlineRendered.add(mdFull[1]);
    // Bare filename on its own line: image.png
    const bareFull = trimmed.match(/^(\S+\.(?:png|jpg|jpeg|gif|svg|webp))$/i);
    if (bareFull) inlineRendered.add(bareFull[1]);
  }

  const seen = new Set();
  const results = [];

  const addPath = (rawPath) => {
    if (!rawPath || !ALL_EXTS.test(rawPath)) return;
    // Strip container-absolute prefix
    let path = rawPath.replace(/^\/projects\/[^/]+\//, "");
    path = path.replace(/^\/+/, "");
    if (seen.has(path) || inlineRendered.has(rawPath)) return;
    seen.add(path);

    const resolvedUrl = rawPath.startsWith("http")
      ? rawPath
      : `/api/files/${project}/${path}`;

    let type = "unknown";
    if (IMAGE_EXTS.test(path)) type = "image";
    else if (VIDEO_EXTS.test(path)) type = "video";
    else if (CSV_EXT.test(path)) type = "csv";

    const ext = path.match(/\.(\w+)$/)?.[1]?.toLowerCase() || "";
    results.push({ path, resolvedUrl, type, ext });
  };

  // Match all three patterns
  let m;
  RE_MD_IMAGE.lastIndex = 0;
  while ((m = RE_MD_IMAGE.exec(text)) !== null) addPath(m[1]);
  RE_BACKTICK.lastIndex = 0;
  while ((m = RE_BACKTICK.exec(text)) !== null) addPath(m[1]);
  RE_BARE_PATH.lastIndex = 0;
  while ((m = RE_BARE_PATH.exec(text)) !== null) addPath(m[1]);

  return results;
}
