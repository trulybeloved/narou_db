const path = require('node:path');

module.exports = {
  apps: [
    {
      name: 'narou-updater',
      cwd: __dirname,
      script: 'autoscrape_autoupdate.py',
      interpreter: path.join(
        __dirname,
        '.venv',
        process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
      ),
      exec_mode: 'fork',
      instances: 1,
      watch: false,
      autorestart: true,
      // Leave orderly exits and reported safety/configuration failures stopped.
      stop_exit_codes: [0, 1, 2, 3],
      restart_delay: 60000,
      kill_timeout: 50000,
      // Let the supervisor finish or terminate its own worker during shutdown.
      treekill: false,
      env: {
        PYTHONUNBUFFERED: '1',
      },
    },
  ],
};
