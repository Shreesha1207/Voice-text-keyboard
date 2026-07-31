import requests
from typing import Dict, Any, Callable, Optional
import logging

logger = logging.getLogger(__name__)


def _user_agent() -> str:
    """Identify the desktop app in server logs.

    Without this every call arrives as `python-requests/2.x`, which is
    indistinguishable from any other script, and Railway's access log shows only
    a proxy address — so there was no way to tell whether traffic came from the
    desktop app or the web dashboard.
    """
    try:
        from main import __version__ as v
    except Exception:
        v = "unknown"
    return f"Xvoice-Desktop/{v}"


USER_AGENT = _user_agent()


class BackendClient:
    def __init__(self, base_url: str, token_provider: Callable[[], Optional[str]]):
        self.base_url = base_url
        self.token_provider = token_provider

    def _headers(self, token: str, json_body: bool = False) -> Dict[str, str]:
        h = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def transform_text(self, action: str, text: str, target_language: str = "English") -> Dict[str, Any]:
        """
        Sends a request to the backend to transform the text.
        """
        token = self.token_provider()
        if not token:
            return {"success": False, "error": "No authentication token found. Please log in."}
            
        url = f"{self.base_url}/text/transform"
        
        payload = {
            "action": action,
            "text": text,
            "target_language": target_language
        }
        
        headers = self._headers(token, json_body=True)
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=15)
            
            if response.status_code == 401:
                return {"success": False, "error": "Authentication expired. Please log in again."}
            elif response.status_code == 403:
                return {"success": False, "error": "Trial expired. Please upgrade on the dashboard."}
                
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timed out. Please try again."}
        except requests.exceptions.RequestException as e:
            logger.error(f"Backend transform request failed: {e}")
            return {"success": False, "error": "Could not connect to server."}
        except Exception as e:
            logger.error(f"Unexpected error in backend transform: {e}")
            return {"success": False, "error": "An unexpected error occurred."}

    def get_preferences(self) -> Dict[str, Any]:
        """Fetch the user's writing preferences from the backend."""
        token = self.token_provider()
        if not token:
            return {}
        try:
            r = requests.get(
                f"{self.base_url}/writing/preferences",
                headers=self._headers(token),
                timeout=5,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logger.error(f"Failed to fetch writing preferences: {e}")
        return {}

    def record_writing_action(self, action: str, char_count: int = 0) -> Dict[str, Any]:
        """Record a writing action execution for usage metering."""
        token = self.token_provider()
        if not token:
            return {}
        try:
            r = requests.post(
                f"{self.base_url}/writing/record",
                headers=self._headers(token),
                json={"action_key": action, "char_count": char_count},
                timeout=5,
            )
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            logger.error(f"Failed to record writing action: {e}")
        return {}

