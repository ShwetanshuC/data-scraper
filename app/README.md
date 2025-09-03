# app/ — Shared Helpers

Small, focused modules used by the main orchestrator (`windowsautomation.py`).

- `config.py`
  - `SHEET_URL`: default Google Sheet URL.
- `sheets.py`
  - `ensure_sheets_tab(driver, url) -> handle`
  - `enter_sheets_iframe_if_needed(driver, timeout=10)`
  - `goto_cell(driver, cell_ref)`
  - `read_cell(driver, cell_ref) -> str`
  - `get_col_values(driver, col_letter) -> list[str]`
  - `find_next_empty_row(driver) -> int`
  - `write_headers_once_simple(driver)`
  - `paste_row_into_row(driver, row, values)`
  - `paste_row_at_next_empty(driver, values) -> int`
- `chat.py`
  - `_find_composer(driver, timeout=5) -> WebElement|None`
  - `_send_message(driver, editor)`
  - `_find_send_button(driver) -> WebElement|None`
  - `_likely_streaming(driver) -> bool`
  - `open_new_chat(driver, chat_handle, model_url='https://chatgpt.com/?model=gpt-5')`
  - `ask_gpt_and_get_reply(driver, chat_handle, prompt, response_timeout=20) -> str`
  - `find_chat_handle(driver) -> handle|None`
- `chat_attach.py`
  - `send_image_and_prompt_get_reply(driver, chat_handle, image_path, prompt) -> str`
- `screenshot.py`
  - `save_temp_fullpage_jpeg_screenshot(driver, target_width=1400, jpeg_quality=50) -> str`
  - `save_temp_jpeg_screenshot(driver, target_width=900, jpeg_quality=40) -> str`
  - `screenshot_to_base64(driver, target_width=900, jpeg_quality=40) -> str`
- `nav.py`
  - `navigate_to_suggested_section(driver, nav_text) -> bool`
  - `_expand_specific_dropdown_and_navigate(driver, parent_text, child_text) -> bool`
  - `_expand_parent_and_click_best_staff_child(driver, parent_text) -> bool`
  - `_expand_dropdowns_and_try(driver, nav_text) -> bool`
  - `_navigate_by_text_via_direct_get(driver, anchor_text) -> bool`
  - `_navigate_best_staff_link_anywhere(driver) -> bool`
  - `_likely_staff_url(url) -> bool`
- `utils.py`
  - `get_visible_link_texts(driver, limit=60) -> list[str]`
  - `_nav_text_matches_links(nav_text, links) -> bool`
  - `_host_of(url) -> str`
  - `switch_to_site_tab_by_host(driver, expected_host, fallback_handle=None) -> handle|None`
  - `debug_where(driver, label='')`
- `prompts.py`
  - `build_nav_prompt(link_texts=None) -> str`
  - `build_staff_csv_prompt() -> str`
  - `parse_comma_reply(reply) -> (phone, first, last, doctors)`
  - `parse_three_reply(reply) -> (phone, first, last)`
  - `extract_first_integer(text) -> str`

## Guidelines
- Keep modules small and single‑purpose.
- Avoid side effects at import time; only define helpers.
- Prefer reusing these helpers rather than copying logic into the orchestrator.
