import time

import requests

from app.core import config


def make_request(method, endpoint, params=None, payload=None, max_retries: int = 3):
    """Call Trading212 using the currently selected demo/live credentials."""
    if not config.API_KEY or not config.API_SECRET:
        raise RuntimeError("Missing Trading212 API credentials")

    url = f"{config.API_LINK}{endpoint.lstrip('/')}"
    last_response = None

    for attempt in range(max_retries):
        response = requests.request(
            method,
            url,
            auth=(config.API_KEY, config.API_SECRET),
            params=params,
            json=payload,
            timeout=60,
        )
        last_response = response

        if response.status_code == 200:
            return response.json()

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    sleep_s = float(retry_after)
                except ValueError:
                    sleep_s = 2 ** attempt
            else:
                sleep_s = 2 ** attempt

            # Keep it small so the UI doesn't hang too long.
            sleep_s = min(sleep_s, 8)
            time.sleep(sleep_s)
            continue

        raise RuntimeError(f"T212 {endpoint}: {response.status_code} - {response.text}")

    raise RuntimeError(
        f"T212 {endpoint}: {last_response.status_code} - {last_response.text}"
    )
