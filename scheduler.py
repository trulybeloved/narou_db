import os
import subprocess
import time
from pathlib import Path

# Path to the script you want to run
SCRIPT_PATH = "scrape_update.py"

VENV_PYTHON = os.path.join(os.getcwd(), 'venv', 'Scripts', 'python.exe')
LOCK_FILE = Path(os.path.join( os.getcwd(), "scrape_update.lock") ) # Path to lock file

def run_script():
    if LOCK_FILE.exists():
        print("Another instance is running. Skipping execution.")
        return

    try:
        # Create a lock file
        LOCK_FILE.touch(exist_ok=True)

        # Run the script
        print("Starting the script...")
        subprocess.run([VENV_PYTHON, SCRIPT_PATH], check=True)
        print("Script execution completed.")
    except subprocess.CalledProcessError as e:
        print(f"Script execution failed: {e}")
    finally:
        # Remove the lock file
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()

# Schedule the task
import schedule
schedule.every(15).minutes.do(run_script)  # Adjust the interval as needed

if __name__ == "__main__":
    print("Scheduler is starting...")
    if not LOCK_FILE.parent.exists():
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Run the script immediately upon first start
    print("Running the script immediately on startup...")
    run_script()

    # Start the scheduler
    while True:
        schedule.run_pending()
        time.sleep(1)
