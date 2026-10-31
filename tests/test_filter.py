"""OWUI dependencies and planner responses are simulated; no live model/server."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch
from fastapi import Request
from pydantic import BaseModel


def module(name, **attrs):
    obj = types.ModuleType(name)
    obj.__dict__.update(attrs)
    sys.modules[name] = obj
    return obj


class UpdateMemoriesForm(BaseModel):
    operations: list[dict]


for name in ("open_webui", "open_webui.models", "open_webui.routers"):
    module(name)
Chats, Folders, Memories, Users = (types.SimpleNamespace() for _ in range(4))
for name, obj in (("chats", Chats), ("folders", Folders), ("memories", Memories), ("users", Users)):
    module("open_webui.models." + name, **{name.capitalize(): obj})
module("open_webui.routers.memories", UpdateMemoriesForm=UpdateMemoriesForm,
       update_memories=AsyncMock(), check_memories_permission=AsyncMock())
path = Path(__file__).resolve().parents[1] / "folder_memory_isolation.py"
spec = importlib.util.spec_from_file_location("scoped_filter", path)
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class FilterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.f = plugin.Filter()
        self.user = {"id": "u"}
        self.rows = [
            {"id": "a", "content": "[F_ID:a][C_ID:old] User likes Rust", "updated_at": 1, "type": "user", "path": "preferences"},
            {"id": "b", "content": "[F_ID:b][C_ID:other] OTHER_FOLDER", "updated_at": 2},
            {"id": "g", "content": "LEGACY_GLOBAL", "updated_at": 3},
            {"id": "r", "content": "[C_ID:c] ROOT_CHAT", "updated_at": 4},
            {"id": "o", "content": "[C_ID:other] OTHER_ROOT", "updated_at": 5},
        ]
        Chats.get_chat_by_id_and_user_id = AsyncMock(return_value={"folder_id": "a"})
        Folders.get_folder_by_id_and_user_id = AsyncMock(return_value={"name": "Project A"})
        Users.get_user_by_id = AsyncMock(return_value=types.SimpleNamespace(id="u"))
        Memories.get_memories_by_user_id = AsyncMock(side_effect=lambda uid: copy.deepcopy(self.rows))
        plugin.check_memories_permission = AsyncMock()
        plugin.update_memories = AsyncMock(side_effect=self.apply)
        self.f._plan = AsyncMock(return_value=({"operations": []}, ["question"]))
        self.body = {"model": "chat-model", "chat_id": "c", "features": {"memory": True, "web_search": True},
                     "metadata": {"features": {"memory": True}},
                     "messages": [{"role": "system", "content": "KEEP_SYSTEM"}, {"role": "user", "content": "question"}]}
        self.request = Request({"type": "http", "headers": [(b"accept-language", b"zh-TW")],
                                "app": types.SimpleNamespace(state=types.SimpleNamespace(MODELS={"chat-model": {"id": "chat-model"}})),
                                "state": {"metadata": {"features": {"memory": True}}, "bypass_filter": False}})
        self.emitter = AsyncMock()

    async def apply(self, *, request, form_data, user):
        results = []
        for op in form_data.operations:
            if op["action"] == "add":
                row = {"id": "new", "content": op["content"], "updated_at": 10}
                self.rows.append(row)
                results.append({"status": "created", "memory": row})
            elif op["action"] == "remove":
                self.rows[:] = [row for row in self.rows if row["id"] != op["id"]]
                results.append({"status": "deleted", "id": op["id"]})
            elif op["action"] == "replace":
                row = next(row for row in self.rows if row["id"] == op["id"])
                row.update(op)
                results.append({"status": "updated", "memory": row})
        return results

    async def inlet(self):
        return await self.f.inlet(self.body, self.user, self.emitter, __request__=self.request)

    def plan(self, operations, users):
        self.f._plan.return_value = {"operations": operations}, users
        self.body["messages"] = [{"role": "system", "content": "KEEP_SYSTEM"}] + [{"role": "user", "content": text} for text in users]

    def context(self):
        return self.body["messages"][0]["content"]

    async def test_scoped_read_preserves_system_prompt(self):
        await self.inlet()
        self.assertIn("KEEP_SYSTEM", self.context())
        self.assertIn("User likes Rust", self.context())
        for text in ("OTHER_FOLDER", "LEGACY_GLOBAL", "ROOT_CHAT", "OTHER_ROOT", "[F_ID:"):
            self.assertNotIn(text, self.context())

    async def test_native_gates_preserve_other_features(self):
        meta = {"features": {"memory": True, "code_interpreter": True}}
        await self.f.inlet(self.body, self.user, __metadata__=meta, __request__=self.request)
        for features in (meta["features"], self.body["features"], self.body["metadata"]["features"], self.request.state.metadata["features"]):
            self.assertFalse(features["memory"])
        self.assertTrue(self.body["features"]["web_search"])
        self.assertTrue(meta["features"]["code_interpreter"])

    async def test_root_scope(self):
        Chats.get_chat_by_id_and_user_id.return_value = {"folder_id": None}
        await self.inlet()
        self.assertIn("ROOT_CHAT", self.context())
        self.assertIn("LEGACY_GLOBAL", self.context())
        self.assertIn("OTHER_ROOT", self.context())
        self.assertNotIn("User likes Rust", self.context())

    async def test_root_save_is_recalled_in_another_root_chat(self):
        Chats.get_chat_by_id_and_user_id.return_value = {"folder_id": None}
        self.rows = []
        self.plan([{"action": "add", "source_index": 0, "quote": "水餃", "content": "使用者今天吃水餃", "evidence": "記住我今天吃水餃"}], ["記住我今天吃水餃"])
        await self.inlet()
        self.assertTrue(self.rows[0]["content"].startswith("[C_ID:c] "))
        self.body["chat_id"] = "another-root-chat"
        self.plan([], ["我今天吃什麼"])
        await self.inlet()
        self.assertIn("使用者今天吃水餃", self.context())
        self.assertIn('"scope": "global"', self.context())
        self.assertEqual(plugin.update_memories.await_count, 1)

    async def test_folder_does_not_inherit_root_memories(self):
        self.rows = [{"id": "x", "content": "[C_ID:other-root-chat] ROOT_ONLY", "updated_at": 1}]
        await self.inlet()
        self.assertNotIn("ROOT_ONLY", self.context())

    async def test_global_pool_is_still_per_user(self):
        Chats.get_chat_by_id_and_user_id.return_value = {"folder_id": None}
        Memories.get_memories_by_user_id.side_effect = lambda uid: copy.deepcopy(self.rows) if uid == "u" else []
        self.user["id"] = "other-user"
        await self.inlet()
        self.assertNotIn("ROOT_CHAT", self.context())
        self.assertNotIn("OTHER_ROOT", self.context())
        Memories.get_memories_by_user_id.assert_any_await("other-user")

    async def test_unknown_chat_never_becomes_global(self):
        Chats.get_chat_by_id_and_user_id.return_value = None
        await self.inlet()
        self.assertNotIn("LEGACY_GLOBAL", self.context())
        self.f._plan.assert_not_awaited()
        plugin.update_memories.assert_not_awaited()

    async def test_temporary_chat_skips(self):
        self.body["chat_id"] = "local:temporary"
        await self.inlet()
        self.f._plan.assert_not_awaited()

    async def test_permission_denied_skips_all_memory(self):
        plugin.check_memories_permission.side_effect = PermissionError()
        await self.inlet()
        Memories.get_memories_by_user_id.assert_not_awaited()
        plugin.update_memories.assert_not_awaited()
        self.assertIn('"status": "unavailable"', self.context())

    async def test_other_user_is_checked(self):
        self.user["id"] = "u2"
        await self.inlet()
        Chats.get_chat_by_id_and_user_id.assert_any_await("c", "u2")
        Memories.get_memories_by_user_id.assert_any_await("u2")

    async def test_untrusted_folder_ignored(self):
        self.body["metadata"]["folder_id"] = "b"
        await self.inlet()
        self.assertIn("User likes Rust", self.context())
        self.assertNotIn("OTHER_FOLDER", self.context())

    async def test_automatic_durable_fact_saved_before_reply(self):
        self.plan([{"action": "add", "source_index": 0, "quote": "繁體中文", "content": "使用者偏好繁體中文", "evidence": "我習慣用繁體中文"}], ["我習慣用繁體中文"])
        await self.inlet()
        self.assertIn('"status": "created"', self.context())
        self.assertIn("使用者偏好繁體中文", self.context())
        self.assertTrue(self.rows[-1]["content"].startswith("[F_ID:a][C_ID:c]"))

    async def test_screenshot_save_then_delete_lifecycle(self):
        self.rows = []
        self.plan([], ["我今天吃水餃"])
        await self.inlet()
        self.assertEqual(self.rows, [])
        self.plan([{"action": "add", "source_index": 0, "quote": "我今天吃水餃", "content": "使用者今天吃水餃", "evidence": "記住"}], ["我今天吃水餃", "記住"])
        await self.inlet()
        self.assertEqual(len(self.rows), 1)
        self.plan([{"action": "remove", "id": "new", "evidence": "刪除此記憶"}], ["我今天吃水餃", "記住", "刪除此記憶"])
        await self.inlet()
        self.assertEqual(self.rows, [])
        self.assertIn('"status": "deleted"', self.context())
        self.assertNotIn("使用者今天吃水餃", self.context())
        self.assertIn("persisted in the user's database", self.context())

    async def test_replace_preserves_tags_type_and_path(self):
        self.plan([{"action": "replace", "id": "a", "source_index": 0, "quote": "Python", "content": "User likes Python", "evidence": "改成 Python"}], ["改成 Python"])
        await self.inlet()
        op = plugin.update_memories.call_args.kwargs["form_data"].operations[0]
        self.assertEqual(op["content"], "[F_ID:a][C_ID:old] User likes Python")
        self.assertEqual(op["type"], "user")
        self.assertEqual(op["path"], "preferences")
        self.assertIn('"status": "updated"', self.context())

    async def test_delete_other_folder_blocked(self):
        self.plan([{"action": "remove", "id": "b", "evidence": "delete it"}], ["delete it"])
        await self.inlet()
        plugin.update_memories.assert_not_awaited()
        self.assertIn('"status": "failed_or_partial"', self.context())

    async def test_missing_current_evidence_blocks_mutation(self):
        self.plan([{"action": "remove", "id": "a", "evidence": "delete it"}], ["hello"])
        await self.inlet()
        plugin.update_memories.assert_not_awaited()

    async def test_bad_source_or_quote_blocks_mutation(self):
        for index, quote in ((True, "data"), (9, "data"), (0, "invented")):
            self.plan([{"action": "add", "source_index": index, "quote": quote, "content": "fact", "evidence": "data"}], ["data"])
            await self.inlet()
        plugin.update_memories.assert_not_awaited()

    async def test_duplicate_legacy_fact_does_not_save(self):
        self.rows[0]["content"] = "[F_ID:a][C_ID:old] User: User likes Rust\nAssistant: Generated claim"
        self.plan([{"action": "add", "source_index": 0, "quote": "Rust", "content": "User likes Rust", "evidence": "remember Rust"}], ["remember Rust"])
        await self.inlet()
        plugin.update_memories.assert_not_awaited()

    async def test_ambiguous_delete_requests_clarification(self):
        self.f._plan.return_value = {"operations": [], "needs_clarification": True}, ["delete it"]
        await self.inlet()
        plugin.update_memories.assert_not_awaited()
        self.assertIn("needs_clarification", self.context())

    async def test_backend_failure_not_claimed_success(self):
        self.plan([{"action": "remove", "id": "a", "evidence": "delete"}], ["delete"])
        plugin.update_memories.side_effect = RuntimeError()
        await self.inlet()
        self.assertIn("failed_or_partial", self.context())
        self.assertNotIn('"status": "deleted"', self.context())

    async def test_partial_vector_failure_reloads_committed_state(self):
        self.plan([{"action": "remove", "id": "a", "evidence": "delete"}], ["delete"])
        async def partial(**kwargs):
            self.rows[:] = [row for row in self.rows if row["id"] != "a"]
            raise RuntimeError("vector unavailable")
        plugin.update_memories.side_effect = partial
        await self.inlet()
        self.assertNotIn("User likes Rust", self.context())
        self.assertIn("failed_or_partial", self.context())

    async def test_move_during_plan_blocks_mutation(self):
        Chats.get_chat_by_id_and_user_id.side_effect = [{"folder_id": "a"}, {"folder_id": "b"}, {"folder_id": "b"}]
        self.plan([{"action": "remove", "id": "a", "evidence": "delete"}], ["delete"])
        await self.inlet()
        plugin.update_memories.assert_not_awaited()
        self.assertNotIn("User likes Rust", self.context())

    async def test_edits_are_not_cached_and_inlet_is_idempotent(self):
        await self.inlet()
        self.rows = []
        await self.inlet()
        self.assertNotIn("User likes Rust", self.context())
        self.assertEqual(self.context().count(plugin.MEMORY_HEADER), 1)
        self.assertIn("KEEP_SYSTEM", self.context())

    async def test_zero_inject_does_not_expose_memories(self):
        self.f.valves.max_inject = 0
        await self.inlet()
        self.assertNotIn("User likes Rust", self.context())

    async def test_bypass_preserves_body(self):
        self.f.valves.intercept_core = False
        before = copy.deepcopy(self.body)
        await self.inlet()
        self.assertEqual(self.body, before)

    async def test_status_is_english_and_can_be_hidden(self):
        await self.inlet()
        self.assertEqual(self.emitter.call_args.args[0]["data"]["description"], "Loaded 1 memory")
        self.user["valves"] = self.f.UserValves(show_status=False)
        self.emitter.reset_mock()
        await self.inlet()
        self.emitter.assert_not_awaited()

    async def test_outlet_does_not_save_generated_answer(self):
        await self.f.outlet(self.body, __user__=self.user, __request__=self.request)
        plugin.update_memories.assert_not_awaited()
        self.f._plan.assert_not_awaited()

    async def test_empty_planner_result_explicitly_reports_no_write(self):
        self.rows = []
        self.plan([], ["記住我今天吃水餃"])
        await self.inlet()
        plugin.update_memories.assert_not_awaited()
        self.assertIn('"status": "no_changes"', self.context())
        self.assertIn('"written_this_turn": false', self.context())
        self.assertIn('"stored_memory_count": 0', self.context())
        self.assertIn("Notes and chat-search results do not verify", self.context())

    async def test_debug_reveals_executed_but_skipped_without_memory_text(self):
        self.f.valves.debug_mode = True
        self.rows = []
        self.plan([], ["記住我今天吃水餃"])
        with self.assertLogs(plugin.logger, level="WARNING") as logs:
            await self.inlet()
        self.assertIn("stage=entered", str(logs.output))
        self.assertIn("stage=validated", str(logs.output))
        self.assertNotIn("水餃", str(logs.output))
        descriptions = [call.args[0]["data"]["description"] for call in self.emitter.call_args_list]
        self.assertIn("Memory check started (folder; 0 stored)", descriptions)
        self.assertIn("Memory check complete: no changes (0 stored)", descriptions)

    async def test_debug_timings_separate_model_from_total(self):
        self.f.valves.debug_mode = True
        with patch.object(plugin, "perf_counter", side_effect=[0.0, 1.0, 2.0, 3.0]):
            await self.inlet()
        descriptions = [call.args[0]["data"]["description"] for call in self.emitter.call_args_list]
        self.assertIn("Memory check: 3.0s (model 1.0s; write 0.0s)", descriptions)

    async def test_old_mode_flags_are_ignored(self):
        valves = self.f.Valves(atomic_memory_enabled=False, raw_save_requires_request=False, openai_api_key="legacy")
        self.assertNotIn("atomic_memory_enabled", valves.model_dump())
        self.assertNotIn("openai_api_key", valves.model_dump())

    async def test_real_planner_payload_has_no_assistant_claims_or_state_leak(self):
        fake_chat = types.ModuleType("open_webui.utils.chat")
        fake_chat.generate_chat_completion = AsyncMock(return_value={"choices": [{"message": {"content": '{"operations": []}'}}]})
        self.body["messages"].insert(1, {"role": "assistant", "content": "INVENTED_ASSISTANT_CLAIM"})
        for text in ("記住", "覚えておいて", "Retiens cela", "Forget that"):
            self.body["messages"][-1]["content"] = text
            with patch.dict(sys.modules, {"open_webui.utils": types.ModuleType("open_webui.utils"), "open_webui.utils.chat": fake_chat}):
                plan, users = await plugin.Filter._plan(self.f, self.body, None, object(), self.request, [(self.rows[0], "User likes Rust")])
            self.assertEqual(plan, {"operations": []})
            self.assertEqual(users[-1], text)
            args = fake_chat.generate_chat_completion.call_args
            self.assertNotIn("INVENTED_ASSISTANT_CLAIM", str(args.args[1]))
            self.assertEqual(args.args[1]["tools"], [])
            self.assertEqual(args.args[1]["response_format"], {"type": "json_object"})
            self.assertEqual(args.args[1]["max_tokens"], 768)
            self.assertNotIn("bypass_filter", args.kwargs)
            self.assertFalse(self.request.state.bypass_filter)
            self.assertTrue(self.request.state.metadata["features"]["memory"])


if __name__ == "__main__":
    unittest.main()
