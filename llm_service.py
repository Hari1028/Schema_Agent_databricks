import logging
import time
import tiktoken
import openai
from typing import Optional, Dict, Any # <-- Add Dict and Any
import json  # <-- ADD THIS IMPORT
# Import the global client and deployment name from your new config file
from config import AZURE_CLIENT, DEPLOYMENT_NAME

def get_llm_json_response(system_prompt: str, user_prompt: str, max_retries: int = 3) -> Dict[str, Any]:
    """
    A robust wrapper that calls the LLM and guarantees JSON output
    or returns an error dictionary.
    """
    logging.info("Calling LLM for a JSON response...")
    
    # 1. Get the raw string response from our existing function
    response_string = get_llm_streaming_response(system_prompt, user_prompt, max_retries)
    
    if response_string is None:
        logging.error("LLM call failed, returned None.")
        return {"error": "LLM call failed, returned None."}

    # 2. Clean and parse the JSON
    # 2. Clean and parse the JSON (Improved version)
    try:
        # Clean potential markdown (```json ... ```)
        if response_string.startswith("```json"):
            response_string = response_string.strip("```json\n").strip("```")

        # Find the start of the JSON (can be { or [)
        start_obj = response_string.find('{')
        start_list = response_string.find('[')
        
        # Determine which comes first, or if one is missing
        if start_obj == -1 and start_list == -1:
            raise json.JSONDecodeError("No JSON object or list found.", response_string, 0)
            
        if start_obj == -1:
            start_index = start_list
        elif start_list == -1:
            start_index = start_obj
        else:
            start_index = min(start_obj, start_list) # Find whichever starts first

        # Find the corresponding end bracket
        # This is a simple but effective way for your LLM's clean output
        if response_string[start_index] == '{':
            end_index = response_string.rfind('}')
            expected_char = '}'
        else:
            end_index = response_string.rfind(']')
            expected_char = ']'

        if end_index == -1 or end_index < start_index:
            raise json.JSONDecodeError(f"Could not find matching end bracket '{expected_char}'.", response_string, 0)
        
        # Extract and parse
        json_str = response_string[start_index : end_index + 1]
        parsed_json = json.loads(json_str)
        
        logging.info("Successfully parsed JSON response from LLM.")
        return parsed_json
    
    except json.JSONDecodeError as e:
        logging.error(f"Failed to decode LLM response into JSON. Error: {e}")
        logging.error(f"--- BAD LLM RESPONSE ---:\n{response_string}\n--- END ---")
        return {"error": "LLM returned invalid JSON.", "raw_response": response_string}
    
def count_tokens(system_prompt: str, user_prompt: str, full_response: str):
    """Counts tokens for a given interaction."""
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        
        system_tokens = len(encoding.encode(system_prompt))
        user_tokens = len(encoding.encode(user_prompt))
        input_tokens = system_tokens + user_tokens
        
        output_tokens = len(encoding.encode(full_response))
        total_tokens = input_tokens + output_tokens
        
        print("\n" + "-"*30 + " TOKEN COUNT " + "-"*30)
        print(f"[AI-CALL] Input Tokens:  {input_tokens} (System: {system_tokens}, User: {user_tokens})")
        print(f"[AI-CALL] Output Tokens: {output_tokens}")
        print(f"[AI-CALL] Total Tokens:  {total_tokens}")
        print("-"*73 + "\n")
        
        return input_tokens, output_tokens, total_tokens
        
    except Exception as e:
        print(f"An error occurred during token counting: {e}")
        return 0, 0, 0

def get_llm_streaming_response(system_prompt: str, user_prompt: str, max_retries: int = 3) -> Optional[str]:
    """Sends a prompt to the Azure LLM and returns the full streaming response."""
    
    # We must check if the client was initialized successfully
    if AZURE_CLIENT is None:
        logging.critical("AZURE_CLIENT is not initialized. Cannot make LLM call.")
        return None

    for attempt in range(max_retries):
        try:
            logging.info(f"Sending prompt to LLM (Attempt {attempt + 1}/{max_retries})...")
            
            # Use the imported client and deployment name
            response = AZURE_CLIENT.chat.completions.create(
                stream=True,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.0,
                top_p=1.0,
                frequency_penalty=0.0,
                presence_penalty=0.0,
                model=DEPLOYMENT_NAME, # Use imported variable
            )

            full_response = ""
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    full_response += chunk.choices[0].delta.content
            
            count_tokens(system_prompt, user_prompt, full_response)
            return full_response
        
        except openai.RateLimitError as e:
            sleep_time = 60 * (attempt + 1) # This was missing in your original code
            logging.warning(f"Rate limit hit. Retrying in {sleep_time}s... ({attempt + 1}/{max_retries})")
            time.sleep(sleep_time) # Use the sleep_time variable
            
        except Exception as e:
            logging.error(f"An error occurred during the AI call: {e}", exc_info=True)
            return None 

    logging.error("Max retries exceeded for RateLimitError. Giving up.")
    return None