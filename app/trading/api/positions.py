from .base import make_request

def get_all_open_positions(ticker: str = None):
    """
    Retrieves all open positions.
    Optional ticker filter uses a query parameter (Trading212 instrument id).
    freq: 1req / 1s
    """
    url_ending = "equity/positions"
    method = "GET"

    params = None
    if ticker:
        params = {"ticker": ticker}

    return make_request(method, url_ending, params=params)
