from __future__ import annotations

import pytest

from llm_wiki.common.paths import _existing_root


def test_existing_root_rejects_missing_path(tmp_path):
    missing = tmp_path / "missing"

    with pytest.raises(RuntimeError, match="不存在"):
        _existing_root(missing, "默认项目根目录")
