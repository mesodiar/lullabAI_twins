import { Clock, RefreshCw, Sparkles, TriangleAlert, Volume2 } from "lucide-react";
import { useCallback, useEffect, useId, useState } from "react";
import type { SleepQuality, SleepSummaryResponse } from "../lib/types";

const WINDOW_OPTIONS = [
  { hours: 6, label: "6h" },
  { hours: 24, label: "24h" },
  { hours: 72, label: "3d" },
] as const;

const QUALITY_LABELS: Record<SleepQuality, string> = {
  RESTFUL: "Restful",
  FAIR: "Fair",
  RESTLESS: "Restless",
  INSUFFICIENT_DATA: "Limited data",
};

const TIME_FORMAT = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });

function formatDuration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hours <= 0) return `${mins}m`;
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
}

interface SleepSummaryProps {
  babyId: string;
  bedLabel: string;
  edgeBaseUrl: string;
  cloudReviewsEnabled: boolean;
}

export function SleepSummary({ babyId, bedLabel, edgeBaseUrl, cloudReviewsEnabled }: SleepSummaryProps) {
  const [hours, setHours] = useState<number>(24);
  const [data, setData] = useState<SleepSummaryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState("");
  const headingId = useId();
  const baseUrl = edgeBaseUrl.replace(/\/$/, "");

  const fetchSummary = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch(
        `${baseUrl}/sleep-summary?baby_id=${encodeURIComponent(babyId)}&hours=${hours}`,
        { signal }
      );
      if (!response.ok) throw new Error("Edge could not load the sleep summary.");
      const next: SleepSummaryResponse = await response.json();
      if (!signal?.aborted) {
        setData(next);
        setError("");
      }
    } catch (failure) {
      if (signal?.aborted) return;
      setError(failure instanceof Error ? failure.message : "Sleep summary unavailable.");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [babyId, baseUrl, hours]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void fetchSummary(controller.signal);
    return () => controller.abort();
  }, [fetchSummary]);

  const isPendingAi = data?.saved_summary?.status === "PENDING_AI";
  useEffect(() => {
    if (!isPendingAi) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      void fetchSummary(controller.signal);
    }, 2500);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [fetchSummary, isPendingAi]);

  const handleGenerate = async () => {
    if (requesting) return;
    setRequesting(true);
    setError("");
    try {
      const response = await fetch(`${baseUrl}/sleep-summary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ baby_id: babyId, hours }),
      });
      const result = await response.json().catch(() => null);
      if (!response.ok || !result?.stats) {
        throw new Error(result?.error || "Edge could not generate the sleep summary.");
      }
      setData(result);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not generate sleep summary.");
    } finally {
      setRequesting(false);
    }
  };

  const stats = data?.stats;
  const saved = data?.saved_summary;
  const hasGeminiSummary = saved?.status === "COMPLETED" && Boolean(saved.ai_summary);
  const activeSummary = hasGeminiSummary ? saved?.ai_summary : data?.local_summary;
  const canUseGemini = data ? data.cloud_reviews_enabled : cloudReviewsEnabled;
  const soundCount = stats ? stats.cry_count + stats.dog_vocalization_count : 0;
  const buckets = data?.hourly_activity ?? [];
  const maxBucket = Math.max(1, ...buckets.map((bucket) => bucket.total));
  const analyzedLabel = saved?.analyzed_at
    ? TIME_FORMAT.format(new Date(saved.analyzed_at))
    : null;

  return (
    <section className="sleep-summary" aria-labelledby={headingId}>
      <div className="section-bar sleep-summary-bar">
        <div className="sleep-summary-title">
          <h2 id={headingId}>Sleep summary</h2>
          <span className="section-caption">{bedLabel}</span>
        </div>
        <div className="sleep-summary-controls">
          <div className="window-pills" role="group" aria-label="Summary time range">
            {WINDOW_OPTIONS.map((option) => (
              <button
                key={option.hours}
                type="button"
                aria-pressed={hours === option.hours}
                onClick={() => setHours(option.hours)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="menu-button summary-action"
            onClick={handleGenerate}
            disabled={requesting || isPendingAi}
          >
            {canUseGemini ? <Sparkles aria-hidden="true" /> : <RefreshCw aria-hidden="true" />}
            {isPendingAi
              ? "Summarizing…"
              : requesting
              ? "Requesting…"
              : canUseGemini
              ? "Summarize with AI"
              : "Refresh"}
          </button>
        </div>
      </div>

      <div className="summary-card">
        {error && !data ? (
          <p className="section-caption" role="alert">{error}</p>
        ) : loading && !data ? (
          <p className="section-caption">Loading sleep summary…</p>
        ) : data && stats && activeSummary ? (
          <>
            <dl className="summary-stats">
              <div className="summary-stat">
                <dt><Clock aria-hidden="true" /> Longest quiet</dt>
                <dd>{formatDuration(stats.longest_quiet_minutes)}</dd>
              </div>
              <div className="summary-stat">
                <dt><Volume2 aria-hidden="true" /> Sounds</dt>
                <dd>{soundCount}</dd>
              </div>
              <div className="summary-stat" data-attention={stats.safety_alert_count > 0 ? "true" : undefined}>
                <dt><TriangleAlert aria-hidden="true" /> Safety alerts</dt>
                <dd>{stats.safety_alert_count}</dd>
              </div>
            </dl>

            {buckets.length > 0 && (
              <div className="summary-timeline" aria-label={`Activity over the last ${hours} hours`}>
                <div className="timeline-bars">
                  {buckets.map((bucket) => {
                    const heightPct = bucket.total === 0 ? 10 : Math.max(22, Math.round((bucket.total / maxBucket) * 100));
                    const tone = bucket.safety > 0 ? "safety" : bucket.sound > 0 ? "sound" : bucket.total > 0 ? "motion" : "quiet";
                    return (
                      <div
                        key={bucket.hour_start}
                        className="timeline-bar-slot"
                        title={`${bucket.label}: ${bucket.total} event${bucket.total === 1 ? "" : "s"}`}
                      >
                        <span className="timeline-bar" data-tone={tone} style={{ height: `${heightPct}%` }} />
                      </div>
                    );
                  })}
                </div>
                <div className="timeline-axis">
                  <span>{buckets[0]?.label}</span>
                  <span>{buckets[Math.floor(buckets.length / 2)]?.label}</span>
                  <span>{buckets[buckets.length - 1]?.label}</span>
                </div>
              </div>
            )}

            <div className="summary-narrative">
              <div className="summary-badges">
                <span className="summary-pill" data-quality={activeSummary.sleep_quality}>
                  {QUALITY_LABELS[activeSummary.sleep_quality]}
                </span>
                <span className="summary-source">
                  {hasGeminiSummary
                    ? `Gemini AI${analyzedLabel ? ` · ${analyzedLabel}` : ""}`
                    : isPendingAi
                    ? "Gemini summarizing…"
                    : "Local summary"}
                </span>
              </div>
              <h3 lang="th">{activeSummary.headline_th}</h3>
              <p lang="th">{activeSummary.summary_th}</p>
              {activeSummary.key_patterns_th.length > 0 && (
                <ul className="summary-list" lang="th">
                  {activeSummary.key_patterns_th.map((pattern, index) => (
                    <li key={index}>{pattern}</li>
                  ))}
                </ul>
              )}
              {activeSummary.twins_insight_th && (
                <p className="summary-twins" lang="th">
                  <strong>Twins insight:</strong> {activeSummary.twins_insight_th}
                </p>
              )}
              {activeSummary.recommendations_th.length > 0 && (
                <div className="summary-recs" lang="th">
                  <strong>Next sleep tips</strong>
                  <ul>
                    {activeSummary.recommendations_th.map((tip, index) => (
                      <li key={index}>{tip}</li>
                    ))}
                  </ul>
                </div>
              )}
              {!canUseGemini && (
                <p className="section-caption">
                  Enable Settings → Event analysis → GCP review to generate Vertex AI Gemini summaries.
                </p>
              )}
              {saved?.status === "UNAVAILABLE" && (
                <p className="section-caption">Previous Gemini summary request expired. Try again.</p>
              )}
              {error && <p className="section-caption" role="alert">{error}</p>}
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}
