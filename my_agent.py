import os
import autogen
import json
import logging
from typing import Annotated, Dict, Any, Optional

# --- 1. IMPORT YOUR LOCAL MODULES & TOOLS ---
# These are all the files you and I created
import tools
import validation_module
import databricks_tools
import build_md

# --- 2. CONFIGURE LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 3. LOAD LLM & DATABRICKS CONFIG ---
from dotenv import load_dotenv
load_dotenv()

# LLM Config
API_VERSION = os.getenv("API_VERSION", "2024-02-01")
AZURE_ENDPOINT = os.getenv("AZURE_ENDPOINT")
API_KEY = os.getenv("API_KEY")
DEPLOYMENT_NAME = os.getenv("DEPLOYMENT_NAME", "gpt-4.1-nano")

config_list = [
    {
        "model": DEPLOYMENT_NAME,
        "api_key": API_KEY,
        "base_url": AZURE_ENDPOINT,
        "api_type": "azure",
        "api_version": API_VERSION,
    }
]

llm_config = {
    "config_list": config_list,
    "cache_seed": 42, # Use caching for development
    "timeout": 300,
}

# --- 4. INITIALIZE DATABRICKS CONNECTION (EFFICIENTLY) ---
# We connect ONCE at the start. These variables will be passed to our tools.
logging.info("Initializing Databricks connection and loading all table schemas...")
DB_ENGINE, ALL_TABLE_SCHEMAS = databricks_tools.get_db_objects()

if DB_ENGINE is None:
    logging.error("FATAL: Databricks connection failed. Check your .env file. Exiting.")
    exit(1)

logging.info(f"Databricks connection successful. Loaded {len(ALL_TABLE_SCHEMAS)} table schemas.")


# --- 5. TOOL DEFINITIONS (WRAPPERS) ---
# We create "wrapper" functions to pass our DB_ENGINE and ALL_TABLE_SCHEMAS
# to the tools, as the LLM can't provide them.

def _tool_get_sheet_names(
    file_path: Annotated[str, "The file path to the CSV or Excel file"]
) -> Annotated[str, "A JSON string of the list of sheet names."]:
    """Gets all sheet names from a given Excel file. For a CSV, returns ['csv_data']."""
    logging.info(f"... EXECUTING: get_sheet_names('{file_path}')...")
    try:
        sheet_names_list = tools.get_sheet_names(file_path)
        return json.dumps({"sheet_names": sheet_names_list})
    except Exception as e:
        logging.error(f"... ERROR in get_sheet_names: {e}", exc_info=True)
        return json.dumps({"error": str(e)})

def _tool_get_table_recommendations(
    file_path: Annotated[str, "The file path to the CSV or Excel file"],
    sheet_name: Annotated[str, "The specific sheet name to analyze (e.g., 'csv_data' or 'Sheet1')"]
) -> Annotated[str, "A JSON string with table recommendations for that sheet."]:
    """Compares a sheet's schema to all DB tables and returns the top matches."""
    logging.info(f"... EXECUTING: get_table_recommendations('{file_path}', '{sheet_name}')...")
    try:
        # This is where we inject the pre-loaded schemas
        recommendations = validation_module.get_recommendations_for_sheet(
            file_path=file_path,
            sheet_name=sheet_name,
            all_db_schemas=ALL_TABLE_SCHEMAS 
        )
        return json.dumps(recommendations)
    except Exception as e:
        logging.error(f"... ERROR in get_table_recommendations: {e}", exc_info=True)
        return json.dumps({"error": str(e)})

def _tool_run_full_schema_validation(
    file_path: Annotated[str, "The file path to the CSV or Excel file"],
    sheet_name: Annotated[str, "The specific sheet name to validate (e.g., 'csv_data' or 'Sheet1')"],
    table_name: Annotated[str, "The *exact* target database table name to validate against"]
) -> Annotated[str, "The FULL JSON string validation report."]:
    """Runs the complete validation pipeline for one sheet against one table."""
    logging.info(f"... EXECUTING: run_full_schema_validation('{file_path}', '{sheet_name}', '{table_name}')...")
    try:
        # This is where we inject the pre-loaded engine
        report = validation_module.run_validation_for_single_sheet(
            file_path=file_path,
            sheet_name=sheet_name,
            table_name=table_name,
            engine=DB_ENGINE 
        )
        return json.dumps(report)
    except Exception as e:
        logging.error(f"... ERROR in run_full_schema_validation: {e}", exc_info=True)
        return json.dumps({"error": str(e)})

def _tool_convert_json_to_markdown(
    json_report_string: Annotated[str, "The full JSON validation report as a string."]
) -> Annotated[str, "A human-readable Markdown report."]:
    """Converts a JSON report string into a formatted Markdown string."""
    logging.info("... EXECUTING: convert_json_to_markdown...")
    try:
        report_data = json.loads(json_report_string)
        markdown_report = build_md.build_markdown_report(report_data)
        return markdown_report # Return the raw string
    except Exception as e:
        logging.error(f"... ERROR in convert_json_to_markdown: {e}", exc_info=True)
        return f"Error converting JSON to Markdown: {e}"


# --- 6. AGENT DEFINITIONS ---

# AGENT 1: The User & Executor
user_proxy = autogen.UserProxyAgent(
    name="User",
    human_input_mode="TERMINATE",
    max_consecutive_auto_reply=10,
    is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
    system_message="""You are the user and code executor.
    You will be asked to provide a file path to start.
    You will also be asked to confirm which database table to use.
    When a tool is called, you execute it and post the result.
    Reply 'TERMINATE' to end the conversation.""",
    code_execution_config={"work_dir": "autogen_work_dir", "use_docker": False},
)

# AGENT 2: The Main Orchestrator
OrchestratorAgent = autogen.AssistantAgent(
    name="OrchestratorAgent",
    llm_config=llm_config,
    system_message="""You are the **Orchestrator**, the primary assistant.
    Your job is to manage the entire validation workflow and talk to the user.

    **YOUR GOAL:**
    1.  **Greet & Get File:** Greet the user and ask for the file path to validate.
    2.  **Delegate Validation:** Once you have the file path, you MUST call the `ValidatorAgent` to start the validation process. (e.g., "@ValidatorAgent, please validate this file: 'path/to/file.csv'").
    3.  **Handle Questions:** The `ValidatorAgent` may come back with questions for the user (e.g., "Which table should I use?"). You must relay these questions to the user. Make sure to end your question with `TERMINATE`.
    4.  **Relay Answers:** When the user answers, you MUST relay that answer back to the `ValidatorAgent`. (e.g., "@ValidatorAgent, the user has selected 'customer_orders'").
    5.  **Get Final Report:** The `ValidatorAgent` will eventually give you a final JSON report.
    6.  **Delegate Reporting:** When you receive the final JSON report, you MUST call the `ReporterAgent` to convert it to Markdown. (e.g., "@ReporterAgent, please format this: [JSON_STRING]").
    7.  **Present & Conclude:** The `ReporterAgent` will give you the final Markdown report. Present this to the user. Conclude by asking if they need anything else. End with `TERMINATE`.
    """
)

# AGENT 3: The Validation Specialist
ValidatorAgent = autogen.AssistantAgent(
    name="ValidatorAgent",
    llm_config=llm_config,
    system_message="""You are the **Schema Validation Specialist**.
    You are a silent worker. You do NOT talk to the user directly.
    You report all findings and questions back to the `OrchestratorAgent`.

    **YOUR WORKFLOW:**
    1.  You will be given a `file_path` by the `OrchestratorAgent`.
    2.  First, you MUST call `_tool_get_sheet_names` to get the list of sheets.
    3.  **FOR EACH SHEET** in the list, you MUST call `_tool_get_table_recommendations`.
    4.  **Report Recommendations:** After you have the recommendations, you MUST report them back to the `OrchestratorAgent`.
        (e.g., "To: OrchestratorAgent. I have the following recommendations for 'csv_data': 'customer_orders' (95% confidence), 'sales' (40% confidence). Please ask the user to select one.")
    5.  **Wait for Choice:** The `OrchestratorAgent` will reply with the user's choice (a `sheet_name` and a `table_name`).
    6.  **Run Validation:** You MUST then call `_tool_run_full_schema_validation` with the `file_path`, chosen `sheet_name`, and chosen `table_name`.
    7.  **Return Final Report:** Finally, you MUST return the complete, final JSON report (as a string) to the `OrchestratorAgent`.
    """
)

# AGENT 4: The Reporting Specialist
ReporterAgent = autogen.AssistantAgent(
    name="ReporterAgent",
    llm_config=llm_config,
    system_message="""You are the **Report Formatting Specialist**.
    You are a silent worker. You do NOT talk to the user.
    You will be given a JSON report string by the `OrchestratorAgent`.
    Your ONLY job is to call the `_tool_convert_json_to_markdown` tool with that JSON.
    You MUST return the final, human-readable Markdown string to the `OrchestratorAgent`.
    """
)   

# --- 7. TOOL REGISTRATION ---
# Note: We are registering the wrapper functions we defined
autogen.register_function(
    _tool_get_sheet_names,
    caller=ValidatorAgent,
    executor=user_proxy,
    name="_tool_get_sheet_names",
    description="Get all sheet names from an Excel/CSV file."
)

autogen.register_function(
    _tool_get_table_recommendations,
    caller=ValidatorAgent,
    executor=user_proxy,
    name="_tool_get_table_recommendations",
    description="Get DB table recommendations for a specific sheet."
)

autogen.register_function(
    _tool_run_full_schema_validation,
    caller=ValidatorAgent,
    executor=user_proxy,
    name="_tool_run_full_schema_validation",
    description="Run the full schema validation report for a sheet against a table."
)

autogen.register_function(
    _tool_convert_json_to_markdown,
    caller=ReporterAgent,
    executor=user_proxy,
    name="_tool_convert_json_to_markdown",
    description="Convert a JSON report string into a Markdown report."
)


# --- 8. GROUP CHAT SETUP ---
agents = [
    user_proxy, 
    OrchestratorAgent, 
    ValidatorAgent, 
    ReporterAgent
]

group_chat = autogen.GroupChat(
    agents=agents,
    messages=[],
    max_round=50,
    speaker_selection_method="auto" # Relies on agent prompts to direct flow
)

manager = autogen.GroupChatManager(
    name="ChatManager",
    groupchat=group_chat,
    llm_config=llm_config,
)

# --- 9. RUN THE CHAT ---
print("="*50)
print("🚀 STARTING SCHEMA VALIDATION AGENT")
print("Type 'exit' or 'terminate' to end the conversation.")
print("="*50)

user_proxy.initiate_chat(
    manager,
    # We start with an empty message so the Orchestrator can ask for the file
    message="Hi, I'm ready to validate a file."
)