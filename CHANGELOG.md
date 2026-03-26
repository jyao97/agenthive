# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2025-01-01

### Added

- Multi-agent orchestration with tmux-based sessions
- Real-time WebSocket communication for live agent output streaming
- Project management with git integration and isolated worktrees per agent
- Voice input support via OpenAI Whisper for hands-free task creation
- Mobile-responsive PWA interface with Add to Home Screen support
- Task management inbox with drag-to-reorder priorities
- Agent coordination with configurable concurrency limits and timeouts
- Session persistence and JSONL-based history with crash recovery
- Push notifications for agent status changes (finish, error, needs input)
- Password authentication with rate limiting and inactivity-based session lock
- HTTPS with self-signed certificate generation for LAN encryption
- System monitor for disk, memory, and GPU usage
- CLI session sync (read-only import and live-tail of terminal sessions)
- Dark/light theme with system-aware toggle
- Automatic hourly database backups
