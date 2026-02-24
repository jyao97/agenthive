import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { authCheck, authLogin, authSetPassword, setAuthToken } from "../lib/api";

export default function LoginPage() {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [needsSetup, setNeedsSetup] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [shake, setShake] = useState(false);

  useEffect(() => {
    authCheck()
      .then((r) => {
        if (r.needs_setup) {
          setNeedsSetup(true);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");

    if (needsSetup) {
      if (password !== confirmPassword) {
        setError("Passwords don't match");
        triggerShake();
        return;
      }
      if (password.length < 4) {
        setError("Password must be at least 4 characters");
        triggerShake();
        return;
      }
    }

    setSubmitting(true);
    try {
      const res = needsSetup
        ? await authSetPassword(password)
        : await authLogin(password);
      setAuthToken(res.token);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err.message || "Login failed");
      triggerShake();
    } finally {
      setSubmitting(false);
    }
  };

  const triggerShake = () => {
    setShake(true);
    setTimeout(() => setShake(false), 500);
  };

  if (loading) {
    return (
      <div className="fixed inset-0 z-50 bg-page/80 backdrop-blur-xl flex items-center justify-center">
        <div className="animate-pulse text-dim">Loading...</div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Frosted glass background */}
      <div className="absolute inset-0 bg-page/60 backdrop-blur-2xl" />

      {/* Subtle animated gradient underneath */}
      <div className="absolute inset-0 opacity-30">
        <div
          className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] rounded-full"
          style={{
            background: "radial-gradient(circle, rgba(6,182,212,0.15) 0%, transparent 70%)",
          }}
        />
      </div>

      {/* Lock + form card */}
      <div
        className={`relative z-10 w-full max-w-xs mx-4 ${shake ? "animate-shake" : ""}`}
      >
        {/* Lock icon */}
        <div className="flex justify-center mb-6">
          <div className="relative">
            {/* Glow ring */}
            <div className="absolute inset-0 rounded-full bg-cyan-500/20 blur-xl scale-150" />
            <div className="relative w-20 h-20 rounded-full bg-surface/80 border border-divider backdrop-blur-sm flex items-center justify-center shadow-lg">
              <svg
                className="w-9 h-9 text-cyan-400"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.5}
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z"
                />
              </svg>
            </div>
          </div>
        </div>

        {/* Title */}
        <div className="text-center mb-6">
          <h1 className="text-lg font-semibold text-heading">AgentHive</h1>
          <p className="text-sm text-dim mt-1">
            {needsSetup ? "Set a password to get started" : "Locked"}
          </p>
        </div>

        {/* Glass form card */}
        <div className="rounded-2xl bg-surface/60 backdrop-blur-md border border-divider/50 p-5 shadow-lg">
          <form onSubmit={handleSubmit} className="space-y-3">
            <input
              type="password"
              value={password}
              onChange={(e) => { setPassword(e.target.value); setError(""); }}
              placeholder={needsSetup ? "New password" : "Password"}
              autoFocus
              className="w-full px-4 py-3 rounded-xl bg-page/50 border border-divider text-heading placeholder-dim focus:outline-none focus:ring-2 focus:ring-cyan-500/40 focus:border-cyan-500 transition-colors"
            />

            {needsSetup && (
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => { setConfirmPassword(e.target.value); setError(""); }}
                placeholder="Confirm password"
                className="w-full px-4 py-3 rounded-xl bg-page/50 border border-divider text-heading placeholder-dim focus:outline-none focus:ring-2 focus:ring-cyan-500/40 focus:border-cyan-500 transition-colors"
              />
            )}

            {error && (
              <p className="text-red-400 text-xs text-center">{error}</p>
            )}

            <button
              type="submit"
              disabled={submitting || !password}
              className="w-full py-3 rounded-xl font-medium transition-all bg-cyan-600 text-white hover:bg-cyan-500 disabled:opacity-40 disabled:cursor-not-allowed active:scale-[0.98]"
            >
              {submitting
                ? "..."
                : needsSetup
                  ? "Set Password"
                  : "Unlock"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
