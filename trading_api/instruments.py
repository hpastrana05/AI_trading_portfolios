"""
Discover what you can trade. 
Lists of tradable instruments and the exchanges they belong to. 
Tickers and Trading Hours
"""
from .base import make_request

def get_exchanges_metadata():
    """
    Retrieves all accessible exchanges and their corresponding working schedules. Refreshed every 10 min
    freq: 1 req / 30s
    """

    url_ending = "equity/metadata/exchanges"
    method = "GET"

    return make_request(method, url_ending)


def get_available_instruments():
    """
    Retrieves all accessible instruments. Data refreshed every 10 min
    freq: 1 req / 50s
    """

    url_ending = "equity/metadata/instruments"
    method = "GET"

    return make_request(method, url_ending)

