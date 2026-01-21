from piper.download import ensure_voice_exists, find_voice, get_voices
import json

# Download high-quality English voice
voice_key = "en_US-lessac-medium"

print("Downloading Piper voice model...")
print(f"Voice: {voice_key}")

try:
    # Download the voice
    model_path, config_path = ensure_voice_exists(voice_key, ".")
    print(f"✓ Model downloaded: {model_path}")
    print(f"✓ Config downloaded: {config_path}")
except Exception as e:
    print(f"Error: {e}")
    print("\nAvailable voices:")
    voices = get_voices(".", update_voices=True)
    for voice in voices[:10]:  # Show first 10
        print(f"  - {voice['key']}")
