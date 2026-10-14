"""jenkins-pb-rebuild 技能纯函数测试（链接解析、CL 合并/整列替换）。

不触发真实 Jenkins 构建 —— 触发路径已由 2026-09-22 手工端到端验证
（构建 #18262 按 #18249 参数 + 追加 CL 72526 触发成功）。
"""
import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "jenkins_pb_rebuild",
    pathlib.Path(__file__).resolve().parents[3] / "skills/jenkins-pb-rebuild/main.py",
)
_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_mod)


class TestParseJobLink:
    @pytest.mark.parametrize("link,job,build", [
        ("https://jenkins.example.com/job/android/job/Android_U/job/Android_U_VendorX_PATCHBUILD/18249/",
         "job/android/job/Android_U/job/Android_U_VendorX_PATCHBUILD", 18249),
        ("https://jenkins.example.com/job/android/job/Android_U/job/Android_U_VendorX_PATCHBUILD/",
         "job/android/job/Android_U/job/Android_U_VendorX_PATCHBUILD", None),
        ("job/android/job/Android_U/job/Android_U_VendorX_PATCHBUILD/18249",
         "job/android/job/Android_U/job/Android_U_VendorX_PATCHBUILD", 18249),
    ])
    def test_parse(self, link, job, build):
        assert _mod.parse_job_link(link) == (job, build)

    def test_invalid_raises(self):
        with pytest.raises(RuntimeError):
            _mod.parse_job_link("not-a-jenkins-link")


class TestMergeClIds:
    def test_append_new(self):
        assert _mod.merge_cl_ids("72530", ["72526"]) == "72530\n72526"

    def test_dedup_existing(self):
        # 原有项在前、去重
        assert _mod.merge_cl_ids("72530\n72525", ["72530", "72526"]) == "72530\n72525\n72526"

    def test_empty_existing(self):
        assert _mod.merge_cl_ids("", ["72530", "72526", "72530"]) == "72530\n72526"

    def test_multiline_input(self):
        assert _mod.merge_cl_ids("72530\n\n  \n72525", ["72526"]) == "72530\n72525\n72526"


class TestResolveClOut:
    """set_ids 整列替换 / add_ids 合并 / 都不传原样（2026-09-30 事故修复）。"""

    def test_set_replaces_whole_list(self):
        """整列替换：完全丢弃 base，只保留 set_ids（2026-09-30 事故场景）。"""
        assert _mod.resolve_cl_out("67394\n73043", [], ["72668"]) == "72668"

    def test_set_dedup_keep_order(self):
        assert _mod.resolve_cl_out("999", [], ["72668", "72668", "111"]) == "72668\n111"

    def test_set_ignores_blank_items(self):
        assert _mod.resolve_cl_out("67394", [], ["  ", "72668", ""]) == "72668"

    def test_set_takes_precedence_over_add(self):
        """同时给 set 和 add → set 优先（确定性），add 被忽略。"""
        assert _mod.resolve_cl_out("67394", ["111"], ["72668"]) == "72668"

    def test_add_when_no_set(self):
        assert _mod.resolve_cl_out("67394\n73043", ["72668"], []) == "67394\n73043\n72668"

    def test_no_change_when_both_empty(self):
        assert _mod.resolve_cl_out("67394\n73043", [], []) == "67394\n73043"
        assert _mod.resolve_cl_out("", [], []) == ""
