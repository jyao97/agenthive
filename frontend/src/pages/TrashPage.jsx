import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { fetchTrashFolders, deleteTrashFolder, restoreTrashFolder } from "../lib/api";
import PageHeader from "../components/PageHeader";

export default function TrashPage({ theme, onToggleTheme }) {
  const navigate = useNavigate();
  const [folders, setFolders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);

  const load = async () => {
    try {
      const data = await fetchTrashFolders();
      setFolders(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleRestore = async (name) => {
    setBusy(name);
    try {
      await restoreTrashFolder(name);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  };

  const handleDelete = async (name) => {
    if (!window.confirm(`Permanently delete "${name}"? This cannot be undone.`)) return;
    setBusy(name);
    try {
      await deleteTrashFolder(name);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  };

  const backButton = (
    <button
      type="button"
      onClick={() => navigate("/projects")}
      className="p-2 rounded-lg text-label hover:text-heading hover:bg-input transition-colors"
    >
      <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
      </svg>
    </button>
  );

  return (
    <div className="h-full flex flex-col">
      <PageHeader title="Deleted Projects" theme={theme} onToggleTheme={onToggleTheme} actions={backButton} />
      <div className="flex-1 overflow-y-auto overflow-x-hidden">
      <div className="pb-20 p-4 max-w-2xl mx-auto w-full">

        {loading && (
          <div className="flex justify-center py-12">
            <span className="text-dim text-sm animate-pulse">Loading...</span>
          </div>
        )}

        {error && (
          <div className="bg-red-950/40 border border-red-800 rounded-xl p-4 mb-4">
            <p className="text-red-400 text-sm">{error}</p>
          </div>
        )}

        {!loading && !error && folders.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-faint">
            <svg className="w-12 h-12 mb-3" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
            <p className="text-sm">Trash is empty</p>
          </div>
        )}

        <div className="space-y-2">
          {folders.map((folder) => (
            <div
              key={folder.name}
              className="flex items-center justify-between rounded-xl bg-surface shadow-card px-5 py-4"
            >
              <div className="min-w-0">
                <h3 className="text-sm font-medium text-label truncate">{folder.name}</h3>
              </div>
              <div className="shrink-0 flex items-center gap-2 ml-4">
                <button
                  type="button"
                  disabled={busy === folder.name}
                  onClick={() => handleRestore(folder.name)}
                  className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-cyan-600/20 text-cyan-400 hover:bg-cyan-600/30 disabled:opacity-50 transition-colors"
                >
                  {busy === folder.name ? "..." : "Restore"}
                </button>
                <button
                  type="button"
                  disabled={busy === folder.name}
                  onClick={() => handleDelete(folder.name)}
                  className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-red-600/20 text-red-400 hover:bg-red-600/30 disabled:opacity-50 transition-colors"
                >
                  {busy === folder.name ? "..." : "Delete"}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
      </div>
    </div>
  );
}
