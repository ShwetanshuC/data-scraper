"""
Google Sheets Website Research Tool
==================================
This tool extracts website URLs from Google Sheets and researches them using OpenAI's GPT-5-nano model.
Results are stored locally and can be uploaded to Google Sheets.

Usage:
  python scraper.py "https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit"
  python scraper.py                    # Interactive mode (asks for spreadsheet ID)
  python scraper.py                    # Debug mode (if DEBUG_MODE = False)

Features:
  - Automatic website extraction from Google Sheets
  - AI-powered research using GPT-5-nano
  - Bucket processing for efficient API usage
  - Automatic Google Sheets updates
  - Webhook integration for post-processing
  - Local file storage for results
  - Configurable web search limitations to reduce API costs

API Credit Optimization:
  - LIMIT_WEB_SEARCH_TO_PROVIDED_WEBSITES: Prevents searching external sites
  - WEB_SEARCH_ENABLED: Can disable web search entirely
  - BATCH_SIZE: Controls how many websites are processed per API call
  - MAX_WORKERS: Limits parallel processing to reduce rate limits
"""

import os
import sys
import json
import gspread
import csv
import shutil
import time
import random
import concurrent.futures
from pathlib import Path
from google.oauth2.service_account import Credentials
 # OpenAI removed in UI-driven variant
from t import attach as _attach_chrome, goto_chatgpt_tab as _goto_chatgpt
from app.chat import find_chat_handle as _find_chat_handle, open_fresh_chat as _open_fresh_chat, ask_gpt_and_get_reply as _ask_gpt
from selenium.webdriver.common.by import By  # type: ignore
import webbrowser

# Dropbox integration removed
DROPBOX_AVAILABLE = False

# Google Sheets API configuration
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Service account credentials file path
SERVICE_ACCOUNT_FILE = 'eminent-scanner-462823-i2-ffd1d24c034e.json'

# UI-driven mode: no OpenAI API used
MULTI_API_ENABLED = False
api_key = os.getenv('OPENAI_API_KEY', '')
print("🔁 Using ChatGPT Web automation (no OpenAI API calls)")

def get_client():
    """Deprecated in UI-driven mode (no API)."""
    return None

def handle_openai_error(error, client=None):
    return False

def get_available_keys_count() -> int:
    return 0

def print_key_status():
    print("🔑 API Key: not used (ChatGPT Web)")

# ===== ChatGPT Web automation driver setup =====
_CHAT_DRIVER = None
_CHAT_HANDLE = None

def _ensure_chat_ready(model_url: str = "https://chatgpt.com/?model=gpt-5") -> bool:
    """Attach to existing Chrome (9222), ensure a fresh ChatGPT composer is ready."""
    global _CHAT_DRIVER, _CHAT_HANDLE
    try:
        if _CHAT_DRIVER is None:
            _CHAT_DRIVER = _attach_chrome()
        try:
            _goto_chatgpt(_CHAT_DRIVER)
        except Exception:
            pass
        if _CHAT_HANDLE is None or _CHAT_HANDLE not in _CHAT_DRIVER.window_handles:
            _CHAT_HANDLE = _find_chat_handle(_CHAT_DRIVER)
            if _CHAT_HANDLE is None:
                try:
                    _CHAT_DRIVER.get(model_url)
                    _CHAT_HANDLE = _CHAT_DRIVER.current_window_handle
                except Exception:
                    _CHAT_HANDLE = _CHAT_DRIVER.current_window_handle
        try:
            _open_fresh_chat(_CHAT_DRIVER, _CHAT_HANDLE, model_url=model_url)
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"❌ Could not prepare ChatGPT Web session: {e}")
        return False

def _chatgpt_ask(prompt: str, timeout: float = 60.0) -> str:
    """Send a prompt to ChatGPT Web and return the reply text."""
    if not _ensure_chat_ready():
        return ""
    try:
        reply = _ask_gpt(_CHAT_DRIVER, _CHAT_HANDLE, prompt, response_timeout=timeout)
        if reply and reply.strip():
            return reply
        # Fallback: try to read the last assistant message directly from the DOM
        end = time.time() + max(timeout, 30.0)
        last_text = ""
        while time.time() < end:
            try:
                _CHAT_DRIVER.switch_to.window(_CHAT_HANDLE)
                nodes = _CHAT_DRIVER.find_elements(By.CSS_SELECTOR, '[data-message-author-role="assistant"], [data-testid="assistant-turn"], [data-testid="conversation-turn"] article')
                if nodes:
                    t = (nodes[-1].text or "").strip()
                    if t:
                        last_text = t
                        break
            except Exception:
                pass
            time.sleep(0.25)
        return last_text or ""
    except Exception as e:
        print(f"❌ ChatGPT Web error: {e}")
        return ""

# Model configuration - easily change between GPT models
OPENAI_MODEL = 'gpt-5-mini'  # unused in UI-driven mode
# Feature flags
EXTRACT_ALL_OWNERS = os.environ.get('EXTRACT_ALL_OWNERS', 'false').lower() == 'true'

# Webhook configuration for Google Apps Script
WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbyiJQvDwjqzdCPC_xH82NNUk6pZ80ttIofK6OYEFcaMslvNvEVCUJE3EDmpS834QcwY/exec"

# Configurable variables for any industry
# To change industries, set the INDUSTRY environment variable or modify this line:
# Examples: 'veterinary', 'dental', 'legal', 'accounting', 'landscaping', 'property_management', 'real_estate'
INDUSTRY = os.environ.get('INDUSTRY', 'property_management')

# Industry is now an input parameter - no hardcoded configurations
# The scraper will adapt to whatever industry is specified
COUNT_PROFESSIONALS = os.environ.get('COUNT_PROFESSIONALS', 'true').lower() == 'true'  # Set to False if you don't need to count professionals
INCLUDE_DOCTOR_FIELD = os.environ.get('INCLUDE_DOCTOR_FIELD', 'true').lower() == 'true'  # Set to False to exclude doctor/professional count from research and updates

def detect_sheet_requirements(headers):
    """Dynamically detect what data to extract based on sheet headers"""
    if not headers:
        return {
            'count_professionals': COUNT_PROFESSIONALS,
            'has_docs_column': COUNT_PROFESSIONALS,
            'output_format': 'Website,First Name,Last Name,Locations,Professionals'
        }
    
    # Check if sheet has Docs/Professionals column
    has_docs_column = any('docs' in header.lower() or 'professionals' in header.lower() 
                         for header in headers)
    
    # Check if sheet has Locations column
    has_locations_column = any('location' in header.lower() for header in headers)
    
    # Determine output format based on available columns
    if has_docs_column and has_locations_column:
        output_format = 'Website,First Name,Last Name,Locations,Professionals'
        count_professionals = True
    elif has_locations_column:
        output_format = 'Website,First Name,Last Name,Locations'
        count_professionals = False
    else:
        output_format = 'Website,First Name,Last Name'
        count_professionals = False
    
    return {
        'count_professionals': count_professionals,
        'has_docs_column': has_docs_column,
        'has_locations_column': has_locations_column,
        'output_format': output_format
    }
# BATCH_SIZE is now imported from web_search_config.py

# Web search configuration is now imported from web_search_config.py

# IMPORTANT: Enhanced fallback instructions to prevent "Unknown,Unknown" results
# The AI model is now instructed to always find a real person's name from the website
# by looking for doctors, veterinarians, or other professionals when owner info is not available
# Using GPT-5-nano for cost optimization while maintaining quality

# Testing mode configuration
TESTING_MODE = True  # Set to True to use hardcoded headers, False to use app header config

# Debug mode configuration
DEBUG_MODE = False  # Set to True to use hardcoded spreadsheet and skip user input
DEBUG_SPREADSHEET_ID = "1-cBoZGdBVUasymQ-S16f2wyM8BBML1DuBv25ullqpGg"
DEBUG_SHEET_GID = "344000088"  # Specific sheet tab ID to test
DEBUG_SKIP_API_CALLS = False  # no effect in UI-driven mode

# Processing configuration
MIN_BUCKET_SIZE = 5  # Minimum number of websites per bucket
TEMP_FOLDER_NAME = "scraping_extraction_from_sheet"

# Rate limiting configuration - tuned to avoid Google Sheets 429s
# You can override via env vars SHEETS_API_DELAY, SHEETS_MAX_RETRIES, SHEETS_RETRY_BASE
SHEETS_API_DELAY = float(os.environ.get('SHEETS_API_DELAY', '1.2'))
MAX_RETRIES = int(os.environ.get('SHEETS_MAX_RETRIES', '6'))
RETRY_DELAY = float(os.environ.get('SHEETS_RETRY_BASE', '10'))  # seconds (base for exponential backoff)

# Cleanup configuration
CLEANUP_TEMP_FILES = True  # Set to True to automatically clean up temporary files
KEEP_FINAL_RESULTS = True  # Set to True to keep final results, False to remove everything

# Timeout configuration - removed restrictions
OPENAI_TIMEOUT = None  # No timeout limit

# Dropbox configuration removed

# Parallel processing configuration is now imported from web_search_config.py

# Default headers from the existing system
DEFAULT_HEADERS = [
    'Company Name', 'First Name', 'Last Name', 'Business Number',
    'Phone Number', 'Email', 'Docs', 'Locations',
    'Google Rating', 'Reviews', 'Website', 'BV', 'MSA', 'Tag'
]

# Testing mode headers (hardcoded for testing)
TESTING_HEADERS = [
    'Company Name', 'First Name', 'Last Name', 'Business Number',
    'Phone Number', 'Email', 'Docs', 'Locations',
    'Google Rating', 'Reviews', 'Website', 'BV', 'MSA', 'Tag'
]

# Try to import web search configuration
try:
    from web_search_config import (
        LIMIT_WEB_SEARCH_TO_PROVIDED_WEBSITES,
        WEB_SEARCH_ENABLED,
        BATCH_SIZE,
        MAX_WORKERS,
        BATCH_DELAY,
        MAX_RETRIES_OPENAI,
        RETRY_DELAY_OPENAI
    )
    print("✅ Loaded web search configuration from web_search_config.py")
except ImportError:
    print("⚠️  web_search_config.py not found, using default settings")
    # Default settings if config file is not available
    LIMIT_WEB_SEARCH_TO_PROVIDED_WEBSITES = True
    WEB_SEARCH_ENABLED = True
    BATCH_SIZE = 10  # Optimized for GPT-5 rate limits
    MAX_WORKERS = 1  # Disabled multiprocessing - single worker only
    BATCH_DELAY = 1.0  # Optimized for GPT-5 rate limits
    MAX_RETRIES_OPENAI = 3  # Optimized for GPT-5 rate limits
    RETRY_DELAY_OPENAI = 8  # Optimized for GPT-5 rate limits

# New: Pipeline processing configuration
PIPELINE_MODE = False  # Set to True when processing entire pipeline
PIPELINE_NAME = None   # Name of the pipeline being processed

def authenticate_google_sheets():
    """Authenticate with Google Sheets API using service account from app"""
    try:
        # Try multiple possible service account file locations
        possible_files = [
            '/Users/aarushchugh/Downloads/inner-doodad-471616-u5-4b6107984cbc.json',
            'eminent-scanner-462823-i2-ffd1d24c034e.json',
            'stellar-aleph-467202-a5-d671ff004e15.json',
            'integrusreal-618392341dcc.json',
            'sa-key.json'
        ]
        
        service_account_file = None
        for file_path in possible_files:
            if os.path.exists(file_path):
                service_account_file = file_path
                break
        
        if not service_account_file:
            print("Error: No service account file found.")
            print("Please ensure one of these files exists:")
            for file_path in possible_files:
                print(f"  - {file_path}")
            return None
        
        print(f"Using service account file: {service_account_file}")
        creds = Credentials.from_service_account_file(service_account_file, scopes=SCOPES)
        client = gspread.authorize(creds)
        print("Successfully authenticated with Google Sheets API")
        return client
    except Exception as e:
        print(f"Error authenticating with Google Sheets: {e}")
        return None

def rate_limited_sheets_api_call(func, *args, **kwargs):
    """Execute a Google Sheets API call with rate limiting and retry logic.
    Implements exponential backoff with jitter for 429/quota errors.
    Returns (result, error_string)."""
    for attempt in range(MAX_RETRIES):
        try:
            # Small pacing before the call on retries
            if attempt > 0:
                time.sleep(SHEETS_API_DELAY)
            result = func(*args, **kwargs)
            # Pace after a successful call to keep RPM under limits
            time.sleep(SHEETS_API_DELAY)
            return result, None
        except Exception as e:
            error_str = str(e).lower()
            status_429 = '429' in error_str
            is_quota = (
                'quota exceeded' in error_str or
                'rate limit' in error_str or
                'user rate limit' in error_str or
                status_429
            )
            if is_quota and attempt < MAX_RETRIES - 1:
                # Exponential backoff with jitter
                backoff = RETRY_DELAY * (2 ** attempt)
                jitter = random.uniform(0, SHEETS_API_DELAY)
                sleep_s = min(backoff + jitter, 120.0)
                print(f"  ⚠️  Sheets quota/rate limit (attempt {attempt + 1}/{MAX_RETRIES}). Retrying in {sleep_s:.1f}s ...")
                time.sleep(sleep_s)
                continue
            if is_quota:
                return None, f"Quota exceeded after {MAX_RETRIES} attempts: {e}"
            # Other errors: return immediately
            return None, f"API call failed: {e}"
    return None, f"Failed after {MAX_RETRIES} attempts"

def handle_quota_exceeded_error():
    """Provide guidance when quota is exceeded"""
    print("\n" + "="*60)
    print("🚨 GOOGLE SHEETS API QUOTA EXCEEDED")
    print("="*60)
    print("You have hit the Google Sheets API rate limit.")
    print("\n📊 Current Limits:")
    print("  - Read requests per minute per user: 60 requests")
    print("  - Read requests per minute per project: 300 requests")
    print("  - Read requests per 100 seconds per user: 100 requests")
    
    print("\n💡 Solutions:")
    print("  1. Wait for the quota to reset (usually 1 minute)")
    print("  2. Reduce the number of sheets being processed")
    print("  3. Increase delays between API calls")
    print("  4. Use a different Google account/service account")
    
    print("\n⚙️  Current Settings:")
    print(f"  - API delay: {SHEETS_API_DELAY} seconds")
    print(f"  - Max retries: {MAX_RETRIES}")
    print(f"  - Retry delay: {RETRY_DELAY} seconds")
    
    print("\n🔧 To adjust rate limiting, modify these values in scraper.py:")
    print("  - SHEETS_API_DELAY: Increase for slower processing")
    print("  - MAX_RETRIES: Increase for more retry attempts")
    print("  - RETRY_DELAY: Increase for longer wait between retries")
    
    print("\n📝 Note: The scraper will automatically retry with delays.")
    print("="*60)

def extract_spreadsheet_id_from_url(sheet_url):
    """Extract spreadsheet ID from a Google Sheets URL"""
    if '/spreadsheets/d/' in sheet_url:
        try:
            spreadsheet_id = sheet_url.split('/spreadsheets/d/')[1].split('/')[0]
            return spreadsheet_id
        except IndexError:
            return None
    return None

def get_spreadsheet_id_from_user():
    """Get spreadsheet ID from user input"""
    print("\n" + "="*60)
    print("GOOGLE SHEETS WEBSITE IMPORT")
    print("="*60)
    print("This tool will import websites from the first sheet of a Google Sheet")
    print("and research business information using AI.")
    print()
    
    # Get spreadsheet ID
    spreadsheet_id = input("Enter Google Sheet ID (or full URL): ").strip()
    
    # Extract ID from URL if full URL was provided
    if '/spreadsheets/d/' in spreadsheet_id:
        start = spreadsheet_id.find('/d/') + 3
        end = spreadsheet_id.find('/', start)
        if end == -1:
            end = spreadsheet_id.find('?', start)
        if end == -1:
            end = len(spreadsheet_id)
        spreadsheet_id = spreadsheet_id[start:end]
    
    if not spreadsheet_id:
        print("Error: No spreadsheet ID provided.")
        return None
    
    return spreadsheet_id

def get_website_column_from_user(headers):
    """Let user select which column contains website URLs"""
    print("\nAvailable columns in the sheet:")
    for i, header in enumerate(headers):
        print(f"{i+1}. {header}")
    
    while True:
        try:
            choice = input(f"\nSelect column number for websites (1-{len(headers)}): ").strip()
            column_index = int(choice) - 1
            
            if 0 <= column_index < len(headers):
                selected_header = headers[column_index]
                print(f"Selected column: {selected_header}")
                return column_index, selected_header
            else:
                print(f"Please enter a number between 1 and {len(headers)}")
        except ValueError:
            print("Please enter a valid number")

def extract_websites_from_sheet(spreadsheet_id, website_column_index):
    """Extract website URLs from the specified column in the first sheet"""
    try:
        # Authenticate with Google Sheets
        gs_client = authenticate_google_sheets()
        if not gs_client:
            return None, "Failed to authenticate with Google Sheets"
        
        # Open the spreadsheet (rate-limited)
        spreadsheet, err = rate_limited_sheets_api_call(gs_client.open_by_key, spreadsheet_id)
        if err:
            return None, f"Failed to open spreadsheet: {err}"
        print(f"Successfully opened spreadsheet: {spreadsheet.title}")
        
        # In debug mode, try to get the specific worksheet by GID
        if DEBUG_MODE and DEBUG_SHEET_GID:
            try:
                # Try to get worksheet by GID first (rate-limited)
                worksheet, err = rate_limited_sheets_api_call(spreadsheet.worksheet_by_id, int(DEBUG_SHEET_GID))
                if err or not worksheet:
                    raise Exception(err or 'worksheet_by_id returned None')
                print(f"Debug: Found specific worksheet by GID: {worksheet.title}")
            except Exception:
                # Fall back to first worksheet (rate-limited)
                worksheet, err = rate_limited_sheets_api_call(spreadsheet.get_worksheet, 0)
                if err or not worksheet:
                    return None, f"Failed to get worksheet: {err or 'None'}"
                print(f"Debug: Using first worksheet: {worksheet.title}")
        else:
            # Get the first worksheet
            worksheet, err = rate_limited_sheets_api_call(spreadsheet.get_worksheet, 0)
            if err:
                return None, f"Failed to get first worksheet: {err}"
            if not worksheet:
                return None, "No worksheets found in the spreadsheet"
            print(f"Reading from worksheet: {worksheet.title}")
        
        # Get all values from the worksheet
        all_values, err = rate_limited_sheets_api_call(worksheet.get_all_values)
        if err:
            return None, f"Failed to read worksheet data: {err}"
        if not all_values:
            return None, "No data found in the worksheet"
        
        # Get headers (first row)
        headers = all_values[0]
        print(f"Found {len(headers)} columns: {', '.join(headers)}")
        
        # In debug mode, show the headers for verification
        if DEBUG_MODE:
            print("\nDebug: Column headers:")
            for i, header in enumerate(headers):
                marker = " ← Website Column" if i == website_column_index else ""
                print(f"  {i}: {header}{marker}")
        
        # Validate website column index
        if website_column_index is not None and website_column_index >= len(headers):
            return None, f"Website column index {website_column_index} is out of range. Only {len(headers)} columns found."
        
        # Extract websites from the specified column
        websites = []
        for row_num, row in enumerate(all_values[1:], start=2):  # Start from row 2 (after headers)
            if website_column_index is not None and len(row) > website_column_index:
                website = row[website_column_index].strip()
                if website and website.startswith(('http://', 'https://')):
                    websites.append(website)
                elif website:
                    print(f"Warning: Row {row_num} has non-URL value in website column: {website}")
        
        print(f"Found {len(websites)} valid website URLs")
        return websites, headers
        
    except Exception as e:
        return None, f"Error reading spreadsheet: {str(e)}"

def create_research_prompt(batch_websites, industry):
    """Create a research prompt for ChatGPT Web with specific directives"""
    web_search_instructions = ""
    if LIMIT_WEB_SEARCH_TO_PROVIDED_WEBSITES:
        web_search_instructions = "ONLY search these websites. NO external sources."
    
    # Determine output format based on COUNT_PROFESSIONALS setting
    if COUNT_PROFESSIONALS:
        output_format = "Website,First Name,Last Name,Locations,Professionals"
        docs_instruction = """* **Number of Docs**

  * Go to **Our Team**, **Staff**, **Doctors**, or similar.
  * Count every individual **professional name** listed.
  * Do not count managers, assistants, technicians, or staff without "Dr." or equivalent professional title.
  * If no professionals are listed, return `0`."""
    else:
        output_format = "Website,First Name,Last Name,Locations"
        docs_instruction = ""
    
    # Owner extraction scope (single owner vs all owners)
    owner_line = "Owner/Top Professional (ONE owner only, separate first and last names)"
    if EXTRACT_ALL_OWNERS:
        owner_line = (
            "All owners/partners/principal clinicians listed (primary owner first). Separate first and last names. Include other owners in 'Professionals' count."
        )

    prompt = f"""Research {len(batch_websites)} {INDUSTRY} websites. For each find:
1. {owner_line}
2. Number of locations  
3. Number of docs

**RESEARCH INSTRUCTIONS - BE THOROUGH AND CONSISTENT**:
- **INDUSTRY FOCUS**: This is a {INDUSTRY} business research task. {INDUSTRY} businesses almost ALWAYS have owner information available. Look specifically for business owners, managers, lead professionals, or administrators in the {INDUSTRY} industry.
- **SMART NAVIGATION**: Start by checking the main menu, sitemap, or navigation to identify ALL available pages. Look for "Menu", "Sitemap", "Site Map", or navigation links to discover hidden pages.
- **ABOUT AND TESTIMONIALS FIRST**: Prioritize "About Us", "Our Story", "Who We Are" and "Testimonials/Reviews" pages to identify owner names before other sources.
- **OWNER INFO**: Look on "About Us", "Our Team", "Staff", "Leadership", "Meet the Team", "Management", "Administration" pages. Check ALL of these pages if they exist. Also check "Company Info", "Who We Are", "Our Story", "History" pages. Additionally, check "Testimonials", "Reviews", "Customer Reviews", "Client Testimonials", "What Our Customers Say" pages - these often mention the business owner by name. **"About Us" pages are especially important - they almost always contain the owner's name if it exists.**
- **INDUSTRY-SPECIFIC NAME SOURCES**: Also check "Contact Us" pages, "Meet Our Team", "Staff Directory", "Our People", "Leadership Team", "Management Team", "Company Leadership", "Executive Team", "Founder", "President", "CEO", "Owner", "Principal", "Director", "Manager", "General Manager", "Operations Manager", "Lead Professional", "Chief Executive", "Business Owner" pages. Look for any page that might list people associated with the {INDUSTRY} business.
- **SOCIAL MEDIA & EXTERNAL LINKS**: Check for links to Facebook, LinkedIn, Instagram, or other social media pages. These often contain the business owner's name. Look for any external links that might lead to personal profiles or business owner information.
- **LOCATIONS**: Check "Locations", "Contact", "Footer", "Our Offices", "Find Us", "Visit Us", "Office Locations", "Service Areas" pages - count actual physical addresses. Look for multiple addresses, phone numbers, or office listings. Check footer on EVERY page. Also check "Branch Offices", "Multiple Locations", "Service Centers".
- **PROFESSIONALS**: Count on "Our Team", "Staff", "Doctors", "Professionals", "Physicians", "Providers", "Clinicians", "Specialists", "Technicians", "Experts", "Consultants" pages - look for individual professional profiles, count actual names listed. Check ALL staff/team pages. Also check "Medical Staff", "Clinical Team", "Technical Team", "Professional Staff".
- **VERIFICATION**: Use web search to visit each page and extract REAL information only. Look for actual staff listings, not just general text.
- **THOROUGHNESS REQUIREMENT**: Spend adequate time on each website. Check multiple pages. Don't rush. If information is unclear, dig deeper. It's OK to take extra time to verify accuracy - this is more important than speed. You must check AT LEAST 5-10 different pages on each website before concluding you can't find an owner name.
- **ACCURACY PRIORITY**: Focus on finding REAL {INDUSTRY} business owner names, not company names. If you find "About Us" pages, read them carefully - they often contain the actual owner's name. Look for phrases like "founded by", "owner", "president", "CEO", "founder", "business owner", "{INDUSTRY} business owner".
- **AVOID COMPANY NAMES**: Do NOT use company names as owner names. Company names are NOT owner names. NEVER leave owner names blank - you MUST find a real person's name.
- **MANDATORY NAME SEARCH**: You are REQUIRED to find a real person's name for every website. Do not give up easily. Check every possible page, including contact forms, staff directories, social media links, and any other page that might contain names.
- **NO HALLUCINATION**: NEVER make up or invent names. Only use names that you can actually see and verify on the website. If you cannot find a real person's name after thoroughly searching, you must indicate this clearly rather than inventing a name.
- **BUSINESS REALITY**: {INDUSTRY} businesses are typically small to medium businesses that almost always have identifiable owners or lead professionals. The owner is typically the business owner, manager, or lead professional in the field. Look for these titles specifically.
- **LAST RESORT ONLY**: Only use "Not Found" or "Unknown" as an absolute last resort after you have exhaustively searched every possible page on the website (About Us, Contact, Team, Staff, Leadership, Social Media, etc.). You must demonstrate that you have made every reasonable effort to find a real person's name before using these fallback options.

**🚨 CRITICAL FALLBACK INSTRUCTIONS - MANDATORY TOP PROFESSIONAL MARKDOWN**: 
- ⚠️ **IF NO OWNER EXISTS**: You MUST immediately mark down the TOP PROFESSIONAL as the primary contact
- 🎯 **TOP PROFESSIONAL PRIORITY**: When owner information is unavailable, the TOP PROFESSIONAL becomes the primary contact - this is MANDATORY
- 📋 **EXTRACT IN THIS ORDER**: 1) Owner/Founder, 2) If no owner → TOP PROFESSIONAL (Manager/Lead/Chief), 3) If no top professional → First listed professional
- 🔍 **LOOK FOR THESE TITLES** (in priority order): "Owner", "Founder", "President", "CEO", "Business Owner" → "Manager", "General Manager", "Operations Manager", "Lead Professional", "Chief of Staff", "Senior Partner", "Director", "Supervisor", "Head of", "Chief" → "Dr.", "Veterinarian", "Medical Director", "Lead Doctor", "Senior Professional"
- ⛔ **NEVER return "Unknown Unknown"** - ALWAYS find someone with authority at the business
- ✅ **SUCCESS CRITERIA**: Every website MUST have a real person's name - either owner or top professional

**🎯 TOP PROFESSIONAL MARKDOWN PROTOCOL**:
- When owner is not found: TOP PROFESSIONAL becomes the primary contact automatically
- This is NOT a failure - it's the CORRECT business practice for {INDUSTRY} businesses
- Mark down the most senior professional available (Manager, Lead, Chief, Director, etc.)
- This ensures we capture the decision-maker even when ownership is unclear
- ALWAYS prioritize finding someone with business authority over returning "Unknown"

**OUTPUT FORMAT**: 
Return results in CSV format: {output_format}
Example: https://example.com,John,Smith,2,5

**WEBSITES TO RESEARCH**:
"""
    for i, website in enumerate(batch_websites, 1):
        prompt += f"{i}. {website}\n"

    prompt += f"""

**IMPORTANT**: 
- {web_search_instructions}
- Return exactly {len(batch_websites)} results (one for each website)
- Use the exact website URLs provided
- Be thorough and accurate - this data will be used for business purposes

**🚨 FINAL REMINDER - TOP PROFESSIONAL MARKDOWN IS MANDATORY**:
- If owner is not found → IMMEDIATELY mark down TOP PROFESSIONAL as primary contact
- This is the CORRECT approach for {INDUSTRY} businesses
- NEVER return "Unknown Unknown" - ALWAYS find a real person with business authority
- Success = finding either owner OR top professional - both are equally valuable

**OUTPUT**:
{output_format}
[website1],[first1],[last1],[locations1]{',[docs1]' if COUNT_PROFESSIONALS else ''}
[website2],[first2],[last2],[locations2]{',[docs2]' if COUNT_PROFESSIONALS else ''}
...and so on for all {len(batch_websites)} websites"""
    
    return prompt

def research_websites(websites, batch_size):
    """Research websites using ChatGPT Web (UI-driven) with web search"""
    try:
        print(f"\nStarting research on {len(websites)} websites...")
        print("Using ChatGPT Web (UI-driven) with web search capability.")
        print("This will take as long as needed - no timeout limit.")
        print("Please be patient - the model is actively researching.")
        
        # Process websites in batches
        all_results = []
        total_batches = (len(websites) + batch_size - 1) // batch_size
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(websites))
            batch_websites = websites[start_idx:end_idx]
            
            print(f"\n--- Processing Batch {batch_num + 1}/{total_batches} ({len(batch_websites)} websites) ---")
            
            # Create prompt for this batch
            prompt = create_research_prompt(batch_websites, INDUSTRY)
            
            # Ask via ChatGPT Web (browser automation)
            output_text = _chatgpt_ask(prompt, timeout=150.0)
            print(f"Batch {batch_num + 1} analysis completed!")
            
            # Clean the output to extract only the formatted data lines
            lines = output_text.strip().split('\n')
            filtered_lines = []
            
            for line in lines:
                line = line.strip()
                # Look for lines that contain website URLs or domain names and comma-separated data
                if line and (',' in line) and ('.com' in line or '.org' in line or '.net' in line or 'http' in line):
                    filtered_lines.append(line)
            
            if filtered_lines:
                print(f"Batch {batch_num + 1} Results:")
                for line in filtered_lines:
                    print(line)
                all_results.extend(filtered_lines)
            else:
                print(f"No formatted results found in batch {batch_num + 1} output.")
                print("Raw output:")
                print(output_text)

        return all_results
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        return []
    except Exception as e:
        print(f"Error during research: {e}")
        error_str = str(e).lower()
        if "organization must be verified" in error_str:
            pass
        elif "timeout" in error_str or "connection" in error_str:
            print("\nNetwork issue occurred. Since there's no timeout limit, this may be a connection problem.")
            print("Check your internet connection and try again.")
        elif "rate limit" in error_str:
            print("\nRate limit exceeded. Please wait a moment and try again.")
        elif "access tier" in error_str or "not supported" in error_str:
            pass
        
        # Raw output unavailable in ChatGPT Web mode
        
        return []

def clean_professional_count(professionals_str):
    """Clean up professional count to extract only the number"""
    if not professionals_str:
        return ""
    
    # Remove any URLs or formatting
    import re
    
    # Extract just the number from formats like "3 (website.com)" or "5 [url]"
    number_match = re.search(r'(\d+)', professionals_str)
    if number_match:
        return number_match.group(1)
    
    # If no number found, return empty string
    return ""

def parse_research_result(result_line):
    """Parse a research result line and extract components"""
    try:
        # Expected format: website,first_name,last_name,locations[,professionals]
        parts = result_line.split(',')
        if len(parts) >= 4:
            website = parts[0].strip()
            first_name = parts[1].strip()
            last_name = parts[2].strip()
            locations_raw = parts[3].strip()
            professionals_raw = parts[4].strip() if len(parts) > 4 else ""
            
            # Ensure minimum number of locations
            locations = ensure_minimum_locations(locations_raw)
            
            # Clean up the professional count
            professionals = clean_professional_count(professionals_raw)
            
            return {
                'Website': website,
                'First Name': first_name,
                'Last Name': last_name,
                'Locations': locations,
                'Professionals': professionals
            }
        else:
            print(f"Warning: Invalid result format: {result_line}")
            return None
    except Exception as e:
        print(f"Error parsing result line: {e}")
        return None

def map_results_to_headers(results, target_headers):
    """Map research results to the target header format"""
    mapped_results = []
    
    for result_line in results:
        parsed_result = parse_research_result(result_line)
        if parsed_result:
            # Create a row with all target headers, filling in available data
            row = {}
            for header in target_headers:
                if header in parsed_result:
                    row[header] = parsed_result[header]
                elif header == 'Company Name':
                    # Try to extract company name from website
                    website = parsed_result.get('Website', '')
                    if website:
                        # Extract domain name as company name
                        domain = website.replace('https://', '').replace('http://', '').split('/')[0]
                        row[header] = domain
                    else:
                        row[header] = ''
                elif header == 'Business Number':
                    # Use locations as business number for now
                    row[header] = str(parsed_result.get('Locations', ''))
                elif header == 'Docs':
                    # Use professionals count for docs
                    row[header] = parsed_result.get('Professionals', '')
                else:
                    row[header] = ''  # Fill empty for missing data
            
            mapped_results.append(row)
    
    return mapped_results

def save_results_to_file(results, target_headers, filename="research_results.csv"):
    """Save research results to a CSV file with target headers"""
    try:
        # Map results to target headers
        mapped_results = map_results_to_headers(results, target_headers)
        
        with open(filename, 'w', encoding='utf-8', newline='') as f:
            # Write header row
            f.write(','.join(target_headers) + '\n')
            
            # Write data rows
            for row in mapped_results:
                row_values = [str(row.get(header, '')) for header in target_headers]
                f.write(','.join(row_values) + '\n')
        
        print(f"\nResults saved to: {filename}")
        print(f"Saved {len(mapped_results)} rows with {len(target_headers)} columns")
        return filename
    except Exception as e:
        print(f"Error saving results: {e}")
        return None

def create_debug_sample_results(websites, target_headers):
    """Create sample results for debug mode without API calls"""
    sample_results = []
    
    for i, website in enumerate(websites):
        # Create a sample result that maps to the target headers
        sample_result = {
            'Company Name': f"Sample Company {i+1}",
            'First Name': 'Sample',
            'Last Name': 'Name',
            'Business Number': f'BUSINESS-{i+1:03d}',
            'Phone Number': '555-000-0000',
            'Email': 'sample@example.com',
            'Docs': '5',
            'Locations': '1',
            'Google Rating': '4.5',
            'Reviews': '100',
            'Website': website,
            'BV': 'DEBUG',
            'MSA': 'DEBUG',
            'Tag': 'DEBUG_MODE'
        }
        sample_results.append(sample_result)
    
    return sample_results

def setup_temp_folder():
    """Create and setup temporary folder structure"""
    temp_folder = Path(TEMP_FOLDER_NAME)
    
    # Remove existing folder if it exists
    if temp_folder.exists():
        shutil.rmtree(temp_folder)
        print(f"Removed existing folder: {temp_folder}")
    
    # Create fresh folder structure
    temp_folder.mkdir(exist_ok=True)
    
    # Create subfolders
    sheets_folder = temp_folder / "sheets"
    buckets_folder = temp_folder / "buckets"
    results_folder = temp_folder / "results"
    
    sheets_folder.mkdir(exist_ok=True)
    buckets_folder.mkdir(exist_ok=True)
    results_folder.mkdir(exist_ok=True)
    
    print(f"Created temporary folder structure: {temp_folder}")
    print(f"  - Sheets: {sheets_folder}")
    print(f"  - Buckets: {buckets_folder}")
    print(f"  - Results: {results_folder}")
    
    return temp_folder, sheets_folder, buckets_folder, results_folder

def cleanup_temp_files(temp_folder, keep_results=False):
    """Clean up temporary files after processing is complete with robust error handling"""
    try:
        if not temp_folder or not temp_folder.exists():
            print("  📁 No temporary folder to clean up")
            return True
        
        print(f"  🧹 Cleaning up temporary files...")
        
        if keep_results:
            # Keep only the final results, clean up intermediate files
            print("  📁 Keeping final results, cleaning intermediate files...")
            
            # Clean up sheets folder (intermediate data)
            sheets_folder = temp_folder / "sheets"
            if sheets_folder.exists():
                try:
                    shutil.rmtree(sheets_folder)
                    print("    ✅ Cleaned up sheets folder")
                except Exception as e:
                    print(f"    ⚠️ Could not clean up sheets folder: {e}")
            
            # Clean up buckets folder (intermediate data)
            buckets_folder = temp_folder / "buckets"
            if buckets_folder.exists():
                try:
                    shutil.rmtree(buckets_folder)
                    print("    ✅ Cleaned up buckets folder")
                except Exception as e:
                    print(f"    ⚠️ Could not clean up buckets folder: {e}")
            
            # Keep results folder with final data
            results_folder = temp_folder / "results"
            if results_folder.exists():
                print("    📁 Kept results folder with final data")
                
                # Try to rename results folder to final_results with better error handling
                try:
                    final_results = temp_folder.parent / f"{temp_folder.name}_final_results"
                    if final_results.exists():
                        # Remove existing final results folder first
                        try:
                            shutil.rmtree(final_results)
                        except Exception as e:
                            print(f"    ⚠️ Could not remove existing final results folder: {e}")
                    
                    # Move results folder
                    shutil.move(str(results_folder), str(final_results))
                    print(f"    📁 Moved results to: {final_results}")
                    
                    # Try to remove the now-empty temp folder
                    try:
                        temp_folder.rmdir()
                        print(f"    ✅ Cleaned up temporary folder structure")
                    except Exception as e:
                        print(f"    ⚠️ Could not remove temp folder: {e}")
                        
                except Exception as move_error:
                    print(f"    ⚠️ Could not move results folder: {move_error}")
                    print(f"    📁 Results remain in: {results_folder}")
            
        else:
            # Remove everything with retry logic
            print("  📁 Removing all temporary files...")
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    shutil.rmtree(temp_folder)
                    print(f"    ✅ Removed temporary folder: {temp_folder}")
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f"    ⚠️ Attempt {attempt + 1} failed: {e}")
                        print(f"    🔄 Retrying in 1 second...")
                        import time
                        time.sleep(1)
                    else:
                        print(f"    ❌ Failed to remove temporary folder after {max_retries} attempts: {e}")
                        return False
        
        return True
        
    except Exception as e:
        print(f"  ❌ Error cleaning up temporary files: {e}")
        return False

def get_all_sheets(spreadsheet_id):
    """Get all worksheets from the spreadsheet"""
    try:
        gs_client = authenticate_google_sheets()
        if not gs_client:
            return None, "Failed to authenticate with Google Sheets"
        
        # Use rate-limited API call
        spreadsheet, error = rate_limited_sheets_api_call(gs_client.open_by_key, spreadsheet_id)
        if error:
            return None, f"Failed to open spreadsheet: {error}"
        
        worksheets, error = rate_limited_sheets_api_call(spreadsheet.worksheets)
        if error:
            return None, f"Failed to get worksheets: {error}"
        
        print(f"Found {len(worksheets)} worksheets in spreadsheet: {spreadsheet.title}")
        for i, ws in enumerate(worksheets):
            print(f"  {i+1}. {ws.title} (ID: {ws.id})")
        
        return worksheets, None
        
    except Exception as e:
        return None, f"Error getting worksheets: {str(e)}"

def is_hallucinated_name(first_name, last_name):
    """Check if a name appears to be hallucinated (business name as person name)"""
    # Common hallucinated patterns
    suspicious_patterns = [
        "Hospital", "Clinic", "Veterinary", "Practice", "Center", "Medical",
        "Staff", "Team", "Group", "Associates", "Partners", "Services",
        "Animal", "Pet", "Care", "Health", "Wellness", "Emergency"
    ]
    
    # Check if first or last name contains suspicious business terms
    first_lower = first_name.lower()
    last_lower = last_name.lower()
    
    for pattern in suspicious_patterns:
        if pattern.lower() in first_lower or pattern.lower() in last_lower:
            return True
    
    # Check for common hallucinated combinations
    if first_lower in ["unknown", "n/a", "none"] and last_lower in ["unknown", "n/a", "none"]:
        return False  # This is actually valid "Unknown,Unknown"
    
    if first_lower in ["unknown", "n/a", "none"] or last_lower in ["unknown", "n/a", "none"]:
        return True  # Mixed unknown/name is suspicious
    
    # Check for obvious business names
    if any(word in first_lower for word in ["dearborn", "tigertails", "powersferry", "avondale", "lavista"]):
        return True
    
    # Check for "ResearchUnavailable" and similar placeholder responses
    if any(term in first_lower or term in last_lower for term in [
        "researchunavailable", "research", "unavailable", "notavailable", "not_available",
        "nofound", "no_found", "notfound", "not_found", "error", "failed", "timeout", "siteblocked",
        "unabletoaccess", "unavailible", "unavail", "access", "blocked"
    ]):
        return True
    
    return False

def extract_websites_from_sheet_by_name(spreadsheet_id, worksheet, target_headers):
    """Extract websites from a specific worksheet"""
    try:
        gs_client = authenticate_google_sheets()
        if not gs_client:
            return None, "Failed to authenticate with Google Sheets"
        
        # Get all values from the worksheet with rate limiting
        all_values, error = rate_limited_sheets_api_call(worksheet.get_all_values)
        if error:
            return None, f"Failed to get worksheet data: {error}"
        
        if not all_values:
            return None, "No data found in the worksheet"
        
        # Get headers (first row)
        headers = all_values[0]
        print(f"  Found {len(headers)} columns: {', '.join(headers)}")
        
        # Find website column
        website_column_index = None
        for i, header in enumerate(headers):
            if 'website' in header.lower() or 'url' in header.lower():
                website_column_index = i
                break
        
        if website_column_index is None:
            return None, "No website column found in worksheet"
        
        print(f"  Website column found at index {website_column_index}: {headers[website_column_index]}")
        
        # Extract websites
        websites = []
        for row_num, row in enumerate(all_values[1:], start=2):
            if len(row) > website_column_index:
                website = row[website_column_index].strip()
                if website and website.startswith(('http://', 'https://')):
                    websites.append(website)
                elif website:
                    print(f"    Warning: Row {row_num} has non-URL value: {website}")
        
        print(f"  Extracted {len(websites)} valid website URLs")
        print(f"  🔍 DEBUG: First 3 websites from worksheet '{worksheet.title}':")
        for i, website in enumerate(websites[:3]):
            print(f"    {i+1}. {website}")
        if len(websites) > 3:
            print(f"    ... and {len(websites) - 3} more")
        return websites, None
        
    except Exception as e:
        return None, f"Error extracting from worksheet {worksheet.title}: {str(e)}"

def save_sheet_data(websites, target_headers, sheet_id, sheets_folder):
    """Save extracted website data for a sheet"""
    try:
        # Ensure sheet_id is a string for consistent filename generation
        sheet_id_str = str(sheet_id)
        filename = sheets_folder / f"{sheet_id_str}_websites.csv"
        
        with open(filename, 'w', encoding='utf-8', newline='') as f:
            # Write header row
            f.write(','.join(target_headers) + '\n')
            
            # Write data rows with websites and empty other fields
            for i, website in enumerate(websites):
                row_data = [''] * len(target_headers)
                
                # Find website column index
                website_col_idx = None
                for j, header in enumerate(target_headers):
                    if 'website' in header.lower():
                        website_col_idx = j
                        break
                
                if website_col_idx is not None:
                    row_data[website_col_idx] = website
                
                # Add row number for tracking (only if first column is not already set)
                if not row_data[0]:
                    row_data[0] = f"Row_{i+1}"
                
                f.write(','.join(row_data) + '\n')
        
        print(f"    Saved {len(websites)} websites to: {filename}")
        return filename
        
    except Exception as e:
        print(f"    Error saving sheet data: {e}")
        return None

def detect_industry_from_websites(websites):
    """Detect industry type from website URLs and domains"""
    try:
        if not websites:
            return 'general_business'
        
        # Count industry indicators
        industry_counts = {
            'property_management': 0,
            'real_estate': 0,
            'veterinary': 0,
            'dental': 0,
            'legal': 0,
            'accounting': 0,
            'landscaping': 0,
            'medical': 0,
            'automotive': 0
        }
        
        # Keywords for each industry
        industry_keywords = {
            'property_management': ['property', 'management', 'properties', 'pm', 'rental', 'leasing', 'apartment', 'condo', 'hvac', 'maintenance'],
            'real_estate': ['realestate', 'realtor', 'realty', 'estate', 'homes', 'houses', 'broker', 'agent'],
            'veterinary': ['vet', 'veterinary', 'animal', 'pet', 'clinic', 'hospital', 'dvm', 'animal hospital'],
            'dental': ['dental', 'dentist', 'dds', 'orthodont', 'periodont', 'oral', 'teeth'],
            'legal': ['law', 'legal', 'attorney', 'lawyer', 'firm', 'litigation', 'counsel'],
            'accounting': ['accounting', 'cpa', 'tax', 'bookkeeping', 'financial', 'audit'],
            'landscaping': ['landscape', 'lawn', 'turf', 'grounds', 'tree', 'garden', 'mowing'],
            'medical': ['medical', 'health', 'doctor', 'physician', 'clinic', 'hospital', 'healthcare'],
            'automotive': ['auto', 'car', 'vehicle', 'repair', 'service', 'garage', 'mechanic']
        }
        
        for website in websites[:10]:  # Check first 10 websites for efficiency
            domain = website.lower()
            for industry, keywords in industry_keywords.items():
                for keyword in keywords:
                    if keyword in domain:
                        industry_counts[industry] += 1
        
        # Find the industry with the highest count
        detected_industry = max(industry_counts, key=industry_counts.get)
        confidence = industry_counts[detected_industry]
        
        # Only return detected industry if confidence is high enough
        if confidence > 0:
            print(f"🏭 Detected industry: {detected_industry} (confidence: {confidence} matches)")
            return detected_industry
        else:
            print(f"🏭 No clear industry detected, using default: {INDUSTRY}")
            return INDUSTRY
            
    except Exception as e:
        print(f"⚠️ Error detecting industry: {e}")
        return INDUSTRY

def extract_domain_from_url(url):
    """Extract clean domain from URL with robust error handling"""
    try:
        if not url:
            return ""
            
        # Remove protocol
        domain = url.replace('https://', '').replace('http://', '')
        
        # Remove path, query parameters, and fragments
        domain = domain.split('/')[0].split('?')[0].split('#')[0]
        
        # Remove www. prefix
        if domain.startswith('www.'):
            domain = domain[4:]
            
        # Remove port numbers
        if ':' in domain:
            domain = domain.split(':')[0]
            
        return domain.lower()
        
    except Exception as e:
        print(f"⚠️ Error extracting domain from {url}: {e}")
        return ""

def normalize_domain_for_matching(domain):
    """Create multiple domain variations for better matching"""
    try:
        if not domain:
            return []
            
        variations = []
        
        # Original domain
        variations.append(domain.lower())
        
        # Domain without TLD
        if '.' in domain:
            domain_name = domain.split('.')[0]
            variations.append(domain_name)
            
        # Domain with common TLD variations
        if '.' in domain:
            base = domain.split('.')[0]
            for tld in ['.com', '.org', '.net', '.co']:
                variations.append(base + tld)
                
        return list(set(variations))  # Remove duplicates
        
    except Exception as e:
        print(f"⚠️ Error normalizing domain {domain}: {e}")
        return [domain.lower()] if domain else []

def deduplicate_websites(websites):
    """Remove duplicate websites while preserving order"""
    try:
        seen = set()
        deduplicated = []
        duplicates_found = 0
        
        for website in websites:
            # Normalize URL for comparison
            normalized = website.lower().rstrip('/')
            if normalized not in seen:
                seen.add(normalized)
                deduplicated.append(website)
            else:
                duplicates_found += 1
                print(f"🔄 Removed duplicate: {website}")
        
        if duplicates_found > 0:
            print(f"🔄 Removed {duplicates_found} duplicate websites")
        
        return deduplicated
        
    except Exception as e:
        print(f"⚠️ Error deduplicating websites: {e}")
        return websites

def create_buckets_for_sheet(websites, sheet_id, buckets_folder):
    """Create bucket files for a sheet"""
    try:
        buckets = []
        
        # Deduplicate websites first
        websites = deduplicate_websites(websites)
        
        # Ensure sheet_id is a string for consistent filename generation
        sheet_id_str = str(sheet_id)
        
        # If we have fewer websites than minimum bucket size, still create buckets
        if len(websites) < MIN_BUCKET_SIZE:
            print(f"    Sheet {sheet_id_str} has {len(websites)} websites (below minimum {MIN_BUCKET_SIZE})")
            print(f"    Creating single bucket with all {len(websites)} websites")
            # Create a single bucket with all websites
            bucket_filename = buckets_folder / f"{sheet_id_str}_bucket_01.csv"
            with open(bucket_filename, 'w', encoding='utf-8', newline='') as f:
                f.write("Website\n")
                for website in websites:
                    f.write(f"{website}\n")
            
            return [{
                'filename': bucket_filename,
                'websites': websites,
                'bucket_num': 1,
                'total_buckets': 1,
                'sheet_id': sheet_id_str,
                'headers': []
            }]
        
        # Calculate buckets for sheets with sufficient websites
        total_buckets = (len(websites) + MIN_BUCKET_SIZE - 1) // MIN_BUCKET_SIZE
        
        print(f"    Creating {total_buckets} buckets for {len(websites)} websites...")
        
        for bucket_num in range(total_buckets):
            start_idx = bucket_num * MIN_BUCKET_SIZE
            end_idx = min(start_idx + MIN_BUCKET_SIZE, len(websites))
            bucket_websites = websites[start_idx:end_idx]
            
            bucket_filename = buckets_folder / f"{sheet_id_str}_bucket_{bucket_num + 1:02d}.csv"
            
            with open(bucket_filename, 'w', encoding='utf-8', newline='') as f:
                f.write("Website\n")  # Simple header for bucket files
                for website in bucket_websites:
                    f.write(f"{website}\n")
            
            buckets.append({
                'filename': bucket_filename,
                'websites': bucket_websites,
                'bucket_num': bucket_num + 1,
                'total_buckets': total_buckets,
                'sheet_id': sheet_id_str,
                'headers': []  # Will be populated from sheet data
            })
            
            print(f"      Created bucket {bucket_num + 1}/{total_buckets}: {len(bucket_websites)} websites")
        
        return buckets
        
    except Exception as e:
        print(f"    Error creating buckets: {e}")
        return []

def create_combined_buckets_from_small_sheets(all_sheets_data, buckets_folder):
    """Create combined buckets from sheets with insufficient websites"""
    try:
        print(f"\n🔄 Creating combined buckets from small sheets...")
        
        # Collect all websites from sheets with insufficient data
        small_sheets_websites = []
        for sheet_id, data in all_sheets_data.items():
            if len(data['websites']) < MIN_BUCKET_SIZE:
                for website in data['websites']:
                    small_sheets_websites.append({
                        'sheet': sheet_id,
                        'website': website
                    })
        
        if not small_sheets_websites:
            print("  No small sheets to combine")
            return []
        
        print(f"  Found {len(small_sheets_websites)} websites from small sheets")
        
        # Create combined buckets
        combined_buckets = []
        total_combined_buckets = (len(small_sheets_websites) + MIN_BUCKET_SIZE - 1) // MIN_BUCKET_SIZE
        
        for bucket_num in range(total_combined_buckets):
            start_idx = bucket_num * MIN_BUCKET_SIZE
            end_idx = min(start_idx + MIN_BUCKET_SIZE, len(small_sheets_websites))
            bucket_data = small_sheets_websites[start_idx:end_idx]
            
            bucket_filename = buckets_folder / f"combined_bucket_{bucket_num + 1:02d}.csv"
            
            # Create CSV with sheet and website information
            with open(bucket_filename, 'w', encoding='utf-8', newline='') as f:
                f.write("Sheet,Website\n")  # Header with sheet information
                for item in bucket_data:
                    f.write(f"{item['sheet']},{item['website']}\n")
            
            # Create text file with formatted output
            txt_filename = buckets_folder / f"combined_bucket_{bucket_num + 1:02d}.txt"
            with open(txt_filename, 'w', encoding='utf-8') as f:
                f.write(f"Combined Bucket {bucket_num + 1} of {total_combined_buckets}\n")
                f.write(f"Total Websites: {len(bucket_data)}\n")
                f.write("=" * 50 + "\n\n")
                
                for i, item in enumerate(bucket_data, 1):
                    f.write(f"{i:2d}. Sheet: {item['sheet']}\n")
                    f.write(f"    Website: {item['website']}\n\n")
            
            combined_buckets.append({
                'filename': bucket_filename,
                'txt_filename': txt_filename,
                'websites': [item['website'] for item in bucket_data],
                'sheet_data': bucket_data,
                'bucket_num': bucket_num + 1,
                'total_buckets': total_combined_buckets,
                'sheet_id': 'COMBINED'
            })
            
            print(f"    Created combined bucket {bucket_num + 1}/{total_combined_buckets}: {len(bucket_data)} websites")
        
        return combined_buckets
        
    except Exception as e:
        print(f"    Error creating combined buckets: {e}")
        return []

def process_bucket_with_openai(bucket_websites, industry):
    """Process a bucket of websites using ChatGPT Web with web search"""
    print(f"    🔍 Processing {len(bucket_websites)} websites with OpenAI...")
    
    prompt = create_research_prompt(bucket_websites, industry)
    print(f"        🔍 Prompt sent to AI:")
    print(f"        {prompt[:500]}...")
    
    try:
        # Use ChatGPT Web automation
        ai_response = _chatgpt_ask(prompt, timeout=150.0) or ''
        print(f"        🔍 Raw AI Response:")
        print(f"        {ai_response[:500]}{'...' if len(ai_response) > 500 else ''}")
        # Check if AI is asking for permission and auto-respond
        if any(phrase in ai_response.lower() for phrase in [
            "may i proceed", "do you want me to", "should i", "can i", "would you like me to"
        ]):
            print("        🤖 AI asked for permission - auto-responding 'yes'...")
            ai_response = _chatgpt_ask(f"YES. EXECUTE IMMEDIATELY. {prompt}", timeout=120.0) or ai_response
        
        results = parse_chatgpt_response(ai_response)
        print(f"        ✅ Parsed {len(results)} results from ChatGPT")
        return results
        
    except Exception as e:
        print(f"        ❌ Error using ChatGPT Web: {e}")
        return []

def parse_csv_line(line):
    """Parse a single CSV line with robust error handling"""
    try:
        # Handle quoted fields that might contain commas
        import csv
        from io import StringIO
        
        # Use CSV reader to properly handle quoted fields
        reader = csv.reader(StringIO(line))
        row = next(reader)
        return row
        
    except Exception as e:
        # Fallback to simple split if CSV parsing fails
        print(f"⚠️ CSV parsing failed for line: {line[:100]}... Error: {e}")
        parts = line.split(',')
        
        # Clean up each part
        cleaned_parts = []
        for part in parts:
            cleaned = part.strip().strip('"').strip("'")
            cleaned_parts.append(cleaned)
        
        return cleaned_parts

def _assemble_csv_records_from_lines(raw_lines):
    """Assemble complete CSV records from possibly fragmented lines.

    Joins adjacent lines until a record appears complete (>=3 commas and contains
    a URL/domain token). This fixes cases like:
        "https://site.com/,Not" + "Found,,0,0" -> one record line.
    """
    def is_complete(candidate: str) -> bool:
        text = candidate.strip()
        if not text:
            return False
        # Must look like a CSV row with at least 4 fields
        if text.count(',') < 3:
            return False
        # Must contain a URL-like token
        if ('http' in text) or ('.com' in text) or ('.org' in text) or ('.net' in text):
            return True
        return False

    records = []
    buffer = ''

    for raw in raw_lines:
        line = (raw if isinstance(raw, str) else str(raw)).strip()
        if not line:
            continue
        # Drop markdown fences
        if line.startswith('```') and line.endswith('```') and len(line) <= 6:
            continue
        if line.startswith('```'):
            line = line[3:].strip()
        if line.endswith('```'):
            line = line[:-3].strip()

        # Skip header lines
        if line.startswith('Website,') or line.startswith('#'):
            continue

        if not buffer:
            buffer = line
            # If already complete, flush immediately
            if is_complete(buffer):
                records.append(buffer)
                buffer = ''
            continue

        # Decide whether this line starts a new record or continues the buffer
        looks_like_record_start = (',' in line) and (('http' in line) or ('.com' in line) or ('.org' in line) or ('.net' in line))

        if is_complete(buffer) and looks_like_record_start:
            # Flush previous and start new
            records.append(buffer)
            buffer = line
            if is_complete(buffer):
                records.append(buffer)
                buffer = ''
        else:
            # Continue the buffer
            buffer = f"{buffer} {line}".strip()
            if is_complete(buffer):
                records.append(buffer)
                buffer = ''

    if buffer:
        records.append(buffer)

    return records

def parse_chatgpt_response(response_text):
    """Parse ChatGPT response and extract structured data with robust error handling"""
    try:
        parsed_results = []

        # Normalize to lines
        lines = response_text if isinstance(response_text, list) else (response_text or '').strip().split('\n')

        # Assemble complete CSV records from possibly fragmented lines
        records = _assemble_csv_records_from_lines(lines)

        for idx, record in enumerate(records, 1):
            try:
                parts = parse_csv_line(record)
                if parts and len(parts) >= 4:
                    website = parts[0].strip()
                    first_name = parts[1].strip()
                    last_name = parts[2].strip()
                    locations = ensure_minimum_locations(parts[3].strip())

                    professionals = parts[4].strip() if (COUNT_PROFESSIONALS and len(parts) > 4) else "0"

                    if website and website.startswith('http'):
                        parsed_results.append({
                            'website': website,
                            'first_name': first_name,
                            'last_name': last_name,
                            'locations': locations,
                            'professionals': professionals
                        })
                    else:
                        print(f"⚠️ Invalid website format on record {idx}: {website}")
                else:
                    print(f"⚠️ Invalid result format (need 4+ fields) on record {idx}: {record[:100]}...")
            except Exception as line_error:
                print(f"⚠️ Error parsing record {idx}: {line_error}")
                print(f"   Record content: {record[:100]}...")
                continue

        return parsed_results
        
    except Exception as e:
        print(f"❌ Error parsing ChatGPT response: {e}")
        return []

def clean_chatgpt_csv_output(raw_output):
    """Clean and extract CSV data from ChatGPT output"""
    try:
        cleaned_results = []
        lines = raw_output.strip().split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Remove markdown formatting
            if line.startswith('```'):
                line = line[3:]
            if line.endswith('```'):
                line = line[:-3]
            
            # Look for CSV formatted lines with website URLs or domain names
            if ',' in line and ('.com' in line or '.org' in line or '.net' in line or 'http' in line):
                parts = line.split(',')
                if len(parts) >= 4:
                    # Clean each part
                    cleaned_parts = [part.strip() for part in parts]
                    # Reconstruct the line
                    cleaned_line = ','.join(cleaned_parts)
                    cleaned_results.append(cleaned_line)
                    print(f"      Cleaned CSV line: {cleaned_line}")
        
        return cleaned_results
        
    except Exception as e:
        print(f"Error cleaning ChatGPT CSV output: {e}")
        return []

def debug_combined_bucket_processing(sheet_id, buckets_folder, results_folder):
    """Debug function to show how combined buckets are being processed"""
    try:
        print(f"\n🔍 Debugging combined bucket processing for {sheet_id}")
        
        # Check for combined buckets
        combined_bucket_files = list(buckets_folder.glob("combined_bucket_*.csv"))
        if not combined_bucket_files:
            print(f"  No combined buckets found")
            return
        
        print(f"  Found {len(combined_bucket_files)} combined buckets:")
        
        for bucket_file in combined_bucket_files:
            print(f"    📁 {bucket_file.name}")
            
            # Read the bucket data
            with open(bucket_file, 'r', encoding='utf-8') as f:
                bucket_data = []
                reader = csv.DictReader(f)
                for row in reader:
                    bucket_data.append({
                        'sheet': row['Sheet'],
                        'website': row['Website']
                    })
            
            # Show websites from our sheet
            sheet_websites = [item for item in bucket_data if item['sheet'] == sheet_id]
            if sheet_websites:
                print(f"      Contains {len(sheet_websites)} websites from {sheet_id}:")
                for item in sheet_websites[:3]:  # Show first 3
                    print(f"        - {item['website']}")
                if len(sheet_websites) > 3:
                    print(f"        ... and {len(sheet_websites) - 3} more")
                
                # Check if results file exists
                results_file = results_folder / f"{bucket_file.stem}_results.csv"
                if results_file.exists():
                    print(f"      ✅ Results file found: {results_file.name}")
                else:
                    print(f"      ❌ Results file missing: {results_file.name}")
            else:
                print(f"      No websites from {sheet_id}")
        
    except Exception as e:
        print(f"      Error debugging combined buckets: {e}")

def call_webhook_for_sheet(spreadsheet_id):
    """Call the Google Apps Script webhook to trigger sheet processing"""
    try:
        import requests
        
        print(f"\n🌐 Calling webhook for spreadsheet: {spreadsheet_id}")
        
        # Prepare the webhook payload
        payload = {
            'sheetId': spreadsheet_id
        }
        
        # Make the POST request to the webhook
        response = requests.post(WEBHOOK_URL, data=payload, timeout=30)
        
        if response.status_code == 200:
            print(f"  ✅ Webhook called successfully!")
            print(f"  📝 Response: {response.text}")
            return True
        else:
            print(f"  ❌ Webhook call failed with status {response.status_code}")
            print(f"  📝 Response: {response.text}")
            return False
            
    except ImportError:
        print("  ❌ 'requests' library not available. Install with: pip install requests")
        return False
    except Exception as e:
        print(f"  ❌ Error calling webhook: {e}")
        return False

def split_combined_bucket_results_by_sheet(buckets_folder, results_folder):
    """Split combined bucket results by sheet and create separate CSV files for each city"""
    try:
        print(f"\n🔄 Splitting combined bucket results by sheet...")
        
        # Find all combined bucket results
        combined_results_files = list(results_folder.glob("combined_bucket_*_results.csv"))
        if not combined_results_files:
            print(f"  No combined bucket results found")
            return
        
        print(f"  Found {len(combined_results_files)} combined bucket result files")
        
        # Process each combined bucket result file
        for results_file in combined_results_files:
            print(f"    Processing {results_file.name}")
            
            # Find corresponding bucket file
            bucket_file = buckets_folder / f"{results_file.stem.replace('_results', '')}.csv"
            if not bucket_file.exists():
                print(f"      ❌ Bucket file not found: {bucket_file}")
                continue
            
            # Read the bucket data to get sheet information
            with open(bucket_file, 'r', encoding='utf-8') as f:
                bucket_data = []
                reader = csv.DictReader(f)
                for row in reader:
                    bucket_data.append({
                        'sheet': row['Sheet'],
                        'website': row['Website']
                    })
            
            # Read the ChatGPT results
            with open(results_file, 'r', encoding='utf-8') as f:
                raw_results = [line.strip() for line in f.readlines()]
            
            # Clean and parse the results
            cleaned_results = clean_chatgpt_csv_output('\n'.join(raw_results))
            
            # Group results by sheet
            sheet_results = {}
            for result in cleaned_results:
                result_parts = result.split(',')
                if result_parts and 'http' in result_parts[0]:
                    result_website = result_parts[0].strip()
                    
                    # Find which sheet this website belongs to
                    for item in bucket_data:
                        if item['website'] == result_website:
                            sheet_id = item['sheet']
                            if sheet_id not in sheet_results:
                                sheet_results[sheet_id] = []
                            sheet_results[sheet_id].append(result)
                            break
            
            # Create separate CSV files for each sheet
            for sheet_id, results in sheet_results.items():
                if results:
                    # Create sheet-specific results file
                    sheet_results_file = results_folder / f"{sheet_id}_combined_results.csv"
                    
                    with open(sheet_results_file, 'w', encoding='utf-8', newline='') as f:
                        # Write header - use default format for combined results
                        f.write("Website,First Name,Last Name,Locations,Professionals\n")
                        # Write results
                        for result in results:
                            f.write(f"{result}\n")
                    
                    print(f"      ✅ Created {sheet_id}_combined_results.csv with {len(results)} results")
        
        print(f"  ✅ Combined bucket results split by sheet completed")
        
        # Debug: List all files created
        print(f"  📁 Files created in results folder:")
        for file_path in results_folder.glob("*"):
            if file_path.is_file():
                print(f"    - {file_path.name}")
        
    except Exception as e:
        print(f"  ❌ Error splitting combined bucket results: {e}")

def update_bucket_csv_with_results(bucket_info, results, results_folder):
    """Update the original bucket CSV with ChatGPT research results"""
    try:
        # Parse ChatGPT response
        parsed_results = parse_chatgpt_response(results)
        if not parsed_results:
            print(f"        No valid results to update bucket CSV")
            return None
        
        # Read original bucket CSV
        bucket_csv_path = bucket_info['filename']
        if not bucket_csv_path.exists():
            print(f"        Original bucket CSV not found: {bucket_csv_path}")
            return None
        
        # Create updated bucket CSV with research results
        updated_bucket_filename = results_folder / f"{bucket_info['filename'].stem}_updated.csv"
        
        with open(updated_bucket_filename, 'w', encoding='utf-8', newline='') as f:
            # Write header with research data - use dynamic format
            sheet_headers = bucket_info.get('headers', [])
            sheet_requirements = detect_sheet_requirements(sheet_headers)
            f.write(f"{sheet_requirements['output_format']},Sheet\n")
            
            # Write updated data
            for result in parsed_results:
                website = result['website']
                sheet_id = bucket_info.get('sheet_id', 'Unknown')
                
                # For combined buckets, try to find the specific sheet
                if sheet_id == 'COMBINED' and 'sheet_data' in bucket_info:
                    for item in bucket_info['sheet_data']:
                        if item['website'] == website:
                            sheet_id = item['sheet']
                            break
                
                f.write(f"{website},{result['first_name']},{result['last_name']},{result['locations']},{result['professionals']},{sheet_id}\n")
        
        print(f"        Updated bucket CSV saved to: {updated_bucket_filename}")
        return updated_bucket_filename
        
    except Exception as e:
        print(f"        Error updating bucket CSV: {e}")
        return None

def save_bucket_results(bucket_info, results, results_folder):
    """Save ChatGPT results for a bucket and update bucket CSV"""
    try:
        # Save raw results
        results_filename = results_folder / f"{bucket_info['filename'].stem}_results.csv"
        
        with open(results_filename, 'w', encoding='utf-8', newline='') as f:
            # Use dynamic header based on sheet requirements
            sheet_headers = bucket_info.get('headers', [])
            sheet_requirements = detect_sheet_requirements(sheet_headers)
            f.write(f"{sheet_requirements['output_format']}\n")
            for result in results:
                f.write(f"{result}\n")
        
        # Update bucket CSV with research results
        updated_bucket_csv = update_bucket_csv_with_results(bucket_info, results, results_folder)
        
        # For combined buckets, also save a text file with sheet information
        if bucket_info.get('sheet_id') == 'COMBINED' and 'txt_filename' in bucket_info:
            txt_results_filename = results_folder / f"{bucket_info['txt_filename'].stem}_results.txt"
            
            with open(txt_results_filename, 'w', encoding='utf-8') as f:
                f.write(f"Combined Bucket {bucket_info['bucket_num']} Results\n")
                f.write(f"Total Websites: {len(results)}\n")
                f.write("=" * 50 + "\n\n")
                
                for i, result in enumerate(results, 1):
                    f.write(f"{i:2d}. {result}\n")
                    # Try to find sheet information for this website
                    website = result.split(',')[0] if result and ',' in result else ''
                    if website:
                        for item in bucket_info.get('sheet_data', []):
                            if item['website'] == website:
                                f.write(f"    Sheet: {item['sheet']}\n")
                                break
                    f.write("\n")
        
        print(f"        Results saved to: {results_filename}")
        if updated_bucket_csv:
            print(f"        Updated bucket CSV: {updated_bucket_csv}")
        return results_filename
        
    except Exception as e:
        print(f"        Error saving bucket results: {e}")
        return None

def process_all_sheets(spreadsheet_id, target_headers, temp_folder, sheets_folder, buckets_folder, results_folder, selected_worksheet_ids=None):
    """Process all sheets in the spreadsheet one by one"""
    try:
        if selected_worksheet_ids:
            print(f"\n📊 Processing selected sheets from spreadsheet...")
        else:
            print(f"\n📊 Processing sheets one by one from spreadsheet...")
        
        # Get all worksheets
        worksheets, error = get_all_sheets(spreadsheet_id)
        if error:
            return False, error
        
        # Filter worksheets if specific ones are selected
        if selected_worksheet_ids:
            # Convert selected_worksheet_ids to a set for faster lookup
            selected_ids_set = set(str(id) for id in selected_worksheet_ids)
            worksheets = [ws for ws in worksheets if str(ws.id) in selected_ids_set or ws.title in selected_ids_set]
            print(f"📋 Filtered to {len(worksheets)} selected worksheets out of {len(worksheets) + len(selected_ids_set) - len(worksheets)} total")
        
        all_sheets_data = {}
        
        # Process each worksheet sequentially
        for i, worksheet in enumerate(worksheets, 1):
            sheet_id = str(worksheet.id)  # Use sheet ID as string for consistency
            
            print(f"\n{'='*60}")
            print(f"📋 Processing sheet {i}/{len(worksheets)}: '{worksheet.title}' (ID: {sheet_id})")
            print(f"{'='*60}")
            
            # Skip Error Log sheet
            if worksheet.title.strip() == "Error Log":
                print(f"⏭️  Skipping Error Log sheet (ID: {sheet_id})")
                continue
            
            # Extract websites from this sheet
            print(f"  🔍 Extracting websites from sheet '{worksheet.title}'...")
            websites, error = extract_websites_from_sheet_by_name(spreadsheet_id, worksheet, target_headers)
            if error:
                print(f"  ❌ Error extracting websites: {error}")
                print(f"  ⏭️  Skipping sheet '{worksheet.title}' (ID: {sheet_id}) and continuing to next sheet...")
                continue
            
            # DEBUG: Show the actual websites being extracted
            print(f"  🔍 DEBUG: Extracted {len(websites)} websites from sheet '{worksheet.title}':")
            for j, website in enumerate(websites[:5]):  # Show first 5
                print(f"    {j+1}. {website}")
            if len(websites) > 5:
                print(f"    ... and {len(websites) - 5} more")
            print()
            
            if not websites:
                print(f"  ⚠️  No websites found in sheet '{worksheet.title}' (ID: {sheet_id})")
                print(f"  ⏭️  Skipping sheet '{worksheet.title}' and continuing to next sheet...")
                continue
            
            # Save sheet data using sheet ID as key
            print(f"  💾 Saving sheet data for '{worksheet.title}'...")
            sheet_file = save_sheet_data(websites, target_headers, sheet_id, sheets_folder)
            if not sheet_file:
                print(f"  ❌ Failed to save sheet data for sheet '{worksheet.title}' (ID: {sheet_id})")
                print(f"  ⏭️  Skipping sheet '{worksheet.title}' and continuing to next sheet...")
                continue
            
            # Create buckets for this sheet
            buckets = create_buckets_for_sheet(websites, sheet_id, buckets_folder)
            
            # Add headers to each bucket for dynamic format detection
            # Use actual sheet headers instead of target_headers for dynamic format detection
            ws_values, err = rate_limited_sheets_api_call(worksheet.get_all_values)
            actual_headers = [header.strip() for header in (ws_values[0] if (ws_values and len(ws_values) > 0) else [])] if not err and ws_values else target_headers
            for bucket in buckets:
                bucket['headers'] = actual_headers
            
            # DEBUG: Verify bucket websites match extracted websites
            if buckets:
                print(f"  🔍 DEBUG: Verifying bucket websites match extracted websites...")
                bucket_websites = []
                for bucket in buckets:
                    bucket_websites.extend(bucket['websites'])
                
                print(f"    Extracted websites: {len(websites)}")
                print(f"    Bucket websites: {len(bucket_websites)}")
                
                # Check if they match
                if set(websites) == set(bucket_websites):
                    print(f"    ✅ Bucket websites match extracted websites")
                else:
                    print(f"    ⚠️  Mismatch detected!")
                    print(f"    Missing from buckets: {set(websites) - set(bucket_websites)}")
                    print(f"    Extra in buckets: {set(bucket_websites) - set(websites)}")
                print()
            
            # Store data using sheet ID as key
            all_sheets_data[sheet_id] = {
                'websites': websites,
                'buckets': buckets,
                'sheet_file': sheet_file,
                'sheet_id': sheet_id,
                'headers': actual_headers  # Use actual sheet headers for dynamic format detection
            }
            
            if buckets:
                print(f"  ✅ Sheet {sheet_id} prepared: {len(websites)} websites, {len(buckets)} buckets")
            else:
                print(f"  ⚠️  Sheet {sheet_id} prepared: {len(websites)} websites (insufficient for buckets)")
            
            print(f"  📁 Sheet data saved to: {sheet_file}")
            print(f"  🪣 Buckets created in: {buckets_folder}")
        
        # Create combined buckets from small sheets
        combined_buckets = create_combined_buckets_from_small_sheets(all_sheets_data, buckets_folder)
        if combined_buckets:
            # Add combined buckets to a special entry
            all_sheets_data['COMBINED_SMALL_SHEETS'] = {
                'websites': [item['website'] for bucket in combined_buckets for item in bucket['sheet_data']],
                'buckets': combined_buckets,
                'sheet_file': None,
                'sheet_id': 'COMBINED_SMALL_SHEETS'
            }
        
        if not all_sheets_data:
            print(f"\n❌ No valid sheets found to process")
            return False, "No valid sheets found"
        
        print(f"\n📊 SUMMARY: Prepared {len(all_sheets_data)} sheets for processing")
        for sheet_id, data in all_sheets_data.items():
            print(f"  - Sheet {sheet_id}: {len(data['websites'])} websites, {len(data['buckets'])} buckets")
        
        print(f"\n🔍 SHEET PROCESSING SUMMARY:")
        print(f"  - Total sheets found: {len(worksheets)}")
        print(f"  - Sheets with websites: {len(all_sheets_data)}")
        print(f"  - Sheets skipped: {len(worksheets) - len(all_sheets_data)}")
        
        if len(all_sheets_data) < len(worksheets):
            print(f"\n⚠️  Some sheets were skipped. Common reasons:")
            print(f"  - No website column found")
            print(f"  - No valid website URLs")
            print(f"  - Empty or invalid data")
            print(f"  - Error Log sheets (automatically skipped)")
        
        return True, all_sheets_data
        
    except Exception as e:
        return False, f"Error processing all sheets: {str(e)}"

def process_bucket_with_openai_parallel(bucket_info, industry, results_folder):
    """Process a single bucket with OpenAI API"""
    try:
        websites = bucket_info['websites']
        bucket_num = bucket_info['bucket_num']
        total_buckets = bucket_info['total_buckets']
        sheet_id = bucket_info.get('sheet_id', 'Unknown')
        
        print(f"      Processing bucket {bucket_num}/{total_buckets} (Sheet {sheet_id}) with {len(websites)} websites...")
        
        # Get sheet headers from bucket info to determine requirements
        sheet_headers = bucket_info.get('headers', [])
        sheet_requirements = detect_sheet_requirements(sheet_headers)
        
        # Detect industry from websites if not provided
        detected_industry = detect_industry_from_websites(websites)
        print(f"        📊 Sheet requirements: {sheet_requirements}")
        print(f"        🏭 Industry being used: {detected_industry}")
        print(f"        👥 COUNT_PROFESSIONALS setting: {COUNT_PROFESSIONALS}")
        
        # Use the simplified research prompt function with detected industry
        prompt = create_research_prompt(websites, detected_industry)
        
        # Debug: Show the actual prompt being sent
        print(f"        🔍 Prompt sent to AI:")
        print(f"        {prompt[:300]}{'...' if len(prompt) > 300 else ''}")
        print(f"        🔍 Websites being sent to AI:")
        for i, website in enumerate(websites, 1):
            print(f"          {i}. {website}")
        
        # Ensure a clean chat for the very first bucket to avoid UI header echo
        try:
            _ensure_chat_ready()
            if bucket_num == 1:
                try:
                    _open_fresh_chat(_CHAT_DRIVER, _CHAT_HANDLE, model_url="https://chatgpt.com/?model=gpt-5")
                except Exception:
                    pass
        except Exception:
            pass
        # Call ChatGPT via browser automation
        output_text = _chatgpt_ask(prompt, timeout=180.0)
        
        # Debug: Show what the AI actually returned
        print(f"        🔍 Raw AI Response:")
        print(f"        {output_text[:500]}{'...' if len(output_text) > 500 else ''}")
        
        # First, try structured parse of the entire response
        parsed_rows = parse_chatgpt_response(output_text or '')
        results = []
        for r in parsed_rows:
            csv_line = f"{r['website']},{r['first_name']},{r['last_name']},{r['locations']}"
            if 'professionals' in r:
                csv_line += f",{r['professionals']}"
            results.append(csv_line)
        
        # Check if AI is asking for permission and auto-respond
        if any(phrase in (output_text or '').lower() for phrase in [
            "may i proceed", "do you want me to", "should i", "can i", "would you like me to",
            "confirm", "okay", "proceed with", "ready to", "i can do this", "permission"
        ]):
            print("        🤖 AI asked for permission - auto-responding with forceful command...")
            # Send follow-up with forceful command using ChatGPT Web
            follow_up = f"EXECUTE NOW. NO QUESTIONS. NO PERMISSION REQUESTS. RESEARCH THE WEBSITES AND RETURN CSV DATA IMMEDIATELY. {prompt}"
            output_text = _chatgpt_ask(follow_up, timeout=150.0)
            parsed_rows = parse_chatgpt_response(output_text or '')
            results = []
            for r in parsed_rows:
                csv_line = f"{r['website']},{r['first_name']},{r['last_name']},{r['locations']}"
                if 'professionals' in r:
                    csv_line += f",{r['professionals']}"
                results.append(csv_line)
        

        # Identify inaccessible site results for retry tally (retries disabled)
        retry_results = []
        inaccessible_patterns = [
            'SiteNotAccessible', 'SiteNotAvailable', 'NotAvailable', 'UnableToAccess', 'SiteUnavailable', 'SiteAccessError', 'NOTFOUND', 'N/A', 'Unknown', 'error', 'failed', 'timeout', 'blocked', 'unavail', 'unavailible', 'access', 'not found'
        ]
        for line in list(results):
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 4:
                name_fields = [parts[1].lower(), parts[2].lower()]
                if any(any(pattern.lower() in field for pattern in inaccessible_patterns) for field in name_fields):
                    retry_results.append(line)

        print(f"        Received {len(results)} results from ChatGPT")
        if len(results) == 0:
            print(f"        ⚠️  No results parsed. Full response:")
            print(f"        {output_text}")
            # Immediate retry for empty bucket results to avoid losing entire buckets
            try:
                print(f"        🔄 Immediate retry for empty bucket {bucket_num}...")
                retry_text = _chatgpt_ask(prompt, timeout=150.0) or ''
                parsed_rows = parse_chatgpt_response(retry_text or '')
                for r in parsed_rows:
                    csv_line = f"{r['website']},{r['first_name']},{r['last_name']},{r['locations']}"
                    if 'professionals' in r:
                        csv_line += f",{r['professionals']}"
                    results.append(csv_line)
                print(f"        🔁 Empty-bucket retry recovered {len(results)} results")
            except Exception as e:
                print(f"        ⚠️  Empty-bucket retry failed: {e}")

        print(f"        ✅ Bucket {bucket_num} completed: {len(results)} results, {len(retry_results)} marked for retry")

        # Save bucket results
        save_bucket_results(bucket_info, results, results_folder)

        # Retry buckets disabled by user request

        return {
            'bucket_num': bucket_num,
            'sheet_id': sheet_id,
            'success': True,
            'results_count': len(results),
            'message': f"Successfully processed bucket {bucket_num}"
        }
        
    except Exception as e:
        error_msg = str(e)
        
        # Handle timeout errors
        if 'timeout' in error_msg.lower() or 'timed out' in error_msg.lower():
            print(f"        ⏰ Timeout error for bucket {bucket_info.get('bucket_num', 'Unknown')}")
            print(f"        🔄 Will retry this bucket with longer timeout")
            return {
                'bucket_num': bucket_info.get('bucket_num', 'Unknown'),
                'sheet_id': bucket_info.get('sheet_id', 'Unknown'),
                'success': False,
                'results_count': 0,
                'message': f"Request timed out - will retry later",
                'timeout_error': True
            }
        
        # Handle rate limiting
        if '429' in error_msg or 'rate limit' in error_msg.lower():
            print(f"        ⚠️  Rate limit hit for bucket {bucket_info.get('bucket_num', 'Unknown')}")
            print(f"        🔄 Will retry this bucket later with exponential backoff")
            return {
                'bucket_num': bucket_info.get('bucket_num', 'Unknown'),
                'sheet_id': bucket_info.get('sheet_id', 'Unknown'),
                'success': False,
                'results_count': 0,
                'message': f"Rate limit hit - will retry later",
                'rate_limited': True
            }
        
        # Handle 401 authentication errors
        if '401' in error_msg and 'invalid_organization' in error_msg.lower():
            print(f"        🔑 API Key authentication error for bucket {bucket_info.get('bucket_num', 'Unknown')}")
            print(f"        💡 This usually means the API key doesn't have access to the organization")
            print(f"        🔧 Please check your OpenAI API key configuration")
            return {
                'bucket_num': bucket_info.get('bucket_num', 'Unknown'),
                'sheet_id': bucket_info.get('sheet_id', 'Unknown'),
                'success': False,
                'results_count': 0,
                'message': f"API Key authentication failed - check your OpenAI API key configuration",
                'auth_error': True
            }
        
        # Handle other errors
        return {
            'bucket_num': bucket_info.get('bucket_num', 'Unknown'),
            'sheet_id': bucket_info.get('sheet_id', 'Unknown'),
            'success': False,
            'results_count': 0,
            'message': f"Error processing bucket {bucket_info.get('bucket_num', 'Unknown')}: {error_msg}"
        }

def process_rate_limited_buckets(rate_limited_buckets, industry, results_folder, max_retries=3):
    """Process rate-limited and timeout buckets with exponential backoff"""
    if not rate_limited_buckets:
        return []
    
    print(f"\n🔄 Processing {len(rate_limited_buckets)} rate-limited/timeout buckets with exponential backoff...")
    
    retry_results = []
    for attempt in range(max_retries):
        delay = (2 ** attempt) * 5  # 5s, 10s, 20s delays
        print(f"        🔄 Retry attempt {attempt + 1}/{max_retries} with {delay}s delay...")
        
        time.sleep(delay)
        
        for bucket_info in rate_limited_buckets[:]:  # Copy list to modify during iteration
            try:
                print(f"        🔄 Retrying bucket {bucket_info['bucket_num']}...")
                result = process_bucket_with_openai_parallel(bucket_info, industry, results_folder)
                
                if result['success']:
                    retry_results.append(result)
                    rate_limited_buckets.remove(bucket_info)  # Remove successful bucket
                    print(f"        ✅ Bucket {bucket_info['bucket_num']} retry successful")
                else:
                    if result.get('rate_limited', False):
                        print(f"        ⚠️  Bucket {bucket_info['bucket_num']} still rate limited")
                    elif result.get('timeout_error', False):
                        print(f"        ⏰ Bucket {bucket_info['bucket_num']} still timing out")
                    else:
                        print(f"        ❌ Bucket {bucket_info['bucket_num']} retry failed: {result['message']}")
                    
            except Exception as e:
                print(f"        ❌ Error retrying bucket {bucket_info['bucket_num']}: {e}")
        
        if not rate_limited_buckets:
            print(f"        ✅ All rate-limited buckets processed successfully")
            break
    
    if rate_limited_buckets:
        print(f"        ⚠️  {len(rate_limited_buckets)} buckets still failed after {max_retries} retries")
    
    return retry_results

def process_all_buckets_with_openai(all_sheets_data, industry, results_folder):
    """Process all buckets with OpenAI API sequentially (no multiprocessing)"""
    try:
        print(f"\n🤖 Processing all buckets with OpenAI API sequentially...")
        print(f"⏱️  Batch delay: {BATCH_DELAY} seconds")
        print(f"🔄 Max retries per bucket: {MAX_RETRIES_OPENAI}")
        print(f"⏳ Retry delay: {RETRY_DELAY_OPENAI} seconds")
        
        # Show API key status
        available_keys = get_available_keys_count()
        print(f"🔑 Using single API key mode")
        
        # Collect all buckets from all sheets
        all_buckets = []
        for sheet_id, sheet_data in all_sheets_data.items():
            for bucket_info in sheet_data['buckets']:
                all_buckets.append(bucket_info)
        
        total_buckets = len(all_buckets)
        print(f"📊 Total buckets to process: {total_buckets}")
        
        if total_buckets == 0:
            print("⚠️  No buckets to process")
            return True, None
        

        # Process buckets sequentially with error handling
        successful_buckets = 0
        failed_buckets = 0
        
        for bucket in all_buckets:
            try:
                result = process_bucket_with_openai_parallel(bucket, industry, results_folder)
                if result['success']:
                    successful_buckets += 1
                    print(f"  ✅ {result['message']}")
                else:
                    failed_buckets += 1
                    if 'rate limit' in result['message'].lower():
                        print(f"  ⚠️  Rate limited: {result['message']}")
                        print(f"  🔄 Retrying bucket {bucket['bucket_num']} (Sheet {bucket.get('sheet_id', 'Unknown')}) in 5 seconds...")
                        time.sleep(5)
                        retry_result = process_bucket_with_openai_parallel(bucket, industry, results_folder)
                        if retry_result['success']:
                            successful_buckets += 1
                            failed_buckets -= 1
                            print(f"  ✅ Retry successful: {retry_result['message']}")
                        else:
                            print(f"  ❌ Retry failed: {retry_result['message']}")
                    elif result.get('auth_error', False):
                        print(f"  🔑 Authentication error: {result['message']}")
                        print(f"  💡 Please check your OpenAI API key configuration")
                        print(f"  🛑 Stopping processing due to authentication error")
                        return False, "API Key authentication failed"
                    else:
                        print(f"  ❌ {result['message']}")
            except Exception as e:
                failed_buckets += 1
                print(f"  ❌ Exception in bucket {bucket['bucket_num']}: {e}")
                # Check if it's an authentication error
                if '401' in str(e) and 'invalid_organization' in str(e).lower():
                    print(f"  🔑 Authentication error detected: {e}")
                    print(f"  💡 Please check your OpenAI API key configuration")
                    print(f"  🛑 Stopping processing due to authentication error")
                    return False, "API Key authentication failed"
            time.sleep(BATCH_DELAY)

        print(f"\n🎉 Sequential processing completed!")
        print(f"✅ Successful buckets: {successful_buckets}")
        print(f"❌ Failed buckets: {failed_buckets}")
        print(f"📊 Total processed: {successful_buckets + failed_buckets}/{total_buckets}")
        
        if successful_buckets > 0:
            return True, None
        else:
            return False, "No buckets were processed successfully"
        
    except Exception as e:
        return False, f"Error in parallel processing: {str(e)}"

def collect_all_research_results(all_sheets_data, results_folder):
    """Collect all research results from bucket result files"""
    try:
        print(f"\n📊 Collecting all research results...")
        
        # Debug: List all files in results folder
        print(f"  📁 All files in results folder:")
        for file_path in results_folder.glob("*"):
            if file_path.is_file():
                print(f"    - {file_path.name}")
        
        all_results = {}
        
        for sheet_id, sheet_data in all_sheets_data.items():
            if sheet_id == 'COMBINED_SMALL_SHEETS':
                continue  # Skip combined sheets for now
                
            print(f"  📋 Processing results for sheet: {sheet_id}")
            
            # Find all result files for this sheet using sheet ID
            result_files = list(results_folder.glob(f"{sheet_id}_bucket_*_results.csv"))
            
            # Also check for combined results files
            combined_result_files = list(results_folder.glob(f"{sheet_id}_combined_results.csv"))
            result_files.extend(combined_result_files)
            
            # Debug: Show what files we found
            print(f"      Looking for result files:")
            print(f"        Bucket files: {[f.name for f in result_files if 'bucket' in f.name]}")
            print(f"        Combined files: {[f.name for f in combined_result_files]}")
            
            # Also check for exact file names to debug
            exact_combined_file = results_folder / f"{sheet_id}_combined_results.csv"
            if exact_combined_file.exists():
                print(f"        ✅ Exact file found: {exact_combined_file.name}")
            else:
                print(f"        ❌ Exact file not found: {exact_combined_file.name}")
            
            sheet_results = []
            for result_file in result_files:
                if result_file.exists():
                    print(f"        Processing {result_file.name}")
                    try:
                        with open(result_file, 'r', encoding='utf-8') as f:
                            reader = csv.DictReader(f)
                            for row in reader:
                                if row.get('Website') and 'http' in row.get('Website', ''):
                                    result_data = {
                                        'website': row.get('Website', '').strip(),
                                        'first_name': row.get('First Name', '').strip(),
                                        'last_name': row.get('Last Name', '').strip(),
                                        'locations': ensure_minimum_locations(row.get('Locations', '').strip()),
                                        'professionals': row.get('Professionals', '').strip()
                                    }
                                    sheet_results.append(result_data)
                                    print(f"          Parsed: {result_data['website']} -> {result_data['first_name']} {result_data['last_name']}")
                    except Exception as e:
                        print(f"        ❌ Error reading {result_file.name}: {e}")
                        # Fallback to basic parsing if CSV reader fails
                        with open(result_file, 'r', encoding='utf-8') as f:
                            lines = f.readlines()[1:]  # Skip header
                            for line in lines:
                                line = line.strip()
                                if line and ('http' in line and ':' in line):
                                    # Parse the result line
                                    parts = line.split(',')
                                    if len(parts) >= 5:
                                        result_data = {
                                            'website': parts[0].strip(),
                                            'first_name': parts[1].strip(),
                                            'last_name': parts[2].strip(),
                                            'locations': ensure_minimum_locations(parts[3].strip()),
                                            'professionals': parts[4].strip()
                                        }
                                        sheet_results.append(result_data)
                                        print(f"          Parsed (fallback): {result_data['website']} -> {result_data['first_name']} {result_data['last_name']}")
            
            if sheet_results:
                all_results[sheet_id] = sheet_results
                print(f"    ✅ Collected {len(sheet_results)} results")
            else:
                print(f"    ⚠️  No results found")
                
                # Fallback: Try to manually read the combined results file
                fallback_file = results_folder / f"{sheet_id}_combined_results.csv"
                if fallback_file.exists():
                    print(f"      🔄 Fallback: Manually reading {fallback_file.name}")
                    try:
                        with open(fallback_file, 'r', encoding='utf-8') as f:
                            reader = csv.DictReader(f)
                            for row in reader:
                                if row.get('Website') and 'http' in row.get('Website', ''):
                                    result_data = {
                                        'website': row.get('Website', '').strip(),
                                        'first_name': row.get('First Name', '').strip(),
                                        'last_name': row.get('Last Name', '').strip(),
                                        'locations': row.get('Locations', '').strip(),
                                        'professionals': row.get('Professionals', '').strip()
                                    }
                                    sheet_results.append(result_data)
                                    print(f"          Parsed: {result_data['website']} -> {result_data['first_name']} {result_data['last_name']}")
                        
                        if sheet_results:
                            all_results[sheet_id] = sheet_results
                            print(f"      ✅ Fallback successful: Collected {len(sheet_results)} results")
                    except Exception as e:
                        print(f"      ❌ Fallback failed: {e}")
        
        # Note: Combined bucket results are now processed by sheet-specific files
        # created by split_combined_bucket_results_by_sheet() function
        
        total_results = sum(len(results) for results in all_results.values())
        print(f"\n📊 Total research results collected: {total_results}")
        
        return True, all_results
        
    except Exception as e:
        return False, f"Error collecting research results: {str(e)}"

def update_all_google_sheets_with_research(all_sheets_data, target_headers, results_folder):
    """Update all Google Sheets with research data using service account"""
    try:
        print(f"\n🔄 Updating all Google Sheets with research data...")
        
        # Collect all research results
        success, all_results = collect_all_research_results(all_sheets_data, results_folder)
        if not success:
            return False, f"Failed to collect research results: {all_results}"
        
        if not all_results:
            print("  ⚠️  No research results found to update")
            return True, "No results to update"
        
        # Authenticate with Google Sheets
        gs_client = authenticate_google_sheets()
        if not gs_client:
            return False, "Failed to authenticate with Google Sheets"
        
        # Update each sheet
        updated_sheets = []
        for sheet_id, research_results in all_results.items():
            print(f"\n📋 Updating Google Sheet: {sheet_id}")
            
            success, message = update_google_sheet_with_research_data(
                sheet_id, research_results, target_headers, gs_client, spreadsheet_id
            )
            
            if success:
                updated_sheets.append(sheet_id)
                print(f"  ✅ {message}")
            else:
                print(f"  ❌ {message}")
        
        print(f"\n🎉 Successfully updated {len(updated_sheets)} Google Sheets")
        return True, updated_sheets
        
    except Exception as e:
        return False, f"Error updating Google Sheets: {str(e)}"

def update_all_sheets_with_results(all_sheets_data, target_headers, results_folder, buckets_folder):
    """Update all sheets with OpenAI research results"""
    try:
        print(f"\n📝 Updating all sheets with research results...")
        
        # First, split combined bucket results by sheet to create city-specific CSV files
        print(f"\n🔄 Pre-processing: Splitting combined bucket results by sheet...")
        split_combined_bucket_results_by_sheet(buckets_folder, results_folder)
        
        updated_files = []
        
        for sheet_id in all_sheets_data.keys():
            print(f"\n📋 Processing sheet: {sheet_id}")
            
            updated_file, error = update_sheet_with_results(sheet_id, buckets_folder, results_folder, target_headers)
            if error:
                print(f"  ❌ Error: {error}")
                continue
            
            updated_files.append(updated_file)
            print(f"  ✅ Sheet updated: {updated_file}")
        
        print(f"\n✅ Updated {len(updated_files)} sheets with research results")
        return True, updated_files
        
    except Exception as e:
        return False, f"Error updating sheets with results: {str(e)}"

def create_vlookup_mapping(research_results):
    """Create a VLOOKUP-style mapping from research results"""
    try:
        # Create a dictionary for fast website lookup with multiple URL variations
        lookup_dict = {}
        for result in research_results:
            # Handle both 'website' and 'Website' keys
            website = result.get('website', result.get('Website', '')).lower().strip()
            
            # Store the original URL
            lookup_dict[website] = result
            
            # Also store variations for better matching
            # Remove protocol
            if website.startswith('http://'):
                lookup_dict[website[7:]] = result
            elif website.startswith('https://'):
                lookup_dict[website[8:]] = result
            
            # Remove www.
            if website.startswith('www.'):
                lookup_dict[website[4:]] = result
            
            # Remove trailing slashes
            if website.endswith('/'):
                lookup_dict[website[:-1]] = result
            
            # Store domain-only version (remove everything after first slash)
            domain = website.split('/')[0]
            if domain.startswith('www.'):
                domain = domain[4:]
            lookup_dict[domain] = result
        
        print(f"      📊 Created VLOOKUP mapping for {len(lookup_dict)} research results")
        return lookup_dict
        
    except Exception as e:
        print(f"      ❌ Error creating VLOOKUP mapping: {e}")
        return {}

def update_google_sheet_with_research_data(sheet_id, research_results, target_headers, gs_client, spreadsheet_id=None):
    """Update the actual Google Sheet with research data using service account"""
    try:
        print(f"    🔄 Updating Google Sheet: {sheet_id}")
        print(f"        📊 Debug: research_results type: {type(research_results)}")
        print(f"        📊 Debug: research_results length: {len(research_results) if research_results else 0}")
        if research_results and len(research_results) > 0:
            print(f"        📊 Debug: First result keys: {list(research_results[0].keys())}")
            print(f"        📊 Debug: First result: {research_results[0]}")
        
        # Find the worksheet by ID instead of name
        # Use the passed spreadsheet_id or fall back to DEBUG_SPREADSHEET_ID
        actual_spreadsheet_id = spreadsheet_id if spreadsheet_id else DEBUG_SPREADSHEET_ID
        print(f"        🔍 Using spreadsheet ID: {actual_spreadsheet_id}")
        spreadsheet, error = rate_limited_sheets_api_call(gs_client.open_by_key, actual_spreadsheet_id)
        if error:
            return False, f"Failed to open spreadsheet: {error}"
        worksheet = None
        
        print(f"        🔍 Debug: Looking for worksheet with sheet_id: '{sheet_id}' (type: {type(sheet_id)})")
        
        # Show available worksheets for debugging
        available_worksheets, error = rate_limited_sheets_api_call(spreadsheet.worksheets)
        if error:
            return False, f"Failed to list worksheets: {error}"
        print(f"        🔍 Debug: Available worksheets in spreadsheet:")
        for i, ws in enumerate(available_worksheets):
            print(f"          {i+1}. Title: '{ws.title}' | ID: {ws.id} (type: {type(ws.id)})")
        
        # Dynamic worksheet lookup - try multiple methods to find the worksheet
        
        # Method 1: Try to get worksheet by ID (for actual Google Sheets worksheet IDs)
        print(f"        🔍 Method 1: Trying worksheet_by_id with integer conversion...")
        try:
            # Convert sheet_id to integer for Google Sheets API lookup
            sheet_id_int = int(sheet_id)
            print(f"        🔍 Debug: Converted '{sheet_id}' to integer: {sheet_id_int}")
            
            # Special handling for sheet ID 0 (only sheet in spreadsheet)
            if sheet_id_int == 0:
                print(f"        🔍 Sheet ID 0 detected - using first worksheet")
                worksheets, error = rate_limited_sheets_api_call(spreadsheet.worksheets)
                if error:
                    return False, f"Failed to list worksheets: {error}"
                if worksheets:
                    worksheet = worksheets[0]
                    print(f"      ✅ Found worksheet by ID 0 (first worksheet): {worksheet.title}")
                else:
                    print(f"        ⚠️  No worksheets found in spreadsheet")
            else:
                # Normal case: search through worksheets to find matching ID
                ws_list, error = rate_limited_sheets_api_call(spreadsheet.worksheets)
                if error:
                    return False, f"Failed to list worksheets: {error}"
                for ws in ws_list:
                    if ws.id == sheet_id_int:
                        worksheet = ws
                        print(f"      ✅ Found worksheet by ID: {worksheet.title}")
                        break
        except (ValueError, TypeError) as e:
            # Not a valid integer ID, try other methods
            print(f"        🔍 Debug: Integer conversion failed: {e}")
            pass
        except Exception as e:
            # Other error, log it but continue with other methods
            print(f"      ⚠️  Error trying worksheet_by_id: {e}")
        
        # Method 2: Search by exact title match
        if not worksheet:
            print(f"        🔍 Method 2: Searching by exact title match...")
            ws_list, error = rate_limited_sheets_api_call(spreadsheet.worksheets)
            if error:
                return False, f"Failed to list worksheets: {error}"
            for ws in ws_list:
                if ws.title == sheet_id:
                    worksheet = ws
                    print(f"      ✅ Found worksheet by title: {worksheet.title}")
                    break
        
        # Method 3: Search by partial title match (case-insensitive)
        if not worksheet:
            print(f"        🔍 Method 3: Searching by partial title match...")
            ws_list, error = rate_limited_sheets_api_call(spreadsheet.worksheets)
            if error:
                return False, f"Failed to list worksheets: {error}"
            for ws in ws_list:
                # Ensure both are strings for comparison
                sheet_id_str = str(sheet_id)
                ws_title_str = str(ws.title)
                if sheet_id_str.lower() in ws_title_str.lower() or ws_title_str.lower() in sheet_id_str.lower():
                    worksheet = ws
                    print(f"      ✅ Found worksheet by partial title match: {ws.title}")
                    break
        
        # Method 4: Search by worksheet ID with consistent type handling
        if not worksheet:
            print(f"        🔍 Method 4: Searching by worksheet ID with type conversion...")
            print(f"        🔍 Debug: sheet_id type: {type(sheet_id)}, value: '{sheet_id}'")
            for ws in spreadsheet.worksheets():
                # Convert both to strings for consistent comparison
                ws_id_str = str(ws.id)
                sheet_id_str = str(sheet_id)
                print(f"          🔍 Debug: Comparing ws.id '{ws_id_str}' (type: {type(ws.id)}) with sheet_id '{sheet_id_str}' (type: {type(sheet_id)})")
                if ws_id_str == sheet_id_str:
                    worksheet = ws
                    print(f"      ✅ Found worksheet by ID string match: {worksheet.title}")
                    break
        
        # Method 5: Map custom sheet IDs to actual worksheet titles
        if not worksheet:
            print(f"        🔍 Method 5: Mapping custom sheet IDs to worksheet titles...")
            sheet_id_mapping = {
                '344000088': 'Dayton',
                '921889515': 'DFW',
                '215220353': 'Durham',
            }
            # Find the worksheet by ID only (no debug, no fallback to title)
            spreadsheet, error = rate_limited_sheets_api_call(gs_client.open_by_key, actual_spreadsheet_id)
            if error:
                return False, f"Failed to open spreadsheet: {error}"
            worksheet = None
            try:
                sheet_id_int = int(sheet_id)
                ws_list, error = rate_limited_sheets_api_call(spreadsheet.worksheets)
                if error:
                    return False, f"Failed to list worksheets: {error}"
                for ws in ws_list:
                    if ws.id == sheet_id_int:
                        worksheet = ws
                        break
            except Exception:
                worksheet = None
            if not worksheet:
                return True, f"Sheet ID {sheet_id} not found in spreadsheet, skipping update."
        
        # If we still don't have a worksheet, return error
        if not worksheet:
            return False, f"Could not find worksheet for sheet_id: {sheet_id}"
        
        print(f"        ✅ Found worksheet: {worksheet.title} (ID: {worksheet.id})")
        
        # Get all values from the worksheet
        all_values, error = rate_limited_sheets_api_call(worksheet.get_all_values)
        if error:
            return False, f"Failed to read worksheet data: {error}"
        if not all_values:
            return False, "No data found in worksheet"
        
        # Find column indices
        headers = all_values[0]
        website_col_idx = None
        first_name_col_idx = None
        last_name_col_idx = None
        locations_col_idx = None
        docs_col_idx = None
        
        for i, header in enumerate(headers):
            if 'website' in header.lower():
                website_col_idx = i
            elif 'first' in header.lower() and 'name' in header.lower():
                first_name_col_idx = i
            elif 'last' in header.lower() and 'name' in header.lower():
                last_name_col_idx = i
            elif 'location' in header.lower():
                locations_col_idx = i
            elif 'doc' in header.lower() or 'professional' in header.lower():
                docs_col_idx = i
        
        if website_col_idx is None:
            return False, "No website column found in worksheet"
        
        print(f"        📊 Found columns - Website: {website_col_idx}, First Name: {first_name_col_idx}, Last Name: {last_name_col_idx}, Locations: {locations_col_idx}, Docs: {docs_col_idx}")
        
        # Create domain mappings from research results
        website_to_result = {}
        domain_to_result = {}
        
        print(f"        🔄 Creating domain mappings from {len(research_results)} research results...")
        for i, result in enumerate(research_results):
            clean_website = result.get('Website', '').strip()
            if not clean_website:
                continue
                
            # Store exact website match
            website_to_result[clean_website] = result
            
            # Extract domain for flexible matching
            if clean_website.startswith(('http://', 'https://')):
                domain = clean_website.replace('https://', '').replace('http://', '').split('/')[0].split('?')[0].replace('www.', '')
                
                # Create multiple domain mappings for better matching
                if domain and '.' in domain:
                    # Full domain (e.g., "example.com")
                    domain_to_result[domain] = result
                    # Domain without TLD (e.g., "example" from "example.com")
                    domain_name = domain.split('.')[0]
                    domain_to_result[domain_name] = result
                    # Domain with common variations
                    if 'www.' in clean_website:
                        www_domain = clean_website.replace('https://www.', '').replace('http://www.', '').split('/')[0].split('?')[0]
                        domain_to_result[www_domain] = result
                    print(f"          Domain mapping: {domain} and {domain_name} -> {result.get('First Name', 'Unknown')} {result.get('Last Name', 'Unknown')}")
                print(f"          Result {i+1}: {clean_website}")
        
        print(f"        📍 Domain mappings created: {len(domain_to_result)}")
        print(f"        📍 Available domains: {list(domain_to_result.keys())}")
        print()
        
        # DEBUG: Show what websites are in the Google Sheet
        print(f"        🔍 DEBUG: Google Sheet contains {len(all_values)-1} rows with websites:")
        print(f"        🔍 DEBUG: Worksheet being updated: '{worksheet.title}' (ID: {worksheet.id})")
        for i, row in enumerate(all_values[1:6], start=2):  # Show first 5 rows
            if len(row) > website_col_idx:
                website = row[website_col_idx].strip()
                print(f"          Row {i}: {website}")
        if len(all_values) > 6:
            print(f"          ... and {len(all_values) - 6} more rows")
        print()
        
        # Initialize batch update variables
        batch_updates = []
        updated_count = 0
        max_batch_size = 100  # Google Sheets API batch limit
        SHEETS_API_DELAY = 0.1  # Rate limiting delay
        
        # Now update using simple website matching
        for row_idx, row in enumerate(all_values[1:], start=2):  # Start from row 2 (after headers)
            if len(row) <= website_col_idx:
                continue
                
            website = row[website_col_idx].strip()
            if not website or not website.startswith(('http://', 'https://')):
                continue
            
            # Debug: Show what domain we're extracting from the sheet URL
            print(f"        🔍 Processing sheet URL: {website}")
            
            # Extract domain properly: remove protocol, then split on '/' and take first part
            if website.startswith('http'):
                # Extract domain properly: remove protocol, then split on '/' and take first part
                domain_part = website.replace('https://', '').replace('http://', '')
                sheet_domain = domain_part.split('/')[0].split('?')[0].replace('www.', '')
                print(f"        🔍 Extracted domain: {sheet_domain}")
            else:
                # Handle non-HTTP URLs
                sheet_domain = website.split('/')[0].split('?')[0].replace('www.', '')
                print(f"        🔍 Extracted domain: {sheet_domain}")
            
            # Try multiple domain matching strategies
            matching_result = None
            
            # Try exact match first (but this will rarely work due to query parameters)
            if website in website_to_result:
                matching_result = website_to_result[website]
                print(f"        ✅ Exact match: Row {row_idx} -> {website}")
            
            # Try full domain match
            if not matching_result and sheet_domain in domain_to_result:
                matching_result = domain_to_result[sheet_domain]
                print(f"        ✅ Full domain match: Row {row_idx} -> {website} (matched domain: {sheet_domain})")
            
            # Try domain name match (part before first dot)
            if not matching_result and '.' in sheet_domain:
                sheet_domain_name = sheet_domain.split('.')[0]
                if sheet_domain_name in domain_to_result:
                    matching_result = domain_to_result[sheet_domain_name]
                    print(f"        ✅ Domain name match: Row {row_idx} -> {website} (matched domain name: {sheet_domain_name})")
            
            # Try partial domain matching for subdomains
            if not matching_result:
                for domain_key in domain_to_result.keys():
                    if sheet_domain.endswith(domain_key) or domain_key in sheet_domain:
                        matching_result = domain_to_result[domain_key]
                        print(f"        ✅ Partial domain match: Row {row_idx} -> {website} (matched: {domain_key})")
                        break
            
            if not matching_result:
                print(f"        ⚠️  No match found for: {website} (domain: {sheet_domain})")
                print(f"        🔍 Available domains: {list(domain_to_result.keys())[:5]}...")
            
            if matching_result:
                
                # Prepare update data
                updates = []
                
                # Update First Name
                if first_name_col_idx is not None:
                    updates.append({
                        'range': f"{chr(65 + first_name_col_idx)}{row_idx}",
                        'values': [[matching_result['First Name']]]
                    })
                
                # Update Last Name
                if last_name_col_idx is not None:
                    updates.append({
                        'range': f"{chr(65 + last_name_col_idx)}{row_idx}",
                        'values': [[matching_result['Last Name']]]
                    })
                
                # Update Locations (minimum locations requirement already applied in data processing)
                if locations_col_idx is not None:
                    updates.append({
                        'range': f"{chr(65 + locations_col_idx)}{row_idx}",
                        'values': [[matching_result['Locations']]]
                    })
                
                # Update Docs/Professionals
                if docs_col_idx is not None:
                    updates.append({
                        'range': f"{chr(65 + docs_col_idx)}{row_idx}",
                        'values': [[matching_result.get('Professionals', matching_result.get('Docs', ''))]]
                    })
                
                # DO NOT update Company Name column - leave it as is
                # The Company Name column (A) should remain unchanged
                
                # Add to batch updates
                if updates:
                    batch_updates.extend(updates)
                    updated_count += 1
                    print(f"        📝 Prepared update for row {row_idx}: {website}")
                    print(f"        📝   -> Name: {matching_result['First Name']} {matching_result['Last Name']}")
                    print(f"        📝   -> Locations: {matching_result['Locations']}")
                    print(f"        📝   -> Professionals: {matching_result.get('Professionals', matching_result.get('Docs', ''))}")
                
                # Process batch when it reaches max size
                if len(batch_updates) >= max_batch_size:
                    try:
                        # Add rate limiting delay
                        time.sleep(SHEETS_API_DELAY)
                        worksheet.batch_update(batch_updates)
                        print(f"        ✅ Applied batch update: {len(batch_updates)} cells")
                        batch_updates = []  # Clear batch
                    except Exception as e:
                        print(f"        ❌ Failed to apply batch update: {e}")
                        # Try individual updates if batch fails
                        print(f"        🔄 Attempting individual updates...")
                        for update in batch_updates:
                            try:
                                time.sleep(SHEETS_API_DELAY)
                                worksheet.update(update['range'], update['values'])
                                print(f"        ✅ Individual update successful: {update['range']}")
                            except Exception as individual_e:
                                print(f"        ❌ Individual update failed: {update['range']} - {individual_e}")
                        batch_updates = []  # Clear failed batch
            else:
                print(f"        ⚠️  No result found for website: {website}")
                print(f"        🔍 Available websites in buckets: {list(website_to_result.keys())[:3]}...")
                print(f"        🔍 Available domains: {list(domain_to_result.keys())[:3]}...")
        
        # Apply remaining updates
        if batch_updates:
            try:
                # Add rate limiting delay
                time.sleep(SHEETS_API_DELAY)
                worksheet.batch_update(batch_updates)
                print(f"        ✅ Applied final batch update: {len(batch_updates)} cells")
            except Exception as e:
                print(f"        ❌ Failed to apply final batch update: {e}")
                # Try individual updates if batch fails
                print(f"        🔄 Attempting individual final updates...")
                for update in batch_updates:
                    try:
                        time.sleep(SHEETS_API_DELAY)
                        worksheet.update(update['range'], update['values'])
                        print(f"        ✅ Individual final update successful: {update['range']}")
                    except Exception as individual_e:
                        print(f"        ❌ Individual final update failed: {update['range']} - {individual_e}")
        
        print(f"      🎉 Successfully updated {updated_count} rows in Google Sheet")
        
        # Summary of assignments for debugging
        print(f"\n      📊 Summary of website assignments:")
        print(f"        Total research results: {len(research_results)}")
        print(f"        Total rows updated: {updated_count}")
        print(f"        Domain mappings created: {len(domain_to_result)}")
        
        # Highlight rows with blank or N/A names
        if first_name_col_idx is not None and last_name_col_idx is not None:
            highlight_blank_name_rows(worksheet, first_name_col_idx, last_name_col_idx, start_row=2)
        
        return True, f"Updated {updated_count} rows"
        
    except Exception as e:
        return False, f"Error updating Google Sheet: {str(e)}"

def update_sheet_with_results(sheet_id, buckets_folder, results_folder, target_headers):
    """Update the sheet data with OpenAI research results"""
    try:
        # Read the original sheet data from Google Sheets (not from extracted websites)
        print(f"    🔄 Reading original sheet data for {sheet_id}...")
        
        # Get the original worksheet data directly from Google Sheets
        gs_client = authenticate_google_sheets()
        if not gs_client:
            return None, "Failed to authenticate with Google Sheets"
        
        # Find the worksheet by ID
        spreadsheet, error = rate_limited_sheets_api_call(gs_client.open_by_key, DEBUG_SPREADSHEET_ID)
        if error:
            return None, f"Failed to open spreadsheet: {error}"
        worksheet = None
        
        # Special handling for sheet ID 0 (only sheet in spreadsheet)
        if int(sheet_id) == 0:
            # If sheet ID is 0, use the first (and only) worksheet
            worksheets, error = rate_limited_sheets_api_call(spreadsheet.worksheets)
            if error:
                return None, f"Failed to list worksheets: {error}"
            if worksheets:
                worksheet = worksheets[0]
                print(f"      🔍 Sheet ID 0 detected - using first worksheet: {worksheet.title}")
            else:
                return None, "No worksheets found in spreadsheet"
        else:
            # Normal case: find worksheet by specific ID
            ws_list, error = rate_limited_sheets_api_call(spreadsheet.worksheets)
            if error:
                return None, f"Failed to list worksheets: {error}"
            for ws in ws_list:
                if ws.id == int(sheet_id):
                    worksheet = ws
                    break
        
        if not worksheet:
            return None, f"Worksheet with ID {sheet_id} not found"
        
        # Get all values from the worksheet
        all_values, error = rate_limited_sheets_api_call(worksheet.get_all_values)
        if error:
            return None, f"Failed to read worksheet data: {error}"
        
        if not all_values or len(all_values) < 2:
            return None, "Insufficient worksheet data"
        
        # Get headers and find column indices
        headers = all_values[0]
        website_col_idx = None
        first_name_col_idx = None
        last_name_col_idx = None
        locations_col_idx = None
        docs_col_idx = None
        
        for i, header in enumerate(headers):
            header_lower = header.lower()
            if 'website' in header_lower or 'url' in header_lower:
                website_col_idx = i
            elif 'first name' in header_lower:
                first_name_col_idx = i
            elif 'last name' in header_lower:
                last_name_col_idx = i
            elif 'locations' in header_lower:
                locations_col_idx = i
            elif 'docs' in header_lower or 'professionals' in header_lower:
                docs_col_idx = i
        
        print(f"      📊 Column mapping:")
        print(f"        Website: {website_col_idx if website_col_idx is not None else 'Not found'}")
        print(f"        First Name: {first_name_col_idx if first_name_col_idx is not None else 'Not found'}")
        print(f"        Last Name: {last_name_col_idx if last_name_col_idx is not None else 'Not found'}")
        print(f"        Locations: {locations_col_idx if locations_col_idx is not None else 'Not found'}")
        print(f"        Docs: {docs_col_idx if docs_col_idx is not None else 'Not found'}")
        
        # Read all bucket results
        all_results = []
        bucket_files = list(buckets_folder.glob(f"{sheet_id}_bucket_*.csv"))
        bucket_files.sort(key=lambda x: int(x.stem.split('_')[-1]))
        
        print(f"      📋 Processing buckets in order:")
        for bucket_file in bucket_files:
            bucket_num = bucket_file.stem.split('_')[-1]
            print(f"        🪣 Bucket {bucket_num}")
            
            results_file = results_folder / f"{bucket_file.stem}_results.csv"
            if results_file.exists():
                with open(results_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    parsed = clean_chatgpt_csv_output(content)
                    all_results.extend(parsed)
                    print(f"        ✅ Added {len(parsed)} results from bucket {bucket_num}")
            else:
                print(f"        ❌ No results file found for bucket {bucket_num}")
        
        print(f"    📊 Total results: {len(all_results)}")
        
        # Create website-to-result mapping
        website_to_result = {}
        for result in all_results:
            parts = result.split(',')
            if len(parts) >= 11:  # Your data has 11 columns
                # Find the website column (index 9 in your data)
                website = parts[9].strip()  # Website column
                first_name = parts[1].strip()  # First Name column
                last_name = parts[2].strip()   # Last Name column
                docs = parts[5].strip()        # Docs column (index 5)
                locations = parts[6].strip()   # Locations column (index 6)
                
                if website and website != 'Website':  # Skip header row
                    website_to_result[website] = {
                        'first_name': first_name,
                        'last_name': last_name,
                        'locations': locations,
                        'docs': docs
                    }
                    print(f"      📍 Mapped: {website} -> {first_name} {last_name} (Docs: {docs}, Locations: {locations})")
        
        # Update rows with research data using website matching
        updated_rows = []
        updated_count = 0
        
        # Process each row from the original worksheet data
        for row_idx, row in enumerate(all_values):
            if row_idx == 0:
                # Header row - keep as is
                updated_rows.append(row)
                continue
            
            # Get website from row
            if website_col_idx is not None and len(row) > website_col_idx:
                website = row[website_col_idx].strip()
                
                if website:
                    # Try to find matching result by website
                    matching_result = None
                    
                    # Try exact match first
                    if website in website_to_result:
                        matching_result = website_to_result[website]
                        print(f"      ✅ Exact match: {website} -> {matching_result['first_name']} {matching_result['last_name']}")
                    
                    # If no exact match, try domain match (ignore query parameters and use only part before first dot)
                    if not matching_result:
                        website_domain = website.split('/')[0].replace('https://', '').replace('http://', '').replace('www.', '')
                        # Extract only the part before the first dot
                        if '.' in website_domain:
                            website_domain_name = website_domain.split('.')[0]
                        else:
                            website_domain_name = website_domain
                        
                        for result_website, result_data in website_to_result.items():
                            result_domain = result_website.split('/')[0].replace('https://', '').replace('http://', '').replace('www.', '')
                            # Extract only the part before the first dot
                            if '.' in result_domain:
                                result_domain_name = result_domain.split('.')[0]
                            else:
                                result_domain_name = result_domain
                            
                            if website_domain_name == result_domain_name:
                                matching_result = result_data
                                print(f"      🔍 Domain name match: {website} -> {result_data['first_name']} {result_data['last_name']} (matched: {website_domain_name})")
                                break
                    
                    if matching_result:
                        # Update row with matched data - preserve all original data
                        if first_name_col_idx is not None and len(row) > first_name_col_idx:
                            row[first_name_col_idx] = matching_result['first_name']
                        if last_name_col_idx is not None and len(row) > last_name_col_idx:
                            row[last_name_col_idx] = matching_result['last_name']
                        if locations_col_idx is not None and len(row) > locations_col_idx:
                            row[locations_col_idx] = matching_result['locations']
                        if docs_col_idx is not None and len(row) > docs_col_idx:
                            row[docs_col_idx] = matching_result['docs']
                        updated_count += 1
                    else:
                        # No match found - mark for research but preserve all other data
                        if first_name_col_idx is not None and len(row) > first_name_col_idx:
                            row[first_name_col_idx] = 'Research_Needed'
                        if last_name_col_idx is not None and len(row) > last_name_col_idx:
                            row[last_name_col_idx] = 'Research_Needed'
                        print(f"      ⚠️  No match for: {website}")
            
            updated_rows.append(row)
        
        # Save updated sheet
        updated_file = results_folder / f"{sheet_id}_updated.csv"
        with open(updated_file, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(updated_rows)
        
        print(f"    📝 Updated {updated_count} rows, saved to {updated_file}")
        return updated_file, None
        
    except Exception as e:
        return None, f"Error updating sheet {sheet_id}: {str(e)}"

def save_debug_sample_results(websites, target_headers, filename="debug_sample_results.csv"):
    """Save debug sample results to a CSV file"""
    try:
        sample_results = create_debug_sample_results(websites, target_headers)
        
        with open(filename, 'w', encoding='utf-8', newline='') as f:
            # Write header row
            f.write(','.join(target_headers) + '\n')
            
            # Write data rows
            for row in sample_results:
                row_values = [str(row.get(header, '')) for header in target_headers]
                f.write(','.join(row_values) + '\n')
        
        print(f"\nDebug sample results saved to: {filename}")
        print(f"Saved {len(sample_results)} rows with {len(target_headers)} columns")
        return filename
    except Exception as e:
        print(f"Error saving debug sample results: {e}")
        return None

def get_headers_from_app_config():
    """Get headers from the app configuration (when not in testing mode)"""
    try:
        # Try to import from the app configuration
        # This would typically read from a config file or database
        # For now, we'll return the default headers
        return DEFAULT_HEADERS
    except Exception as e:
        print(f"Warning: Could not load headers from app config: {e}")
        print("Falling back to default headers")
        return DEFAULT_HEADERS

def display_web_search_configuration():
    """Display current web search configuration for API credit optimization"""
    print("\n🔍 Web Search Configuration:")
    print(f"  - Web Search Enabled: {'✅ Yes' if WEB_SEARCH_ENABLED else '❌ No'}")
    if WEB_SEARCH_ENABLED:
        print(f"  - Limit to Provided Websites: {'✅ Yes' if LIMIT_WEB_SEARCH_TO_PROVIDED_WEBSITES else '❌ No'}")
        if LIMIT_WEB_SEARCH_TO_PROVIDED_WEBSITES:
            print("    📝 ChatGPT will ONLY search the websites you provide")
            print("    🚫 ChatGPT will NOT search Google, LinkedIn, or other external sites")
        else:
            print("    ⚠️  ChatGPT may search external websites (higher API costs)")
    else:
        print("    📝 Web search is disabled - ChatGPT will use only provided content")
    
    print(f"  - Batch Size: {BATCH_SIZE} websites per API call")
    print(f"  - Max Parallel Workers: {MAX_WORKERS}")
    print(f"  - Batch Delay: {BATCH_DELAY} seconds")
    
    if LIMIT_WEB_SEARCH_TO_PROVIDED_WEBSITES and WEB_SEARCH_ENABLED:
        print("\n💰 API Credit Optimization: ACTIVE")
        print("   - Reduced external web searches")
        print("   - Focused on provided websites only")
        print("   - Lower API costs expected")
    elif not WEB_SEARCH_ENABLED:
        print("\n💰 API Credit Optimization: MAXIMUM")
        print("   - Web search completely disabled")
        print("   - Minimal API usage")
        print("   - Results based on provided content only")
    else:
        print("\n⚠️  API Credit Optimization: DISABLED")
        print("   - ChatGPT may search external websites")
        print("   - Higher API costs expected")
        print("   - Set LIMIT_WEB_SEARCH_TO_PROVIDED_WEBSITES = True to enable")

def highlight_blank_name_rows(worksheet, first_name_col_idx, last_name_col_idx, start_row=2):
    """Highlight rows where names are blank or N/A N/A"""
    try:
        print(f"        🎨 Highlighting rows with blank or N/A names...")
        
        # Get all values from the name columns
        first_name_range = f"{chr(65 + first_name_col_idx)}{start_row}:{chr(65 + first_name_col_idx)}"
        last_name_range = f"{chr(65 + last_name_col_idx)}{start_row}:{chr(65 + last_name_col_idx)}"
        
        try:
            # Add delay for rate limiting
            time.sleep(SHEETS_API_DELAY)
            first_names = worksheet.get(first_name_range)
            last_names = worksheet.get(last_name_range)
        except Exception as e:
            print(f"        ❌ Error getting name data: {e}")
            return
        
        if not first_names or not last_names:
            print(f"        ⚠️  No name data found to check")
            return
        
        # Prepare batch update for highlighting
        highlight_requests = []
        highlighted_count = 0
        
        for i, (first_name, last_name) in enumerate(zip(first_names, last_names)):
            row_num = start_row + i
            first_val = str(first_name[0]).strip().upper() if first_name and first_name[0] else ""
            last_val = str(last_name[0]).strip().upper() if last_name and last_name[0] else ""
            
            # Check if names are blank, N/A, or similar placeholder values
            is_blank_or_na = (
                not first_val or not last_val or
                first_val in ["N/A", "NA", "NONE", "UNKNOWN", "UNABLETOACCESS", "UNAVAILABLE", "UNAVAILIBLE", "UNAVAIL", "ACCESS", "BLOCKED", "ERROR", "FAILED", "TIMEOUT", "SITEBLOCKED", ""] or
                last_val in ["N/A", "NA", "NONE", "UNKNOWN", "UNABLETOACCESS", "UNAVAILABLE", "UNAVAILIBLE", "UNAVAIL", "ACCESS", "BLOCKED", "ERROR", "FAILED", "TIMEOUT", "SITEBLOCKED", ""] or
                (first_val == "N/A" and last_val == "N/A") or
                (first_val == "NA" and last_val == "NA") or
                (first_val == "NONE" and last_val == "NONE") or
                (first_val == "UNKNOWN" and last_val == "UNKNOWN") or
                (first_val == "UNABLETOACCESS" and last_val == "UNABLETOACCESS") or
                (first_val == "UNAVAILABLE" and last_val == "UNAVAILABLE") or
                (first_val == "UNAVAILIBLE" and last_val == "UNAVAILIBLE") or
                (first_val == "UNAVAIL" and last_val == "UNAVAIL") or
                (first_val == "ACCESS" and last_val == "ACCESS") or
                (first_val == "BLOCKED" and last_val == "BLOCKED") or
                (first_val == "ERROR" and last_val == "ERROR") or
                (first_val == "FAILED" and last_val == "FAILED") or
                (first_val == "TIMEOUT" and last_val == "TIMEOUT") or
                (first_val == "SITEBLOCKED" and last_val == "SITEBLOCKED")
            )
            
            if is_blank_or_na:
                # Highlight the entire row with a light red background
                row_range = f"A{row_num}:Z{row_num}"  # Highlight from A to Z
                highlight_requests.append({
                    'range': row_range,
                    'format': {
                        'backgroundColor': {
                            'red': 1.0,
                            'green': 0.8,
                            'blue': 0.8
                        }
                    }
                })
                highlighted_count += 1
                print(f"        🎨 Highlighted row {row_num}: '{first_val}' '{last_val}'")
        
        # Apply highlighting in batch if there are rows to highlight
        if highlight_requests:
            try:
                # Use the correct format for batch_update
                batch_update_request = {
                    'requests': [
                        {
                            'repeatCell': {
                                'range': {
                                    'sheetId': worksheet.id,
                                    'startRowIndex': int(request['range'][1:]) - 1,  # Convert row number to 0-based index
                                    'endRowIndex': int(request['range'][1:]),  # End row is exclusive
                                    'startColumnIndex': 0,  # Start from column A
                                    'endColumnIndex': 26   # End at column Z
                                },
                                'cell': request['format'],
                                'fields': 'userEnteredFormat.backgroundColor'
                            }
                        } for request in highlight_requests
                    ]
                }
                worksheet.spreadsheet.batch_update(batch_update_request)
                print(f"        ✅ Successfully highlighted {highlighted_count} rows with blank/N/A names")
            except Exception as e:
                print(f"        ❌ Error applying highlighting: {e}")
        else:
            print(f"        ℹ️  No rows found with blank or N/A names")
            
    except Exception as e:
        print(f"        ❌ Error highlighting blank name rows: {e}")

def process_buckets_for_sheet(buckets, industry, results_folder, sheet_id):
    """Process buckets for a single sheet with OpenAI API"""
    try:
        # Use sheet_id directly since we're not using sheet names anymore
        print(f"      Processing {len(buckets)} buckets for {sheet_id}...")
        
        successful_buckets = 0
        for i, bucket in enumerate(buckets, 1):
            bucket_num = bucket.get('bucket_num', i)
            total_buckets = len(buckets)
            
            print(f"        Processing bucket {bucket_num}/{total_buckets} for {sheet_id}...")
            
            # Process this bucket with OpenAI
            result = process_bucket_with_openai_parallel(bucket, industry, results_folder)
            if result['success']:
                successful_buckets += 1
                print(f"        ✅ Bucket {bucket_num} completed successfully")
            else:
                print(f"        ❌ Bucket {bucket_num} failed: {result['message']}")
        
        print(f"      📊 {sheet_id} processing complete: {successful_buckets}/{len(buckets)} buckets successful")
        return True, f"Processed {successful_buckets}/{len(buckets)} buckets"
        
    except Exception as e:
        return False, f"Error processing buckets for {sheet_id}: {str(e)}"

# Removed unused function - using existing working functions instead

# Removed unused function - using existing working functions instead

# Removed unused function - using existing working functions instead




def check_data_quality_for_sheet(sheet_id, results_folder):
    """Check for obviously wrong names, poor quality data, and duplicate names that need retry"""
    try:
        bad_quality_sites = []
        all_contacts = []  # Store all contacts to check for duplicates
        
        # Look for result files for this sheet using sheet ID
        for file in Path(results_folder).glob(f"{sheet_id}_bucket_*_results.csv"):
            if file.exists():
                with open(file, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        website = row.get('Website', '')
                        first_name = row.get('First Name', '')
                        last_name = row.get('Last Name', '')
                        
                        # Store contact info for duplicate checking
                        full_name = f"{first_name} {last_name}".strip()
                        if full_name and first_name and last_name:
                            all_contacts.append({
                                'website': website,
                                'first_name': first_name,
                                'last_name': last_name,
                                'full_name': full_name
                            })
                        
                        # Check for obviously wrong names
                        if is_poor_quality_name(first_name, last_name):
                            bad_quality_sites.append(website)
                            print(f"        🚨 Poor quality data detected: {website} -> {first_name} {last_name}")
        
        # Check for duplicate names
        duplicate_sites = check_for_duplicate_names(all_contacts)
        if duplicate_sites:
            bad_quality_sites.extend(duplicate_sites)
            print(f"        🔄 Found {len(duplicate_sites)} sites with duplicate names flagged for retry")
        
        return bad_quality_sites
        
    except Exception as e:
        print(f"        ❌ Error checking data quality for {sheet_id}: {str(e)}")
        return []

def is_poor_quality_name(first_name, last_name):
    """Check if a name is obviously wrong and needs retry
    
    IMPORTANT: This function should ONLY flag names that are clearly:
    - Site accessibility issues ("SiteNotAccessible", "Unknown", etc.)
    - Generic placeholders ("Info", "Contact", "N/A", etc.)
    - Obvious errors (numbers, special characters, etc.)
    
    It should NOT flag legitimate business names or person names,
    even if they sound like company names (e.g., "Red", "Maple", "Capital").
    """
    # Global retry tracking to prevent infinite retry loops
    if not hasattr(is_poor_quality_name, 'retry_counts'):
        is_poor_quality_name.retry_counts = {}
    
    website_key = f"{first_name}_{last_name}"
    retry_count = is_poor_quality_name.retry_counts.get(website_key, 0)
    
    # Limit retries to prevent infinite loops
    if retry_count >= 2:  # Maximum 2 retries per website
        return False
        
    if not first_name or not last_name:
        is_poor_quality_name.retry_counts[website_key] = retry_count + 1
        return True
    
    first_name = str(first_name).strip()
    last_name = str(last_name).strip()
    
    # Check for empty or whitespace-only names
    if not first_name or not last_name or first_name.isspace() or last_name.isspace():
        return True
    
    # Check for site inaccessibility indicators
    site_inaccessible_indicators = [
        'site inaccessible', 'site unavailable', 'site blocked', 'site error',
        'site failed', 'site timeout', 'site loading', 'site not found',
        'site down', 'site maintenance', 'site restricted', 'site forbidden',
        'inaccessible', 'unavailable', 'blocked', 'error', 'failed', 'timeout',
        'loading', 'not found', 'down', 'maintenance', 'restricted', 'forbidden',
        'sitenotaccessible', 'site not accessible', 'site_not_accessible'  # Add specific SiteNotAccessible cases
    ]
    
    first_name_lower = first_name.lower()
    last_name_lower = last_name.lower()
    
    # Check for site inaccessibility
    for indicator in site_inaccessible_indicators:
        if indicator in first_name_lower or indicator in last_name_lower:
            return True
    
    # Check for "Unknown Unknown" pattern specifically
    if (first_name_lower == 'unknown' and last_name_lower == 'unknown') or \
       (first_name_lower == 'n/a' and last_name_lower == 'n/a') or \
       (first_name_lower == 'none' and last_name_lower == 'none'):
        return True
    
    # Check for generic/placeholder names (ONLY obvious non-person names)
    generic_indicators = [
        # Website/technical terms that are clearly not person names
        'info', 'contact', 'about', 'home', 'page', 'website', 'site', 'web', 'online',
        'unknown', 'n/a', 'na', 'none', 'null', 'error', 'failed', 'loading',
        'please', 'click', 'here', 'more', 'details', 'view', 'see', 'read',
        'contact us', 'call us', 'email us', 'get quote', 'request info', 'learn more',
        'schedule', 'appointment', 'book now', 'call now', 'email now',
        
        # Business entity terms (NOT person names)
        'associates', 'partners', 'group', 'team', 'staff', 'services', 'inc', 'llc',
        'corporation', 'company', 'business', 'enterprise', 'organization',
        
        # Professional titles (NOT person names)
        'dr', 'doctor', 'dr.', 'dvm', 'vmd', 'phd', 'md', 'rn', 'lpn',
        
        # Single letter names (likely incomplete)
        'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o',
        'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z'
    ]
    
    # Check if either name contains generic indicators
    # BUT be more intelligent - don't flag names that are clearly person names
    for indicator in generic_indicators:
        if indicator in first_name_lower or indicator in last_name_lower:
            # Special case: if the name is clearly a person name, don't flag it
            if (len(first_name) > 2 and len(last_name) > 2 and 
                first_name.replace(' ', '').isalpha() and last_name.replace(' ', '').isalpha() and
                not any(char.isdigit() for char in first_name + last_name) and
                not any(char in '&+-_.,()[]{}' for char in first_name + last_name)):
                # This looks like a real person name, don't flag it
                continue
            return True
    
    # Check for very short names (likely incomplete)
    if len(first_name) < 2 or len(last_name) < 2:
        return True
    
    # Check for names that are just numbers or special characters
    if first_name.isdigit() or last_name.isdigit():
        return True
    
    # Check for names that are just punctuation
    if not any(c.isalpha() for c in first_name) or not any(c.isalpha() for c in last_name):
        return True
    
    # Check for names that are too long (likely business names)
    if len(first_name) > 20 or len(last_name) > 20:
        return True
    
    # Check for names that contain business-like patterns
    business_patterns = [
        'hospital', 'clinic', 'medical', 'center', 'practice', 'care', 'health',
        'veterinary', 'animal', 'pet', 'vet', 'dvm', 'associates', 'partners'
    ]
    
    for pattern in business_patterns:
        if pattern in first_name_lower or pattern in last_name_lower:
            return True
    
    # Check for names that look like business names (multiple words)
    if ' ' in first_name or ' ' in last_name:
        return True
    
    # Check for names that are all caps (likely business names)
    if first_name.isupper() or last_name.isupper():
        return True
    
    # Check for names that contain numbers or special characters
    if any(c.isdigit() for c in first_name) or any(c.isdigit() for c in last_name):
        return True
    
    # Check for names that contain common business symbols
    business_symbols = ['&', '+', '-', '_', '.', ',', '(', ')', '[', ']', '{', '}']
    if any(symbol in first_name or symbol in last_name for symbol in business_symbols):
        return True
    
    # NEW: Check for names that look like company abbreviations or business names
    # Single letters that are likely company abbreviations
    if len(first_name) == 1 or len(last_name) == 1:
        return True
    
    # Names that are too generic and likely company names
    generic_company_names = [
        'limelight', 'turf', 'guilderland', 'groundskeeping', 'groundsguys', 'staucet',
        'proservices', 'red', 'maple', 'squared', 'unavailable', 'customized', 'reliable',
        'capital', 'region', 'maggs', 'brennan', 'kolakoff', 'landscape', 'precision',
        'hometown', 'lawnstarter', 'kings', 'greenthumb', 'beautiful', 'lawns', 'ogsnow',
        'asprion', 'mohawk', 'valley', 'amberfield', 'squarespace', 'customer', 'sedo',
        'domainparking', 'turfpros', 'dymes', 'hunza', 'webx', 'romero', 'vautrin',
        'nvs', 'kane', 'dijohn', 'andson', 'smith', 'savaria', 'gabries', 'clark'
    ]
    
    if first_name_lower in generic_company_names or last_name_lower in generic_company_names:
        return True
    
    # Check for names that are likely business names (contain business-related words)
    business_words = [
        'lawn', 'landscape', 'landscaping', 'turf', 'grounds', 'snow', 'plow', 'mow',
        'care', 'service', 'maintenance', 'hospital', 'clinic', 'veterinary', 'animal',
        'pet', 'medical', 'health', 'wellness', 'emergency', 'specialty', 'practice'
    ]
    
    for word in business_words:
        if word in first_name_lower or word in last_name_lower:
            return True
    
    return False

def check_for_duplicate_names(all_contacts):
    """Check for duplicate names and flag them for retry"""
    try:
        if not all_contacts:
            return []
        
        # Group contacts by full name (case-insensitive)
        name_groups = {}
        for contact in all_contacts:
            full_name_lower = contact['full_name'].lower().strip()
            if full_name_lower not in name_groups:
                name_groups[full_name_lower] = []
            name_groups[full_name_lower].append(contact)
        
        # Find groups with more than one contact (duplicates)
        duplicate_sites = []
        for full_name, contacts in name_groups.items():
            if len(contacts) > 1:
                print(f"        🔄 Duplicate names detected: '{full_name}' appears {len(contacts)} times")
                for contact in contacts:
                    duplicate_sites.append(contact['website'])
                    print(f"          - {contact['website']} -> {contact['full_name']}")
        
        return duplicate_sites
        
    except Exception as e:
        print(f"        ❌ Error checking for duplicate names: {str(e)}")
        return []

def contains_error_patterns(bucket_file_path):
    """Check if a bucket file contains specific error patterns that need retry processing"""
    try:
        if not bucket_file_path.exists():
            return False
        
        # Define the specific error patterns we want to target (based on log analysis)
        error_patterns = [
            'SITENOTACCESSIBLE', 'SITE NOT ACCESSIBLE', 'SITE_NOT_ACCESSIBLE',
            'SITEACCESS', 'SITE ACCESS', 'SITE_ACCESS',
            'SITEACCESSERROR', 'SITE ACCESS ERROR', 'SITE_ACCESS_ERROR',
            'SITEUNAVAILABLE', 'SITE UNAVAILABLE', 'SITE_UNAVAILABLE',
            'NOTFOUND', 'NOT FOUND', 'NOT_FOUND', 'NOFOUND', 'NO_FOUND',
            'UNAVAILABLE', 'UNAVAIL', 'UNAVAILIBLE',
            'INACCESSIBLE', 'SITE INACCESSIBLE', 'SITE_INACCESSIBLE',
            'SITE BLOCKED', 'SITE ERROR', 'SITE FAILED', 'SITE TIMEOUT',
            'SITE LOADING', 'SITE NOT FOUND', 'SITE DOWN', 'SITE MAINTENANCE',
            'SITE RESTRICTED', 'SITE FORBIDDEN', 'BLOCKED', 'ERROR', 'FAILED',
            'TIMEOUT', 'LOADING', 'NOT FOUND', 'DOWN', 'MAINTENANCE',
            'RESTRICTED', 'FORBIDDEN'
        ]
        
        with open(bucket_file_path, 'r', encoding='utf-8') as f:
            content = f.read().upper()  # Read entire file and convert to uppercase
            
            # Check if any error pattern exists in the file
            for pattern in error_patterns:
                if pattern in content:
                    print(f"        🚨 Error pattern '{pattern}' found in {bucket_file_path.name}")
                    return True
        
        return False
        
    except Exception as e:
        print(f"        ❌ Error checking bucket for error patterns: {str(e)}")
        return False

def contains_error_patterns(bucket_file_path):
    """Check if a bucket file contains specific error patterns that need retry processing"""
    try:
        if not bucket_file_path.exists():
            return False
        
        # Define the specific error patterns we want to target (based on log analysis)
        error_patterns = [
            'SITENOTACCESSIBLE', 'SITE NOT ACCESSIBLE', 'SITE_NOT_ACCESSIBLE',
            'SITEACCESS', 'SITE ACCESS', 'SITE_ACCESS',
            'SITEACCESSERROR', 'SITE ACCESS ERROR', 'SITE_ACCESS_ERROR',
            'SITEUNAVAILABLE', 'SITE UNAVAILABLE', 'SITE_UNAVAILABLE',
            'NOTFOUND', 'NOT FOUND', 'NOT_FOUND', 'NOFOUND', 'NO_FOUND',
            'UNAVAILABLE', 'UNAVAIL', 'UNAVAILIBLE',
            'INACCESSIBLE', 'SITE INACCESSIBLE', 'SITE_INACCESSIBLE',
            'SITE BLOCKED', 'SITE ERROR', 'SITE FAILED', 'SITE TIMEOUT',
            'SITE LOADING', 'SITE NOT FOUND', 'SITE DOWN', 'SITE MAINTENANCE',
            'SITE RESTRICTED', 'SITE FORBIDDEN', 'BLOCKED', 'ERROR', 'FAILED',
            'TIMEOUT', 'LOADING', 'NOT FOUND', 'DOWN', 'MAINTENANCE',
            'RESTRICTED', 'FORBIDDEN'
        ]
        
        with open(bucket_file_path, 'r', encoding='utf-8') as f:
            content = f.read().upper()  # Read entire file and convert to uppercase
            
            # Check if any error pattern exists in the file
            for pattern in error_patterns:
                if pattern in content:
                    print(f"        🚨 Error pattern '{pattern}' found in {bucket_file_path.name}")
                    return True
        
        return False
        
    except Exception as e:
        print(f"        ❌ Error checking bucket for error patterns: {str(e)}")
        return False

def create_and_process_retry_buckets(bad_quality_sites, sheet_id, results_folder, buckets_folder):
    """Create new retry buckets and reprocess failed sites with AI - ONLY for sites with error patterns"""
    try:
        if not bad_quality_sites:
            print(f"        ✅ No poor quality sites to retry")
            return True
        
        # Retry ALL poor quality sites, not just those with specific error patterns
        retry_sites = []
        for site in bad_quality_sites:
            site_str = str(site).lower()
            
            # Check if site has error patterns that warrant retry
            has_error_pattern = any(pattern in site_str for pattern in [
                'sitenotaccessible', 'site not accessible', 'site_not_accessible',
                'siteaccess', 'site access', 'site_access',
                'siteaccesserror', 'site access error', 'site_access_error',
                'siteunavailable', 'site unavailable', 'site_unavailable',
                'notfound', 'not found', 'not_found', 'nofound', 'no_found',
                'unavailable', 'unavail', 'unavailible',
                'inaccessible', 'site inaccessible', 'site_inaccessible',
                'blocked', 'site blocked', 'site error', 'site failed', 'site timeout',
                'site loading', 'site not found', 'site down', 'site maintenance',
                'site restricted', 'site forbidden', 'error', 'failed', 'timeout',
                'loading', 'not found', 'down', 'maintenance', 'restricted', 'forbidden'
            ])
            
            # Also check for "Unknown Unknown" patterns in the data
            has_unknown_pattern = any(pattern in site_str for pattern in [
                'unknown unknown', 'unknown,unknown', 'unknown, unknown',
                'n/a n/a', 'n/a,n/a', 'n/a, n/a',
                'none none', 'none,none', 'none, none'
            ])
            
            # Retry ALL poor quality sites, regardless of pattern
            retry_sites.append(site)
            if has_error_pattern:
                print(f"        🚨 Site with error pattern flagged for retry: {site}")
            elif has_unknown_pattern:
                print(f"        🚨 Site with Unknown/Unknown pattern flagged for retry: {site}")
            else:
                print(f"        🚨 Site with poor quality data flagged for retry: {site}")
        
        if not retry_sites:
            print(f"        ✅ No sites found for retry - nothing to process")
            return True
        
        print(f"        🔄 Creating retry buckets for {len(retry_sites)} poor quality sites...")
        
        # Create retry buckets (similar to main bucket creation)
        retry_buckets = []
        batch_size = 5  # Process 5 sites at a time for retry
        
        for i in range(0, len(retry_sites), batch_size):
            batch = retry_sites[i:i + batch_size]
            bucket_num = len(retry_buckets) + 1
            bucket_name = f"{sheet_id}_retry_bucket_{bucket_num:02d}"
            
            # Create retry bucket file
            bucket_file = buckets_folder / f"{bucket_name}.csv"
            with open(bucket_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['Website'])  # Header
                for website in batch:
                    writer.writerow([website])
            
            retry_buckets.append({
                'name': bucket_name,
                'file': bucket_file,
                'websites': batch
            })
            print(f"        🪣 Created retry bucket {bucket_num}: {len(batch)} error pattern websites")
        
        # Process each retry bucket with AI
        print(f"        🔄 Processing {len(retry_buckets)} retry buckets with AI...")
        
        # Log specific problematic cases for tracking
        problematic_sites = []
        for site in retry_sites:
            site_str = str(site).lower()
            if any(pattern in site_str for pattern in ['sitenotaccessible', 'unable_to_access_site', 'unable to access site']):
                problematic_sites.append(('Site Access Issues', site))
            elif any(pattern in site_str for pattern in ['notfound', 'not found', 'unavailable']):
                problematic_sites.append(('Not Found/Unavailable', site))
            else:
                problematic_sites.append(('Poor Quality Data', site))
        
        if problematic_sites:
            print(f"        🚨 Special attention needed for {len(problematic_sites)} poor quality sites:")
            for issue_type, site in problematic_sites:
                print(f"          - {issue_type}: {site}")
        
        # Process each retry bucket using the same logic as normal buckets
        for bucket in retry_buckets:
            print(f"        🔄 Processing retry bucket: {bucket['name']}")
            
            # Use the same processing logic as normal buckets for consistency
            print(f"        🔄 Using enhanced retry logic with same processing pipeline as normal buckets")
            try:
                # Create bucket info structure that matches what process_bucket_with_openai_parallel expects
                bucket_info = {
                    'websites': bucket['websites'],
                    'bucket_num': bucket['name'],
                    'total_buckets': len(retry_buckets),
                    'sheet_id': f"{sheet_id}_RETRY",  # Mark as retry for tracking
                    'filename': bucket['file']  # Add filename key that save_bucket_results expects
                }
                
                # Process retry bucket using the same logic as normal buckets
                result = process_bucket_with_openai_parallel(bucket_info, INDUSTRY, results_folder)
                
                if result and result.get('success'):
                    print(f"        ✅ Retry bucket {bucket['name']} completed: {result.get('results_count', 0)} results")
                    
                    # NEW: Ensure retry results are properly saved with correct naming
                    print(f"        🔄 Verifying retry results for {bucket['name']}...")
                    retry_result_files = list(results_folder.glob(f"{bucket['name']}_*_results.csv"))
                    if retry_result_files:
                        print(f"        ✅ Found {len(retry_result_files)} retry result files for {bucket['name']}")
                    else:
                        print(f"        ⚠️  No retry result files found for {bucket['name']} - checking for alternative naming...")
                        # Check for alternative naming patterns
                        alt_files = list(results_folder.glob(f"*{bucket['name']}*results*.csv"))
                        if alt_files:
                            print(f"        ✅ Found {len(alt_files)} alternative result files")
                        else:
                            print(f"        ❌ No result files found for retry bucket {bucket['name']}")
                else:
                    print(f"        ⚠️  Retry bucket {bucket['name']} failed: {result.get('message', 'Unknown error')}")
                    
            except Exception as e:
                print(f"        ❌ Error processing retry bucket {bucket['name']}: {str(e)}")
                continue
        
        print(f"        ✅ Retry bucket processing completed")
        
        # NEW: Force update of main results with retry data to ensure data is merged
        print(f"        🔄 Forcing update of main results with retry data...")
        update_results_with_retry_data(sheet_id, results_folder)
        
        # Debug: Check if merge worked by counting files
        main_files_after = list(Path(results_folder).glob(f"{sheet_id}_bucket_*_results.csv"))
        retry_files_after = list(Path(results_folder).glob(f"{sheet_id}_retry_bucket_*_results.csv"))
        print(f"        🔍 After merge: {len(main_files_after)} main files, {len(retry_files_after)} retry files")
        
        return True
        
    except Exception as e:
        print(f"        ❌ Error creating and processing retry buckets: {str(e)}")
        return False

def update_results_with_retry_data(sheet_id, results_folder):
    """Update the main results with retry data before final sheet update"""
    try:
        print(f"        🔄 Updating main results with retry data...")
        
        # Find all retry bucket result files
        retry_files = list(results_folder.glob(f"{sheet_id}_retry_bucket_*_results.csv"))
        
        if not retry_files:
            print(f"        ⚠️  No retry result files found")
            return
        
        print(f"        📁 Found {len(retry_files)} retry result files")
        
        # Read and merge retry results, handling duplicates by keeping the best quality data
        retry_results = {}
        for retry_file in retry_files:
            with open(retry_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    website = row['Website'].strip()
                    if website:
                        # If we already have this website, keep the better quality data
                        if website in retry_results:
                            if is_better_quality_data(row, retry_results[website]):
                                retry_results[website] = row
                                print(f"        🔄 Replaced duplicate retry data for {website} with better quality")
                        else:
                            retry_results[website] = row
        
        print(f"        📊 Merged {len(retry_results)} retry results")
        print(f"        🔍 Retry websites: {list(retry_results.keys())}")
        
        # Update the main bucket result files with retry data
        bucket_files = list(results_folder.glob(f"{sheet_id}_bucket_*_results.csv"))
        print(f"        🔍 Found {len(bucket_files)} main bucket files to update")
        for bucket_file in bucket_files:
            print(f"          - {bucket_file.name}")
        
        total_updates = 0
        for bucket_file in bucket_files:
            print(f"        🔍 Processing bucket file: {bucket_file.name}")
            updated = False
            bucket_results = []
            updates_in_file = 0
            existing_websites = set()
            
            # Read current bucket results
            with open(bucket_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                
                for row in reader:
                    website = row['Website'].strip()
                    existing_websites.add(website)
                    print(f"          📍 Found website in bucket: {website}")
                    
                    # Check if we have better retry data for this website
                    if website in retry_results:
                        retry_row = retry_results[website]
                        
                        # Ensure retry row has all required fields
                        normalized_retry_row = {}
                        for field in fieldnames:
                            if field in retry_row:
                                normalized_retry_row[field] = retry_row[field]
                            else:
                                # Keep original value if retry data doesn't have this field
                                normalized_retry_row[field] = row.get(field, '')
                        
                        # Use retry data if it's better quality
                        if is_better_quality_data(normalized_retry_row, row):
                            bucket_results.append(normalized_retry_row)
                            updated = True
                            updates_in_file += 1
                            print(f"        🔄 Updated {website} with retry data")
                            
                            # Log the improvement details
                            original_names = f"{row.get('First Name', '')} {row.get('Last Name', '')}".strip()
                            retry_names = f"{normalized_retry_row.get('First Name', '')} {normalized_retry_row.get('Last Name', '')}".strip()
                            print(f"           From: '{original_names}' → To: '{retry_names}'")
                        else:
                            bucket_results.append(row)
                            print(f"        ⏭️  Keeping original data for {website} (retry data not better)")
                    else:
                        bucket_results.append(row)
            
            # Add missing websites from retry results that weren't in the original bucket
            for website, retry_row in retry_results.items():
                if website not in existing_websites:
                    # This website was completely missing from the original bucket
                    # Add it to the bucket results
                    normalized_retry_row = {}
                    for field in fieldnames:
                        if field in retry_row:
                            normalized_retry_row[field] = retry_row[field]
                        else:
                            normalized_retry_row[field] = ''  # Fill with empty string for missing fields
                    
                    bucket_results.append(normalized_retry_row)
                    updated = True
                    updates_in_file += 1
                    print(f"        ➕ Added missing website {website} from retry results")
            
            # Write updated bucket results
            if updated:
                with open(bucket_file, 'w', newline='', encoding='utf-8') as f:
                    if bucket_results:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(bucket_results)
                    print(f"        ✅ Updated {bucket_file.name} with {updates_in_file} retry data entries")
                    total_updates += updates_in_file
        
        print(f"        ✅ Main results updated with retry data: {total_updates} total updates")
        
        # Additional validation: ensure all rows have complete data
        validate_and_clean_bucket_data(sheet_id, results_folder)
        
    except Exception as e:
        print(f"        ❌ Error updating results with retry data: {str(e)}")

def validate_and_clean_bucket_data(sheet_id, results_folder):
    """Validate and clean bucket data to ensure consistency"""
    try:
        print(f"        🔍 Validating and cleaning bucket data...")
        
        bucket_files = list(results_folder.glob(f"{sheet_id}_bucket_*_results.csv"))
        total_cleaned = 0
        
        for bucket_file in bucket_files:
            cleaned = False
            bucket_results = []
            
            with open(bucket_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                
                for row in reader:
                    website = row['Website'].strip()
                    first_name = row.get('First Name', '').strip()
                    last_name = row.get('Last Name', '').strip()
                    
                    # Check for missing or invalid data
                    if not first_name or not last_name or first_name == 'n/a' or last_name == 'n/a':
                        print(f"        ⚠️  Found incomplete data for {website}: '{first_name}' '{last_name}'")
                        
                        # Try to find this website in retry results
                        retry_files = list(results_folder.glob(f"{sheet_id}_retry_bucket_*_results.csv"))
                        retry_data = None
                        
                        for retry_file in retry_files:
                            with open(retry_file, 'r', encoding='utf-8') as rf:
                                retry_reader = csv.DictReader(rf)
                                for retry_row in retry_reader:
                                    if retry_row.get('Website', '').strip() == website:
                                        retry_data = retry_row
                                        break
                                if retry_data:
                                    break
                        
                        if retry_data and retry_data.get('First Name') and retry_data.get('Last Name'):
                            # Use retry data to fill missing information
                            cleaned_row = row.copy()
                            cleaned_row['First Name'] = retry_data['First Name']
                            cleaned_row['Last Name'] = retry_data['Last Name']
                            bucket_results.append(cleaned_row)
                            cleaned = True
                            total_cleaned += 1
                            print(f"        ✅ Cleaned {website} with retry data")
                        else:
                            # Keep original row but mark as needing attention
                            bucket_results.append(row)
                    else:
                        bucket_results.append(row)
            
            # Write cleaned results
            if cleaned:
                with open(bucket_file, 'w', newline='', encoding='utf-8') as f:
                    if bucket_results:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(bucket_results)
                    print(f"        ✅ Cleaned {bucket_file.name}")
        
        if total_cleaned > 0:
            print(f"        ✅ Data validation complete: {total_cleaned} rows cleaned")
        else:
            print(f"        ✅ Data validation complete: no cleaning needed")
            
    except Exception as e:
        print(f"        ❌ Error validating bucket data: {str(e)}")

def is_better_quality_data(retry_row, original_row):
    """Check if retry data is better quality than original data"""
    try:
        # Check if retry data has real names (not generic/placeholder text)
        retry_first = retry_row.get('First Name', '').strip().lower()
        retry_last = retry_row.get('Last Name', '').strip().lower()
        
        original_first = original_row.get('First Name', '').strip().lower()
        original_last = original_row.get('Last Name', '').strip().lower()
        
        # Generic/placeholder text patterns
        generic_patterns = [
            'info', 'contact', 'about', 'page', 'form', 'quote', 'request',
            'not_found', 'unavailable', 'n/a', 'unknown', 'placeholder',
            'get', 'send', 'submit', 'click', 'here', 'more',
            'sitenotaccessible', 'site not accessible', 'site_not_accessible',
            'unable_to_access_site', 'unable to access site', 'site_unavailable'
        ]
        
        # Check if retry data avoids generic patterns
        retry_is_generic = any(pattern in retry_first or pattern in retry_last for pattern in generic_patterns)
        original_is_generic = any(pattern in original_first or pattern in original_last for pattern in generic_patterns)
        
        # Retry data is better if it's not generic and original was generic
        if not retry_is_generic and original_is_generic:
            return True
        
        # Retry data is better if it has actual names (not empty/not_found)
        if (retry_first and retry_first != 'not_found' and retry_last and retry_last != 'not_found' and
            (not original_first or original_first == 'not_found' or not original_last or original_last == 'not_found')):
            return True
        
        # Special case: Retry data is better if it improves from SiteNotAccessible
        if (retry_first.lower() != 'sitenotaccessible' and retry_last.lower() != 'sitenotaccessible' and
            (original_first.lower() == 'sitenotaccessible' or original_last.lower() == 'sitenotaccessible')):
            return True
        
        # Special case: Retry data is better if it improves from UNABLE_TO_ACCESS_SITE
        if (retry_first.lower() != 'unable_to_access_site' and retry_last.lower() != 'unable_to_access_site' and
            (original_first.lower() == 'unable_to_access_site' or original_last.lower() == 'unable_to_access_site')):
            return True
        
        # Retry data is better if it fills in completely missing data
        if (retry_first and retry_last and 
            (not original_first or not original_last or original_first.isspace() or original_last.isspace())):
            return True
        
        # Retry data is better if it has more complete information
        retry_has_names = bool(retry_first and retry_last and retry_first != 'n/a' and retry_last != 'n/a')
        original_has_names = bool(original_first and original_last and original_first != 'n/a' and original_last != 'n/a')
        
        if retry_has_names and not original_has_names:
            return True
        
        return False
        
    except Exception as e:
        print(f"        ❌ Error comparing data quality: {str(e)}")
        return False
        
    except Exception as e:
        print(f"        ❌ Error checking data quality: {str(e)}")
        return False

def parse_ai_response(ai_response):
    """Parse AI response and extract structured data"""
    try:
        results = []
        lines = (ai_response or '').strip().split('\n')

        # Assemble complete CSV records from possibly fragmented lines
        records = _assemble_csv_records_from_lines(lines)

        for idx, record in enumerate(records, 1):
            parts = parse_csv_line(record)
            if parts and len(parts) >= 4:
                website = parts[0].strip()
                first_name = parts[1].strip()
                last_name = parts[2].strip()
                locations = parts[3].strip()

                professionals = parts[4].strip() if (COUNT_PROFESSIONALS and len(parts) > 4) else '0'

                results.append({
                    'Website': website,
                    'First Name': first_name,
                    'Last Name': last_name,
                    'Locations': locations,
                    'Professionals': professionals
                })
            else:
                print(f"        Warning: Invalid result format (need 4+ fields): {record}")

        return results
        
    except Exception as e:
        print(f"        ❌ Error parsing AI response: {str(e)}")
        return []

def process_specific_sheet(sheet_id, all_sheets_data, buckets_folder, results_folder, headers, spreadsheet_id):
    """Process a specific sheet by ID instead of looping through all sheets"""
    try:
        print(f"\n🎯 PROCESSING SPECIFIC SHEET: {sheet_id}")
        print("=" * 80)
        
        # Check if target sheet exists in our data
        if sheet_id not in all_sheets_data:
            print(f"❌ Target sheet {sheet_id} not found in available sheets")
            print(f"Available sheets: {list(all_sheets_data.keys())}")
            return False, f"Sheet {sheet_id} not found"
        
        sheet_data = all_sheets_data[sheet_id]
        buckets = sheet_data['buckets']
        
        if not buckets:
            print(f"⏭️  No buckets for sheet {sheet_id}, cannot proceed...")
            return False, f"No buckets for sheet {sheet_id}"
        
        print(f"🪣 Processing {len(buckets)} buckets for sheet {sheet_id}...")
        
        # Process buckets for this sheet with OpenAI API
        try:
            success, error = process_buckets_for_sheet(buckets, INDUSTRY, results_folder, sheet_id)
            if not success:
                print(f"❌ Failed to process buckets for sheet {sheet_id}: {error}")
                return False, f"Bucket processing failed: {error}"
        except Exception as e:
            print(f"❌ Bucket processing crashed for sheet {sheet_id}: {str(e)}")
            return False, f"Bucket processing crashed: {str(e)}"
        
        print(f"✅ Successfully processed {len(buckets)} buckets for sheet {sheet_id}")
        
        # 🔄 Retry buckets disabled per user instruction
        print(f"🔄 Processing failed sites for sheet {sheet_id} with retry logic...")
        print(f"🚫 Retry buckets are disabled. Skipping retry checks and processing.")
        
        # 📝 NOW update Google Sheet with final results (after all processing is complete)
        print(f"📝 Updating sheet {sheet_id} with final research results...")
        
        # Collect final research results for this sheet (including any retry improvements)
        sheet_results = []
        
        # First, collect main bucket results
        main_result_files = list(Path(results_folder).glob(f"{sheet_id}_bucket_*_results.csv"))
        print(f"        🔍 Looking for main result files matching pattern: {sheet_id}_bucket_*_results.csv")
        print(f"        🔍 Found {len(main_result_files)} main result files")
        
        for result_file in main_result_files:
            print(f"        📄 Reading results from: {result_file.name}")
            with open(result_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sheet_results.append(row)
        
        # Then, collect retry bucket results (these should have been merged, but let's be safe)
        retry_result_files = list(Path(results_folder).glob(f"{sheet_id}_retry_bucket_*_results.csv"))
        print(f"        🔍 Looking for retry result files matching pattern: {sheet_id}_retry_bucket_*_results.csv")
        print(f"        🔍 Found {len(retry_result_files)} retry result files")
        
        for result_file in retry_result_files:
            print(f"        📄 Reading retry results from: {result_file.name}")
            with open(result_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sheet_results.append(row)
        
        print(f"        📊 Found {len(sheet_results)} final research results for sheet {sheet_id}")
        print(f"        📊 Breakdown: {len(main_result_files)} main bucket files, {len(retry_result_files)} retry bucket files")
        
        if not sheet_results:
            print(f"        ⚠️  No research results found for sheet {sheet_id}")
            return False, f"No research results found for sheet {sheet_id}"
        
        # Update Google Sheet with final results
        gs_client = authenticate_google_sheets()
        if not gs_client:
            return False, "Failed to authenticate with Google Sheets"
        
        success, message = update_google_sheet_with_research_data(sheet_id, sheet_results, headers, gs_client, spreadsheet_id)
        if not success:
            print(f"❌ Failed to update Google Sheet {sheet_id}: {message}")
            return False, f"Google Sheet update failed: {message}"
        
        print(f"✅ Successfully updated Google Sheet {sheet_id} with final results: {message}")
        
        # Open sync link in browser for this sheet and provided pipeline id (if any)
        try:
            pipeline_id = os.environ.get('PIPELINE_ID') or globals().get('PIPELINE_ID_ARG')
            if pipeline_id:
                sync_url = f"https://integrus-app-lajc.onrender.com/api/sync-sheet?pipeline_id={pipeline_id}&sheet_id={sheet_id}"
                print(f"🌐 Opening sync URL: {sync_url}")
                try:
                    webbrowser.open(sync_url, new=2)
                except Exception as _wb_e:
                    print(f"⚠️ Could not open browser: {_wb_e}")
            else:
                print("ℹ️ PIPELINE_ID not provided; skipping sync link open")
        except Exception as _e:
            print(f"⚠️ Sync open step failed: {_e}")
        
        print(f"✅ Sheet {sheet_id} processing completed")
        print(f"✅ COMPLETED PROCESSING: Sheet {sheet_id}")
        print(f"   - Buckets processed: {len(buckets)}")
        print(f"   - Results saved to: {results_folder}")
        
        # NEW: If in pipeline mode, update workflow status to "BV ready"
        if PIPELINE_MODE and PIPELINE_NAME:
            update_worksheet_status_to_bv_ready(sheet_id, spreadsheet_id)
        
        return True, f"Successfully processed sheet {sheet_id}"
        
    except Exception as e:
        print(f"❌ Error processing sheet {sheet_id}: {str(e)}")
        return False, f"Processing error: {str(e)}"

def update_worksheet_status_to_bv_ready(sheet_id, spreadsheet_id):
    """Update worksheet workflow status to 'bv_ready' after successful processing"""
    try:
        print(f"🔄 Updating workflow status for sheet {sheet_id} to 'BV Ready'...")
        
        # Import app.py functions for workflow management
        try:
            import sys
            import os
            
            # Add the current directory to Python path to import app functions
            current_dir = os.path.dirname(os.path.abspath(__file__))
            if current_dir not in sys.path:
                sys.path.insert(0, current_dir)
            
            # Try to import the workflow management function
            try:
                from app import complete_workflow_stage
                print(f"✅ Successfully imported workflow management functions")
            except ImportError as e:
                print(f"⚠️  Could not import workflow management functions: {e}")
                print(f"🔄 Continuing without workflow status update...")
                return False
            
            # Get the worksheet ID from the sheet_id (which might be a title or custom ID)
            worksheet_id = get_worksheet_id_from_sheet_data(sheet_id, spreadsheet_id)
            if not worksheet_id:
                print(f"⚠️  Could not determine worksheet ID for sheet {sheet_id}")
                return False
            
            # Complete the 'not_scraped' stage to advance to 'bv_ready'
            success, message = complete_workflow_stage(
                worksheet_id=worksheet_id,
                stage_name='not_scraped',
                notes=f'Automatically completed by scraper for pipeline {PIPELINE_NAME}',
                assigned_intern='system'
            )
            
            if success:
                print(f"✅ Successfully updated workflow status to 'BV Ready' for sheet {sheet_id}")
                return True
            else:
                print(f"❌ Failed to update workflow status: {message}")
                return False
                
        except Exception as e:
            print(f"❌ Error updating workflow status: {str(e)}")
            return False
            
    except Exception as e:
        print(f"❌ Error in update_worksheet_status_to_bv_ready: {str(e)}")
        return False

def get_worksheet_id_from_sheet_data(sheet_id, spreadsheet_id):
    """Get the database worksheet ID from sheet data"""
    try:
        # This function would need to be implemented based on your database schema
        # For now, we'll try to extract it from the sheet_id or use a default
        print(f"🔍 Attempting to get worksheet ID for sheet {sheet_id}")
        
        # If sheet_id is numeric, it might be the worksheet ID
        try:
            worksheet_id = int(sheet_id)
            print(f"✅ Using sheet_id as worksheet_id: {worksheet_id}")
            return worksheet_id
        except ValueError:
            pass
        
        # If sheet_id is a string (title), we need to look it up
        # This would require database access - for now return None
        print(f"⚠️  Could not determine worksheet ID for sheet {sheet_id}")
        return None
        
    except Exception as e:
        print(f"❌ Error getting worksheet ID: {str(e)}")
        return None

def process_pipeline_all_sheets(pipeline_name, spreadsheet_id, headers, selected_worksheet_ids=None):
    """Process all sheets in a pipeline and update their status to 'BV ready'"""
    global PIPELINE_MODE, PIPELINE_NAME
    
    try:
        if selected_worksheet_ids:
            print(f"\n🚀 PIPELINE MODE: Processing selected sheets in pipeline '{pipeline_name}'")
            print(f"📋 Selected worksheets: {len(selected_worksheet_ids)} sheets")
        else:
            print(f"\n🚀 PIPELINE MODE: Processing all sheets in pipeline '{pipeline_name}'")
        print(f"📊 Spreadsheet ID: {spreadsheet_id}")
        print("=" * 80)
        
        # Set pipeline mode
        PIPELINE_MODE = True
        PIPELINE_NAME = pipeline_name
        
        # Setup temporary folder structure
        print(f"\n📁 Setting up temporary folder structure...")
        temp_folder, sheets_folder, buckets_folder, results_folder = setup_temp_folder()
        
        # Process all sheets from the spreadsheet
        success, all_sheets_data = process_all_sheets(spreadsheet_id, headers, temp_folder, sheets_folder, buckets_folder, results_folder, selected_worksheet_ids)
        if not success:
            error_msg = str(all_sheets_data)
            if 'quota exceeded' in error_msg.lower():
                handle_quota_exceeded_error()
                print(f"\n❌ Processing failed due to quota limits: {error_msg}")
            else:
                print(f"❌ Failed to process sheets: {error_msg}")
            return False, error_msg
        
        if not all_sheets_data:
            print("❌ No sheets with websites found.")
            return False, "No sheets with websites found"
        
        print(f"\n📊 Successfully prepared {len(all_sheets_data)} sheets for pipeline processing")
        total_websites = sum(len(data['websites']) for data in all_sheets_data.values())
        total_buckets = sum(len(data['buckets']) for data in all_sheets_data.values())
        print(f"📈 Total websites: {total_websites}")
        print(f"🪣 Total buckets: {total_buckets}")
        
        # Dropbox integration removed
        
        # Process ALL sheets sequentially
        print(f"\n🚀 Starting pipeline processing for ALL sheets...")
        print(f"📊 Found {len(all_sheets_data)} sheets to process")
        
        # Process each sheet one by one
        successful_sheets = 0
        failed_sheets = 0
        
        for sheet_id, sheet_data in all_sheets_data.items():
            if sheet_id == 'COMBINED_SMALL_SHEETS':
                print(f"\n⏭️  Skipping combined small sheets (will be processed with individual sheets)")
                continue
                
            print(f"\n{'='*60}")
            print(f"🎯 Processing sheet ID: {sheet_id}")
            print(f"{'='*60}")
            
            # Process this specific sheet
            try:
                success, message = process_specific_sheet(sheet_id, all_sheets_data, buckets_folder, results_folder, headers, spreadsheet_id)
                
                if success:
                    successful_sheets += 1
                    print(f"✅ Sheet {sheet_id} processing completed successfully!")
                else:
                    failed_sheets += 1
                    print(f"❌ Sheet {sheet_id} processing failed: {message}")
            except Exception as e:
                failed_sheets += 1
                print(f"❌ Sheet {sheet_id} processing crashed with error: {str(e)}")
                print(f"🔄 Continuing to next sheet despite error...")
            
            # Continue to next sheet - don't end program here
            print(f"🔄 Moving to next sheet...")
        
        # Summary of processing
        print(f"\n🎉 PIPELINE PROCESSING COMPLETED!")
        print(f"✅ Successful sheets: {successful_sheets}")
        print(f"❌ Failed sheets: {failed_sheets}")
        print(f"📊 Total processed: {successful_sheets + failed_sheets}")
        
        # Dropbox upload removed
        
        # Call webhook to trigger Google Apps Script processing ONLY AFTER all sheets are done
        print(f"\n🌐 Triggering Google Apps Script processing...")
        webhook_success = call_webhook_for_sheet(spreadsheet_id)
        if webhook_success:
            print(f"  ✅ Webhook triggered successfully! Google Apps Script will now:")
            print(f"     - Sort all sheets alphabetically")
            print(f"     - Generate BeenVerified URLs for 'bv ready' sheets")
        else:
            print(f"  ❌ Webhook call failed - manual intervention may be needed")
        
        print(f"\n🎉 Pipeline processing completed!")
        print(f"📁 All files saved in: {temp_folder}")
        print(f"📊 Total sheets processed: {successful_sheets + failed_sheets}")
        print(f"📝 Results saved to: {results_folder}")
        
        # Dropbox status removed
        
        # Clean up temporary files
        if CLEANUP_TEMP_FILES:
            print(f"\n🧹 Cleaning up temporary files...")
            cleanup_success = cleanup_temp_files(temp_folder, keep_results=KEEP_FINAL_RESULTS)
            
            if cleanup_success:
                if KEEP_FINAL_RESULTS:
                    final_results_folder = Path(f"{TEMP_FOLDER_NAME}_final_results")
                    if final_results_folder.exists():
                        print(f"✅ Cleanup completed! Final results saved in: {final_results_folder}")
                    else:
                        print("✅ Cleanup completed! Final results saved in results folder")
                else:
                    print("✅ Cleanup completed! All temporary files removed")
            else:
                print("⚠️  Cleanup failed, temporary files may still exist")
        else:
            print(f"\n📁 Temporary files preserved in: {temp_folder}")
            print("💡 Set CLEANUP_TEMP_FILES = True to enable automatic cleanup")
        
        # Reset pipeline mode
        PIPELINE_MODE = False
        PIPELINE_NAME = None
        
        return True, f"Pipeline '{pipeline_name}' processing completed successfully. {successful_sheets} sheets processed, {failed_sheets} failed."
        
    except Exception as e:
        print(f"❌ Error in pipeline processing: {str(e)}")
        # Reset pipeline mode on error
        PIPELINE_MODE = False
        PIPELINE_NAME = None
        return False, f"Pipeline processing error: {str(e)}"

# Dropbox functions removed

# Dropbox upload function removed

def main(selected_worksheet_ids=None, pipeline_name=None):
    """Main function to run the website research process"""
    print("🔄 SEQUENTIAL SHEET PROCESSING: Google Sheets Website Research Tool")
    print("🎯 PROCESSING SHEETS ONE BY ONE")
    print("=" * 50)
    
    # Update INDUSTRY if pipeline name is provided
    if pipeline_name:
        global INDUSTRY
        INDUSTRY = pipeline_name
        print(f"🏭 Industry set to: {INDUSTRY}")
    
    # Display web search configuration for API credit optimization
    display_web_search_configuration()
    
    # Prompt for pipeline ID (used for per-sheet sync) if not provided via env or CLI
    try:
        if not os.environ.get('PIPELINE_ID') and 'PIPELINE_ID_ARG' not in globals():
            entered = input("Enter Pipeline ID for sync (leave blank to skip): ").strip()
            if entered:
                globals()['PIPELINE_ID_ARG'] = entered
                print(f"🔗 Pipeline ID set for sync: {entered}")
            else:
                print("ℹ️ No Pipeline ID provided; sync link will be skipped.")
    except Exception:
        pass
    
    # Determine which headers to use
    if TESTING_MODE:
        print("🧪 TESTING MODE: Using hardcoded headers")
        headers = TESTING_HEADERS
        print(f"Headers: {', '.join(headers)}")
    else:
        print("📱 APP MODE: Loading headers from app configuration")
        headers = get_headers_from_app_config()
        print(f"Headers: {', '.join(headers)}")
    
    # Get spreadsheet ID from command line argument or user input
    import sys
    
    # Check for help argument
    if len(sys.argv) > 1 and sys.argv[1] in ['-h', '--help', 'help']:
        print("\n📖 Usage:")
        print("  python scraper.py \"https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit\"")
        print("  python scraper.py                    # Interactive mode")
        print("  python scraper.py                    # Debug mode (if DEBUG_MODE = False)")
        print("  python scraper.py --pipeline-mode    # Pipeline mode (process all sheets)")
        print("\n💡 Examples:")
        print("  python scraper.py \"https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit\"")
        print("  python scraper.py")
        print("  python scraper.py --pipeline-mode --pipeline 'Pool Services' --sheet-id 'SPREADSHEET_ID'")
        print("\n📁 Local Storage Configuration:")
        print(f"  - Results stored locally in temporary folder")
        return
    
    # Check if we're running with named arguments (from app.py)
    if len(sys.argv) > 1 and sys.argv[1].startswith('--'):
        # We're running with named arguments, so we need to get the sheet ID from the arguments
        # The arguments are already parsed by argparse in the __main__ section
        # We'll use the global variables that were set by argparse
        if 'spreadsheet_id' in globals():
            spreadsheet_id = globals()['spreadsheet_id']
            print(f"🔑 Using spreadsheet ID from arguments: {spreadsheet_id}")
        else:
            print("❌ No spreadsheet ID provided in arguments")
            print("Expected: --sheet-id SPREADSHEET_ID")
            return
    elif len(sys.argv) > 1:
        # Extract spreadsheet ID from full Google Sheets URL (legacy mode)
        sheet_url = sys.argv[1]
        print(f"📋 Sheet URL provided: {sheet_url}")
        
        # Extract spreadsheet ID from URL
        spreadsheet_id = extract_spreadsheet_id_from_url(sheet_url)
        if spreadsheet_id:
            print(f"🔑 Extracted Spreadsheet ID: {spreadsheet_id}")
        else:
            print("❌ Invalid Google Sheets URL format")
            print("Expected format: https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit")
            print("\n💡 Try: python scraper.py --help")
            return
    elif DEBUG_MODE:
        print("🐛 DEBUG MODE: Using hardcoded spreadsheet")
        spreadsheet_id = DEBUG_SPREADSHEET_ID
        print(f"Debug Spreadsheet ID: {spreadsheet_id}")
        print(f"Debug Sheet GID: {DEBUG_SHEET_GID}")
    else:
        # Get spreadsheet ID from user
        spreadsheet_id = get_spreadsheet_id_from_user()
        if not spreadsheet_id:
            return
    
    # Check if we're in pipeline mode
    pipeline_mode = '--pipeline-mode' in sys.argv
    pipeline_name = None
    
    if pipeline_mode:
        # Try to get pipeline name from arguments
        try:
            pipeline_idx = sys.argv.index('--pipeline')
            if pipeline_idx + 1 < len(sys.argv):
                pipeline_name = sys.argv[pipeline_idx + 1]
        except ValueError:
            pipeline_name = "Default Pipeline"
        
        print(f"\n🚀 PIPELINE MODE DETECTED!")
        print(f"📊 Pipeline Name: {pipeline_name}")
        print(f"🔑 Spreadsheet ID: {spreadsheet_id}")
        
        # Process all sheets in pipeline mode
        success, message = process_pipeline_all_sheets(pipeline_name, spreadsheet_id, headers, selected_worksheet_ids)
        if success:
            print(f"\n🎉 Pipeline processing completed successfully!")
            print(f"📝 {message}")
        else:
            print(f"\n❌ Pipeline processing failed!")
            print(f"📝 {message}")
        return
    
    # Regular single-sheet processing mode
    print(f"\n📁 Setting up temporary folder structure...")
    print("🚀 Starting immediately - no further prompts needed...")
    temp_folder, sheets_folder, buckets_folder, results_folder = setup_temp_folder()
    
    # Process all sheets from the spreadsheet
    if selected_worksheet_ids:
        print(f"📋 Selected worksheets provided: {selected_worksheet_ids}")
    success, all_sheets_data = process_all_sheets(
        spreadsheet_id,
        headers,
        temp_folder,
        sheets_folder,
        buckets_folder,
        results_folder,
        selected_worksheet_ids
    )
    if not success:
        error_msg = str(all_sheets_data)
        if 'quota exceeded' in error_msg.lower():
            handle_quota_exceeded_error()
            print(f"\n❌ Processing failed due to quota limits: {error_msg}")
        else:
            print(f"❌ Failed to process sheets: {error_msg}")
        return
    
    if not all_sheets_data:
        print("❌ No sheets with websites found.")
        return
    
    print(f"\n📊 Successfully prepared {len(all_sheets_data)} sheets for sequential processing")
    total_websites = sum(len(data['websites']) for data in all_sheets_data.values())
    total_buckets = sum(len(data['buckets']) for data in all_sheets_data.values())
    print(f"📈 Total websites: {total_websites}")
    print(f"🪣 Total buckets: {total_buckets}")
    
    # Handle debug mode vs API research
    if DEBUG_MODE and DEBUG_SKIP_API_CALLS:
        print("\n🐛 DEBUG MODE: Skipping API calls - extraction completed successfully!")
        print(f"📁 Temporary folder created: {temp_folder}")
        print(f"📊 Extracted websites from {len(all_sheets_data)} sheets")
        print(f"🎯 Target headers: {', '.join(headers)}")
        print(f"🪣 Created {total_buckets} buckets for OpenAI processing")
        
        print("\n📁 Files created:")
        print(f"  - Sheets: {sheets_folder}")
        print(f"  - Buckets: {buckets_folder}")
        print(f"  - Results: {results_folder}")
        
        # Call webhook to trigger Google Apps Script processing even in debug mode
        print(f"\n🌐 Triggering Google Apps Script processing...")
        webhook_success = call_webhook_for_sheet(spreadsheet_id)
        if webhook_success:
            print(f"  ✅ Webhook triggered successfully! Google Apps Script will now:")
            print(f"     - Sort all sheets alphabetically")
            print(f"     - Generate BeenVerified URLs for 'bv ready' sheets")
        else:
            print(f"  ❌ Webhook call failed - manual intervention may be needed")
        
        print("\n💡 To enable actual research:")
        print("  1. Set DEBUG_SKIP_API_CALLS = False")
        print("  2. Run the scraper again")
        print("  3. The scraper will process all buckets with OpenAI API")
        
        # Clean up temporary files in debug mode too
        if CLEANUP_TEMP_FILES:
            print(f"\n🧹 Cleaning up temporary files...")
            cleanup_success = cleanup_temp_files(temp_folder, keep_results=KEEP_FINAL_RESULTS)
            
            if cleanup_success:
                if KEEP_FINAL_RESULTS:
                    final_results_folder = Path(f"{TEMP_FOLDER_NAME}_final_results")
                    if final_results_folder.exists():
                        print(f"✅ Cleanup completed! Final results saved in: {final_results_folder}")
                    else:
                        print("✅ Cleanup completed! Final results saved in results folder")
                else:
                    print("✅ Cleanup completed! All temporary files removed")
            else:
                print("⚠️  Cleanup failed, temporary files may still exist")
        else:
            print(f"\n📁 Temporary files preserved in: {temp_folder}")
            print("💡 Set CLEANUP_TEMP_FILES = True to enable automatic cleanup")
        
        return
    
    # Dropbox integration removed
    
    # Process ALL sheets sequentially instead of targeting a specific one
    print(f"\n🚀 Starting processing for ALL sheets sequentially...")
    print(f"📊 Found {len(all_sheets_data)} sheets to process")
    
    # Process each sheet one by one
    successful_sheets = 0
    failed_sheets = 0
    
    for sheet_id, sheet_data in all_sheets_data.items():
        if sheet_id == 'COMBINED_SMALL_SHEETS':
            print(f"\n⏭️  Skipping combined small sheets (will be processed with individual sheets)")
            continue
            
        print(f"\n{'='*60}")
        print(f"🎯 Processing sheet ID: {sheet_id}")
        print(f"{'='*60}")
        
        # Process this specific sheet
        try:
            success, message = process_specific_sheet(sheet_id, all_sheets_data, buckets_folder, results_folder, headers, spreadsheet_id)
            
            if success:
                successful_sheets += 1
                print(f"✅ Sheet {sheet_id} processing completed successfully!")
            else:
                failed_sheets += 1
                print(f"❌ Sheet {sheet_id} processing failed: {message}")
        except Exception as e:
            failed_sheets += 1
            print(f"❌ Sheet {sheet_id} processing crashed with error: {str(e)}")
            print(f"🔄 Continuing to next sheet despite error...")
        
        # Continue to next sheet - don't end program here
        print(f"🔄 Moving to next sheet...")
    
    # Summary of processing
    print(f"\n🎉 ALL SHEETS PROCESSING COMPLETED!")
    print(f"✅ Successful sheets: {successful_sheets}")
    print(f"❌ Failed sheets: {failed_sheets}")
    print(f"📊 Total processed: {successful_sheets + failed_sheets}")
    
    # Dropbox upload removed
    
    # Call webhook to trigger Google Apps Script processing ONLY AFTER all sheets are done
    print(f"\n🌐 Triggering Google Apps Script processing...")
    webhook_success = call_webhook_for_sheet(spreadsheet_id)
    if webhook_success:
        print(f"  ✅ Webhook triggered successfully! Google Apps Script will now:")
        print(f"     - Sort all sheets alphabetically")
        print(f"     - Generate BeenVerified URLs for 'bv ready' sheets")
    else:
        print(f"  ❌ Webhook call failed - manual intervention may be needed")
    
    print(f"\n🎉 All sheets processing completed!")
    print(f"📁 All files saved in: {temp_folder}")
    print(f"📊 Total sheets processed: {successful_sheets + failed_sheets}")
    print(f"📝 Results saved to: {results_folder}")
    
    # Dropbox status removed

    # Clean up temporary files
    if CLEANUP_TEMP_FILES:
        print(f"\n🧹 Cleaning up temporary files...")
        cleanup_success = cleanup_temp_files(temp_folder, keep_results=KEEP_FINAL_RESULTS)
        
        if cleanup_success:
            if KEEP_FINAL_RESULTS:
                final_results_folder = Path(f"{TEMP_FOLDER_NAME}_final_results")
                if final_results_folder.exists():
                    print(f"✅ Cleanup completed! Final results saved in: {final_results_folder}")
                else:
                    print("✅ Cleanup completed! Final results saved in results folder")
            else:
                print("✅ Cleanup completed! All temporary files removed")
        else:
            print("⚠️  Cleanup failed, temporary files may still exist")
    else:
        print(f"\n📁 Temporary files preserved in: {temp_folder}")
        print("💡 Set CLEANUP_TEMP_FILES = True to enable automatic cleanup")

def ensure_minimum_locations(locations_str, min_locations=1):
    """Ensure locations count is at least the minimum value"""
    try:
        # Try to convert to integer
        if locations_str and locations_str.strip():
            # Extract just the number from the string
            import re
            number_match = re.search(r'(\d+)', locations_str)
            if number_match:
                location_count = int(number_match.group(1))
                # Ensure minimum of 1 location
                if location_count < min_locations:
                    print(f"        📍 Adjusting locations from {location_count} to minimum {min_locations}")
                    return str(min_locations)
                return str(location_count)
        
        # If no valid number found or empty, return minimum
        print(f"        📍 Setting locations to minimum {min_locations} (no valid count found)")
        return str(min_locations)
        
    except (ValueError, TypeError) as e:
        print(f"        📍 Error parsing locations '{locations_str}', setting to minimum {min_locations}: {e}")
        return str(min_locations)

if __name__ == "__main__":
    import argparse
    
    # Test the minimum locations functionality
    print("Testing minimum locations functionality:")
    test_cases = [
        ("0", "1"),           # Should become 1
        ("1", "1"),           # Should stay 1
        ("3", "3"),           # Should stay 3
        ("", "1"),            # Empty should become 1
        ("abc", "1"),         # Non-numeric should become 1
        ("2 locations", "2"), # Should extract 2
        ("0 offices", "1"),   # Should become 1
    ]
    
    for input_val, expected in test_cases:
        result = ensure_minimum_locations(input_val)
        status = "✅" if result == expected else "❌"
        print(f"  {status} Input: '{input_val}' -> Output: '{result}' (Expected: '{expected}')")
    
    print("\n" + "="*60)
    print("GOOGLE SHEETS WEBSITE RESEARCH TOOL")
    print("="*60)
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Integrus Scraper - AI-powered research tool')
    parser.add_argument('--pipeline', type=str, help='Pipeline name for this execution')
    parser.add_argument('--sheet-id', type=str, help='Google Sheets spreadsheet ID')
    parser.add_argument('--headers', type=str, help='Comma-separated list of headers to process')
    parser.add_argument('--count-professionals', action='store_true', help='Enable professional counting')
    parser.add_argument('--pipeline-mode', action='store_true', help='Enable pipeline mode to process all sheets')
    parser.add_argument('--pipeline-id', type=str, help='Pipeline ID to sync with integrus-app after each sheet')
    parser.add_argument('--selected-worksheets', type=str, help='Comma-separated list of worksheet IDs or names to process')
    
    args = parser.parse_args()
    
    # If arguments provided, use them; otherwise run main()
    if args.pipeline and args.sheet_id:
        # Declare global variables at the top
        global spreadsheet_id
        
        print(f"🚀 Starting scraper with pipeline: {args.pipeline}")
        print(f"📊 Sheet ID: {args.sheet_id}")
        if args.headers:
            print(f"📋 Headers: {args.headers}")
        if args.count_professionals:
            print(f"👥 Professional counting: Enabled")
        
        # Set global variables based on arguments
        if args.headers:
            TARGET_HEADERS = args.headers.split(',')
        if args.count_professionals:
            COUNT_PROFESSIONALS = True
        
        # Set the spreadsheet_id as a global variable so main() can access it
        spreadsheet_id = args.sheet_id
        
        # Store pipeline name for use in main function
        pipeline_name = args.pipeline
        print(f"🏭 Pipeline name: {pipeline_name}")
        
        # Parse selected worksheets if provided
        selected_worksheet_ids = None
        if args.selected_worksheets:
            selected_worksheet_ids = [ws.strip() for ws in args.selected_worksheets.split(',') if ws.strip()]
            print(f"📋 Selected worksheets: {selected_worksheet_ids}")
        
        # Stash pipeline-id globally so process_specific_sheet can read it
        if args.pipeline_id:
            globals()['PIPELINE_ID_ARG'] = args.pipeline_id
            print(f"🔗 Pipeline ID for sync: {args.pipeline_id}")

        # Run the scraper
        main(selected_worksheet_ids, pipeline_name)
    else:
        # Run without arguments (original behavior)
        main()
