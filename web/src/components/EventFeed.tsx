import { Activity, AudioLines, ChevronDown, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";
import type { LullabAIEvent } from "../lib/types";
import { alertTitle, isImportantAlert, isPetCoverAlert } from "../localAlerts";

const RECENT_EVENT_LIMIT = 3;
const TIME_FORMAT = new Intl.DateTimeFormat("th-TH", { hour: "2-digit", minute: "2-digit" });
const DATE_FORMAT = new Intl.DateTimeFormat("th-TH", { day: "numeric", month: "short" });

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
        <h2 id={headingId}>เหตุการณ์ล่าสุด</h2>
        {alerts.length > 0 && alerts.every((event) => new Date(event.created_at).toDateString() === today) && (
          <span className="section-caption">วันนี้</span>
        )}
      </div>
      <div id={historyId}>
        {visibleAlerts.map((event) => {
          const analysis = event.ai_analysis;
          const petCover = isPetCoverAlert(event);
          const urgent = petCover || event.trigger_type === "BAND_ROLLOVER" ||
            event.trigger_type === "BREATHING_ANOMALY" || analysis?.safety_alert || analysis?.urgency === "CRITICAL";
          const label = event.simulated ? "จำลอง" : urgent ? "ควรตรวจดู" : analysis ? "ตรวจสอบแล้ว" : "ตรวจพบที่เครื่อง";
          const Icon = urgent ? TriangleAlert : event.trigger_type === "CRY" ? AudioLines : Activity;
          const created = new Date(event.created_at);
          const time = Number.isNaN(created.getTime())
            ? "ไม่ทราบเวลา"
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
                {analysis?.title_th && <p className="event-result">{analysis.title_th}</p>}
                <p>
                  {analysis?.reason_hypothesis_th || (petCover
                    ? "พบผ้าทับบริเวณที่ติดตามน้องหมา"
                    : event.trigger_type === "CRY" ? "ผลประเมินเบื้องต้นจากลักษณะของเสียง" : label)}
                </p>
                {analysis?.recommended_action_th && <p>{analysis.recommended_action_th}</p>}
                {localMedia?.startsWith("/media/") && (
                  <a className="text-action" href={edgeBaseUrl.replace(/\/$/, "") + localMedia} target="_blank" rel="noreferrer">
                    {video ? "ดูคลิป" : "ดูภาพเหตุการณ์"}
                  </a>
                )}
              </div>
            </details>
          );
        })}
        {!visibleAlerts.length && <p className="section-caption">ยังไม่มีเหตุการณ์</p>}
      </div>
      {hasOlder && (
        <button type="button" className="text-action history-toggle" aria-controls={historyId}
          aria-expanded={showOlder} onClick={() => setShowOlder(!showOlder)} lang="en">
          {showOlder ? "See less" : "See more"} <ChevronDown aria-hidden="true" />
        </button>
      )}
    </section>
  );
}
