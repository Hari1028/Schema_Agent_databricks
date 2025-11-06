import os
import autogen
import json
import logging
import pandas as pd
from typing import Annotated, Dict, Any, Optional

# --- 1. IMPORT LOCAL MODULES & TOOLS ---
# Friend's imports
from file_info import get_file_metadata 
from data_connector import read_data_file 
from DataProfilerAgent_end_to_end import DataProfilerAgent

# Your imports
import tools
import validation_module
import databricks_tool
import build_md

# --- 2. CONFIGURE LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- 3. LOAD CONFIG ---
from dotenv import load_dotenv
load_dotenv()
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
    "cache_seed": 42,
    "timeout": 300,
}

# --- 3.5. INITIALIZE DB CONNECTION (NEW) ---
# Connect to Databricks ONCE at the start for efficiency
logging.info("Initializing Databricks connection...")
DB_ENGINE, ALL_TABLE_SCHEMAS = databricks_tool.get_db_objects()

if DB_ENGINE is None:
    logging.error("Failed to initialize Databricks engine. Schema validation tools will fail.")
else:
    logging.info(f"Databricks connection successful. Loaded {len(ALL_TABLE_SCHEMAS)} table schemas.")


# --- 4. TOOL DEFINITIONS ---

# --- Friend's Profiler Tools ---
def check_file_support(
    file_path: Annotated[str, "The file path to the CSV, Excel, Parquet, or JSON file"]
) -> Annotated[str, "A JSON string with 'supported': true/false and an optional 'error'"]:
    """Check if the file exists and is a supported file type."""
    logging.info(f"... EXECUTING: check_file_support('{file_path}')...")
    supported_extensions = ['.csv', '.xlsx', '.parquet', '.json']
    result = {}

    if not os.path.exists(file_path):
        # Let's create a dummy file if it doesn't exist, to allow the flow to proceed
        logging.warning(f"File not found: {file_path}. Creating dummy file for demo.")
        try:
            with open(file_path, 'w') as f:
                f.write("col1,col2\nval1,val2")
            result = {"supported": True, "message": "File not found, but dummy file was created."}
        except Exception as e:
            result = {"supported": False, "error": f"File not found: {file_path}. Failed to create dummy file: {e}"}
    else:
        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext not in supported_extensions:
            result = {"supported": False, "error": f"File type {file_ext} is not supported. Supported types: {supported_extensions}"}
        else:
            result = {"supported": True}
            
    return json.dumps(result)

def get_file_information(
    file_path: Annotated[str, "The file path to the data file"]
) -> Annotated[str, "A JSON string with file metadata (size, rows, columns, etc.)"]:
    """Get basic information (metadata, rows, columns) about the file."""
    logging.info(f"... EXECUTING: get_file_information('{file_path}')...")
    try:
        info = get_file_metadata(file_path)
        df = read_data_file(file_path)
        info.update({
            "encoding": "UTF-8", # Mocked
            "language": "Unknown", # Mocked
            "totalRows": len(df),
            "totalColumns": len(df.columns),
        })
        return json.dumps(info)
    except Exception as e:
        logging.error(f"... ERROR in get_file_information: {e}")
        return json.dumps({"error": str(e)})

def run_data_profiling(
    file_path: Annotated[str, "The file path to the data file"],
    sample_size: Annotated[Optional[int], "Number of sample rows to use (default 7)"] = 7
) -> Annotated[str, "A JSON string containing the full data profile report"]:
    """Run data profiling on the file."""
    logging.info(f"... EXECUTING: run_data_profiling('{file_path}')...")
    try:
        profiler_agent = DataProfilerAgent(
            api_version=API_VERSION,
            endpoint=AZURE_ENDPOINT,
            api_key=API_KEY,
            deployment=DEPLOYMENT_NAME
        )
        df = read_data_file(file_path)
        profile = profiler_agent.profile(df, file_path, take_sample_size=7)
        profiler_agent.print_total_token_usage()
        return profile # Assuming it's already a JSON string
    except Exception as e:
        logging.error(f"... ERROR in run_data_profiling: {e}")
        return json.dumps({"error": str(e)})

# --- (NEW) Your Schema Validation Tools ---

def get_sheet_names(
    file_path: Annotated[str, "The file path to the CSV or Excel file"]
) -> Annotated[str, "A JSON string of the list of sheet names."]:
    """Gets all sheet names from a given Excel file. For a CSV, returns ['csv_data']."""
    logging.info(f"... EXECUTING: get_sheet_names('{file_path}')...")
    try:
        sheet_names_list = tools.get_sheet_names(file_path)
        return json.dumps({"sheet_names": sheet_names_list})
    except Exception as e:
        logging.error(f"... ERROR in get_sheet_names: {e}")
        return json.dumps({"error": str(e)})

def get_table_recommendations(
    file_path: Annotated[str, "The file path to the CSV or Excel file"],
    sheet_name: Annotated[str, "The specific sheet name to analyze (e.g., 'csv_data' or 'Sheet1')"]
) -> Annotated[str, "A JSON string with table recommendations for that sheet."]:
    """Compares a sheet's schema to all DB tables and returns the top matches."""
    logging.info(f"... EXECUTING: get_table_recommendations('{file_path}', '{sheet_name}')...")
    if DB_ENGINE is None or ALL_TABLE_SCHEMAS is None:
        return json.dumps({"error": "Databricks connection not initialized."})
    try:
        # Pass the pre-loaded table schemas for efficiency
        recommendations = validation_module.get_recommendations_for_sheet(
            file_path=file_path,
            sheet_name=sheet_name,
            all_db_schemas=ALL_TABLE_SCHEMAS
        )
        return json.dumps(recommendations)
    except Exception as e:
        logging.error(f"... ERROR in get_table_recommendations: {e}")
        return json.dumps({"error": str(e)})

def run_full_schema_validation(
    file_path: Annotated[str, "The file path to the CSV or Excel file"],
    sheet_name: Annotated[str, "The specific sheet name to validate (e.g., 'csv_data' or 'Sheet1')"],
    table_name: Annotated[str, "The *exact* target database table name to validate against"]
) -> Annotated[str, "The FULL JSON string validation report."]:
    """Runs the complete validation pipeline for one sheet against one table."""
    logging.info(f"... EXECUTING: run_full_schema_validation('{file_path}', '{sheet_name}', '{table_name}')...")
    if DB_ENGINE is None:
        return json.dumps({"error": "Databricks connection not initialized."})
    try:
        # Pass the pre-created DB engine for efficiency
        report = validation_module.run_validation_for_single_sheet(
            file_path=file_path,
            sheet_name=sheet_name,
            table_name=table_name,
            engine=DB_ENGINE
        )
        return json.dumps(report)
    except Exception as e:
        logging.error(f"... ERROR in run_full_schema_validation: {e}")
        return json.dumps({"error": str(e)})

# --- (NEW) Your Markdown Conversion Tool ---
def convert_json_to_markdown(
    json_report_string: Annotated[str, "The full JSON validation or profiling report as a string."]
) -> Annotated[str, "A human-readable Markdown report."]:
    """Converts a JSON report string into a formatted Markdown string."""
    logging.info("... EXECUTING: convert_json_to_markdown...")
    try:
        report_data = json.loads(json_report_string)
        # Check if it's a validation report (has 'sheet_reports')
        if 'sheet_reports' in report_data:
            markdown_report = build_md.build_markdown_report(report_data)
        else:
            # Assume it's a profiling report or other JSON
            # (You can create a new build_md function for this)
            logging.warning("This looks like a profiling report. Using basic formatting.")
            markdown_report = f"## JSON Report\n```json\n{json.dumps(report_data, indent=2)}\n```"
        return markdown_report
    except Exception as e:
        logging.error(f"... ERROR in convert_json_to_markdown: {e}")
        return f"Error converting JSON to Markdown: {e}"


# --- 5. AGENT DEFINITIONS ---

# AGENT 1: The User
user_proxy = autogen.UserProxyAgent(
    name="User",
    human_input_mode="TERMINATE",
    max_consecutive_auto_reply=10,
    is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
    system_message="""You are the user and code executor.
    You provide the initial file path and task.
    You answer questions when asked.
    When a tool is called, you execute it and post the result.
    You ONLY stop for input when a message ends with the single word TERMINATE.""",
    code_execution_config={"work_dir": "autogen_work_dir", "use_docker": False},
)

# AGENT 2: The Conductor / Main Assistant
workflow_planner_agent = autogen.AssistantAgent(
    name="WorkflowPlannerAgent",
    llm_config=llm_config,
    system_message="""You are the **WorkflowPlanner**, the primary assistant.
    Your job is to manage the entire workflow, from greeting to final report.

    **YOUR GOAL (The Workflow):**
    1.  **Greet & Validate:** Greet the user. Receive the file path. Call `@InformationValidatorAgent` to check the file.
    2.  **Report Validation:**
        - If validation *fails*, report the error clearly to the user and ask for a new file. End message with `TERMINATE`.
        - If validation *succeeds*, proceed to step 3.
    3.  **Get File Info:** Call `@FileInfoAgent` to get basic file information.
    4.  **Confirm Task:** Present the file info to the user. Check the initial prompt.
        - If the user *already* specified "profile", state what you are doing (e.g., "The file is valid. Now I will proceed with data profiling.") and call `@DataProfilerAgent`.
        - If the user *already* specified "validate", state what you are doing (e.g., "The file is valid. Now I will begin the schema validation process.") and call `@SchemaValidatorAgent`.
        - If the user did *not* specify, you MUST ask them: "The file is valid. Would you like to **profile** the data or **validate** its schema? TERMINATE"
    5.  **Handle Task:**
        - **Profiling:** Call `@DataProfilerAgent`.
        - **Validation:** Call `@SchemaValidatorAgent`. This specialist will handle the multi-step validation. It may return with questions for you to ask the user (like choosing a table). You must act as the intermediary.
    6.  **Present JSON:** When a specialist (`@DataProfilerAgent` or `@SchemaValidatorAgent`) gives you a final JSON report, you MUST call `@ConversationAgent` to get the human-readable version.
    7.  **Present & Conclude:** Present the final HUMAN-READABLE report from `@ConversationAgent` to the user. Ask "Is there anything else I can help you with? TERMINATE".

    **CRITICAL RULES:**
    - **ASK ONE QUESTION AT A TIME.**
    - When you need to ask the user a question, end your *entire* message with the single word `TERMINATE`.
    """
)

# AGENT 3: The Validator (Specialist)
info_validator_agent = autogen.AssistantAgent(
    name="InformationValidatorAgent",
    llm_config=llm_config,
    system_message="""You are a silent specialist. Your only job is to call the `check_file_support` tool.
    Report the JSON result back to the `WorkflowPlannerAgent`."""
)

# AGENT 4: The File Info (Specialist)
file_info_agent = autogen.AssistantAgent(
    name="FileInfoAgent",
    llm_config=llm_config,
    system_message="""You are a silent specialist. Your only job is to call the `get_file_information` tool.
    Report the JSON result back to the `WorkflowPlannerAgent`."""
)

# AGENT 5: The Profiler (Specialist)
data_profiler_agent = autogen.AssistantAgent(
    name="DataProfilerAgent",
    llm_config=llm_config,
    system_message="""You are a silent specialist. Your only job is to call the `run_data_profiling` tool.
    Report the JSON result back to the `WorkflowPlannerAgent`."""
)

# AGENT 6: The Schema Validator (Specialist) (UPDATED)
schema_validator_agent = autogen.AssistantAgent(
    name="SchemaValidatorAgent",
    llm_config=llm_config,
    system_message="""You are the **Schema Validation Specialist**.
    You are responsible for the multi-step schema validation workflow.
    You report back to the `WorkflowPlannerAgent`.

    **YOUR WORKFLOW:**
    1.  You will be given a `file_path` from the WorkflowPlannerAgent.
    2.  First, you MUST call `get_sheet_names` to get the list of sheets.
    3.  **FOR EACH SHEET** in the list (e.g., 'csv_data' or 'Sheet1'), you MUST call `get_table_recommendations`.
    4.  After you have the recommendations for all sheets, you MUST report them back to the `WorkflowPlannerAgent`.
        (e.g., "I have the following recommendations for 'csv_data': [table_a, table_b]. Please ask the user to select one table to validate.")
    5.  The `WorkflowPlannerAgent` will come back to you with the user's choice (a `sheet_name` and a `table_name`).
    6.  You MUST then call `run_full_schema_validation` with the `file_path`, `sheet_name`, and `table_name`.
    7.  Finally, you MUST return the complete, final JSON report to the `WorkflowPlannerAgent`.
    """
)

# AGENT 7: The Report Formatter (Specialist) (UPDATED)
conversation_agent = autogen.AssistantAgent(
    name="ConversationAgent",
    llm_config=llm_config,
    system_message="""You are a silent specialist. Your only job is to convert a JSON report string into a human-readable Markdown report.
    You MUST call the `convert_json_to_markdown` tool.
    Report the human-readable string result back to the `WorkflowPlannerAgent`."""
)   

# --- 6. TOOL REGISTRATION ---
# We register each tool with its specific CALLER and the user_proxy as EXECUTOR.

autogen.register_function(
    check_file_support,
    caller=info_validator_agent,
    executor=user_proxy,
    name="check_file_support",
    description="Check if a file exists and is supported."
)

autogen.register_function(
    get_file_information,
    caller=file_info_agent,
    executor=user_proxy,
    name="get_file_information",
    description="Get basic file info (rows, cols, etc)."
)

autogen.register_function(
    run_data_profiling,
    caller=data_profiler_agent,
    executor=user_proxy,
    name="run_data_profiling",
    description="Run the data profiler tool."
)

# --- (NEW) SCHEMA TOOL REGISTRATIONS ---

autogen.register_function(
    get_sheet_names,
    caller=schema_validator_agent, # Called by the specialist
    executor=user_proxy,           # Executed by the user
    name="get_sheet_names",
    description="Get all sheet names from an Excel/CSV file."
)

autogen.register_function(
    get_table_recommendations,
    caller=schema_validator_agent, # Called by the specialist
    executor=user_proxy,           # Executed by the user
    name="get_table_recommendations",
    description="Get DB table recommendations for a specific sheet."
)

autogen.register_function(
    run_full_schema_validation,
    caller=schema_validator_agent, # Called by the specialist
    executor=user_proxy,           # Executed by the user
    name="run_full_schema_validation",
    description="Run the full schema validation report for a sheet against a table."
)

# --- (NEW) MARKDOWN TOOL REGISTRATION ---

autogen.register_function(
    convert_json_to_markdown,
    caller=conversation_agent,     # Called by the formatter
    executor=user_proxy,           # Executed by the user
    name="convert_json_to_markdown",
    description="Convert a JSON report string into a Markdown report."
)


# --- 7. GROUP CHAT SETUP ---
agents = [
    user_proxy, 
    workflow_planner_agent, 
    info_validator_agent, 
    file_info_agent, 
    data_profiler_agent, 
    schema_validator_agent, 
    conversation_agent
]

group_chat = autogen.GroupChat(
    agents=agents,
    messages=[],
    max_round=50,
    speaker_selection_method="auto", # This is simpler and relies on the good agent prompts
    allow_repeat_speaker=True
)

# The manager will use the "auto" method based on the prompts
manager = autogen.GroupChatManager(
    name="Orchestrator",
    groupchat=group_chat,
    llm_config=llm_config
)

# --- 8. RUN THE CHAT ---
print("="*50)
print("🚀 STARTING CHAT")
print("Type 'exit' or 'terminate' to end the conversation.")
print("="*50)

# The user can now specify "profile" or "validate" in their first message
user_proxy.initiate_chat(
    manager,
    message=input("Enter the file path and task (e.g., 'data/sales_data.csv to profile' or 'data/new_orders.csv to validate'): ") 
)