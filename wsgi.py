"""Ponto de entrada WSGI para hospedagem (PythonAnywhere, gunicorn, etc.)."""

from app import create_app

app = application = create_app()
