FROM python:3.10
WORKDIR /app
COPY . .

RUN python -m venv venv && . venv/bin/activate \
    && pip install --upgrade pip \
    && pip install -r requirements.txt

CMD ["venv/bin/python", "bot.py"]

