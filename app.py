"""Entry point for gunicorn (Render start command: gunicorn app:app)."""
from sfm import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)
