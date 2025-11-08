#!/bin/bash
# Test runner script for Jararaca framework

set -e

echo "🧪 Running Jararaca Test Suite"
echo "================================"
echo ""

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Run tests with coverage
echo -e "${BLUE}Running tests with coverage...${NC}"
poetry run pytest tests/ -v --cov=src/jararaca --cov-report=term-missing --cov-report=html

echo ""
echo -e "${GREEN}✅ All tests passed!${NC}"
echo ""
echo "📊 Coverage report generated in htmlcov/index.html"
echo ""
echo "To view the HTML coverage report:"
echo "  xdg-open htmlcov/index.html  # Linux"
echo "  open htmlcov/index.html      # macOS"
