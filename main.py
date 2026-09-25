"""ASGI entry point: python -m uvicorn main:app --port 8000."""
import logging
from payments import create_app

logging.basicConfig(level=logging.INFO, format='%(message)s')
app = create_app()
