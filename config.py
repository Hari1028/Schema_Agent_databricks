import os
import logging
import httpx
import openai
from openai import AzureOpenAI
from dotenv import load_dotenv

# --- 1. Load .env and Set Up Logging ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 2. Azure Credentials ---
API_VERSION = os.getenv("API_VERSION", "2024-02-01")
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT")
API_KEY = os.getenv("API_KEY")
DEPLOYMENT_NAME = os.getenv("DEPLOYMENT_NAME", "gpt-4.1-nano")

# --- 3. Global Client (Exported) ---
AZURE_CLIENT = None  # This is the variable we will import in other files

try:
    if not all([AZURE_ENDPOINT, API_KEY, DEPLOYMENT_NAME]):
        raise ValueError("AZURE_ENDPOINT, API_KEY, or DEPLOYMENT_NAME is not set in .env file.")
    
    http_client = httpx.Client(verify=False)
    
    # We initialize the global client here, once.
    AZURE_CLIENT = AzureOpenAI(
        api_version=API_VERSION,
        azure_endpoint=AZURE_ENDPOINT,
        api_key=API_KEY,
        http_client=http_client,
    )
    logging.info(f"Successfully initialized AzureOpenAI client for endpoint: {AZURE_ENDPOINT}")
    logging.info(f"Using Deployment: {DEPLOYMENT_NAME}")
    
except Exception as e:
    logging.critical(f"Failed to initialize AzureOpenAI client: {e}. Check your .env file.")
    # We exit here because nothing else can run without the client.
    exit(1)