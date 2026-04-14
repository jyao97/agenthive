import { useState, useCallback } from "react";
import { Copy, Check } from "lucide-react";
import katex from "katex";
import { serverNow } from "./serverTime";
import { uploadUrl, fileUrl, fileUrlToThumbUrl, API_FILES_PREFIX, RE_UPLOADS_PATH, RE_PROJECTS_PATH, PROJECTS_DIR_SEGMENT } from "./urls";

/** Code block with copy button. */
function CodeBlock({ code }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback((e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => console.warn("Clipboard write failed"));
  }, [code]);
  return (
    <div className="group/code relative my-2">
      <pre className="p-3 pr-9 rounded-lg bg-inset text-sm text-body overflow-auto max-h-80 font-mono bubble-scroll">
        <code>{code}</code>
      </pre>
      <button
        type="button"
        onClick={handleCopy}
        className="absolute top-2 right-2 p-1 rounded bg-surface border border-divider text-dim opacity-100 sm:opacity-0 sm:group-hover/code:opacity-100 hover:text-body active:scale-95 transition-all cursor-pointer shadow-sm"
        title="Copy code"
      >
        {copied ? <Check size={13} strokeWidth={2.5} className="text-emerald-500" /> : <Copy size={13} />}
      </button>
    </div>
  );
}

/** Render a LaTeX string to HTML via KaTeX. Returns null on failure. */
function renderKatex(latex, displayMode) {
  try {
    return katex.renderToString(latex, { displayMode, throwOnError: false });
  } catch {
    return null;
  }
}

/** Shared date format options for toLocaleString / toLocaleTimeString. */
export const DATE_SHORT = { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" };
export const TIME_SHORT = { hour: "2-digit", minute: "2-digit" };

/** Turn an ISO / unix timestamp into a relative string like "2m ago". */
export function relativeTime(dateStr) {
  if (!dateStr) return "";
  const now = serverNow();
  // Backend returns UTC datetimes without timezone suffix — append Z so
  // JavaScript doesn't misinterpret them as local time.
  let str = String(dateStr);
  if (/^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(str)) str += "Z";
  const then = new Date(str).getTime();
  const raw = now - then;
  const future = raw < 0;
  const diff = Math.abs(raw);
  const seconds = Math.floor(diff / 1000);
  const fmt = (v, u) => future ? `in ${v}${u}` : `${v}${u} ago`;
  if (seconds < 60) return fmt(seconds, "s");
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return fmt(minutes, "m");
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return fmt(hours, "h");
  const days = Math.floor(hours / 24);
  return fmt(days, "d");
}

/**
 * Extremely lightweight markdown-ish renderer.
 * Handles ## headers, fenced code blocks, tables, inline code, bold,
 * italic, and image paths that look like local file references.
 */
export function renderMarkdown(text, project) {
  if (typeof text !== "string" || !text) return null;
  project = project || "";
  try {
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
        <CodeBlock key={elements.length} code={codeLines.join("\n")} />
      );
      continue;
    }

    // Block math ($$...$$)
    if (line.trimStart().startsWith("$$")) {
      const opening = line.trimStart();
      // Single-line: $$formula$$
      if (opening.endsWith("$$") && opening.length > 4) {
        const latex = opening.slice(2, -2).trim();
        const html = renderKatex(latex, true);
        if (html) {
          elements.push(<div key={elements.length} className="my-2 overflow-x-auto bubble-scroll" dangerouslySetInnerHTML={{ __html: html }} />);
          i++; continue;
        }
      }
      // Multi-line: $$\n...\n$$
      const mathLines = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith("$$")) {
        mathLines.push(lines[i]);
        i++;
      }
      i++; // skip closing $$
      const latex = mathLines.join("\n").trim();
      const html = renderKatex(latex, true);
      if (html) {
        elements.push(<div key={elements.length} className="my-2 overflow-x-auto bubble-scroll" dangerouslySetInnerHTML={{ __html: html }} />);
      } else {
        elements.push(<CodeBlock key={elements.length} code={latex} />);
      }
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
      const resolvedSrc = resolveFileUrl(src, project);
      elements.push(
        <img
          key={elements.length}
          src={resolvedSrc}
          alt=""
          className="my-2 max-w-full rounded-lg border border-divider cursor-pointer"
        />
      );
      i++;
      continue;
    }

    // Markdown table
    if (line.trim().startsWith("|") && line.trim().endsWith("|")) {
      const tableRows = [];
      while (i < lines.length && lines[i].trim().startsWith("|") && lines[i].trim().endsWith("|")) {
        tableRows.push(lines[i].trim());
        i++;
      }
      if (tableRows.length >= 2) {
        // Parse header, separator, and body rows
        const parseRow = (row) =>
          row.split("|").slice(1, -1).map((c) => c.trim());
        const header = parseRow(tableRows[0]);
        // Skip separator row (|---|---|)
        const isSep = (row) => /^[\s|:-]+$/.test(row);
        const bodyStart = isSep(tableRows[1]) ? 2 : 1;
        const bodyRows = tableRows.slice(bodyStart).filter((r) => !isSep(r));
        elements.push(
          <div key={elements.length} className="my-2 overflow-auto max-h-80 rounded-lg border border-divider bubble-scroll">
            <table className="min-w-full text-xs text-body">
              <thead>
                <tr className="bg-inset">
                  {header.map((h, j) => (
                    <th key={j} className="px-3 py-1.5 text-left font-semibold text-heading whitespace-nowrap border-b border-divider">
                      {renderInline(h)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {bodyRows.map((row, ri) => (
                  <tr key={ri} className={ri % 2 ? "bg-inset/50" : ""}>
                    {parseRow(row).map((cell, ci) => (
                      <td key={ci} className="px-3 py-1.5 whitespace-pre-wrap border-b border-divider last:border-b-0">
                        {renderInline(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
        continue;
      }
      // Fallback: not a real table, rewind
      i -= tableRows.length;
    }

    // Empty line
    if (line.trim() === "") {
      elements.push(<div key={elements.length} className="h-2" />);
      i++;
      continue;
    }

    // Regular paragraph — apply inline formatting
    elements.push(
      <p key={elements.length} className="text-sm text-body leading-relaxed break-words">
        {renderInline(line)}
      </p>
    );
    i++;
  }

  return <div className="space-y-0.5">{elements}</div>;
  } catch (e) {
    console.error("renderMarkdown error:", e);
    return <pre className="text-sm text-body whitespace-pre-wrap">{text}</pre>;
  }
}

// Agent IDs are 12-char hex strings. Match them as whole tokens
// (bounded by word boundaries or common delimiters).
const AGENT_ID_RE = /\b([0-9a-f]{12})\b/g;

/** Linkify agent IDs in a plain text string, returning an array of React elements. */
function linkifyAgentIds(text, keyPrefix) {
  const parts = [];
  let last = 0;
  let m;
  AGENT_ID_RE.lastIndex = 0;
  while ((m = AGENT_ID_RE.exec(text)) !== null) {
    if (m.index > last) {
      parts.push(<span key={`${keyPrefix}-t${last}`}>{text.slice(last, m.index)}</span>);
    }
    const agentId = m[1];
    parts.push(
      <a
        key={`${keyPrefix}-a${m.index}`}
        href={`/agents/${agentId}`}
        className="text-cyan-400 hover:underline font-mono"
        onClick={(e) => { e.stopPropagation(); }}
        onDoubleClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          navigator.clipboard.writeText(agentId).catch(() => console.warn("Clipboard write failed"));
        }}
        title="Click to open, double-tap to copy"
      >
        {agentId}
      </a>
    );
    last = AGENT_ID_RE.lastIndex;
  }
  if (last === 0) return null; // no IDs found — caller uses plain text
  if (last < text.length) {
    parts.push(<span key={`${keyPrefix}-t${last}`}>{text.slice(last)}</span>);
  }
  return parts;
}

/** Inline formatting: bold, italic, inline code. Uses React elements only (no innerHTML). */
export function renderInline(text) {
  if (typeof text !== "string" || !text) return null;
  try {
  // Split on inline code first, then handle bold/italic within non-code segments
  const codeParts = text.split(/(`[^`]+`)/g);
  const elements = [];

  for (let i = 0; i < codeParts.length; i++) {
    const part = codeParts[i];
    if (part.startsWith("`") && part.endsWith("`")) {
      const inner = part.slice(1, -1);
      const linked = linkifyAgentIds(inner, `c${i}`);
      elements.push(
        <code
          key={i}
          className="px-1 py-0.5 rounded bg-input text-cyan-300 text-xs font-mono"
        >
          {linked || inner}
        </code>
      );
    } else {
      // Split on inline math $...$ before tokenizing bold/italic
      const mathParts = part.split(/(\$[^$\n]+?\$)/g);
      for (let mi = 0; mi < mathParts.length; mi++) {
        const mp = mathParts[mi];
        // Inline math: require at least one LaTeX marker (\, {, ^, _) to avoid false positives
        if (mp.startsWith("$") && mp.endsWith("$") && mp.length > 2 && /[\\{}_^]/.test(mp)) {
          const latex = mp.slice(1, -1);
          const html = renderKatex(latex, false);
          if (html) {
            elements.push(<span key={`${i}-m${mi}`} dangerouslySetInnerHTML={{ __html: html }} />);
            continue;
          }
        }
      // Tokenize bold, italic, and links into safe React elements
      const tokens = tokenizeBoldItalic(mp);
      for (let j = 0; j < tokens.length; j++) {
        const token = tokens[j];
        const key = `${i}-${mi}-${j}`;
        if (token.type === "link") {
          elements.push(
            <a key={key} href={token.href} target="_blank" rel="noopener noreferrer"
              className="text-cyan-400 hover:underline" onClick={(e) => e.stopPropagation()}>
              {token.text}
            </a>
          );
        } else {
          const linked = linkifyAgentIds(token.text, key);
          if (token.type === "bold") {
            elements.push(<strong key={key}>{linked || token.text}</strong>);
          } else if (token.type === "italic") {
            elements.push(<em key={key}>{linked || token.text}</em>);
          } else {
            elements.push(<span key={key}>{linked || token.text}</span>);
          }
        }
      }
      } // end mathParts loop
    }
  }

  return elements;
  } catch (e) {
    console.error("renderInline error:", e);
    return <span>{text}</span>;
  }
}

/**
 * Split text into tokens of plain text, bold (**...**), italic (*...*),
 * and markdown links ([text](url)).
 * Returns an array of { type: "text"|"bold"|"italic"|"link", text: string, href?: string }.
 */
function tokenizeBoldItalic(text) {
  const tokens = [];
  // Match **bold**, *italic*, and [text](url) — but not ![img](url)
  const re = /(\*\*(.+?)\*\*|\*(.+?)\*|(?<!!)\[([^\]]+)\]\((https?:\/\/[^)]+)\))/g;
  let lastIndex = 0;
  let match;

  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ type: "text", text: text.slice(lastIndex, match.index) });
    }
    if (match[2] !== undefined) {
      tokens.push({ type: "bold", text: match[2] });
    } else if (match[3] !== undefined) {
      tokens.push({ type: "italic", text: match[3] });
    } else {
      tokens.push({ type: "link", text: match[4], href: match[5] });
    }
    lastIndex = re.lastIndex;
  }

  if (lastIndex < text.length) {
    tokens.push({ type: "text", text: text.slice(lastIndex) });
  }

  return tokens;
}

/**
 * Normalise a file path relative to a project.
 * Strips common prefixes that Claude outputs:
 *  - /projects/{name}/...
 *  - {project-name}/...  (e.g. "splitvla/file.webp" when project is "splitvla")
 *  - absolute paths containing the project name
 *  - leading slashes
 */
function cleanProjectPath(raw, project) {
  let p = raw;
  // /projects/{name}/...
  p = p.replace(/^\/projects\/[^/]+\//, "");
  // absolute path: strip everything up to and including project-name dir
  if (project) {
    const absRe = new RegExp(`^.*/` + project.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + `/`);
    p = p.replace(absRe, "");
    // relative project-name prefix: "splitvla/file.webp" → "file.webp"
    if (p.startsWith(project + "/")) {
      p = p.slice(project.length + 1);
    }
  }
  p = p.replace(/^\/+/, "");
  return p;
}

/**
 * Resolve a raw file path to an API URL.
 *
 * Handles cross-project references: if the path contains an absolute path
 * like `/home/.../agenthive-projects/<other-project>/rest`, we extract the
 * true project from the path instead of using the agent's project.
 * Also handles paths that are already `/api/files/...` URLs.
 */
function resolveFileUrl(rawPath, defaultProject) {
  if (rawPath.startsWith("http")) return rawPath;

  // Already an /api/files/ URL (from a previous render or message content)
  const apiMatch = rawPath.match(/^\/?api\/files\/([^/]+)\/(.+)/);
  if (apiMatch) {
    const proj = apiMatch[1];
    const rest = apiMatch[2];
    return fileUrl(proj, rest);
  }

  // User-uploaded file path (.agenthive/uploads/) — route to /api/uploads/
  const uploadMatch = rawPath.match(RE_UPLOADS_PATH);
  if (uploadMatch) {
    return uploadUrl(uploadMatch[1]);
  }

  // Absolute path with agenthive-projects — extract true project
  const absMatch = rawPath.match(RE_PROJECTS_PATH);
  if (absMatch) {
    const proj = absMatch[1];
    const rest = absMatch[2];
    return fileUrl(proj, rest);
  }

  // Default: use the agent's project
  const clean = cleanProjectPath(rawPath, defaultProject);
  return fileUrl(defaultProject, clean);
}

// File extension groups
const IMAGE_EXTS = /\.(png|jpg|jpeg|gif|svg|webp)$/i;
const VIDEO_EXTS = /\.(mp4|webm|mov)$/i;
const DOC_EXTS = /\.(py|js|ts|jsx|tsx|html|css|md|txt|pdf|sh|bash|rb|go|rs|c|cpp|h|hpp|java|kt|swift|yaml|yml|toml|xml|sql|r|lua|pl|ex|exs|zig|nim|dart|scala|clj|hs|erl|elm)$/i;
const CSV_EXTS = /\.(csv)$/i;
const IGNORE_EXTS = /\.(jsonl|log|json|lock)$/i;

// All extensions the agent path scanner should match (media + doc)
const AGENT_EXTS = /\.(png|jpg|jpeg|gif|svg|webp|mp4|webm|mov|csv|py|js|ts|jsx|tsx|html|css|md|txt|pdf|sh|bash|rb|go|rs|c|cpp|h|hpp|java|kt|swift|yaml|yml|toml|xml|sql|r|lua|pl|ex|exs|zig|nim|dart|scala|clj|hs|erl|elm|ply|obj|stl|glb|gltf)$/i;

// Compiled regexes for agent path detection
const AGENT_EXT_LIST = "png|jpg|jpeg|gif|svg|webp|mp4|webm|mov|csv|py|js|ts|jsx|tsx|html|css|md|txt|pdf|sh|bash|rb|go|rs|c|cpp|h|hpp|java|kt|swift|yaml|yml|toml|xml|sql|r|lua|pl|ex|exs|zig|nim|dart|scala|clj|hs|erl|elm|ply|obj|stl|glb|gltf";
const RE_MD_IMAGE = new RegExp(`!\\[.*?\\]\\((\\S+?\\.(?:${AGENT_EXT_LIST}))\\)`, "gi");
const RE_BACKTICK = new RegExp("`([^`]*/[^`]*\\.(?:" + AGENT_EXT_LIST + "))`", "gi");
const RE_BARE_PATH = new RegExp("(?:^|[\\s(])([^\\s()\\[\\]!]*/[^\\s()\\[\\]]+\\.(?:" + AGENT_EXT_LIST + "))(?=[\\s),\\]]|$)", "gim");

// User-uploaded file tag
const RE_ATTACHED_FILE = /\[Attached file: ([^\]]+)\]/gi;

/** Strip [Attached file: ...] tags from display text. */
export function stripAttachmentTags(text) {
  if (!text) return text;
  return text.replace(/\n?\[Attached file: [^\]]+\]/g, "").trim();
}

function classifyExt(filename) {
  if (IMAGE_EXTS.test(filename)) return "image";
  if (VIDEO_EXTS.test(filename)) return "video";
  if (CSV_EXTS.test(filename)) return "csv";
  if (DOC_EXTS.test(filename)) return "doc";
  return "file";
}

/**
 * Extract file attachments from message text.
 *
 * @param {string} text - Message content
 * @param {string} project - Project name (for resolving project file URLs)
 * @param {"USER"|"AGENT"} role - Message role controls detection strategy:
 *   USER  → only [Attached file: ...] tags (uploaded via "+" button)
 *   AGENT → detect file paths; images/videos as thumbnails, code/doc as
 *           collapsible cards, ignore log/data files (.jsonl,.log,.csv,.json,.lock)
 *
 * Returns array of { path, resolvedUrl, type, ext }.
 */
export function extractFileAttachments(text, project, role, metadata) {
  if (!text && !metadata?.attachments?.length) return [];

  const seen = new Set();
  const results = [];

  // --- USER messages: only uploaded file tags ---
  if (role === "USER") {
    // Pre-parsed attachment paths from display file metadata
    if (metadata?.attachments?.length) {
      return metadata.attachments.map(filePath => {
        const filename = filePath.split("/").pop();
        const resolvedUrl = uploadUrl(filename);
        const ext = filename.match(/\.(\w+)$/)?.[1]?.toLowerCase() || "";
        const type = classifyExt(filename);
        return { path: filename, resolvedUrl, type, ext, originalPath: filePath };
      });
    }
    // Fallback: parse from content tags (legacy messages)
    let m;
    RE_ATTACHED_FILE.lastIndex = 0;
    while ((m = RE_ATTACHED_FILE.exec(text)) !== null) {
      const filePath = m[1];
      if (!filePath) continue;
      const filename = filePath.split("/").pop();
      if (seen.has(filename)) continue;
      seen.add(filename);

      const resolvedUrl = uploadUrl(filename);
      const ext = filename.match(/\.(\w+)$/)?.[1]?.toLowerCase() || "";
      const type = classifyExt(filename);
      results.push({ path: filename, resolvedUrl, type, ext, originalPath: filePath });
    }
    return results;
  }

  // --- AGENT messages: detect file paths in text ---

  // Collect paths already rendered inline by renderMarkdown.
  // Skip lines inside fenced code blocks — renderMarkdown renders those as
  // code text, not as inline <img>, so they must NOT be marked as "already rendered".
  const inlineRendered = new Set();
  let inCodeBlock = false;
  for (const line of text.split("\n")) {
    if (line.startsWith("```")) { inCodeBlock = !inCodeBlock; continue; }
    if (inCodeBlock) continue;
    const trimmed = line.trim();
    const mdFull = trimmed.match(/^!\[.*?\]\((.+?)\)$/);
    if (mdFull) inlineRendered.add(mdFull[1]);
    const bareFull = trimmed.match(/^(\S+\.(?:png|jpg|jpeg|gif|svg|webp))$/i);
    if (bareFull) inlineRendered.add(bareFull[1]);
  }

  const addPath = (rawPath) => {
    if (!rawPath || !AGENT_EXTS.test(rawPath)) return;
    if (IGNORE_EXTS.test(rawPath)) return;
    if (rawPath.endsWith(".thumb.jpg")) return;
    let path = cleanProjectPath(rawPath, project);
    if (seen.has(path) || inlineRendered.has(rawPath)) return;
    seen.add(path);

    const resolvedUrl = resolveFileUrl(rawPath, project);

    const ext = path.match(/\.(\w+)$/)?.[1]?.toLowerCase() || "";
    const type = classifyExt(path);
    // Only generate thumbUrl for /api/files/ URLs (not /api/uploads/ or http)
    const thumbCandidate = type === "image" && resolvedUrl.startsWith(API_FILES_PREFIX)
      ? fileUrlToThumbUrl(resolvedUrl)
      : undefined;
    const thumbUrl = thumbCandidate !== resolvedUrl ? thumbCandidate : undefined;
    results.push({ path, resolvedUrl, type, ext, originalPath: rawPath, thumbUrl });
  };

  let m;
  RE_MD_IMAGE.lastIndex = 0;
  while ((m = RE_MD_IMAGE.exec(text)) !== null) addPath(m[1]);
  RE_BACKTICK.lastIndex = 0;
  while ((m = RE_BACKTICK.exec(text)) !== null) addPath(m[1]);
  RE_BARE_PATH.lastIndex = 0;
  while ((m = RE_BARE_PATH.exec(text)) !== null) addPath(m[1]);

  return results;
}

/** Format elapsed seconds as "2m 30s" or "1h 15m". */
export function elapsedDisplay(seconds) {
  if (seconds == null || seconds < 0) return "";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
}

/** Format duration between two ISO strings as "took 5m". */
export function durationDisplay(startIso, endIso) {
  if (!startIso || !endIso) return "";
  let s1 = String(startIso);
  let s2 = String(endIso);
  if (/^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(s1)) s1 += "Z";
  if (/^\d{4}-\d{2}-\d{2}T[\d:.]+$/.test(s2)) s2 += "Z";
  const seconds = Math.max(0, Math.floor((new Date(s2) - new Date(s1)) / 1000));
  return `took ${elapsedDisplay(seconds)}`;
}
