"""Toxiproxy REST control layer."""
import requests
import time


class ToxiproxyController:
    def __init__(self, api_url: str = "http://localhost:8474"):
        self.api = api_url

    # -- toxics --------------------------------------------------------------------

    def add_latency(self, proxy: str, latency_ms: int, jitter_ms: int = 0):
        self._post(f"/proxies/{proxy}/toxics", {
            "name":       f"latency_{proxy}",
            "type":       "latency",
            "stream":     "downstream",
            "attributes": {"latency": latency_ms, "jitter": jitter_ms},
        })

    def add_bandwidth(self, proxy: str, rate_kbps: int):
        """Throttle to rate_kbps kilobytes/sec."""
        self._post(f"/proxies/{proxy}/toxics", {
            "name":       f"bw_{proxy}",
            "type":       "bandwidth",
            "stream":     "downstream",
            "attributes": {"rate": rate_kbps},
        })

    def add_timeout(self, proxy: str, timeout_ms: int):
        """Drop connection after timeout_ms with no response."""
        self._post(f"/proxies/{proxy}/toxics", {
            "name":       f"timeout_{proxy}",
            "type":       "timeout",
            "stream":     "downstream",
            "attributes": {"timeout": timeout_ms},
        })

    def add_slicer(self, proxy: str, avg_size: int = 1, delay_us: int = 0):
        """Break TCP stream into avg_size-byte slices with delay_us delay."""
        self._post(f"/proxies/{proxy}/toxics", {
            "name":       f"slicer_{proxy}",
            "type":       "slicer",
            "stream":     "downstream",
            "attributes": {"average_size": avg_size, "size_variation": 0,
                           "delay": delay_us},
        })

    def disable(self, proxy: str):
        self._post(f"/proxies/{proxy}", {"enabled": False}, method="PATCH")

    def enable(self, proxy: str):
        self._post(f"/proxies/{proxy}", {"enabled": True}, method="PATCH")

    def remove_toxic(self, proxy: str, toxic_name: str):
        requests.delete(f"{self.api}/proxies/{proxy}/toxics/{toxic_name}")

    def reset_all(self):
        for proxy in self._get("/proxies"):
            for toxic in self._get(f"/proxies/{proxy}/toxics"):
                self.remove_toxic(proxy, toxic["name"])
        # re-enable all
        for proxy in self._get("/proxies"):
            self.enable(proxy)

    def health_check(self, timeout: int = 5) -> bool:
        try:
            r = requests.get(f"{self.api}/proxies", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False

    # -- internals -----------------------------------------------------------------

    def _post(self, path: str, body: dict, method: str = "POST"):
        fn = getattr(requests, method.lower())
        fn(f"{self.api}{path}", json=body, timeout=5)

    def _get(self, path: str) -> dict:
        return requests.get(f"{self.api}{path}", timeout=5).json()
