import { useState, useEffect } from "react";

const certSteps = [
  {
    num: 1,
    title: "Download certificate",
    desc: "",
    link: { href: "/api/cert", label: "Tap here to download" },
  },
  {
    num: 2,
    title: "Install profile",
    desc: "Settings \u2192 General \u2192 VPN & Device Management \u2192 tap the downloaded profile \u2192 Install.",
  },
  {
    num: 3,
    title: "Enable trust",
    desc: "Settings \u2192 General \u2192 About \u2192 Certificate Trust Settings \u2192 toggle on \"mkcert\" or \"agenthive\".",
  },
];

const webClipSteps = [
  {
    num: 1,
    title: "Enter server IP",
    desc: "Type the IP address you use to access AgentHive (shown in your browser's address bar).",
  },
  {
    num: 2,
    title: "Install profile",
    desc: "Tap the button below \u2192 Allow \u2192 Settings \u2192 General \u2192 VPN & Device Management \u2192 tap \"AgentHive\" \u2192 Install.",
  },
  {
    num: 3,
    title: "Done!",
    desc: "The AgentHive icon appears on your Home Screen. Tap it and set your password.",
  },
];

function StepList({ steps }) {
  return (
    <div className="space-y-4">
      {steps.map((s) => (
        <div key={s.num} className="flex gap-3">
          <div className="flex-shrink-0 w-7 h-7 rounded-full bg-cyan-600 text-white text-sm font-bold flex items-center justify-center">
            {s.num}
          </div>
          <div className="min-w-0">
            <div className="font-medium text-heading text-sm">{s.title}</div>
            <p className="text-xs text-dim mt-0.5">
              {s.desc}
              {s.link && (
                <>
                  {" "}
                  <a href={s.link.href} className="text-cyan-400 underline">
                    {s.link.label}
                  </a>
                  .
                </>
              )}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function CertGuidePage() {
  const [host, setHost] = useState(() => {
    if (typeof window === "undefined") return "";
    const h = window.location.hostname;
    return h === "localhost" || h === "127.0.0.1" ? "" : h;
  });


  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto">
      <div className="absolute inset-0 bg-page/60 backdrop-blur-2xl" />

      <div className="relative z-10 w-full max-w-sm mx-4 my-8">
        {/* Section 1: CA Certificate (must be done first) */}
        <div className="text-center mb-5">
          <h1 className="text-lg font-semibold text-heading">Step 1: Trust Certificate</h1>
          <p className="text-sm text-dim mt-1">Required for voice input, file uploads, and the app to work without warnings</p>
        </div>

        <div className="rounded-2xl bg-surface/60 backdrop-blur-md border border-divider/50 p-5 shadow-lg">
          <StepList steps={certSteps} />
        </div>

        {/* Section 2: Add to Home Screen via Web Clip */}
        <div className="text-center mt-8 mb-5">
          <h2 className="text-base font-semibold text-heading">Step 2: Add to Home Screen</h2>
          <p className="text-sm text-dim mt-1">Install as an app with the correct icon</p>
        </div>

        <div className="rounded-2xl bg-surface/60 backdrop-blur-md border border-divider/50 p-5 shadow-lg">
          <StepList steps={webClipSteps} />

          <input
            type="text"
            value={host}
            onChange={(e) => setHost(e.target.value.trim())}
            placeholder="e.g. 192.168.1.100 or 100.x.x.x"
            className="mt-4 w-full px-4 py-3 rounded-xl bg-page/50 border border-divider text-heading placeholder-dim focus:outline-none focus:ring-2 focus:ring-cyan-500/40 focus:border-cyan-500 transition-colors text-sm"
          />

          <a
            href={host ? `/api/webclip?host=${encodeURIComponent(host)}` : "#"}
            onClick={(e) => { if (!host) e.preventDefault(); }}
            className={`mt-3 w-full py-3 rounded-xl font-medium transition-all text-white text-center block ${host ? "bg-cyan-600 hover:bg-cyan-500 active:scale-[0.98]" : "bg-cyan-600/40 cursor-not-allowed"}`}
          >
            Install AgentHive App
          </a>
        </div>

        <p className="text-center mt-4">
          <a href="/login" className="text-sm text-cyan-400 hover:underline">
            Back to login
          </a>
        </p>
      </div>
    </div>
  );
}
