const LEVELS = ["low", "medium", "high", "max"];

export default function EffortSelector({ value, onChange }) {
  const activeIdx = LEVELS.indexOf(value);
  return (
    <div
      className="inline-flex gap-[4px] items-center rounded-lg bg-elevated px-2.5 py-1.5 cursor-pointer"
      title={value}
    >
      {LEVELS.map((lvl, i) => (
        <span
          key={lvl}
          onClick={() => onChange(lvl)}
          className={`block w-[10px] h-[10px] rounded-[2px] cursor-pointer transition-colors ${
            i <= activeIdx ? "bg-cyan-500" : "bg-current/15"
          }`}
        />
      ))}
    </div>
  );
}
