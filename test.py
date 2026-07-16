import asyncio
import json
import random
import os
from datetime import datetime
from playwright.async_api import async_playwright, Page, Dialog
import ollama
import re

# CONFIGURATION
TARGET_URL = "https://noblequran-85hu2yge.manus.space/dashboard"
# OLLAMA_MODEL = "llama3.2"
OLLAMA_MODEL = "minimax-m3:cloud"
MAX_STEPS = 100
HEADLESS = True

# 📁 TIMESTAMPED OUTPUT FOLDER
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
OUTPUT_DIR = os.path.abspath(f"testrun_{TIMESTAMP}")
os.makedirs(OUTPUT_DIR, exist_ok=True)
USER_DATA_DIR = os.path.abspath("./playwright_user_data")
os.makedirs(USER_DATA_DIR, exist_ok=True)

test_logs: list[dict] = []

async def get_page_state(page: Page) -> str:
    try:
        # Wait briefly for any pending navigation to settle
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
        
        state = await page.evaluate("""() => {
            const interactives = Array.from(document.querySelectorAll('button, a, input, select, textarea, [role="button"], [onclick], form'));
            const tags = interactives.map(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return null;
                
                let text = el.innerText?.trim() 
                        || el.getAttribute('aria-label') 
                        || el.placeholder 
                        || el.value 
                        || el.getAttribute('title') 
                        || '';
                
                let typeInfo = el.tagName;
                if (el.tagName === 'FORM') typeInfo = 'FORM';
                else if (el.tagName === 'INPUT') typeInfo = `INPUT[type=${el.type}]`;
                
                if (text.length > 60) text = text.substring(0, 60) + '...';
                return `<${typeInfo} text="${text}" />`;
            }).filter(Boolean);

            const modals = Array.from(document.querySelectorAll('[role="dialog"], .modal, .popup, .alert')).filter(el => {
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            }).map(el => `<MODAL text="${el.innerText?.trim() || 'Dialog'}" />`);

            return `URL: ${window.location.href}\\nTitle: ${document.title}\\nModals: ${modals.length > 0 ? modals.join(', ') : 'None'}\\nElements:\\n${tags.join('\\n')}`;
        }""")
        return state
    except Exception as e:
        if "Execution context was destroyed" in str(e):
            print("   -> ⚠️ Navigation detected, waiting for new page load...")
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
                # Retry once after navigation
                return await get_page_state(page)
            except Exception:
                return "URL: Loading...\nElements: []"
        raise e

async def decide_next_action(page_state: str) -> dict:
    prompt = f"""
    You are an Advanced Monkey Testing Agent. Your goal is to deeply test the app by filling forms, submitting data, and handling modals.
    
    Current Page State:
    {page_state}
    
    Choose ONE action from this list:
    1. "click": Click a button or link.
    2. "type": Type random text into an input field.
    3. "submit_form": Find a form and submit it (trigger a 'submit' button or press Enter).
    4. "handle_modal": If a modal/dialog is detected, try to close it (click 'X', 'Cancel', 'Close') or accept it.
    5. "scroll": Scroll the page.
    
    Rules:
    - If you see a <FORM>, prioritize "submit_form" or "type" inside it.
    - If you see a <MODAL>, prioritize "handle_modal".
    - For "type", generate a random string like "test_123".
    - The 'target' MUST match the 'text' attribute from the list EXACTLY.
    
    Respond ONLY with JSON: {{"action": "...", "target": "...", "value": "..."}}
    """
    try:
        response = ollama.chat(model=OLLAMA_MODEL, messages=[{'role': 'user', 'content': prompt}])
        content = response['message']['content'].replace('```json', '').replace('```', '').strip()
        return json.loads(content)
    except Exception as e:
        return {"action": "scroll", "target": "none", "value": ""}

async def execute_action(page: Page, action_plan: dict, step_num: int):
    action = action_plan.get("action", "scroll")
    target = action_plan.get("target", "")
    value = action_plan.get("value", "")
    
    log_entry = {
        "step": step_num, "action": action, "target": target,
        "value": value if action == "type" else None,
        "status": "SUCCESS", "error": None, "screenshot": None, "url": page.url
    }

    print(f"🤖 Step {step_num}: Executing {action} on '{target}'")

    try:
        # 1. Handle Native Browser Dialogs (Alerts/Confirms) immediately if they pop up
        # (Note: Usually handled via event listener, but we can check state too)
        
        if action == "scroll":
            await page.evaluate(f"window.scrollBy(0, {random.choice([-500, 500])})")
            
        elif action == "handle_modal":
            # Strategy A: Close button
            close_btn = page.locator("button[aria-label='Close'], .close, [title='Close']").first
            if await close_btn.count() > 0:
                await close_btn.click(timeout=2000)
            else:
                # Strategy B: Cancel/No button
                cancel_btn = page.get_by_role("button", name=re.compile("cancel|close|no|dismiss", re.I)).first
                if await cancel_btn.count() > 0:
                    await cancel_btn.click(timeout=2000)
                else:
                    # Strategy C: Press Escape
                    await page.keyboard.press("Escape")
                    print("   -> Sent Escape key to close modal")
                    
        elif action == "submit_form":
            # Find any visible form
            form = page.locator("form:visible").first
            if await form.count() > 0:
                # Try to find a submit button inside
                submit_btn = form.locator("button[type='submit'], input[type='submit']").first
                if await submit_btn.count() > 0:
                    await submit_btn.click(timeout=3000)
                else:
                    # No submit button? Press Enter on the last input
                    inputs = form.locator("input:visible, textarea:visible")
                    if await inputs.count() > 0:
                        await inputs.last.press("Enter")
                    else:
                        raise Exception("Form found but no inputs or submit button")
            else:
                raise Exception("No visible form found to submit")

        elif action == "click":
            locator = page.get_by_text(target, exact=False).first
            if await locator.count() == 0:
                locator = page.get_by_role("button", name=target, exact=False).first
            
            if await locator.count() > 0:
                await locator.click(timeout=3000)
            else:
                raise Exception(f"Element '{target}' not found")
                
        elif action == "type":
            locator = page.get_by_label(target, exact=False).first
            if await locator.count() == 0:
                locator = page.get_by_placeholder(target, exact=False).first
            if await locator.count() == 0:
                # Fallback: find any visible input
                locator = page.locator("input:visible, textarea:visible").first
            
            if await locator.count() > 0:
                await locator.fill(value)
            else:
                raise Exception(f"Input '{target}' not found")
        
        await page.wait_for_load_state("networkidle", timeout=5000)
        log_entry["url"] = page.url

    except Exception as e:
        error_msg = str(e)
        log_entry["status"] = "FAILED"
        log_entry["error"] = error_msg
        print(f"💥 Error: {error_msg}")
        
        screenshot_name = f"error_step_{step_num}.png"
        try:
            await page.screenshot(path=os.path.join(OUTPUT_DIR, screenshot_name))
            log_entry["screenshot"] = screenshot_name
        except Exception:
            pass

    test_logs.append(log_entry)

# 🚨 Global Dialog Handler for Native Alerts
async def handle_dialog(dialog: Dialog):
    print(f"   -> 🚨 Native Dialog Detected: {dialog.message}")
    # Randomly accept or dismiss to test both paths
    if random.random() > 0.5:
        await dialog.accept()
        print("   -> Accepted dialog")
    else:
        await dialog.dismiss()
        print("   -> Dismissed dialog")

def generate_markdown_report(start_time, end_time):
    duration_seconds = (end_time - start_time).total_seconds()
    total_steps = len(test_logs)
    failed_steps = [log for log in test_logs if log["status"] in ["FAILED", "CRASH"]]
    success_rate = ((total_steps - len(failed_steps)) / total_steps * 100) if total_steps > 0 else 0

    md_content = f"""# 🐵 Advanced Monkey Test Report

**Target URL:** {TARGET_URL}  
**Date:** {start_time.strftime('%Y-%m-%d %H:%M:%S')}  
**Duration:** {duration_seconds:.2f} seconds  
**Total Steps:** {total_steps}  
**Success Rate:** {success_rate:.2f}%  
**Errors Found:** {len(failed_steps)}  
**Output Folder:** `{OUTPUT_DIR}`

## 📊 Summary
The agent performed {total_steps} actions using **{OLLAMA_MODEL}**.
Actions included: Clicking, Typing, **Form Submission**, and **Modal Handling**.
"""

    if failed_steps:
        md_content += "\n## 🚨 Errors Detected\n"
        for log in failed_steps:
            md_content += f"\n### Step {log['step']}: {log['action']} failed\n"
            md_content += f"- **Target:** `{log['target']}`\n"
            md_content += f"- **Error:** `{log['error']}`\n"
            if log['screenshot']:
                md_content += f"- **Screenshot:** `![Screenshot](./{log['screenshot']})`\n"
    
    md_content += "\n## 📜 Action Log\n\n| Step | Action | Target | Status |\n|---|---|---|---|\n"
    for log in test_logs:
        icon = "✅" if log["status"] == "SUCCESS" else "❌"
        md_content += f"| {log['step']} | {log['action']} | {log['target'][:30]}... | {icon} |\n"

    report_path = os.path.join(OUTPUT_DIR, "test_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    
    print(f"\n📄 Report generated: {report_path}")
    print(f"💾 All artifacts saved in: {OUTPUT_DIR}")

async def main():
    start_time = datetime.now()
    
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=HEADLESS,
            args=["--window-size=1920,1080", "--disable-blink-features=AutomationControlled"],
            no_viewport=True
        )
        
        page = context.pages[0]
        page.on("dialog", handle_dialog)

        print(f"🚀 Starting Advanced Monkey Test on {TARGET_URL}...")
        await page.goto(TARGET_URL)
        await page.wait_for_load_state("networkidle")
        
        for step in range(1, MAX_STEPS + 1):
            print(f"\n--- Step {step}/{MAX_STEPS} ---")
            
            # 🛡️ Safe State Extraction
            try:
                state = await get_page_state(page)
            except Exception as e:
                print(f"   -> 🚨 Failed to get state: {e}. Skipping step.")
                continue

            plan = await decide_next_action(state)
            await execute_action(page, plan, step)
            
            # 🛑 Critical: Wait for navigation AFTER action before next loop
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass # Timeout is okay, we just proceed to next step
            
            await asyncio.sleep(1.0)

        await context.close()

    end_time = datetime.now()
    generate_markdown_report(start_time, end_time)

if __name__ == "__main__":
    asyncio.run(main())