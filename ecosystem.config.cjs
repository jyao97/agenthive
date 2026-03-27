// AgentHive — pm2 process configuration
// Usage:  pm2 start ecosystem.config.cjs
//         pm2 stop   ecosystem.config.cjs
//         pm2 logs

const path = require('path');
const fs = require('fs');

const ROOT = __dirname;
const VENV_UVICORN = path.join(ROOT, '.venv', 'bin', 'uvicorn');
const ENV_FILE = path.join(ROOT, '.env');

// Load .env into a plain object for pm2 env injection
function loadDotenv(filePath) {
  const env = {};
  if (!fs.existsSync(filePath)) return env;
  for (const line of fs.readFileSync(filePath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq < 1) continue;
    const key = trimmed.slice(0, eq).trim();
    let val = trimmed.slice(eq + 1).trim();
    // Strip surrounding quotes
    if ((val.startsWith('"') && val.endsWith('"')) ||
        (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    env[key] = val;
  }
  return env;
}

const dotenv = loadDotenv(ENV_FILE);
const port = dotenv.PORT || '8080';
const frontendPort = dotenv.FRONTEND_PORT || '3000';

module.exports = {
  apps: [
    {
      name: 'agenthive-backend',
      cwd: path.join(ROOT, 'orchestrator'),
      script: VENV_UVICORN,
      args: `main:app --host 0.0.0.0 --port ${port}`,
      interpreter: 'none',  // uvicorn is its own binary
      env: {
        ...dotenv,
        PROJECTS_DIR: dotenv.HOST_PROJECTS_DIR || path.join(require('os').homedir(), 'agenthive-projects'),
        DB_PATH: path.join(ROOT, 'data', 'orchestrator.db'),
        LOG_DIR: path.join(ROOT, 'logs'),
        BACKUP_DIR: path.join(ROOT, 'backups'),
        PROJECT_CONFIGS_PATH: path.join(ROOT, 'project-configs'),
        AGENTHIVE_MANAGED: '1',
      },
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      log_file: path.join(ROOT, 'logs', 'backend-pm2.log'),
      error_file: path.join(ROOT, 'logs', 'backend-pm2-error.log'),
      merge_logs: true,
    },
    {
      name: 'agenthive-frontend',
      cwd: path.join(ROOT, 'frontend'),
      script: 'npx',
      args: `vite --host 0.0.0.0 --port ${frontendPort}`,
      interpreter: 'none',
      autorestart: true,
      max_restarts: 10,
      restart_delay: 3000,
      log_file: path.join(ROOT, 'logs', 'frontend-pm2.log'),
      error_file: path.join(ROOT, 'logs', 'frontend-pm2-error.log'),
      merge_logs: true,
    },
  ],
};
