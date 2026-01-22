import React, { useState, useRef } from 'react';
import axios from 'axios';
import './App.css';

function App() {
  const [messages, setMessages] = useState([]);
  const [status, setStatus] = useState('Ready');
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);
  const audioContextRef = useRef(null);
  const analyserRef = useRef(null);
  const silenceStartTimeRef = useRef(null);
  const speechDetectedRef = useRef(false);

  const monitorAudioLevel = () => {
    if (!analyserRef.current || !mediaRecorderRef.current) return;

    const bufferLength = analyserRef.current.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    const SILENCE_THRESHOLD = 2; // Audio level threshold (0-255) - silence is ~0.5, speech is 8+
    const SILENCE_DURATION = 2000; // 2 seconds of continuous silence stops recording

    const checkAudioLevel = () => {
      if (!mediaRecorderRef.current || mediaRecorderRef.current.state !== 'recording') {
        return;
      }

      analyserRef.current.getByteTimeDomainData(dataArray);

      // Calculate average volume
      let sum = 0;
      for (let i = 0; i < bufferLength; i++) {
        const value = Math.abs(dataArray[i] - 128);
        sum += value;
      }
      const average = sum / bufferLength;

      const currentTime = Date.now();

      // Detect speech vs silence
      if (average > SILENCE_THRESHOLD) {
        // Speech detected
        if (!speechDetectedRef.current) {
          console.log('✅ Speech started! Average:', average.toFixed(2));
          speechDetectedRef.current = true;
        }

        // Reset silence timer - we're speaking again
        silenceStartTimeRef.current = null;
        setStatus('🔴 Recording... (listening)');

      } else if (speechDetectedRef.current) {
        // Silence detected after speech has started

        if (silenceStartTimeRef.current === null) {
          // First frame of silence - start the timer
          silenceStartTimeRef.current = currentTime;
          console.log('⏸️  Silence started');
          setStatus('🔴 Recording... (silence detected)');
        } else {
          // Check how long we've been silent
          const silenceDuration = currentTime - silenceStartTimeRef.current;

          if (silenceDuration >= SILENCE_DURATION) {
            // 2 full seconds of continuous silence!
            console.log('🛑 Stopping: 2 seconds of continuous silence');
            mediaRecorderRef.current.stop();
            setIsRecording(false);
            return; // Stop monitoring
          }
        }
      }

      // Continue monitoring
      requestAnimationFrame(checkAudioLevel);
    };

    checkAudioLevel();
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate: 16000
        }
      });

      // Setup Voice Activity Detection
      audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)();
      analyserRef.current = audioContextRef.current.createAnalyser();
      const source = audioContextRef.current.createMediaStreamSource(stream);
      source.connect(analyserRef.current);
      analyserRef.current.fftSize = 2048;

      speechDetectedRef.current = false;
      silenceStartTimeRef.current = null;

      mediaRecorderRef.current = new MediaRecorder(stream);
      audioChunksRef.current = [];

      mediaRecorderRef.current.ondataavailable = (event) => {
        audioChunksRef.current.push(event.data);
      };

      mediaRecorderRef.current.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });

        // Cleanup
        if (audioContextRef.current) {
          audioContextRef.current.close();
        }

        stream.getTracks().forEach(track => track.stop());
        await processAudio(audioBlob);
      };

      mediaRecorderRef.current.start();
      setIsRecording(true);
      setStatus('🔴 Recording... (speak now)');

      // Start VAD monitoring
      monitorAudioLevel();

      // Maximum recording time: 30 seconds (safety timeout)
      setTimeout(() => {
        if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
          mediaRecorderRef.current.stop();
          setIsRecording(false);
        }
      }, 30000);

    } catch (error) {
      if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
        setStatus('❌ Microphone access denied. Please enable in browser settings.');
      } else if (error.name === 'NotFoundError') {
        setStatus('❌ No microphone found. Please connect a microphone.');
      } else {
        setStatus('❌ Error accessing microphone: ' + error.message);
      }
      console.error('Error accessing microphone:', error);
    }
  };

  const processAudio = async (audioBlob) => {
    setIsProcessing(true);

    try {
      // Convert blob to base64
      const reader = new FileReader();
      reader.readAsDataURL(audioBlob);
      reader.onloadend = async () => {
        const base64Audio = reader.result;

        // Transcribe
        setStatus('🎧 Transcribing...');
        const transcribeResponse = await axios.post('/api/transcribe', {
          audio: base64Audio
        });

        const userText = transcribeResponse.data.text;
        setMessages(prev => [...prev, { role: 'user', text: userText }]);

        // Get AI response
        setStatus('🤖 Thinking...');
        const chatResponse = await axios.post('/api/chat', {
          message: userText
        });

        const assistantText = chatResponse.data.response;

        // Speak response and show text simultaneously
        setStatus('🔊 Speaking...');
        setMessages(prev => [...prev, { role: 'assistant', text: assistantText }]);

        // Get TTS audio from backend
        const speakResponse = await axios.post('/api/speak', {
          text: assistantText
        });

        // Play audio in browser
        const audio = new Audio(speakResponse.data.audio);
        audio.play();

        // Wait for audio to finish before setting Ready
        audio.onended = () => {
          setStatus('✅ Ready');
        };

        // Handle errors
        audio.onerror = () => {
          console.error('Audio playback error');
          setStatus('✅ Ready');
        };
      };

    } catch (error) {
      setStatus('❌ Error: ' + error.message);
      console.error('Error processing audio:', error);
    } finally {
      setIsProcessing(false);
    }
  };

  return (
    <div className="App">
      <div className="container">
        <h1>🎤 Voice Assistant</h1>

        <div className="status">{status}</div>

        <button
          className={`record-button ${isRecording ? 'recording' : ''}`}
          onClick={startRecording}
          disabled={isRecording || isProcessing}
        >
          {isRecording ? '🔴 Recording...' : '🎙️ Start Recording'}
        </button>

        <div className="messages-container">
          <h3>Conversation</h3>
          <div className="messages">
            {messages.length === 0 ? (
              <p className="empty-state">Click the button to start a conversation</p>
            ) : (
              messages.map((msg, index) => (
                <div key={index} className={`message ${msg.role}`}>
                  <strong>{msg.role === 'user' ? 'You' : 'Assistant'}:</strong>
                  <p>{msg.text}</p>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
