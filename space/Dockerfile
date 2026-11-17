# Hugging Face Space (Docker SDK) for the CML research assistant. deploy_space.py uploads this file with
# serve.py, assistant.py, dashboard.html and data/knowledge.db. Model settings and API keys come from the
# Space's variables and secrets, not from this file.
FROM python:3.12-slim

# Spaces run the container as user 1000
RUN useradd -m -u 1000 user
USER user
WORKDIR /home/user/app
COPY --chown=user . .

# The dashboard on GitHub Pages may call the API; limits protect the API credit
ENV PYTHONUNBUFFERED=1 \
    ALLOWED_ORIGINS=https://cmlplatform.github.io \
    HOURLY_LIMIT=20 \
    DAILY_LIMIT=300

EXPOSE 7860
CMD ["python3", "serve.py", "--host", "0.0.0.0", "--port", "7860"]
