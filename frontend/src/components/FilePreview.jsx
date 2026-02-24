import { useState, useCallback } from "react";

// --- CSV parser (handles quoted fields) ---

function parseCSV(text) {
  const rows = [];
  let i = 0;
  while (i < text.length) {
    const row = [];
    while (i < text.length) {
      if (text[i] === '"') {
        // Quoted field
        i++;
        let field = "";
        while (i < text.length) {
          if (text[i] === '"') {
            if (i + 1 < text.length && text[i + 1] === '"') {
              field += '"';
              i += 2;
            } else {
              i++; // closing quote
              break;
            }
          } else {
            field += text[i];
            i++;
          }
        }
        row.push(field);
      } else {
        // Unquoted field
        let field = "";
        while (i < text.length && text[i] !== "," && text[i] !== "\n" && text[i] !== "\r") {
          field += text[i];
          i++;
        }
        row.push(field);
      }
      if (i < text.length && text[i] === ",") {
        i++;
      } else {
        break;
      }
    }
    // Skip line endings
    if (i < text.length && text[i] === "\r") i++;
    if (i < text.length && text[i] === "\n") i++;
    if (row.length > 0 && !(row.length === 1 && row[0] === "")) {
      rows.push(row);
    }
  }
  return rows;
}

// --- Image Preview ---

function ImagePreview({ src, filename }) {
  const [error, setError] = useState(false);
  const [lightbox, setLightbox] = useState(false);

  if (error) return null;

  return (
    <>
      <div className="group cursor-pointer" onClick={() => setLightbox(true)}>
        <img
          src={src}
          alt={filename}
          loading="lazy"
          onError={() => setError(true)}
          className="max-h-48 max-w-full rounded-lg border border-divider object-contain"
        />
        <p className="text-xs text-dim mt-1 truncate max-w-[240px]">{filename}</p>
      </div>

      {lightbox && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
          onClick={() => setLightbox(false)}
        >
          <img
            src={src}
            alt={filename}
            className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain"
          />
        </div>
      )}
    </>
  );
}

// --- Video Preview ---

function VideoPreview({ src, filename }) {
  const [error, setError] = useState(false);

  if (error) return null;

  return (
    <div>
      <video
        src={src}
        controls
        preload="metadata"
        onError={() => setError(true)}
        className="max-h-64 max-w-full rounded-lg border border-divider"
      />
      <p className="text-xs text-dim mt-1 truncate max-w-[240px]">{filename}</p>
    </div>
  );
}

// --- CSV Preview ---

function CsvPreview({ src, filename }) {
  const [state, setState] = useState("idle"); // idle | loading | loaded | error
  const [rows, setRows] = useState([]);
  const [totalRows, setTotalRows] = useState(0);

  const loadCsv = useCallback(async () => {
    setState("loading");
    try {
      const res = await fetch(src);
      if (!res.ok) throw new Error("fetch failed");
      const text = await res.text();
      const parsed = parseCSV(text);
      setTotalRows(parsed.length > 0 ? parsed.length - 1 : 0); // exclude header
      setRows(parsed.slice(0, 21)); // header + 20 data rows
      setState("loaded");
    } catch {
      setState("error");
    }
  }, [src]);

  if (state === "error") {
    return (
      <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-red-500/10 text-red-400 text-xs">
        <span>Failed to load</span>
        <span className="text-dim truncate max-w-[160px]">{filename}</span>
      </div>
    );
  }

  if (state === "idle" || state === "loading") {
    return (
      <button
        type="button"
        onClick={loadCsv}
        disabled={state === "loading"}
        className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-elevated hover:bg-input text-sm text-label transition-colors disabled:opacity-50"
      >
        <svg className="w-4 h-4 text-dim" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 10h18M3 14h18M3 6h18M3 18h18" />
        </svg>
        {state === "loading" ? "Loading..." : filename}
      </button>
    );
  }

  // loaded
  const header = rows[0] || [];
  const data = rows.slice(1);

  return (
    <div className="space-y-1">
      <p className="text-xs text-dim truncate max-w-[240px]">{filename}</p>
      <div className="overflow-x-auto rounded-lg border border-divider max-h-72">
        <table className="text-xs text-body w-full border-collapse">
          <thead className="sticky top-0 bg-elevated">
            <tr>
              {header.map((col, i) => (
                <th key={i} className="px-2 py-1.5 text-left font-medium text-heading whitespace-nowrap border-b border-divider">
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((row, ri) => (
              <tr key={ri} className="border-b border-divider last:border-0">
                {row.map((cell, ci) => (
                  <td key={ci} className="px-2 py-1 whitespace-nowrap">{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {totalRows > 20 && (
        <p className="text-xs text-dim">Showing 20 of {totalRows} rows</p>
      )}
    </div>
  );
}

// --- Main component ---

export default function FileAttachments({ attachments }) {
  if (!attachments || attachments.length === 0) return null;

  return (
    <div className="flex flex-col gap-2 mt-1.5">
      {attachments.map((att) => {
        const filename = att.path.split("/").pop();
        if (att.type === "image") {
          return <ImagePreview key={att.path} src={att.resolvedUrl} filename={filename} />;
        }
        if (att.type === "video") {
          return <VideoPreview key={att.path} src={att.resolvedUrl} filename={filename} />;
        }
        if (att.type === "csv") {
          return <CsvPreview key={att.path} src={att.resolvedUrl} filename={filename} />;
        }
        return null;
      })}
    </div>
  );
}
