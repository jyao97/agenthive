/**
 * Dismissable inline error banner — shared across task views.
 */
export default function ErrorAlert({ error, onDismiss }) {
  if (!error) return null;
  return (
    <div className="bg-red-950/40 border border-red-800 rounded-xl px-3 py-2 flex items-center justify-between">
      <p className="text-red-400 text-sm">{error}</p>
      <button type="button" onClick={onDismiss} className="text-red-400/60 hover:text-red-400 text-xs ml-2">dismiss</button>
    </div>
  );
}
