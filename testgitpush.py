import os
from custom_modules.utilities import Git
repo = os.getcwd()
Git.git_commit_all(repo, f'test commitex')
Git.git_push(repo, branch_name='master')