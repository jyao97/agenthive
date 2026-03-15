const LEVELS = [
  ["low",    [1, 0, 0, 0]],
  ["medium", [1, 1, 0, 0]],
  ["high",   [1, 1, 1, 0]],
  ["max",    [1, 1, 1, 1]],
];

function PowerBars({ filled, active }) {
  return (
    <span className="inline-flex gap-[2px] items-end">
      {filled.map((on, i) => (
        <span
          key={i}
          className={`inline-block w-[3px] rounded-[1px] ${
            on
              ? active ? "bg-white" : "bg-current"
              : active ? "bg-white/30" : "bg-current/20"
          }`}
          style={{ height: `${8 + i * 2}px` }}
        />
      ))}
    </span>
  );
}

export default function EffortSelector({ value, onChange }) {
  return (
    <div className="flex rounded-lg bg-elevated p-0.5">
      {LEVELS.map(([lvl, bars]) => (
        <button
          key={lvl}
          type="button"
          onClick={() => onChange(lvl)}
          title={lvl}
          className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
            value === lvl
              ? "bg-cyan-600 text-white shadow-sm"
              : "text-body hover:text-heading"
          }`}
        >
          <PowerBars filled={bars} active={value === lvl} />
        </button>
      ))}
    </div>
  );
}
