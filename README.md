Frontend (React) <--> Backend (Flask + Socket.IO) <--> Google Cloud APIs
|                        |
WebSocket              STT / Gemini / TTS



## Prerequisites

- Python 3.12+
- Node.js 18+
- Google Cloud account with:
  - Speech-to-Text API enabled
  - Text-to-Speech API enabled
  - Service account JSON key
- Gemini API key

## Setup

### Backend

1. Navigate to backend directory:
   ```bash
   cd backend
Create virtual environment:


python -m venv venv
Activate virtual environment:


source venv/Scripts/activate
Install dependencies:


pip install -r requirements.txt
Create .env file:


GEMINI_API_KEY=your_gemini_api_key
GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\your\google-credentials.json
SECRET_KEY=your_secret_key
GRPC_DNS_RESOLVER=native
Run the backend:


python app_streaming.py
Backend runs on http://localhost:5000

Frontend
Navigate to frontend directory:


cd frontend
Install dependencies:


npm install
Start the development server:


npm start
Frontend runs on http://localhost:3000


Usage

Start the backend server first
Start the frontend development server
Open http://localhost:3000 in your browser
Click to enable microphone access
Speak your question - the app will:
Stream your voice to Google STT
Send transcript to Gemini AI
Stream the response back as audio via TTS


Features

Real-time streaming speech-to-text
Gemini AI for natural language responses
Text-to-speech audio playback
Barge-in support (interrupt AI while speaking)
Session management for conversation context

Tech Stack

Frontend: React, Socket.IO Client
Backend: Flask, Flask-SocketIO, Python
APIs: Google Cloud Speech-to-Text, Google Cloud Text-to-Speech, Gemini AI

