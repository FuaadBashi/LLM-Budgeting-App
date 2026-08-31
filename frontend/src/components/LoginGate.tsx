"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { login } from "@/lib/api";

/**
 * The login screen, shown when the API reports a session is required.
 *
 * Deliberately says nothing about what a correct password looks like and gives
 * one message for every failure — there is a single field, so a more specific
 * error could only help someone guessing.
 */
export function LoginGate() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const password = new FormData(form).get("password");
    setError(null);
    setBusy(true);
    try {
      await login(String(password ?? ""));
      form.reset();
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main
      className="flex min-h-dvh items-center justify-center p-6"
      style={{ background: "var(--page-plane)" }}
    >
      <div className="card w-full max-w-sm p-6">
        <h1
          className="text-lg font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          Personal Finance OS
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-muted)" }}>
          Enter your password to continue.
        </p>

        <form onSubmit={onSubmit} className="mt-5 space-y-4">
          <label
            className="block text-sm font-medium"
            style={{ color: "var(--text-secondary)" }}
          >
            <span className="mb-1.5 block">Password</span>
            <input
              type="password"
              name="password"
              required
              autoFocus
              autoComplete="current-password"
              className="form-control"
            />
          </label>

          {error && (
            <p className="text-sm" role="alert" style={{ color: "var(--status-critical)" }}>
              ✕ {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-full px-4 py-3 text-sm font-medium"
            style={{
              background: "var(--accent)",
              color: "#ffffff",
              opacity: busy ? 0.6 : 1,
            }}
          >
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="mt-5 text-xs" style={{ color: "var(--text-muted)" }}>
          Forgotten it? There is no recovery — set a new one with{" "}
          <code className="font-mono">scripts/set_password.py</code> and restart
          the API. Changing the password ends every existing session.
        </p>
      </div>
    </main>
  );
}
