from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=60)
_SENTINEL = object()
