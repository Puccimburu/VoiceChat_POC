"""Stream cancellation manager"""
import logging

logger = logging.getLogger(__name__)

# Track active streams for interrupt cancellation
active_streams = {}


def cancel_active_streams():
    """Cancel all active streams

    Returns:
        int: Number of streams cancelled
    """
    cancelled_count = 0
    streams_to_cancel = []

    for stream_id in list(active_streams.keys()):
        if not active_streams[stream_id]:  # Not already cancelled
            active_streams[stream_id] = True
            streams_to_cancel.append(stream_id)
            cancelled_count += 1

    if cancelled_count > 0:
        logger.info(f" Auto-interrupting {cancelled_count} active stream(s): {streams_to_cancel}")

    return cancelled_count


def register_stream(stream_id):
    """Register a new stream

    Args:
        stream_id: Unique stream identifier
    """
    active_streams[stream_id] = False


def is_stream_cancelled(stream_id):
    """Check if stream is cancelled

    Args:
        stream_id: Stream identifier

    Returns:
        bool: True if cancelled
    """
    return active_streams.get(stream_id, False)


def cleanup_stream(stream_id):
    """Remove stream from tracking

    Args:
        stream_id: Stream identifier
    """
    active_streams.pop(stream_id, None)
    logger.info(f"🧹 Cleaned up stream {stream_id}")
