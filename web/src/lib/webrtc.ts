/**
 * go2rtc WebRTC + MSE (Media Source Extensions) Client for LullabAI Twins.
 *
 * Uses WebRTC on the LAN, with MSE over the local WebSocket as a fallback.
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
  let fallbackTimer: number | null = null;

  onProtocolChange("CONNECTING");

  try {
    const wsUrl = new URL(`${go2rtcBaseUrl.replace(/\/$/, "")}/api/ws`, window.location.href);
    wsUrl.protocol = wsUrl.protocol === "https:" ? "wss:" : "ws:";
    wsUrl.searchParams.set("src", streamName);
    ws = new WebSocket(wsUrl);
  } catch {
    onFallbackToMock();
    return { close: () => {} };
  }

  const startMSE = (socket: WebSocket) => {
    if (closed || !("MediaSource" in window)) {
      onFallbackToMock();
      return;
    }
    const ms = new MediaSource();
    videoEl.srcObject = null;
    videoEl.src = URL.createObjectURL(ms);

    ms.addEventListener(
      "sourceopen",
      () => {
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
      if (typeof ev.data === "string") {
        const msg = JSON.parse(ev.data);
        if (msg.type === "mse" && ms.readyState === "open" && !sb) {
          try {
            sb = ms.addSourceBuffer(msg.value);
            sb.mode = "segments";
            sb.addEventListener("updateend", () => {
              if (sb && !sb.updating && queue.length > 0) {
                const chunk = queue.shift()!;
                sb.appendBuffer(chunk);
              }
            });
            onProtocolChange("MSE");
            videoEl.play().catch(() => {});
          } catch {
            onFallbackToMock();
          }
        } else if (msg.type === "error") {
          onFallbackToMock();
        }
      } else if (ev.data instanceof ArrayBuffer) {
        if (sb && !sb.updating && queue.length === 0) {
          sb.appendBuffer(ev.data);
        } else {
          queue.push(ev.data);
        }
      }
    };
  };

  ws.onopen = async () => {
    if (closed || !ws) return;

    pc = new RTCPeerConnection();

    pc.addTransceiver("video", { direction: "recvonly" });
    pc.addTransceiver("audio", { direction: "recvonly" });

    pc.ontrack = (event) => {
      if (videoEl.srcObject !== event.streams[0]) {
        videoEl.srcObject = event.streams[0];
        videoEl.play().catch(() => {});
      }
    };

    pc.onicecandidate = (event) => {
      if (event.candidate && ws?.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({
            type: "webrtc/candidate",
            value: event.candidate.candidate,
          })
        );
      }
    };

    pc.onconnectionstatechange = () => {
      if (!pc) return;
      if (pc.connectionState === "connected") {
        if (fallbackTimer) window.clearTimeout(fallbackTimer);
        onProtocolChange("WEBRTC");
      } else if (
        pc.connectionState === "failed" ||
        pc.connectionState === "disconnected"
      ) {
        if (ws && ws.readyState === WebSocket.OPEN) {
          startMSE(ws);
        } else {
          onFallbackToMock();
        }
      }
    };

    // If WebRTC UDP does not connect within four seconds, switch to MSE over the same socket
    fallbackTimer = window.setTimeout(() => {
      if (pc && pc.connectionState !== "connected" && ws?.readyState === WebSocket.OPEN) {
        pc.close();
        pc = null;
        startMSE(ws);
      }
    }, 4000);

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    ws.send(JSON.stringify({ type: "webrtc/offer", value: offer.sdp }));
  };

  ws.onmessage = async (ev) => {
    if (typeof ev.data !== "string" || !pc) return;
    const msg = JSON.parse(ev.data);
    if (msg.type === "webrtc/answer") {
      await pc.setRemoteDescription({ type: "answer", sdp: msg.value });
    } else if (msg.type === "webrtc/candidate" && msg.value) {
      try {
        await pc.addIceCandidate({ candidate: msg.value, sdpMid: "0" });
      } catch {
        // Ignore non-critical candidate errors
      }
    }
  };

  ws.onerror = () => {
    if (!closed) onFallbackToMock();
  };

  return {
    close: () => {
      closed = true;
      if (fallbackTimer) window.clearTimeout(fallbackTimer);
      if (pc) {
        pc.close();
        pc = null;
      }
      if (ws) {
        ws.close();
        ws = null;
      }
    },
  };
}

