import requests
from typing import Dict, Any, Callable, Optional
import logging

logger = logging.getLogger(__name__)

class BackendClient:
    def __init__(self, base_url: str, token_provider: Callable[[], Optional[str]]):
        self.base_url = base_url
        self.token_provider = token_provider

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
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
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
