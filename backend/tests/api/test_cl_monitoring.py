"""CL 监控 V2 API 测试 — 走真实生产路由。

覆盖：
  1. GET  /api/dashboard/cl_status          页面缓存（含 V2 字段，无 status_filter）
  2. GET  /api/dashboard/cl_focus_summary   重点监控汇总（按状态分组）
  3. PUT  /api/dashboard/cl_monitoring_config  保存配置后 JQL 以 advanced_jql 为准
"""

import json

import pytest

from app.services.cl_queue_service import upsert_state

USER_ID = 1


def _focus_state(issue_key: str, status: str, **fields):
    """队列记录字段（除主键）。"""
    base = dict(
        project_name="R+长虹T6d", summary=f"s-{issue_key}", assignee="Kai.Qin",
        status=status, prev_status="In Progress", queued=True, archived=False,
    )
    base.update(fields)
    return base


@pytest.mark.asyncio
async def test_cl_status_has_v2_fields(async_client, db_session):
    """页面缓存返回 V2 字段（prev_status / cl_unregistered / queue_reason）。"""
    await upsert_state(
        db_session, USER_ID, "TV-950814",
        **_focus_state("TV-950814", "Verified", prev_status="Resolved",
                       queue_reason="transition", cl_noregister=True,
                       cl_items_json="[]"),
    )
    await db_session.commit()

    # 缓存由扫描重建；测试里直接构造一份等价的页面缓存
    from app.services.cl_snapshot_service import rebuild_from_queue
    await rebuild_from_queue(db_session, USER_ID)
    await db_session.commit()

    resp = await async_client.get("/api/dashboard/cl_status")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    groups = {g["jira_key"]: g for g in data[0]["jira_groups"]}
    assert "TV-950814" in groups
    group = groups["TV-950814"]
    assert group["prev_status"] == "Resolved"
    assert group["status"] == "Verified"
    assert group["cl_unregistered"] is True
    assert group["queue_reason"] == "transition"


@pytest.mark.asyncio
async def test_cl_status_ignores_status_filter_param(async_client):
    """status_filter 参数已删除：传了也不应触发强制实时查询（仍走缓存）。"""
    resp = await async_client.get("/api/dashboard/cl_status?status_filter=Verified,Resolved")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_cl_focus_summary_groups_by_status(async_client, db_session):
    """重点监控汇总按状态分组，并单列「未登记 CL」与「CL 未合并」。"""
    await upsert_state(db_session, USER_ID, "TV-1",
                       **_focus_state("TV-1", "Resolved"))
    await upsert_state(db_session, USER_ID, "TV-2",
                       **_focus_state("TV-2", "Verified", cl_total=1, cl_unmerged=1))
    await upsert_state(db_session, USER_ID, "TV-3",
                       **_focus_state("TV-3", "Closed", cl_noregister=True))
    await db_session.commit()

    resp = await async_client.get("/api/dashboard/cl_focus_summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert [i["jira_key"] for i in body["groups"]["Resolved"]] == ["TV-1"]
    assert [i["jira_key"] for i in body["groups"]["Verified"]] == ["TV-2"]
    assert [i["jira_key"] for i in body["groups"]["Closed"]] == ["TV-3"]
    assert [i["jira_key"] for i in body["unregistered"]] == ["TV-3"]
    assert [i["jira_key"] for i in body["unmerged"]] == ["TV-2"]


@pytest.mark.asyncio
async def test_cl_focus_summary_skips_archived_and_exited(async_client, db_session):
    """已归档（首轮历史 Closed）与已退出队列的题不出现在汇总里。"""
    await upsert_state(db_session, USER_ID, "TV-1",
                       **_focus_state("TV-1", "Resolved"))
    await upsert_state(db_session, USER_ID, "TV-2",
                       **_focus_state("TV-2", "Verified", archived=True))
    await upsert_state(db_session, USER_ID, "TV-3",
                       **_focus_state("TV-3", "Closed", queued=False))
    await db_session.commit()

    body = (await async_client.get("/api/dashboard/cl_focus_summary")).json()
    assert body["total"] == 1
    assert [i["jira_key"] for i in body["groups"]["Resolved"]] == ["TV-1"]


@pytest.mark.asyncio
async def test_config_advanced_jql_wins_over_stale_labels(async_client, db_session):
    """保存配置后，JQL 以 advanced_jql 为准（回归：曾被残留 labels 覆盖）。"""
    jql = 'status not in (Closed) AND "Project ID" = "PROJECTX-CHIP-X" AND issuetype not in (Epic)'
    resp = await async_client.put("/api/dashboard/cl_monitoring_config", json={
        "projects": [{
            "name": "R+长虹T6d", "enabled": True, "cl_monitoring_enabled": True,
            "advanced_jql": jql,
            "labels": "oem_release_tv",   # 残留的基本模式字段
            "jira_project_key": "TV",
        }],
    })
    assert resp.status_code == 200

    from app.services.dashboard_service import build_cl_jql, get_cl_monitoring_config
    config = await get_cl_monitoring_config(USER_ID)
    proj = config["projects"][0]
    assert build_cl_jql(proj) == jql
    assert "oem_release_tv" not in build_cl_jql(proj)
