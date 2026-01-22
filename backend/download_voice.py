import requests
import os

# Direct download from Piper releases
MODEL_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
CONFIG_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"

print("Downloading Piper voice model from HuggingFace...")

# Download model
print("Downloading model file (~63MB)...")
response = requests.get(MODEL_URL, stream=True)
total_size = int(response.headers.get('content-length', 0))

with open('en_US-lessac-medium.onnx', 'wb') as f:
    downloaded = 0
    for chunk in response.iter_content(chunk_size=8192):
        if chunk:
            f.write(chunk)
            downloaded += len(chunk)
            if total_size > 0:
                progress = (downloaded / total_size) * 100
                print(f"\rProgress: {progress:.1f}%", end='')

print("\n✓ Model downloaded")

# Download config
print("Downloading config file...")
response = requests.get(CONFIG_URL)
with open('en_US-lessac-medium.onnx.json', 'w', encoding='utf-8') as f:
    f.write(response.text)

print("✓ Config downloaded")
print("\nDone! Voice model ready to use.")
