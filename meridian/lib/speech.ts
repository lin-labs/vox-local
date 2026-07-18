/* Voice I/O — Web Speech API (Chrome-first), no keys required. */

type RecHandlers = {
  onInterim: (t: string) => void;
  onFinal: (t: string) => void;
  onError: (code: string) => void;
  onEnd: () => void;
};

let rec: any = null;
let utterQueue: SpeechSynthesisUtterance[] = [];
let speakToken = 0;
let cachedVoice: SpeechSynthesisVoice | null = null;

export function sttSupported(): boolean {
  if (typeof window === "undefined") return false;
  return "webkitSpeechRecognition" in window || "SpeechRecognition" in window;
}

export function ttsSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

export function startRec(h: RecHandlers) {
  const Ctor =
    (window as any).SpeechRecognition ||
    (window as any).webkitSpeechRecognition;
  if (!Ctor) {
    h.onError("unsupported");
    return;
  }
  stopRec();
  rec = new Ctor();
  rec.lang = "en-US";
  rec.interimResults = true;
  rec.maxAlternatives = 1;
  rec.continuous = false;
  rec.onresult = (e: any) => {
    let interim = "";
    let final = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) final += r[0].transcript;
      else interim += r[0].transcript;
    }
    if (interim) h.onInterim(interim);
    if (final.trim()) h.onFinal(final.trim());
  };
  rec.onerror = (e: any) => h.onError(e?.error ?? "unknown");
  rec.onend = () => h.onEnd();
  try {
    rec.start();
  } catch {
    /* start() throws if already running — ignore */
  }
}

export function stopRec() {
  try {
    rec?.stop();
  } catch {
    /* noop */
  }
  rec = null;
}

function pickVoice(): SpeechSynthesisVoice | null {
  if (cachedVoice) return cachedVoice;
  const voices = window.speechSynthesis.getVoices();
  const prefs = [
    "Google UK English Female",
    "Google UK English Male",
    "Google US English",
    "Samantha",
    "Karen",
    "Daniel",
    "Moira",
    "Tessa",
  ];
  for (const name of prefs) {
    const v = voices.find((v) => v.name === name);
    if (v) return (cachedVoice = v);
  }
  const en = voices.find((v) => v.lang?.startsWith("en"));
  return (cachedVoice = en ?? null);
}

export function primeTTS() {
  if (!ttsSupported()) return;
  window.speechSynthesis.getVoices();
  window.speechSynthesis.addEventListener?.("voiceschanged", () => {
    cachedVoice = null;
    pickVoice();
  });
}

/** Strip anything that reads badly aloud. */
function cleanForSpeech(text: string): string {
  return text
    .replace(/[*_#`~>]/g, "")
    .replace(/https?:\/\/\S+/g, "")
    .replace(
      /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/gu,
      ""
    )
    .replace(/\s+/g, " ")
    .trim();
}

/** Chunk long text — Chrome silently dies on very long utterances. */
function chunk(text: string): string[] {
  const sentences = text.split(/(?<=[.!?…])\s+/);
  const out: string[] = [];
  let cur = "";
  for (const s of sentences) {
    if ((cur + " " + s).length > 190 && cur) {
      out.push(cur.trim());
      cur = s;
    } else {
      cur = cur ? cur + " " + s : s;
    }
  }
  if (cur.trim()) out.push(cur.trim());
  return out.length ? out : [text];
}

export function speak(text: string, onEnd: () => void) {
  if (!ttsSupported()) {
    onEnd();
    return;
  }
  cancelSpeak();
  const token = ++speakToken;
  const parts = chunk(cleanForSpeech(text));
  const voice = pickVoice();
  utterQueue = parts.map((p) => {
    const u = new SpeechSynthesisUtterance(p);
    if (voice) u.voice = voice;
    u.rate = 1.0;
    u.pitch = 0.95;
    u.volume = 1;
    return u;
  });
  utterQueue.forEach((u, i) => {
    if (i === utterQueue.length - 1) {
      u.onend = () => {
        if (token === speakToken) onEnd();
      };
      u.onerror = () => {
        if (token === speakToken) onEnd();
      };
    }
    window.speechSynthesis.speak(u);
  });
}

export function cancelSpeak() {
  if (!ttsSupported()) return;
  speakToken++; // invalidate pending onEnd callbacks
  utterQueue = [];
  window.speechSynthesis.cancel();
}
