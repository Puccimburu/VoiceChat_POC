/**
 * VoiceWidget — the embeddable voice UI component.
 *
 * Connects to the Go Gateway WebSocket using the customer's API key.
 * The backend validates the key and routes queries to their database.
 */
import { useState, useRef, useEffect } from 'react';

const DEFAULT_WS_URL = process.env.REACT_APP_WS_URL || 'ws://localhost:8080/ws';

export default function VoiceWidget({ apiKey, agentName, voice, wsUrl, mode }) {
  const WS_URL = wsUrl || DEFAULT_WS_URL;
  const widgetMode = mode || 'general';
  const [phase, setPhase]     = useState('idle');   // idle | listening | thinking | speaking
  const [error, setError]           = useState('');
  const [connected, setConnected]   = useState(false);

  const socketRef          = useRef(null);
  const audioCtxRef        = useRef(null);
  const analyserRef        = useRef(null);
  const processorRef       = useRef(null);
  const streamRef          = useRef(null);
  const mediaRecRef        = useRef(null);
  const streamingRef       = useRef(false);
  const audioQueueRef      = useRef([]);
  const currentAudioRef    = useRef(null);
  const sessionIdRef       = useRef(crypto.randomUUID());
  const requestIdRef       = useRef(null);
  const epochRef           = useRef(0);
  const silenceTimerRef    = useRef(null);
  const destroyedRef       = useRef(false);   // true after unmount — no reconnect
  const audioReadyRef      = useRef(false);   // true after first successful mic init
  const reconnectDelayRef  = useRef(1000);    // current backoff delay in ms
  const reconnectTimerRef  = useRef(null);    // pending setTimeout handle

  // ── Send helper ──────────────────────────────────────────────────
  const sendMsg = (type, data) => {
    const ws = socketRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type, data: data || {} }));
    }
  };

  // ── Connect to Go Gateway (with exponential-backoff reconnect) ──
  useEffect(() => {
    destroyedRef.current = false;

    const connect = () => {
      if (destroyedRef.current) return;

      const ws = new WebSocket(WS_URL);
      socketRef.current = ws;

      ws.onopen = () => {
        console.log('[widget] connected — sending auth');
        ws.send(JSON.stringify({ type: 'auth', data: { api_key: apiKey } }));
      };

      ws.onmessage = (event) => {
        const { type, data } = JSON.parse(event.data);

        switch (type) {
          case 'connected':
            // Reset backoff on successful auth.
            reconnectDelayRef.current = 1000;
            setConnected(true);
            setError('');
            // Only init audio once — mic permission persists across reconnects.
            if (!audioReadyRef.current) {
              audioReadyRef.current = true;
              initAudio();
            }
            break;

          case 'audio_chunk':
            if (data.request_id && data.request_id !== requestIdRef.current) return;
            audioQueueRef.current.push(data);
            setPhase('speaking');
            if (!currentAudioRef.current) playNext();
            break;

          case 'stream_complete':
            if (!currentAudioRef.current && audioQueueRef.current.length === 0) {
              setPhase('idle');
            }
            break;

          case 'conversation_pair':
            // Notify the host page so it can refresh live data (enrolled counts, bookings, etc.)
            window.dispatchEvent(new CustomEvent('clubhouse:data_changed'));
            break;

          case 'error':
            setError(data.message);
            setPhase('idle');
            break;

          default:
            break;
        }
      };

      ws.onclose = () => {
        setConnected(false);
        streamingRef.current = false;
        setPhase('idle');
        if (destroyedRef.current) return;

        const delay = reconnectDelayRef.current;
        console.log(`[widget] disconnected — reconnecting in ${delay / 1000}s`);
        setError(`Reconnecting in ${delay / 1000}s...`);

        reconnectTimerRef.current = setTimeout(() => {
          // Exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s (cap).
          reconnectDelayRef.current = Math.min(reconnectDelayRef.current * 2, 30000);
          connect();
        }, delay);
      };

      ws.onerror = () => {
        // onclose fires right after onerror — let it handle the reconnect.
        setConnected(false);
      };
    };

    connect();

    return () => {
      destroyedRef.current = true;
      clearTimeout(reconnectTimerRef.current);
      socketRef.current?.close();
      audioCtxRef.current?.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey]);

  // ── Audio init ──────────────────────────────────────────────────
  const initAudio = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 48000 }
      });
      streamRef.current = stream;

      audioCtxRef.current = new AudioContext({ sampleRate: 48000 });
      analyserRef.current = audioCtxRef.current.createAnalyser();
      analyserRef.current.fftSize = 2048;

      const source = audioCtxRef.current.createMediaStreamSource(stream);
      source.connect(analyserRef.current);

      processorRef.current = audioCtxRef.current.createScriptProcessor(4096, 1, 1);
      processorRef.current.onaudioprocess = (e) => {
        e.outputBuffer.getChannelData(0).fill(0);
        if (!streamingRef.current || !socketRef.current) return;
        const input  = e.inputBuffer.getChannelData(0);
        const int16  = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
          int16[i] = Math.max(-32768, Math.min(32767, input[i] * 32767 | 0));
        }
        const bytes = new Uint8Array(int16.buffer);
        let bin = '';
        for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
        sendMsg('stt_audio', { audio: btoa(bin) });
      };
      source.connect(processorRef.current);
      processorRef.current.connect(audioCtxRef.current.destination);

      mediaRecRef.current = new MediaRecorder(stream);
      mediaRecRef.current.onstop = () => {
        const reqId = crypto.randomUUID();
        requestIdRef.current = reqId;
        sendMsg('end_speech', {
          session_id: sessionIdRef.current,
          request_id: reqId
        });
        setPhase('thinking');
      };

      monitorLevel();
    } catch (e) {
      setError('Microphone access denied');
    }
  };

  // ── Voice activity detection ────────────────────────────────────
  const monitorLevel = () => {
    const buf = new Uint8Array(analyserRef.current.frequencyBinCount);
    const SPEECH = 2.5, SILENCE = 0.4, SILENCE_MS = 1000;

    const tick = () => {
      if (!analyserRef.current) return;
      analyserRef.current.getByteTimeDomainData(buf);
      const avg = buf.reduce((s, v) => s + Math.abs(v - 128), 0) / buf.length;

      if (avg > SPEECH) {
        clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = null;

        if (!streamingRef.current) {
          // barge-in
          if (currentAudioRef.current) {
            currentAudioRef.current.pause();
            currentAudioRef.current = null;
            audioQueueRef.current = [];
            epochRef.current++;
            sendMsg('barge_in', { session_id: sessionIdRef.current });
          }
          startRecording();
        }
      } else if (avg < SILENCE && streamingRef.current && !silenceTimerRef.current) {
        silenceTimerRef.current = setTimeout(() => {
          stopRecording();
          silenceTimerRef.current = null;
        }, SILENCE_MS);
      }

      requestAnimationFrame(tick);
    };
    tick();
  };

  const startRecording = () => {
    if (streamingRef.current) return;
    streamingRef.current = true;
    setPhase('listening');
    let selectedMember = {};
    try { selectedMember = JSON.parse(localStorage.getItem('selectedMember') || '{}'); } catch (_) {}
    sendMsg('start_stream', {
      voice: voice || 'en-US-Neural2-J',
      mode: widgetMode,
      selected_document: 'all',
      selected_member: selectedMember,
    });
    mediaRecRef.current?.start();
  };

  const stopRecording = () => {
    if (!streamingRef.current) return;
    streamingRef.current = false;
    mediaRecRef.current?.stop();
  };

  // ── Audio playback ──────────────────────────────────────────────
  const playNext = () => {
    if (currentAudioRef.current || audioQueueRef.current.length === 0) return;
    const { audio } = audioQueueRef.current.shift();
    const el = new Audio(`data:audio/mp3;base64,${audio}`);
    currentAudioRef.current = el;
    el.onended = () => {
      currentAudioRef.current = null;
      if (audioQueueRef.current.length > 0) playNext();
      else setPhase('idle');
    };
    el.play().catch(() => { currentAudioRef.current = null; });
  };

  // ── UI ──────────────────────────────────────────────────────────
  const phaseColor = {
    idle:      '#4a9eff',
    listening: '#3fb950',
    thinking:  '#e3b341',
    speaking:  '#f78166',
  };

  const phaseLabel = {
    idle:      connected ? 'Tap to speak' : (error || 'Connecting...'),
    listening: 'Listening...',
    thinking:  'Thinking...',
    speaking:  'Speaking...',
  };

  const handleTap = () => {
    // Resume AudioContext on first user gesture (browser auto-suspends it)
    if (audioCtxRef.current?.state === 'suspended') {
      audioCtxRef.current.resume();
    }
    if (phase === 'listening') stopRecording();
    else if (phase === 'idle' && connected) startRecording();
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <div style={{ ...styles.dot, background: connected ? '#3fb950' : '#f85149' }} />
        <span style={styles.name}>{agentName}</span>
      </div>

      {error && connected && <div style={styles.error}>{error}</div>}

      <button
        onClick={handleTap}
        style={{ ...styles.micBtn, borderColor: phaseColor[phase] }}
        disabled={!connected || phase === 'thinking'}
      >
        <div style={styles.bars}>
          {[1,2,3,4,5].map(i => (
            <div
              key={i}
              style={{
                ...styles.bar,
                background: phaseColor[phase],
                animation: phase === 'listening' || phase === 'speaking'
                  ? `barPulse 0.8s ease-in-out ${i * 0.1}s infinite alternate`
                  : 'none',
                height: phase === 'idle' ? '12px' : `${10 + i * 6}px`
              }}
            />
          ))}
        </div>
      </button>

      <div style={{ ...styles.status, color: phaseColor[phase] }}>
        {phaseLabel[phase]}
      </div>

      <style>{`
        @keyframes barPulse {
          from { transform: scaleY(0.5); }
          to   { transform: scaleY(1.4); }
        }
      `}</style>
    </div>
  );
}

const styles = {
  container: {
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    background: '#161b22',
    border: '1px solid #30363d',
    borderRadius: '16px',
    padding: '20px',
    maxWidth: '360px',
    width: '100%',
    color: '#e1e4e8',
    boxSizing: 'border-box',
  },
  header: { display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '16px' },
  dot: { width: '8px', height: '8px', borderRadius: '50%' },
  name: { fontWeight: '600', fontSize: '14px', color: '#f0f6fc' },
  error: {
    background: '#3d1a1a', border: '1px solid #f85149', borderRadius: '8px',
    padding: '8px 12px', fontSize: '12px', color: '#f85149', marginBottom: '12px',
  },
  micBtn: {
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    width: '80px', height: '80px', borderRadius: '50%', background: '#0d1117',
    border: '2px solid', cursor: 'pointer', margin: '0 auto 12px', transition: 'border-color 0.3s',
  },
  bars: { display: 'flex', alignItems: 'center', gap: '3px', height: '36px' },
  bar: { width: '4px', borderRadius: '2px', transition: 'height 0.3s' },
  status: { textAlign: 'center', fontSize: '13px', fontWeight: '500', marginBottom: '14px', transition: 'color 0.3s' },
};
