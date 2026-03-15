const LEVELS = [
  ["low", "L"],
  ["medium", "M"],
  ["high", "H"],
  ["max", "Max"],
];

export default function EffortSelector({ value, onChange }) {
  return (
    <div className="flex shrink-0 rounded-lg bg-elevated p-0.5">
      {LEVELS.map(([lvl, label]) => (
        <button
          key={lvl}
          type="button"
          onClick={() => onChange(lvl)}
          title={lvl}
          className={`whitespace-nowrap px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
            value === lvl
              ? "bg-cyan-600 text-white shadow-sm"
              : "text-body hover:text-heading"
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
