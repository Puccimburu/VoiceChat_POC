"""Qdrant vector search service with Gemini embeddings for voice assistant"""
import time
import threading
from google import genai
from google.genai import types
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, Filter, FieldCondition, MatchValue
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
                # Ensure payload index exists on 'source' field (required for document filtering)
                try:
                    _qdrant_client.create_payload_index(
                        collection_name=QDRANT_COLLECTION_NAME,
                        field_name="source",
                        field_schema="keyword",
                    )
                    print("Payload index on 'source' created (or already exists)")
                except Exception as idx_err:
                    print(f"Payload index warning (safe to ignore if index exists): {idx_err}")
    return _qdrant_client

# ---------------------------------------------------------------------------
# Voice search — search collection by transcript
# ---------------------------------------------------------------------------
def voice_search(query, top_k=64, document_filter=None):
    """Embed the query with Gemini and search for relevant document chunks.

    Args:
        query: The search query text
        top_k: Maximum number of results to return
        document_filter: Optional document filename to filter results by
    """
    query_vector = gemini_embed(query)

    client = get_qdrant_client()

    # Build filter if document_filter is specified
    query_filter = None
    if document_filter:
        print(f"🔍 Filtering by document: '{document_filter}'")
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="source",
                    match=MatchValue(value=document_filter)
                )
            ]
        )

    # Retry logic for transient SSL errors
    max_retries = 3
    for attempt in range(max_retries):
        try:
            results = client.query_points(
                collection_name=QDRANT_COLLECTION_NAME,
                query=query_vector,
                limit=top_k,
                query_filter=query_filter,
            )

            # Debug: print which documents were returned
            if results.points:
                sources = set(p.payload.get('source', 'unknown') for p in results.points[:5])
                print(f"📄 Returned {len(results.points)} chunks from documents: {sources}")
            else:
                print(f"⚠️ No results found for filter: {document_filter}")

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

# ---------------------------------------------------------------------------
# Get list of documents in collection
# ---------------------------------------------------------------------------
def get_document_list():
    """Retrieve unique document names from the Qdrant collection."""
    client = get_qdrant_client()
    try:
        # Get collection info first
        collection_info = client.get_collection(QDRANT_COLLECTION_NAME)
        print(f"Collection '{QDRANT_COLLECTION_NAME}' has {collection_info.points_count} points")

        # Scroll through all points to get unique document names
        documents = set()
        offset = None
        limit = 100
        total_points_scanned = 0

        while True:
            result = client.scroll(
                collection_name=QDRANT_COLLECTION_NAME,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )

            points, next_offset = result
            total_points_scanned += len(points)

            # Debug: print first few payloads to see structure
            if total_points_scanned <= 3:
                for i, point in enumerate(points[:3]):
                    print(f"Sample point {i+1} payload keys: {list(point.payload.keys()) if point.payload else 'None'}")
                    if point.payload:
                        print(f"  Payload: {point.payload}")

            for point in points:
                if point.payload:
                    # Check for 'source' or 'source_file' for backward compatibility
                    source = point.payload.get('source') or point.payload.get('source_file')
                    if source:
                        # Extract just the filename from the path
                        filename = source.split('/')[-1] if '/' in source else source.split('\\')[-1]
                        documents.add(filename)
                        print(f"Found document: {filename}")

            if next_offset is None:
                break
            offset = next_offset

        print(f"Scanned {total_points_scanned} points, found {len(documents)} unique documents: {sorted(documents)}")
        return sorted(list(documents))
    except Exception as e:
        print(f"Error retrieving document list: {e}")
        import traceback
        traceback.print_exc()
        return []

# ---------------------------------------------------------------------------
# Upload single document
# ---------------------------------------------------------------------------
def upload_document(file_content, filename):
    """Upload a single PDF document to Qdrant collection.

    Args:
        file_content: Binary content of the PDF file
        filename: Name of the file

    Returns:
        dict: Status with 'success' bool and 'message' string
    """
    from PyPDF2 import PdfReader
    from qdrant_client.http.models import PointStruct
    import io

    try:
        # Extract text from PDF
        pdf_reader = PdfReader(io.BytesIO(file_content))
        text = ""
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"

        if not text.strip():
            return {"success": False, "message": "No text could be extracted from the PDF"}

        # Chunk the text
        def chunk_text(text, chunk_size=200, overlap=0):
            words = text.split()
            chunks = []
            start = 0
            while start < len(words):
                end = start + chunk_size
                chunk = " ".join(words[start:end])
                if chunk.strip():
                    chunks.append(chunk)
                start += chunk_size - overlap
            return chunks

        chunks = chunk_text(text, chunk_size=200, overlap=0)
        print(f"Processing {filename}: {len(text)} chars, {len(chunks)} chunks")

        # Get next available point ID
        client = get_qdrant_client()
        collection_info = client.get_collection(QDRANT_COLLECTION_NAME)
        next_point_id = collection_info.points_count

        # Embed in batches
        BATCH_SIZE = 20
        points = []

        for batch_start in range(0, len(chunks), BATCH_SIZE):
            batch_chunks = chunks[batch_start:batch_start + BATCH_SIZE]
            vectors = gemini_embed_batch(batch_chunks)

            for i, (chunk, vector) in enumerate(zip(batch_chunks, vectors)):
                point = PointStruct(
                    id=next_point_id,
                    vector=vector,
                    payload={
                        "document_name": filename,
                        "chunk_index": batch_start + i,
                        "text": chunk,
                        "source": filename,
                    },
                )
                points.append(point)
                next_point_id += 1

        # Upsert all points
        for batch_start in range(0, len(points), 100):
            batch = points[batch_start:batch_start + 100]
            client.upsert(
                collection_name=QDRANT_COLLECTION_NAME,
                wait=True,
                points=batch,
            )

        return {
            "success": True,
            "message": f"Successfully uploaded {filename} ({len(chunks)} chunks)",
            "filename": filename,
            "chunks": len(chunks)
        }

    except Exception as e:
        print(f"Error uploading document: {e}")
        return {"success": False, "message": f"Upload failed: {str(e)}"}
