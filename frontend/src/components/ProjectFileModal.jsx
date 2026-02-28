import { useState, useEffect, useCallback, useRef } from "react";
import { fetchProjectFile, updateProjectFile } from "../lib/api";
import { renderMarkdown } from "../lib/formatters";

export default function ProjectFileModal({ project, filename, onClose }) {
  const [content, setContent] = useState("");
  const [exists, setExists] = useState(true);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const textareaRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchProjectFile(project, filename);
      setExists(res.exists);
      setContent(res.content || "");
      setDraft(res.content || "");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [project, filename]);

  useEffect(() => { load(); }, [load]);

  // Escape to close (but not when editing)
  useEffect(() => {
    const handler = (e) => {
      if (e.key === "Escape") {
        if (editing) setEditing(false);
        else onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose, editing]);

  // Lock body scroll
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, []);

  // Focus textarea when entering edit mode
  useEffect(() => {
    if (editing && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [editing]);

  const handleScaffold = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await updateProjectFile(project, filename, "");
      if (res.saved) {
        setContent(res.content);
        setDraft(res.content);
        setExists(true);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const res = await updateProjectFile(project, filename, draft);
      if (res.saved) {
        setContent(res.content);
        setEditing(false);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const label = filename === "CLAUDE.md" ? "CLAUDE.md" : "PROGRESS.md";
  const hasChanges = draft !== content;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-page">
      {/* Header */}
      <div className="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-divider safe-area-pt">
        <button
          type="button"
          onClick={() => { if (editing && hasChanges) { if (confirm("Discard unsaved changes?")) { setEditing(false); setDraft(content); onClose(); } } else onClose(); }}
          className="w-8 h-8 flex items-center justify-center rounded-lg hover:bg-input transition-colors"
        >
          <svg className="w-5 h-5 text-label" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        <h2 className="text-base font-bold text-heading flex-1 truncate">{label}</h2>

        {exists && !loading && (
          <>
            {editing ? (
              <>
                <button
                  type="button"
                  onClick={() => { setEditing(false); setDraft(content); }}
                  className="px-3 py-1.5 text-xs font-medium rounded-lg bg-zinc-500/15 text-label hover:bg-zinc-500/25 transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={saving || !hasChanges}
                  className="px-3 py-1.5 text-xs font-medium rounded-lg bg-cyan-500/15 text-cyan-400 hover:bg-cyan-500/25 transition-colors disabled:opacity-40"
                >
                  {saving ? "Saving..." : "Save"}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="px-3 py-1.5 text-xs font-medium rounded-lg bg-zinc-500/15 text-label hover:bg-zinc-500/25 transition-colors"
              >
                Edit
              </button>
            )}
          </>
        )}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center h-40 text-label text-sm">Loading...</div>
        ) : error ? (
          <div className="p-4 text-red-400 text-sm">{error}</div>
        ) : !exists ? (
          <div className="flex flex-col items-center justify-center h-64 gap-4">
            <p className="text-label text-sm">{filename} does not exist in this project.</p>
            <button
              type="button"
              onClick={handleScaffold}
              disabled={saving}
              className="px-4 py-2 text-sm font-medium rounded-lg bg-cyan-500/15 text-cyan-400 hover:bg-cyan-500/25 transition-colors disabled:opacity-40"
            >
              {saving ? "Generating..." : `Generate ${filename}`}
            </button>
          </div>
        ) : editing ? (
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            spellCheck={false}
            className="w-full h-full min-h-[calc(100vh-60px)] p-4 bg-page text-body text-sm font-mono leading-relaxed resize-none outline-none"
          />
        ) : (
          <div className="p-4 max-w-3xl mx-auto">
            {renderMarkdown(content, project)}
          </div>
        )}
      </div>
    </div>
  );
}
