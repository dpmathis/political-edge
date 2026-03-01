#!/bin/bash
# Streamlit Cloud startup: initialize DB and run initial collection
if [ ! -f "data/political_edge.db" ]; then
    echo "Initializing database..."
    python scripts/setup_db.py
    echo "Running initial collection..."
    python scripts/run_collectors.py
fi
