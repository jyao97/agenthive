import useProjects from "../hooks/useProjects";

export default function ProjectSelector({ value, onChange }) {
  const { projects, loading, error } = useProjects();

  if (loading) return <div className="text-sm text-dim">Loading projects...</div>;
  if (error) return <div className="text-sm text-red-400">{error}</div>;

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full rounded-lg bg-input border border-edge px-2.5 py-1.5 text-sm text-heading focus:border-cyan-500 focus:outline-none transition-colors"
    >
      <option value="">Select a project...</option>
      {projects.map((p) => (
        <option key={p.name} value={p.name}>
          {p.display_name || p.name}
        </option>
      ))}
    </select>
  );
}
