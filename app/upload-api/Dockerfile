FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py safelog.py media.py school.py upload_page.html ./
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 60 main:app
