import { useState, useEffect, useCallback, useRef, useLayoutEffect } from "react";
import { fetchProjectTree, browseProjectFile, downloadFile as dlFile } from "../lib/api";
import { renderMarkdown } from "../lib/formatters";
import { fileUrl } from "../lib/urls";
import { SCROLL_SAVE_DEBOUNCE } from "../lib/constants";

/* ---- tiny helpers ---- */

function extFromName(name) {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

function langFromExt(ext) {
  const map = {
    js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
    py: "python", rb: "ruby", rs: "rust", go: "go", java: "java",
    c: "c", cpp: "cpp", h: "c", hpp: "cpp", cs: "csharp",
    sh: "bash", bash: "bash", zsh: "bash",
    json: "json", yaml: "yaml", yml: "yaml", toml: "toml",
    html: "html", css: "css", scss: "scss", sql: "sql",
    md: "markdown", mdx: "markdown",
  };
  return map[ext] || "";
}

function downloadFile(project, path, filename) {
  const url = fileUrl(project, path);
  dlFile(url, filename);
}

/* ---- folder / file icons (inline SVG) ---- */

function FolderIcon({ open }) {
  return open ? (
    <svg className="w-4 h-4 text-amber-400 shrink-0" fill="currentColor" viewBox="0 0 20 20">
      <path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v1H2V6z" />
      <path fillRule="evenodd" d="M2 9h16l-2 7H4L2 9z" clipRule="evenodd" />
    </svg>
  ) : (
    <svg className="w-4 h-4 text-amber-400 shrink-0" fill="currentColor" viewBox="0 0 20 20">
      <path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg className="w-4 h-4 text-zinc-400 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
    </svg>
  );
}

/* ---- recursive tree node ---- */

function TreeNode({ node, depth, project, onFileClick, expandedDirs, toggleDir }) {
  const isDir = node.type === "dir";
  const isOpen = expandedDirs.has(node.path);

  return (
    <>
      <div
        onClick={() => isDir ? toggleDir(node.path) : onFileClick(node)}
        className="w-full flex items-center gap-1.5 py-1.5 px-2 rounded-lg hover:bg-input transition-colors text-left cursor-pointer group"
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {isDir ? <FolderIcon open={isOpen} /> : <FileIcon />}
        <span className={`text-sm truncate ${isDir ? "font-medium text-heading" : "text-body group-hover:text-heading"}`}>
          {node.name}
        </span>
        {isDir && node.children?.length > 0 && (
          <span className="text-[10px] text-dim ml-auto shrink-0">{node.children.length}</span>
        )}
        {!isDir && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); downloadFile(project, node.path, node.name); }}
            title="Download"
            className="ml-auto p-0.5 rounded hover:bg-hover transition-colors text-dim hover:text-label opacity-0 group-hover:opacity-100 shrink-0"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
            </svg>
          </button>
        )}
      </div>
      {isDir && isOpen && node.children?.map((child) => (
        <TreeNode
          key={child.path}
          node={child}
          depth={depth + 1}
          project={project}
          onFileClick={onFileClick}
          expandedDirs={expandedDirs}
          toggleDir={toggleDir}
        />
      ))}
    </>
  );
}

/* ---- file viewer panel ---- */

function FileViewer({ project, node }) {
  const [content, setContent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    browseProjectFile(project, node.path)
      .then((res) => {
        if (res.message) setError(res.message);
        else setContent(res.content);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [project, node.path]);

  const ext = extFromName(node.name);
  const isMarkdown = ext === "md" || ext === "mdx";

  if (loading) return <div className="flex items-center justify-center h-40 text-label text-sm">Loading...</div>;
  if (error) return <div className="p-4 text-label text-sm">{error}</div>;
  if (isMarkdown) return <div className="p-4 max-w-3xl mx-auto">{renderMarkdown(content, project)}</div>;
  return (
    <pre className="p-4 text-sm text-body font-mono leading-relaxed overflow-x-auto whitespace-pre">
      <code>{content}</code>
    </pre>
  );
}

/* ---- main modal ---- */

export default function ProjectBrowserModal({ project, onClose }) {
  const [tree, setTree] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expandedDirs, setExpandedDirs] = useState(new Set());
  const [viewingFile, setViewingFile] = useState(null);
  const scrollRef = useRef(null);
  const scrollSaveTimer = useRef(null);

  // Bottom sheet animation state
  const [mounted, setMounted] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const [sheetY, setSheetY] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const touchStartRef = useRef(null);

  useEffect(() => {
    requestAnimationFrame(() => requestAnimationFrame(() => setMounted(true)));
  }, []);

  const dismissing = useRef(false);
  const dismiss = useCallback(() => {
    if (dismissing.current) return;
    dismissing.current = true;
    setIsClosing(true);
    setTimeout(() => onClose(), 300);
  }, [onClose]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchProjectTree(project, 3);
      setTree(res.tree || []);
      // restore cached state or auto-expand root dirs
      let restored = false;
      try {
        const savedExp = localStorage.getItem(`filebrowser:${project}:expanded`);
        if (savedExp) { setExpandedDirs(new Set(JSON.parse(savedExp))); restored = true; }
        const savedView = localStorage.getItem(`filebrowser:${project}:viewing`);
        if (savedView) setViewingFile(JSON.parse(savedView));
      } catch { /* ignore */ }
      if (!restored) {
        const rootDirs = (res.tree || []).filter((n) => n.type === "dir").map((n) => n.path);
        setExpandedDirs(new Set(rootDirs));
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [project]);

  useEffect(() => { load(); }, [load]);

  // Escape to close
  useEffect(() => {
    const handler = (e) => {
      if (e.key === "Escape") {
        if (viewingFile) setViewingFile(null);
        else dismiss();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [dismiss, viewingFile]);

  // Lock body scroll
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, []);

  const toggleDir = useCallback((path) => {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  // Save tree scroll before switching to file viewer
  const openFile = useCallback((node) => {
    const el = scrollRef.current;
    if (el) {
      clearTimeout(scrollSaveTimer.current);
      try { localStorage.setItem(`filebrowser:${project}:scroll`, String(el.scrollTop)); } catch { /* ignore */ }
    }
    setViewingFile(node);
  }, [project]);

  // Persist expandedDirs
  useEffect(() => {
    if (loading) return;
    try { localStorage.setItem(`filebrowser:${project}:expanded`, JSON.stringify([...expandedDirs])); } catch { /* ignore */ }
  }, [expandedDirs, project, loading]);

  // Persist viewingFile
  useEffect(() => {
    if (loading) return;
    try {
      if (viewingFile) localStorage.setItem(`filebrowser:${project}:viewing`, JSON.stringify({ path: viewingFile.path, name: viewingFile.name, type: viewingFile.type }));
      else localStorage.removeItem(`filebrowser:${project}:viewing`);
    } catch { /* ignore */ }
  }, [viewingFile, project, loading]);

  // Debounced scroll save for tree view
  const handleTreeScroll = useCallback(() => {
    if (viewingFile) return;
    const el = scrollRef.current;
    if (!el) return;
    clearTimeout(scrollSaveTimer.current);
    scrollSaveTimer.current = setTimeout(() => {
      try { localStorage.setItem(`filebrowser:${project}:scroll`, String(el.scrollTop)); } catch { /* ignore */ }
    }, SCROLL_SAVE_DEBOUNCE);
  }, [project, viewingFile]);

  // Restore tree scroll after render
  useLayoutEffect(() => {
    if (loading || viewingFile) return;
    try {
      const saved = localStorage.getItem(`filebrowser:${project}:scroll`);
      if (saved) {
        const el = scrollRef.current;
        if (el) el.scrollTop = Number(saved);
      }
    } catch { /* ignore */ }
  }, [loading, viewingFile, project]);

  // Swipe-down gesture on drag handle
  const handleDragStart = (e) => {
    touchStartRef.current = { y: e.touches[0].clientY };
    setIsDragging(true);
  };
  const handleDragMove = (e) => {
    if (!touchStartRef.current) return;
    const dy = e.touches[0].clientY - touchStartRef.current.y;
    if (dy > 0) setSheetY(dy);
  };
  const handleDragEnd = () => {
    if (!touchStartRef.current) return;
    setIsDragging(false);
    if (sheetY > 120) dismiss();
    else setSheetY(0);
    touchStartRef.current = null;
  };

  const sheetTranslate = isClosing ? "translateY(100%)" : `translateY(${sheetY}px)`;
  const sheetTransition = isDragging ? "none" : "transform 0.3s cubic-bezier(0.32, 0.72, 0, 1)";

  // Block touches on backdrop / drag-handle from reaching the page below
  const blockBgTouch = useCallback((e) => {
    if (scrollRef.current?.contains(e.target)) return;
    e.preventDefault();
  }, []);

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col justify-end items-center"
      onTouchMove={blockBgTouch}
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 transition-opacity duration-300"
        style={{ backgroundColor: "rgba(0,0,0,0.4)", opacity: mounted && !isClosing ? 1 : 0, touchAction: "none" }}
        onClick={dismiss}
      />

      {/* Bottom sheet */}
      <div
        className="relative z-10 bg-page rounded-t-[20px] shadow-2xl flex flex-col w-full"
        style={{
          maxHeight: "92vh",
          transform: mounted ? sheetTranslate : "translateY(100%)",
          transition: sheetTransition,
          willChange: "transform",
        }}
      >
        {/* Drag handle */}
        <div
          className="flex justify-center pt-3 pb-1 cursor-grab active:cursor-grabbing shrink-0"
          style={{ touchAction: "none" }}
          onTouchStart={handleDragStart}
          onTouchMove={handleDragMove}
          onTouchEnd={handleDragEnd}
        >
          <div className="w-10 h-1 rounded-full bg-dim/40" />
        </div>

        {/* Header */}
        <div className="shrink-0 flex items-center gap-2 px-4 pb-3 border-b border-divider">
          {viewingFile ? (
            <button
              type="button"
              onClick={() => setViewingFile(null)}
              className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-input transition-colors shrink-0"
            >
              <svg className="w-5 h-5 text-label" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
              </svg>
            </button>
          ) : (
            <button
              type="button"
              onClick={dismiss}
              className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-input transition-colors shrink-0"
            >
              <svg className="w-5 h-5 text-label" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
          <h2 className="text-base font-bold text-heading flex-1 truncate">
            {viewingFile ? viewingFile.path : "Browse Files"}
          </h2>
          {viewingFile && (
            <button
              type="button"
              onClick={() => downloadFile(project, viewingFile.path, viewingFile.name)}
              title="Download file"
              className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-input transition-colors shrink-0"
            >
              <svg className="w-4 h-4 text-label" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
              </svg>
            </button>
          )}
        </div>

        {/* Body */}
        <div ref={scrollRef} onScroll={handleTreeScroll} className="flex-1 overflow-y-auto" style={{ overscrollBehavior: "none" }}>
          {viewingFile ? (
            <FileViewer project={project} node={viewingFile} />
          ) : loading ? (
            <div className="flex items-center justify-center h-40 text-label text-sm">Loading...</div>
          ) : error ? (
            <div className="p-4 text-red-400 text-sm">{error}</div>
          ) : tree.length === 0 ? (
            <div className="flex items-center justify-center h-40 text-label text-sm">No files found</div>
          ) : (
            <div className="p-2">
              {tree.map((node) => (
                <TreeNode
                  key={node.path}
                  node={node}
                  depth={0}
                  project={project}
                  onFileClick={openFile}
                  expandedDirs={expandedDirs}
                  toggleDir={toggleDir}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
