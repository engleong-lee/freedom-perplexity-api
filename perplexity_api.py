from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from seleniumbase import Driver
from selenium.webdriver.common.keys import Keys
import time
import os


class PromptRequest(BaseModel):
    prompt: str
    use_research_mode: bool = False


app = FastAPI()


def safe_find_input(driver):
    """Safely find the input element, handling stale references"""
    return driver.find_element("div[contenteditable='true']")

def wait_for_typing_completion(driver, expected_text, max_wait=300):
    """Wait for typing animation to complete by monitoring text changes - will keep waiting until complete"""
    print(f"[PERPLEXITY] Waiting for typing completion of {len(expected_text)} characters...")
    start_time = time.time()
    last_text = ""
    stable_count = 0
    last_progress_time = start_time
    
    while time.time() - start_time < max_wait:
        try:
            current_input = safe_find_input(driver)
            current_text = current_input.get_attribute('textContent') or current_input.text
            current_text = current_text.strip()
            
            # Check if text is stable (hasn't changed)
            if current_text == last_text:
                stable_count += 1
                if stable_count >= 6:  # Text stable for 6 checks (1.2s)
                    if len(current_text) >= len(expected_text) * 0.95:  # 95% complete
                        print(f"[PERPLEXITY] ✓ Typing completed and stable: {len(current_text)}/{len(expected_text)} characters")
                        return True
                    elif len(current_text) > 0:  # Text present but incomplete - keep waiting
                        print(f"[PERPLEXITY] Text stable but incomplete ({len(current_text)}/{len(expected_text)}), continuing to wait...")
                        stable_count = 0  # Reset and keep waiting
            else:
                stable_count = 0
                last_text = current_text
                if len(current_text) > 0:
                    print(f"[PERPLEXITY] Typing in progress: {len(current_text)}/{len(expected_text)} characters")
                    last_progress_time = time.time()  # Update progress time
                    
            # Check for stalled typing (no progress for too long)
            if time.time() - last_progress_time > 60:  # No progress for 60 seconds
                print(f"[PERPLEXITY] ⚠️ No typing progress for 60s, current text: {len(current_text)}/{len(expected_text)}")
                if len(current_text) >= len(expected_text) * 0.8:  # If we have 80%+, accept it
                    print("[PERPLEXITY] Accepting current text as typing may have stalled")
                    return True
                else:
                    print("[PERPLEXITY] Text too incomplete, continuing to wait...")
                    last_progress_time = time.time()  # Reset stall timer
                
            time.sleep(0.2)  # Check every 200ms
        except Exception as e:
            print(f"[PERPLEXITY] Error monitoring typing: {e}")
            time.sleep(0.2)
            continue
    
    print(f"[PERPLEXITY] ⚠️ Typing completion timeout after {max_wait}s")
    return False  # Timeout

def select_research_mode(driver):
    """Select research mode by clicking the research mode button"""
    try:
        print("[PERPLEXITY] Attempting to select research mode...")
        
        # Wait for research mode button to be available
        max_attempts = 3
        research_button = None
        
        for attempt in range(max_attempts):
            try:
                research_button = driver.find_element("[data-testid='search-mode-research']")
                if research_button and research_button.is_displayed():
                    print(f"[PERPLEXITY] ✓ Found research mode button on attempt {attempt + 1}")
                    break
                else:
                    research_button = None
            except Exception:
                if attempt < max_attempts - 1:
                    print(f"[PERPLEXITY] Research mode button not found, attempt {attempt + 1}/{max_attempts}")
                    driver.sleep(1)
                else:
                    print("[PERPLEXITY] ⚠️ Research mode button not found after all attempts")
                    return False
        
        if research_button:
            # Click the research mode button
            try:
                research_button.click()
                driver.sleep(0.5)
                print("[PERPLEXITY] ✓ Research mode selected successfully")
                return True
            except Exception as click_error:
                print(f"[PERPLEXITY] Failed to click research mode button: {click_error}")
                # Try JavaScript click as fallback
                try:
                    driver.execute_script("arguments[0].click();", research_button)
                    driver.sleep(0.5)
                    print("[PERPLEXITY] ✓ Research mode selected via JavaScript click")
                    return True
                except Exception as js_error:
                    print(f"[PERPLEXITY] JavaScript click also failed: {js_error}")
                    return False
        else:
            print("[PERPLEXITY] ⚠️ Research mode button not available")
            return False
            
    except Exception as e:
        print(f"[PERPLEXITY] Error in select_research_mode: {e}")
        return False

def inputFieldCheck(driver, contentToType, use_research_mode=False):
    """Check and fill the input field with the prompt"""
    try:
        # Find and interact with the contenteditable div (re-find each time to avoid stale references)
        input_div = safe_find_input(driver)
        input_div.click()  # Focus the element first
        driver.sleep(0.5)
        
        # Re-find element before clearing and typing
        input_div = safe_find_input(driver)
        input_div.clear()
        
        # Handle newlines by replacing \n with Shift+Enter
        if '\n' in contentToType:
            parts = contentToType.split('\n')
            for i, part in enumerate(parts):
                input_div.send_keys(part)
                if i < len(parts) - 1:  # Don't add Shift+Enter after the last part
                    input_div.send_keys(Keys.SHIFT + Keys.ENTER)
        else:
            input_div.send_keys(contentToType)
        
        # Wait for typing animation to complete using dynamic monitoring (will wait until complete)
        typing_completed = wait_for_typing_completion(driver, contentToType)
        if not typing_completed:
            print("[PERPLEXITY] ⚠️ Extremely long timeout reached - this should rarely happen")
            # Even after 5 minutes, still try to verify text before giving up
            try:
                current_input = safe_find_input(driver)
                final_text = current_input.get_attribute('textContent') or current_input.text
                print(f"[PERPLEXITY] Final text check: {len(final_text.strip())}/{len(contentToType)} characters")
                if len(final_text.strip()) < len(contentToType) * 0.5:
                    raise Exception("Typing failed - text is severely incomplete even after extended wait")
            except Exception as final_check_error:
                print(f"[PERPLEXITY] Final text check failed: {final_check_error}")
                raise Exception("Cannot verify text completion after extended wait")
        
        # Only after typing is confirmed complete, select research mode if requested
        if use_research_mode:
            print("[PERPLEXITY] ✓ Typing fully completed, now selecting research mode...")
            research_selected = select_research_mode(driver)
            if not research_selected:
                print("[PERPLEXITY] ⚠️ Research mode selection failed, continuing with default mode")
        
        # Now look for submit button
        print("[PERPLEXITY] ✓ Typing fully completed, now looking for submit button...")
        submit_button = None
        max_attempts = 3
        attempt = 0
        
        while attempt < max_attempts and not submit_button:
            attempt += 1
            driver.sleep(0.2)
            
            try:
                # Primary selector - submit button should be ready now
                submit_button = driver.find_element("button[data-testid='submit-button']")
                if submit_button and submit_button.is_displayed():
                    print(f"[PERPLEXITY] ✓ Found submit button on attempt {attempt}")
                    break
                else:
                    submit_button = None
                    
            except Exception:
                # Try fallback on final attempt
                if attempt == max_attempts:
                    try:
                        submit_button = driver.find_element("button[aria-label='Submit']")
                        if submit_button and submit_button.is_displayed():
                            print("[PERPLEXITY] ✓ Found submit button via aria-label fallback")
                        else:
                            submit_button = None
                    except:
                        pass
        
        if submit_button:
            submit_button.click()
            driver.sleep(3)
            print("[PERPLEXITY] Successfully clicked submit button")
        else:
            # Fast fallback - press Enter key (most reliable method)
            print("[PERPLEXITY] Submit button not found, using Enter key")
            try:
                # Verify text is still there before pressing Enter (use safe find)
                fresh_input = safe_find_input(driver)
                current_text = fresh_input.get_attribute('textContent') or fresh_input.text
                if len(current_text.strip()) == 0:
                    # Text was cleared, re-type it
                    print("[PERPLEXITY] Input was cleared, re-typing content")
                    fresh_input.send_keys(contentToType)
                    driver.sleep(0.2)
                
                # Use keyboard shortcut to submit instead of Enter key
                fresh_input.send_keys(Keys.CONTROL + Keys.RETURN)  # Ctrl+Enter to submit
                driver.sleep(2)
                print("[PERPLEXITY] Ctrl+Enter sent successfully")
            except Exception as fallback_error:
                print(f"[PERPLEXITY] Keyboard shortcut failed, trying Enter: {fallback_error}")
                try:
                    # Final fallback - regular Enter (re-find element)
                    fresh_input = safe_find_input(driver)
                    fresh_input.send_keys(Keys.ENTER)
                    driver.sleep(2)
                    print("[PERPLEXITY] Enter key sent successfully")
                except Exception as enter_error:
                    print(f"[PERPLEXITY] Enter key failed: {enter_error}")
                    raise Exception("Submit failed - all methods exhausted")
            
    except Exception as e:
        print(f"[PERPLEXITY] Error in inputFieldCheck: {e}")
        raise


def getResult(driver):
    """Wait for response generation and extract the result"""
    max_wait_time = 450  # Extended to 7.5 minutes for complex queries
    check_interval = 5  # seconds
    start_time = time.time()
    print("[PERPLEXITY] Starting to wait for response generation to complete...")

    # Wait for response generation to complete
    while time.time() - start_time < max_wait_time:
        try:
            stop_buttons = driver.find_elements("button[data-testid='stop-generating-response-button']")
            if not stop_buttons or not any(btn.is_displayed() for btn in stop_buttons):
                print("[PERPLEXITY] ✓ Response generation completed - 'stop' button disappeared")
                break
            else:
                print(f"[PERPLEXITY] ⏳ Still generating response... waiting {check_interval}s")
                driver.sleep(check_interval)
        except Exception as e:
            print(f"[PERPLEXITY] ⚠️ Error checking generation status: {e}")
            driver.sleep(check_interval)
    else:
        print("[PERPLEXITY] ⏰ TIMEOUT: Response generation exceeded 7.5 minutes")

    # Scroll to bottom with progressive scrolling to ensure we reach the actual bottom
    print("[PERPLEXITY] Scrolling to bottom to find copy button...")
    
    # Enhanced progressive scrolling approach with multiple height checks
    max_scroll_attempts = 10
    scroll_attempt = 0
    stable_height_count = 0
    
    while scroll_attempt < max_scroll_attempts:
        scroll_attempt += 1
        
        # Get multiple scroll height measurements for more reliable detection
        body_height = driver.execute_script("return document.body.scrollHeight")
        doc_height = driver.execute_script("return document.documentElement.scrollHeight")
        max_height = max(body_height, doc_height)
        
        print(f"[PERPLEXITY] Scroll attempt {scroll_attempt}: body={body_height}, doc={doc_height}, max={max_height}")
        
        # Scroll using multiple methods to ensure we reach bottom
        driver.execute_script("window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))")
        driver.sleep(3)  # Increased wait time for dynamic content
        
        # Check if height is stable after scroll
        new_body_height = driver.execute_script("return document.body.scrollHeight")
        new_doc_height = driver.execute_script("return document.documentElement.scrollHeight")
        new_max_height = max(new_body_height, new_doc_height)
        
        if new_max_height == max_height:
            stable_height_count += 1
            print(f"[PERPLEXITY] Height stable ({stable_height_count}/3): {new_max_height}")
            # Require height to be stable for 3 consecutive checks
            if stable_height_count >= 3:
                print("[PERPLEXITY] ✓ Bottom reached - height stable for 3 checks")
                break
        else:
            stable_height_count = 0  # Reset if height changed
            print(f"[PERPLEXITY] Content still loading: {max_height} → {new_max_height}")
    
    if scroll_attempt >= max_scroll_attempts:
        print("[PERPLEXITY] ⚠️ Max scroll attempts reached, proceeding with current position")
    
    # More aggressive final scroll attempts with multiple methods and containers
    print("[PERPLEXITY] Performing final aggressive scroll attempts...")
    
    # Method 1: Standard scroll approaches (repeated with delays)
    for _ in range(3):
        driver.execute_script("window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight))")
        driver.sleep(1)
        driver.execute_script("document.body.scrollTop = document.body.scrollHeight")
        driver.sleep(1)
        driver.execute_script("document.documentElement.scrollTop = document.documentElement.scrollHeight")
        driver.sleep(1)
    
    # Method 2: Scroll to maximum possible value
    driver.execute_script("window.scrollTo(0, Number.MAX_SAFE_INTEGER)")
    driver.sleep(2)
    
    # Method 3: Try scrolling specific containers that might contain the content
    scroll_containers = [
        "main",
        "[role='main']", 
        ".conversation",
        ".answer",
        ".result",
        "[data-testid='conversation']"
    ]
    
    for container in scroll_containers:
        try:
            script = f"""
            const element = document.querySelector('{container}');
            if (element) {{
                element.scrollTo(0, element.scrollHeight);
                console.log('Scrolled container: ' + '{container}');
            }}
            """
            driver.execute_script(script)
            driver.sleep(0.5)
        except Exception as e:
            # Continue with other containers if one fails
            print(f"[PERPLEXITY] Container scroll failed for {container}: {e}")
            continue
    
    # Method 4: Scroll to the last visible element
    try:
        driver.execute_script("""
        const allElements = document.querySelectorAll('*');
        const lastElement = allElements[allElements.length - 1];
        if (lastElement) {
            lastElement.scrollIntoView({behavior: 'smooth', block: 'end'});
        }
        """)
        driver.sleep(2)
    except Exception as e:
        print(f"[PERPLEXITY] Last element scroll failed: {e}")
    
    print("[PERPLEXITY] ✓ Completed all aggressive scroll attempts")
    
    print("[PERPLEXITY] Finished scrolling to bottom")
    
    try:
        # Set window size to prevent element overlap issues
        driver.set_window_size(1920, 1080)
        driver.maximize_window()
        driver.sleep(1)
        
        # Enhanced copy button detection with retry logic
        copy_button = None
        max_copy_attempts = 5
        copy_attempt = 0
        
        print("[PERPLEXITY] Starting enhanced copy button detection...")
        
        while copy_attempt < max_copy_attempts and not copy_button:
            copy_attempt += 1
            print(f"[PERPLEXITY] Copy button detection attempt {copy_attempt}/{max_copy_attempts}")
            
            # Wait longer between attempts to allow for dynamic content loading
            if copy_attempt > 1:
                driver.sleep(3)  # Progressive wait
            
            # Try multiple selectors for copy button
            copy_selectors = [
                "button[aria-label='Copy']",
                "button[title='Copy']", 
                "button[data-testid='copy-button']",
                "button:has-text('Copy')",
                ".copy-button",
                "[role='button'][aria-label='Copy']"
            ]
            
            for selector in copy_selectors:
                try:
                    copy_buttons = driver.find_elements(selector)
                    if copy_buttons:
                        # Filter for visible and clickable buttons
                        visible_buttons = [btn for btn in copy_buttons if btn.is_displayed() and btn.is_enabled()]
                        if visible_buttons:
                            copy_button = visible_buttons[-1]  # Get the last (most recent) copy button
                            print(f"[PERPLEXITY] ✓ Found copy button with selector: {selector}")
                            break
                except Exception as e:
                    print(f"[PERPLEXITY] Selector {selector} failed: {e}")
                    continue
            
            if copy_button:
                break
                
            # If no button found, try scrolling a bit more and check again
            if copy_attempt < max_copy_attempts:
                print("[PERPLEXITY] No copy button found, trying additional scroll...")
                driver.execute_script("window.scrollBy(0, 200)")
                driver.sleep(1)
        
        if copy_button:
            print("[PERPLEXITY] ✓ Copy button found, attempting to click...")
            
            # Ensure button is in view and clickable
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", copy_button)
            driver.sleep(2)  # Longer wait for scroll to complete
            
            # Try clicking with multiple approaches
            click_success = False
            
            # Method 1: Normal click
            try:
                copy_button.click()
                copy_button.click()  # Double click as in original
                click_success = True
                print("[PERPLEXITY] ✓ Copy button clicked successfully (normal click)")
            except Exception as click_error:
                print(f"[PERPLEXITY] Normal click failed: {click_error}")
            
            # Method 2: JavaScript click (fallback)
            if not click_success:
                try:
                    driver.execute_script("arguments[0].click();", copy_button)
                    driver.execute_script("arguments[0].click();", copy_button)
                    click_success = True
                    print("[PERPLEXITY] ✓ Copy button clicked successfully (JavaScript click)")
                except Exception as js_error:
                    print(f"[PERPLEXITY] JavaScript click failed: {js_error}")
            
            # Method 3: Force click event (final fallback)
            if not click_success:
                try:
                    driver.execute_script("""
                    arguments[0].dispatchEvent(new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        view: window
                    }));
                    """, copy_button)
                    click_success = True
                    print("[PERPLEXITY] ✓ Copy button clicked successfully (forced event)")
                except Exception as force_error:
                    print(f"[PERPLEXITY] Forced click failed: {force_error}")
            
            if not click_success:
                raise Exception("All copy button click methods failed")
            
            # Wait longer for clipboard operation to complete
            driver.sleep(3)
            
            # Get clipboard content with retry
            clipboard_content = None
            for clipboard_attempt in range(3):
                try:
                    clipboard_content = driver.execute_script("return navigator.clipboard.readText()")
                    if clipboard_content:
                        break
                    driver.sleep(1)
                except Exception as e:
                    print(f"[PERPLEXITY] Clipboard read attempt {clipboard_attempt + 1} failed: {e}")
                    if clipboard_attempt < 2:
                        driver.sleep(1)
            
            if clipboard_content:
                cleaned_citation_string = clipboard_content.split("[1]")[0] if clipboard_content else ""
                print(f"[PERPLEXITY] ✓ Successfully retrieved clipboard content ({len(cleaned_citation_string)} chars)")
                return cleaned_citation_string
            else:
                raise Exception("Failed to read clipboard content after multiple attempts")
        else:
            raise Exception(f"No copy button found after {max_copy_attempts} attempts")
    except Exception as e:
        print(f"[PERPLEXITY] Error getting result: {e}")
        raise


def run_perplexity(prompt: str, use_research_mode: bool = False):
    """Main function to run Perplexity query using SeleniumBase"""
    driver = None
    try:
        # Initialize driver with UC mode and Chrome debug profile
        profile_dir = os.getenv("CHROME_PROFILE_DIR", "./chrome-debug-4")
        driver = Driver(
            uc=True, 
            headless=False,
            user_data_dir=profile_dir  # Use chrome debug profile
        )
        
        # Navigate to Perplexity with UC mode
        print("[PERPLEXITY] Opening Perplexity.ai with Cloudflare bypass...")
        driver.uc_open_with_reconnect("https://perplexity.ai", reconnect_time=6)
        
        # Wait for any challenges to complete
        driver.sleep(10)
        
        # Input the prompt and get result
        inputFieldCheck(driver, prompt, use_research_mode)        
        aiOutput = getResult(driver)
        
        print("[PERPLEXITY] ✓ Successfully retrieved AI response")
        print(f"[PERPLEXITY] Response length: {len(aiOutput)} characters")
        
        return aiOutput
        
    except Exception as e:
        print(f"[PERPLEXITY] Error in run_perplexity: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")
    finally:
        if driver:
            driver.quit()


@app.post("/ask")
def ask_perplexity(request: PromptRequest):
    """API endpoint to ask Perplexity a question"""
    try:
        result = run_perplexity(request.prompt, request.use_research_mode)
        return {"response": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def root():
    """Root endpoint"""
    return {"message": "Perplexity AI API with SeleniumBase is running"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)  # Using port 8001 to avoid conflict