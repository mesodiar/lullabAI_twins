import { useState, type FormEvent } from "react";
import { ChevronDown } from "lucide-react";

export interface CloudReviewStatus {
  available: boolean;
  enabled: boolean;
  setupConfigured: boolean;
}

interface CloudReviewSettingsProps {
  status: CloudReviewStatus | null;
  onChange: (enabled: boolean) => void;
}

export function CloudReviewSettings({ status, onChange }: CloudReviewSettingsProps) {
  const [choice, setChoice] = useState<"local" | "gcp" | null>(null);
  const [code, setCode] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const active = status?.enabled ? "gcp" : "local";
  const next = choice ?? active;
  const secure = window.isSecureContext;
  const canChange = status?.available && status.setupConfigured && secure;

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canChange || next === active || saving || !code.trim()) return;
    setSaving(true);
    setError("");
    try {
      const response = await fetch("/edge/cloud-review", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Setup-Code": code.trim() },
        body: JSON.stringify({ enabled: next === "gcp" }),
      });
      const result = await response.json().catch(() => null);
      if (!response.ok || typeof result?.enabled !== "boolean") {
        throw new Error(result?.error || "Edge could not save the analysis mode.");
      }
      onChange(result.enabled);
      setChoice(null);
      setCode("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Edge could not save the analysis mode.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <details className="menu-detail cloud-review">
      <summary>
        <span>Event analysis</span>
        <span className="cloud-review-status">
          <strong>{status ? status.enabled ? "GCP review" : "Local only" : "Edge unavailable"}</strong>
          <ChevronDown aria-hidden="true" />
        </span>
      </summary>
      <form className="cloud-review-form" onSubmit={save}>
        <label>
          Analysis mode
          <select
            value={canChange ? next : active}
            disabled={!canChange || saving}
            onChange={(event) => {
              setChoice(event.target.value === "gcp" ? "gcp" : "local");
              setCode("");
              setError("");
            }}
          >
            <option value="local">Local only</option>
            <option value="gcp">GCP review</option>
          </select>
        </label>
        {canChange ? (
          <p>GCP reviews new event clips in Gemini and may incur charges.</p>
        ) : (
          <p>{!status ? "Connect to Edge to change the mode." :
            !status.available ? "Configure GCP on Edge to enable reviews." :
            !status.setupConfigured ? "Set the Edge setup code to change the mode." :
            "Open the dashboard over HTTPS or localhost to change the mode."}</p>
        )}
        {canChange && next !== active && (
          <>
            <label>
              Edge setup code
              <input type="password" autoComplete="off" value={code} onChange={(event) => setCode(event.target.value)} />
            </label>
            <button className="menu-button" type="submit" disabled={saving || !code.trim()}>
              {saving ? "Saving…" : "Apply mode"}
            </button>
          </>
        )}
        {error && <p role="alert">{error}</p>}
      </form>
    </details>
  );
}
