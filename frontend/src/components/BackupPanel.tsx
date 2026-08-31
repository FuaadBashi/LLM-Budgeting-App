"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { downloadExport, runBackup, type BackupStatus } from "@/lib/api";

function age(hours: number | null): string {
  if (hours === null) return "never";
  if (hours < 1) return "less than an hour ago";
  if (hours < 48) return `${Math.round(hours)} hours ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function size(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(0)} kB`;
}

/**
 * Scheduled backup status. Plan section 14.
 *
 * The age of the newest file is the point of this panel, not the on/off switch.
 * A timer that stopped a month ago looks exactly like a working one unless
 * something reports how old the newest backup actually is.
 */
export function BackupPanel({ status }: { status: BackupStatus }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tone = !status.enabled
    ? "var(--status-warning)"
    : status.stale
      ? "var(--status-serious)"
      : "var(--status-good)";
  const mark = !status.enabled ? "▲" : status.stale ? "▲" : "✓";

  async function onRun() {
    setBusy(true);
    setError(null);
    try {
      await runBackup();
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Backup failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <h2 className="section-label mb-3">Scheduled backups</h2>
      <div className="card space-y-4 p-5" style={{ boxShadow: `inset 0 0 0 1px ${tone}` }}>
        <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
          <span aria-hidden style={{ color: tone }}>{mark}</span>{" "}
          {!status.enabled
            ? "Switched off. Nothing is written automatically."
            : status.latest === null
              ? "Switched on, but nothing has been written yet."
              : `Last backup ${age(status.age_hours)}, every ${status.interval_hours} hours.`}
          <span className="mt-1 block text-xs" style={{ color: "var(--text-muted)" }}>
            Written to {status.directory}, keeping the newest {status.keep}. The timer
            only runs while the API does — for one that does not depend on that, put
            scripts/backup.py in cron.
          </span>
        </p>

        {status.last_error && (
          <p className="text-sm" role="alert" style={{ color: "var(--status-critical)" }}>
            ✕ Last scheduled run failed: {status.last_error}
          </p>
        )}
        {error && (
          <p className="text-sm" role="alert" style={{ color: "var(--status-critical)" }}>
            ✕ {error}
          </p>
        )}

        {status.files.length > 0 && (
          <ul className="divide-y text-sm" style={{ borderColor: "var(--gridline)" }}>
            {status.files.slice(0, 5).map((f) => (
              <li key={f.name} className="flex flex-wrap items-baseline gap-x-4 gap-y-1 py-2">
                <span className="min-w-0 flex-1" style={{ color: "var(--text-secondary)" }}>
                  {new Date(f.written_at).toLocaleString()}
                  <span className="ml-2 text-xs" style={{ color: "var(--text-muted)" }}>
                    {size(f.size_bytes)}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={() => downloadExport(`/backups/${f.name}`, f.name)}
                  className="rounded-full px-3 py-1 text-xs"
                  style={{
                    color: "var(--text-secondary)",
                    boxShadow: "inset 0 0 0 1px var(--hairline-strong)",
                  }}
                >
                  Download
                </button>
              </li>
            ))}
            {status.files.length > 5 && (
              <li className="py-2 text-xs" style={{ color: "var(--text-muted)" }}>
                and {status.files.length - 5} older.
              </li>
            )}
          </ul>
        )}

        <div className="flex justify-end">
          <button
            type="button"
            disabled={busy}
            onClick={onRun}
            className="rounded-full px-4 py-2 text-sm font-medium"
            style={{ background: "var(--accent)", color: "#fff", opacity: busy ? 0.6 : 1 }}
          >
            {busy ? "Writing…" : "Back up now"}
          </button>
        </div>
      </div>
    </section>
  );
}
