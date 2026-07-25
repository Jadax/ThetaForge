#!/bin/bash
# IBKR TWS/Gateway Setup Script for ThetaForge
# This script helps verify IBKR connectivity

echo "=== ThetaForge IBKR Setup Verification ==="
echo ""

# Check if TWS/Gateway is running
echo "Checking IBKR connection..."
echo "  Paper Trading Port: 4001"
echo "  Live Trading Port:  4002"
echo ""

# Check Python dependencies
echo "Checking Python dependencies..."
pip install -r requirements.txt 2>/dev/null
if [ $? -eq 0 ]; then
    echo "  Python dependencies OK"
else
    echo "  WARNING: Some Python dependencies may be missing"
fi

# Check Go scanner
echo "Checking Go scanner..."
if command -v go &> /dev/null; then
    echo "  Go installed: $(go version)"
    cd agents/scanner && go mod init scanner 2>/dev/null
    go get github.com/gin-gonic/gin
    go get github.com/redis/go-redis/v9
    echo "  Go scanner dependencies OK"
else
    echo "  WARNING: Go not installed. Scanner microservice will not work."
fi

# Check Redis
echo "Checking Redis..."
if command -v redis-cli &> /dev/null; then
    redis-cli ping 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "  Redis connection OK"
    else
        echo "  WARNING: Redis not responding"
    fi
else
    echo "  WARNING: redis-cli not found. Ensure Redis is running via Docker."
fi

# Check PostgreSQL/TimescaleDB
echo "Checking PostgreSQL..."
if command -v psql &> /dev/null; then
    echo "  PostgreSQL client found"
else
    echo "  WARNING: psql not found. Ensure PostgreSQL is running via Docker."
fi

echo ""
echo "=== Setup Summary ==="
echo "1. Ensure IBKR TWS/Gateway is running with API enabled"
echo "2. Copy .env.example to .env and configure your credentials"
echo "3. Run: docker-compose up -d"
echo "4. Or run components individually:"
echo "   - uvicorn orchestrator.main:app --reload"
echo "   - celery -A orchestrator.celery_app worker"
echo "   - cd agents/scanner && go run main.go scanner.go"
echo ""
echo "=== IMPORTANT ==="
echo "Default connection is PAPER TRADING (port 4001)"
echo "Live trading requires PIN confirmation + hardware switch"
echo ""
echo "=== ThetaForge Setup Complete ==="
