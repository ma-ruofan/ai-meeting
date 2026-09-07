from test_llm_audio import wav_bytes

from meeting_assistant.audio import wav_info
from meeting_assistant.export import escape


def test_truncated_wav_is_not_treated_as_complete(tmp_path):
    path = tmp_path / "broken.wav"
    path.write_bytes(wav_bytes()[:-100])
    assert wav_info(path) == (None, False)


def test_transcript_cannot_insert_markdown_image():
    output = escape("![secret](https://example.invalid/image) <script>x</script>")
    assert "![secret]" not in output
    assert "<script>" not in output
