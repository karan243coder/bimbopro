# BIMBO v4.0 - Unified MongoDB for users + bans + settings + premium
import time
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, uri, db_name="BimboBotDB"):
        self._client = None
        self.db = None
        if not uri:
            logger.warning("⚠️  BIMBO_DATABASE_URL not set — using in-memory fallback.")
            self._fallback = {"users": {}, "bans": set(), "premium": {}, "thumbs": {}, "settings": {}}
            self._use_fb = True
            return
        try:
            self._client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
            self.db = self._client[db_name]
            self.users = self.db["users"]
            self.bans = self.db["bans"]
            self.premium = self.db["premium"]
            self.thumbs = self.db["thumbs"]
            self.settings = self.db["settings"]
            self.stats = self.db["stats"]
            self._use_fb = False
        except Exception as e:
            logger.error(f"DB init error: {e}")
            self._use_fb = True
            self._fallback = {"users": {}, "bans": set(), "premium": {}, "thumbs": {}, "settings": {}}

    async def add_user(self, user_id, name="", username=""):
        try:
            if self._use_fb:
                self._fallback["users"][user_id] = {"id": user_id, "name": name,
                                                     "username": username,
                                                     "joined": time.time()}
                return
            await self.users.update_one(
                {"id": int(user_id)},
                {"$setOnInsert": {"id": int(user_id), "join_date": time.time()},
                 "$set": {"name": name, "username": username,
                          "last_active": time.time()}},
                upsert=True
            )
        except Exception as e:
            logger.error(f"add_user: {e}")

    async def total_users(self):
        try:
            if self._use_fb:
                return len(self._fallback["users"])
            return await self.users.count_documents({})
        except Exception:
            return 0

    async def get_all_users(self):
        try:
            if self._use_fb:
                return list(self._fallback["users"].values())
            return self.users.find({})
        except Exception:
            return []

    async def delete_user(self, user_id):
        try:
            if self._use_fb:
                self._fallback["users"].pop(int(user_id), None)
                return
            await self.users.delete_one({"id": int(user_id)})
        except Exception as e:
            logger.error(f"del_user: {e}")

    # ---- BAN SYSTEM ----
    async def ban_user(self, user_id, reason=""):
        try:
            if self._use_fb:
                self._fallback["bans"].add(int(user_id))
                return
            await self.bans.update_one({"id": int(user_id)},
                                       {"$set": {"id": int(user_id), "reason": reason,
                                                 "banned_at": time.time()}},
                                       upsert=True)
        except Exception as e:
            logger.error(f"ban: {e}")

    async def unban_user(self, user_id):
        try:
            if self._use_fb:
                self._fallback["bans"].discard(int(user_id))
                return
            await self.bans.delete_one({"id": int(user_id)})
        except Exception as e:
            logger.error(f"unban: {e}")

    async def is_banned(self, user_id):
        try:
            if self._use_fb:
                return int(user_id) in self._fallback["bans"]
            return await self.bans.find_one({"id": int(user_id)}) is not None
        except Exception:
            return False

    async def ban_list(self):
        try:
            if self._use_fb:
                return list(self._fallback["bans"])
            return [u["id"] async for u in self.bans.find({})]
        except Exception:
            return []

    # ---- PREMIUM ----
    async def set_premium(self, user_id, expires_at, plan="premium"):
        try:
            if self._use_fb:
                self._fallback["premium"][int(user_id)] = {"id": int(user_id),
                                                           "expires": expires_at,
                                                           "plan": plan}
                return
            await self.premium.update_one({"id": int(user_id)},
                                          {"$set": {"id": int(user_id),
                                                    "expires": expires_at,
                                                    "plan": plan}},
                                          upsert=True)
        except Exception as e:
            logger.error(f"set_premium: {e}")

    async def remove_premium(self, user_id):
        try:
            if self._use_fb:
                self._fallback["premium"].pop(int(user_id), None)
                return
            await self.premium.delete_one({"id": int(user_id)})
        except Exception as e:
            logger.error(f"remove_premium: {e}")

    async def get_premium(self, user_id):
        try:
            if self._use_fb:
                return self._fallback["premium"].get(int(user_id))
            u = await self.premium.find_one({"id": int(user_id)})
            if u and u.get("expires", 0) > time.time():
                return u
            return None
        except Exception:
            return None

    # ---- THUMBNAIL ----
    async def set_thumb(self, user_id, file_id):
        try:
            if self._use_fb:
                self._fallback["thumbs"][int(user_id)] = file_id
                return
            await self.thumbs.update_one({"id": int(user_id)},
                                         {"$set": {"id": int(user_id), "file_id": file_id}},
                                         upsert=True)
        except Exception as e:
            logger.error(f"set_thumb: {e}")

    async def get_thumb(self, user_id):
        try:
            if self._use_fb:
                return self._fallback["thumbs"].get(int(user_id))
            d = await self.thumbs.find_one({"id": int(user_id)})
            return d["file_id"] if d else None
        except Exception:
            return None

    async def del_thumb(self, user_id):
        try:
            if self._use_fb:
                self._fallback["thumbs"].pop(int(user_id), None)
                return
            await self.thumbs.delete_one({"id": int(user_id)})
        except Exception as e:
            logger.error(f"del_thumb: {e}")

    # ---- SETTINGS per user ----
    async def set_user_setting(self, user_id, key, value):
        try:
            if self._use_fb:
                self._fallback["settings"].setdefault(int(user_id), {})[key] = value
                return
            await self.settings.update_one({"id": int(user_id)},
                                           {"$set": {key: value}}, upsert=True)
        except Exception as e:
            logger.error(f"set_setting: {e}")

    async def get_user_setting(self, user_id, key, default=None):
        try:
            if self._use_fb:
                return self._fallback["settings"].get(int(user_id), {}).get(key, default)
            d = await self.settings.find_one({"id": int(user_id)})
            return d.get(key, default) if d else default
        except Exception:
            return default

    async def incr_stat(self, key, amount=1):
        try:
            if self._use_fb:
                return
            await self.stats.update_one({"key": key},
                                        {"$inc": {"value": amount}},
                                        upsert=True)
        except Exception:
            pass

    async def get_stat(self, key):
        try:
            if self._use_fb:
                return 0
            d = await self.stats.find_one({"key": key})
            return d["value"] if d else 0
        except Exception:
            return 0


db = Database(Config.BIMBO_DATABASE_URL)
