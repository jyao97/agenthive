# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | Yes                |

Only the latest release receives security updates.

## Reporting a Vulnerability

If you discover a security vulnerability in AgentHive, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please open a [private security advisory](https://github.com/jyao97/AgentHive/security/advisories/new) on this repository.

### What to Include

- A clear description of the vulnerability
- Steps to reproduce the issue
- The potential impact (e.g., data exposure, privilege escalation, denial of service)
- Any suggested fix or mitigation, if you have one
- Your environment details (OS, Python version, browser, etc.)

### Response Timeline

- **Acknowledgment:** Within 48 hours of your report
- **Status update:** Within 7 days, including an initial assessment and expected timeline for a fix
- **Fix release:** As soon as a patch is ready, coordinated with the reporter when possible

## Self-Hosted Deployment

AgentHive is a self-hosted tool. Users are responsible for securing their own deployments, including:

- Keeping the host OS and dependencies up to date
- Restricting network access (e.g., firewall rules, Tailscale)
- Managing SSL certificates and authentication credentials
- Securing the `.env` file and any API keys it contains
- Setting appropriate file permissions on the SQLite database and session data

We strongly recommend running AgentHive behind a VPN (such as Tailscale) rather than exposing it to the public internet.
