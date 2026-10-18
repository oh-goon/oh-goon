"""Optional roadmap archive checks use explicit fixtures, never checkout history."""
from pathlib import Path

from oh_goon.repository_tooling.architecture.co_r01_roadmap_contract import check_co_r01_roadmap_integrity


def _fixture(tmp_path: Path) -> Path:
    (tmp_path / '.checkpoint').mkdir()
    (tmp_path / '.checkpoint/CO_R3T_SOURCE.sha256').write_text('fixture', encoding='utf-8')
    path = tmp_path / 'OH_GOON_FORWARD_TODO.md'
    path.write_text(
        '\n'.join(f'### [ ] {name} — archived work item' for name in
                  ('CO-R3T', 'CO-R3U', 'CO-R3V', 'CO-R3W', 'CO-R3X'))
        + '\nGate A Gate B Gate C CO-R2b P3C APP PASS release\n',
        encoding='utf-8',
    )
    documents = ('ADR_CANONICAL_OWNER_REPLACEABLE_IMPLEMENTATIONS.md',
                 'CO_R01_CANONICAL_OWNER_CENSUS.md')
    history = '\n'.join(f'- [ ] **{name} — archived invariant**' for name in
                        ('CO-R0 + CO-R1', 'CO-R2b', 'CO-R2a', 'CO-R2c', 'CO-R3', 'CO-R4'))
    (tmp_path / 'OH_GOON_FORWARD_TODO_HISTORY_CO_R3S.md').write_text(
        history + '\n' + '\n'.join(documents) + '\n', encoding='utf-8'
    )
    directory = tmp_path / 'docs/architecture'
    directory.mkdir(parents=True)
    for name in documents:
        (directory / name).write_text('archived decision\n', encoding='utf-8')
    return path


def test_explicit_archive_has_all_lifecycle_checkpoints(tmp_path):
    _fixture(tmp_path)
    assert check_co_r01_roadmap_integrity(tmp_path) == []


def test_dropped_workspace_lifecycle_checkpoint_rejected(tmp_path):
    path = _fixture(tmp_path)
    content = path.read_text()
    path.write_text(content.replace('### [ ] CO-R3U —', '### [ ] DELETED-CO-R3U —'))
    assert any('CO-R3U' in issue.message for issue in check_co_r01_roadmap_integrity(tmp_path))


def test_duplicate_lifecycle_todo_is_rejected(tmp_path):
    path = _fixture(tmp_path)
    content = path.read_text()
    copy = next(line for line in content.splitlines() if line.startswith('### [ ] CO-R3U —'))
    path.write_text(content + '\n' + copy + '\n')
    assert any('CO-R3' in issue.message for issue in check_co_r01_roadmap_integrity(tmp_path))
