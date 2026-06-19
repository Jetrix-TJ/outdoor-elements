import { useState } from "react";
import { login } from "./api.js";

// Passcode-only access gate. The passcode is validated server-side (/api/login);
// on success the caller persists the unlocked state.
export default function Login({ onAuthed }) {
  const [code, setCode] = useState("");
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!code || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await login(code);
      onAuthed();
    } catch (e) {
      setErr(e.message || "Incorrect passcode");
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <div className="login-mark">
          <span className="material-symbols-outlined">lock</span>
        </div>
        <h1>Outdoor Elements <span className="accent">AI Takeoff</span></h1>
        <p className="muted">Enter your passcode to continue</p>
        <input
          className={`passcode-in ${err ? "bad" : ""}`}
          type="password"
          inputMode="numeric"
          autoComplete="one-time-code"
          autoFocus
          placeholder="••••"
          value={code}
          onChange={(e) => { setCode(e.target.value); setErr(null); }}
        />
        {err && <div className="login-err">⚠ {err}</div>}
        <button className="primary login-btn" type="submit" disabled={busy || !code}>
          {busy ? "Checking…" : "Unlock"}
        </button>
      </form>
    </div>
  );
}
