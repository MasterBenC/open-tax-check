# Multi-Store Skill Distribution Implementation Plan

**Status:** Deferred by user on 2026-08-02. Do not execute this plan until the user explicitly resumes multi-store distribution work.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce one verifiable OpenTaxCheck Skill release that can be wrapped for WorkBuddy, Qoder, Tencent SkillHub, Coze, Codex, Claude Code, OpenClaw, and GitHub-installed Hermes without copying tax logic.

**Architecture:** Keep the canonical Skill under `skills/open-tax-check`, add versioned model and distribution manifests, and generate channel packages from that canonical tree. A small distribution builder verifies SHA-256, channel metadata, Python compatibility, and a fictional self-test; stores remain replaceable delivery channels.

**Tech Stack:** Python 3.11–3.13, JSON schemas, SHA-256, ZIP archives, pathlib, existing package/release validators, pytest.

## Global Constraints

- The canonical Skill is the only business source; channel packages contain no tax formulas or copied rule packs.
- GitHub is Hermes' primary source and only a fallback for other hosts.
- Store publication requires the user's platform accounts and remains a manual external action.
- Recommended models are DeepSeek V4, Tencent Hunyuan 3, GLM-5.2, GPT-5.6, Claude 4.8+, Qwen 3.7 Max, and Doubao Seed 2.0 Pro.
- `qwen3.7-plus` is an accepted balanced-cost option; preview models are not default recommendations.
- Model choice never changes deterministic calculations, facts, confirmation audit, or case status.
- First release integrity uses SHA-256 and publisher identity; no custom signing service is added.
- No push, merge, tag, GitHub Release, or external store publication is part of this plan.

---

### Task 1: Add Versioned Model Recommendations

**Files:**
- Create: `src/open_tax_check/model_recommendations.json`
- Create: `src/open_tax_check/model_recommendations.py`
- Create: `tests/test_model_recommendations.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `load_model_recommendations() -> ModelRecommendationSet`
- Produces: `recommend_ai_mode(model_name: str | None, capabilities: Mapping[str, bool]) -> Literal["deep", "assist", "off"]`
- Schema: `model-recommendations/v1`.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_recommendations_contain_approved_formal_models():
    config = load_model_recommendations()
    assert [item.display_name for item in config.models] == [
        "DeepSeek V4", "腾讯混元 3（HY3）", "GLM-5.2", "GPT-5.6",
        "Claude 4.8 及以上", "Qwen 3.7 Max", "Doubao Seed 2.0 Pro"
    ]
    assert "qwen3.8-max-preview" not in config.identifiers
    assert "qwen3.7-plus" in config.balanced_alternatives
```

- [ ] **Step 2: Write failing capability-degradation tests**

```python
def test_unknown_model_with_full_capabilities_uses_assist_not_deep():
    assert recommend_ai_mode("other-model", full_capabilities()) == "assist"


def test_missing_structured_output_or_tools_uses_off():
    assert recommend_ai_mode("GPT-5.6", full_capabilities() | {"structured_json": False}) == "off"
```

- [ ] **Step 3: Run tests and verify RED**

Run: `python -m pytest tests/test_model_recommendations.py -q`

Expected: FAIL because the configuration and loader are absent.

- [ ] **Step 4: Implement the exact schema and loader**

```json
{
  "schema_version": "model-recommendations/v1",
  "models": [
    {"family": "deepseek", "minimum": "4", "display_name": "DeepSeek V4"},
    {"family": "hunyuan", "minimum": "3", "display_name": "腾讯混元 3（HY3）"},
    {"family": "glm", "minimum": "5.2", "display_name": "GLM-5.2"},
    {"family": "gpt", "minimum": "5.6", "display_name": "GPT-5.6"},
    {"family": "claude", "minimum": "4.8", "display_name": "Claude 4.8 及以上"},
    {"family": "qwen", "minimum": "3.7-max", "display_name": "Qwen 3.7 Max"},
    {"family": "doubao-seed", "minimum": "2.0-pro", "display_name": "Doubao Seed 2.0 Pro"}
  ],
  "balanced_alternatives": ["qwen3.7-plus"],
  "required_capabilities": ["structured_json", "chinese_long_context", "file_tools", "terminal_tools", "fact_state_discipline"]
}
```

Use package resources to load the JSON and reject unknown fields, duplicate families, malformed versions, and preview models in the formal list.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m pytest tests/test_model_recommendations.py tests/test_package_contract.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/open_tax_check/model_recommendations.json src/open_tax_check/model_recommendations.py tests/test_model_recommendations.py pyproject.toml
git commit -m "feat: add model recommendation contract"
```

### Task 2: Build the Canonical Distribution Manifest

**Files:**
- Create: `src/open_tax_check/skill_distribution.py`
- Create: `scripts/build_skill_distribution.py`
- Create: `tests/test_skill_distribution.py`
- Create: `distribution/manifest.json`

**Interfaces:**
- Produces: `build_distribution_manifest(repo_root: Path, version: str) -> dict[str, object]`
- Produces: `verify_distribution_manifest(repo_root: Path, payload: Mapping[str, object]) -> tuple[str, ...]`
- CLI: `python scripts/build_skill_distribution.py --output DIST_DIR`.

- [ ] **Step 1: Write failing deterministic-manifest tests**

```python
def test_manifest_hashes_every_canonical_skill_file(repo_root):
    manifest = build_distribution_manifest(repo_root, "1.0.0")
    assert manifest["schema_version"] == "opentaxcheck-distribution/v1"
    assert manifest["python_requires"] == ">=3.11,<3.14"
    assert manifest["formal_period"] == {"start": "2023-01-01", "end": "2026-12-31"}
    assert {item["path"] for item in manifest["canonical_files"]} == canonical_skill_files(repo_root)
    assert all(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) for item in manifest["canonical_files"])
```

- [ ] **Step 2: Write failing tamper and link tests**

Verify that one changed byte produces `canonical_file_hash_mismatch`, an unknown channel produces `unknown_distribution_channel`, and a symlink/reparse point produces `unsafe_distribution_source`.

- [ ] **Step 3: Run tests and verify RED**

Run: `python -m pytest tests/test_skill_distribution.py -q`

Expected: FAIL because the builder does not exist.

- [ ] **Step 4: Implement canonical traversal and manifest generation**

```python
CHANNELS = ("universal", "workbuddy", "qoder", "skillhub", "coze", "codex", "claude-code", "openclaw", "hermes")


def canonical_skill_files(repo_root: Path) -> tuple[Path, ...]:
    root = repo_root / "skills" / "open-tax-check"
    return tuple(sorted(
        (path for path in root.rglob("*") if path.is_file() and not path.is_symlink()),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    ))
```

The committed `distribution/manifest.json` is generated from the canonical tree and contains publisher identity, core version, rule-pack version source, model configuration version, permissions, channel versions, and self-test ID.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python -m pytest tests/test_skill_distribution.py -q`

Expected: PASS and repeated generation produces byte-identical JSON.

- [ ] **Step 6: Commit**

```bash
git add src/open_tax_check/skill_distribution.py scripts/build_skill_distribution.py tests/test_skill_distribution.py distribution/manifest.json
git commit -m "feat: add canonical skill distribution manifest"
```

### Task 3: Generate Thin Channel Packages and Hermes GitHub Install Card

**Files:**
- Create: `distribution/workbuddy/channel.json`
- Create: `distribution/qoder/channel.json`
- Create: `distribution/skillhub/channel.json`
- Create: `distribution/coze/channel.json`
- Create: `distribution/codex/channel.json`
- Create: `distribution/claude-code/channel.json`
- Create: `distribution/openclaw/channel.json`
- Create: `distribution/hermes/channel.json`
- Create: `distribution/hermes/INSTALL.zh-CN.md`
- Modify: `src/open_tax_check/skill_distribution.py`
- Modify: `tests/test_skill_distribution.py`

**Interfaces:**
- Produces archives: `open-tax-check-<version>-<channel>.zip`
- Channel schema: `opentaxcheck-channel/v1`.

- [ ] **Step 1: Write failing channel-package tests**

```python
@pytest.mark.parametrize("channel", [
    "workbuddy", "qoder", "skillhub", "coze", "codex", "claude-code", "openclaw", "hermes"
])
def test_channel_archive_contains_canonical_skill_and_thin_metadata(tmp_path, channel, repo_root):
    archive = build_channel_archive(repo_root, channel, tmp_path)
    members = zip_members(archive)
    assert "open-tax-check/SKILL.md" in members
    assert f"channel/{channel}.json" in members
    assert not any("tax_packs/" in member for member in members)
```

- [ ] **Step 2: Add failing Hermes-source assertions**

```python
def test_hermes_uses_github_as_primary_source():
    payload = load_channel("hermes")
    assert payload["primary_source"] == "https://github.com/MasterBenC/open-tax-check"
    assert payload["store"] is None
    assert "虚构案例自检" in Path("distribution/hermes/INSTALL.zh-CN.md").read_text("utf-8")
```

- [ ] **Step 3: Run tests and verify RED**

Run: `python -m pytest tests/test_skill_distribution.py -q`

Expected: FAIL because channel metadata and archive generation are absent.

- [ ] **Step 4: Implement allowlisted channel metadata**

Each `channel.json` contains only schema version, channel, display name, publisher, core manifest digest, install mode, primary source, fallback sources, permissions, and validation command. Coze uses `adapter` install mode; Hermes uses `github`; store platforms use `marketplace`; local platforms use `local_skill`.

- [ ] **Step 5: Generate deterministic ZIP archives**

Normalize member paths, timestamps, permissions, ordering, and compression. Reject channel files that contain Python tax logic, rule JSON, credentials, executable binaries, or external absolute paths.

- [ ] **Step 6: Run tests and verify GREEN**

Run: `python -m pytest tests/test_skill_distribution.py tests/test_host_replays.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add distribution src/open_tax_check/skill_distribution.py tests/test_skill_distribution.py
git commit -m "feat: generate multi-store skill packages"
```

### Task 4: Add Installation Self-Test and Chinese Result Contract

**Files:**
- Create: `src/open_tax_check/install_self_test.py`
- Create: `tests/test_install_self_test.py`
- Modify: `src/open_tax_check/cli.py`
- Modify: `scripts/build_skill_distribution.py`

**Interfaces:**
- CLI: `opentaxcheck skill-self-test --manifest MANIFEST --skill-root SKILL_ROOT`
- Produces schema: `opentaxcheck-install-check/v1`.

- [ ] **Step 1: Write failing success-contract test**

```python
def test_self_test_reports_versions_permissions_and_fictional_case(tmp_path):
    result = run_install_self_test(valid_install(tmp_path))
    assert result == {
        "schema_version": "opentaxcheck-install-check/v1",
        "status": "passed",
        "skill_version": __version__,
        "rule_pack_status": "verified",
        "python_status": "supported",
        "file_access": "verified",
        "terminal_access": "verified",
        "fictional_case": "passed",
        "error_codes": [],
    }
```

- [ ] **Step 2: Write failing error-classification tests**

Parameterize `store_not_found`, `version_too_old`, `python_incompatible`, `file_permission_missing`, `terminal_permission_missing`, `dependency_install_failed`, `manifest_hash_mismatch`, and `fictional_case_failed`. Ensure no failure payload contains `"status": "passed"`.

- [ ] **Step 3: Run tests and verify RED**

Run: `python -m pytest tests/test_install_self_test.py -q`

Expected: FAIL because the self-test module and command do not exist.

- [ ] **Step 4: Implement self-test without customer data**

```python
@dataclass(frozen=True, slots=True)
class InstallSelfTestRequest:
    manifest: Path
    skill_root: Path
    require_terminal: bool = True


def run_install_self_test(request: InstallSelfTestRequest) -> dict[str, object]:
    issues = verify_installation(request)
    fictional = run_bundled_minimal_case() if not issues else "not_run"
    return install_check_payload(issues, fictional)
```

Use a temporary directory and bundled fictional request. Do not inspect user home broadly and do not read any customer case directory.

- [ ] **Step 5: Add CLI and run GREEN tests**

Run: `python -m pytest tests/test_install_self_test.py tests/test_tax_health_cli.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/open_tax_check/install_self_test.py src/open_tax_check/cli.py scripts/build_skill_distribution.py tests/test_install_self_test.py
git commit -m "feat: add skill installation self-test"
```

### Task 5: Integrate Distribution Assets with Release Validation

**Files:**
- Modify: `scripts/validate_release.py`
- Modify: `tests/test_release_validation.py`
- Modify: `tests/test_package_contract.py`
- Modify: `MANIFEST.in`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `skills/open-tax-check/references/host-integrations.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Extends strict release validation with distribution manifest, channel archive, model configuration, and self-test requirements.

- [ ] **Step 1: Write failing release-surface tests**

```python
def test_strict_release_requires_model_and_distribution_contracts(release_root):
    result = validate_release(release_root, strict=True, require_distributions=True)
    assert result.error is None
    remove(release_root / "distribution/manifest.json")
    assert validate_release(release_root, strict=True).error == "distribution_manifest_missing"
```

Also require channel metadata for all eight channels, a Hermes install card, model recommendation package data, and deterministic archive hashes.

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_release_validation.py tests/test_package_contract.py -q`

Expected: FAIL because strict validation does not know the new assets.

- [ ] **Step 3: Extend packaging and validation allowlists**

Add `model_recommendations.json` to package data. Add public distribution JSON and Markdown files to the sdist manifest; generated ZIP archives remain build artifacts and are not imported by Python. Validate actual byte sizes, safe paths, hashes, schema versions, and absence of secrets using existing release-validator patterns.

- [ ] **Step 4: Document store-neutral installation**

README must state:

```text
在 WorkBuddy、Qoder 或腾讯 SkillHub 中，请让当前 AI 智能体搜索并安装 OpenTaxCheck，然后运行安装自检。
Hermes 从 MasterBenC/open-tax-check 获取，并在处理客户资料前完成哈希和虚构案例验证。
```

Clarify that store availability is channel-specific, GitHub is not the global primary source, and package generation is not proof of store publication.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_release_validation.py tests/test_package_contract.py tests/test_skill_distribution.py tests/test_install_self_test.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/validate_release.py tests/test_release_validation.py tests/test_package_contract.py MANIFEST.in pyproject.toml README.md skills/open-tax-check/references/host-integrations.md CHANGELOG.md
git commit -m "build: validate multi-store skill release"
```

### Task 6: Build, Isolate, Replay, and Prepare Manual Store Submission

**Files:**
- Create: `docs/distribution/store-submission-checklist.zh-CN.md`
- Modify: `scripts/replay_host_integrations.py`
- Modify: `tests/test_host_replays.py`

**Interfaces:**
- Produces a manual checklist with artifact names, versions, hashes, permissions, screenshots/descriptions, and platform runtime-verification status.

- [ ] **Step 1: Add failing channel replay expectations**

Extend the replay result with `distribution_status`, `manifest_sha256`, and `self_test_status`. Assert Hermes reports `source=github`, WorkBuddy/Qoder/SkillHub report `source=marketplace`, and Coze reports `source=adapter` without claiming runtime verification.

- [ ] **Step 2: Run replay tests and verify RED**

Run: `python -m pytest tests/test_host_replays.py -q`

Expected: FAIL because channel distribution fields are absent.

- [ ] **Step 3: Implement channel-aware offline replay**

Load each channel package, verify its core manifest digest, install into an isolated staging root, run `skill-self-test`, and preserve `runtime_validation="pending"` unless a real platform evidence record exists.

- [ ] **Step 4: Run the full repository suite**

Run: `python -m pytest -q`

Expected: all tests pass with only established optional-environment skips.

- [ ] **Step 5: Build and validate distributions**

Run: `python -m build`

Run: `python scripts/build_skill_distribution.py --output dist/skills`

Run: `python scripts/validate_release.py --strict --require-distributions`

Expected: wheel, sdist, universal archive, and eight channel archives are built; strict validation passes.

- [ ] **Step 6: Validate an isolated wheel installation**

Create a clean Python 3.12 environment, install the wheel with Excel extras, run `opentaxcheck skill-self-test`, run the complete fictional example, and confirm imported modules come from that environment's `site-packages`.

- [ ] **Step 7: Write the manual submission checklist**

For each store, record package path, SHA-256, publisher identity, permissions, Chinese description, privacy disclosure, self-test result, and `runtime_verified` status. Do not include platform credentials or claim that an unpublished package is available in the store.

- [ ] **Step 8: Commit**

```bash
git add docs/distribution/store-submission-checklist.zh-CN.md scripts/replay_host_integrations.py tests/test_host_replays.py
git commit -m "docs: prepare skill store submissions"
```
