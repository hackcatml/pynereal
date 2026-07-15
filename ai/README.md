# AI Integration

This directory contains the shared instructions, workflows, scripts, and data
contracts used when an LLM CLI evaluates a running PyneReal session.

## Layout

- `INSTRUCTIONS.md`: common rules for every supported LLM CLI
- `workflows/`: task-specific procedures and required evidence
- `scripts/`: deterministic data collection tools
- `schemas/`: JSON contracts produced by the scripts

CLI-specific entry files such as the repository `AGENTS.md` should stay small
and point to `INSTRUCTIONS.md`. Do not duplicate the common rules in multiple
CLI-specific files.
