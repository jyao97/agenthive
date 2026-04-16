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
        if (r.authenticated) {
          navigate("/", { replace: true });
        } else if (r.needs_setup) {
          setNeedsSetup(true);
        }
      })
      .catch((err) => console.error('authCheck failed:', err))
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
        {/* Xylocopa app icon — direct SVG, no wrapper */}
        <div className="flex justify-center mb-6">
          <div className="relative">
            {/* Ambient cyan glow */}
            <div className="absolute inset-0 bg-cyan-500/20 blur-2xl scale-150" />
            <img
              src="/icon.svg"
              alt="Xylocopa"
              className="relative w-24 h-24 rounded-[22%] select-none"
              draggable={false}
            />
          </div>
        </div>

        {/* Title */}
        <div className="text-center mb-6">
          <h1 className="text-lg font-semibold text-heading">Xylocopa</h1>
          <p className="text-sm text-dim mt-1">
            {needsSetup ? "Set a password to get started" : "Locked"}
          </p>
        </div>

        {/* Glass form card */}
        <div className="rounded-2xl bg-surface/60 backdrop-blur-md border border-divider/50 p-5 shadow-lg">
          <form onSubmit={handleSubmit} autoComplete="on" className="space-y-3">
            {/* Hidden username for iOS autofill credential matching */}
            <input
              type="text"
              name="username"
              autoComplete="username"
              value="user"
              readOnly
              aria-hidden="true"
              tabIndex={-1}
              style={{ position: "absolute", width: 0, height: 0, overflow: "hidden", opacity: 0 }}
            />
            <input
              type="password"
              name="password"
              id="password"
              autoComplete={needsSetup ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => { setPassword(e.target.value); setError(""); }}
              placeholder={needsSetup ? "New password" : "Password"}
              autoFocus
              className="w-full px-4 py-3 rounded-xl bg-page/50 border border-divider text-heading placeholder-dim focus:outline-none focus:ring-2 focus:ring-cyan-500/40 focus:border-cyan-500 transition-colors"
            />

            {needsSetup && (
              <input
                type="password"
                name="confirm-password"
                id="confirm-password"
                autoComplete="new-password"
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

        {/* Cert install hint for mobile */}
        <p className="text-center text-xs text-dim mt-4">
          First time on this device?{" "}
          <a
            href="/cert-guide"
            className="text-cyan-400 hover:underline"
          >
            Install CA certificate
          </a>
        </p>
      </div>
    </div>
  );
}
