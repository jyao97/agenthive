/**
 * Reusable horizontal filter tab bar.
 *
 * @param {Array<{key:string, label:string}>} tabs
 * @param {string}   active   - Currently selected tab key
 * @param {function} onChange - Called with the new key
 * @param {Object}   [counts] - Optional map of key → number to show beside label
 */
export default function FilterTabs({ tabs, active, onChange, counts }) {
  return (
    <div className="overflow-x-auto no-scrollbar">
      <div className="flex gap-1.5 px-4 pb-3 min-w-max">
        {tabs.map((tab) => {
          const isActive = active === tab.key;
          const count = counts?.[tab.key];
          return (
            <button
              key={tab.key}
              type="button"
              onClick={() => onChange(tab.key)}
              className={`shrink-0 min-h-[36px] px-3 py-1.5 rounded-full text-sm font-medium transition-colors whitespace-nowrap ${
                isActive
                  ? "bg-cyan-600 text-white"
                  : "bg-surface text-label hover:bg-input hover:text-body"
              }`}
            >
              {tab.label}
              {count != null && (
                <span className={`ml-1.5 text-xs ${isActive ? "text-cyan-200" : "text-faint"}`}>
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
