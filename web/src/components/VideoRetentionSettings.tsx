import { ChevronDown } from "lucide-react";
import { useState, type FormEvent } from "react";

const RETENTION_OPTIONS = [1, 3, 7, 14, 30, 90];

interface VideoRetentionSettingsProps {
  days: number | null | undefined;
  setupConfigured: boolean;
  onChange: (days: number | null) => void;
}

export function VideoRetentionSettings({ days, setupConfigured, onChange }: VideoRetentionSettingsProps) {
  const [choice, setChoice] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const current = days == null ? "" : String(days);
  const next = choice ?? current;
  const secure = window.isSecureContext;
  const canChange = days !== undefined && setupConfigured && secure;

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canChange || next === current || saving || !code.trim()) return;
    setSaving(true);
    setError("");
    try {
      const response = await fetch("/edge/video-retention", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Setup-Code": code.trim() },
        body: JSON.stringify({ days: next ? Number(next) : null }),
      });
      const result = await response.json().catch(() => null);
      if (!response.ok || !result || (result.days !== null && typeof result.days !== "number")) {
        throw new Error(result?.error || "Edge could not save video retention.");
      }
      onChange(result.days);
      setChoice(null);
      setCode("");
      setFeedback(result.days === null ? "Automatic video deletion is off." : "Retention saved. Cleanup is scheduled.");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Edge could not save video retention.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <details className="menu-detail edge-setting">
      <summary>
        <span>Video retention</span>
        <span className="edge-setting-status">
          <strong>{days === undefined ? "Edge unavailable" : days === null ? "Keep forever" : `${days} ${days === 1 ? "day" : "days"}`}</strong>
          <ChevronDown aria-hidden="true" />
        </span>
      </summary>
      <form className="edge-setting-form" onSubmit={save}>
        <label>
          Keep videos for
          <select value={canChange ? next : current} disabled={!canChange || saving} onChange={(event) => {
            setChoice(event.target.value);
            setCode("");
            setFeedback("");
            setError("");
          }}>
            <option value="">Keep forever</option>
            {RETENTION_OPTIONS.map((option) => (
              <option key={option} value={option}>{option} {option === 1 ? "day" : "days"}</option>
            ))}
          </select>
        </label>
        {canChange ? <p>Applies to local videos for both beds. Snapshots and event history stay available.</p> :
          <p>{days === undefined ? "Connect to an Edge detector with video retention." :
            !setupConfigured ? "Set the Edge setup code to change retention." :
            "Open the dashboard over HTTPS or localhost to change retention."}</p>}
        {canChange && next !== current && (
          <>
            {next && <p>Videos already older than {next} {next === "1" ? "day" : "days"} will be permanently deleted.</p>}
            <label>
              Edge setup code
              <input type="password" autoComplete="off" value={code} onChange={(event) => setCode(event.target.value)} />
            </label>
            <button className="menu-button" type="submit" disabled={saving || !code.trim()}>
              {saving ? "Saving…" : next ? "Apply and delete expired videos" : "Apply retention"}
            </button>
          </>
        )}
        {feedback && <p role="status">{feedback}</p>}
        {error && <p role="alert">{error}</p>}
      </form>
    </details>
  );
}
