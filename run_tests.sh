#!/bin/bash
# CodeAssist Test Runner

set -e

cd "$(dirname "$0")"

echo "=========================================="
echo "CodeAssist Test Suite"
echo "=========================================="
echo ""

# Check if virtual environment exists
if [ ! -d ".venv" ]; then
    echo "Error: Virtual environment not found. Run:"
    echo "  python -m venv .venv"
    echo "  source .venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
fi

# Activate virtual environment
source .venv/bin/activate

# Install test dependencies if needed
if ! python -c "import pytest" 2>/dev/null; then
    echo "Installing test dependencies..."
    pip install -q pytest pytest-asyncio pytest-cov httpx
fi

# Parse arguments
CASE="${1:-all}"

echo "Running tests: $CASE"
echo ""

case "$CASE" in
    "all")
        echo "Running all tests..."
        pytest tests/ -v --cov=. --cov-report=term-missing
        ;;
    "unit")
        echo "Running unit tests..."
        pytest tests/test_config.py tests/test_session.py tests/test_skills.py tests/test_agents.py tests/test_session_manager.py -v
        ;;
    "tools")
        echo "Running tool tests..."
        pytest tests/test_tools/ -v
        ;;
    "git")
        echo "Running git tests..."
        pytest tests/test_tools/test_git.py -v
        ;;
    "skills")
        echo "Running skills tests..."
        pytest tests/test_skills.py -v
        ;;
    "agents")
        echo "Running agent tests..."
        pytest tests/test_agents.py -v
        ;;
    "coverage")
        echo "Running with coverage report..."
        pytest tests/ -v --cov=. --cov-report=html --cov-report=term-missing
        echo ""
        echo "Coverage report: htmlcov/index.html"
        ;;
    *)
        echo "Unknown test case: $CASE"
        echo ""
        echo "Available test cases:"
        echo "  all      - Run all tests (default)"
        echo "  unit     - Run unit tests only"
        echo "  tools    - Run tool tests"
        echo "  git      - Run git tests"
        echo "  skills   - Run skills tests"
        echo "  agents   - Run agent tests"
        echo "  coverage - Run with coverage report"
        exit 1
        ;;
esac

echo ""
echo "=========================================="
echo "Tests complete!"
echo "=========================================="
