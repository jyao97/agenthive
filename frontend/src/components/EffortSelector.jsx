const LEVELS = ["low", "medium", "high", "max"];

export default function EffortSelector({ value, onChange }) {
  const activeIdx = LEVELS.indexOf(value);
  return (
    <div className="flex rounded-lg bg-elevated p-0.5" title={value}>
      {LEVELS.map((lvl, i) => (
        <button
          key={lvl}
          type="button"
          onClick={() => onChange(lvl)}
          className="flex-1 px-1 py-1.5 rounded-md flex items-center justify-center cursor-pointer"
        >
          <span
            className={`block w-full rounded-[2px] transition-colors ${
              i <= activeIdx ? "bg-cyan-500" : "bg-current/15"
            }`}
            style={{ aspectRatio: "1" }}
          />
        </button>
      ))}
    </div>
  );
}
