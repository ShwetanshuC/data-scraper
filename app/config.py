"""Project-wide configuration constants."""

# Default Google Sheet URL used by both automation.py and windowsautomation.py
SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1gIJOeJQh4Vu8jhCdVY-ETR01zgQ58buQ2ztmUs7p7u0/edit?gid=0"
)



# Column mapping for production sheet
# Adjust these if the sheet layout changes
WEBSITE_COL = 'K'
OWNER_FIRST_COL = 'C'
OWNER_LAST_COL = 'D'
PHONE_COL = 'F'
DOCTOR_COUNT_COL = 'O'  # Doctor count lives in new column O
