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

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          sampleRate: 16000
        }
      });

      mediaRecorderRef.current = new MediaRecorder(stream);
      audioChunksRef.current = [];

      mediaRecorderRef.current.ondataavailable = (event) => {
        audioChunksRef.current.push(event.data);
      };

      mediaRecorderRef.current.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });
        stream.getTracks().forEach(track => track.stop());
        await processAudio(audioBlob);
      };

      mediaRecorderRef.current.start();
      setIsRecording(true);
      setStatus('🔴 Recording...');

    } catch (error) {
      setStatus('❌ Microphone access denied');
      console.error('Error accessing microphone:', error);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
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
          onClick={isRecording ? stopRecording : startRecording}
          disabled={isProcessing}
        >
          {isRecording ? '⏹️ Stop Recording' : '🎙️ Start Recording'}
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
