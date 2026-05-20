import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_JSON_PATH = BASE_DIR / "config.json"

class AppConfig:
    def __init__(self):
        # Sensitive credentials from .env
        self.omie_app_key = os.getenv("OMIE_APP_KEY", "")
        self.omie_app_secret = os.getenv("OMIE_APP_SECRET", "")
        self.omie_api_url = os.getenv("OMIE_API_URL", "https://app.omie.com.br/api/v1/produtos/nfconsultar/")
        
        # User configurations (editable via UI/config.json)
        self.polling_interval = 30
        self.auto_print = False
        self.printer_name = ""
        self.log_dir = "logs"
        self.db_path = "omie_automation.db"
        
        # Load local overrides from config.json if they exist
        self.load_from_json()

    def load_from_json(self):
        if CONFIG_JSON_PATH.exists():
            try:
                with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.polling_interval = data.get("polling_interval", self.polling_interval)
                    self.auto_print = data.get("auto_print", self.auto_print)
                    self.printer_name = data.get("printer_name", self.printer_name)
                    self.log_dir = data.get("log_dir", self.log_dir)
                    self.db_path = data.get("db_path", self.db_path)
            except Exception as e:
                # Fallback to defaults if json is corrupt
                print(f"Error loading config.json: {e}")
        else:
            self.save_to_json()

    def save_to_json(self):
        data = {
            "polling_interval": self.polling_interval,
            "auto_print": self.auto_print,
            "printer_name": self.printer_name,
            "log_dir": self.log_dir,
            "db_path": self.db_path
        }
        try:
            with open(CONFIG_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving config.json: {e}")

    def update_settings(self, **kwargs):
        """Helper to programmatically update and save settings from the UI."""
        for key, val in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, val)
        self.save_to_json()

    def validate(self) -> tuple[bool, str]:
        """Validates that key parameters are populated."""
        if not self.omie_app_key:
            return False, "OMIE_APP_KEY is missing from environment/config."
        if not self.omie_app_secret:
            return False, "OMIE_APP_SECRET is missing from environment/config."
        return True, "Configuration is valid."

# Global configuration instance
config = AppConfig()
