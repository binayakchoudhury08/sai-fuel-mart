"""Vercel entry point: every request is routed here (see vercel.json) and handled by the Flask app."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402,F401  (Vercel looks for a WSGI variable named `app`)
