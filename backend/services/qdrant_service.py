"""Qdrant vector search service with Gemini embeddings for voice assistant"""
import time
import threading
from google import genai
from google.genai import types
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams
from config import QDRANT_CLUSTER_URL, QDRANT_API_KEY, QDRANT_COLLECTION_NAME, GEMINI_API_KEY

VECTOR_SIZE = 768
EMBEDDING_MODEL = "gemini-embedding-001"
_EMBED_CONFIG = types.EmbedContentConfig(output_dimensionality=VECTOR_SIZE)

# ---------------------------------------------------------------------------
# Singleton: Gemini client for embeddings
# ---------------------------------------------------------------------------
_genai_client = None
_genai_lock = threading.Lock()

def get_genai_client():
    global _genai_client
    if _genai_client is None:
        with _genai_lock:
            if _genai_client is None:
                _genai_client = genai.Client(api_key=GEMINI_API_KEY)
                print("Gemini embedding client initialized")
    return _genai_client

# ---------------------------------------------------------------------------
# Embed text using Gemini
# ---------------------------------------------------------------------------
def gemini_embed(text):
    """Embed a single text string using Gemini embedding (768-dim)."""
    client = get_genai_client()
    max_retries = 3
    for attempt in range(max_retries):
        try:
            result = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=text,
                config=_EMBED_CONFIG,
            )
            return result.embeddings[0].values
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Gemini embed failed (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(0.5 * (attempt + 1))
            else:
                raise

def gemini_embed_batch(texts):
    """Embed a list of texts using Gemini embedding (768-dim)."""
    client = get_genai_client()
    max_retries = 3
    for attempt in range(max_retries):
        try:
            result = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=texts,
                config=_EMBED_CONFIG,
            )
            return [e.values for e in result.embeddings]
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Gemini embed_batch failed (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(0.5 * (attempt + 1))
            else:
                raise

# ---------------------------------------------------------------------------
# Singleton: QdrantClient
# ---------------------------------------------------------------------------
_qdrant_client = None
_client_lock = threading.Lock()

def get_qdrant_client():
    global _qdrant_client
    if _qdrant_client is None:
        with _client_lock:
            if _qdrant_client is None:
                start = time.perf_counter()
                _qdrant_client = QdrantClient(
                    url=QDRANT_CLUSTER_URL,
                    api_key=QDRANT_API_KEY,
                )
                print(f"Qdrant client initialized in {time.perf_counter() - start:.2f}s")
    return _qdrant_client

# ---------------------------------------------------------------------------
# Voice search — search collection by transcript
# ---------------------------------------------------------------------------
def voice_search(query, top_k=64):
    """Embed the query with Gemini and search for relevant document chunks."""
    query_vector = gemini_embed(query)

    client = get_qdrant_client()

    # Retry logic for transient SSL errors
    max_retries = 3
    for attempt in range(max_retries):
        try:
            results = client.query_points(
                collection_name=QDRANT_COLLECTION_NAME,
                query=query_vector,
                limit=top_k,
            )
            return results.points
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Qdrant query failed (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(0.5 * (attempt + 1))  # Exponential backoff
            else:
                print(f"Qdrant query failed after {max_retries} attempts: {e}")
                raise

# ---------------------------------------------------------------------------
# Create / recreate collection
# ---------------------------------------------------------------------------
def create_voice_collection():
    """Creates the collection if it doesn't already exist."""
    client = get_qdrant_client()
    try:
        client.create_collection(
            collection_name=QDRANT_COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        print(f"Collection '{QDRANT_COLLECTION_NAME}' created successfully.")
    except Exception as e:
        print(f"Collection '{QDRANT_COLLECTION_NAME}' already exists or error: {e}")
    info = client.get_collection(QDRANT_COLLECTION_NAME)
    print(f"'{QDRANT_COLLECTION_NAME}': {info.points_count} points, vector size: {info.config.params.vectors.size}")
    return info

def recreate_voice_collection():
    """Delete and recreate collection with 768-dim vectors for Gemini embeddings."""
    client = get_qdrant_client()
    try:
        client.delete_collection(collection_name=QDRANT_COLLECTION_NAME)
        print(f"Deleted old collection '{QDRANT_COLLECTION_NAME}'")
    except Exception as e:
        print(f"Could not delete collection: {e}")
    client.create_collection(
        collection_name=QDRANT_COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )
    info = client.get_collection(QDRANT_COLLECTION_NAME)
    print(f"Recreated '{QDRANT_COLLECTION_NAME}': vector size {info.config.params.vectors.size}")
    return info
