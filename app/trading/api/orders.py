# Orders can only be executed in main account currency
"""
Place, monitor, and cancel equity trade orders. 
This section provides the core functionality for programmatically executing your trading strategies for stocks and ETFs.
"""
from .base import make_request

def get_pending_orders():
    """
    Retrieves a list of all orders that are currently active.
    This is useful for monitoring the status of your open positions and managing your trading strategy.
    freq: 1 req / 5s
    """
    url_ending = "equity/orders"
    method = "GET"

    return make_request(method, url_ending)



def post_place_limit_order(quantity, ticker, limit_price, time_validity):
    """
    Creates new Limit order.
    BUY order: positive quantity
    SELL order: negative quantity
    timeValidity: 0 == "DAY" / 1 == "GOOD_TILL_CANCEL
    freq: 1 req /2s
    """
    """
    payload = {
        "limitPrice": 100.23,
        "quantity": 0.1,
        "ticker": "AAPL_US_EQ", Ticker
        "timeValidity": "DAY" / "GOOD_TILL_CANCEL"
    }
    """

    url_ending = "equity/orders/limit"
    method = "POST"

    payload={
        "limitPrice": limit_price,
        "quantity": quantity,
        "ticker": ticker,
        "timeValidity": "DAY" if time_validity==0 else "GOOD_TILL_CANCEL"
    }

    return make_request(method, url_ending, payload=payload)


def post_place_market_order(quantity, ticker, extended_hours=True):
    """
    Creates new Market order.
    BUY order: positive quantity
    SELL order: negative quantity
    extendedHours: set True to include extended hours
    freq: 50 req / 1min
    """
    """
    payload = {
      "extendedHours": True,
      "quantity": 0.1,
      "ticker": "AAPL_US_EQ"
    }
    """
    url_ending = "equity/orders/market"
    method = "POST"

    payload = {
        "extendedHours": extended_hours,
        "quantity": quantity,
        "ticker": ticker
    }

    return make_request(method, url_ending, payload=payload)


def post_place_stop_order(quantity:float, ticker:str, stop_price:float, time_validity):
    """
    Creates new Stop order.
    BUY stop order: positive quantity
    SELL stop order (STOP LOSS): negative quantity
    freq: 1 req / 2s
    """
    """
    payload = {
      "quantity": 0.1,
      "stopPrice": 100.23,
      "ticker": "AAPL_US_EQ",
      "timeValidity": "DAY"
    }
    """
    url_ending = "equity/orders/stop"
    method = "POST"

    payload = {
        "quantity": quantity,
        "stopPrice": stop_price,
        "ticker": ticker,
        "timeValidity": "DAY" if time_validity==0 else "GOOD_TILL_CANCEL"
    }

    return make_request(method, url_ending, payload=payload)

def post_place_stoplimit_order(quantity:float, ticker:str, stop_price:float, limit_price:float, time_validity:int):
    """
    Creates new Stoplimit order.
    Direction of trade by sign of quantity
    When Last Traded Price reaches stopPrice, Limit order settled
    freq: 1 req / 2s
    """
    """
    payload = {
      "limitPrice": 100.23,
      "quantity": 0.1,
      "stopPrice": 100.23,
      "ticker": "AAPL_US_EQ",
      "timeValidity": "DAY"
    }
    """
    url_ending = "equity/orders/stop_limit"
    method = "POST"

    payload = {
        "limitPrice": limit_price,
        "quantity": quantity,
        "stopPrice": stop_price,
        "ticker": ticker,
        "timeValidity": "DAY" if time_validity == 0 else "GOOD_TILL_CANCEL"
    }

    return make_request(method, url_ending, payload=payload)

def delete_cancel_pending_order(order_id:int):
    """
    Attempts to cancel an active order by id
    freq: 50 req / 1min
    """
    url_ending = "equity/orders/"+ f"{order_id}"
    method = "DELETE"

    return make_request(method, url_ending)

def get_pending_order(order_id:int):
    """
    Retrieves a single pending order using its unique numerical ID.
    This is useful for checking the status of a specific order you have previously placed.
    freq: 1req / 1s
    """
    url_ending = "equity/orders/"+ f"{order_id}"
    method = "GET"

    return make_request(method, url_ending)