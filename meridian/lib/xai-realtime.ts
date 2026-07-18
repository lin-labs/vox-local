/* Browser transport for xAI's realtime Voice Agent API.
   The long-lived API key never leaves the server: /api/realtime-token mints a
   five-minute client secret for the WebSocket handshake. */

export type RealtimeEvent = {
  type: string;
  [key: string]: unknown;
};

type SessionConfig = Record<string, unknown>;
type EventHandler = (event: RealtimeEvent) => void;

type TokenResponse = {
  value: string;
  expires_at?: number;
  model: string;
  voice: string;
  error?: string;
};

const SAMPLE_RATE = 24_000;

function base64ToBytes(encoded: string): Uint8Array {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

export function floatToPcm16(
  input: Float32Array,
  inputRate: number
): Uint8Array {
  const ratio = inputRate / SAMPLE_RATE;
  const length = Math.max(1, Math.floor(input.length / ratio));
  const buffer = new ArrayBuffer(length * 2);
  const view = new DataView(buffer);

  for (let i = 0; i < length; i++) {
    const position = i * ratio;
    const left = Math.floor(position);
    const right = Math.min(input.length - 1, left + 1);
    const mix = position - left;
    const sample = input[left] * (1 - mix) + input[right] * mix;
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(
      i * 2,
      clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff,
      true
    );
  }
  return new Uint8Array(buffer);
}

class PcmPlayer {
  private context: AudioContext | null = null;
  private nextStart = 0;
  private sources = new Set<AudioBufferSourceNode>();

  constructor(private readonly onIdle: () => void) {}

  prime() {
    if (!this.context) this.context = new AudioContext({ sampleRate: SAMPLE_RATE });
    void this.context.resume();
  }

  enqueue(encoded: string) {
    this.prime();
    const context = this.context;
    if (!context) return;

    const bytes = base64ToBytes(encoded);
    const frames = Math.floor(bytes.byteLength / 2);
    if (!frames) return;
    const pcm = new DataView(bytes.buffer, bytes.byteOffset, frames * 2);
    const audio = context.createBuffer(1, frames, SAMPLE_RATE);
    const channel = audio.getChannelData(0);
    for (let i = 0; i < frames; i++) {
      channel[i] = pcm.getInt16(i * 2, true) / 0x8000;
    }

    const source = context.createBufferSource();
    source.buffer = audio;
    source.connect(context.destination);
    const startAt = Math.max(context.currentTime + 0.015, this.nextStart);
    this.nextStart = startAt + audio.duration;
    this.sources.add(source);
    source.onended = () => {
      this.sources.delete(source);
      if (!this.sources.size) {
        this.nextStart = 0;
        this.onIdle();
      }
    };
    source.start(startAt);
  }

  get playing(): boolean {
    return this.sources.size > 0;
  }

  cancel(notify = true) {
    const hadAudio = this.sources.size > 0;
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        // A source that ended between iteration and stop is already harmless.
      }
    }
    this.sources.clear();
    this.nextStart = 0;
    if (hadAudio && notify) this.onIdle();
  }
}

export class XaiRealtimeVoice {
  private socket: WebSocket | null = null;
  private player: PcmPlayer;
  private muted = false;
  private microphone: MediaStream | null = null;
  private micContext: AudioContext | null = null;
  private micSource: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private silentGain: GainNode | null = null;

  constructor(private readonly onEvent: EventHandler) {
    this.player = new PcmPlayer(() =>
      this.onEvent({ type: "playback.idle" })
    );
  }

  primeAudio() {
    this.player.prime();
  }

  async connect(session: SessionConfig): Promise<void> {
    if (this.socket?.readyState === WebSocket.OPEN) return;

    const tokenResponse = await fetch("/api/realtime-token", {
      method: "POST",
      cache: "no-store",
    });
    const token = (await tokenResponse.json().catch(() => ({}))) as Partial<TokenResponse>;
    if (!tokenResponse.ok || !token.value || !token.model || !token.voice) {
      throw new Error(token.error || `Voice authentication failed (${tokenResponse.status})`);
    }

    const url = new URL("wss://api.x.ai/v1/realtime");
    url.searchParams.set("model", token.model);

    await new Promise<void>((resolve, reject) => {
      const socket = new WebSocket(url, [
        `xai-client-secret.${token.value}`,
      ]);
      this.socket = socket;
      let settled = false;
      const timeout = window.setTimeout(() => {
        if (!settled) {
          settled = true;
          socket.close();
          reject(new Error("xAI voice connection timed out"));
        }
      }, 12_000);

      socket.addEventListener("open", () => {
        this.send({
          type: "session.update",
          session: { ...session, voice: token.voice },
        });
      });

      socket.addEventListener("message", (message) => {
        if (typeof message.data !== "string") return;
        let event: RealtimeEvent;
        try {
          event = JSON.parse(message.data) as RealtimeEvent;
        } catch {
          return;
        }

        if (
          event.type === "response.output_audio.delta" &&
          typeof event.delta === "string" &&
          !this.muted
        ) {
          this.player.enqueue(event.delta);
        } else if (event.type === "input_audio_buffer.speech_started") {
          // Server VAD cancels generation; stop already-buffered local playback too.
          this.player.cancel(false);
        }

        if (event.type === "session.updated" && !settled) {
          settled = true;
          window.clearTimeout(timeout);
          resolve();
        }
        this.onEvent(event);
      });

      socket.addEventListener("error", () => {
        if (!settled) {
          settled = true;
          window.clearTimeout(timeout);
          reject(new Error("Could not connect to xAI voice"));
        }
        this.onEvent({ type: "transport.error" });
      });

      socket.addEventListener("close", (event) => {
        if (!settled) {
          settled = true;
          window.clearTimeout(timeout);
          reject(new Error(`xAI voice closed during setup (${event.code})`));
        }
        this.stopMicrophone();
        this.onEvent({
          type: "transport.closed",
          code: event.code,
          reason: event.reason,
        });
      });
    });
  }

  updateSession(session: SessionConfig) {
    this.send({ type: "session.update", session });
  }

  sendText(text: string) {
    this.send({
      type: "conversation.item.create",
      item: {
        type: "message",
        role: "user",
        content: [{ type: "input_text", text }],
      },
    });
    this.requestResponse();
  }

  forceMessage(text: string) {
    this.send({
      type: "conversation.item.create",
      item: {
        type: "force_message",
        role: "assistant",
        content: [{ type: "output_text", text }],
      },
    });
  }

  sendFunctionOutput(callId: string, output: unknown) {
    this.send({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: callId,
        output: JSON.stringify(output),
      },
    });
  }

  requestResponse() {
    this.send({ type: "response.create" });
  }

  cancelResponse() {
    this.send({ type: "response.cancel" });
    this.send({ type: "input_audio_buffer.clear" });
    this.player.cancel();
  }

  async startMicrophone(): Promise<void> {
    if (this.microphone) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Microphone capture is not supported in this browser");
    }

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    const context = new AudioContext({ sampleRate: SAMPLE_RATE });
    await context.resume();
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(2048, 1, 1);
    const silentGain = context.createGain();
    silentGain.gain.value = 0;
    processor.onaudioprocess = (event) => {
      if (this.socket?.readyState !== WebSocket.OPEN) return;
      const floatData = event.inputBuffer.getChannelData(0);
      const pcm = floatToPcm16(floatData, context.sampleRate);
      this.send({
        type: "input_audio_buffer.append",
        audio: bytesToBase64(pcm),
      });
    };
    source.connect(processor);
    processor.connect(silentGain);
    silentGain.connect(context.destination);

    this.microphone = stream;
    this.micContext = context;
    this.micSource = source;
    this.processor = processor;
    this.silentGain = silentGain;
  }

  stopMicrophone() {
    if (this.processor) this.processor.onaudioprocess = null;
    this.micSource?.disconnect();
    this.processor?.disconnect();
    this.silentGain?.disconnect();
    for (const track of this.microphone?.getTracks() || []) track.stop();
    void this.micContext?.close();
    this.microphone = null;
    this.micContext = null;
    this.micSource = null;
    this.processor = null;
    this.silentGain = null;
  }

  setMuted(muted: boolean) {
    this.muted = muted;
    if (muted) this.player.cancel();
  }

  get isPlaying(): boolean {
    return this.player.playing;
  }

  get microphoneActive(): boolean {
    return this.microphone !== null;
  }

  close() {
    this.stopMicrophone();
    this.player.cancel(false);
    this.socket?.close(1000, "client closed");
    this.socket = null;
  }

  private send(event: RealtimeEvent) {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify(event));
  }
}
