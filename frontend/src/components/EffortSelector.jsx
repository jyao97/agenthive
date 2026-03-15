const LEVELS = [
  ["low", "L"],
  ["medium", "M"],
  ["high", "H"],
  ["max", "M"],
];

export default function EffortSelector({ value, onChange }) {
  return (
    <div className="inline-flex shrink-0 justify-center items-center rounded-lg bg-elevated p-0.5">
      {LEVELS.map(([lvl, label]) => (
        <button
          key={lvl}
          type="button"
          onClick={() => onChange(lvl)}
          title={lvl}
          className={`flex-1 text-center px-1.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
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
