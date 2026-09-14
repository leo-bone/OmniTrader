FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN pip install --no-cache-dir -e .

# run backtests by default
ENTRYPOINT ["omnitrader"]
CMD ["--strategy", "momentum", "--symbol", "BTCUSDT"]
