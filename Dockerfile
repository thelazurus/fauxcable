FROM python:3.12-slim

ARG GIT_COMMIT=unknown
ENV GIT_COMMIT=$GIT_COMMIT

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fauxcable/ ./fauxcable/

EXPOSE 8000

CMD ["uvicorn", "fauxcable.main:app", "--host", "0.0.0.0", "--port", "8000"]
