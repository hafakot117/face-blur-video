#!/bin/bash
# One-shot setup for face_blur_video.py on macOS (Intel + Apple Silicon)
set -e

echo "=== face_blur setup ==="

# Install Homebrew if missing
if ! command -v brew &>/dev/null; then
  echo "Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# Add Homebrew to PATH (handles both Apple Silicon /opt/homebrew and Intel /usr/local)
eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"

echo "Installing ffmpeg..."
brew install ffmpeg

echo "Installing Python dependencies..."
pip3 install -r requirements.txt

echo ""
echo "✓ Setup complete! Usage:"
echo "  python3 face_blur_video.py /path/to/video.mp4"
