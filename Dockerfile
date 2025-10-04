FROM mcr.microsoft.com/playwright/python:v1.46.0-jammy
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

ENV PYTHONUNBUFFERED=1
CMD ["python", "main.py"]
