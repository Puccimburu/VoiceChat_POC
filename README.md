# Voice Agent Platform

Embeddable voice AI widget for any website. Customers speak naturally — the platform handles speech recognition, AI reasoning against your MongoDB data, and text-to-speech response delivery in real time.

## Architecture

```
Customer Site (site 2)
  └── <script src="http://localhost:8081/chat-widget.js">
           │
           │  WebSocket (wss://:8443)        REST (https://:8444)
           ▼                                        ▼
       nginx (TLS termination)──────────────────────┤
           │                                        │
           ▼                                        ▼
   ws_gateway.py (:8080)              Flask REST API (:5001)
   ├── ws_handler.py                  └── routes.py
   │   ├── pipeline/stt.py                ├── /api/data/*        (MongoDB CRUD)
   │   ├── pipeline/llm.py                ├── /api/agent/query   (REST agent)
   │   ├── pipeline/agent.py              ├── /admin/documents/*  (Qdrant upload)
   │   └── pipeline/tts.py                └── /api/widget/init
   └── static server (:8081)
       └── chat-widget.js / widget.js
           │
           ├── Google Cloud STT  (speech → transcript)
           ├── Gemini 2.5 Flash Lite  (reasoning + MongoDB agent)
           ├── Google Cloud TTS  (text → audio)
           ├── MongoDB  (customer data)
           ├── Qdrant  (document vectors / RAG)
           └── Redis  (session storage)
```

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 8080 | WS | WebSocket AI pipeline (internal, nginx proxies) |
| 8081 | HTTP | Widget static file server (chat-widget.js, widget.js) |
| 5001 | HTTP | Flask REST API (internal, nginx proxies) |
| 8443 | WSS | nginx TLS → :8080 |
| 8444 | HTTPS | nginx TLS → :5001 |

## Prerequisites

- Python 3.12+
- Node.js 18+
- Redis (WSL: `sudo service redis-server start`)
- MongoDB (local :27017)
- Qdrant cloud account
- Google Cloud project with Speech-to-Text + Text-to-Speech APIs enabled
- Google Cloud service account JSON key
- Gemini API key

## Setup

### Backend

```bash
cd backend
python -m venv venv
source venv/Scripts/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Create `backend/.env`:
```env
GEMINI_API_KEY=your_gemini_api_key
GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\google-credentials.json
API_KEY=your_api_key
MONGO_URI=mongodb://localhost:27017
MONGO_DB=Test
QDRANT_CLUSTER_URL=https://your-cluster.qdrant.io
QDRANT_API_KEY=your_qdrant_api_key
ALLOWED_ORIGINS=https://yourdomain.com   # leave empty to allow all (dev)
GRPC_DNS_RESOLVER=native
```

### Widget (frontend)

```bash
cd frontend
npm install
npm run build:chat-widget   # builds frontend/build/chat-widget.js
npm run build:widget        # builds frontend/build/widget.js
```

The gateway auto-rebuilds widgets on startup if source files are newer than the build output.

## Startup

```bash
# 1. Start Redis (WSL terminal)
sudo service redis-server start

# 2. Start the gateway (everything in one process)
cd backend
source venv/Scripts/activate
python ws_gateway.py
```

This starts three servers:
- WebSocket gateway on `:8080`
- Widget static server on `:8081`
- Flask REST API on `:5001`

### With nginx TLS (production)

Run nginx with your TLS config proxying `:8443 → :8080` and `:8444 → :5001`, then start the gateway as above.

## Embedding the Widget

Add to any HTML page:

```html
<script
  src="http://localhost:8081/chat-widget.js"
  data-api-key="your_api_key"
  data-agent-name="My Assistant"
  data-api-url="http://localhost:5001"
  data-ws-url="ws://localhost:8080/ws"
  data-mode="agent"
></script>
```

For production replace `localhost` URLs with your domain and use `wss://` / `https://`.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Widget | React 18, energy-based VAD (AnalyserNode) |
| Gateway | Python asyncio, websockets |
| STT | Google Cloud Speech-to-Text v1 (gRPC streaming) |
| LLM | Gemini 2.5 Flash Lite |
| TTS | Google Cloud Text-to-Speech (Neural2 voices) |
| REST API | Flask |
| Database | MongoDB |
| Vector DB | Qdrant (cloud) |
| Sessions | Redis |
| TLS | nginx |

## Testing

```bash
# Integration tests (server must be running)
cd tests
python test_system.py --ws wss://localhost:8443/ws --api https://localhost:8444

# Skip slow AI tests
python test_system.py --ws wss://localhost:8443/ws --api https://localhost:8444 --quick

# Unit tests (no server needed)
python test_unit.py
```
