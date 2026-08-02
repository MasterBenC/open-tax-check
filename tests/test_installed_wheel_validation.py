from pathlib import Path


def test_wheel_gate_accepts_only_module_loaded_from_isolated_site_packages(tmp_path: Path):
    from scripts.validate_installed_wheel import module_origin_is_isolated

    environment = tmp_path / "venv"
    repository = tmp_path / "repository"
    site_package = environment / "Lib/site-packages/open_tax_check/__init__.py"
    repository_source = repository / "src/open_tax_check/__init__.py"

    assert module_origin_is_isolated(site_package, environment, repository)
    assert not module_origin_is_isolated(repository_source, environment, repository)
    assert not module_origin_is_isolated(tmp_path / "elsewhere/open_tax_check/__init__.py", environment, repository)
