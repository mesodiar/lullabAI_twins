import { Maximize, Minimize, VideoOff, Volume2, VolumeX } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { BabyProfile } from "../lib/types";
import { connectGo2RTCStream, type StreamProtocol } from "../lib/webrtc";

type ImageSource = "EDGE_CAM" | "GO2RTC" | "MOCK" | "STATIC" | "UNAVAILABLE";

interface LivePlayerProps {
  baby: BabyProfile;
  cameraConfigured: boolean;
  useMock: boolean;
  mockFeedVersion: number;
  showDetection: boolean;
  go2rtcUrl: string;
  edgeBaseUrl: string;
  mockCameraUrl: string;
  onConnectionChange: (connection: string) => void;
  children?: ReactNode;
}

export function LivePlayer({
  baby, cameraConfigured, useMock, mockFeedVersion, showDetection, go2rtcUrl, edgeBaseUrl, mockCameraUrl, onConnectionChange, children,
}: LivePlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [protocol, setProtocol] = useState<StreamProtocol>("CONNECTING");
  const [imageSource, setImageSource] = useState<ImageSource>("EDGE_CAM");
  const [ready, setReady] = useState(false);
  const [muted, setMuted] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [frameAspectRatio, setFrameAspectRatio] = useState(16 / 9);
  const mockVersion = useMock ? mockFeedVersion : 0;

  useEffect(() => {
    setReady(false);
    if (useMock) {
      setImageSource("MOCK");
      setProtocol("MJPEG_MOCK");
      return;
    }
    const video = videoRef.current;
    if (!cameraConfigured || !video) return;
    setImageSource("EDGE_CAM");
    const connection = connectGo2RTCStream(video, go2rtcUrl, baby.stream_name, setProtocol, () => {
      setReady(false);
      setProtocol("MJPEG_MOCK");
    });
    return () => {
      connection.close();
      video.srcObject = null;
      const source = video.getAttribute("src");
      if (source?.startsWith("blob:")) URL.revokeObjectURL(source);
      if (source) video.removeAttribute("src");
    };
  }, [baby.stream_name, cameraConfigured, go2rtcUrl, useMock, mockVersion]);

  useEffect(() => {
    if (!expanded) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [expanded]);

  const imageMode = protocol === "MJPEG_MOCK";
  const usingSample = imageMode && (imageSource === "MOCK" || imageSource === "STATIC");
  const unavailable = (!cameraConfigured && !useMock) || (imageMode && imageSource === "UNAVAILABLE");
  const imageUrl = {
    EDGE_CAM: `${edgeBaseUrl}/stream.mjpeg?baby_id=${encodeURIComponent(baby.baby_id)}`,
    GO2RTC: `${go2rtcUrl}/api/stream.mjpeg?src=${encodeURIComponent(baby.stream_name)}`,
    MOCK: `${mockCameraUrl}/stream.mjpeg?baby=${encodeURIComponent(baby.baby_id)}&v=${mockVersion}`,
    STATIC: "/sample_baby_band.jpg",
    UNAVAILABLE: undefined,
  }[imageSource];

  const connection = unavailable ? "Offline" : !ready ? "Connecting"
    : usingSample ? (imageSource === "STATIC" ? "Sample image" : "Mock feed") : "Online";
  const connectionDetail = connection === "Online" ? (imageMode ? "MJPEG" : protocol) : connection;
  useEffect(() => onConnectionChange(connectionDetail), [connectionDetail, onConnectionChange]);

  const nextImageSource = () => {
    setReady(false);
    const next: Record<ImageSource, ImageSource> = {
      EDGE_CAM: "GO2RTC",
      GO2RTC: baby.baby_id === "baby_1" ? "MOCK" : "UNAVAILABLE",
      MOCK: "STATIC",
      STATIC: "UNAVAILABLE",
      UNAVAILABLE: "UNAVAILABLE",
    };
    setImageSource(next[imageSource]);
  };

  const detectionBox = showDetection && ready && !usingSample && !unavailable ? baby.band_bbox_pct : null;
  const hasAudio = ready && !imageMode && !unavailable;
  const VolumeIcon = muted ? VolumeX : Volume2;
  const ExpandIcon = expanded ? Minimize : Maximize;

  return (
    <section className="camera" aria-labelledby="camera-title">
      <div className="section-bar">
        <h2 id="camera-title" tabIndex={-1}>Live view</h2>
        <span className="connection" data-live={connection === "Online"}>
          <span className="connection-dot" aria-hidden="true" />{connection}
        </span>
      </div>
      <div className={`player${expanded ? " player-expanded" : ""}`}>
        <div className="camera-frame" style={{ aspectRatio: frameAspectRatio }}>
          <video
            ref={videoRef}
            autoPlay
            playsInline
            muted={muted}
            hidden={imageMode || unavailable}
            aria-label="Live camera feed"
            onPlaying={() => setReady(true)}
            onWaiting={() => setReady(false)}
            onResize={(event) => {
              const { videoWidth, videoHeight } = event.currentTarget;
              if (videoHeight > 0) setFrameAspectRatio(videoWidth / videoHeight);
            }}
          />
          {imageMode && !unavailable && (
            <img
              key={imageSource}
              src={imageUrl}
              alt={usingSample ? connectionDetail : "Live camera feed"}
              onLoad={(event) => {
                const { naturalWidth, naturalHeight } = event.currentTarget;
                if (naturalHeight > 0) setFrameAspectRatio(naturalWidth / naturalHeight);
                setReady(true);
              }}
              onError={nextImageSource}
            />
          )}
          {unavailable && (
            <div className="no-camera"><VideoOff aria-hidden="true" />No live feed</div>
          )}
          {usingSample && !unavailable && (
            <span className="image-label">{imageSource === "STATIC" ? "Sample image" : "Mock feed"}</span>
          )}
          {detectionBox && (baby.band_detected || baby.subject_type === "ADULT") && (
            <div
              className="detection-box"
              data-adult={baby.subject_type === "ADULT"}
              style={{
                left: `${detectionBox[0]}%`,
                top: `${detectionBox[1]}%`,
                width: `${detectionBox[2]}%`,
                height: `${detectionBox[3]}%`,
              }}
            />
          )}
        </div>
        {(!unavailable || expanded) && (
          <div className="player-bar">
            {hasAudio && (
              <button type="button" className="text-action" aria-pressed={!muted} onClick={() => setMuted(!muted)}>
                <VolumeIcon aria-hidden="true" />{muted ? "Unmute" : "Mute"}
              </button>
            )}
            <button
              type="button"
              className="icon-button"
              aria-label={expanded ? "Exit expanded view" : "Expand live view"}
              aria-expanded={expanded}
              onClick={() => setExpanded(!expanded)}
            >
              <ExpandIcon aria-hidden="true" />
            </button>
          </div>
        )}
      </div>
      {children}
    </section>
  );
}
