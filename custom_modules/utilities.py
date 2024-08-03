from datetime import datetime, timezone, timedelta
import time
import git
import backoff

def convert_to_unix_timestamp(timestamp_str: str, offset_hours: float) -> int:
    # Define the format of the input timestamp
    date_format = "%Y/%m/%d %H:%M"

    # Parse the timestamp string into a datetime object
    dt = datetime.strptime(timestamp_str, date_format)

    # Create a timezone offset
    offset = timedelta(hours=offset_hours)

    # Set the datetime object to the specified timezone (UTC+9)
    dt_with_offset = dt.replace(tzinfo=timezone(offset))

    # Convert the datetime object to UTC
    dt_utc = dt_with_offset.astimezone(timezone.utc)

    # Convert the datetime object to a Unix timestamp
    unix_timestamp = int(dt_utc.timestamp())

    return unix_timestamp

def get_current_unix_timestamp(mode: int = 1) -> int:
    current_time = datetime.utcnow()
    if mode == 1:
        current_time = datetime.fromtimestamp(timestamp=time.time())
    return int(current_time.timestamp())

class Git:

    @staticmethod
    @backoff.on_exception(
        backoff.expo,
        exception=git.GitCommandError,
        max_tries=3,
        max_time=30
    )
    def git_commit_all(repository_path, commit_message):
        try:
            repo = git.Repo(repository_path)
            repo.git.add(all=True)
            repo.index.commit(commit_message)
            print("Committed all changes successfully.")
        except git.GitCommandError as e:
            print("Error:", e)

    @staticmethod
    @backoff.on_exception(
        backoff.expo,
        exception=git.GitCommandError,
        max_tries=3,
        max_time=30
    )
    def git_push(repository_path, branch_name):
        try:
            repo = git.Repo(repository_path)
            origin = repo.remotes.origin
            origin.push(refspec=branch_name)
            print("Pushed changes successfully.")
        except git.GitCommandError as e:
            print("Error:", e)