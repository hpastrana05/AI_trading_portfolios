from .base import make_request

def get_account_summary():
    """
    Provides a breakdown of your account's cash and investment metrics, including available funds, invested capital, and total account value.
    freq: 1 req / 5s
    """
    url_ending = "equity/account/summary"
    method = "GET"
    
    return make_request(method, url_ending)
