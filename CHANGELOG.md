# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The next planned version is `0.1.0`（未发布）. This repository-foundation release
remains unpublished until creation of a GitHub tag is explicitly authorized.

### Added

- Repository governance, contribution, security, privacy, and professional-review boundaries.
- Deterministic CSV intake, mapping, data-quality gates, initial rule evaluation, and traceable JSON/JSONL and Markdown outputs.
- A fully fictional, balanced CSV example and local runner for the deterministic core.
- A public roadmap that separates completed repository-foundation work from planned public-v1 capabilities.

### Changed

- Business-entertainment rule `CIT-BE-001` advances to `2026.2`: the ledger revenue basis recognizes main-business and other-business revenue accounts and explicitly remains incomplete until reconciled to the annual CIT return and deemed-sales revenue.
- CSV intake now fails closed on directories, symbolic links, and other unsupported top-level entries without following or hashing their contents.
