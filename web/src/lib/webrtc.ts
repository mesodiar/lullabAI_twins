/**
 * go2rtc WebRTC + MSE (Media Source Extensions) Client for LullabAI Twins.
 *
 * Uses WebRTC on the LAN and starts with MSE on a same-origin HTTPS proxy.
 */

export type StreamProtocol = "WEBRTC" | "MSE" | "MJPEG_MOCK" | "CONNECTING";

export interface Go2RTCConnection {
  close: () => void;
}

export function connectGo2RTCStream(
  videoEl: HTMLVideoElement,
  go2rtcBaseUrl: string,
  streamName: string,
  onProtocolChange: (protocol: StreamProtocol) => void,
  onFallbackToMock: () => void
): Go2RTCConnection {
  let ws: WebSocket | null = null;
  let pc: RTCPeerConnection | null = null;
  let closed = false;
  let fallbackTimer: number | undefined;
  let mseStarted = false;

  const stopTimer = () => window.clearTimeout(fallbackTimer);
  const closePeer = () => {
    if (pc) {
      pc.onconnectionstatechange = null;
      pc.close();
      pc = null;
    }
  };
  const close = () => {
    closed = true;
    stopTimer();
    videoEl.removeEventListener("playing", stopTimer);
    closePeer();
    ws?.close();
    ws = null;
  };
  const fallback = () => {
    if (closed) return;
    close();
    const src = videoEl.getAttribute("src");
    videoEl.srcObject = null;
    if (src?.startsWith("blob:")) {
      videoEl.removeAttribute("src");
      URL.revokeObjectURL(src);
      videoEl.load();
    }
    onFallbackToMock();
  };
  const waitForPlayback = (next: () => void, ms: number) => {
    stopTimer();
    fallbackTimer = window.setTimeout(next, ms);
  };

  onProtocolChange("CONNECTING");
  let preferMSE: boolean;

  try {
    const wsUrl = new URL(`${go2rtcBaseUrl.replace(/\/$/, "")}/api/ws`, window.location.href);
    preferMSE = wsUrl.protocol === "https:" && wsUrl.origin === window.location.origin;
    wsUrl.protocol = wsUrl.protocol === "https:" ? "wss:" : "ws:";
    wsUrl.searchParams.set("src", streamName);
    ws = new WebSocket(wsUrl);
  } catch {
    fallback();
    return { close };
  }
  videoEl.addEventListener("playing", stopTimer);
  waitForPlayback(fallback, 5000);

  const startMSE = (socket: WebSocket) => {
    if (closed || mseStarted) return;
    if (!("MediaSource" in window) || socket.readyState !== WebSocket.OPEN) {
      fallback();
      return;
    }
    mseStarted = true;
    closePeer();
    waitForPlayback(fallback, 6000);
    const ms = new MediaSource();
    videoEl.srcObject = null;
    videoEl.src = URL.createObjectURL(ms);

    ms.addEventListener(
      "sourceopen",
      () => {
        if (closed || socket.readyState !== WebSocket.OPEN) return;
        const codecs = [
          "avc1.640029",
          "avc1.64002A",
          "avc1.640033",
          "avc1.4D0029",
          "avc1.42E01E",
          "hvc1.1.6.L153.B0",
          "mp4a.40.2",
          "mp4a.40.5",
          "flac",
          "opus",
        ].filter((c) => MediaSource.isTypeSupported(`video/mp4; codecs="${c}"`) || MediaSource.isTypeSupported(`audio/mp4; codecs="${c}"`));
        socket.send(
          JSON.stringify({
            type: "mse",
            value: codecs.join(","),
          })
        );
      },
      { once: true }
    );

    let sb: SourceBuffer | null = null;
    const queue: ArrayBuffer[] = [];

    socket.binaryType = "arraybuffer";
    socket.onmessage = (ev) => {
      if (closed) return;
      try {
        if (typeof ev.data === "string") {
          const msg = JSON.parse(ev.data);
          if (msg.type === "mse" && ms.readyState === "open" && !sb) {
            // go2rtc can negotiate audio only when the browser lacks the camera's video codec.
            if (typeof msg.value !== "string" || !/\b(?:avc1|hvc1)\./.test(msg.value)) {
              fallback();
              return;
            }
            sb = ms.addSourceBuffer(msg.value);
            sb.mode = "segments";
            sb.addEventListener("error", fallback);
            sb.addEventListener("updateend", () => {
              if (!closed && sb && !sb.updating && queue.length > 0) {
                try { sb.appendBuffer(queue.shift()!); } catch { fallback(); }
              }
            });
            onProtocolChange("MSE");
            videoEl.play().catch(() => {});
          } else if (msg.type === "error" && String(msg.value).startsWith("mse")) {
            fallback();
          }
        } else if (ev.data instanceof ArrayBuffer) {
          if (sb && !sb.updating && queue.length === 0) {
            sb.appendBuffer(ev.data);
          } else {
            queue.push(ev.data);
          }
        }
      } catch {
        fallback();
      }
    };
  };

  ws.onopen = async () => {
    if (closed || !ws) return;
    const socket = ws;
    if (preferMSE) {
      startMSE(socket);
      return;
    }
    waitForPlayback(() => startMSE(socket), 4000);
    try {
      const peer = new RTCPeerConnection();
      pc = peer;
      peer.addTransceiver("video", { direction: "recvonly" });
      peer.addTransceiver("audio", { direction: "recvonly" });

      peer.ontrack = (event) => {
        if (pc === peer && videoEl.srcObject !== event.streams[0]) {
          videoEl.srcObject = event.streams[0];
          videoEl.play().catch(() => {});
        }
      };
      peer.onicecandidate = (event) => {
        if (pc === peer && event.candidate && socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: "webrtc/candidate", value: event.candidate.candidate }));
        }
      };
      peer.onconnectionstatechange = () => {
        if (peer.connectionState === "connected") {
          onProtocolChange("WEBRTC");
        } else if (peer.connectionState === "failed" || peer.connectionState === "disconnected") {
          startMSE(socket);
        }
      };

      const offer = await peer.createOffer();
      if (pc !== peer) return;
      await peer.setLocalDescription(offer);
      if (pc === peer && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "webrtc/offer", value: offer.sdp }));
      }
    } catch {
      startMSE(socket);
    }
  };

  ws.onmessage = async (ev) => {
    if (typeof ev.data !== "string" || !pc) return;
    const peer = pc;
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === "webrtc/answer") {
        await peer.setRemoteDescription({ type: "answer", sdp: msg.value });
      } else if (msg.type === "webrtc/candidate" && msg.value) {
        // A candidate can arrive before the remote description.
        await peer.addIceCandidate({ candidate: msg.value, sdpMid: "0" }).catch(() => {});
      }
    } catch {
      if (pc === peer && ws) startMSE(ws);
    }
  };

  ws.onerror = fallback;
  ws.onclose = fallback;

  return { close };
}

