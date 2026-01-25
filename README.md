# Voice Assistant

Python voice assistant with React frontend using local audio processing and Google AI Studio.

## Structure

```
POC/
├── backend/          # Flask API
│   ├── app.py
│   └── requirements.txt
└── frontend/         # React app
    ├── src/
    ├── public/
    └── package.json
```

## Quick Start

### Backend
```bash
cd backend
pip install -r requirements.txt
set GEMINI_API_KEY=your_api_key_here
python app.py
```

### Frontend
```bash
cd frontend
npm install
npm start
```

## Features

- **STT**: Google API for speech to text
- **LLM**: Gemini 2.5 Flash lite
- **TTS**: Google API for text to speech
- **Frontend**: Modern React UI with real-time status updates
