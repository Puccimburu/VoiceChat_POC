# Voice Assistant Backend

Flask API for voice assistant with STT, LLM, and TTS capabilities.

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set your Gemini API key:
```bash
set GEMINI_API_KEY=your_api_key_here
```

## Run

```bash
python app.py
```

Server runs on http://localhost:5000

## API Endpoints

- `POST /api/transcribe` - Convert audio to text using Whisper
- `POST /api/chat` - Get response from Gemini AI
- `POST /api/speak` - Convert text to speech using pyttsx3
