import pathlib
from datetime import datetime, timedelta

from investing_algorithm_framework import create_app, PortfolioConfiguration, \
    RESOURCE_DIRECTORY, TimeUnit, Algorithm, CCXTOHLCVMarketDataSource, \
    CCXTTickerMarketDataSource, MarketCredential

# Define market data sources
bitvavo_btc_eur_ohlcv_2h = CCXTOHLCVMarketDataSource(
    identifier="BTC/EUR-ohlcv",
    market="BITVAVO",
    symbol="BTC/EUR",
    timeframe="2h",
    start_date_func=lambda : datetime.utcnow() - timedelta(days=17)
)
bitvavo_btc_eur_ticker = CCXTTickerMarketDataSource(
    identifier="BTC/EUR-ticker",
    market="BITVAVO",
    symbol="BTC/EUR",
)
app = create_app({RESOURCE_DIRECTORY: pathlib.Path(__file__).parent.resolve()})
app.add_market_data_source(bitvavo_btc_eur_ohlcv_2h)
app.add_market_data_source(bitvavo_btc_eur_ticker)
app.add_portfolio_configuration(
    PortfolioConfiguration(
        market="<your_market>",
        trading_symbol="<your_trading_symbol>",
        initial_balance=200
    )
)
app.add_market_credential(
    MarketCredential(
        market="<your_market>",
        api_key="<your_api_key>",
        secret_key="<your_secret_key>"
    )
)


@app.strategy(
    time_unit=TimeUnit.SECOND,
    interval=5,
    market_data_sources=["BTC/EUR-ohlcv", "BTC/EUR-ticker"]
)
def perform_strategy(algorithm: Algorithm, market_data):
    pass


if __name__ == "__main__":
    app.run()
