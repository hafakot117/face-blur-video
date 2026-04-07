#!/bin/bash

echo "=== face_blur setup ==="

# Install ffmpeg
echo "Installing ffmpeg..."
if ! command -v ffmpeg &> /dev/null; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install ffmpeg
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        sudo apt-get update
        sudo apt-get install -y ffmpeg
    fi
else
    echo "ffmpeg already installed ✓"
fi

# Create virtual environment
echo "Creating Python virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "Virtual environment created ✓"
else
    echo "Virtual environment already exists ✓"
fi

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install opencv-python numpy pillow

echo ""
echo "✅ Setup complete!"
echo ""
echo "To use the face blur tool:"
echo "  1. Activate the virtual environment: source venv/bin/activate"
echo "  2. Run the GUI: python face_blur_gui.py"
echo ""
