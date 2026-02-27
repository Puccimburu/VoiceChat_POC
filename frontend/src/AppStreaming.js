import React, { useState, useRef, useEffect } from 'react';
import './App.css';
import '@fortawesome/fontawesome-free/css/all.min.css';
import { MicVAD } from '@ricky0123/vad-web';

const WS_URL     = process.env.REACT_APP_WS_URL     || 'ws://localhost:8080/ws';
const API_KEY    = process.env.REACT_APP_API_KEY    || 'va_kdLkuOCwIC6-tzojkHvoPAqxMFvfsViB2TGBYtQjUGY'; // Grand Clubhouse key
const UPLOAD_URL = process.env.REACT_APP_UPLOAD_URL || 'http://localhost:5001/upload_document';

// Streaming STT mode - captures raw PCM via ScriptProcessorNode and streams to backend
const USE_STREAMING_STT = true;

function AppStreaming() {
  const [responseText, setResponseText] = useState('');
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [selectedVoice, setSelectedVoice] = useState('en-US-Neural2-J');
  const selectedVoiceRef = useRef('en-US-Neural2-J');
  const [isMonitoring, setIsMonitoring] = useState(false);
  const [mode, setMode] = useState('general');  // 'general', 'document', or 'agent'
  const modeRef = useRef('general');
  const [documents, setDocuments] = useState([]);
  const [selectedDocument, setSelectedDocument] = useState('all');
  const selectedDocumentRef = useRef('all');
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef(null);
  const [conversationHistory, setConversationHistory] = useState([]);
  const chatEndRef = useRef(null);

  const socketRef = useRef(null);  // holds the raw WebSocket
  const mediaRecorderRef = useRef(null);
  const audioContextRef = useRef(null);
  const micVADRef = useRef(null);
  const streamingRef = useRef(false);
  const setupInProgressRef = useRef(false);
  const currentAudioRef = useRef(null);
  const audioQueueRef = useRef([]);
  const audioChunksRef = useRef([]);
  const sessionIdRef = useRef(crypto.randomUUID());
  const isSpeakingRef = useRef(false);
  const lastEndTimeRef = useRef(0);
  const currentRequestIdRef = useRef(null);
  const responseEpochRef = useRef(0);

  // ── WebSocket send helper ────────────────────────────────────────
  const sendMsg = (type, data) => {
    const ws = socketRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type, data: data || {} }));
    }
  };

  // ── WebSocket setup ──────────────────────────────────────────────
  useEffect(() => {
    const ws = new WebSocket(WS_URL);
    socketRef.current = ws;

    ws.onopen = () => {
      console.log('[ws] connected — sending auth');
      ws.send(JSON.stringify({ type: 'auth', data: { api_key: API_KEY } }));
    };

    ws.onmessage = (event) => {
      const { type, data } = JSON.parse(event.data);

      switch (type) {
        case 'connected':
          console.log('[ws] authenticated, session:', data.session_id);
          startListening();
          break;

        case 'stream_started':
          console.log('[ws] STT stream started:', data.session_id);
          break;

        case 'audio_chunk':
          if (data.request_id && data.request_id !== currentRequestIdRef.current) {
            console.log('[ws] ignoring audio chunk from old request:', data.text);
            return;
          }
          console.log('[ws] audio chunk:', data.text, `(queue: ${audioQueueRef.current.length})`);
          audioQueueRef.current.push(data);
          isSpeakingRef.current = true;
          setIsSpeaking(true);
          if (!currentAudioRef.current) {
            playNextAudio();
          }
          break;

        case 'stream_complete':
          console.log('[ws] stream complete');
          if (!currentAudioRef.current && audioQueueRef.current.length === 0) {
            isSpeakingRef.current = false;
            setIsSpeaking(false);
          }
          setTimeout(() => setResponseText(''), 2000);
          break;

        case 'documents_list':
          console.log('[ws] documents:', data.documents);
          setDocuments(data.documents || []);
          break;

        case 'conversation_pair':
          console.log('[ws] conversation pair:', data);
          setConversationHistory(prev => [...prev, {
            user: data.user_query,
            assistant: data.llm_response,
            timestamp: new Date().toISOString()
          }]);
          break;

        case 'error':
          console.error('[ws] server error:', data.message);
          break;

        default:
          break;
      }
    };

    ws.onclose = (e) => {
      console.log('[ws] closed:', e.code, e.reason);
    };

    ws.onerror = (e) => {
      console.error('[ws] error:', e);
    };

    return () => {
      ws.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch documents when entering document mode
  useEffect(() => {
    if (mode === 'document') {
      console.log('[ws] requesting documents list');
      sendMsg('get_documents', {});
    } else if (mode === 'general') {
      setConversationHistory([]);
    } else if (mode === 'agent') {
      setConversationHistory([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (chatEndRef.current) {
      chatEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [conversationHistory]);

  const playNextAudio = async () => {
    if (currentAudioRef.current || audioQueueRef.current.length === 0) {
      return;
    }

    const { audio, text, words } = audioQueueRef.current.shift();
    console.log(`[audio] playing: "${text}"`);

    return new Promise((resolve) => {
      const audioElement = new Audio(`data:audio/mp3;base64,${audio}`);
      currentAudioRef.current = audioElement;

      audioElement.onloadedmetadata = () => {
        const epochAtStart = responseEpochRef.current;
        setResponseText(text.split(/\s+/)[0] || '');
        (words || []).forEach((wordData, idx) => {
          if (idx === 0) return;
          const delayMs = wordData.time_seconds * 1000;
          setTimeout(() => {
            if (responseEpochRef.current !== epochAtStart) return;
            setResponseText((words || []).slice(0, idx + 1).map(w => w.word).join(' '));
          }, delayMs);
        });
      };

      audioElement.onended = () => {
        currentAudioRef.current = null;
        resolve();
        playNextAudio();
        if (!currentAudioRef.current) {
          isSpeakingRef.current = false;
          setIsSpeaking(false);
          setResponseText('');
        }
      };

      if (!isMuted) {
        audioElement.play().catch(e => {
          console.error('[audio] play failed:', e);
          currentAudioRef.current = null;
          resolve();
          playNextAudio();
          if (!currentAudioRef.current) {
            isSpeakingRef.current = false;
            setIsSpeaking(false);
          }
        });
      } else {
        setResponseText(prev => prev + (prev ? ' ' : '') + text);
        currentAudioRef.current = null;
        resolve();
        playNextAudio();
        if (!currentAudioRef.current) {
          isSpeakingRef.current = false;
          setIsSpeaking(false);
        }
      }
    });
  };

  const startListening = async () => {
    if (setupInProgressRef.current) return;
    setupInProgressRef.current = true;

    try {
      if (audioContextRef.current) {
        audioContextRef.current.close();
        audioContextRef.current = null;
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 48000,
          channelCount: 1
        }
      });

      audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
      const source = audioContextRef.current.createMediaStreamSource(stream);

      // ScriptProcessorNode for PCM streaming to Go STT
      const scriptProcessor = audioContextRef.current.createScriptProcessor(4096, 1, 1);
      scriptProcessor.onaudioprocess = (event) => {
        event.outputBuffer.getChannelData(0).fill(0);
        if (!streamingRef.current || !socketRef.current) return;

        const input = event.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
          int16[i] = Math.max(-32768, Math.min(32767, input[i] * 32767 | 0));
        }
        const bytes = new Uint8Array(int16.buffer);
        let binary = '';
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        sendMsg('stt_audio', { audio: btoa(binary) });
      };
      source.connect(scriptProcessor);
      scriptProcessor.connect(audioContextRef.current.destination);

      let mimeType = '';
      const formats = ['audio/ogg;codecs=opus', 'audio/webm;codecs=opus', 'audio/webm', ''];
      for (const format of formats) {
        if (format === '' || MediaRecorder.isTypeSupported(format)) {
          mimeType = format;
          break;
        }
      }

      mediaRecorderRef.current = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      selectedVoiceRef.current = selectedVoice;
      window.currentAudioMimeType = mediaRecorderRef.current.mimeType;

      mediaRecorderRef.current.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunksRef.current.push(event.data);
      };

      mediaRecorderRef.current.onstop = async () => {
        const requestId = crypto.randomUUID();
        currentRequestIdRef.current = requestId;

        if (USE_STREAMING_STT) {
          console.log('[ws] sending end_speech');
          sendMsg('end_speech', { session_id: sessionIdRef.current, request_id: requestId });
        } else {
          if (audioChunksRef.current.length > 0) {
            const actualType = window.currentAudioMimeType || 'audio/webm;codecs=opus';
            const audioBlob = new Blob(audioChunksRef.current, { type: actualType });
            const reader = new FileReader();
            reader.onloadend = () => {
              sendMsg('audio_complete', {
                session_id: sessionIdRef.current,
                audio: reader.result,
                voice: selectedVoiceRef.current,
                mimeType: window.currentAudioMimeType || 'audio/webm;codecs=opus',
                request_id: requestId
              });
            };
            reader.readAsDataURL(audioBlob);
          }
        }
        audioChunksRef.current = [];
      };

      setIsMonitoring(true);

      micVADRef.current = await MicVAD.new({
        stream,
        workletURL: '/vad.worklet.bundle.min.js',
        modelURL: '/silero_vad_v5.onnx',
        positiveSpeechThreshold: 0.5,
        negativeSpeechThreshold: 0.35,
        minSpeechFrames: 3,
        redemptionFrames: 8,
        onSpeechStart: () => {
          const now = Date.now();
          const timeSinceLastEnd = now - lastEndTimeRef.current;
          const COOLDOWN_MS = 500;

          if (!streamingRef.current && timeSinceLastEnd > COOLDOWN_MS) {
            if (isSpeakingRef.current) {
              console.log('[vad] barge-in — stopping AI speech');
              currentRequestIdRef.current = null;
              responseEpochRef.current += 1;
              if (currentAudioRef.current) {
                currentAudioRef.current.pause();
                currentAudioRef.current = null;
              }
              audioQueueRef.current = [];
              isSpeakingRef.current = false;
              setIsSpeaking(false);
              sendMsg('barge_in', { session_id: sessionIdRef.current });
            }
            setResponseText('');
            console.log('[vad] speech detected — starting recording');
            setIsListening(true);
            streamingRef.current = true;
            audioChunksRef.current = [];

            if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'inactive') {
              if (USE_STREAMING_STT) {
                let selectedMember = {};
                try { selectedMember = JSON.parse(localStorage.getItem('selectedMember') || '{}'); } catch (_) {}
                sendMsg('start_stream', {
                  voice: selectedVoiceRef.current,
                  mode: modeRef.current,
                  selected_document: selectedDocumentRef.current,
                  selected_member: selectedMember,
                });
                mediaRecorderRef.current.start();
              } else {
                mediaRecorderRef.current.start();
              }
            }
          }
        },
        onSpeechEnd: () => {
          if (streamingRef.current) {
            console.log('[vad] speech ended — stopping recording');
            setIsListening(false);
            streamingRef.current = false;
            lastEndTimeRef.current = Date.now();
            if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
              mediaRecorderRef.current.stop();
            }
          }
        },
      });
      micVADRef.current.start();
      console.log('[vad] Silero VAD initialized and listening');

    } catch (error) {
      console.error('[audio] error accessing microphone:', error);
    } finally {
      setupInProgressRef.current = false;
    }
  };

  const toggleMute = () => {
    setIsMuted(!isMuted);
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }
  };

  const endSession = () => {
    streamingRef.current = false;
    if (mediaRecorderRef.current) {
      mediaRecorderRef.current.stop();
      mediaRecorderRef.current = null;
    }
    if (micVADRef.current) {
      micVADRef.current.destroy();
      micVADRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    setIsListening(false);
    setIsSpeaking(false);
    setIsMonitoring(false);
    setResponseText('');
  };

  const handleFileUpload = async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    if (!file.name.toLowerCase().endsWith('.pdf')) {
      alert('Please select a PDF file');
      return;
    }

    setIsUploading(true);
    console.log('[upload] uploading:', file.name);

    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await fetch(UPLOAD_URL, {
        method: 'POST',
        body: formData,
      });

      const result = await response.json();

      if (result.success) {
        console.log('[upload] success:', result.message);
        sendMsg('get_documents', {});
        setSelectedDocument(result.filename);
        selectedDocumentRef.current = result.filename;
        alert(`Successfully uploaded: ${result.filename}`);
      } else {
        console.error('[upload] failed:', result.message);
        alert(`Upload failed: ${result.message}`);
      }
    } catch (error) {
      console.error('[upload] error:', error);
      alert(`Upload error: ${error.message}`);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  return (
    <div className="App">
      {true ? (
        <div className="chat-container">
          <div className="chat-history">
            {conversationHistory.length === 0 ? (
              <div className="chat-empty">
                <i className="fas fa-comments"></i>
                <p>
                  {mode === 'document'
                    ? 'Start a conversation by asking a question about your documents'
                    : mode === 'agent'
                    ? 'Ask questions about facilities, bookings, classes, and memberships'
                    : 'Start a conversation'}
                </p>
              </div>
            ) : (
              conversationHistory.map((msg, index) => (
                <div key={index} className="chat-message-group">
                  <div className="chat-message user-message">
                    <div className="message-icon"><i className="fas fa-user"></i></div>
                    <div className="message-content">{msg.user}</div>
                  </div>
                  <div className="chat-message assistant-message">
                    <div className="message-icon"><i className="fas fa-robot"></i></div>
                    <div className="message-content">{msg.assistant}</div>
                  </div>
                </div>
              ))
            )}
            <div ref={chatEndRef} />
          </div>
        </div>
      ) : (
        <div className="message-area">
          {responseText && <div className="response-text">{responseText}</div>}
        </div>
      )}

      <div className="wave-container">
        <div className={`wave-bars ${isListening ? 'listening' : ''} ${isSpeaking ? 'speaking' : ''}`}>
          <div className="bar"></div>
          <div className="bar"></div>
          <div className="bar"></div>
          <div className="bar"></div>
          <div className="bar"></div>
        </div>
      </div>

      <div className="status-text">
        {isListening && 'Listening...'}
        {isSpeaking && 'Speaking...'}
        {!isListening && !isSpeaking && isMonitoring && `Ready — ${
          mode === 'document' ? 'Document Mode' :
          mode === 'agent' ? 'Database Agent' :
          'General Mode'
        }`}
        {!isListening && !isSpeaking && !isMonitoring && 'Connecting...'}
      </div>

      <div className="controls">
        <button className="control-btn exit" onClick={endSession} title="End session">
          <i className="fas fa-times"></i>
        </button>
        <button
          className={`control-btn mode-toggle ${mode !== 'general' ? 'active' : ''}`}
          onClick={() => {
            let newMode;
            if (mode === 'general') newMode = 'document';
            else if (mode === 'document') newMode = 'agent';
            else newMode = 'general';
            setMode(newMode);
            modeRef.current = newMode;
          }}
          title={`Current: ${mode.charAt(0).toUpperCase() + mode.slice(1)} Mode (Click to switch)`}
        >
          <i className={`fas ${
            mode === 'general' ? 'fa-comment' :
            mode === 'document' ? 'fa-file-alt' :
            'fa-database'
          }`}></i>
        </button>
        <button
          className={`control-btn mute ${isMuted ? 'active' : ''}`}
          onClick={toggleMute}
          title={isMuted ? 'Unmute' : 'Mute'}
        >
          <i className={`fas ${isMuted ? 'fa-volume-mute' : 'fa-microphone'}`}></i>
        </button>
      </div>

      <div className="voice-selector">
        <label htmlFor="voice">Voice:</label>
        <select
          id="voice"
          value={selectedVoice}
          onChange={(e) => {
            setSelectedVoice(e.target.value);
            selectedVoiceRef.current = e.target.value;
          }}
        >
          <option value="en-US-Neural2-A">Male 1 (Warm)</option>
          <option value="en-US-Neural2-D">Male 2 (Professional)</option>
          <option value="en-US-Neural2-I">Male 3 (Confident)</option>
          <option value="en-US-Neural2-J">Male 4 (Friendly)</option>
          <option value="en-US-Neural2-C">Female 1 (Clear)</option>
          <option value="en-US-Neural2-E">Female 2 (Energetic)</option>
          <option value="en-US-Neural2-F">Female 3 (Friendly)</option>
          <option value="en-US-Neural2-G">Female 4 (Professional)</option>
          <option value="en-US-Neural2-H">Female 5 (Calm)</option>
        </select>
      </div>

      {mode === 'document' && (
        <div className="document-selector">
          <label htmlFor="document">Document:</label>
          <select
            id="document"
            value={selectedDocument}
            onChange={(e) => {
              setSelectedDocument(e.target.value);
              selectedDocumentRef.current = e.target.value;
            }}
            disabled={isUploading}
          >
            <option value="all">All Documents</option>
            {documents.map((doc) => (
              <option key={doc} value={doc}>{doc}</option>
            ))}
          </select>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf"
            onChange={handleFileUpload}
            style={{ display: 'none' }}
          />
          <button
            className="upload-doc-btn"
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploading}
            title="Upload PDF document"
          >
            <i className={`fas ${isUploading ? 'fa-spinner fa-spin' : 'fa-upload'}`}></i>
          </button>
        </div>
      )}
    </div>
  );
}

export default AppStreaming;
