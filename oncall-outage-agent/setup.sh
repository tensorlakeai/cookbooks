#!/bin/bash

# Setup script for the On-Call Outage Agent

echo "🚀 Setting up On-Call Outage Agent..."
echo ""

# Check Python version
echo "Checking Python version..."
python3 --version || { echo "❌ Python 3 not found. Please install Python 3.9+"; exit 1; }
echo "✅ Python found"
echo ""

# Create virtual environment
echo "Creating virtual environment..."
python3 -m venv venv
echo "✅ Virtual environment created"
echo ""

# Activate virtual environment and install dependencies
echo "Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
echo "✅ Dependencies installed"
echo ""

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file from template..."
    cp .env.example .env
    echo "✅ .env file created"
    echo ""
    echo "⚠️  IMPORTANT: Edit .env and add your API keys:"
    echo "   - GROQ_API_KEY (required)"
    echo "   - EXA_API_KEY (optional)"
    echo "   - BROWSERBASE_API_KEY (optional)"
    echo ""
else
    echo "✅ .env file already exists"
    echo ""
fi

echo "🎉 Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Activate the virtual environment:"
echo "     source venv/bin/activate"
echo ""
echo "  2. Edit .env file and add your API keys"
echo ""
echo "  3. Run a test:"
echo "     python3 -m outage_agent.indexify_app"
echo ""
echo "  4. Or start the webhook server:"
echo "     python3 main.py"
echo ""
