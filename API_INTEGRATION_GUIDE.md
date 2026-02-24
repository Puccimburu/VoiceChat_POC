# Voice Agent API - Integration Guide

## Overview

This API provides **voice agent services** similar to 11 Labs - you get the backend API, not the UI. Integrate it into any frontend or phone system.

## Architecture

```
┌─────────────────┐
│  Your Frontend  │
│   or System     │
└────────┬────────┘
         │
         │ REST API + API Key
         ▼
┌─────────────────────────────┐
│   Voice Agent API (Port 5001)│
│   ┌──────────────────────┐  │
│   │  MongoDB Agent       │  │ ← Queries clubhouse database
│   │  Document RAG        │  │ ← Queries uploaded PDFs
│   │  STT + TTS          │  │ ← Voice processing
│   └──────────────────────┘  │
└─────────────────────────────┘
         │
         ├──► MongoDB (Customer data)
         └──► Qdrant (Document vectors)
```

## Quick Start

### 1. Start the API Server

```bash
cd backend
python app_api.py
```

Server runs on: `http://localhost:5001`

### 2. Create an API Key

```bash
curl -X POST http://localhost:5001/api/keys/create \
  -H "Content-Type: application/json" \
  -d '{
    "customer_name": "My Company",
    "customer_email": "contact@mycompany.com"
  }'
```

Response:
```json
{
  "api_key": "va_abc123def456...",
  "customer_id": "CUST_7f3a2b1c",
  "customer_name": "My Company",
  "message": "API key created successfully"
}
```

**Save this API key!** You'll use it for all requests.

### 3. Make Your First Request

```bash
curl -X POST http://localhost:5001/api/agent/query \
  -H "X-API-Key: va_abc123def456..." \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What swimming classes are available?",
    "return_audio": false
  }'
```

Response:
```json
{
  "query": "What swimming classes are available?",
  "response": "We have two swimming classes available. There's Swimming for Beginners...",
  "timestamp": "2026-02-19T16:30:00"
}
```

## API Endpoints

### 🔑 Authentication

All endpoints (except `/health`) require an API key in the header:

```
X-API-Key: va_your_api_key_here
```

Or:

```
Authorization: Bearer va_your_api_key_here
```

---

### 📊 Database Agent

#### `POST /api/agent/query`

Query the clubhouse database using natural language.

**Request:**
```json
{
  "query": "What swimming classes are available?",
  "return_audio": false  // optional
}
```

**Response:**
```json
{
  "query": "What swimming classes are available?",
  "response": "We have two swimming classes...",
  "audio": "base64_encoded_mp3",  // if return_audio=true
  "timestamp": "2026-02-19T..."
}
```

**Use Cases:**
- "What facilities do you have?"
- "Is the gym open right now?"
- "Show me today's bookings"
- "Who is the yoga instructor?"
- "Book me the tennis court for tomorrow at 6pm"

---

#### `POST /api/agent/voice`

Voice-to-voice: Send audio, get audio response.

**Request:**
- Content-Type: `multipart/form-data`
- Field: `audio` (file: .webm, .ogg, .mp3, .wav)

**Response:**
```json
{
  "transcript": "What swimming classes are available?",
  "response": "We have two swimming classes...",
  "audio": "base64_encoded_mp3"
}
```

**Example (JavaScript):**
```javascript
const formData = new FormData();
formData.append('audio', audioBlob, 'recording.webm');

const response = await fetch('http://localhost:5001/api/agent/voice', {
  method: 'POST',
  headers: {
    'X-API-Key': 'va_your_key_here'
  },
  body: formData
});

const data = await response.json();
console.log('AI said:', data.response);

// Play audio response
const audioElement = new Audio(`data:audio/mp3;base64,${data.audio}`);
audioElement.play();
```

---

### 📄 Document Queries (RAG)

#### `GET /api/documents/list`

List all uploaded documents.

**Response:**
```json
{
  "documents": ["contract.pdf", "agreement.pdf"],
  "count": 2
}
```

#### `POST /api/documents/query`

Query documents using RAG (Retrieval Augmented Generation).

**Request:**
```json
{
  "query": "What are the parties in the contract?",
  "document": "contract.pdf",  // optional filter
  "return_audio": false
}
```

**Response:**
```json
{
  "query": "What are the parties in the contract?",
  "response": "The parties are API Software Limited and How Group Services Limited.",
  "chunks_found": 5,
  "audio": "base64...",  // if return_audio=true
  "timestamp": "2026-02-19T..."
}
```

---

### 📈 Usage Tracking

#### `GET /api/keys/usage`

Get your API usage statistics.

**Response:**
```json
{
  "customer_id": "CUST_7f3a2b1c",
  "customer_name": "My Company",
  "usage_count": 42,
  "last_used": "2026-02-19T16:30:00",
  "created_at": "2026-02-19T10:00:00"
}
```

---

### ❤️ Health Check

#### `GET /health`

Check if the API is running.

**Response:**
```json
{
  "status": "healthy",
  "service": "Voice Agent API",
  "version": "1.0.0"
}
```

---

## Integration Examples

### React Integration

```javascript
// services/voiceAgent.js
const API_BASE_URL = 'http://localhost:5001';
const API_KEY = 'va_your_key_here';

export async function queryAgent(question, returnAudio = false) {
  const response = await fetch(`${API_BASE_URL}/api/agent/query`, {
    method: 'POST',
    headers: {
      'X-API-Key': API_KEY,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      query: question,
      return_audio: returnAudio
    })
  });

  return await response.json();
}

// Usage in component
import { queryAgent } from './services/voiceAgent';

function ChatComponent() {
  const handleQuery = async () => {
    const result = await queryAgent("What classes are available?");
    console.log(result.response);
  };

  return <button onClick={handleQuery}>Ask Agent</button>;
}
```

### Python Integration

```python
import requests

API_BASE_URL = "http://localhost:5001"
API_KEY = "va_your_key_here"

def query_agent(question):
    response = requests.post(
        f"{API_BASE_URL}/api/agent/query",
        headers={
            "X-API-Key": API_KEY,
            "Content-Type": "application/json"
        },
        json={"query": question}
    )
    return response.json()

# Usage
result = query_agent("What swimming classes are available?")
print(result['response'])
```

### Phone System Integration (Twilio)

```python
from twilio.twiml.voice_response import VoiceResponse
import requests

@app.route("/voice", methods=['POST'])
def voice():
    """Handle incoming phone call"""
    response = VoiceResponse()

    # Record user's question
    response.record(maxLength=10, action='/handle_recording')

    return str(response)

@app.route("/handle_recording", methods=['POST'])
def handle_recording():
    """Process recording with Voice Agent API"""
    recording_url = request.values.get("RecordingUrl")

    # Download recording
    audio = requests.get(recording_url).content

    # Send to Voice Agent API
    response = requests.post(
        "http://localhost:5001/api/agent/voice",
        headers={"X-API-Key": "va_your_key_here"},
        files={"audio": audio}
    )

    data = response.json()

    # Play AI response
    twiml = VoiceResponse()
    twiml.play(f"data:audio/mp3;base64,{data['audio']}")

    return str(twiml)
```

---

## Testing

Run the automated test suite:

```bash
# Make sure API server is running first
python backend/test_api.py
```

This will:
1. Create a test API key
2. Test all endpoints
3. Show usage statistics
4. Print integration examples

---

## Database Collections

The MongoDB agent queries these collections:

- **facilities**: Gym, pool, tennis court, squash, yoga studio
- **members**: Member profiles with membership types
- **classes**: Yoga, swimming, HIIT classes with schedules
- **bookings**: Current and upcoming facility bookings
- **staff**: Instructors, trainers, and staff details

---

## Production Deployment

### Environment Variables

```bash
# .env file
GEMINI_API_KEY=your_gemini_key
QDRANT_CLUSTER_URL=your_qdrant_url
QDRANT_API_KEY=your_qdrant_key
MONGO_URI=mongodb://localhost:27017/
SECRET_KEY=your_secret_key
```

### Security Recommendations

1. **HTTPS Only**: Use SSL/TLS in production
2. **Rate Limiting**: Add rate limits per API key
3. **API Key Rotation**: Allow customers to rotate keys
4. **IP Whitelisting**: Optionally restrict by IP
5. **Logging**: Log all API requests for audit

### Scaling

- **MongoDB**: Use MongoDB Atlas for managed hosting
- **Qdrant**: Use Qdrant Cloud for vector storage
- **Load Balancer**: Use Nginx/HAProxy for multiple instances
- **Redis**: Cache agent instances and frequent queries

---

## Support

For integration help, contact your account manager or email support@voiceagent.com

---

## Pricing Model (Example)

| Plan | API Calls/Month | Price |
|------|----------------|-------|
| Free | 1,000 | $0 |
| Starter | 10,000 | $29 |
| Professional | 100,000 | $199 |
| Enterprise | Unlimited | Custom |

Each call = 1 STT + 1 LLM + 1 TTS operation
