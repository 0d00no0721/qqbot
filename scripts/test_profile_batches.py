# -*- coding: utf-8 -*-
"""画像批次离线回归：临时文件、模拟模型和事件屏障，不访问 QQ 或模型网关。"""

import asyncio
import io
import json
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from scripts import user_profile as profile


VALID_PROFILE = "该用户喜欢讨论游戏，表达简洁，常与群友交流日常生活。"


class ProfileBatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(temp) / "user_profiles.json"
        self.stack.enter_context(patch.multiple(
            profile, _speak_buffer={}, _pending_retry={}, _profiles={},
            _update_lock=asyncio.Lock(), _napcat_connected=True,
            PROFILE_ENABLED=True, _PROFILE_FILE=str(self.path), create=True,
        ))
        self.school = self.stack.enter_context(patch.object(
            profile, "_is_school_only_hours", return_value=True))
        self.api = self.stack.enter_context(patch.object(
            profile, "_call_profile_api", new_callable=AsyncMock,
            return_value=VALID_PROFILE))
        self.stack.enter_context(patch.object(profile, "logger"))
        self.stack.enter_context(patch.object(profile.traceback, "print_exc"))
        self.stack.enter_context(redirect_stdout(io.StringIO()))

    def record(self, uid="u", count=5, prefix="旧", gid="g"):
        msgs = [f"{prefix}{i}" for i in range(count)]
        for msg in msgs:
            profile.record_message(gid, uid, msg)
        return msgs

    def pending(self, uid="u", gid="g"):
        return [msg for batch in profile._pending_retry.get(gid, {}).get(uid, [])
                for msg in batch.messages]

    async def seed_failure(self, uid="u", connected=True):
        profile.set_napcat_connected(connected)
        msgs = self.record(uid)
        self.api.return_value = None
        await profile.update_profiles()
        self.api.reset_mock()
        self.api.return_value = VALID_PROFILE
        return msgs

    async def wait_event(self, event):
        await asyncio.wait_for(event.wait(), 2)

    async def entered_call(self, entered, fn):
        entered.set()
        return await fn()

    async def test_retry_success_consumes_batch_before_next_regular_cycle(self):
        # 复现 1：首轮失败 -> 重试成功 -> 下一常规周期，修复前第三次仍处理旧五条。
        msgs = self.record()
        self.api.side_effect = [None, VALID_PROFILE, VALID_PROFILE]
        self.assertIsNone(await profile.update_profiles())
        self.assertEqual(profile.get_pending_count(), 1)
        self.assertEqual(await profile.retry_pending_updates(), 1)
        await profile.update_profiles()
        self.assertEqual(self.api.await_count, 2)
        self.assertEqual([c.args[0] for c in self.api.call_args_list], [msgs, msgs])
        self.assertFalse(profile.has_pending_retry())
        self.assertEqual(profile._speak_buffer, {})

    async def test_retry_and_regular_update_keep_new_pending_user(self):
        # 复现 2：旧用户重试期间触发常规更新，修复前重试结束覆盖新用户。
        await self.seed_failure("old", connected=False)
        started, release, entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def call(msgs, existing, school_only):
            if not started.is_set():
                started.set()
                await release.wait()
            return None

        self.api.side_effect = call
        retry = asyncio.create_task(profile.retry_pending_updates())
        await self.wait_event(started)
        new = self.record("new", prefix="新")
        regular = asyncio.create_task(self.entered_call(entered, profile.update_profiles))
        await self.wait_event(entered)
        release.set()
        self.assertEqual(await asyncio.wait_for(asyncio.gather(retry, regular), 2), [0, None])
        self.assertEqual(set(profile._pending_retry["g"]), {"old", "new"})
        self.assertEqual(profile.get_pending_count(), 2)
        self.assertEqual(self.pending("new"), new)
        self.assertEqual(self.api.await_count, 2)

    async def test_regular_update_and_retry_share_claimed_batch(self):
        old = await self.seed_failure()
        started, release, entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def call(*args):
            started.set()
            await release.wait()
            return VALID_PROFILE

        self.api.side_effect = call
        regular = asyncio.create_task(profile.update_profiles())
        await self.wait_event(started)
        fresh = self.record(prefix="新")
        other = self.record("new", prefix="另一用户")
        retry = asyncio.create_task(self.entered_call(entered, profile.retry_pending_updates))
        await self.wait_event(entered)
        release.set()
        self.assertEqual(await asyncio.wait_for(asyncio.gather(regular, retry), 2), [None, 0])
        self.api.assert_awaited_once_with(old, None, True)
        self.assertEqual(profile._speak_buffer, {"g": {"u": fresh, "new": other}})
        self.assertFalse(profile.has_pending_retry())

    async def test_interleaved_retry_and_regular_update_keep_same_user_new_batch(self):
        for result in (VALID_PROFILE, None):
            with self.subTest(result=result):
                profile._speak_buffer.clear()
                profile._pending_retry.clear()
                self.api.side_effect = None
                old = await self.seed_failure(connected=False)
                started, release, entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

                async def call(*args):
                    if not started.is_set():
                        started.set()
                        await release.wait()
                        return result
                    return None

                self.api.side_effect = call
                retry = asyncio.create_task(profile.retry_pending_updates())
                await self.wait_event(started)
                fresh = self.record(prefix="新批次")
                other = self.record("new", prefix="新用户")
                regular = asyncio.create_task(self.entered_call(entered, profile.update_profiles))
                await self.wait_event(entered)
                release.set()
                self.assertEqual(await asyncio.wait_for(asyncio.gather(retry, regular), 2),
                                 [1 if result else 0, None])
                self.assertEqual(self.pending(), fresh if result else old + fresh)
                self.assertEqual(self.pending("new"), other)
                self.assertEqual(profile.get_pending_count(), 2)
                self.assertEqual(self.api.await_count, 3)

    async def test_simultaneous_retries_claim_successful_batch_once(self):
        old = await self.seed_failure(connected=False)
        started, release, entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def call(*args):
            started.set()
            await release.wait()
            return VALID_PROFILE

        self.api.side_effect = call
        first = asyncio.create_task(profile.retry_pending_updates())
        await self.wait_event(started)
        second = asyncio.create_task(self.entered_call(entered, profile.retry_pending_updates))
        await self.wait_event(entered)
        release.set()
        self.assertEqual(await asyncio.wait_for(asyncio.gather(first, second), 2), [1, 0])
        self.api.assert_awaited_once_with(old, None, True)
        self.assertFalse(profile.has_pending_retry())

    async def test_new_messages_survive_regular_success_and_failure(self):
        for result in (VALID_PROFILE, None):
            with self.subTest(result=result):
                profile._speak_buffer.clear()
                profile._pending_retry.clear()
                old = self.record()
                started, release = asyncio.Event(), asyncio.Event()

                async def call(*args):
                    started.set()
                    await release.wait()
                    return result

                self.api.side_effect = call
                task = asyncio.create_task(profile.update_profiles())
                await self.wait_event(started)
                fresh = self.record(count=2, prefix="新")
                release.set()
                await asyncio.wait_for(task, 2)
                self.assertEqual(profile._speak_buffer, {"g": {"u": fresh}})
                self.assertEqual(self.pending(), [] if result else old)
                self.assertEqual(profile.get_pending_count(), 0 if result else 1)

    async def test_new_messages_survive_retry_success_and_failure(self):
        for result in (VALID_PROFILE, None):
            with self.subTest(result=result):
                profile._speak_buffer.clear()
                profile._pending_retry.clear()
                self.api.side_effect = None
                old = await self.seed_failure(connected=False)
                started, release = asyncio.Event(), asyncio.Event()

                async def call(*args):
                    started.set()
                    await release.wait()
                    return result

                self.api.side_effect = call
                task = asyncio.create_task(profile.retry_pending_updates())
                await self.wait_event(started)
                fresh = self.record(count=2, prefix="新")
                release.set()
                self.assertEqual(await asyncio.wait_for(task, 2), 1 if result else 0)
                self.assertEqual(profile._speak_buffer, {"g": {"u": fresh}})
                self.assertEqual(self.pending(), [] if result else old)

    async def test_regular_fallback_does_not_need_new_messages(self):
        old = await self.seed_failure()
        self.assertEqual(profile._speak_buffer, {})
        await profile.update_profiles()
        self.api.assert_awaited_once_with(old, None, True)
        self.assertFalse(profile.has_pending_retry())
        self.assertEqual(await profile.retry_pending_updates(), 0)

    async def test_retry_only_batch_keeps_its_original_eligibility(self):
        old = await self.seed_failure(connected=False)
        profile.set_napcat_connected(True)
        await profile.update_profiles()
        self.api.assert_not_awaited()
        fresh = self.record(prefix="新")
        await profile.update_profiles()
        self.api.assert_awaited_once_with(fresh, None, True)
        self.assertEqual(self.pending(), old)
        self.assertEqual(profile.get_pending_count(), 1)
        self.assertEqual(await profile.retry_pending_updates(), 1)
        self.assertEqual(self.api.call_args.args, (old, VALID_PROFILE, True))

    async def test_same_user_batches_remain_ordered_for_retry(self):
        old = await self.seed_failure(connected=False)
        profile.set_napcat_connected(True)
        fresh = self.record(prefix="新")
        self.api.return_value = None
        await profile.update_profiles()
        self.assertEqual(self.pending(), old + fresh)
        self.assertEqual([b.regular_fallback for b in profile._pending_retry["g"]["u"]],
                         [False, True])
        self.assertEqual(profile.get_pending_count(), 1)
        self.api.reset_mock()
        self.api.return_value = VALID_PROFILE
        self.assertEqual(await profile.retry_pending_updates(), 1)
        self.api.assert_awaited_once_with(old + fresh, None, True)
        self.assertFalse(profile.has_pending_retry())

    async def test_regular_claims_only_eligible_batches_and_new_messages(self):
        old = await self.seed_failure(connected=False)
        profile.set_napcat_connected(True)
        eligible = self.record(prefix="可兜底")
        self.api.return_value = None
        await profile.update_profiles()
        fresh = self.record(count=2, prefix="新")
        self.api.reset_mock()
        self.api.return_value = VALID_PROFILE
        await profile.update_profiles()
        self.api.assert_awaited_once_with(eligible + fresh, None, True)
        self.assertEqual(self.pending(), old)
        self.assertEqual(profile._speak_buffer, {})

    async def test_offline_retry_keeps_regular_fallback_flag(self):
        old = await self.seed_failure()
        profile.set_napcat_connected(False)
        self.api.return_value = None
        self.assertEqual(await profile.retry_pending_updates(), 0)
        self.assertTrue(profile._pending_retry["g"]["u"][0].regular_fallback)
        self.api.reset_mock()
        self.api.return_value = VALID_PROFILE
        await profile.update_profiles()
        self.api.assert_awaited_once_with(old, None, True)
        self.assertFalse(profile.has_pending_retry())

    async def test_too_few_messages_wait_and_identical_text_is_not_deduplicated(self):
        for _ in range(4):
            profile.record_message("g", "u", "重复的真实发言")
        await profile.update_profiles()
        self.api.assert_not_awaited()
        self.assertEqual(profile._speak_buffer["g"]["u"], ["重复的真实发言"] * 4)
        profile.record_message("g", "u", "重复的真实发言")
        await profile.update_profiles()
        self.api.assert_awaited_once_with(["重复的真实发言"] * 5, None, True)
        self.assertFalse(profile.has_pending_retry())

    async def test_short_batch_is_restored_before_messages_received_during_other_call(self):
        self.record("busy")
        old = self.record(count=4)
        started, release = asyncio.Event(), asyncio.Event()

        async def call(*args):
            started.set()
            await release.wait()
            return VALID_PROFILE

        self.api.side_effect = call
        task = asyncio.create_task(profile.update_profiles())
        await self.wait_event(started)
        fresh = self.record(count=1, prefix="新")
        release.set()
        await asyncio.wait_for(task, 2)
        self.assertEqual(profile._speak_buffer["g"]["u"], old + fresh)
        self.assertEqual(self.api.await_count, 1)

    async def test_regular_failure_routing_matches_existing_policy(self):
        for school in (True, False):
            for connected in (True, False):
                for raises in (True, False):
                    with self.subTest(school=school, connected=connected, raises=raises):
                        profile._speak_buffer.clear()
                        profile._pending_retry.clear()
                        self.school.return_value = school
                        profile.set_napcat_connected(connected)
                        old = self.record()

                        async def call(*args):
                            profile.record_message("g", "u", "新发言")
                            if raises:
                                raise RuntimeError("离线模拟错误")
                            return None

                        self.api.side_effect = call
                        await profile.update_profiles()
                        queued = school and (not connected or not raises)
                        self.assertEqual(self.pending(), old if queued else [])
                        self.assertEqual(profile._speak_buffer["g"]["u"],
                                         ["新发言"] if queued else old + ["新发言"])
                        self.assertEqual(profile.get_pending_count(), int(queued))
                        if queued:
                            self.assertEqual(profile._pending_retry["g"]["u"][0].regular_fallback,
                                             connected)

    async def test_retry_failure_routing_matches_existing_policy(self):
        for school in (True, False):
            for connected in (True, False):
                for raises in (True, False):
                    with self.subTest(school=school, connected=connected, raises=raises):
                        profile._speak_buffer.clear()
                        profile._pending_retry.clear()
                        self.school.return_value = school
                        profile.set_napcat_connected(connected)
                        old = ["旧发言"] * 5
                        profile._queue_batch("g", "u", old, regular_fallback=True)

                        async def call(*args):
                            profile.record_message("g", "u", "新发言")
                            if raises:
                                raise RuntimeError("离线模拟错误")
                            return None

                        self.api.side_effect = call
                        self.assertEqual(await profile.retry_pending_updates(), 0)
                        queued = school and not connected
                        self.assertEqual(self.pending(), old if queued else [])
                        self.assertEqual(profile._speak_buffer["g"]["u"],
                                         ["新发言"] if queued else old + ["新发言"])
                        self.assertEqual(profile.has_pending_retry(), queued)
                        if queued:
                            self.assertTrue(profile._pending_retry["g"]["u"][0].regular_fallback)

    async def test_failed_retry_merges_with_existing_pending_batches(self):
        old = await self.seed_failure(connected=False)

        async def call(*args):
            # 模拟领取后新增的队列项，验证归还只合并相关用户，不整体赋值。
            profile._queue_batch("g", "u", ["后批"] * 5)
            profile._queue_batch("g", "new", ["新用户"] * 5)
            return None

        self.api.side_effect = call
        await profile.retry_pending_updates()
        self.assertEqual(self.pending(), old + ["后批"] * 5)
        self.assertEqual(self.pending("new"), ["新用户"] * 5)
        self.assertEqual(profile.get_pending_count(), 2)

    async def test_pending_count_counts_users_across_groups_not_batches(self):
        for gid, uid in (("g", "u"), ("g", "u"), ("g", "other"), ("h", "u")):
            profile._queue_batch(gid, uid, ["发言"] * 5)
        self.assertEqual(profile.get_pending_count(), 3)
        self.assertEqual(await profile.retry_pending_updates(), 3)
        self.assertEqual(self.api.await_count, 3)
        self.assertFalse(profile.has_pending_retry())

    async def test_public_recording_and_profile_file_format_are_unchanged(self):
        self.path.write_text(json.dumps({"g": {"u": "已有画像"}}, ensure_ascii=False),
                             encoding="utf-8")
        profile.load_profiles()
        self.assertEqual(profile.get_profile("g", "u"), "已有画像")
        profile.record_message("g", "u", "   ")
        profile.record_message("g", "u", "  " + "字" * 220 + "  ")
        self.record(count=4)
        await profile.update_profiles()
        self.assertEqual(self.api.call_args.args[0][0], "字" * 200)
        self.assertEqual(self.api.call_args.args[1], "已有画像")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         {"g": {"u": VALID_PROFILE}})
        with patch.object(profile, "PROFILE_ENABLED", False):
            self.record()
            await profile.update_profiles()
            self.assertIsNone(profile.get_profile("g", "u"))
        self.assertEqual(self.api.await_count, 1)
        self.assertEqual(profile._speak_buffer, {})


class ProfilePolicyTests(unittest.IsolatedAsyncioTestCase):
    """行为锁定：保留作者原有时段、尝试策略、提示词和无效首轮画像回退。"""

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.api = self.stack.enter_context(patch.object(
            profile, "_call_single_api", new_callable=AsyncMock))
        self.alternate = self.stack.enter_context(patch.object(
            profile, "_alternate_pool_model", return_value="offline-backup"))
        self.stack.enter_context(patch.object(profile, "logger"))

    def test_schedule_and_limits_are_unchanged(self):
        self.assertEqual(profile.PROFILE_UPDATE_INTERVAL, 7200)
        self.assertEqual(profile.PROFILE_MIN_MESSAGES, 5)
        self.assertEqual(profile.PROFILE_API_TIMEOUT, 40)
        self.assertEqual(profile.PROFILE_MAX_TOKENS, 512)
        self.assertEqual((profile._CONTENT_MIN_LENGTH, profile._CONTENT_MAX_LENGTH), (10, 300))

    def test_restricted_hours_boundaries(self):
        for hour in range(24):
            with self.subTest(hour=hour), patch.object(profile, "_dt") as clock:
                clock.now.return_value = datetime(2026, 10, 13, hour)
                self.assertEqual(profile._is_school_only_hours(),
                                 hour in (9, 10, 11, 14, 15, 16, 17))

    async def test_valid_primary_returns_after_one_attempt(self):
        for school in (True, False):
            with self.subTest(school=school):
                self.api.reset_mock()
                self.api.return_value = VALID_PROFILE
                self.assertEqual(await profile._call_profile_api(["发言"], None, school), VALID_PROFILE)
                self.assertEqual(self.api.await_count, 1)
                self.alternate.assert_not_called()

    async def test_restricted_hours_do_not_try_backup(self):
        for primary in (None, "", "短句", "error: 模型返回错误信息"):
            with self.subTest(primary=primary):
                self.api.reset_mock()
                self.api.return_value = primary
                self.assertIsNone(await profile._call_profile_api(["发言"], None, True))
                self.assertEqual(self.api.await_count, 1)
                self.alternate.assert_not_called()

    async def test_unrestricted_backup_is_used_once_with_the_same_prompt(self):
        for primary in (None, "短句", "error: 模型返回错误信息"):
            with self.subTest(primary=primary):
                self.api.reset_mock()
                self.api.side_effect = [primary, VALID_PROFILE]
                self.assertEqual(await profile._call_profile_api(["发言"], None, False), VALID_PROFILE)
                self.assertEqual(self.api.await_count, 2)
                first, second = self.api.call_args_list
                self.assertEqual(first.args[3], second.args[3])
                self.assertEqual(second.args[:3], ("", "", "offline-backup"))
                self.assertEqual(second.kwargs, {"preferred": "offline-backup"})

    async def test_invalid_primary_is_still_returned_if_backup_fails(self):
        for primary in ("短句", "error: 模型返回错误信息"):
            for backup in (None, "仍短"):
                with self.subTest(primary=primary, backup=backup):
                    self.api.reset_mock()
                    self.api.side_effect = [primary, backup]
                    self.assertFalse(profile._validate_profile(primary))
                    self.assertEqual(await profile._call_profile_api(["发言"], "旧画像", False), primary)
                    self.assertEqual(self.api.await_count, 2)

    async def test_missing_primary_and_invalid_backup_still_fail(self):
        for primary in (None, ""):
            with self.subTest(primary=primary):
                self.api.reset_mock()
                self.api.side_effect = [primary, "短句"]
                self.assertIsNone(await profile._call_profile_api(["发言"], "旧画像", False))
                self.assertEqual(self.api.await_count, 2)

    async def test_existing_prompt_and_last_twenty_messages_are_unchanged(self):
        self.api.return_value = VALID_PROFILE
        msgs = [str(i) for i in range(25)]
        recent = "\n".join(f"- {m}" for m in msgs[-20:])
        for existing in (None, "已有画像"):
            with self.subTest(existing=existing):
                await profile._call_profile_api(msgs, existing, False)
                if existing:
                    expected = (
                        f"已有画像：{existing}\n\n该用户最近新发言：\n{recent}\n\n"
                        "请结合新发言更新画像。保持第三人称、50-100字，包含风格/兴趣/情绪特征。"
                        "直接输出更新后的画像文本，不要加任何前缀或解释。"
                    )
                else:
                    expected = (
                        f"你是用户画像分析师。根据以下QQ群发言记录，提炼该用户的特征画像：\n\n{recent}\n\n"
                        "要求：第三人称、50-100字，包含说话风格/兴趣爱好/情绪特征。"
                        "直接输出画像文本，不要加任何前缀或解释。"
                    )
                self.assertEqual(self.api.call_args.args[3], expected)


if __name__ == "__main__":
    unittest.main()
