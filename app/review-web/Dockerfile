# Hosted review screen (Cloud Run): built UI + review API in one container.
# Build context is staged by infra/deploy_review.sh (frontend-web, samples, backend-pipeline).
FROM node:20-slim AS ui
WORKDIR /ctx/frontend-web
COPY frontend-web/package.json frontend-web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY samples/ /ctx/samples/
COPY frontend-web/ ./
RUN npx vite build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY review-web/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend-pipeline/common/ common/
COPY backend-pipeline/gemini_analyzer/ gemini_analyzer/
COPY backend-pipeline/pipeline_service/__init__.py backend-pipeline/pipeline_service/records.py pipeline_service/
COPY backend-pipeline/review_api/ review_api/
# Domains + Classes seed (): read by review_api/school_setup.py until an admin saves a change
COPY samples/school-setup/ samples/school-setup/
# Weekly parent email recipients (): fictional guardians, read only by review_api/parent_email.py
COPY samples/contacts/ samples/contacts/
COPY --from=ui /ctx/frontend-web/dist /srv/ui
RUN useradd --uid 10001 --no-create-home app
USER app
# --auth identity is the default on Cloud Run (K_SERVICE set); stated here for clarity.
CMD ["python", "review_api/server.py", "--auth", "identity", "--static", "/srv/ui"]
