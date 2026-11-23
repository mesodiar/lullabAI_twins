import { Activity, AudioLines, ChevronDown, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";
import type { LullabAIEvent } from "../lib/types";
import { alertTitle, isImportantAlert, isPetCoverAlert } from "../localAlerts";

const RECENT_EVENT_LIMIT = 3;
const TIME_FORMAT = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
const DATE_FORMAT = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });

interface EventFeedProps {
  events: LullabAIEvent[];
  edgeBaseUrl: string;
}

export function EventFeed({ events, edgeBaseUrl }: EventFeedProps) {
  const [showOlder, setShowOlder] = useState(false);
  const historyId = useId();
  const headingId = useId();
  const today = new Date().toDateString();
  const alerts = events
    .filter(isImportantAlert)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const hasOlder = alerts.length > RECENT_EVENT_LIMIT;
  const visibleAlerts = showOlder ? alerts : alerts.slice(0, RECENT_EVENT_LIMIT);

  return (
    <section className="history" aria-labelledby={headingId}>
      <div className="section-bar">
        <h2 id={headingId}>Recent events</h2>
        {alerts.length > 0 && alerts.every((event) => new Date(event.created_at).toDateString() === today) && (
          <span className="section-caption">Today</span>
        )}
      </div>
      <div id={historyId}>
        {visibleAlerts.map((event) => {
          const analysis = event.ai_analysis;
          const petCover = isPetCoverAlert(event);
          const urgent = petCover || event.trigger_type === "BAND_ROLLOVER" ||
            event.trigger_type === "BREATHING_ANOMALY" || analysis?.safety_alert || analysis?.urgency === "CRITICAL";
          const label = event.simulated ? "Simulated" : urgent ? "Check now" :
            event.cloud_review === "REVIEWED" ? "Reviewed by Gemini" : analysis ? "Reviewed" : "Detected locally";
          const soundDescription = event.edge_metrics.audio_kind === "DOG_VOCALIZATION"
            ? "The detector heard a sound that resembled a dog vocalization."
            : "The detector heard a sound that resembled crying.";
          const Icon = urgent ? TriangleAlert : event.trigger_type === "CRY" ? AudioLines : Activity;
          const created = new Date(event.created_at);
          const time = Number.isNaN(created.getTime())
            ? "Unknown time"
            : `${created.toDateString() === today ? "" : DATE_FORMAT.format(created) + " "}${TIME_FORMAT.format(created)}`;
          const video = event.media?.local_video_path;
          const image = event.media?.local_snapshot_path;
          const localMedia = video || image;

          return (
            <details className="event" key={event.event_id}>
              <summary>
                <span className={`event-icon${urgent || event.trigger_type === "CRY" ? " attention" : ""}`}>
                  <Icon aria-hidden="true" />
                </span>
                <span className="event-summary">
                  <strong>{alertTitle(event)}</strong>
                  <span>
                    <time dateTime={event.created_at}>{time}</time> · {label}
                  </span>
                </span>
                <ChevronDown className="chevron" aria-hidden="true" />
              </summary>
              <div className="event-detail">
                {event.cloud_review === "PENDING" && <p>Gemini review pending.</p>}
                {event.cloud_review === "UNAVAILABLE" && <p>Gemini review unavailable.</p>}
                {event.media?.local_video_unavailable && <p>Video is no longer stored on Edge.</p>}
                {analysis?.title_th && <p className="event-result" lang="th">{analysis.title_th}</p>}
                <p lang={analysis?.reason_hypothesis_th ? "th" : undefined}>
                  {analysis?.reason_hypothesis_th || (petCover
                    ? "The detector spotted what may be a blanket over the dog's face."
                    : event.trigger_type === "CRY" ? soundDescription : label)}
                </p>
                {analysis?.recommended_action_th && <p lang="th">{analysis.recommended_action_th}</p>}
                {localMedia?.startsWith("/media/") && (
                  <a className="text-action" href={edgeBaseUrl.replace(/\/$/, "") + localMedia} target="_blank" rel="noreferrer">
                    {video ? "View clip" : "View snapshot"}
                  </a>
                )}
                {video && event.media?.local_video_has_audio !== true && (
                  <p>Audio was not recorded for this clip.</p>
                )}
              </div>
            </details>
          );
        })}
        {!visibleAlerts.length && <p className="section-caption">No events yet</p>}
      </div>
      {hasOlder && (
        <button type="button" className="text-action history-toggle" aria-controls={historyId}
          aria-expanded={showOlder} onClick={() => setShowOlder(!showOlder)}>
          {showOlder ? "See less" : "See more"} <ChevronDown aria-hidden="true" />
        </button>
      )}
    </section>
  );
}
