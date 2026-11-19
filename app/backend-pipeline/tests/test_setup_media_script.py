"""infra/setup_media.sh must work for any configured project, not only a demo id."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SETUP = (ROOT / "infra" / "setup_media.sh").read_text()
SETUP_SH = (ROOT / "infra" / "setup.sh").read_text()


def test_setup_media_is_not_locked_to_a_demo_project():
    assert "example-project" not in SETUP
    assert "intellisolve-atlanta-demo" not in SETUP
    assert 'PROJECT_ID" ==' not in SETUP
    assert "demo project only" not in SETUP.lower()


def test_setup_media_covers_qr_grants_and_drops_branding():
    for title in ("upload-create-media", "upload-read-media", "pipeline-read-media", "pipeline-list-media"):
        assert title in SETUP
    assert "media_tokens/" in SETUP
    assert "AUDIO_RETENTION_DAYS" in SETUP
    assert "upload-read-branding" not in SETUP
    assert "upload-list-branding" not in SETUP
    assert "$(obj branding/)" not in SETUP
    assert "$(lst branding/)" not in SETUP


def test_setup_sh_calls_setup_media_and_keeps_seven_day_media_retention():
    assert "setup_media.sh" in SETUP_SH
    assert 'matchesPrefix": ["media/", "media_tokens/"]' in SETUP_SH
