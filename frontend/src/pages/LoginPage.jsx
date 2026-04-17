import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { authCheck, authLogin, authSetPassword, setAuthToken } from "../lib/api";
import beeLogo from "../assets/xylocopa-bee.svg";

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
    <div className="fixed inset-0 z-50 flex items-center justify-center px-4">
      {/* Soft color blobs behind the glass — give the blur something to refract */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div
          className="absolute top-[20%] left-[30%] w-[420px] h-[420px] rounded-full opacity-60"
          style={{ background: "radial-gradient(circle, rgba(6,182,212,0.35) 0%, transparent 65%)" }}
        />
        <div
          className="absolute bottom-[15%] right-[25%] w-[380px] h-[380px] rounded-full opacity-50"
          style={{ background: "radial-gradient(circle, rgba(127,119,221,0.30) 0%, transparent 65%)" }}
        />
        <div
          className="absolute top-[55%] left-[60%] w-[300px] h-[300px] rounded-full opacity-40"
          style={{ background: "radial-gradient(circle, rgba(168,85,247,0.25) 0%, transparent 65%)" }}
        />
      </div>

      <div className={`relative z-10 w-full max-w-md ${shake ? "animate-shake" : ""}`}>
        {/* Single horizontal liquid-glass card: bee | form */}
        <div className="glass-bar rounded-3xl p-5 flex items-center gap-5">
          <img
            src={beeLogo}
            alt="Xylocopa"
            className="w-24 h-24 shrink-0 select-none drop-shadow-[0_2px_8px_rgba(0,0,0,0.15)]"
            draggable={false}
          />

          <div className="flex-1 min-w-0">
            <h1 className="text-base font-semibold text-heading leading-tight">Xylocopa</h1>
            <p className="text-xs text-dim mb-3">
              {needsSetup ? "Set a password to get started" : "Locked"}
            </p>

            <form onSubmit={handleSubmit} autoComplete="on" className="space-y-2">
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
                className="w-full px-3 py-2 rounded-lg bg-black/5 dark:bg-black/30 border-0 text-sm text-heading placeholder-dim focus:outline-none transition-shadow"
                style={{ boxShadow: "inset 0 1.5px 3px rgba(0,0,0,0.18), inset 0 -0.5px 0 rgba(255,255,255,0.4)" }}
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
                  className="w-full px-3 py-2 rounded-lg bg-black/5 dark:bg-black/30 border-0 text-sm text-heading placeholder-dim focus:outline-none transition-shadow"
                  style={{ boxShadow: "inset 0 1.5px 3px rgba(0,0,0,0.18), inset 0 -0.5px 0 rgba(255,255,255,0.4)" }}
                />
              )}

              {error && (
                <p className="text-red-400 text-xs">{error}</p>
              )}

              <button
                type="submit"
                disabled={submitting || !password}
                className="w-full py-2 rounded-lg text-sm font-medium transition-all bg-cyan-600/90 text-white hover:bg-cyan-500 disabled:opacity-40 disabled:cursor-not-allowed active:scale-[0.98]"
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
