# Pipeline service (Cloud Run). Build context: app/backend-pipeline
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements-pipeline.txt .
RUN pip install --no-cache-dir -r requirements-pipeline.txt
COPY common/ common/
COPY speech_transcriber/ speech_transcriber/
COPY gemini_analyzer/ gemini_analyzer/
COPY pipeline_service/ pipeline_service/
# Access logs off: Cloud Run's own request log is enough and holds no content.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 4 --timeout 300 pipeline_service.main:app
