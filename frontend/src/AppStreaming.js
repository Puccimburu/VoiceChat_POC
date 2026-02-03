import React, { useState, useRef, useEffect } from 'react';
import './App.css';
import '@fortawesome/fontawesome-free/css/all.min.css';
import io from 'socket.io-client';

function AppStreaming() {
  const [responseText, setResponseText] = useState('');
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [selectedVoice, setSelectedVoice] = useState('en-US-Neural2-J');
  const selectedVoiceRef = useRef('en-US-Neural2-J');
  const [isMonitoring, setIsMonitoring] = useState(false);

  const socketRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const streamingRef = useRef(false);
  const currentAudioRef = useRef(null);
  const audioQueueRef = useRef([]);
  const audioChunksRef = useRef([]);  // Accumulate audio chunks
  const sessionIdRef = useRef(crypto.randomUUID());
  const isSpeakingRef = useRef(false);

  useEffect(() => {
    // Connect to WebSocket server
    socketRef.current = io('http://localhost:5000');

    socketRef.current.on('connected', (data) => {
      console.log('✅ Connected to server:', data);

      // Start listening on connect
      startListening();
    });

    socketRef.current.on('transcript', (data) => {
      console.log('📝 Transcript:', data.text, 'Final:', data.is_final);
      if (data.is_final) {
        setResponseText('');  // Clear interim text
      }
    });

    socketRef.current.on('audio_chunk', (data) => {
      console.log('🔊 Received audio chunk:', data.text, `(queue: ${audioQueueRef.current.length})`);
      audioQueueRef.current.push(data);
      isSpeakingRef.current = true;
      setIsSpeaking(true);
      if (!currentAudioRef.current) {
        console.log('▶️ Starting playback');
        playNextAudio();
      }
    });

    socketRef.current.on('stream_complete', () => {
      console.log('✅ Stream complete');
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

    socketRef.current.on('error', (data) => {
      console.error('❌ Server error:', data.message);
    });

    return () => {
      // Don't disconnect socket in cleanup - causes issues with React StrictMode
      // Socket will disconnect when component fully unmounts or page closes
    };
  }, []);

  const playNextAudio = async () => {
    if (currentAudioRef.current || audioQueueRef.current.length === 0) {
      console.log(`⏸️ Playback blocked - current: ${!!currentAudioRef.current}, queue: ${audioQueueRef.current.length}`);
      return;
    }

    const { audio, text, words } = audioQueueRef.current.shift();
    console.log(`▶️ Playing audio: "${text}"`);

    return new Promise((resolve) => {
      const audioElement = new Audio(`data:audio/mp3;base64,${audio}`);
      currentAudioRef.current = audioElement;

      audioElement.onloadedmetadata = () => {
        console.log(`📊 Audio loaded, duration: ${audioElement.duration}s`);
        words.forEach((wordData) => {
          const delayMs = wordData.time_seconds * 1000;
          setTimeout(() => {
            setResponseText(prev => prev + (prev ? ' ' : '') + wordData.word);
          }, delayMs);
        });
      };

      audioElement.onended = () => {
        console.log('✅ Audio playback ended');
        currentAudioRef.current = null;
        resolve();
        playNextAudio();
        // If nothing new started, this was the last chunk
        if (!currentAudioRef.current) {
          isSpeakingRef.current = false;
          setIsSpeaking(false);
        }
      };

      if (!isMuted) {
        console.log('🔊 Starting audio.play()');
        audioElement.play().catch(e => {
          console.error('❌ Audio play failed:', e);
          currentAudioRef.current = null;
          resolve();
          playNextAudio();
          if (!currentAudioRef.current) {
            isSpeakingRef.current = false;
            setIsSpeaking(false);
          }
        });
      } else {
        console.log('🔇 Muted - skipping audio');
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
    const SPEECH_THRESHOLD = 0.8;    // Lower threshold to detect speech more easily
    const SILENCE_DURATION_MS = 1200; // Stop after 1.2s of silence (balanced: fast but reliable)
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
        console.log(`🎚️ Audio level: ${average.toFixed(2)} (threshold: ${SPEECH_THRESHOLD}, streaming: ${streamingRef.current})`);
        lastLogTime = now;
      }

      if (average > SPEECH_THRESHOLD) {
        // Reset silence timer
        silenceStartTime = null;

        if (!streamingRef.current && !isSpeakingRef.current) {
          // Barge-in: discard any leftover audio from previous response
          if (currentAudioRef.current) {
            currentAudioRef.current.pause();
            currentAudioRef.current = null;
          }
          audioQueueRef.current = [];
          setResponseText('');

          // User started speaking - start fresh recording
          console.log('🎤 Speech detected - starting new recording');
          setIsListening(true);
          streamingRef.current = true;
          audioChunksRef.current = [];  // Clear any old chunks

          // Start recording (no timeslice - single blob on stop for proper WebM header)
          if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'inactive') {
            mediaRecorderRef.current.start();
            console.log('🎙️ Recording started');
          }
        }
      } else if (average < SILENCE_THRESHOLD && streamingRef.current) {
        // Track silence duration
        if (!silenceStartTime) {
          silenceStartTime = now;
          console.log('🤫 Silence detected - waiting...');
        } else if (now - silenceStartTime > SILENCE_DURATION_MS) {
          // Sustained silence - stop recording
          console.log('🛑 Stopping recording after sustained silence');
          setIsListening(false);
          streamingRef.current = false;

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
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 48000,
          channelCount: 1
        }
      });

      // AudioContext for level monitoring
      audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)();
      analyserRef.current = audioContextRef.current.createAnalyser();
      const source = audioContextRef.current.createMediaStreamSource(stream);
      source.connect(analyserRef.current);
      analyserRef.current.fftSize = 2048;

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
          console.log(`🎙️ Using MIME type: ${mimeType || 'browser default'}`);
          break;
        } else {
          console.warn(`⚠️ ${format} not supported, trying next...`);
        }
      }

      mediaRecorderRef.current = new MediaRecorder(stream,
        mimeType ? { mimeType } : undefined
      );

      // Store the actual MIME type being used
      selectedVoiceRef.current = selectedVoice; // Also update voice ref
      const actualMimeType = mediaRecorderRef.current.mimeType;
      console.log(`✅ MediaRecorder created with MIME type: ${actualMimeType}`);

      // Store MIME type for sending to backend
      window.currentAudioMimeType = actualMimeType;

      // Accumulate all audio chunks (we control recording start/stop)
      mediaRecorderRef.current.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      // When MediaRecorder stops, send the finalized audio file
      mediaRecorderRef.current.onstop = async () => {
        console.log('📭 MediaRecorder stopped');
        // Discard if AI is still speaking — this blob is just mic picking up speaker output
        if (isSpeakingRef.current) {
          console.log('🔇 Discarding blob — AI is speaking (echo)');
          audioChunksRef.current = [];
          return;
        }
        if (audioChunksRef.current.length > 0 && socketRef.current) {
          // Use the actual MIME type from MediaRecorder, not hardcoded
          const actualType = window.currentAudioMimeType || 'audio/webm;codecs=opus';
          const audioBlob = new Blob(audioChunksRef.current, { type: actualType });
          console.log(`📤 Sending audio: ${audioBlob.size} bytes (${audioChunksRef.current.length} chunks) - Type: ${actualType}`);

          // Convert to base64 and send
          const reader = new FileReader();
          reader.onloadend = () => {
            const base64Audio = reader.result;
            socketRef.current.emit('audio_complete', {
              session_id: sessionIdRef.current,
              audio: base64Audio,
              voice: selectedVoiceRef.current,
              mimeType: window.currentAudioMimeType || 'audio/webm;codecs=opus'
            });
          };
          reader.readAsDataURL(audioBlob);
        }
        audioChunksRef.current = [];
      };

      // Don't start recording yet - wait for speech detection
      setIsMonitoring(true);
      console.log('🎤 Audio monitoring started (webm/opus) - waiting for speech...');

      monitorAudioLevel();

    } catch (error) {
      console.error('Error accessing microphone:', error);
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
        {!isListening && !isSpeaking && isMonitoring && 'Ready (Real-Time Streaming)'}
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
            console.log('🎙️ Voice changed to:', newVoice);
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
