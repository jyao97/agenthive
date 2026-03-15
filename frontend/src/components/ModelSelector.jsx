import { MODEL_OPTIONS } from "../lib/constants";

export default function ModelSelector({ value, onChange }) {
  return (
    <div className="flex rounded-lg bg-elevated p-0.5">
      {MODEL_OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={`whitespace-nowrap px-2 py-1.5 rounded-md text-xs font-medium transition-colors ${
            value === opt.value
              ? "bg-cyan-600 text-white shadow-sm"
              : "text-body hover:text-heading"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
