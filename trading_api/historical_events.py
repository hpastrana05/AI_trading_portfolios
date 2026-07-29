from .base import make_request

def get_paid_out_dividends(ticker:str, cursor="0", limit=20):
    """
    Retrieves all paid out dividends for a given ticker.
    freq: 6 req / 1min
    """
    """
    query = {
        "cursor": "0",
        "ticker": ticker,
        "limit": "21" max 50
    }
    """
    url_ending = "equity/history/dividends"
    method = "GET"

    query = {
        "cursor": cursor,
        "ticker": ticker,
        "limit": str(limit)
    }

    return make_request(method, url_ending, payload=query)

def get_list_generated_reports():
    """
    Retrieves a list of all requested CSV reports and their current status.
    ASYNCHRONOUS:
    - Call POST /history/reports to request report -> get id
    - periodically call GET /history/reports/{id} to check status
    - Once status is "Finished" the downloadLink will contain URL to CSV file
    freq: 1req / 1min
    """
    url_ending = "equity/history/exports"
    method = "GET"

    return make_request(method, url_ending)

def post_request_csv_report(start_date:str, end_date:str, include_dividends=True, include_interest=True, include_orders=True, include_transactions=True):
    """
    Initiates the generation of CSV report.
    Response will contain reprtId for tracking with GET /history/exports
    freq: 1req / 30s
    """
    """
    payload = {
    "dataIncluded": {
        "includeDividends": True,
        "includeInterest": True,
        "includeOrders": True,
        "includeTransactions": True
    },
    "timeFrom": "2019-08-24T14:15:22Z",
    "timeTo": "2019-08-24T14:15:22Z"
    }
    """
    url_ending = "equity/history/exports"
    method = "POST"

    payload = {
        "dataIncluded": {
            "includeDividends": include_dividends,
            "includeInterest": include_interest,
            "includeOrders": include_orders,
            "includeTransactions": include_transactions
        },
        "timeFrom": start_date,
        "timeTo": end_date
    }

    return make_request(method, url_ending, payload=payload)

def get_historical_orders_data(ticker:str, cursor="0", limit=20):
    """
    get historical orders data
    freq: 6req / 1min
    """
    """
    query = {
        "cursor": "0",
        "ticker": "string",
        "limit": "21" max 50
    }
    """
    url_ending = "equity/history/orders"
    method = "GET"

    query = {
        "cursor": cursor,
        "ticker": ticker,
        "limit": str(limit)
    }

    return make_request(method, url_ending, payload=query)

def get_transactions(time:str, cursor="0", limit=20):
    """
    Fetch superficial information about movements to and from your account
    freq: 6req / 1min
    """
    """
    query = {
        "cursor": "string",
        "time": "2019-08-24T14:15:22Z",
        "limit": "21"
    }
    """

    url_ending = "equity/history/transactions"
    method = "GET"
    query = {
        "cursor": cursor,
        "time": time,
        "limit": str(limit)
    }