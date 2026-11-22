FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY catalog ./catalog
COPY telegram_bot.py doctor.py parse_voices.py ./
RUN useradd --create-home --uid 10001 botuser && mkdir /app/data && chown botuser:botuser /app/data
USER botuser
ENV PYTHONUNBUFFERED=1
CMD ["python", "telegram_bot.py"]
