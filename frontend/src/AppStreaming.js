import React, { useState, useRef, useEffect } from 'react';
import './App.css';
import '@fortawesome/fontawesome-free/css/all.min.css';
import io from 'socket.io-client';

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
  const [mode, setMode] = useState('general');  // 'general' or 'document'
  const modeRef = useRef('general');

  const socketRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const streamingRef = useRef(false);
  const setupInProgressRef = useRef(false);  // prevents concurrent startListening calls
  const currentAudioRef = useRef(null);
  const audioQueueRef = useRef([]);
  const audioChunksRef = useRef([]);  // Accumulate audio chunks
  const sessionIdRef = useRef(crypto.randomUUID());
  const isSpeakingRef = useRef(false);
  const lastEndTimeRef = useRef(0);  // Cooldown to prevent rapid start/stop cycles
  const currentRequestIdRef = useRef(null);  // Track which request we're waiting for
  const responseEpochRef = useRef(0);        // Bumped on barge-in — stale word-timing timeouts bail out

  useEffect(() => {
    // Connect to WebSocket server
    socketRef.current = io('http://localhost:5000');

    socketRef.current.on('connected', (data) => {
      console.log(' Connected to server:', data);

      // Start listening on connect
      startListening();
    });

    socketRef.current.on('transcript', (data) => {
      console.log(' Transcript:', data.text, 'Final:', data.is_final);
      if (data.is_final) {
        setResponseText('');  // Clear interim text
      }
    });

    socketRef.current.on('audio_chunk', (data) => {
      // Ignore chunks from old/cancelled requests
      if (data.request_id && data.request_id !== currentRequestIdRef.current) {
        console.log(' Ignoring audio chunk from old request:', data.text);
        return;
      }
      console.log(' Received audio chunk:', data.text, `(queue: ${audioQueueRef.current.length})`);
      audioQueueRef.current.push(data);
      isSpeakingRef.current = true;
      setIsSpeaking(true);
      if (!currentAudioRef.current) {
        console.log('▶ Starting playback');
        playNextAudio();
      }
    });

    socketRef.current.on('stream_complete', () => {
      console.log(' Stream complete');
      // Only mark done if no audio is still playing — otherwise playNextAudio handles it on last onended
      if (!currentAudioRef.current && audioQueueRef.current.length === 0) {
        isSpeakingRef.current = false;
        setIsSpeaking(false);
      }
      // Clear response text for next interaction after a delay
      setTimeout(() => {
        setResponseText('');
      }, 2000);
    });

    socketRef.current.on('stream_started', (data) => {
      console.log(' Streaming STT started:', data.session_id);
    });

    socketRef.current.on('error', (data) => {
      console.error(' Server error:', data.message);
    });

    return () => {
      socketRef.current?.disconnect();
    };
  }, []);

  const playNextAudio = async () => {
    if (currentAudioRef.current || audioQueueRef.current.length === 0) {
      console.log(`⏸ Playback blocked - current: ${!!currentAudioRef.current}, queue: ${audioQueueRef.current.length}`);
      return;
    }

    const { audio, text, words } = audioQueueRef.current.shift();
    console.log(`▶ Playing audio: "${text}"`);

    return new Promise((resolve) => {
      const audioElement = new Audio(`data:audio/mp3;base64,${audio}`);
      currentAudioRef.current = audioElement;

      audioElement.onloadedmetadata = () => {
        console.log(` Audio loaded, duration: ${audioElement.duration}s`);
        const epochAtStart = responseEpochRef.current;
        // Reset to show only the current sentence (not a growing paragraph)
        setResponseText(text.split(/\s+/)[0] || '');
        words.forEach((wordData, idx) => {
          if (idx === 0) return; // first word already set above
          const delayMs = wordData.time_seconds * 1000;
          setTimeout(() => {
            if (responseEpochRef.current !== epochAtStart) return; // barge-in happened
            setResponseText(words.slice(0, idx + 1).map(w => w.word).join(' '));
          }, delayMs);
        });
      };

      audioElement.onended = () => {
        console.log(' Audio playback ended');
        currentAudioRef.current = null;
        resolve();
        playNextAudio();
        // If nothing new started, this was the last chunk
        if (!currentAudioRef.current) {
          isSpeakingRef.current = false;
          setIsSpeaking(false);
          setResponseText('');
        }
      };

      if (!isMuted) {
        console.log(' Starting audio.play()');
        audioElement.play().catch(e => {
          console.error(' Audio play failed:', e);
          currentAudioRef.current = null;
          resolve();
          playNextAudio();
          if (!currentAudioRef.current) {
            isSpeakingRef.current = false;
            setIsSpeaking(false);
          }
        });
      } else {
        console.log(' Muted - skipping audio');
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

  const monitorAudioLevel = () => {
    if (!analyserRef.current) return;

    const bufferLength = analyserRef.current.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);
    const SILENCE_THRESHOLD = 0.4;   // For detecting silence
    const SPEECH_THRESHOLD = 2.5;    // Higher threshold to avoid false triggers from breathing
    const SILENCE_DURATION_MS = 300; // Stop after 300ms of sustained silence
    let lastLogTime = 0;
    let silenceStartTime = null;

    const checkAudioLevel = () => {
      if (!analyserRef.current) return;

      analyserRef.current.getByteTimeDomainData(dataArray);

      let sum = 0;
      for (let i = 0; i < bufferLength; i++) {
        sum += Math.abs(dataArray[i] - 128);
      }
      const average = sum / bufferLength;

      // Log audio level every 2 seconds for debugging
      const now = Date.now();
      if (now - lastLogTime > 2000) {
        console.log(` Audio level: ${average.toFixed(2)} (threshold: ${SPEECH_THRESHOLD}, streaming: ${streamingRef.current})`);
        lastLogTime = now;
      }

      if (average > SPEECH_THRESHOLD) {
        // Reset silence timer
        silenceStartTime = null;

        // Check cooldown period (500ms after last end)
        const timeSinceLastEnd = now - lastEndTimeRef.current;
        const COOLDOWN_MS = 500;

        if (!streamingRef.current && timeSinceLastEnd > COOLDOWN_MS) {
          // Barge-in: stop AI speech if it's playing
          if (isSpeakingRef.current) {
            console.log(' Barge-in detected - stopping AI speech');
            currentRequestIdRef.current = null;  // Invalidate — drops any in-flight chunks
            responseEpochRef.current += 1;       // Kill pending word-timing timeouts
            if (currentAudioRef.current) {
              currentAudioRef.current.pause();
              currentAudioRef.current = null;
            }
            audioQueueRef.current = [];
            isSpeakingRef.current = false;
            setIsSpeaking(false);
            // Notify backend to cancel current pipeline
            socketRef.current.emit('barge_in', { session_id: sessionIdRef.current });
          }
          setResponseText('');

          // User started speaking - start fresh recording
          console.log(' Speech detected - starting new recording');
          setIsListening(true);
          streamingRef.current = true;
          audioChunksRef.current = [];  // Clear any old chunks

          if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'inactive') {
            if (USE_STREAMING_STT) {
              // Streaming mode: open backend STT stream, PCM flows via ScriptProcessorNode
              socketRef.current.emit('start_stream', {
                session_id: sessionIdRef.current,
                voice: selectedVoiceRef.current,
                mimeType: 'audio/pcm',  // signals LINEAR16 @ 48kHz
                mode: modeRef.current   // 'general' or 'document'
              });
              mediaRecorderRef.current.start();  // no timeslice — blob kept for fallback only
              console.log(' Streaming recording started (PCM via ScriptProcessor)');
            } else {
              // Batch mode: single blob on stop
              mediaRecorderRef.current.start();
              console.log(' Recording started (batch mode)');
            }
          }
        }
      } else if (average <= SPEECH_THRESHOLD && streamingRef.current) {
        // Track silence duration
        if (!silenceStartTime) {
          silenceStartTime = now;
          console.log(' Silence detected - waiting...');
        } else if (now - silenceStartTime > SILENCE_DURATION_MS) {
          // Sustained silence - stop recording
          console.log(' Stopping recording after sustained silence');
          setIsListening(false);
          streamingRef.current = false;
          lastEndTimeRef.current = now;  // Set cooldown timestamp

          // Stop MediaRecorder - produces single complete blob with proper header
          if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
            mediaRecorderRef.current.stop();
          }

          silenceStartTime = null;
        }
      }

      requestAnimationFrame(checkAudioLevel);
    };

    checkAudioLevel();
  };

  const startListening = async () => {
    // Guard: if a previous call is still awaiting getUserMedia, skip.
    // Without this, rapid 'connected' events each launch getUserMedia concurrently;
    // both see audioContextRef as null, both create a ScriptProcessorNode → 2x PCM.
    if (setupInProgressRef.current) return;
    setupInProgressRef.current = true;

    try {
      // Tear down any previous audio pipeline — each call creates a new AudioContext +
      // ScriptProcessorNode, so stale ones must be closed or they keep sending PCM.
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

      // AudioContext for level monitoring + PCM capture — lock to 48kHz to match Google STT config
      audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 48000 });
      analyserRef.current = audioContextRef.current.createAnalyser();
      analyserRef.current.fftSize = 2048;
      const source = audioContextRef.current.createMediaStreamSource(stream);
      source.connect(analyserRef.current);

      // ScriptProcessorNode captures raw PCM for streaming STT
      const scriptProcessor = audioContextRef.current.createScriptProcessor(4096, 1, 1);
      scriptProcessor.onaudioprocess = (event) => {
        event.outputBuffer.getChannelData(0).fill(0); // silence output — no mic loopback
        if (!streamingRef.current || !socketRef.current) return;

        // Float32 → Int16 (LINEAR16)
        const input = event.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
          int16[i] = Math.max(-32768, Math.min(32767, input[i] * 32767 | 0));
        }
        // Int16 buffer → base64
        const bytes = new Uint8Array(int16.buffer);
        let binary = '';
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        socketRef.current.emit('stt_audio', { audio: btoa(binary) });
      };
      source.connect(scriptProcessor);
      scriptProcessor.connect(audioContextRef.current.destination);

      // Try formats in order of reliability (OGG is more reliable than WebM)
      let mimeType = '';
      const formats = [
        'audio/ogg;codecs=opus',  // Most reliable
        'audio/webm;codecs=opus',
        'audio/webm',
        ''  // Browser default
      ];

      for (const format of formats) {
        if (format === '' || MediaRecorder.isTypeSupported(format)) {
          mimeType = format;
          console.log(` Using MIME type: ${mimeType || 'browser default'}`);
          break;
        } else {
          console.warn(` ${format} not supported, trying next...`);
        }
      }

      mediaRecorderRef.current = new MediaRecorder(stream,
        mimeType ? { mimeType } : undefined
      );

      // Store the actual MIME type being used
      selectedVoiceRef.current = selectedVoice; // Also update voice ref
      const actualMimeType = mediaRecorderRef.current.mimeType;
      console.log(` MediaRecorder created with MIME type: ${actualMimeType}`);

      // Store MIME type for sending to backend
      window.currentAudioMimeType = actualMimeType;

      // Accumulate MediaRecorder chunks (fallback blob in streaming mode, primary in batch)
      mediaRecorderRef.current.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      // When MediaRecorder stops
      mediaRecorderRef.current.onstop = async () => {
        console.log(' MediaRecorder stopped');

        // New request — stamp an ID so we can drop stale chunks from a previous one
        const requestId = crypto.randomUUID();
        currentRequestIdRef.current = requestId;

        if (USE_STREAMING_STT) {
          // Streaming mode: signal end of speech
          console.log(' Sending end_speech signal');
          socketRef.current.emit('end_speech', { session_id: sessionIdRef.current, request_id: requestId });
        } else {
          // Batch mode: send complete audio blob
          if (audioChunksRef.current.length > 0 && socketRef.current) {
            const actualType = window.currentAudioMimeType || 'audio/webm;codecs=opus';
            const audioBlob = new Blob(audioChunksRef.current, { type: actualType });
            console.log(` Sending audio: ${audioBlob.size} bytes (${audioChunksRef.current.length} chunks) - Type: ${actualType}`);

            const reader = new FileReader();
            reader.onloadend = () => {
              const base64Audio = reader.result;
              socketRef.current.emit('audio_complete', {
                session_id: sessionIdRef.current,
                audio: base64Audio,
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

      // Don't start recording yet - wait for speech detection
      setIsMonitoring(true);
      console.log(' Audio monitoring started (webm/opus) - waiting for speech...');

      monitorAudioLevel();

    } catch (error) {
      console.error('Error accessing microphone:', error);
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
    if (streamingRef.current) {
      socketRef.current.emit('stop_stream');
      streamingRef.current = false;
    }

    if (mediaRecorderRef.current) {
      mediaRecorderRef.current.stop();
      mediaRecorderRef.current = null;
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

  return (
    <div className="App">
      <div className="message-area">
        {responseText && (
          <div className="response-text">{responseText}</div>
        )}
      </div>

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
        {!isListening && !isSpeaking && isMonitoring && `Ready — ${mode === 'document' ? 'Document Mode' : 'General Mode'}`}
        {!isListening && !isSpeaking && !isMonitoring && 'Connecting...'}
      </div>

      <div className="controls">
        <button
          className="control-btn exit"
          onClick={endSession}
          title="End session"
        >
          <i className="fas fa-times"></i>
        </button>
        <button
          className={`control-btn mode-toggle ${mode === 'document' ? 'active' : ''}`}
          onClick={() => {
            const newMode = mode === 'general' ? 'document' : 'general';
            setMode(newMode);
            modeRef.current = newMode;
            console.log(' Mode changed to:', newMode);
          }}
          title={mode === 'general' ? 'Switch to Document Mode' : 'Switch to General Mode'}
        >
          <i className={`fas ${mode === 'general' ? 'fa-comment' : 'fa-file-alt'}`}></i>
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
            const newVoice = e.target.value;
            setSelectedVoice(newVoice);
            selectedVoiceRef.current = newVoice;
            console.log(' Voice changed to:', newVoice);
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
    </div>
  );
}

export default AppStreaming;
