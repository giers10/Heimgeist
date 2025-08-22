#!/bin/sh
python -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
npm install
npm run dev