from concurrent.futures import ThreadPoolExecutor

_executor = ThreadPoolExecutor(max_workers=20)
_SENTINEL = object()
