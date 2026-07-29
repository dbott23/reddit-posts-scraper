FROM apify/actor-python:3.12

ENV PYTHONPATH=/usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install playwright && python -m playwright install-deps firefox
RUN python -m camoufox fetch

COPY . ./

CMD ["python3", "-m", "src.main"]
