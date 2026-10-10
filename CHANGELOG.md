# Changelog

All notable changes are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Ollama backend with streaming, tool calling and a text tool-call fallback.
- Agent loop with a step limit and size-capped tool results.
- Rule tools (list, read, validate, rule type help) and read-only Elasticsearch tools (indices, mapping, sample docs).
- Rule validator built on ElastAlert 2's own loader, with hints for common gaps.
- Tkinter GUI: chat, rule editor with validation, tool-call trace.
- Example rules, including deliberately flawed ones.
