#!/usr/bin/env node
/**
 * AgentHive — cross-platform installer (Linux + macOS)
 *
 * Run directly:   node install.js
 * Or via curl:    curl -fsSL <url>/install.sh | bash
 *                 (the bash bootstrap ensures node exists, then runs this)
 */

const { execSync, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');
const readline = require('readline');

// ── Helpers ────────────────────────────���─────────────────────────────

const BOLD = '\x1b[1m';
const GREEN = '\x1b[32m';
const YELLOW = '\x1b[33m';
const RED = '\x1b[31m';
const RESET = '\x1b[0m';

function info(msg) { console.log(`${GREEN}[+]${RESET} ${msg}`); }
function warn(msg) { console.log(`${YELLOW}[!]${RESET} ${msg}`); }
function fail(msg) { console.error(`${RED}[x]${RESET} ${msg}`); process.exit(1); }
function run(cmd, opts = {}) {
  try {
    return execSync(cmd, { stdio: opts.quiet ? 'pipe' : 'inherit', encoding: 'utf8', ...opts });
  } catch (e) {
    if (!opts.allowFail) fail(`Command failed: ${cmd}\n${e.message}`);
    return null;
  }
}
function which(cmd) {
  try { return execSync(`which ${cmd}`, { encoding: 'utf8' }).trim(); } catch { return null; }
}
function ask(question, defaultVal) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => {
    rl.question(`  ${question} [${defaultVal}]: `, answer => {
      rl.close();
      resolve(answer.trim() || defaultVal);
    });
  });
}

const PLATFORM = os.platform();  // 'linux' or 'darwin'
const ROOT = path.dirname(__filename);

// ── Main ─────��───────────────────────────────────────────────────────

async function main() {
  console.log(`\n  ${BOLD}===============================${RESET}`);
  console.log(`  ${BOLD}     AgentHive Installer${RESET}`);
  console.log(`  ${BOLD}===============================${RESET}`);
  console.log(`  Platform: ${PLATFORM === 'darwin' ? 'macOS' : 'Linux'}\n`);

  // ── 1. System dependencies ──────────────────────────────────────
  const missing = [];
  if (!which('python3')) missing.push('python3');
  if (!which('tmux')) missing.push('tmux');
  if (!which('openssl')) missing.push('openssl');
  if (!which('git')) missing.push('git');

  if (missing.length) {
    warn(`Missing system dependencies: ${missing.join(', ')}`);
    if (PLATFORM === 'linux') {
      info('Installing via apt-get...');
      run('sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv tmux openssl git');
    } else if (PLATFORM === 'darwin') {
      if (!which('brew')) {
        fail('Homebrew not found. Install it first: https://brew.sh');
      }
      info('Installing via brew...');
      run(`brew install ${missing.join(' ')}`);
    }
    info('System dependencies installed');
  } else {
    info('System dependencies OK');
  }

  // ── 2. Claude Code CLI ──────────────────────────────────────────
  if (!which('claude')) {
    warn('Claude Code CLI not found — installing...');
    run('npm install -g @anthropic-ai/claude-code');
    info('Claude Code CLI installed');
  } else {
    info('Claude Code CLI OK');
  }

  // ── 3. pm2 ────────────��────────────────────────────────────────
  if (!which('pm2')) {
    info('Installing pm2...');
    run('npm install -g pm2');
    info('pm2 installed');
  } else {
    info('pm2 OK');
  }

  // ── 4. Python virtual environment ──────��────────────────────────
  const venvDir = path.join(ROOT, '.venv');
  if (!fs.existsSync(venvDir)) {
    info('Creating Python virtual environment...');
    run(`python3 -m venv "${venvDir}"`);
  }
  const pip = path.join(venvDir, 'bin', 'pip');
  info('Installing Python dependencies...');
  run(`"${pip}" install -q -r "${path.join(ROOT, 'orchestrator', 'requirements.txt')}"`);
  info('Python dependencies installed');

  // ── 5. Frontend dependencies ─────────��──────────────────────────
  info('Installing frontend dependencies...');
  run('npm install', { cwd: path.join(ROOT, 'frontend') });
  info('Frontend dependencies installed');

  // ── 6. Configuration (.env) ─────────────────────────────────────
  const envFile = path.join(ROOT, '.env');
  const defaultProjectsDir = path.join(os.homedir(), 'agenthive-projects');

  if (!fs.existsSync(envFile)) {
    const projectsDir = await ask('Projects directory', defaultProjectsDir);
    // Copy template and set HOST_PROJECTS_DIR
    let envContent = fs.readFileSync(path.join(ROOT, '.env.example'), 'utf8');
    envContent = envContent.replace(
      /^HOST_PROJECTS_DIR=.*$/m,
      `HOST_PROJECTS_DIR=${projectsDir}`
    );
    fs.writeFileSync(envFile, envContent);
    info(`Created .env (HOST_PROJECTS_DIR=${projectsDir})`);

    // Create projects directory
    fs.mkdirSync(projectsDir, { recursive: true });
    info(`Created projects directory: ${projectsDir}`);
  } else {
    info('.env already exists');
  }

  // ── 7. Required directories ─────────────────────────────────────
  for (const dir of ['data', 'logs', 'backups', 'project-configs']) {
    fs.mkdirSync(path.join(ROOT, dir), { recursive: true });
  }

  // ── 8. SSL certificates ───────────────────────────���─────────────
  const certsDir = path.join(ROOT, 'certs');
  if (!fs.existsSync(path.join(certsDir, 'selfsigned.crt'))) {
    fs.mkdirSync(certsDir, { recursive: true });

    // Get LAN IP (cross-platform)
    let lanIp = '127.0.0.1';
    if (PLATFORM === 'linux') {
      const r = spawnSync('hostname', ['-I'], { encoding: 'utf8' });
      if (r.status === 0) lanIp = r.stdout.trim().split(/\s+/)[0] || '127.0.0.1';
    } else {
      const r = spawnSync('ipconfig', ['getifaddr', 'en0'], { encoding: 'utf8' });
      if (r.status === 0 && r.stdout.trim()) lanIp = r.stdout.trim();
    }

    info(`Generating SSL certificates (LAN IP: ${lanIp})...`);
    run(
      `openssl req -x509 -nodes -days 365 -newkey rsa:2048 ` +
      `-keyout "${path.join(certsDir, 'selfsigned.key')}" ` +
      `-out "${path.join(certsDir, 'selfsigned.crt')}" ` +
      `-subj "/CN=agenthive" ` +
      `-addext "subjectAltName=DNS:agenthive,DNS:localhost,IP:127.0.0.1,IP:${lanIp}"`,
      { quiet: true }
    );
    info('SSL certificates generated');

    // Trust certificate system-wide
    if (PLATFORM === 'linux' && fs.existsSync('/usr/local/share/ca-certificates')) {
      info('Trusting certificate (requires sudo)...');
      run(`sudo cp "${path.join(certsDir, 'selfsigned.crt')}" /usr/local/share/ca-certificates/agenthive.crt`, { allowFail: true });
      run('sudo update-ca-certificates', { allowFail: true, quiet: true });
    } else if (PLATFORM === 'darwin') {
      info('Trusting certificate (requires sudo)...');
      run(
        `sudo security add-trusted-cert -d -r trustRoot ` +
        `-k /Library/Keychains/System.keychain ` +
        `"${path.join(certsDir, 'selfsigned.crt')}"`,
        { allowFail: true }
      );
    }
  } else {
    info('SSL certificates already exist');
  }

  // ── 9. Project registry ─────────────────────────────────────────
  const registryFile = path.join(ROOT, 'project-configs', 'registry.yaml');
  const registryExample = path.join(ROOT, 'project-configs', 'registry.yaml.example');
  if (!fs.existsSync(registryFile) && fs.existsSync(registryExample)) {
    fs.copyFileSync(registryExample, registryFile);
    info('Created project registry from template');
  }

  // ── Done ───────────���────────────────────────��───────────────────
  console.log(`\n  ${BOLD}=======================================${RESET}`);
  console.log(`  ${BOLD}  Setup complete!${RESET}`);
  console.log();
  console.log('  Next steps:');
  console.log(`    1. Start:    ${BOLD}./run.sh${RESET}`);
  console.log(`    2. Open:     ${BOLD}https://localhost:3000${RESET}`);
  console.log(`    3. On boot:  ${BOLD}./run.sh startup${RESET}`);
  console.log(`  ${BOLD}=======================================${RESET}\n`);
}

main().catch(e => { fail(e.message); });
