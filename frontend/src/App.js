import React, { useState, useRef, useEffect } from 'react';
import './App.css';
import '@fortawesome/fontawesome-free/css/all.min.css';

function App() {
  const [responseText, setResponseText] = useState('');
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [selectedVoice, setSelectedVoice] = useState('en-US-Neural2-J'); // Default male voice
  const [isMonitoring, setIsMonitoring] = useState(false); // New: track if mic is monitoring
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const shouldProcessAudioRef = useRef(true); // Track if we should process audio on stop
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const silenceStartTimeRef = useRef(null);
  const speechDetectedRef = useRef(false);
  const recordingStartTimeRef = useRef(null); // Track when recording started
  const currentAudioRef = useRef(null); // Track playing audio
  const audioQueueRef = useRef([]); // Track audio queue
  const streamReaderRef = useRef(null); // Track stream reader for cancellation
  const currentStreamIdRef = useRef(null); // Track current backend stream ID for cancellation
  const sessionIdRef = useRef(0); // Track session to prevent old responses
  const activeTimeoutsRef = useRef([]); // Track all active word timeouts

  useEffect(() => {
    // Auto-start listening when component mounts
    startListening();
  }, []);

  // Interrupt handler - stops assistant when user speaks
  const stopAssistantAndListen = async () => {
    console.log(' USER INTERRUPT DETECTED - Stopping assistant');

    // Increment session ID to invalidate old responses
    sessionIdRef.current += 1;
    console.log(` New session ID: ${sessionIdRef.current}`);

    // Cancel backend stream FIRST (most important)
    if (currentStreamIdRef.current) {
      try {
        console.log(` Cancelling backend stream ${currentStreamIdRef.current}`);
        await fetch(`http://localhost:5000/api/cancel/${currentStreamIdRef.current}`, {
          method: 'POST'
        });
        currentStreamIdRef.current = null;
      } catch (e) {
        console.log('Error cancelling stream:', e);
      }
    }

    // Clear ALL active timeouts (word-by-word rendering)
    console.log(` Clearing ${activeTimeoutsRef.current.length} active timeouts`);
    activeTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId));
    activeTimeoutsRef.current = [];

    // Stop current audio
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }

    // Clear audio queue
    audioQueueRef.current = [];

    // Cancel streaming response
    if (streamReaderRef.current) {
      try {
        streamReaderRef.current.cancel();
      } catch (e) {
        console.log('Stream already cancelled');
      }
      streamReaderRef.current = null;
    }

    // Clear response text IMMEDIATELY
    setResponseText('');
    setIsSpeaking(false);

    // Clear audio chunks and prevent processing current recording
    audioChunksRef.current = [];
    speechDetectedRef.current = false;
    silenceStartTimeRef.current = null;
    shouldProcessAudioRef.current = false; // Don't process the audio we're about to stop

    // Stop and restart recorder to clear any accumulated audio
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      console.log(' Stopping recorder to clear accumulated audio');
      mediaRecorderRef.current.stop(); // This will trigger onstop, which will restart
    } else if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'inactive') {
      console.log(' Restarting recorder after interrupt');
      audioChunksRef.current = [];
      shouldProcessAudioRef.current = true;
      mediaRecorderRef.current.start();
    }

    console.log(' Ready for new question');
  };

  const monitorAudioLevel = () => {
    if (!analyserRef.current || !mediaRecorderRef.current) return;

    const bufferLength = analyserRef.current.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    const SILENCE_THRESHOLD = 5; // Increased from 2 to ignore background noise
    const SILENCE_DURATION = 3000; // 3 seconds of silence before processing
    const INTERRUPT_THRESHOLD = 15; // Higher threshold to detect user speech during assistant speaking

    const checkAudioLevel = () => {
      if (!mediaRecorderRef.current || mediaRecorderRef.current.state !== 'recording') {
        return;
      }

      analyserRef.current.getByteTimeDomainData(dataArray);

      let sum = 0;
      for (let i = 0; i < bufferLength; i++) {
        const value = Math.abs(dataArray[i] - 128);
        sum += value;
      }
      const average = sum / bufferLength;
      const currentTime = Date.now();

      // Check for user interrupt during assistant speech OR text rendering
      if (average > INTERRUPT_THRESHOLD && (isSpeaking || responseText)) {
        console.log(' Interrupt detected! Audio level:', average);
        stopAssistantAndListen();
        return;
      }

      if (average > SILENCE_THRESHOLD) {
        if (!speechDetectedRef.current) {
          speechDetectedRef.current = true;
          setIsListening(true); // Show "Listening" only when voice detected
          console.log(' Speech detected');
        }
        silenceStartTimeRef.current = null;
      } else if (speechDetectedRef.current) {
        if (silenceStartTimeRef.current === null) {
          silenceStartTimeRef.current = currentTime;
        } else {
          const silenceDuration = currentTime - silenceStartTimeRef.current;
          if (silenceDuration >= SILENCE_DURATION) {
            // Stop and restart recorder to process this utterance
            console.log(' Silence detected - stopping recorder to process utterance');
            setIsListening(false);
            if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
              mediaRecorderRef.current.stop(); // This will trigger onstop → processAudio → restart
            }
            // Reset silence tracking
            silenceStartTimeRef.current = null;
          }
        }
      }

      requestAnimationFrame(checkAudioLevel);
    };

    checkAudioLevel();
  };

  const startListening = async () => {
    // Don't start if already monitoring
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      console.log('Already monitoring, skipping...');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 16000,
          channelCount: 1
        }
      });

      audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)();
      analyserRef.current = audioContextRef.current.createAnalyser();
      const source = audioContextRef.current.createMediaStreamSource(stream);
      source.connect(analyserRef.current);
      analyserRef.current.fftSize = 2048;

      speechDetectedRef.current = false;
      silenceStartTimeRef.current = null;
      audioChunksRef.current = [];
      shouldProcessAudioRef.current = true;

      mediaRecorderRef.current = new MediaRecorder(stream, {
        mimeType: 'audio/webm;codecs=opus'
      });

      // Save ALL data when recording stops
      mediaRecorderRef.current.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
          console.log(` Captured chunk: ${event.data.size} bytes`);
        }
      };

      mediaRecorderRef.current.onstop = async () => {
        console.log(` Recording stopped with ${audioChunksRef.current.length} chunks, shouldProcess: ${shouldProcessAudioRef.current}`);

        // Only process if we should (not interrupted)
        if (shouldProcessAudioRef.current && audioChunksRef.current.length > 0 && speechDetectedRef.current) {
          const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm;codecs=opus' });
          console.log(` Created audio blob: ${audioBlob.size} bytes`);

          // Clear for next recording
          audioChunksRef.current = [];
          speechDetectedRef.current = false;
          silenceStartTimeRef.current = null;

          // Process this audio
          await processAudio(audioBlob);
        } else {
          console.log('Skipping audio processing (interrupted or no speech)');
          audioChunksRef.current = [];
          speechDetectedRef.current = false;
          silenceStartTimeRef.current = null;
        }

        // Reset flag for next recording
        shouldProcessAudioRef.current = true;

        // Restart recording immediately (continuous monitoring)
        if (mediaRecorderRef.current && !isMuted) {
          console.log(' Restarting recorder for next utterance');
          audioChunksRef.current = [];
          mediaRecorderRef.current.start();  // 1s chunks
        }
      };

      // Start recording with 1-second chunks for faster processing
      mediaRecorderRef.current.start();  // 1000ms = 1 second chunks
      setIsMonitoring(true); // Mic is active continuously
      console.log(' Continuous monitoring started');

      monitorAudioLevel();

    } catch (error) {
      console.error('Error accessing microphone:', error);
    }
  };

  const processAudio = async (audioBlob) => {
    try {
      const reader = new FileReader();
      reader.readAsDataURL(audioBlob);
      reader.onloadend = async () => {
        const base64Audio = reader.result;

        // *** CRITICAL: Stop any active streams/audio BEFORE starting new request ***
        console.log('🛑 NEW REQUEST - Stopping any active streams/audio');

        // Increment session to invalidate old responses
        sessionIdRef.current += 1;
        const currentSessionId = sessionIdRef.current;
        console.log(` Starting new request with session ID: ${currentSessionId}`);

        // Cancel backend stream if exists
        if (currentStreamIdRef.current) {
          try {
            console.log(` Cancelling backend stream ${currentStreamIdRef.current}`);
            await fetch(`http://localhost:5000/api/cancel/${currentStreamIdRef.current}`, {
              method: 'POST'
            });
          } catch (e) {
            console.log('Error cancelling stream:', e);
          }
          currentStreamIdRef.current = null;
        }

        // Clear all active timeouts (word-by-word rendering)
        activeTimeoutsRef.current.forEach(timeoutId => clearTimeout(timeoutId));
        activeTimeoutsRef.current = [];

        // Stop current audio immediately
        if (currentAudioRef.current) {
          currentAudioRef.current.pause();
          currentAudioRef.current.currentTime = 0;
          currentAudioRef.current = null;
        }

        // Clear audio queue
        audioQueueRef.current = [];

        // Cancel streaming response reader
        if (streamReaderRef.current) {
          try {
            streamReaderRef.current.cancel();
          } catch (e) {
            console.log('Stream already cancelled');
          }
          streamReaderRef.current = null;
        }

        // Clear previous response and start streaming
        setResponseText('');
        setIsSpeaking(true);

        // Use refs for interrupt capability
        audioQueueRef.current = [];
        let isPlaying = false;
        let isProcessingStream = false;

        const playNextAudio = async () => {
          console.log('playNextAudio called. isPlaying:', isPlaying, 'queue length:', audioQueueRef.current.length, 'session:', currentSessionId);

          // Check if this session is still active (not interrupted)
          if (sessionIdRef.current !== currentSessionId) {
            console.log(' Session changed - stopping playback');
            isPlaying = false;
            return;
          }

          if (isPlaying || audioQueueRef.current.length === 0) return;
          isPlaying = true;

          const { audioBase64, text, words } = audioQueueRef.current.shift();
          console.log('Playing audio chunk, text:', text, 'words:', words);

          return new Promise((resolve) => {
            // Double-check session is still valid before creating audio
            if (sessionIdRef.current !== currentSessionId) {
              console.log(' Session changed before audio creation - skipping');
              isPlaying = false;
              resolve();
              return;
            }

            const audio = new Audio(`data:audio/mp3;base64,${audioBase64}`);
            currentAudioRef.current = audio; // Track for interrupt

            // Show words one by one using exact timings from SSML marks
            audio.onloadedmetadata = () => {
              // Check if we have exact timings from SSML
              const hasExactTimings = words.length > 0 && words[0].time_seconds !== undefined;

              if (hasExactTimings) {
                // Use exact timings from Google TTS
                words.forEach((wordData) => {
                  const delayMs = wordData.time_seconds * 1000;
                  const timeoutId = setTimeout(() => {
                    // Only render if session hasn't been interrupted
                    if (sessionIdRef.current === currentSessionId) {
                      setResponseText(prev => prev + (prev ? ' ' : '') + wordData.word);
                    }
                  }, delayMs);
                  activeTimeoutsRef.current.push(timeoutId);
                });
              } else {
                // Fallback to estimated timing
                const duration = audio.duration * 1000;
                const timePerWord = duration / words.length;
                words.forEach((wordData, index) => {
                  const timeoutId = setTimeout(() => {
                    // Only render if session hasn't been interrupted
                    if (sessionIdRef.current === currentSessionId) {
                      setResponseText(prev => prev + (prev ? ' ' : '') + wordData.word);
                    }
                  }, timePerWord * index);
                  activeTimeoutsRef.current.push(timeoutId);
                });
              }
            };

            audio.onended = () => {
              console.log('Audio chunk finished');
              currentAudioRef.current = null; // Clear ref
              isPlaying = false;
              resolve();
              // Play next chunk
              playNextAudio();
            };

            audio.onerror = (e) => {
              console.error('Audio playback error:', e);
              setResponseText(prev => prev + (prev ? ' ' : '') + text);
              isPlaying = false;
              resolve();
              playNextAudio();
            };

            // Final check before playing
            if (sessionIdRef.current !== currentSessionId) {
              console.log(' Session changed before play - aborting');
              isPlaying = false;
              resolve();
              return;
            }

            if (!isMuted) {
              audio.play().catch(e => {
                console.error('Audio play failed:', e);
                setResponseText(prev => prev + (prev ? ' ' : '') + text);
                isPlaying = false;
                resolve();
                playNextAudio();
              });
            } else {
              // If muted, just show text immediately
              setResponseText(prev => prev + (prev ? ' ' : '') + text);
              isPlaying = false;
              resolve();
              playNextAudio();
            }
          });
        };

        // Unified voice pipeline: Send audio directly, get streaming TTS response
        console.log('Sending audio to unified voice endpoint');
        const response = await fetch('/api/voice', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            audio: base64Audio,
            voice: selectedVoice
          })
        });

        console.log('Response received, status:', response.status);

        // Capture stream ID from response header
        const streamId = response.headers.get('X-Stream-ID');
        if (streamId) {
          currentStreamIdRef.current = streamId;
          console.log(` Tracking stream ID: ${streamId}`);
        }

        // Check for errors
        if (!response.ok) {
          console.error('Stream error:', response.status);
          setIsSpeaking(false);
          currentStreamIdRef.current = null;
          setTimeout(() => startListening(), 1000);
          return;
        }

        const reader2 = response.body.getReader();
        streamReaderRef.current = reader2; // Track for interrupt
        const decoder = new TextDecoder();

        console.log('Starting to read stream...');

        let buffer = '';  // Buffer to accumulate incomplete messages

        try {
          while (true) {
            const { done, value } = await reader2.read();
            if (done) {
              console.log('Stream done');
              break;
            }

            // Append new data to buffer
            buffer += decoder.decode(value, { stream: true });
            console.log('Buffer size:', buffer.length);

            // Process complete lines from buffer
            const lines = buffer.split('\n');
            // Keep the last incomplete line in the buffer
            buffer = lines.pop() || '';

            for (const line of lines) {
              if (line.startsWith('data: ')) {
                try {
                  const jsonStr = line.slice(6);
                  const data = JSON.parse(jsonStr);

                  if (data.done) {
                    console.log('Received done signal');
                    setIsSpeaking(false);
                    streamReaderRef.current = null;
                    currentStreamIdRef.current = null; // Clear stream ID
                    // Don't restart listening - microphone should already be monitoring for interrupts
                    console.log('Response complete, ready for next question');
                    break;
                  }

                  // Queue text and audio together - but ONLY if session is still valid
                  if (data.audio && data.text) {
                    // Check if this response is from the current session
                    if (sessionIdRef.current !== currentSessionId) {
                      console.log(' Ignoring audio from old session');
                      continue;
                    }

                    console.log('Received complete message, text length:', data.text.length);
                    audioQueueRef.current.push({
                      audioBase64: data.audio,
                      text: data.text,
                      words: data.words || []
                    });
                    console.log('Queue length:', audioQueueRef.current.length, 'isPlaying:', isPlaying);
                    playNextAudio();
                  }
                } catch (e) {
                  console.error('Parse error:', e, 'Line preview:', line.substring(0, 100));
                }
              }
            }
          }
        } catch (streamError) {
          console.error('Stream reading error:', streamError);
          setIsSpeaking(false);
          currentStreamIdRef.current = null; // Clear stream ID
          // Don't restart - microphone should still be monitoring
        }
      };

    } catch (error) {
      console.error('Error processing audio:', error);
      setIsSpeaking(false);
      currentStreamIdRef.current = null; // Clear stream ID
      // Don't restart - microphone should still be monitoring
    }
  };

  const toggleMute = () => {
    const newMutedState = !isMuted;
    setIsMuted(newMutedState);

    if (newMutedState) {
      // Muting: Stop continuous recording completely
      if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
        mediaRecorderRef.current.stop();
        mediaRecorderRef.current = null;
      }

      // Close audio context
      if (audioContextRef.current) {
        audioContextRef.current.close();
        audioContextRef.current = null;
      }

      setIsListening(false);
      setIsMonitoring(false);

      // Stop assistant if speaking
      if (currentAudioRef.current) {
        currentAudioRef.current.pause();
        currentAudioRef.current = null;
      }
      audioQueueRef.current = [];
      audioChunksRef.current = [];
      setIsSpeaking(false);

      console.log(' Muted - stopped recording');
    } else {
      // Unmuting: Resume continuous recording
      console.log(' Unmuted - resuming continuous recording');
      setTimeout(() => startListening(), 100);
    }
  };

  const endSession = () => {
    // Stop continuous recording
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      mediaRecorderRef.current.stop();
      mediaRecorderRef.current = null;
    }

    // Close audio context
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }

    // Clear all state
    setIsListening(false);
    setIsSpeaking(false);
    setIsMonitoring(false);
    setResponseText('');
    audioChunksRef.current = [];
    audioQueueRef.current = [];

    // Stop any playing audio
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }

    console.log(' Session ended - continuous recording stopped');
  };

  const getOrbClass = () => {
    if (isSpeaking) return 'orb speaking';
    if (isListening) return 'orb listening';
    return 'orb';
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
        {!isListening && !isSpeaking && isMonitoring && 'Ready'}
        {!isListening && !isSpeaking && !isMonitoring && 'Ready'}
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
            setSelectedVoice(e.target.value);
            console.log('🎙️ Voice changed - recorder continues running');
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

      {responseText && (
        <button className="end-session" onClick={endSession}>
          End Session
        </button>
      )}
    </div>
  );
}

export default App;