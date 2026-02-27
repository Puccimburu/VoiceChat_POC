/**
 * ChatWidget — unified floating chat + voice bubble.
 *
 * Text mode : type a message → REST POST /api/agent/query → text reply
 * Voice mode: tap mic (or speak) → STT via WebSocket → LLM/Agent → TTS audio
 *             conversation_pair arrives → user transcript + agent reply appear as bubbles
 *
 * Embed on any page:
 *   <script
 *     src="https://yourplatform.com/chat-widget.js"
 *     data-api-key="va_..."
 *     data-agent-name="My Assistant"
 *     data-api-url="http://localhost:5001"
 *     data-ws-url="ws://localhost:8080/ws"
 *     data-mode="agent"
 *   ></script>
 */
import React, { useState, useRef, useEffect } from 'react';

const DEFAULT_API_URL = process.env.REACT_APP_API_URL || 'http://localhost:5001';
const DEFAULT_WS_URL  = process.env.REACT_APP_WS_URL  || 'ws://localhost:8080/ws';

// ── Icons ──────────────────────────────────────────────────────────────
function IconChat() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="white">
      <path d="M20 2H4C2.9 2 2 2.9 2 4V22L6 18H20C21.1 18 22 17.1 22 16V4C22 2.9 21.1 2 20 2ZM20 16H6L4 18V4H20V16Z" />
    </svg>
  );
}

function IconClose() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
      <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
    </svg>
  );
}

function IconSend() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
      <path d="M2 21L23 12 2 3V10L17 12 2 14V21Z" />
    </svg>
  );
}

function IconMic({ color }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill={color || 'currentColor'}>
      <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V5zm6 6c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z" />
    </svg>
  );
}

// ── Animated bars (shown on mic button while listening / speaking) ──────
function MicBars({ color }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '2px', height: '16px' }}>
      {[1, 2, 3].map(i => (
        <div
          key={i}
          style={{
            width: '3px',
            height: '100%',
            borderRadius: '2px',
            background: color,
            animation: `barPulse 0.8s ease-in-out ${i * 0.12}s infinite alternate`,
            transformOrigin: 'center',
          }}
        />
      ))}
    </div>
  );
}

// ── Typing indicator (while REST call is in flight) ─────────────────────
function TypingDots() {
  return (
    <span style={styles.dotsRow}>
      {[0, 1, 2].map(i => (
        <span key={i} style={{ ...styles.dot, animationDelay: `${i * 0.2}s` }} />
      ))}
    </span>
  );
}

// ── Main component ─────────────────────────────────────────────────────
export default function ChatWidget({ apiKey, agentName, apiUrl, wsUrl, voice, mode }) {
  const BASE       = apiUrl || DEFAULT_API_URL;
  const WS_URL     = wsUrl  || DEFAULT_WS_URL;
  const widgetMode = mode   || 'general';

  // ── UI state
  const [open, setOpen]           = useState(false);
  const [messages, setMessages]   = useState([]);
  const [input, setInput]         = useState('');
  const [textBusy, setTextBusy]   = useState(false);  // REST call in flight
  const [phase, setPhase]         = useState('idle'); // idle|listening|thinking|speaking
  const [wsConnected, setWsConnected] = useState(false);
  const [error, setError]         = useState('');

  // ── Voice refs
  const socketRef         = useRef(null);
  const audioCtxRef       = useRef(null);
  const analyserRef       = useRef(null);
  const processorRef      = useRef(null);
  const streamRef         = useRef(null);
  const mediaRecRef       = useRef(null);
  const streamingRef      = useRef(false);
  const audioQueueRef     = useRef([]);
  const currentAudioRef   = useRef(null);
  const sessionIdRef      = useRef(crypto.randomUUID());
  const requestIdRef      = useRef(null);
  const silenceTimerRef   = useRef(null);
  const destroyedRef      = useRef(false);
  const audioReadyRef     = useRef(false);
  const reconnectDelayRef = useRef(1000);
  const reconnectTimerRef = useRef(null);

  // ── UI refs
  const bottomRef = useRef(null);
  const inputRef  = useRef(null);

  // Auto-scroll to latest message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, textBusy]);

  // Focus input when panel opens
  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 50);
  }, [open]);

  // ── WebSocket send helper
  const sendMsg = (type, data) => {
    const ws = socketRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type, data: data || {} }));
    }
  };

  // ── WebSocket connection with exponential-backoff reconnect
  useEffect(() => {
    destroyedRef.current = false;

    const connect = () => {
      if (destroyedRef.current) return;
      const ws = new WebSocket(WS_URL);
      socketRef.current = ws;

      ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'auth', data: { api_key: apiKey } }));
      };

      ws.onmessage = (event) => {
        const { type, data } = JSON.parse(event.data);
        switch (type) {
          case 'connected':
            reconnectDelayRef.current = 1000;
            setWsConnected(true);
            setError('');
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
            // Both user transcript and agent response appear as chat bubbles
            setMessages(prev => [
              ...prev,
              { role: 'user', text: data.user_query },
              { role: 'agent', text: data.llm_response },
            ]);
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
        setWsConnected(false);
        streamingRef.current = false;
        setPhase('idle');
        if (destroyedRef.current) return;
        const delay = reconnectDelayRef.current;
        reconnectTimerRef.current = setTimeout(() => {
          reconnectDelayRef.current = Math.min(reconnectDelayRef.current * 2, 30000);
          connect();
        }, delay);
      };

      ws.onerror = () => setWsConnected(false);
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

  // ── Mic init (called once after first successful WS auth)
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
        const inp   = e.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(inp.length);
        for (let i = 0; i < inp.length; i++) {
          int16[i] = Math.max(-32768, Math.min(32767, inp[i] * 32767 | 0));
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
        sendMsg('end_speech', { session_id: sessionIdRef.current, request_id: reqId });
        setPhase('thinking');
      };

      monitorLevel();
    } catch {
      setError('Microphone access denied');
    }
  };

  // ── Voice activity detection (auto start/stop + barge-in)
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
          if (currentAudioRef.current) {
            // Barge-in: interrupt current playback
            currentAudioRef.current.pause();
            currentAudioRef.current = null;
            audioQueueRef.current = [];
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

  const handleMicTap = () => {
    if (phase === 'listening') stopRecording();
    else if (phase === 'idle' && wsConnected) startRecording();
  };

  // ── TTS audio playback queue
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

  // ── Text send via REST
  const sendText = async () => {
    const text = input.trim();
    if (!text || textBusy) return;
    setInput('');
    setMessages(prev => [...prev, { role: 'user', text }]);
    setTextBusy(true);
    try {
      const res = await fetch(`${BASE}/api/agent/query`, {
        method:  'POST',
        headers: { 'X-API-Key': apiKey, 'Content-Type': 'application/json' },
        body:    JSON.stringify({ query: text }),
      });
      const data = await res.json();
      setMessages(prev => [...prev, {
        role: 'agent',
        text: data.response || data.error || 'No response received.',
      }]);
    } catch {
      setMessages(prev => [...prev, {
        role: 'agent',
        text: 'Connection error — please try again.',
      }]);
    } finally {
      setTextBusy(false);
    }
  };

  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendText(); }
  };

  // ── Phase colour for mic button
  const phaseColor = {
    idle:      wsConnected ? '#4a9eff' : '#8b949e',
    listening: '#3fb950',
    thinking:  '#e3b341',
    speaking:  '#f78166',
  };

  const micTitle = {
    idle:      wsConnected ? 'Tap to speak' : 'Connecting...',
    listening: 'Listening — tap to stop',
    thinking:  'Thinking...',
    speaking:  'Speaking...',
  };

  // ── Render
  return (
    <div style={styles.wrapper}>

      {/* ── Chat panel ──────────────────────────────────────────────── */}
      {open && (
        <div style={styles.panel}>

          {/* Header */}
          <div style={styles.header}>
            <div style={styles.headerLeft}>
              <div style={{ ...styles.statusDot, background: wsConnected ? '#3fb950' : '#f85149' }} />
              <span style={styles.headerName}>{agentName}</span>
            </div>
            <button style={styles.closeBtn} onClick={() => setOpen(false)} title="Close">
              <IconClose />
            </button>
          </div>

          {/* Error banner */}
          {error && (
            <div style={styles.errorBanner}>{error}</div>
          )}

          {/* Messages */}
          <div style={styles.messages}>
            {messages.length === 0 && (
              <div style={styles.welcome}>
                <div style={styles.welcomeAvatar}>{(agentName || 'A')[0].toUpperCase()}</div>
                <p style={styles.welcomeTitle}>{agentName}</p>
                <p style={styles.welcomeSub}>Type a message or tap the mic to speak.</p>
              </div>
            )}

            {messages.map((m, i) => (
              <div key={i} style={m.role === 'user' ? styles.rowRight : styles.rowLeft}>
                <div style={m.role === 'user' ? styles.userBubble : styles.agentBubble}>
                  {m.text}
                </div>
              </div>
            ))}

            {textBusy && (
              <div style={styles.rowLeft}>
                <div style={styles.agentBubble}><TypingDots /></div>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Input area: [Mic] [textarea] [Send] */}
          <div style={styles.inputArea}>

            {/* Mic button */}
            <button
              style={{
                ...styles.micBtn,
                borderColor: phaseColor[phase],
                background:
                  phase === 'listening' ? '#0a2010' :
                  phase === 'speaking'  ? '#1f0e0a' :
                  '#0d1117',
              }}
              onClick={handleMicTap}
              disabled={!wsConnected || phase === 'thinking'}
              title={micTitle[phase]}
            >
              {(phase === 'listening' || phase === 'speaking')
                ? <MicBars color={phaseColor[phase]} />
                : <IconMic color={phaseColor[phase]} />
              }
            </button>

            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={onKey}
              placeholder={phase !== 'idle' ? `${phase.charAt(0).toUpperCase() + phase.slice(1)}...` : 'Type a message...'}
              style={styles.textarea}
              rows={1}
              disabled={textBusy || phase !== 'idle'}
            />

            <button
              onClick={sendText}
              disabled={textBusy || !input.trim() || phase !== 'idle'}
              style={{
                ...styles.sendBtn,
                opacity: (textBusy || !input.trim() || phase !== 'idle') ? 0.4 : 1,
              }}
              title="Send"
            >
              <IconSend />
            </button>
          </div>
        </div>
      )}

      {/* ── Floating action button ─────────────────────────────────── */}
      <button
        style={styles.fab}
        onClick={() => {
          setOpen(o => !o);
          // Resume AudioContext on first user gesture (browser auto-suspends it)
          if (audioCtxRef.current?.state === 'suspended') {
            audioCtxRef.current.resume();
          }
        }}
        title={open ? 'Close' : `Chat with ${agentName}`}
      >
        {open ? <IconClose /> : <IconChat />}
      </button>

      <style>{`
        @keyframes chatBounce {
          0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
          40%            { transform: translateY(-5px); opacity: 1; }
        }
        @keyframes barPulse {
          from { transform: scaleY(0.5); }
          to   { transform: scaleY(1.4); }
        }
      `}</style>
    </div>
  );
}

// ── Styles ─────────────────────────────────────────────────────────────
const styles = {
  wrapper: {
    position:   'fixed',
    bottom:     '24px',
    right:      '24px',
    zIndex:     2147483647,
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },

  panel: {
    position:      'absolute',
    bottom:        '70px',
    right:         0,
    width:         '340px',
    height:        '500px',
    background:    '#161b22',
    border:        '1px solid #30363d',
    borderRadius:  '16px',
    display:       'flex',
    flexDirection: 'column',
    overflow:      'hidden',
    boxShadow:     '0 8px 40px rgba(0,0,0,0.5)',
  },

  header: {
    display:        'flex',
    alignItems:     'center',
    justifyContent: 'space-between',
    padding:        '14px 16px',
    background:     '#0d1117',
    borderBottom:   '1px solid #30363d',
    flexShrink:     0,
  },
  headerLeft: { display: 'flex', alignItems: 'center', gap: '8px' },
  statusDot:  { width: '8px', height: '8px', borderRadius: '50%' },
  headerName: { fontWeight: '600', fontSize: '14px', color: '#f0f6fc' },
  closeBtn: {
    background: 'none', border: 'none', color: '#8b949e',
    cursor: 'pointer', padding: '2px', lineHeight: 0, borderRadius: '4px',
  },

  errorBanner: {
    background: '#3d1a1a', borderBottom: '1px solid #f85149',
    padding: '6px 16px', fontSize: '12px', color: '#f85149', flexShrink: 0,
  },

  messages: {
    flex:          1,
    overflowY:     'auto',
    padding:       '16px 12px',
    display:       'flex',
    flexDirection: 'column',
    gap:           '8px',
  },

  welcome: { textAlign: 'center', padding: '32px 16px 16px' },
  welcomeAvatar: {
    width: '52px', height: '52px', borderRadius: '50%',
    background: '#1f6feb', color: '#fff',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: '22px', fontWeight: '700', margin: '0 auto 12px',
  },
  welcomeTitle: { color: '#f0f6fc', fontWeight: '600', fontSize: '15px', margin: '0 0 6px' },
  welcomeSub:   { color: '#8b949e', fontSize: '13px', margin: 0, lineHeight: '1.5' },

  rowRight: { display: 'flex', justifyContent: 'flex-end' },
  rowLeft:  { display: 'flex', justifyContent: 'flex-start' },

  userBubble: {
    background:   '#1f6feb',
    color:        '#fff',
    padding:      '9px 13px',
    borderRadius: '16px 16px 4px 16px',
    fontSize:     '13px',
    lineHeight:   '1.5',
    maxWidth:     '82%',
    whiteSpace:   'pre-wrap',
    wordBreak:    'break-word',
  },
  agentBubble: {
    background:   '#21262d',
    color:        '#c9d1d9',
    padding:      '9px 13px',
    borderRadius: '16px 16px 16px 4px',
    fontSize:     '13px',
    lineHeight:   '1.5',
    maxWidth:     '82%',
    whiteSpace:   'pre-wrap',
    wordBreak:    'break-word',
  },

  dotsRow: { display: 'inline-flex', alignItems: 'center', gap: '4px', padding: '2px 0' },
  dot: {
    display:      'inline-block',
    width:        '7px',
    height:       '7px',
    borderRadius: '50%',
    background:   '#8b949e',
    animation:    'chatBounce 1.2s ease-in-out infinite',
  },

  inputArea: {
    display:    'flex',
    alignItems: 'flex-end',
    gap:        '8px',
    padding:    '10px 12px',
    borderTop:  '1px solid #30363d',
    background: '#0d1117',
    flexShrink: 0,
  },

  micBtn: {
    width:          '36px',
    height:         '36px',
    flexShrink:     0,
    borderRadius:   '50%',
    border:         '1.5px solid',
    cursor:         'pointer',
    display:        'flex',
    alignItems:     'center',
    justifyContent: 'center',
    transition:     'border-color 0.3s, background 0.3s',
    padding:        0,
  },

  textarea: {
    flex:         1,
    background:   '#161b22',
    border:       '1px solid #30363d',
    borderRadius: '10px',
    color:        '#e1e4e8',
    fontSize:     '13px',
    padding:      '8px 12px',
    resize:       'none',
    outline:      'none',
    fontFamily:   'inherit',
    lineHeight:   '1.5',
    maxHeight:    '100px',
    overflowY:    'auto',
  },

  sendBtn: {
    background:     '#1f6feb',
    border:         'none',
    borderRadius:   '10px',
    color:          '#fff',
    cursor:         'pointer',
    padding:        '9px 12px',
    display:        'flex',
    alignItems:     'center',
    justifyContent: 'center',
    flexShrink:     0,
    transition:     'opacity 0.2s',
  },

  fab: {
    width:          '54px',
    height:         '54px',
    borderRadius:   '50%',
    background:     '#1f6feb',
    border:         'none',
    cursor:         'pointer',
    display:        'flex',
    alignItems:     'center',
    justifyContent: 'center',
    boxShadow:      '0 4px 20px rgba(31,111,235,0.45)',
    transition:     'transform 0.15s, box-shadow 0.15s',
  },
};
