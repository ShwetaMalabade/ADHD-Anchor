import { useState, useEffect, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Mic, MicOff, Volume2 } from "lucide-react";
import AgoraRTC, {
  IAgoraRTCClient,
  IMicrophoneAudioTrack,
} from "agora-rtc-sdk-ng";

const AGORA_APP_ID = "a6ec6f8912664496baa13238a3ec20ca";
const CHANNEL_NAME = "study_room_anchor";
const AGORA_TEMP_TOKEN = "007eJxTYOjLkYifn7dSI6vva0902qrFDq+XymzNPGOx57Og+LaTN6cqMCSapSabpVlYGhqZmZmYWJolJSYaGhsZWyQapyYbGSQn1pqfyGwIZGQQnL2KlZEBAsF8huKS0pTK+KL8/Nz4xLzkjPwiBgYAYLAlyg==";
const BACKEND_WS = "ws://localhost:8000/ws-audio";

interface AgoraRoomProps {
  onUserSpeech?: (transcript: string) => void;
}

const AgoraRoom = ({ onUserSpeech }: AgoraRoomProps) => {
  const [joined, setJoined] = useState(false);
  const [micOn, setMicOn] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [expanded, setExpanded] = useState(false);

  const clientRef = useRef<IAgoraRTCClient | null>(null);
  const micTrackRef = useRef<IMicrophoneAudioTrack | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const audioWsRef = useRef<WebSocket | null>(null);
  const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  // Connect audio WebSocket to backend for STT
  const connectAudioWs = useCallback(() => {
    const ws = new WebSocket(BACKEND_WS);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      console.log("[AUDIO-WS] Connected to backend STT");
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === "transcription" && data.text) {
          console.log("[STT] Transcription:", data.text);
          setTranscript(data.text);
          onUserSpeech?.(data.text);
          setTimeout(() => setTranscript(""), 4000);
        }
      } catch (e) {
        // Binary response, ignore
      }
    };

    ws.onclose = () => {
      console.log("[AUDIO-WS] Disconnected");
    };

    audioWsRef.current = ws;
    return ws;
  }, [onUserSpeech]);

  // Start recording mic audio and sending chunks to backend
  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          noiseSuppression: true,
          echoCancellation: true,
          autoGainControl: true,
          sampleRate: 16000,
          channelCount: 1,
        }
      });
      const recorder = new MediaRecorder(stream, {
        mimeType: "audio/webm;codecs=opus",
        audioBitsPerSecond: 64000,
      });
      recorderRef.current = recorder;
      chunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };

      recorder.onstop = async () => {
        if (chunksRef.current.length === 0) return;

        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        chunksRef.current = [];

        // Send audio blob to backend for STT
        if (audioWsRef.current?.readyState === WebSocket.OPEN) {
          const buffer = await blob.arrayBuffer();
          audioWsRef.current.send(buffer);
          console.log("[AUDIO] Sent", buffer.byteLength, "bytes for STT");
        }
      };

      // Record in 4-second chunks, then send for transcription
      recorder.start();
      console.log("[AUDIO] Recording started");

      // Periodically stop/restart to send chunks
      const sendChunks = () => {
        if (recorderRef.current?.state === "recording") {
          recorderRef.current.stop();
          // Restart after a brief pause
          setTimeout(() => {
            if (recorderRef.current && micOn) {
              try {
                recorderRef.current.start();
              } catch (e) {
                // Recorder was disposed, create new one
                startRecording();
              }
            }
          }, 200);
        }
        silenceTimerRef.current = setTimeout(sendChunks, 6000);
      };
      silenceTimerRef.current = setTimeout(sendChunks, 6000);
    } catch (err) {
      console.error("[AUDIO] Recording failed:", err);
    }
  }, [micOn]);

  const stopRecording = useCallback(() => {
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
    if (recorderRef.current?.state === "recording") {
      recorderRef.current.stop();
    }
    recorderRef.current = null;
  }, []);

  const joinChannel = useCallback(async () => {
    try {
      // Connect audio WS for STT
      connectAudioWs();

      // Join Agora
      const client = AgoraRTC.createClient({ mode: "rtc", codec: "vp8" });
      clientRef.current = client;

      await client.join(AGORA_APP_ID, CHANNEL_NAME, AGORA_TEMP_TOKEN, null);
      console.log("[AGORA] Joined channel:", CHANNEL_NAME);

      // Create and publish mic track
      const micTrack = await AgoraRTC.createMicrophoneAudioTrack({
        AEC: true,   // Acoustic echo cancellation
        ANS: true,   // Automatic noise suppression
        AGC: true,   // Automatic gain control
      });
      micTrackRef.current = micTrack;
      await client.publish([micTrack]);
      console.log("[AGORA] Mic published");

      setJoined(true);
      setMicOn(true);

      // Start recording for STT
      setTimeout(() => startRecording(), 500);
    } catch (err) {
      console.error("[AGORA] Join failed:", err);
    }
  }, [connectAudioWs, startRecording]);

  const leaveChannel = useCallback(async () => {
    stopRecording();

    if (audioWsRef.current) {
      audioWsRef.current.close();
      audioWsRef.current = null;
    }

    if (micTrackRef.current) {
      micTrackRef.current.stop();
      micTrackRef.current.close();
      micTrackRef.current = null;
    }

    if (clientRef.current) {
      await clientRef.current.leave();
      clientRef.current = null;
    }

    setJoined(false);
    setMicOn(false);
    setTranscript("");
    console.log("[AGORA] Left channel");
  }, [stopRecording]);

  const toggleMic = useCallback(async () => {
    if (micOn) {
      micTrackRef.current?.setEnabled(false);
      stopRecording();
      setMicOn(false);
      setTranscript("");
    } else {
      micTrackRef.current?.setEnabled(true);
      setMicOn(true);
      setTimeout(() => startRecording(), 500);
    }
  }, [micOn, stopRecording, startRecording]);

  // Cleanup on unmount
  useEffect(() => {
    return () => { leaveChannel(); };
  }, [leaveChannel]);

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      className="fixed top-6 right-6 z-30"
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="rounded-2xl bg-card backdrop-blur-md border border-border shadow-md px-4 py-2.5 flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        {joined ? (
          <>
            {micOn ? <Mic size={15} className="text-green-500" /> : <MicOff size={15} className="text-red-400" />}
            {micOn && <div className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />}
            <span>{micOn ? "Listening..." : "Voice connected"}</span>
          </>
        ) : (
          <>
            <Volume2 size={15} />
            <span>Join voice</span>
          </>
        )}
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ opacity: 0, y: -8, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.95 }}
            className="absolute right-0 mt-2 rounded-2xl bg-card border border-border shadow-xl p-5 w-72"
          >
            <p className="text-sm font-medium text-foreground mb-2">
              {joined ? "Voice Channel" : "Talk to Anchor"}
            </p>

            {joined ? (
              <>
                <div className="flex items-center gap-2 mb-3">
                  <div className={`h-2.5 w-2.5 rounded-full ${micOn ? "bg-green-500 animate-pulse" : "bg-gray-400"}`} />
                  <span className="text-xs text-muted-foreground">
                    {micOn ? "Speak naturally -- Anchor is listening" : "Mic off -- tap to unmute"}
                  </span>
                </div>

                {transcript && (
                  <div className="bg-secondary rounded-lg px-3 py-2 mb-3 text-xs text-muted-foreground italic">
                    "{transcript}"
                  </div>
                )}

                <div className="flex gap-2">
                  <button
                    onClick={toggleMic}
                    className={`flex-1 rounded-xl py-2 text-sm font-medium transition-all active:scale-[0.98] flex items-center justify-center gap-1.5 ${
                      micOn
                        ? "bg-red-50 text-red-600 border border-red-200 hover:bg-red-100"
                        : "bg-green-50 text-green-600 border border-green-200 hover:bg-green-100"
                    }`}
                  >
                    {micOn ? <MicOff size={14} /> : <Mic size={14} />}
                    {micOn ? "Mute" : "Unmute"}
                  </button>
                  <button
                    onClick={leaveChannel}
                    className="flex-1 rounded-xl py-2 text-sm font-medium border border-border text-foreground hover:bg-secondary transition-all active:scale-[0.98]"
                  >
                    Leave
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="text-xs text-muted-foreground mb-3">
                  Join the voice channel to talk with Anchor. Say things like "I'm stuck" or "I need a break."
                </p>
                <button
                  onClick={joinChannel}
                  className="w-full rounded-xl py-2 text-sm font-medium btn-primary-action transition-all active:scale-[0.98]"
                >
                  Join voice channel
                </button>
              </>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
};

export default AgoraRoom;
